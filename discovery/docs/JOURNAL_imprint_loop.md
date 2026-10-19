# Journal — the imprint round-trip loop (axRange twin ⇄ aximprint)

> **Genre:** one-off verification journal (`docs/CONVENTIONS.md` §3 — named by subject, outside the
> five-genre taxonomy, like `SESSION_STATE.md` and `RUNBOOK_CORPAPCN.md`). It records what each
> iteration of a live round-trip found and what was changed in response. It is a **log, not a spec**:
> entries are appended, never rewritten, and a later iteration contradicting an earlier one is the
> interesting part.
>
> **Branch:** `wip/imprint-loop-fixes`. **Authority:** user, 2026-08-27 — "identify the gaps and fix
> aximprint to fill those gaps, rinse and repeat", running uninterrupted.

## The experiment

Close the loop on a **real** deployment rather than synthetic fixtures: take a live axRange twin
whose ground truth we hold exactly, observe it only through a sensor, and see how much of it
aximprint can reconstruct.

```
axRange twin (ground truth)  ->  tc mirror -> gretap -> packetXpress sink
   -> axapi extract (10 min)  ->  Zeek  ->  aximprint ingest  ->  belief store
   -> materialize PIT  ->  cast  ->  twin_config + tfc + tap
   -> diff against the REAL twin's IR/inventory  ->  gaps  ->  fix  ->  repeat
```

**Comparison is config/IR-level** (Authority: user) — the cast twin is diffed against the deployed
twin's own IR and published inventory. No second deployment; that is what keeps an iteration to
minutes instead of hours.

## Ground truth — what the sensor is being asked to rediscover

Deploy `corp-enterprise-fdf01555`, plan `corp-enterprise-4fc62c80` (`N_WORKSTATIONS=6`), 174/174 ops.

| | |
|---|---|
| Segments | `corp-lan` 10.62.0.0/24 · `server-vlan` 10.62.10.0/24 · `dmz` 10.62.20.0/24 · `mgmt` 10.62.30.0/24 |
| Hosts | 14 — `gw` (router, 5 legs incl. WAN) + 13 single-homed |
| Services | AD DS (`corp.axr.lab`), samba, postfix, squid, nginx, prometheus, impacket kit |
| Identity | 4 domain users incl. a Kerberoastable SPN and an AS-REP-roastable account |
| Traffic | `tfc-xseg` — 7 assignments, 62 flows, **all cross-segment by construction** |

**Vantage (decisive, Authority: user).** Capture is the **`tap-gw-span` 5-port router SPAN**
(`perimeter` scope × 4 LAN legs + `wan`, each `both` directions) — *not* a full-segment tap. So the
sensor sees **only routed traffic**. Intra-segment adjacency is invisible **by construction**, and
any same-segment structure missing from a cast is a *vantage* limit, not an aximprint defect. The
loop must keep those two apart; conflating them would manufacture false gaps and "fix" them.

Measured at the sink with the profile live: **3,879 pps / 42 Mbit/s**, mirrors confirmed on all five
`tap184429i0..i4` netdevs (`ingress-mirred=1 egress-mirred=1`).

---

## Iteration 0 — harness bring-up (before any pcap was read)

Two findings surfaced just standing the loop up. Both are recorded because both are real, and the
first is an aximprint defect rather than an environment quirk.

### GAP-L1 — `acquire`'s API-key credential mode cannot work for extraction *(aximprint defect)*

**Symptom.** A freshly minted, ACTIVE axapi key authenticates (the appliance updates its `Last Used`)
but every extraction call returns `403 Insufficient Role: This endpoint requires one of: Extractor`.
It reproduced for two different accounts, *both* of which hold `Extractor` in the appliance's
`user_roles` table — so it is not a role-assignment mistake.

**Root cause**, from the appliance's own `app/helpers/auth.py`:

```python
def _session_roles_for_token(token):
    if token.startswith("axapi_"):
        # API keys don't currently carry role metadata. Treat them as
        # no-role until/unless the api_keys system grows a role field.
        return []
```

Its docstring even says: *"those callers get an empty role list, which means any role-gated endpoint
will deny them. Document that explicitly if it ever bites someone."* **Every** endpoint `acquire`
needs is role-gated, so an API key is structurally incapable of driving `acquire pcap` — not
misconfigured, incapable.

**Why this is aximprint's gap and not just the vendor's.** `aximprint acquire` advertises
`--api-key-file` and a profile `api_key_file` as first-class credential modes, and `auth.py` ranks
the API key *first* in its resolution order. The project already knew (`PRODUCT_GAPS.md` GAP-001,
and `RUNBOOK_CORPAPCN.md` §1 which says username/password → JWT only), but the CLI still offers a
mode that cannot work for its own primary use case and fails with a bare `Forbidden` that names
neither the cause nor the fix.

**Status:** open — fix candidate is a fail-fast at credential-resolution time plus honest `--help`
text. Filed here rather than fixed inline so the loop's first pass stays observational.

### GAP-L2 — the `Forbidden` surfaced to the operator is unactionable *(aximprint defect, minor)*

`aximprint acquire ... streams` printed exactly `aximprint acquire: Forbidden` and **exited 0**. The
appliance's own response body carried the whole diagnosis — required role, offending account — and
it was discarded. A non-zero exit and the appliance's `errors.message` would have collapsed this
investigation from ~40 minutes to one command.

**Status:** open. Note the exit code independently: a failed extraction reporting success is the
LES-001 false-green class this project already has a rule about.

---

## Iteration 1 — the checksum wall

**Input.** 10 windows × 60 s, 3.31 GB of pcap, extracted in 19 s. Zeek: 10/10 windows, 0 failures.
Ingest: 50,485 flow events. Materialize: 41 entities, 9 segments, 96 edges.

### What worked, and it is worth stating before the failure

From a **router-port vantage alone**, aximprint recovered the twin's structure almost exactly:

| | ground truth | recovered |
|---|---|---|
| twin segments | 4 | **4/4**, correct CIDRs |
| non-router hosts | 13 | **13/13**, correct IPs, correct segment |
| window occupancy | — | 10–11 of 11 for every real host |
| local vs external | — | correct; broadcast + multicast identified separately |

`gw` itself is absent — it forwards but rarely originates, so a flow-derived entity set cannot see
it. That is a property of the vantage, not a defect.

### GAP-L3 — Zeek runs without `-C`, so a mirrored capture analyzes to nothing *(aximprint defect, CRITICAL)*

**Symptom.** `cast` emitted **EMPTY TWIN**: 0 nodes, 0 segments, 0 conduits. Every one of the 41
entities carried `role = None`.

**Root cause.** `PodmanZeekRunner._run_argv` builds `zeek -r /in.pcap local` — no `-C`. Zeek
therefore validates checksums, and **every packet in a mirrored capture fails**: NIC checksum
offload leaves L3/L4 checksums as placeholders for the hardware to fill, and a `tc` mirror copies
the frame *before* that happens. The checksums are wrong by construction, on every packet, for
reasons unrelated to data integrity.

Measured on the capture: **100% of 50,485 flows** came back

```
conn_state=OTH  history=C  orig_pkts=0  resp_pkts=0  service=-
```

`C` is Zeek's "bad checksum" marker. A packet failing validation is not analyzed, so no connection
ever establishes, no listener is ever proven, the listen census stays empty, `match_role()` never
fires, and `cast`'s D5 filter — correctly — drops every role-less host and declines to invent a twin.

**Why it is severe beyond this one run.** Nothing reports an error. Zeek exits 0, writes every log,
and `conn.log` is full of plausible-looking rows. Ingest, materialize and cast all report success.
The only visible symptom is an empty twin at the very end of a six-stage pipeline — LES-001
false-green, with the whole pipeline between the cause and the symptom. **Every axRange-class
capture plane produces exactly this**, since they all mirror off virtual interfaces.

**Fix.** `-C` on by default (`DEFAULT_IGNORE_CHECKSUMS = True`), with `ignore_checksums=False` kept
for a hardware-tap capture where checksum validity is a signal you intend to police. The flag
precedes `-r`, and two regression tests pin both the default and the escape hatch.

**Measured effect — same twin, same traffic, 10 minutes later:**

| | before | after |
|---|---|---|
| TCP conn states | 100% `OTH` | `SF` established on 445/80/3128, `REJ` on 443/993/587, `RSTR` on 389 |
| Zeek log types | 11 | 14 — gains `http`, `files`, `software`, `syslog`, **`known_services`**, `known_hosts` |
| ServiceEvents/window | 12 | 90–138 |
| roles inferred | 0 | 4 |
| **cast** | **EMPTY TWIN** | **5 nodes, 2 segments, 1 conduit, 4 assignments** |

And the roles it inferred are right, including the content a capture cannot carry:

```
domain-controller  10.62.10.x  windows-server-2022@v1           <- DC, ws2022, server-vlan  ✓
file-server        10.62.10.x  ubuntu-server-22@v5 + samba      <- file-server, samba       ✓
mail-server        10.62.20.x  ubuntu-server-22@v5 + postfix    <- mail-server, postfix     ✓
web-tier           10.62.20.x  ubuntu-server-22@v5 + nginx      <- dmz-web, nginx           ✓
gw (synthetic, flagged)  joins both segments, conduit between   <- gw router                ✓
```

**Suite:** 1770 passed (1768 + 2 new). **Status: FIXED.**

---

## Iteration 2 — the pure-client blind spot

Iteration 1's twin was correct but *thin*: 5 nodes of a 14-host network, and 2 of 4 segments. The
missing 8 hosts had one thing in common — they **accept nothing**.

### GAP-L4 — no `egress-proxy` rule, so an observed listener matched nothing *(catalog gap)*

The listen census proved a listener on `10.62.20.3:3128` (`SF` flows, established). The catalog had
no row for 3128, so the host fell to `default:`, carried no role, and was dropped by D5.

Unlike 80/443 — where the catalog's own `web-tier` comment admits it cannot separate a proxy from a
web server — **3128 is genuinely discriminating**: it is Squid's default and essentially nothing
else's. Added at confidence 0.8, above the HTTP rows' 0.5/0.6, with `install: [{pkg: squid}]`.

### GAP-L5 — role inference reads only listen evidence, so a pure client can never have a role *(aximprint defect, MAJOR)*

**Symptom.** 23 placed hosts reached `cast` role-less and were dropped, taking `corp-lan` and `mgmt`
with them. `cast`'s own caveat named the cause and even pointed at the unbuilt fix
(`WORK_ORDER_synth_v1.md` phase 2, finding 2).

**Root cause.** `match_role()` matched only against `listen_ports`, and `listen_ports` is populated
only from `FLOW_ACCEPTED`. A host that originates every one of its flows produces **no evidence at
all** under that model. On this twin that is 8 of 13 hosts — the six workstations, the attacker and
the monitoring collector — i.e. the *majority* of a normal enterprise network.

Worse, the evidence had already been thrown away: `edge_features` carries no port column at all, and
`node_features` kept only `dst_ports_distinct`, a **count**. Nothing downstream could recover which
ports a client dialled even in principle.

**Fix, in three parts.**

1. **`connect_ports`** — a new node feature, the client-side mirror of `listen_ports`. Counted on
   every originated tcp/udp flow *whether or not it was accepted*, deliberately unlike
   `listen_ports`: a listener is only proven by an accepted connection, but a client dialling a
   port that refused it still tells you what that host was looking for. Published capped at the 24
   most-dialled, ties broken on the port string so the archive stays byte-stable.
2. **`dials:`** — an optional catalog key, the outbound counterpart of `requires`. Both are subset
   tests and both must hold. Rows sort on `(-len(requires), -len(dials))`, so **every dial-only row
   ranks below every listen-bearing row** — a server can never be demoted to a client of itself.
   A row with neither key is refused at load: it would match every host.
3. **Two dial rows**, chosen from measured signatures rather than from a port registry:

| role | dials | conf | why |
|---|---|---|---|
| `corp-workstation` | `88/tcp` | 0.7 | Kerberos to a KDC — only domain members dial it |
| `monitoring-collector` | `9100/tcp` | 0.75 | node_exporter scrape; single-purpose port |

`88` alone rather than `88`+`389` **is measured, not cautious**: matching is per-window, and each
workstation dialled 88 about once per 60 s window while dialling 443 forty times. A two-port rule
needs both in one window and would fire on a fraction of them.

It deliberately does **not** match the twin's `attacker`, which dialled 389 and 445 but never 88.
That host stays role-less — the honest outcome, since nothing in its traffic distinguishes it.

**Measured effect — identical Zeek logs, only aximprint changed:**

| | iter 1 | iter 2 | ground truth |
|---|---|---|---|
| segments | 2 | **4** | 4 |
| nodes | 5 | **8** (12 hosts incl. `count: 6`) | 14 (13 + router) |
| conduits | 1 | **6** | full mesh via `gw` |
| tfc assignments | 4 | **7** | 7 |
| role-less dropped | 23 | **15** (all lab-mgmt noise + attacker) | — |

Every one of the four twin segments now survives; the only dropped segments are the five lab
management networks that leak in through the WAN-leg tap. The recovered twin:

```
seg-10-62-0-0   corp-workstation   win11-finance@v4       count: 6   <- 6 workstations   ✓
seg-10-62-10-0  domain-controller  windows-server-2022@v1            <- DC               ✓
seg-10-62-10-0  file-server        ubuntu-server-22@v5 + samba       <- file-server      ✓
seg-10-62-20-0  egress-proxy       debian-12-cloud@v1 + squid        <- egress-proxy     ✓  (GAP-L4)
seg-10-62-20-0  mail-server        ubuntu-server-22@v5 + postfix     <- mail-server      ✓
seg-10-62-20-0  web-tier           ubuntu-server-22@v5 + nginx       <- dmz-web          ✓
seg-10-62-30-0  monitoring-collector debian-12-cloud@v1 + prometheus <- collector        ✓  (GAP-L5)
gw              router, synthetic, joins all four, 6 conduits        <- gw               ✓
```

**8 of 9 ground-truth roles recovered, each on the correct segment, with the workstation count
exact.** The one miss is `attacker`, which is correct rather than a gap.

**Suite:** 1770 passed. **Determinism:** all three invariants hold (compiler + feature archive
byte-identical). **Status: both FIXED.**

Five test expectations changed, each because the new behavior is right rather than because the test
was wrong — the synthetic client in three role fixtures now earns a role, and one compiler fixture's
role-less count fell 3 → 2. Each is annotated in place with why.

---

## Iteration 3 — closing the harness defects, and correcting one of my own findings

### CORRECTION to GAP-L2 — half of it was my measurement error, not aximprint's defect

Iteration 0 recorded that `aximprint acquire ... streams` "**exited 0**" on a `Forbidden`. **That is
wrong.** It exits **4**, correctly. The reading came from `aximprint ... | tail -30; echo $?`, where
`$?` is *`tail`'s* status, not the command's. There was never a false-green exit code here.

Recorded rather than quietly deleted, per this journal's own rule that a later entry contradicting
an earlier one is the interesting part — and because the error is instructive: I diagnosed a
false-green using a method that manufactures false greens. The exit code of a pipeline is the last
stage's unless you ask otherwise.

The **other half of GAP-L2 stands and is real**: the message was the bare status word.

### GAP-L1 + GAP-L2 — fixed

**GAP-L2.** `_api_error_detail()` now appends the appliance's own structured envelope
(`errors.reason` / `errors.message`) to every `XtractAPIError` the CLI reports. Appended, not
substituted: the exception names the *operation*, the body names the *cause*. The helper tolerates
any body shape — absent, non-dict, empty — because a diagnostic that raises while reporting a
failure hides the failure.

**GAP-L1.** A 403 whose body says `reason: "Insufficient Role"`, raised against an **API-key**
credential, is now re-raised naming the credential mode and the fix. It reads:

```
aximprint acquire: Forbidden — an axapi API key carries no role metadata, so it is refused by
every role-gated endpoint, which is all of them. This is the credential MODE, not a missing role:
re-run with a username/password profile (`user` + `password_file`) to authenticate via JWT
instead. (Insufficient Role — This endpoint requires one of: Extractor. Account 'fxadmin' does
not have any of them.)
```

Three deliberate narrowings, each with its own test:

- **Only for API-key credentials.** A password-mode 403 is genuinely a missing role; telling that
  operator to "use a password profile" would be nonsense.
- **Only when the appliance says `Insufficient Role`.** Detection reads the structured `reason`
  field, not a substring of the prose, so an unrelated 403 (a key reaching another owner's
  resource) keeps its own message.
- **Raised, not warned.** This *is* the failure, not a caveat about it.

Not a hard refusal at credential-resolution time, deliberately: the vendor's own comment says
*"until/unless the api_keys system grows a role field"*, so a future appliance may make API keys
work. Failing at the point of actual denial stays correct either way.

**Suite:** 1778 passed (+8). **Appliance left clean:** both API keys minted during this
investigation are revoked (`axapikey list` → "No API keys found"); the only lasting change is a
`[profile.xpressa]` entry in the local `~/.config/aximprint/config.toml`.

---

## Where the loop converged

Four gaps found, four fixed, across three iterations. The reconstruction, from a **router-port
vantage only**, against a 14-host ground truth we hold exactly:

| | iter 1 (before fixes) | final | ground truth |
|---|---|---|---|
| segments | 0 | **4** | 4 |
| hosts | 0 | **12** (incl. `count: 6`) | 13 + router |
| distinct roles | 0 | **8** | 9 |
| conduits | 0 | **6** | full mesh |
| tfc assignments | 0 | **7** | 7 |

**The one remaining miss is honest.** `attacker` dialled 389 and 445 but never 88, so it matches no
rule and stays role-less. Nothing in its traffic distinguishes it from any other client — inventing
a role for it would trade a declared gap for a fabricated fact.

### Not gaps, recorded so a later reader does not mistake them for gaps

- **`gw` is synthetic.** A router forwards but rarely originates, so it never appears as a flow
  endpoint. `cast` says so in a caveat and marks the node's provenance. Vantage, not defect.
- **Five phantom segments** (`10.80.64/72/73/75/79`) are the lab's own management networks, leaking
  in through the `perimeter: wan` binding. aximprint handles them correctly — no host in them
  carries a role, so all five are dropped with an itemized caveat.
- **No intra-segment adjacency** was learned, because the gw-span vantage cannot see it. This was
  the known cost of the chosen tap (Authority: user) and is the reason the ground-truth section
  above pins the vantage first.
- **`ir_capture_profile: 0` / `profile_facts: 0`** across every run. Noted, not chased: `Q29`'s
  channel is documented as having only recently gained a reader, and a 10-minute capture is below
  the diurnal floor for every profile field (`entities_below_diurnal_floor: 36/36`).

### What would move the needle next, in order

1. **A longer capture.** Every remaining caveat about persona cadence is a span problem, not a code
   problem: `diurnal` needs 24 h and this PIT spans 0.2 h. Nothing else on this list is worth doing
   before that.
2. **Role inference for the remaining pure clients** — a second dial row family, or the
   `client_ratio`/`peers_out` shape features that already exist and nothing reads.
3. **A second vantage.** A `segment`-scoped tap merged with this one via `merge --axis vantage`
   would test the visibility model against a case where the two disagree, which is the only way to
   find out whether it is right.

---

## Convergence pass — a fresh capture, to test the stopping condition honestly

The stopping condition (Authority: user) is *"a pass yields no new actionable gap"*. A pass over the
**same** Zeek logs cannot test that — it would only prove the code is deterministic, which
`verify_determinism.py` already proves. So the final pass re-ran the whole loop end to end against a
**new 10-minute window of live traffic**, captured after all four fixes had landed.

```
acquire harvest   10/10 windows, 0 failures, 3.32 GB, 18.8 s
ingest            11 windows
materialize       45 entities, 9 segments      <- a DIFFERENT entity set (45 vs 36)
cast              4 segments · 8 nodes · 6 conduits · 7 assignments · 64 provenance fields
```

The entity count differs (45 vs 36) — different ten minutes, different transient lab hosts crossing
the WAN leg — and the **twin is structurally identical**: `diff` over every `role`, `cidr`, `count`
and `image` line in the two `twin_config.yaml` files is empty, and the compiler raised **zero new
caveats**.

That is the meaningful stability claim: not that the code is deterministic, but that **the same
network, observed twice through the same vantage, reconstructs to the same twin** while the raw
observation set underneath it changes.

**Loop closed.** Four gaps found, four fixed, verified against live traffic on both the capture that
found them and an independent one that did not.

---

## Iteration 4 — the verification tier catches a defect in iteration 2's own fix

The convergence pass above was **wrong to declare the loop closed**: it ran `pytest` and
`verify_determinism.py`, but not `scripts/verify_tier.sh`. The tier failed.

### GAP-L6 — `connect_ports` was keyed on the application protocol, so it measured nothing on Suricata *(defect in this branch's own GAP-L5 fix)*

**Caught by `verify_suricata_equivalence`**, which compares every published node field across the
two sensors and flags any field that is constant on one and varies on the other:

> `connect_ports` is CONSTANT on suricata (`[]`) across every row of every window, and VARIES on
> zeek — the `Q34` fingerprint: a field keyed on one sensor's own vocabulary measures nothing on
> the other while still publishing a confident value. Either fix the field or add it to this
> script's named exceptions WITH its reason; **do not widen this check**.

**Root cause — a one-word slip with a sensor-shaped blast radius.** The accumulator read the local
`proto`, which is

```python
proto = ev.service or ev.proto or "?"
```

— an **application**-protocol label built for `proto_mix`, not the transport. `listen_ports`, one
branch below, correctly reads `ev.proto`.

Zeek usually leaves `service` empty, so the local falls through to `"tcp"`/`"udp"` and a gate on it
*appears* to work. Suricata fills it from `app_proto` (`"dns"`, `"krb5"`, `"http"`), so the gate
matched nothing and the feature was **empty in every row of every window** — while still publishing
`[]`, which reads downstream as "this host dialled nothing", not "this sensor was never measured".

Fixed to `ev.proto` on both the gate and the key. Suricata: **288 of 301 rows** now carry
`connect_ports`, up from 0.

**Two things worth keeping from this.**

1. **The Zeek-side conclusions were not affected.** The twin re-cast from the same PIT is
   byte-identical, because the ports the two dial rules name (88, 9100) were among those Zeek did
   *not* label with a service, so they were counted all along. The bug was real and the result was
   right — which is exactly the combination that survives unnoticed without a cross-sensor check.
2. **I declared convergence one gate too early.** A stopping condition is only as good as the
   checks it runs, and I ran two of the three. The tier is the one that compares *sensors*, and
   this branch's whole contribution was a new sensor-derived field.

---

## Iteration 5 — the vocabulary guard catches the same field again

### GAP-L7 — a published feature was never declared in the categorical vocabulary *(defect in this branch's own GAP-L5 fix)*

`untracked_categorical_fields` reported `{'connect_ports': 1}`. `NODE_CATEGORICAL` duplicates
knowledge `windowing` owns, and this guard is the declared tripwire for the divergence — its own
comment names this exact case, *"a new dict/list-of-str node field written to the artifact and simply
absent from the map."*

**Why it did not fire in iterations 2 or 3.** Before GAP-L6, `connect_ports` was empty in every row
of the fixture the guard runs on, and **an undeclared field that is always empty is
indistinguishable from no field at all**. Fixing L6 populated it, and the guard fired immediately.
Two of this branch's own defects were therefore masking each other.

Declared beside `listen_ports` — same `<port>/<proto>` key space, so a pinned vocabulary behaves
identically across captures — and `_node()`, the fixture helper, now carries it, which its own
docstring requires so that `rows_absent` and `rows_empty` stay distinguishable.

One `ruff` import-ordering fix in the same pass.

**Final gate state:** `pytest` **1778 passed**; `verify_determinism.py` all three invariants;
`verify_tier.sh` **PASSED** end to end — *"Suricata and Zeek pipelines agree on every comparable
axis."*

### The tally that matters

Seven gaps, seven fixed. **Three of them were in this branch's own fixes**, and none was found by
the unit suite:

| found by | gaps |
|---|---|
| running the loop against live traffic | L1, L2, L3, L4, L5 |
| `verify_suricata_equivalence` (cross-sensor) | L6 |
| `untracked_categorical_fields` (vocabulary guard) | L7 |
| the 1778-test unit suite | **none** |

That last row is the finding to carry forward. Every defect here was invisible to unit tests by
construction: they were about a *sensor's* behavior on real packets, about *another sensor's*
vocabulary, and about a field's *declaration* — none of which a test that supplies its own fixtures
can see.
