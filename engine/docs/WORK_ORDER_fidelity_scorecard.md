# Work Order — The fidelity scorecard: certifying a twin against the golden-twin spec

> **STATUS: BUILT 2026-09-05 — S1–S4** (CHANGELOG "Fidelity scorecard BUILT (spec 13 §6's
> *certify* box, S1–S4)"; **§9.7** records what was built, the five corrections and the one
> refinement). **S5 remains OPEN** on the fingerprint seam (§8.2 — `reference.fingerprint: "absent"`
> is the field it fills). **The gated `dev45` READ-ONLY run (§7) RAN 2026-09-05 and PASSED** — all
> six steps, nothing deployed, updated or destroyed; step 1 reproduced the offline measurement
> number for number, and step 2's probe flipped row 5 to **fail** and the verdict to **`not-golden`**
> exactly as predicted (§7 carries the results and the five deltas; CHANGELOG 2026-09-05 (later)
> "Scorecard run 1 RAN on `dev45`"). Drafted 2026-09-04. **Authority:** *implementation (Claude Code)* for this
> draft; the row set, gate set, evidence-class discipline and the four §8 rulings it builds against
> are **Authority: user, 2026-09-03** (`docs/design/13-golden-twin-fidelity.md` §3/§3.3/§4/§8).
> Where §8's *consequences* are an implementation reading rather than a ruling, §9 asks rather than
> assumes.
> **Spec:** `docs/design/13-golden-twin-fidelity.md` — this builds spec §6's **certify** box, the
> one box in that lifecycle (besides *drift*) that does not exist: spec §3.1's eleven rows under
> spec §3.3's gate set, spec §4's category rule and spec §8.3's provisional flagging.
> **Version-locked:** **none proposed.** No `IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/
> `ADAPTER`/`IMAGE_MANIFEST` change; **no new op type** (stays 40); no validator rule (stays 114);
> no state-store bump. The scorecard is a pure read over existing artifacts plus one in-guest probe
> over the existing QGA channel. §9 names the two places that could change.
> **Route:** Pipeline (`/dev`) — code-writer → tester → code-reviewer → doc-writer → **gated
> `dev45` live-verify (READ-ONLY, §7)**.
> **Companion, in parallel:** the row-5 **server half** (spec §7) —
> `docs/WORK_ORDER_protocol_faithful_services.md`, drafted by a sibling agent. The two meet at the
> file seam; the four overlaps are *named* in §8.5 (and from its side in that file's §6.3), not
> resolved in either.

## 1. What this builds, and the one thing it must never do

A **fidelity scorecard** is what makes a build **golden** (spec §2): per row, *observed / referenced
/ imputed* against *reproduced*, computed from the provenance sidecar, the published twin inventory
and in-guest checks; stamped with a spec version; versioned in git beside the twin config and its
provenance; served over the API behind the Console's imprint screen.

**Scorecard run 1 is the standing `te corpapcn-twin-0e4f7d58`** (138 VMs on `dev45`, `completed` in
`/home/rocky/.axrange/state.db`), ruled spec §8.2: *"Score the standing twin first; the fresh cast
is the first golden."*

> **The run is READ-ONLY against that twin. Do not destroy it, `--update` it, `--reapply` it, or add
> a node to it.** It is both the scorecard's first subject and Option A's live proof (spec §7).
> Adding so much as a package to a twin host trips `--update`'s mutation refusal and forces a
> redeploy (`docs/WORK_ORDER_protocol_faithful_traffic.md` §5.2) — §3.3's probe design exists to
> avoid exactly that.

Expected verdict for run 1 (§3.4, measured offline): **not golden**, rows 1/2/5 failing for nameable
reasons, rows 8/9/10 reported-provisional. A scorecard calling this twin golden would be the bug.
**Confirmed on-cluster 2026-09-05** (§7.1): `not-golden`, rows 1/2/5 fail, rows 8/9/10
`not-computed`, and the twin was neither destroyed, `--update`d nor added to.

## 2. The three inputs, as they exist today

**Reference — the cast bundle.** `aximprint/src/aximprint/synth/compiler.py` emits three documents
plus a sidecar; `aximprint/scripts/install_cast_as_twin.py` installs them into
`twin-configs-cast/corpapcn-twin/`:

| file | present | what the scorecard takes from it |
|---|---|---|
| `corpapcn-twin.yaml` | **yes** | `segments[]` (27), `nodes[]` (62 groups → 138 hosts via `count:`), `install:`, `conduits[]` (22) |
| `corpapcn-twin.topology.json` | **yes** | the observed superset: `nodes[]` (786; `address`/`segment`/`role`/`*_evidence_class`/`listen_ports`/`dial_ports`/`windows_seen`/`locality`), `edges[]` (1437; `proto`/`flows`/`bytes`/`evidence_class`), `segments[]` (32), `windows` (11), `census`, `vantage`, `as_of`, `schema_version: "1.3"` |
| `corpapcn-twin.provenance.json` | **NO — not installed** *(on THIS bundle; the installer copies it as of 2026-09-05, and `corpapcn-golden/` has one — §8.1)* | per-field evidence classes; the `population` block (`node_groups[].observed`/`.emitted`/`.capped_by`, `role_less`, `excluded_by_occupancy[].windows_seen`, `segments_pruned`) |
| `tfc-cast.yaml`, `tap-cast.yaml`, `personas/`, `scripts/` | yes | not scorecard inputs |

The compiler *produces* that sidecar and `aximprint/src/aximprint/cli.py` writes it into the cast
dir; **at the time this was written the installer copied `topology.json` but not it** (§8.1) — fixed
2026-09-05 on the producer side, so a *freshly installed* bundle now carries it and the standing
`corpapcn-twin/` (installed 2026-09-02) still does not. Everything below is recomputable from
`topology.json` + the twin config *for now*, but re-deriving an evidence class is the fabrication the
house rule forbids. Note also that **two sidecar shapes exist and disagree**:
`axrange/api/fixtures/imprints/corp-enterprise/corp-enterprise.provenance.json` is a Track-C
*normalized* shape, while the compiler emits `{pit_id, code_fingerprint, vantage, population,
fields}` with varying per-field extras and a **fourth evidence class, `defaulted`**, which spec §3
does not list. `axrange/api/imprints.py`'s docstring records this; §9.1 asks which is canonical.

**Reproduced — the state store.** `axrange/planner/inventory.py`'s `build_twin_inventory(store,
deploy_id, manifests=, role_overrides=)` publishes `hosts[]` (`host_id`/`vm_name`/`vmid`/
`vm_status`/`kind`/`role`/`role_baseline`/`os`/`nets[]`) plus `net_bridge`/`vlan_underlay` as a pure
state-store derivation with no cluster call, and is already shared by `axrange te inventory` and
`GET /api/deploys/{id}/inventory` — **reuse it; do not scan op rows a second time** (LES-025,
LES-037). For row 3's applied installs, read op rows via `axrange/planner/opview.py`'s `op_fields`
under three mandatory rules: **filter to `COMPLETED`** (`inputs_resolved_json` holds *desired* inputs
and is refreshed at attempt-start; neither means *applied* — LES-159; and filter to completed, never
"exclude destroyed" — LES-113); **`.get(...)` everything** (an older deploy's row shape is frozen
until `--fresh` — LES-096); and **source only from unconditional ops** (LES-095) — on this twin
`clone-vm`/`attach-vm-to-vnet` fire 138 times each and `create-vnet`/`create-ovs-bridge` 27, while
`provision-app` (7) and `run-script` (20) are conditional **by design**, which row 3 must say.

**Wire — the fingerprint.** `aximprint/scripts/shape_fingerprint.py` reduces a **DuckDB IR store**
to six axes; its `compare()` computes the key-set Jaccard row 8 names — inside `_cmp_dict`, into a
text buffer, never returned structurally. axRange cannot import it
(`docs/design/aximprint-integration/WORK_ORDERS.md` §0), so the seam carries **both sides' raw axis
dicts and axRange does the arithmetic** (§8.2).

## 3. Per-row computation

### 3.1 The table

"Feasible" means *with §2's artifacts as they exist today, before any §8 extension*.

| # | dimension | reference input | reproduced input | check | first golden | feasible |
|---|---|---|---|---|---|---|
| 1 | asset | `topology.json` `nodes[]` (`locality`,`segment`,`windows_seen`) + `windows`; sidecar `population` when present | inventory `hosts[]` + `nodes[].count` | every observed placed host with `windows_seen/windows ≥ 0.8` is covered by its `(segment, role)` group's emitted hosts (group-granular, §3.2); OS/image/sizing reported, not scored | **gates** | **yes**; OS family **no** |
| 2 | topology | `segments[]`, `edges[]` joined to `nodes[].segment` | twin `segments[]`; router legs from `hosts[].nets[]` where `kind == "router"` | every observed segment maps to a twin segment; every observed local cross-segment pair has both ends present **and** a router attached to both | **gates** | **yes** |
| 3 | configuration | twin `nodes[].install[]` (the cast's role-catalog expansion) | `provision-app.install_actions` + `run-script.script_content`, `COMPLETED` only | every placed host's declared install list is **applied**; the role→install mapping is reported **imputed**, never scored (ruled spec §8.2) | **gates** | **yes**, as declared-vs-applied |
| 4 | state | `nodes[].listen_ports` | in-guest `ss -lnH -tu` over QGA (§3.3) | 100% of observed listens answer at certification time | **deferred** (spec §8.1) | yes — build, ship off |
| 5 | protocol | `nodes[].listen_ports` + `edges[].proto` | in-guest protocol probe over QGA (§3.3) | each observed `(host, port, protocol)` returns a **protocol-valid** response — **as built, over the *service*-listen denominator ruled 2026-09-05 (§3.2)**, not every listen fact | **gates** | yes for the probe; the *twin* is expected to fail |
| 6 | performance | `edges[].bytes`/`.flows` | fingerprint `counts`; loss counters | top-N edge volumes ±50%; capture lossless at offered rate | deferred | partial — report only |
| 7 | security | none (LDIF ingest unbuilt) | AD identity ops | ≥95% of referenced principals/SPNs/ACL edges; firewall policy a recorded gap | deferred | **no** — `not-computed` |
| 8 | dependency | fingerprint `role_edges_local` (source) | same (twin) | key-set Jaccard ≥ 0.8 | gates from v1.1; **reported** on run 1 | no without §8.2 |
| 9 | temporal | hourly envelope over ≥24 h capture | same | reported only in v1 | reported | no without §8.2/§8.3 |
| 10 | behavioral | the six axes | same | within tolerance (v1.1) | gates from v1.1; **reported** on run 1 | no without §8.2 |
| 11 | visual | Console `imprints/ImprintTopologyPanel.tsx` | Console `twin/DeployedTopology.tsx` | learned and deployed topology side by side; imputed elements visibly distinct | reported | **operator-attested** |

### 3.2 Five rows whose v1 check is narrower than its prose

- **Row 1 is group-granular, and that is a finding.** The cast collapses observed hosts into
  `(role, segment)` groups with `count:` (141 placed observed hosts → 62 groups), so **no per-host
  identity survives** and the ≥80%-of-windows rule cannot be evaluated host-to-host. The check is:
  per group, emitted covers observed; **plus** every high-occupancy observed host that fell out of
  the cast entirely is **named** (role-less under D5, or occupancy-excluded). That named list is the
  useful output — the population the twin lacks. Publish the imprecision as `granularity: "group"`,
  flipping to `"host"` if §8.1 makes it possible.
- **Row 1's OS-family clause is `not-computed`** — `topology.json` carries no OS attribute; spec §5
  item 1 makes it a *directory-export* fact and the LDIF ingest follows the first golden.
- **Row 3 checks declared-vs-applied, not catalog-vs-declared.** The role→install mapping lives in
  `aximprint/src/aximprint/catalog/roles.yaml` (`ROLE_CATALOG`), which axRange must not import; what
  axRange *can* prove is that every declared install action ran on every host of its group, with the
  imputation half reported `imputed` naming the catalog as source — exactly spec §8.2's ruling.
- ~~**Row 5's denominator is the observed listen set, not the host roster** — only 16 of 141 placed
  observed hosts carry any `listen_ports`. Publish the denominator beside the ratio; "100% of 0" is
  the classic vacuous pass (LES-101).~~ **SUPERSEDED as built, 2026-09-05** (kept above, not
  deleted). **Authority: implementation (Claude Code)** — an implementation refinement of this
  bullet, recorded here, in `observed_listen_facts`' docstring, in spec 13 §3.1 row 5 and in the
  CHANGELOG. The denominator is the observed **service** listens **on deployed hosts** **at or
  above the deploy threshold**, and every exclusion is **published with its reason and its count**
  rather than silently dropped — LES-101 cuts both ways, an unexplained filter being as dishonest
  as a vacuous pass. Three exclusion categories:
  - **`not a service`** — dynamic/ephemeral ports (≥ 49152) and Windows Delivery Optimization
    **7680**. A Windows RPC endpoint-mapper allocation seen once in the capture window is an
    artefact of the window; scoring the twin for not reproducing `57086/tcp` is scoring noise.
  - **`no protocol-aware probe`** — proprietary **9997** (Splunk forwarder) and **3389/udp**
    (RDP-UDP, negotiated inside an already-established TCP session). A port that can only ever
    return `inconclusive` blocks row 5 from passing for a reason that has nothing to do with the
    twin.
  - **`below deploy threshold`** — §3.4/§9.5's deployment scope, applied to row 5 as well as to
    rows 1 and 2: **reported, never failed.** Without it a gated-out host's listen fact resolves to
    no twin candidate and reads as a reproduction *failure*, the exact inversion of the ruling. A
    `None` (unconfirmed) threshold applies no filter at all — a guessed one would be worse than
    none.

  The ratio is still published beside its denominator, now with the exclusion counts and the full
  excluded-fact list. **Motivation:** 13 of the standing twin's 25 observed port pairs had no
  protocol-aware probe, which made `golden` structurally unreachable on row 5; with §3.3's four
  added methods, **every remaining fact in the denominator is protocol-aware**. Also as built: an
  observed fact with **no twin host at all** is `unresolved` and fails, while a fact whose twin host
  could not be asked is `unmeasured` — it blocks a pass and never asserts absence.
- **Row 11 is attested, not computed.** Its channel is *console*: a human compares
  `o2/grafana/plugins/axellio-axrange-control-panel/src/components/imprints/ImprintTopologyPanel.tsx`
  against `.../twin/DeployedTopology.tsx`. The scorecard records an operator attestation (who, when,
  a note); an attestation not made reads `not-attested`, never `pass`. Inferring a visual verdict
  from the existence of two React components is imputed-as-observed.

### 3.3 Where the in-guest probe lives (rows 5 and 4)

**Proposed: in-guest over the existing QGA channel, from a twin host that already shares the
target's segment. No new node, no new network reach, no package installed.** The alternatives lose
structurally: *from the orchestrator host* is impossible — twin segments are isolated SDN networks
with no route from the orchestrator, which is why cloud-init and Ansible are QGA-mediated at all
(`CLAUDE.md` Tech Stack; CHANGELOG 2026-06-02) — it reaches only the router's WAN leg; *a dedicated
probe VM* needs a node added, i.e. a `--update`, right for a fresh cast and wrong for run 1; and
*`drive: command` persona clients* are a cadenced generator, not a probe with a result to read back,
needing `dig`/`curl`/`ntpdate` baked in (`docs/PRODUCT_GAPS.md` **GAP-056**).

The probe mirrors `axrange/config_exec/backend.py`'s `_verify_capture`: pick a guest,
`GuestAgent.write_file` a rendered script (base64-in-command, LES-002), `GuestAgent.exec` it, read
structured output back, fail loud on no result (LES-001). Two constraints keep it cheap:

- **Python 3, stdlib only.** `axrange/personas/drivers/scripted.py` already requires and checks for
  `python3` in every Linux guest, so `socket`/`ssl`/`http.client`/`struct` are available with no
  install — enough for row 5's protocol set and row 4's `ss -lnH -tu` (`iproute2` is on the base
  image). **The protocol set follows the observed listen set, not a fixed v1 list (reconciled with
  the server half 2026-09-04):** dns, http, https, ntp **and**, because the observed set is an AD
  profile (§3.4), krb (an AS-REQ expecting AS-REP or KRB-ERROR), ldap (a rootDSE search), smb (an
  SMB2 NEGOTIATE) and rdp (an X.224 connection request) — each a minimal binary handshake in
  stdlib — plus a bare `connect` that is labelled `not protocol-aware` and can never count as
  `pass`. Without the AD four, row 5 could never pass on the one host that carries 12 of the
  observed ports, and the gate would be hollow. A missing interpreter is a **loud refusal, never
  a silent skip** (LES-089).
  **WIDENED as built, 2026-09-05** (the original eight kept above; **Authority: implementation
  (Claude Code)**, the same refinement as §3.2's denominator ruling): the shipped method set is
  **thirteen protocol-aware methods** — dns, http, https, ntp, krb, ldap, smb, rdp **plus cldap** (a
  connectionless LDAP NetLogon query on 389/udp), **nbns** (137/udp), **dhcp** (a DHCP INFORM),
  **dcerpc** (an EPM bind on 135/tcp) and **ldaps** — beside **two** explicitly
  not-protocol-aware fallbacks, `connect` and `udp-connect`, neither of which can ever count as
  `pass`. The original eight left four of the standing twin's observed ports unprobeable, and a
  denominator carrying facts that can only ever return `inconclusive` is a gate that cannot pass
  for reasons unrelated to the twin; §3.2's exclusions then account for the remainder, so **every
  fact in the scored denominator is protocol-aware**. Each method is **wire-tested against a canned
  responder**, never asserted on its rendered bytes alone (LES-221).
  **The refusal is per-peer, not per-run:** `_require_python3` and the render both run inside the
  per-peer `try`, so one unusable guest yields `unmeasured` facts instead of losing the whole
  document — the loud refusal is scoped to the peer it applies to.
- **Every peer address is an orchestrator-resolved literal baked into the render** — the guest never
  resolves a name, the containment property `drive: command` is built around
  (`docs/WORK_ORDER_protocol_faithful_traffic.md` §2.2; LES-201/LES-202).

Injector selection reuses the shape `o2control/taps.py::_select_verify_injector` already has (a host
on the target's segment, prefer a leaf, know when the router is the only option — LES-121, LES-146)
— **but as built it also filters to Linux peers** (`wait-os-ready`'s recorded `os_family`), which
that shape does not, because its payload is `ping` and this one is a Python 3 script. Copied
verbatim it would have peered **28 of 45 targets to Windows guests** and aborted the run (LES-216).
Row 4 probes the target host itself; row 5 probes from a **peer** on the same segment, because "does
this service answer" is a question only another host can ask. **Row 4 ships behind
`--include-deferred` and stays off for the first golden** (spec §8.1's "ignore them for now") — same
harness, different command.

### 3.4 What run 1 is expected to say (measured offline, 2026-09-04)

From `twin-configs-cast/corpapcn-twin/` and `/home/rocky/.axrange/state.db`; §7's acceptance targets:

> **This section is the 2026-09-04 PRE-BUILD estimate and is kept as written.** The build measured
> run 1 for real on 2026-09-05 and **§7 step 1 now carries the measured numbers**; §9.7 records
> them too. Rows 1, 2 and 3's verdicts held exactly. Row 5 reads **`not-computed`** rather than
> `fail` in the measured run only because that run was `--no-probe` — §7 step 2 is what turns it
> into the honest `fail` this section predicts, and **it ran 2026-09-05 and did exactly that**
> (5 passed / 26 failed; §7.1). Row 5's *counts* moved for a real
> reason: this file's own §3.2 ruling narrowed the denominator to observed *service* listens on
> deployed hosts at or above the deploy threshold (**31 facts / 10 hosts**, 10 published
> exclusions), rather than every listen fact on every placed observed host. Where the two disagree,
> §7 is the measurement.

- **Row 1 — FAIL.** 11 windows ⇒ threshold `windows_seen ≥ 8.8`. Of 141 observed placed hosts, **64**
  clear it and **5 of those 64 are role-less**, hence absent from the twin under D5. Name the five.
- **Row 2 — FAIL.** 32 observed segments vs 27 twin segments: **6 observed segments have no twin
  segment** (`seg-10-20-205-0`, `seg-10-5-0-0`, `seg-10-64-7-0`, `seg-10-80-31-0`, `seg-10-80-63-0`,
  `seg-172-16-0-0`); `internet-sink` is twin-only (synthetic, correctly excluded from the observed
  side). Of 31 distinct observed local cross-segment pairs, **6 touch a segment the twin lacks**; the
  other 25 should show a router leg at both ends (one `gw`, `configure-router`, 22 conduits).
- **Row 3 — expected PASS.** 11 groups declare `install:`; the store holds 7 `provision-app` + 20
  `run-script`, all `completed`. The `count:` fan-out is why 11 groups yield 27 ops — **expand groups
  to hosts before comparing**, or a correct twin reads as broken.
- **Row 5 — FAIL, honestly.** The observed listen set is `53/udp` (5 hosts), `135/tcp` (5),
  `389/tcp+udp`, `445/tcp`, `88/tcp`, `137/udp`, `3389/tcp` — an AD/Windows profile — while the twin
  is 55 `debian-12-cloud@v1` groups, 4 `win11-finance@v4`, 2 `ubuntu-server-22@v5`, 1
  `windows-server-2022@v1`, whose Windows hosts answer nothing (spec §1: *"a Windows domain
  controller that answers nothing"*). This is the failure spec §8.2 predicts, and what the row-5
  server half fixes.
- **Rows 8/9/10 — `reported, provisional`,** and on run 1 `not-computed` until this file's §8.2
  lands. **Rows 4/6/7 — `deferred`;** row 4 may be *run* with `--include-deferred`, but never
  enters the gate set.

## 4. The output document

### 4.1 Shape, and the summary that never lies

One JSON document; every field is proposed, nothing here is an existing schema.

```
{ "scorecard_version": "1",              # the SPEC version certified against
  "generated_at": "…Z",
  "verdict": "not-golden",               # golden | not-golden | incomplete
  "subject":   {"deploy_id","scenario_id","plan_id","ir_hash","deploy_status","hosts","segments"},
  "reference": {"bundle","pit_id","network","vantage","windows","as_of","topology_schema_version",
                "provenance_sidecar": "absent",     # §8.1 — stated, never assumed present
                "fingerprint": "absent"},           # §8.2
  "gate_set":  {"policy":"category: structural + behavioral (spec §3.3)", "gating":[1,2,3,5],
                "provisional":[6,8,9,10], "deferred":[4,6,7], "reported":[9,11]},
  "rows": [ {"row":1,"dimension":"asset","category":"structural","channel":"wire+reference",
             "status":"gates","verdict":"fail","provisional":false,"granularity":"group",
             "evidence":{"observed_and_reproduced":N,"referenced_and_reproduced":N,"imputed":N},
             "measured":{"windows":11,"threshold_windows_seen":8.8,"observed_placed":141,
                         "observed_above_threshold":64,"unreproduced_hosts":[…]},
             "clauses":[{"clause":"occupancy","verdict":"fail","detail":"5 of 64 …"},
                        {"clause":"os_family","verdict":"not-computed","reason":"…"}]}, … ],
  "warnings": ["provenance sidecar not installed — evidence classes are RE-DERIVED (§8.1)"] }
```

Three load-bearing properties: **per-fact evidence states, never one number** (spec §3), every
inference-backed clause carrying `evidence_class: "imputed"` explicitly; **`not-computed` is a
first-class verdict with a mandatory `reason`**, distinct from `fail`; and **`provisional: true` on
rows 6/8/9/10** until spec v1.1 fixes their tolerances (spec §8.3), whatever the verdict. Rollup:

```
verdict = "golden"      iff every GATING row is "pass"
        = "not-golden"  iff every GATING row is pass|fail and at least one is "fail"
        = "incomplete"  iff any GATING row is "not-computed"   ← never "not-golden"
```

A gate that could not be evaluated is not a failure, and no build is *golden* on a scorecard with an
uncomputed gate. A `provisional` row never contributes to `verdict` on spec v1 — the whole of spec
§8.3's ruling. Same discipline as LES-089 ("capture UNVERIFIED", not silently skipped) and LES-177
("static OK" must not imply existence), applied to a certification artifact.

### 4.2 Where it lives, and how it is served

`twin-configs-cast/<twin-id>/<twin-id>.scorecard.<deploy_id>.json`, committed beside the twin config
and its provenance (spec §2). Keying on `deploy_id` is deliberate: a re-certification after
`te deploy --update` is a **new** scorecard beside the old, not an overwrite, so a future drift
report has two dated documents to diff. Deploy-scoped and read-only, so it follows `te inventory`'s
precedent — a standalone verb outside the six-verb grid (`CLAUDE.md`, "unified verb grid"):

- **CLI:** `axrange te scorecard [DEPLOY_ID] [--json] [--reference <bundle-dir>] [--out <path>]
  [--include-deferred] [--no-probe] [--attest-visual "<note>"]`. The bare form defaults to the latest
  `te` deploy ordered on `updated_at` (LES-084). `--json` needs an explicit quiet path threaded
  through every progress print; a failure emits its diagnostic to **stderr** and relies on the exit
  code — never half-JSON on stdout (LES-083).
- **API:** `GET /api/deploys/{deploy_id}/scorecard`, peer of `/inventory` and `/access`, returning a
  `ScorecardDTO` in `axrange/api/dtos.py` — clean DTOs only (`docs/design/11-api-ui.md`).
  `?probe=false` serves the artifact-only rows without touching a guest; probing is opt-in on the
  API, default-on in the CLI.
- **Console:** a later slice behind `.../imprints/ImprintsScreen.tsx`. **Named, not designed here.**
- **Exit code:** `0` whenever a scorecard was produced, whatever its verdict — the verdict is data,
  not a process failure; `1` when it could not be produced. A gate-failure exit code is a tempting
  third state and a trap (CI would read "honestly not golden" as "the tool broke"); §9.4 raises it.

## 5. Module layout

New **`axrange/fidelity/`** — a leaf consumer structured like `axrange/measurement/`, whose own
`__init__.py` describes exactly this posture (inside the package for reuse and testability; the
engine proper never imports it).

```
axrange/fidelity/
  __init__.py    build_scorecard(...) -> Scorecard ; Scorecard.to_dict()
  reference.py   reads the cast bundle: <id>.yaml + <id>.topology.json + <id>.provenance.json
  rows.py        the eleven row computations — PURE over (reference, reproduced, probe)
  probe.py       in-guest probe renderers + result parsing (pure render; no QGA call)
  runner.py      the one place holding a GuestAgent and driving probe.py
  schema.py      output dataclasses + §4.1's verdict rollup
```

Import direction (`CLAUDE.md` "Conventions to Follow", LES-025): it **may** import `axrange.planner`
(`inventory`, `state`, `opview`), `axrange.twinconfig`, `axrange.manifest`, and — from `runner.py`
only — `axrange.adapter_proxmox` (`GuestAgent`, `RestClient`, config), the same downward reuse
`imagebuild/` and `config_exec/` make. It **must not** import `axrange.o2control` (role overrides
arrive as a `role_overrides` argument, exactly as `build_twin_inventory` takes them, resolved by the
caller via `o2control.traffic.effective_roles_for_twin`) and **must not** import `aximprint`, ever —
it reads three files. `axrange.api.service` and `axrange.cli.main` share **one** helper (LES-037),
and `rows.py`/`probe.py` stay **pure** so the eleven-row computation is unit-testable from fixtures
with no DB and no cluster, the way `planner/plandiff.py` is.

**Do not reuse `axrange/api/imprints.py`:** it reads the *normalized Track-C fixture* sidecar, not
the shape the real compiler emits (§2), and its own docstring already names the adapter as future
work. If §9.1 rules one shape canonical, collapse the two readers in *that* change.

## 6. Build slices, and how they are tested

**S1 — the reference and reproduced readers (no rows).** `reference.py` loads a bundle directory and
fails loud with a typed error on a malformed or missing file (LES-110); it publishes the id **its own
lookup resolved by**, cross-checks any content-declared id against it (LES-195), and pins resolution
by an explicit argument when more than one catalog could answer — never a first-hit chain (LES-198).
*Accept:* `twin-configs-cast/corpapcn-twin/` yields 786 topology nodes / 1437 edges / 32 observed
segments / 62 twin groups / 138 expanded hosts and reports `provenance_sidecar: absent` rather than
raising; the `corp-enterprise` fixture yields 42 hosts / 4 segments and `topology_export: absent`.

**S2 — rows 1, 2, 3 + the output document + the verdict rollup.** Artifact-only; no probe, no
fingerprint. *Accept:* against the real bundle plus a state-store fixture, verdicts and counts match
§3.4 exactly (row 1 fail naming 5 hosts, row 2 fail naming 6 segments and 6 pairs, row 3 pass over 11
groups expanded to their `count:` hosts); rows 4–11 present as `not-computed`/`deferred` with a
reason on every one; overall `not-golden`; `incomplete` unit-pinned as distinct from `not-golden`.

**S3 — the probe harness (rows 5, then 4).** `probe.py` renders; `runner.py` executes over QGA and
parses. *Accept:* the render is byte-deterministic for fixed input; every peer address in it is a
literal (a containment assertion, mirroring the `drive: command` pin); a missing `python3` raises at
start rather than logging per-fire; an empty `(host, port, proto)` set yields `not-computed` **with a
denominator**, never a vacuous 100% (LES-101); injector selection prefers a segment leaf and says so
when the router is the only choice.

**S4 — CLI verb + API endpoint + DTO,** both calling one shared helper. *Accept:* CLI and API produce
byte-identical row data for the same deploy (the exact `te inventory`-vs-endpoint divergence
`inventory.py` records as a past review finding — do not repeat it); `--json` emits nothing but JSON
on stdout on success **and** failure (LES-083); review confirms no credential reaches the document
(row 4's probe output can carry hostnames — check rather than assume).

**S5 — rows 8/9/10 behind the fingerprint seam (§8.2).** Pure arithmetic: key-set Jaccard and total
variation distance over two axis dicts, in `rows.py`; `not-computed` when the seam file is absent —
run 1's state — so S5 must not block S1–S4. *Accept:* Jaccard and TVD match hand-computed values; a
missing seam file yields `not-computed` with a reason, not a crash; rows 6/8/9/10 carry
`provisional: true` regardless of verdict.

**Testing approach.** Fixtures first — `axrange/api/fixtures/imprints/corp-enterprise/` is the
cross-repo test seam already built for this (spec §7); every row gets a fixture-driven unit test with
no DB and no cluster. **And shipped content, mandatory:** at least one regression test loads the
**real** `twin-configs-cast/corpapcn-twin/` bundle end to end — LES-185 is unambiguous, and a fixture
exercising row 1 cannot prove row 1 works on `count:`-collapsed groups unless it mirrors that shape.
The state-store fixture (built with `StateStore`, as `tests/test_plandiff.py` does) must include one
**non-`COMPLETED`** op row (pins LES-113/LES-159) and one deploy missing a recently-added input key
(pins LES-096). A golden-output test asserts a small scorecard byte-for-byte, so this published
document's shape cannot drift silently.

**Rough sizing:** S1 ≈ 0.5 d · S2 ≈ 1.5 d (the bulk) · S3 ≈ 1 d · S4 ≈ 0.5 d · S5 ≈ 0.5 d once §8.2
exists, plus one gated read-only live-verify.

## 7. Gated `dev45` live-verify — READ-ONLY (scorecard run 1)

**STATUS: RAN 2026-09-05 — PASSED.** All six steps; nothing deployed, updated or destroyed; no
`DECISION NEEDED` gate hit. Step 1 reproduced the offline measurement **number for number**, and
step 2 — the probe pass, the only part that could not be measured offline — flipped row 5 to
**fail** and the verdict to **`not-golden`** exactly as predicted. **§7.1 records what was measured
and the five deltas from the expectations below**; the runbook is left as written so the two can be
read side by side. (CHANGELOG 2026-09-05 (later) "Scorecard run 1 RAN on `dev45`".)

Step 1's *numbers* were measured offline against the same artifacts and state store during the build
(§9.7 ruling 4), so the run's job was to confirm them on-cluster and to execute **steps 2–3, the
only ones that touch a guest**. The numbers below are what the build measured; where they differ
from §3.4's pre-build estimate, §3.4 is the estimate and this is the measurement.

**Gate:** explicit user go-ahead. **Unlike most gated runs this one stands nothing up and tears
nothing down** — say so when asking. Preflight: confirm `corpapcn-twin-0e4f7d58` is still `completed`
and that `twin-configs-cast/corpapcn-twin/` is the bundle it was deployed from (compare
`deploys.ir_hash` against a re-expansion; if they differ, record it in `subject` rather than
proceeding silently). Note the state store is `~/.axrange/state.db`, not `./state.db` — and
`StateStore.__init__` **creates** a db file (and its parent) at whatever path it is handed, so a
typo'd `--db` silently scores nothing rather than failing (GAP-060): pass the real path.

1. `axrange te scorecard corpapcn-twin-0e4f7d58 --no-probe --no-write --json` — artifact-only, and
   **`--no-write` on this step** so a repeated run does not leave several near-identical artifacts
   in `twin-configs-cast/` before step 5 writes the one that matters. **Expect, as measured
   2026-09-05:** verdict **`incomplete`** (row 5 gates and is uncomputed without the probe) — that
   `incomplete`, not `not-golden`, is itself an acceptance criterion for §4.1; **row 1 fail** naming
   the five role-less hosts `10.20.193.95`, `10.20.205.56`, `10.80.20.67`, `10.80.72.13`,
   `192.168.0.149`; **row 2 fail** — 6 of 32 observed segments missing, 6 of 31 observed local
   cross-segment pairs touching one of them, **0 unrouted** among the rest; **row 3 pass** over
   **11 groups → 22 hosts → 27 actions**; **row 5 `not-computed`** with denominator **31 facts /
   10 hosts** and **10 exclusions** (7 *not a service*, 2 *no protocol-aware probe*, 1 *below the
   bar*); rows 4/6/7 `deferred`, 8/9/10 provisional `not-computed`, 11 `not-attested`; and
   `provenance_sidecar: absent` with its re-derivation warning.
2. `axrange te scorecard corpapcn-twin-0e4f7d58 --no-write --json` — **with the probe; the first
   step that touches a guest.** **Expect** row 5 to **fail** over that same 31-fact / 10-host
   denominator with a per-target reason, and the verdict to become `not-golden`. A row 5 returning
   `not-computed` means the probe did not run — diagnose, do not accept. Distinguish the two failure
   shapes the build ships: `unresolved` (no twin host for the fact) is a real reproduction failure;
   `unmeasured` (a twin host, no result) means the probe could not ask and must be diagnosed, not
   read as absence.
3. `axrange te scorecard --include-deferred --no-write --json` — row 4's `ss -lnH -tu` runs and is
   **reported**; confirm it does not enter the gate set (`gate_set.gating` stays `[1,2,3,5]`).
4. `curl -s localhost:8000/api/deploys/corpapcn-twin-0e4f7d58/scorecard?probe=false | jq` — assert
   the API's row data is identical to step 1's. (Restart `axrange serve` first if it predates this
   build, and serve **both** config dirs: `--twin-configs-dir twin-configs:twin-configs-cast`.)
5. Re-run step 1 **without** `--no-write` once, then check the written
   `…scorecard.corpapcn-twin-0e4f7d58.json` is valid JSON, carries `scorecard_version: "1"`, and its
   `warnings` names the absent provenance sidecar.
6. **Confirm nothing changed:** `te status` still `completed`, 138 VMs, no new deploy row, `git
   status` on `twin-configs-cast/` showing only the new scorecard file, and the twin's uptime
   unchanged across the run (the W1 live-verify's "the VM was never touched" proof, CHANGELOG
   2026-08-04). Steps 2–3 write a temporary script into a peer guest and remove it in a `finally` —
   that is the whole of the run's write surface, and §9.6's 9.2(a) is the ruling it rests on.

A *platform* defect the run finds (as opposed to a scorecard defect) goes to
`docs/PRODUCT_GAPS.md`, not into a quiet fix. *(Pointer corrected 2026-09-05: this said "next free
id: **GAP-061**", which the same day's services build had already taken along with 062–065. The run
filed none; **GAP-066** was filed from the cast install. A "next free id" written into a work order
goes stale the first time a sibling build files a gap — read the register's tail, not a pointer.
Proof, one day later: the corrected pointer to **GAP-067** was itself consumed on 2026-09-06 by the
services live-verify, which filed **GAP-067..GAP-078**.)*

### 7.1 Results — RUN 2026-09-05 on `dev45`, READ-ONLY, PASSED

Subject: the standing **`te corpapcn-twin-0e4f7d58`** (138 VMs), `completed`, plan_id
`corpapcn-twin-b6aaa3c5`. **Preflight:** `~/.axrange/state.db` was pre-existing (GAP-060 never
fired), and `deploys.ir_hash` was **byte-identical** to a re-expansion of
`twin-configs-cast/corpapcn-twin/` (`b6aaa3c5b145cba7c24b4f7fbf4da497a8bbf2317fa72384163a7739e76cb0c0`),
so nothing was recorded in `subject` as a mismatch. **Wall: ~9.5 min** for steps 1–6, ~13 min with
preflight.

| step | what ran | result |
|---|---|---|
| 1 | `--no-probe --no-write --json`, 1.2 s | **every predicted number matched exactly** — verdict `incomplete`; row 1 fail naming `10.20.193.95`, `10.20.205.56`, `10.80.20.67`, `10.80.72.13`, `192.168.0.149`; row 2 fail (6 of 32 segments, 6 of 31 pairs, **0 unrouted**, `internet-sink` twin-only/`compiler`); row 3 pass (11 groups → 22 hosts → 27 actions = 7 `provision-app` + 20 `run-script`); row 5 `not-computed` over 31 facts / 10 hosts with 10 exclusions (7 *not a service* — 7680/tcp + six ephemeral ≥ 49152; 2 *no protocol-aware probe* — 9997/tcp, 3389/udp; 1 *below threshold* — `10.21.161.1:53/udp`); rows 4/6/7 deferred, 8/9/10 provisional `not-computed`, 11 `not-attested`; `provenance_sidecar: absent`; `gate_set.gating` `[1,2,3,5]`; `expanded_nodes: 138` / `expanded_hosts: 137` |
| 2 | with the probe, 111 s (probe ≈ 110 s for 31 facts / 10 hosts) | **row 5 computed; `incomplete` → `not-golden`, as predicted.** 5 passed / 26 failed / 0 inconclusive / **0 unresolved / 0 unmeasured**, `probe_errors: 0` |
| 3 | `--include-deferred`, 115 s | row 4 ran (11 hosts, 69 listen facts, per-host sets published) and **stayed out of the gate set** (`gating` still `[1,2,3,5]`, status `deferred`); row 5 **bit-identical** to step 2 — the probe is deterministic on this twin |
| 4 | API parity | a second `axrange serve` on `127.0.0.1:8010` with both config dirs returned a document **identical to step 1's modulo `generated_at`** |
| 5 | one write | `twin-configs-cast/corpapcn-twin/corpapcn-twin.scorecard.corpapcn-twin-0e4f7d58.json` — 26,519 B, valid JSON, `scorecard_version: "1"`, identical to step 1's content, left **uncommitted** |
| 6 | nothing changed | `te status` `completed`, **1242/1242** op rows completed, 151 VMs / 143 running / 138 `te-`, deploy rows still 46, `updated_at` unchanged (`1788204492.519899`), `git status twin-configs-cast/` showing only the new scorecard file, guest **uptime monotonic across 578 s** on two sampled guests, and `/tmp/axr-fidelity-probe.py` **absent** on all four sampled peers — the `finally` cleanup works |

**Step 2 in full, because it is the finding.** The five passes: `10.20.196.1:53/udp`,
`10.80.20.5:53/tcp`, `10.80.20.5:53/udp`, `10.80.64.2:53/udp` (dns) and `10.80.75.171:80/tcp` (http).
The 26 failures: **17 timeout, 6 connection-refused, 3 protocol-mismatch** — and the three mismatches
are the data: `10.20.196.1:123/udp` ntp *"NTP mode 3, expected 4 (server)"*, `10.80.20.5:389/tcp` and
`:389/udp` *"unexpected LDAP protocolOp tag 0x63"*. Mode 3 is the **client's own request**; 0x63 is
**searchRequest**. That is the echo responder bouncing the probe's bytes back, and an **open port
scored zero** — correctly. **Nine of the twelve exercised methods scored no passes at all**, and
**`axdc01` (`domain-controller-10-80-2`) timed out on all ten of its facts** — spec §1's *"a Windows
domain controller that answers nothing"*, measured. Generalized as **LES-229**: an open port is not
a reproduced service; only a protocol-aware probe tells them apart. Had row 5 been scored on
open-port checks it would have read a flattered **8/31** instead of the honest **5/31**.

**Five deltas from the expectations above.**

1. **`gate_active: false`, and a SECOND warning fired.** The standing twin's bundle predates the
   deployment-scope change and carries **no `observation:` block**, so it has no recorded deploy
   threshold: rows 1 and 2 were judged against spec §3.1's default 0.8 and
   `below_deploy_threshold_nodes` is **necessarily empty**. The extra warning says so in the
   document. §7 predicted one warning (the sidecar re-derivation one, which also fired).
2. **Terminology.** Rows 4/6/7 carry `status: deferred` **and** `verdict: not-computed` — two
   fields. "Rows 4/6/7 `deferred`" above reads as a verdict and is not one.
3. **Step 4 was executed differently on purpose.** "Restart `axrange serve` first" would have killed
   the operator's 9-day-old server on `:8080` (which predates the build and 404s on the route), so a
   second instance was started beside it and only that one was stopped. The long-running server was
   left untouched.
4. **The GAP pointer above was stale** — corrected in place.
5. **The run scored the WORKING TREE, not committed code.** A mid-flight Console S1 slice had
   already reworded `axrange/fidelity/reference.py`'s sidecar-absent warning (wording only — no
   scoring logic, no row, no verdict), so the artifact carries the reworded text. Recorded so the
   artifact's provenance is honest. *(That slice landed 2026-09-06 — CHANGELOG "Console S1 BUILT";
   the rider is now committed code, and the wording in the artifact matches it.)*

**One expectation this run corrects.** §10 said the run "must produce real numbers for rows that do
not gate", and the handoff read that as the v1.1 tolerance numbers for rows 6/8/9/10. It **did not,
and could not**: rows 8/9/10 need **S5's fingerprint seam** (`reference.fingerprint: "absent"`) plus
a **recapture** of the deployed twin, and row 6 is deferred by category — all four returned
`not-computed`. What the run *did* produce for the non-gating half is **row 4**, measured in-guest
(11 hosts, 69 listen facts). **Spec §8.3's v1.1 numbers therefore wait on S5 plus a recapture, not
on run 1.**

**No platform gap filed.** The one near-miss — the newer `tap-corpapcn-twin-0e4f7d58-e4b517fe` row
stamped `tier: te` — is already handled by `_latest_te_deploy`'s `exclude_plan_id_prefix="tap-"`,
verified on cluster rather than assumed.

## 8. Contract extensions on the cross-repo file seam

The seam is **twin config + provenance sidecar + topology export, no imports in either direction**
(spec §7; `docs/design/aximprint-integration/WORK_ORDERS.md` §0). Four things it does not carry
today. **A sibling agent is drafting the row-5 server-half work order in parallel and will name seam
extensions of its own; §8.2 and §8.3 are the likely overlaps** — the main session reconciles.

**8.1 — Install the provenance sidecar beside the twin config.** *aximprint, small.*
`install_cast_as_twin.py` already copies `topology.json`; it should copy `provenance.json` to
`<stable>.provenance.json` the same way. Without it, every evidence class the scorecard reports for
rows 1/2/3 is **re-derived by axRange** rather than read from the producer — the imputed-as-observed
hazard the spec exists to prevent. Also unlocks the `population` block, turning row 1 from a
re-derivation into a read and giving row 2 the authoritative pruned-segment list.
**LANDED 2026-09-05** (aximprint's reference-ingest S0, D9): the installer copies it, and the first
golden's candidate cast is the first bundle to arrive with one —
`twin-configs-cast/corpapcn-golden/corpapcn-golden.provenance.json`, `axr-cast-provenance/1`, **687
fields**, loading as `provenance_sidecar: present` / `evidence_source: sidecar fields` /
`warnings: []`. The standing `twin-configs-cast/corpapcn-twin/` predates the change and still loads
`absent` + re-derived, which is one of the two warnings scorecard run 1 emitted (§7.1).

**8.2 — A fingerprint result on the seam: `<id>.fingerprint.json`.** *aximprint.* Rows 8/9/10 need
both sides' axis dicts. `shape_fingerprint.py::fingerprint()` already emits exactly the right JSON
for one side; `compare()` computes row 8's Jaccard but only into text. Proposed, so
`shape_fingerprint.py` stays **unchanged** (spec §6) and axRange owns the arithmetic and tolerances:

```
{"schema": "axr-fingerprint/1",
 "source": {<fingerprint() output for the source PIT store>},
 "twin":   {<fingerprint() output for the recaptured twin store>},
 "twin_capture": {"deploy_id","pit_id","from","to","windows","span_s","vantage",
                  "external_segments": ["10.200.254.0/24"]},
 "produced_by": "shape_fingerprint.py@<git-sha>"}
```

`twin_capture` is not decoration: row 9's rule is *"hourly activity envelope over a ≥24 h capture"*,
so a scorecard blind to the span cannot honestly report row 9, and `external_segments` records the
internet-sink locality override `compare()` applies to the twin side only — an axis measured under a
different override is a different measurement.

**8.3 — An hourly activity envelope (row 9).** *aximprint; may fold into 8.2.* Not one of
`fingerprint()`'s six axes. Either 8.2's document gains an `hourly_envelope` block (both sides,
bucketed by hour-of-day) or row 9 stays `not-computed` through spec v1, which its "reported only"
rule makes tolerable. **Recommend folding into 8.2** rather than a second file.

**8.4 — Optional: the role catalog's install expectation.** *aximprint, only if §9.3 says so.* Row
3's v1 rule is satisfied without it (§3.2). If the catalog half is ever to be *checked* rather than
reported, the cheapest carrier is a per-node provenance entry naming the role rule and its install
list — `compiler.py` already emits the neighbouring
`prov(f"twin_config.nodes[{idx}].image", "defaulted", source="catalog/roles.yaml")`.

**8.5 — Overlaps with the row-5 server-half work order**
(`docs/WORK_ORDER_protocol_faithful_services.md`, drafted in parallel; its §6.3 names the same three
from its side). Reconciled by the main session, not here. (1) **The observed listen set** — this
file reads `nodes[].listen_ports` as it exists at
`schema_version 1.3`; that work order proposes a **1.4** adding per-node `services[]` (port + proto
+ `evidence_class` + ~~`windows_seen`~~). If 1.4 lands, row 5 should consume `services[]` directly
rather than re-deriving a protocol by joining `listen_ports` against `edges[].proto`, and row 4
~~gains a per-listen `windows_seen` it does not have today~~ — **one specifier for that field, not
two**. **Reconciled 2026-09-04: 1.4's `services[]` is specified once, in that
file's §6.1; this file consumes it.**
**Corrected twice on 2026-09-05.** (a) **There is no per-listen `windows_seen` and there cannot be**
— the producer's store holds a *host's* occupancy, not a *port's* (that file's §6.1 correction 1,
LES-228), so **row 4's per-listen occupancy has no producer** and the in-guest `ss -lnH -tu` check
stays its only source. (b) **1.4 landed, and this file does NOT consume `services[]`** — measured on
the first golden's candidate cast: 403 `services[]` rows across 365 of 786 nodes, plus 354
`observations.dns` names and 11 identity principals, all published and unread. `reference.py`
reports the `bundle_schema` key and still reads `listen_ports`; the probe still selects its method
from a port table and row 5's evidence mix still counts every fact `unclassified`. Filed as
**GAP-066** and deliberately sequenced with S4 rather than fixed in the window run 1 was measured
in — changing method selection or the evidence mix now would make run 1 and its successor
incomparable for a reason unrelated to the twin. (2) **A declared listen set** — this file proposes **no** carrier for one: row 4's reproduced
side is in-guest `ss -lnH -tu`, never a declaration, so if that work order wires `App.listen` the
scorecard can consume it as a *third* source with no conflict here. (3) **The probe belongs to this
work order** (§3.3); that file's own validation must call it, never grow a second implementation
(LES-200, LES-205). **Reconciled 2026-09-04: agreed on both sides, and the probe's protocol set is
widened to the observed AD profile (§3.3).** (4) Both files independently found the **missing `*.provenance.json`** (§8.1) —
whoever touches `install_cast_as_twin.py` first ships it; it is one change, not two.


## 9. Decisions for the user

Each hits a `CLAUDE.md` risk trigger or materially changes the work. **None is decided here.**

**9.1 — Which provenance sidecar shape is canonical?** The real compiler shape (`population`, varying
per-field extras, the fourth class `defaulted`) or the Track-C normalized shape the fixture and
`axrange/api/imprints.py` use? *(a)* real shape canonical — `imprints.py` gains the adapter its
docstring names and the fixture is regenerated in the real shape; *(b)* normalized canonical —
aximprint emits it as a second file; *(c)* both persist, scorecard reads one, Console the other.
**Recommend (a)**: one producer, one shape, and the fixture's job is to stand in for real output,
which today it does not. *Risk trigger:* a **cross-repo contract** change touching a shipped Track-C
surface. **Separable sub-question:** spec §3 names three evidence classes and the producer emits
four — is `defaulted` a fourth class, or `imputed` with a `source`? The per-fact states depend on it.

**9.2 — Does row 5's probe count as read-only enough for the standing twin?** It writes a temporary
script into a guest and executes it; nothing persists, nothing installs, no op row moves — but it is
the only step in §7 that is not a pure read. *(a)* proceed as designed; *(b)* probe only from a
designated host; *(c)* skip the probe on run 1 and accept `incomplete`. **Recommend (a)** — it is
materially the action `verify-capture` performs on every tapped deploy — **but this is the user's
call, because run 1's read-only posture was stated as a constraint and should not be quietly
reinterpreted.**

**9.3 — Is row 3's "reported as imputed" final, or does v1.1 promote it to a catalog check?**
*(a)* never — §3.1's own successor is the host-inventory-backed rule, not a catalog check; *(b)* add
§8.4 at v1.1. **Recommend (a)**: the spec's stated successor is inventory-backed, and a catalog check
would put an aximprint-owned table on axRange's certification path.

**9.4 — Should a gate failure change the CLI exit code?** *(a)* exit 0 whenever produced (§4.2's
proposal); *(b)* exit 3 on `not-golden`, 4 on `incomplete`, so CI can gate; *(c)* `--fail-on-verdict`
opting into (b). **Recommend (c)** — default (a) so an operator reading a scorecard is never told
the tool broke, with (b) available. Raised because it is hard to reverse once anything scripts
against it.

**9.5 — Confirm the spec's §7 re-sequencing.** Spec §8.5 leaves standing *"confirmation of §7's
re-sequencing of reference ingest and the firewall-policy extension behind the first golden, which is
a reading of the rulings rather than one of them."* This work order is built on that reading — rows
4/6/7 are deferred precisely because reference ingest follows the first golden. If the reading is
wrong, rows 4 and 7 join the critical path and S3 grows a second reference reader.

### 9.6 Rulings and calls, 2026-09-04

**Ruled by the user (CHANGELOG 2026-09-04):** **9.1** — resolved by
`docs/design/14-operator-assertions.md`: the real compiler shape is canonical; this reader adapts
it for the Console (`axrange/api/imprints.py` gains the adapter its docstring names, the fixture
is regenerated from a real cast); aximprint adds `confidence` and `signal` per-field extras; the
sub-question is answered by a **six-class** model — `asserted` / `imported` / `observed` /
`referenced` / `imputed` / `compiler` — in which `defaulted` is renamed `compiler` and stays
unscored, and **every row publishes its evidence mix**. §3's per-fact states and §4.1's output
grow accordingly. **Row 1 (§3.2)** — the group-granular occupancy check gates; the OS-family
clause is reported `imputed`-from-role and not scored until the reference channel lands.

**Taken as implementation calls (Claude Code), open to veto:** **9.2 (a)** — the probe runs on the
standing twin; "read-only" is defined as no package install, no op row, no state-store write, and
the temporary script removed after the run — the action `verify-capture` already performs.
**9.3 (a)**. **9.4 (c)**.

**9.5 — RULED later on 2026-09-04, with a variant:** the reference channel and firewall policy do
follow the first golden, and the fresh cast's rows 1 and 2 are closed by spec 13 **§3.4** (the cast
emits its whole observed population with an observation block; the twin-config expander gates on
occupancy at `DEPLOY_MIN_OCCUPANCY`, default 0.8 = row 1's bar; role-less hosts become generic
endpoints). **Consequence for S2:** rows 1 and 2 judge against the *deployable* set at the twin's
recorded threshold — read it from the twin config's params and record it in `subject` — and gain
the per-fact state `observed, below deploy threshold` (reported, never failed). §3.4's run-1
expectations stand unchanged: the standing twin was cast under the old drop policy and carries no
threshold, so rows 1 and 2 fail there exactly as measured.

### 9.7 Built as — 2026-09-05 (S1–S4)

**Authority:** implementation (Claude Code) for the build and for rulings 1–3 below; the rulings
this builds *against* are the user's, recorded in §9.6 (9.1 resolved by
`docs/design/14-operator-assertions.md`'s six-class model; 9.5 ruled with the §3.4 deployment-scope
variant) and in `docs/design/13-golden-twin-fidelity.md` §3/§3.3/§3.4/§8. **CHANGELOG:** 2026-09-05
"Fidelity scorecard BUILT (spec 13 §6's *certify* box, S1–S4)".

**Delivered.** `axrange/fidelity/` exactly as §5 laid it out — `reference.py` / `schema.py` /
`rows.py` / `probe.py` / `runner.py` / `__init__.py`, `rows.py` and `probe.py` **pure**, `runner.py`
the sole `GuestAgent` holder, and `scorecard_for_deploy` the **one** helper the CLI verb and the API
route both call. `axrange te scorecard [DEPLOY_ID]` ships every flag §4.2 proposed plus **`--out`**,
**`--no-write`** and **`--fail-on-verdict`** (§9.4(c)); `GET /api/deploys/{id}/scorecard` ships with
`?probe=`, `?include_deferred=` and `?reference=` and a `ScorecardDTO` field-for-field with
`Scorecard.to_dict()`. `axrange.twinconfig.instance_ids` was made **public** (was `_instance_ids`)
so row 3's group→host expansion uses the *same* derivation the emission loop and the deploy gate do.

**Ruling 1 — row 5's denominator** (implementation refinement of §3.2, recorded there, in
`observed_listen_facts`' docstring, in spec 13 §3.1 row 5, and in the CHANGELOG). The denominator is
the observed **service** listens on **deployed** hosts **at or above the deploy threshold**, with
every exclusion published with its reason and count: dynamic/ephemeral ports (≥ 49152) and Windows
Delivery Optimization 7680 → *not a service*; proprietary 9997 and 3389/udp → *no protocol-aware
probe*; below-threshold facts → *below deploy threshold*, reported and never failed (§3.4).
**Motivation:** 13 of the standing twin's 25 observed port pairs had no protocol-aware probe, making
`golden` structurally unreachable on row 5; with §3.3's four added methods (cldap, nbns, dhcp,
dcerpc) every remaining fact in the denominator is protocol-aware.

**Ruling 2 — five reviewer must-fixes**, each a real defect a green suite had certified:

1. **Linux-only peer selection.** `_select_verify_injector`'s shape was reused without its
   OS/capability filter; against the standing twin it would have peered **28 of 45 targets** to
   Windows guests and aborted the run on a missing `python3` (LES-216).
2. **Row 5 honours the deploy threshold.** A gated-out host's listen fact resolved to no candidate
   and read as a reproduction **FAILURE** — the exact inversion of §3.4/§9.5 (LES-217).
3. **Rows 1 and 2 return `not-computed` on an empty denominator**, never `pass` (LES-218, extends
   LES-101).
4. **The re-derivation warning is attached to the derivation**, not to the sidecar's absence — as
   first written it would have vanished the day the sidecar landed while classes were still being
   re-derived (LES-219).
5. **Planned-but-never-cloned hosts are excluded from the reproduction denominator** and published
   as `planned_not_deployed` rather than counted as reproduced.

**Ruling 3 — code-writer deviations, recorded rather than quietly taken.** `expanded_nodes` (138)
and `expanded_hosts` (137) are **both** published rather than one reconciled number; the
"below threshold" state is **named from the mechanism that produced it**, so a gate-less twin reads
`not_evaluated_below_bar` and a gated one reads `below deploy threshold` (LES-220); `to_dict()`
emits **every** key including empty ones, so two scorecards diff cleanly; a missing deploy raises
`InventoryError` (HTTP 404), reusing the inventory endpoint's own contract; and the second fixture
bundle deliberately keeps the golden document on the **sidecar-absent** shape, which is run 1's real
state. On the seam, **`bundle_id` is the addressing id** (the directory this lookup resolved by,
cross-checked against `twin_config.id`) while **`pit_id` is published and never cross-checked** — it
is a point-in-time id by the producer's design (LES-222).

**Ruling 4 — run 1, measured offline** on the standing `te corpapcn-twin-0e4f7d58` (read-only,
`--no-probe`), the numbers §7 step 1 now carries: verdict **`incomplete`**; **row 1 fail** naming
`10.20.193.95`, `10.20.205.56`, `10.80.20.67`, `10.80.72.13`, `192.168.0.149`; **row 2 fail** — 6 of
32 observed segments missing, 6 of 31 pairs touching one, **0 unrouted**; **row 3 pass** over 11
groups → 22 hosts → 27 actions; **row 5 `not-computed`**, denominator 31 facts / 10 hosts with 10
exclusions (7 / 2 / 1); rows 4/6/7 `deferred`, 8/9/10 provisional `not-computed`, 11 `not-attested`;
`provenance_sidecar: absent` with its warning. **The gated `dev45` read-only run RAN 2026-09-05 and
PASSED** (§7.1): step 1 reproduced every one of these numbers on-cluster, and step 2's probe computed
row 5 — **5 passed / 26 failed** over the same 31-fact denominator — taking the verdict to
`not-golden`, which is what this ruling predicted it would do.

**Ruling 5 — scope.** **S5 is not built**, by scope: rows 8/9/10 need §8.2's `<id>.fingerprint.json`
and the field that already says so is `reference.fingerprint: "absent"`. S4's deferred `deploy_gate`
inventory key is **n/a** here — the scorecard calls `confirm_deploy_gate` directly (as
`docs/SESSION_STATE.md` 2026-09-04 anticipated). The subject does **not** publish a per-host
`os_family`: it is read for peer selection only and the inventory endpoint already carries `os` — a
**recorded non-publication, not a gap**.

**Tests.** **137 cases** across `tests/test_fidelity_{reference,rows,probe,cli_api}.py`, on two
fixture bundles: `tests/fixtures/cast-bundles/mini-cast/` mirrors the real cast's degenerate shape
(a router legging every segment, `count:`-collapsed groups, a synthetic segment, a gated-out node
and segment, a Windows node) per §6's shipped-content rule (LES-185), and `mini-cast-prov/` carries
a canonical `axr-cast-provenance/1` sidecar. `tests/fixtures/scorecards/mini-cast.scorecard.json`
pins the published document **byte-for-byte**. Every probe method is **wire-tested against a canned
responder** rather than asserted on its render (LES-221), and the parametrized method list is
**derived from the rendered script's own dispatch table**, not hardcoded — a hardcoded list kept
passing at full green while the four new methods went unexercised (LES-223). **Full suite: 2262 passed / 12 skipped / 0 failed, 2274
collected.** Nothing version-locked moved: op count stays **40**, catalog rules stay **114**, no
state-store bump.

## 10. Honest limits, non-goals, and sequence

- **It certifies against a spec version, not against reality.** Spec §9's out-of-scope list (L2
  fabric, payload content, secrets, patch level below image granularity, sub-hourly timing) is
  inherited wholesale.
- **Row 1 cannot be host-granular** from the installed artifacts (§3.2): a twin emitting the right
  *number* of hosts in the right group passes row 1 even if they are not the same hosts, because
  after a cast collapse "the same host" has no referent. §8.1 improves the accounting; it does not
  make the twin per-host addressable.
- **It measures the twin, not the tap plan or the traffic profile.** A twin can be golden with no
  capture bound and no personas running; rows 8/9/10 need a *recapture*, and their absence reads
  `not-computed`, never `fail`.
- **No drift report** (spec §6's other missing box) — two dated scorecards side by side are the
  substrate it will need, and that is all this provides. **No automatic re-certification:** a
  `te deploy --update` invalidates a standing scorecard, and v1's answer is that the document names
  the `deploy_id`/`ir_hash` it certified, so a reader can see it is stale. **No Console UI** (§4.2).
- **Sequence.** Depends on nothing unbuilt in axRange for S1–S4; S5 needs §8.2, and §8.1 (one file
  copy) should land first if it is free. Runs **in parallel with the row-5 server half**, which this
  is what *proves* — row 5 flips from `fail` to `pass` on the fresh cast and nothing else changes.
  **Feeds spec v1.1**, which fixes rows 6/8/9/10's tolerances — *corrected 2026-09-05 by the run
  itself:* run 1 could **not** produce those numbers, because rows 8/9/10 need S5's fingerprint seam
  plus a recapture of the deployed twin and row 6 is deferred by category; all four returned
  `not-computed`. The non-gating number run 1 *did* produce is **row 4** (§7.1). **v1.1's tolerances
  wait on S5 plus a recapture.** **Then** the fresh cast, certified as the first golden — its
  candidate bundle is produced, installed and read-only-verified as
  `twin-configs-cast/corpapcn-golden/` (CHANGELOG 2026-09-05 (later)); only its gated deploy remains. Does not block and is not blocked by
  `docs/WORK_ORDER_o2_sensor_rules.md`, the firewall-policy IR extension, or the aximprint reference
  ingest — all three serve rows the first golden defers.
