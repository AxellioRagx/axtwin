# Journal — CorpAPCN behavioural fidelity loop

> **Genre:** one-off verification journal (`docs/CONVENTIONS.md` §3), same discipline as
> `docs/JOURNAL_imprint_loop.md`: appended, never rewritten; a later entry contradicting an
> earlier one is the interesting part.
>
> **Branches:** `wip/corpapcn-fidelity` (aximprint, branched off `wip/imprint-loop-fixes` — loop
> 2's work depends on loop 1's fixes) and `wip/persona-fidelity` (axRange).
> **Authority:** user, 2026-08-27 — source CorpAPCN, deploy the cast, compare captures, fix both
> repos, iterate with `te deploy --update`.

## How this differs from loop 1, and why that changes everything

`JOURNAL_imprint_loop.md` compared **config to config** against a twin *we built*: ground truth was
exact, and any difference was a defect or a known vantage limit. This loop compares **traffic to
traffic** against a network *we do not control and cannot inspect*.

```
CorpAPCN (real)  -> axapi -> Zeek -> aximprint -> cast -> twin_config
                                                            |
                                                         DEPLOY on dev45
                                                            |
                                          tap -> XPress-SW-A -> axapi -> Zeek
                                                            |
                    compare the two captures' SHAPE  <-------+
```

There is no ground-truth host list here. The only evidence is what the sensor saw, which means a
missing host is indistinguishable from a host that never spoke during the window — a distinction
loop 1 could always make and this loop structurally cannot.

### The comparison is at Zeek/IR level, not pcap (decision: implementation)

A pcap- or flow-level comparison scores ~0 no matter how good the twin is: different IPs, MACs,
payloads, and absolute timing. The only thing "similar" can mean between two different networks is
**shape**, so each capture is reduced to a fingerprint and the fingerprints are diffed:

    role census · service census (ports served, and by how many hosts)
    role->role edge structure · fan-out degree distribution · proto_mix
    flow-size / duration / interarrival quantiles · client_ratio distribution
    flows-per-second and bytes-per-second per role

Both sides traverse the **identical** Zeek → ingest → materialize path, so a difference is the twin,
never the pipeline.

---

## Iteration 0 — scale probe, no deploy

**Source capture.** 12 streams × 10 min, 223 MB, 10/10 windows Zeeked in 55 s.

CorpAPCN is an order of magnitude richer than the synthetic twin of loop 1, and the log inventory
alone says so:

| | loop-1 twin | CorpAPCN |
|---|---|---|
| Zeek log types | 14 | **23** — adds `dce_rpc` `dhcp` `http` `ocsp` `quic` `ssh` `ssl` `x509` `software` `files` |
| entities / window | 20–27 | **188–286** |
| edges / window | 54–63 | **279–399** |
| `identity_bindings` | 46 | **1,978** |
| `profile_facts` | **0** | **50** |
| `ir_capture_profile` | **0** | **5** |

The last two rows matter: loop 1 noted `profile_facts: 0` and declined to chase it. It was not a
defect — a synthetic twin genuinely produces no profile evidence. A real network does.

**Materialized PIT:** 786 entities · 32 segments · 1,437 edges · 730 identities.

### The finding that defines this loop: 92% of placed hosts carry no role

```
786 PIT entities  ->  141 placed hosts  ->  11 emitted nodes (12 VMs)
                      130 of 141 placed hosts (92%) are ROLE-LESS and dropped by D5
```

The twin represents **8% of the observed network**. For comparison, loop 1's synthetic twin was 62%
role-less before that loop's fixes and effectively 0% after — but its host population was 13, its
role vocabulary was 9, and every one of those roles was in the catalog. Here the population is 141
and the catalog matches 11.

This is not the same gap loop 1 closed. Loop 1's `dials:` work made *pure clients* inferable; what
is missing here is **role vocabulary breadth on a real network**, plus whatever real roles produce
evidence no current rule names.

The 11 that did survive:

```
dns-resolver     x4   (10.20.196 / 10.21.161 / 10.80.20 / 10.80.64)
corp-workstation x3 groups, 4 VMs   (10.80.20 count:2 / 10.80.48 / 10.80.78)
domain-controller x1 (10.80.20)
web-tier         x2   (10.80.72 / 10.80.75)
gw               synthetic router joining all 8 segments
```

**A second gap visible already:** 4 of the 11 nodes are `dns-resolver`, and the catalog gives that
role `persona: null` — a deliberate, correct statement that axRange ships no such persona. But it
means **4 of 11 twin nodes will deploy as inert VMs generating no traffic at all**, on a network
where resolvers are among the busiest hosts. That is a traffic-fidelity gap with a known home
(axRange's persona catalog), and this loop is authorized to fix it.

### Harness: the stable-id problem, and `scripts/install_cast_as_twin.py`

A cast names `twin_config.id` after its PIT (`pit-<hash>-<ts>`) — right for provenance, fatal for
iteration. `te deploy --update` finds *"the standing deploy of this scenario"* by id, so a re-cast
always looks like a new scenario with no standing deploy, and every iteration would be a full
rebuild. The installer rewrites the id to a fixed one and moves the PIT id into the description
(moved, not lost), and rewrites the traffic profile's `target:` to match, since it resolves the twin
by name.

**Round-trip confirmed:** the installed config passes `axrange te validate --config corpapcn-twin`
with **0 findings in all three passes** — aximprint's output is directly deployable, unedited.

### Scale ruling (decision: implementation, delegated by the user)

dev45 has 5.7 TB free, 80 cores, 274 GB RAM free. The natural cast is 12 VMs, so no cap is needed
now; the working ceiling is **~40 VMs**, to be revisited as role coverage rises. No `--max-count`
or `--min-occupancy` is applied in iteration 1: capping before the coverage gap is closed would
hide the very thing this loop is measuring.

---

## Iteration 1 — role coverage on a real network: 7.8% → 77.3%

The diagnostic that shaped everything: of the 130 role-less placed hosts, **only 6 accept any
connection at all.** The other 124 are pure clients.

```
listen evidence (6 hosts):  8006/tcp Proxmox · 3389/udp RDP-UDP · 7680/tcp Windows DO
                            9997/tcp Splunk indexer · two ephemeral ports
dialled (all 130):          443(282) · 123 NTP(190) · 53 DNS(184) · 22(80)
                            1900 SSDP(51) · 1985 HSRP(40) · 5353 mDNS · 10051 Zabbix
```

That splits into two problems, and only one is a catalog gap.

### GAP-C1 — the `corp-workstation` rule names only `3389/tcp` *(catalog bug)*

RDP since Windows 8/2012 negotiates a **UDP** transport alongside TCP and prefers it. Hosts
observed accepting `3389/udp` matched nothing. Same role, same confidence, same content — a
second row for the UDP port. A bug, not a new signature.

### GAP-C2 — three real service signatures the catalog had no row for

| port | role | conf | why |
|---|---|---|---|
| `7680/tcp` | `corp-workstation` | 0.7 | Windows Delivery Optimization — a strong OS marker, but says "Windows host", not "desktop" |
| `8006/tcp` | **`hypervisor`** (new) | 0.85 | Proxmox management, as discriminating as 3128 is for Squid. `persona: null` — axRange ships none |
| `9997/tcp` | `monitoring-collector` | 0.7 | a host *accepting* on 9997 is the Splunk **indexer**, not the forwarder — direction is the signal |

Effect: coverage **7.8% → 10.6%**, 11 → 15 roled hosts.

### GAP-C3 — 124 hosts whose only evidence is being a client *(the real gap, and the debatable fix)*

No further port rule can separate them: a file server and a laptop both resolve DNS, sync NTP and
fetch HTTPS. But leaving them out means a twin carrying **11% of the network's hosts** and a
correspondingly tiny share of its traffic — for a *fidelity* twin, that is the dominant error.

Added **`generic-endpoint`**, which asserts exactly one thing: *this host consumes services and
serves none.* It does **not** assert workstation, server, laptop or OS. `corp-workstation` was the
tempting label and would have been a fabrication — a headless server dialling 443 fits the same
evidence.

Three rows (`443/tcp`, then `53/udp` and `123/udp` at 0.30) because matching is per-window and a
quiet host may make no HTTPS request in a given minute. Safety comes from **ordering, not a new
gate**: every listen-bearing row sorts above every dial row, and these are the last dial rows, so
they can only catch what nothing else claimed.

Paired with a new **axRange `generic-endpoint` persona** whose emit mix is taken from what those
hosts were *measured* dialling, in proportion — 443 / 123 / 53 / 22 — not from a notion of what an
endpoint "should" do.

| | uncapped | after C1+C2 | after C3 |
|---|---|---|---|
| role coverage | 7.8% | 10.6% | **77.3%** |
| roled hosts | 11 | 15 | **109** of 141 placed |

### Deployable shape

109 VMs exceeded the working ceiling, so the cast is reduced with **`--min-occupancy 0.8`** —
keep hosts present in ≥80% of windows, drop flickering transients. Chosen over `--max-count`
because it drops whole hosts rather than truncating real per-segment populations, and the
compiler itemizes exactly what it excluded.

**Final shape: 18 segments · 28 nodes · 49 VMs · gw with 18 legs — validates through axRange with
0 findings in all three passes.**

**Suite:** aximprint 1778 passed; the new persona validates with 0 PSN findings (axRange catalog
41).

---

## Iteration 1b — scale correction: don't shrink the twin to a comfortable number

**Authority: user, 2026-08-27** — *"make sure the twin gets as close as possible to the real
network size for the available resources; don't try to scale it down to trivial size and declare
success."*

A correct and necessary correction. Iteration 1 reduced a 109-VM cast to 49 with
`--min-occupancy 0.8`, reasoning from a *working ceiling I had invented* (~40 VMs) rather than from
what dev45 can actually carry. That optimises for a comfortable number, and on a **fidelity**
experiment the host count is not a cost to minimise — it is the thing being reproduced.

Two levers were wrong, and only one of them was the reduction.

**1. The per-VM sizing was mine, not the evidence's.** `generic-endpoint` had been given
`{cpu: 1, ram: 2Gi, disk: 20Gi}` — my choice, made without asking what the node is *for*. It emits
a few KB/s of background traffic. At `1Gi/10Gi` the same host count costs half the RAM, and nothing
about the role needs more. Right-sizing bought more fidelity than the reduction saved.

**2. More coverage was still available.** The 32 hosts still role-less after C3 were not the
unclassifiable residue — several carried *strongly* discriminating dial evidence:

| dialled | new role | conf | why |
|---|---|---|---|
| `1985/udp` | **`network-device`** | 0.7 | **HSRP** — only routers and L3 switches speak it; among the most discriminating dial signatures there is |
| `9997/tcp` | **`log-shipper`** | 0.6 | the *opposite direction* from the 9997 listen row: accepting = indexer, dialling = forwarder |
| `1900/udp`, `5353/udp` | `generic-endpoint` | 0.3 | SSDP/mDNS discovery — sent to a multicast group, which is why dial evidence exists with no peer to point at |

`network-device` is a distinct role rather than `gw` on purpose: the compiler *synthesises* a `gw`
to join the twin's segments, and a second source of that role would collide with it. These are the
real network's own L3 devices, observed, and they sit in the twin as what they are. Its persona is
`broadcast-chatter`, because that is what HSRP *is* on the wire — periodic multicast announcements,
not a client/server exchange.

**Result — 87% of the real network's observed host population, deployed at full scale:**

| | iter 1 (reduced) | iter 1b (full) |
|---|---|---|
| role coverage | 77.3% | **86.5%** (122 of 141 placed) |
| VMs | 49 | **123** |
| nodes / segments | 28 / 18 | **38 / 26** |

```
        twin needs        dev45 free
vCPU    140               80 cores      1.75:1 overcommit — these are light emitters
RAM     173 GiB           243 GiB       70 GiB headroom
disk    1,670 GiB thin    5.55 TB
```

No `--min-occupancy`, no `--max-count`: the cast is emitted whole. `axrange te validate` passes
with **0 findings in all three passes** at 26 segments and a `gw` carrying 26 legs.

**The 19 hosts still role-less are the honest residue** — hosts that dialled none of
443/53/123/1900/5353/1985/9997 in any window they were seen in. They are recorded in the
compiler's `population` block, not silently dropped.

---

## Iteration 2 — the twin recreated the lab it was deployed into

The 12-VM baseline deploy reached 146/159 and then sat on one `provision-app` for 11 minutes. Not
a slow apt — inside the guest:

```
route:   default via 10.80.64.1 dev eth0     <- the twin's own gw
DNS:     DNS-FAIL          egress: CURL-FAIL          apt: no process at all
```

And on the twin's router:

```
eth4  10.80.64.1/24      <- a twin LAN leg
eth5  10.80.72.1/24      <- another twin LAN leg
eth8  10.80.72.75/20     <- WAN uplink, DHCP from the REAL lab network
default via 10.80.64.1 dev eth8
```

The lab's real default gateway is **10.80.64.1**. The twin built a LAN interface carrying that
exact address, so the router's own route to the internet resolved to one of its own legs. Egress
was structurally dead. The twin's `10.80.72.0/24` also overlaps the network the **capture sink
itself** lives on (`XPress-SW-A`, 10.80.72.43), and `10.80.65.0/24` overlaps **CorpAPCN** (10.80.65.7).

### GAP-C4 — a cast reproduces the source's address space with no awareness of where the twin must run *(aximprint defect, CRITICAL for deployability)*

Faithfully reproducing observed CIDRs is exactly right for a *description* and can be fatal for a
*deployment*. Nothing in the capture could reveal this: it is a property of the **destination**, so
it cannot be inferred — only declared.

**Fix: `cast --relocate <prefix>`.** Segment *i* (already sorted by CIDR, so the mapping is
order-stable) becomes `<a>.<b>.i.0/24`. Opt-in, because the default must remain a faithful
description; the caller names the prefix because only they know their lab.

The observed CIDR is written into per-field provenance as `observed_cidr`, and the evidence class
drops from `observed` to **`defaulted`** — a relocated address is no longer an observation and must
not claim to be one. Relocation *moves* an address; it never loses one.

> **Superseded in shape, not in reasoning, 2026-09-04** (`docs/WORK_ORDER_reference_ingest.md`
> S1, CHANGELOG same date). The rule above is exactly right and is now stated more precisely: a
> relocated CIDR is a **declared deployment transformation**, which never enters precedence at
> all and is applied *after* resolution. `observed_cidr` is gone as a key — the pre-transformation
> reading now lives in `transformation: {kind, reason, from: {evidence_class, value, signal}, to}`
> — and the class is spelled `compiler`, `defaulted` having been renamed with its meaning
> unchanged. What that shape buys, which a flat `defaulted` could not: a scorecard can tell a
> value the compiler MOVED from one it INVENTED, and score the first as *reproduced under a
> declared transformation* rather than as a mismatch.

**Two bugs in my own fix, both caught before deploy and both pinned by tests.** `segments[]` holds
the relocated CIDR while every internal lookup keys on the observed one, so the node→segment lookup
and the conduit-endpoint lookup each raised on the first relocated cast. Neither is reachable
without passing `relocate`, which is why they get dedicated tests rather than relying on the
existing suite.

Also removed a `_host_ip` helper I had written speculatively: the compiler emits no explicit node
IPs at all (axRange allocates within the segment), so it was dead on arrival.

**Result:** 26 segments relocated into `10.200.0.0/16`, node/conduit counts unchanged.
**1782 passed** (+4), determinism invariants hold.

**A note on the comparison, which this does not damage.** The shape fingerprint was deliberately
built on roles and distributions, never on addresses — precisely so two different networks could be
compared at all. Relocating the twin therefore costs the experiment nothing.

---

## Iteration 2b — the relocated twin stands up, and a confound worth declaring

**GAP-C4's fix confirmed live.** From inside a deployed guest:

```
eth0     10.200.6.2/24              <- relocated
default  via 10.200.6.1 dev eth0    <- the twin's own gw, no longer shadowing the lab's
DNS-OK   HTTP:200                   <- egress works
```

Before the fix the same probe returned `DNS-FAIL` / `CURL-FAIL` with no apt process running at
all. The router now carries LAN legs at `10.200.x.1` and reaches the real gateway `10.80.64.1`
over its WAN leg without either shadowing the other.

**113 of 117 assigned VMs carry a scripted (actually emitting) persona** — because
`generic-endpoint` is 102 of them. Only 4 are `real-service` no-ops. The twin will generate
traffic rather than sit inert, which is the precondition for the whole comparison.

### Declared confound: the default agents

axRange installs a Prometheus exporter and a Wazuh defense agent on every node
(`scenario.default_agents`, default-ON). Two consequences, both recorded before measuring rather
than discovered in the results:

1. **They are traffic the source network does not have.** Any 9100 / 1514 / 1515 flow in the twin
   capture is axRange's own instrumentation, not a reproduction of CorpAPCN, and must be excluded
   when reading the fingerprint.
2. **They dominate the deploy.** 203 agent installs at ~40 s each is ~2.5 h of the build — the
   other 1,138 ops took ~30 min between them.

Not disabled, deliberately. `scenario.default_agents` is an IR field the twin-config expander does
not expose, so switching it off means a schema change to a layer whose byte-identical `plan_id` is
a pinned regression guard — a poor trade to save a **one-time** cost, since `--update` iterations
will not reinstall them.

### Prediction, recorded before the measurement

`generic-endpoint` dials **443**; the twin's `web-tier` installs nginx, which serves **80**. A
refused TCP connect is 2 packets, not a transfer (loop 1, GAP-L3's neighbourhood). So the
STRUCTURAL axes — role census, role→role edges, fan-out — should hold, while the VOLUME axes come
in far below source. If that is what the numbers show, the fix belongs to axRange's persona/content
side, not to aximprint's inference. Recorded now so the comparison can refute it rather than be
rationalised after the fact.

---

## Iteration 3 — the cast emitted a traffic profile the twin could not run

Twin up: **123/123 VMs, 1341/1341 ops, 0 failures.** Tap applied across 26 segments / 148 ports.
Then `tfc deploy` refused outright:

```
persona 'corp-workstation' requires role(s) ['egress-proxy', 'file-server', 'mail-server']
which the target twin has no host for
```

Two of the seven assigned personas were unappliable — `corp-workstation` (3 missing roles) and
`web-tier` (missing `db-tier`).

### GAP-C5 — `requires` was claiming structural dependency for peers that are merely typical *(axRange persona-catalog defect)*

The schema is explicit about the distinction: `optional: true` means *"I talk to this peer IF
PRESENT"*, and the default means *"I structurally can't function without this peer (a domain-joined
workstation NEEDS its DC)"*. `corp-workstation` already used `optional` correctly for
`dns-resolver` and `print-server`, with a comment arguing a twin without them is *"perfectly
realistic, not broken"*.

The same argument covers the other three and had simply not been applied. A workstation needs its
domain controller; it does not structurally need a file share, an egress proxy, or an on-prem mail
server — hosted mail and direct egress are the common case. Likewise a `web-tier` with no database
behind it is a static site or a reverse proxy, not a broken web tier.

So `requires` is now `[domain-controller]` and `[]` respectively, with the rest resolve-if-present.
**This is a correction of a mis-classification, not a loosening of a check** — the constraint still
fails loudly for a genuinely structural peer.

**Why it only surfaced now.** Every twin this catalog had been exercised against was authored by
hand from the same vocabulary — `corp-enterprise` ships a file server, a proxy and a mail server,
so the hard requires were always satisfied by construction. A twin **cast from real traffic**
contains whatever the network actually ran, which here included no SMB server, no proxy and no
on-prem mail. That is the class of defect only a cast-from-reality can expose.

### Follow-on recorded, not fixed: cast can emit an unappliable profile

The deeper issue is that `cast` assigns a persona to a role without checking that the persona's
required peer roles exist in the twin it is building — it cannot, because aximprint deliberately
does **not** depend on axRange's persona catalog at runtime (`roles.yaml`'s own note: *"a separate
repo on its own release cadence"*). The catalog already copies `persona_cadence` across that
boundary for exactly this kind of check; a `persona_requires` field would let `cast` drop or
caveat an unsatisfiable assignment instead of emitting a profile that fails at apply time.

Not built here: the axRange fix unblocks this run, and adding a second copied field is a
contract-shaped change that deserves its own pass rather than being smuggled into a fix for
something else.

---

## Iteration 4 — the first real measurement

Twin live: **123/123 VMs**, tap across 26 segments / 148 ports, 111 of 117 personas driving
(6 lost to transient `HTTP 596 Broken pipe` on the QGA exec channel under 123-VM load), **335 pps**
reaching the sink. Both captures then took the identical Zeek → ingest → materialize path.

```
                    source      twin
placed hosts          141        118
roled                 122        113
segments               32         25
edges                1437        555
role_coverage       0.865      0.958

role_census   Jaccard 0.300     role_edges 0.200     fan_out_degree 0.750
```

### What reproduced exactly

The three roles driven by **scripted** personas came back with the population **exactly** right:

```
corp-workstation    6 -> 6        dns-resolver 4 -> 4        generic-endpoint 102 -> 102
```

That is the strong result of this iteration and it is worth stating plainly: **where the twin
emits, it reproduces its source's structure exactly.** The cast → deploy → observe → re-infer round
trip is sound.

### GAP-C6 — six of nine roles are not re-inferable from the twin's own traffic *(the central fidelity gap)*

`domain-controller`, `hypervisor`, `log-shipper`, `monitoring-collector`, `network-device` and
`web-tier` all exist as deployed VMs and **all vanish when the twin is observed**. Three distinct
causes:

1. **`real-service` personas are no-op emitters, and the twin does not run the service either.**
   `domain-controller` and `monitoring-collector` map to personas whose driver only *asserts* a
   daemon is running; the catalog gives those roles `install: []`, so no AD and no Prometheus is
   ever installed. The node emits nothing and serves nothing — it is invisible by construction.
2. **`hypervisor` has `persona: null`,** which is a truthful statement (axRange ships no such
   persona) and leaves the node completely inert.
3. **`web-tier` serves 80; `generic-endpoint` dials 443.** The connection is refused, so no
   listener is ever proven and the role is never inferred — and because `role_edges` needs a role
   at *both* ends, the whole `generic-endpoint -> web-tier` edge family disappears with it. That
   single mismatch accounts for a large share of the 16 source-only edges.

**Why this matters beyond a low Jaccard.** A twin whose roles cannot be re-derived from its own
traffic cannot be used to test anything that keys on those roles — detection rules, asset
inventory, segmentation policy. Reproducing the *host count* is not the same as reproducing the
*network*, and this is the measurement that tells the two apart.

**`bastion-jump` (twin-only) is not a defect.** The twin's `hypervisor` node is a plain Debian VM
with sshd; it serves 22 and nothing else, so the catalog correctly reads it as a jump host. The
rule did its job — the twin simply is not what it claims to be. Fixing the twin fixes the label.

### The other axis: the twin is too uniform

```
fan_out_degree      source      twin
  1                    230        27
  2-5                   84       109
  6-20                  27         0     <- absent entirely
  21+                   11         4
```

The source has a long tail of hosts that speak once and hosts that speak to dozens; the twin has
almost everything clustered at 2–5 peers, because 102 of its 123 hosts run the *same* persona with
the same emit list. `generic-endpoint -> dns-resolver` is 69 edges in the source and **204** in the
twin — over-produced, not under-produced.

That is the honest cost of GAP-C3's `generic-endpoint`: it bought host-count fidelity (7.8% → 86.5%
coverage) at the price of behavioural *variety*. Both are real, and the fingerprint shows both
rather than averaging them into one flattering number — which is exactly why it was built as
per-axis rather than as a single score.

---

## Iteration 5 — the fan-out fix worked, over-corrected, and caught my own metric lying

Applied two changes to `generic-endpoint`, both prompted by iteration 4's measurement:

- **`peer_scope: all`** on the infrastructure emits — the fan-out lever the maxrate `loadgen`
  family used — so each endpoint reaches all 4 resolvers and both web tiers instead of one peer.
- **an `http/80` emit alongside the existing 443**, because the twin's nginx serves 80 and the
  443 emit was being refused.

Traffic went **335 → 1,869 pps** (still ~70× below the measured 132,000 pps lossless ceiling).

### Both predictions held

```
                            before    after    source
web-tier in role census      absent    2 -> 2   EXACT
generic-endpoint->web-tier   absent      205      298
edges                           555     1256     1437
fan_out_degree Jaccard        0.750    1.000
role_census Jaccard           0.300    0.400
```

GAP-C6's web-tier third is closed: completing a real connection proved the listener, which
restored both the role **and** the edge family that needed a role at each end.

### ...and it over-corrected

```
degree     source   twin
1             230     14
2-5            84      7
6-20           27    102     <- everything piled here
21+           11       4
```

`generic-endpoint -> dns-resolver` went 69 → **817**. The source is dominated by hosts that speak
to *one* peer; the twin is now dominated by hosts that speak to 6–20. **One uniformity was traded
for another** — which is what a single persona driving 102 of 123 hosts will always produce,
whatever its emit list. The real fix is population *variety*, not a different uniform setting.

### GAP-C7 — the fingerprint's own headline metric could not see this *(defect in this experiment's harness)*

`fan_out_degree` scored **key-set Jaccard 1.000** on the distribution above. All four buckets
existed, so a membership measure called it perfect while the shape was inverted.

That is the same class of error this project keeps finding elsewhere: a green number computed over
the wrong question. Added **total variation distance** over the normalised distributions, reported
*alongside* Jaccard and never blended into it — they answer different questions, and averaging them
would hide precisely the case that motivated the change:

```
role_census   Jaccard 0.400   TVD 0.066   <- vocabulary thin, but population shape excellent
role_edges    Jaccard 0.250   TVD 0.733   <- wrong on both axes
fan_out       Jaccard 1.000   TVD 0.727   <- perfect membership, near-disjoint shape
```

The `role_census` line is the one that changes the story: TVD **0.066** says the twin's *population
mix* is very close to the source's. The low Jaccard is about missing role NAMES (5 of them), not
about getting the proportions wrong. Reading Jaccard alone would have understated the twin; reading
Jaccard alone on fan-out would have overstated it. Both errors, opposite directions, same cause.

### What remains

Five roles still absent — `domain-controller`, `hypervisor`, `log-shipper`, `monitoring-collector`,
`network-device`. Causes unchanged from iteration 4: `real-service` personas that emit nothing on a
twin that also installs no service, `persona: null` on `hypervisor`, and 6 personas still failing to
launch on transient QGA `HTTP 596 Broken pipe` under 123-VM load (4 of which are the
`network-device` group).

---

## Iteration 6 — stop referencing archetypes; synthesize personas from the evidence

**Authority: user, 2026-08-27** — *"was not expecting the twin to use predefined personas as is.
Depending on the role and traffic profile, create new personas and save them along with the twin
config in a single folder."*

That reframing dissolves GAP-C6/C8 rather than patching them. The starter catalog is a set of
hand-authored **archetypes** — what a corp workstation *typically* does. Right thing to ship,
wrong thing to drive a twin cast from a real capture with, for two reasons this loop had already
measured: an archetype emits what its author imagined rather than what the network was seen doing
(so role and persona disagree — GAP-C8, seven rows), and its peer list may name roles the observed
network never ran (so the profile will not apply at all — GAP-C5).

A persona **generated from the evidence** has neither problem *by construction*. Its emits are the
ports the role's own members were observed dialling; its peers are roles this twin actually
carries. The fixed-point property stops being something to maintain and becomes a tautology.

### GAP-C9 — the pipeline could not carry port evidence to the compiler

`cast` reads the IR, and the IR carried the **role** but not the observations it was inferred
from. Three additions: a `dials_port` assertion in the extractor mirroring `listens_on`
(`observed` — dialling a port is something the sensor genuinely saw, unlike the role); the dial
census in the belief-store snapshot beside `listens`; and `listen_ports`/`dial_ports` columns on
`ir_entity`. Assertions went 8,562 → 11,378 on the same capture.

**A latent defect found on the way, worth more than the feature.** `_columns_from_ddl` parses the
schema by splitting on commas and does **not** strip `--` comments — so documenting the new
columns turned the comment's own prose into column names (`--`, `kept`, `unlike`), displaced
`listen_ports` entirely, and produced a count mismatch pointing nowhere near the cause. Fixed at
the parser: *a schema file that cannot be commented is a schema file nobody explains.*

### `aximprint/synth/personas.py`

One persona per role with dial evidence, `drive: scripted` always — `real-service` asserts a
daemon the twin was never told to install, which is how six roles became invisible in their own
capture (GAP-C6).

| field | derived from |
|---|---|
| `emits[].ports` | the role's members' dial census |
| `emits[].peer_role` | whichever role in **this twin** serves that port; reserved `multicast`/`broadcast` for group ports; emit dropped if nothing serves it |
| `volume` | `bytes_per_window_q` ÷ `flows_per_window_q` → bytes per flow |
| `cadence` | **rate-matched** — spacing such that `tier_bytes / interval` equals the observed byte rate |
| `os_family` | the role's catalog image |

**Four calibration defects caught before deploying any of it**, each of which would have produced
a confidently wrong twin:

1. **Volume read per-window instead of per-flow** put every role in `bulk` — 102 endpoints at
   ~7 MB/s each, roughly 5.7 Gbit/s, **above the capture path's measured 2.6 Gbit/s ceiling**. A
   twin that floods its own sensor is not a faithful twin.
2. **Cadence derived from flow count.** A fire is not a flow: one `bulk` fire is 64 MiB, so firing
   at the source's ~9 flows/minute offered 8.7× the observed rate. Now rate-matched.
3. **Ephemeral ports (≥32768) treated as services** — the local end of somebody's outbound
   connection became a peer's listening port, inventing emits aimed at ports nothing answers.
4. **First-wins peer resolution** produced `smb → dns-resolver`; now a vote across all entities
   observed listening on that port.

Plus `optional: true` on every synthesized emit — every peer is real by construction, but
axRange's PSN-090 demands non-optional peers be declared in `requires[]`, and hard requires are
precisely what made a cast profile unappliable (GAP-C5). "Talk to it if present" satisfies the
rule *and* is what is actually true.

### axRange: `AXRANGE_PERSONAS_DIR`

The registry loaded only its own bundled catalog, so a cast-shipped persona was unreachable. Now a
colon-separated search path, later directories overriding earlier on id collision — the local,
capture-derived persona wins over a same-named starter archetype, which is the useful direction.
An env var rather than a flag: personas resolve at import time in the traffic tier, the validator
and the drivers alike, and threading a flag through three places invites them to disagree.

### Result

**9 personas synthesized**, shipped in one folder with the twin config that references them, all
validating clean against axRange's own PSN catalog. Offered load ≈1.0 Gbit/s aggregate against the
source's measured 2.0 Gbit/s and a 2.6 Gbit/s ceiling — the right order of magnitude, with headroom.

```
cast-log-shipper      emits 9997 -> monitoring-collector   <- the exact signature its role is inferred from
cast-network-device   emits hsrp/1985 -> multicast          <- ditto
cast-domain-controller emits real traffic                   <- was a real-service no-op
```

**1782 passed; determinism invariants hold.** A `SPEC_network_ir.md` update came with the schema
change, enforced by the repo's own spec-vs-code column guard.

---

## Iteration 7 — synthesized personas on the twin, and the capture ceiling this loop actually hit

`te deploy --update` against the standing 123-VM twin: **COMPLETED**. The te-tier diff was empty
(the change was persona-side), so every op skipped rather than refusing — a weaker proof of that
feature than a real add/remove would be, and worth saying so rather than claiming a live-verify.

`tfc deploy` with `AXRANGE_PERSONAS_DIR` pointed at the cast's own folder: **117/117 personas
driving**, including the six that had persistently failed on transient QGA `HTTP 596 Broken pipe`.
The 111 `destroyed` rows are the archetype instances de-scoped by the tier's idempotent replace.

### The measurement that matters

```
                     archetypes      synthesized     source
offered traffic       1,869 pps       64,196 pps        —
                                       618 Mbit/s    2,022 Mbit/s
```

Three orders of magnitude closer. The archetypes were not merely *wrong in shape* — they were
emitting a rounding error's worth of the network's actual load.

### GAP-C10 — segmentation offload silently destroys a mirrored capture *(axRange adapter defect)*

Realistic per-flow sizes immediately exposed a ceiling the previous rates never could:

```
tc mirred overlimits   0            <- the mirror was fine
gretap pfifo/txqlen    correct      <- the maxrate fix was already applied
gretap tx              19,067 errors + ~1.1M dropped across 26 gretaps
worst two              14% and 33% loss at only 1,698 and 195 pps
```

**Loss at 195 pps is not a rate ceiling — it is frame SIZE.** A gretap is created `mtu 1500` while
advertising `gso_max_size 65536`, and a tapped VM port has GSO/TSO/GRO on by default, so the kernel
hands the tunnel 64 KB super-frames it cannot encapsulate. They are dropped silently: a mirror is
fire-and-forget, so there is no sender to notice and nothing upstream reports a problem.

**This confirms a hypothesis the max-capture-rate run recorded and could not prove** — *"most
likely occasional oversized/GSO frames ... Unconfirmed."* Its drops were not rate-correlated, so it
had no lever; the `bulk`-tier synthesized personas made it reproducible on demand.

```
GSO off on tapped VM ports    ->  9 of 11 gretaps to zero
GSO off on the gretaps too    ->  2,448,400 frames / 30s, ZERO dropped, loss 0.0000%
```

Both halves are needed and neither is sufficient — the tunnel's own `gso_max_size` was the
dominant one. Fixed in the axRange adapter rather than left as a runbook step, and each half
placed where it survives: the gretap assertion in `attach-gre-tunnel`'s *idempotent* section so an
adopted tunnel heals, and the source-port one *inside the mirror hook*, because a tap netdev is
recreated on every VM boot and a one-shot `ethtool` would be lost at the first reboot exactly like
the mirror itself.

### Also this iteration: the `{role: adds}` fix reverted, deliberately

> **The premise in the next sentence was measured FALSE on 2026-09-04 and is superseded**
> (`docs/WORK_ORDER_reference_ingest.md` §7.1, CHANGELOG same date). The wire carries the forest
> name plainly, in **observed** rows: `BW-L$/AXELLIO.DOM`, `desktop-21n0pq1$/AXELLIO.DOM`,
> `matthew.admin/AXELLIO.DOM` (Kerberos) and `axdc01.Axellio.dom`, the DC itself (NTLM).
> `ir_identity` on this run is 730 rows — 708 `dns`/`referenced`, which is what was seen here,
> plus **22 observed** kerberos/ntlm/dhcp rows carrying the realm. The visible 97% was the noise.
> What is actually in the way is a **parser** gap, not an evidence one: the realm survives only
> inside `Identifier.raw`, dropped by design at the entity parser and with no column at the IR
> boundary. Closing it is that work order's S5; making the DC serve 88/389 is its S4. Left in
> place rather than rewritten, per this file's own record-don't-revise discipline — the
> conclusion below (the row stays reverted *today*) is unchanged; only its reason is.
>
> **AND THE CONCLUSION ITSELF IS NOW REVERSED — 2026-09-05.** `roles.yaml`'s `domain-controller`
> row carries `install: [{role: adds}]` again, and a cast twin stands up a real domain
> (CHANGELOG 2026-09-05, "protocol-faithful services"). What unblocked it is not the evidence
> the blockquote above corrects — that finding stands and still needs S5 — but a **ruling**:
> axRange `docs/WORK_ORDER_protocol_faithful_services.md` §10.1 **D4** (Authority: user, ruled
> 2026-09-04) makes the twin's forest the **lab realm `corp.axr.lab`**, `compiler`-class, so no
> observed name is reproduced at all and the objection this section records — *"emitting a domain
> name nothing had extracted would fabricate the single most identifying string in the twin"* —
> is answered rather than overridden. The two `("domain-controller", ...)` exceptions in
> `scripts/check_role_fixed_point.py::DECLARED` are deleted with it; **24 catalog rows, 24 are
> fixed points**. Everything below is left standing as the record of why it took three months.

Making a cast twin's domain controller re-inferable needs AD promotion, and axRange requires
`role: adds` to be paired with `provides: [{domain: <fqdn>}]`. **Nothing in a capture supplies an
AD forest name** — `ir_identity` on this run carries only external DNS (`apple.com`,
`meraki.net`). Emitting one would fabricate the single most identifying string in the twin, which
is a different class of default from `image`: an image is a substitutable container for behaviour,
a domain name is an identity other things assert against.

So it is a **declared exception** in `check_role_fixed_point.py` rather than papered over, and the
honest state is recorded: a cast twin's DC is not re-inferable from its own traffic until domain
extraction from Kerberos/LDAP exists.

---

## Iteration 8 — the export contract, and a semantic reversal caught by review

### GAP-C11 — the IR's conversation graph and node census were never exported

The Console's **Logical** view is the IR's L3 structure *plus the conversation graph*; its
**Physical** view is the IR's *full observed node census from the tap vantage*, of which a deployed
twin is a scaled-down subset (Authority: user).

Neither can be drawn from the `twin_config` a cast emits. That document is a deliberately **lossy
projection** — `cast`'s D5 filter drops role-less hosts, and it carries no traffic edges at all.
Rendering it and calling it the imprint's topology presents a subset as the superset.

A reviewer challenged the claim that this was unfixable ("axRange's declarative IR carries no
observed traffic, so there is nothing for an arc band to plot") and **the claim was wrong**.
`ir_edge` has carried `bytes`/`flows`/`evidence_class` per (src, dst, proto) since the IR existed.
What was missing was an **export**, not the data.

`synth/imprint_bundle.py` now emits a third bundle artifact:

```
<id>.yaml               twin_config      -- the deployable projection
<id>.provenance.json    per-field provenance
<id>.topology.json      THIS             -- the observed superset + conversation graph
```

Measured for `corpapcn-twin`: **786 nodes · 32 segments · 1,437 edges (1,158 byte-weighted)**, of
which **430 nodes carry `role: null`** — exactly what `twin_config` drops. `census` states the
observed total and the roled subset so no consumer recomputes either; the deployed twin is 123 VMs,
so the census really is the superset. Every row keeps its `evidence_class`.

axRange reads the bundle and never imports aximprint — the Track C boundary. Five regression tests
pin the property that matters: **the census must stay the superset.** If this exporter ever starts
filtering the way `cast` does, the Physical view silently becomes a second copy of the twin.

### The reversal worth recording

The Console work had redefined Logical/Physical from "views of the aximprint IR" to "the axRange
twin's structure vs its PVE placement" — the lossy projection the earlier, user-authored definition
explicitly refused. The CHANGELOG framed it as relaxing over-caution; it was not, and under
`CLAUDE.md`'s Design Document Maintenance a user-authored decision is a confirm-first trigger.
Surfaced; the user restored the original semantics, and what had been built moved to a fourth
"Structure" tab. **This is why the export had to exist now** — restoring the semantics without it
would have reproduced the empty views that started the Console work.

---

## Iteration 9 — the measurement: observation-derived personas vs hand-authored archetypes

First fingerprint since persona synthesis (GAP-C9) and the GSO fix (GAP-C10).

**The extraction failed first, and the reason is itself a finding.** A 60 s window of the twin's own
traffic is now ~4.6 GB, and ten of those over HTTPS exceeded the appliance read timeout —
`aximprint acquire` has no timeout knob. **Making the twin faithful in volume broke the tooling
that measures it.** 10 s windows fixed it (10/10 extracted, 7.9 GB, 46 s). A scaling limit of
`acquire`, recorded rather than worked around silently.

Worse, that first run reported **exit 0 with no output**: the invocation piped through
`sed -n '/== 4\/4/,$p'` and the failure happened before stage 4, so the filter hid it. This
project's own recurring failure class — a green exit code over an empty result — committed by hand.

### The numbers

Twin span 110 s against the source's 660 s, so absolute counts are not comparable; the **shapes**
are what this measures.

```
                        iter 4    iter 5     NOW
role_census Jaccard      0.300     0.400    0.667
role_census TVD              —     0.066    0.149
role_edges  Jaccard      0.200     0.250    0.455
fan_out     TVD              —     0.727    0.055   <- the headline
```

**Fan-out TVD 0.727 → 0.055.** Iteration 5's `peer_scope: all` fix had *inverted* the distribution
rather than converging it, and key-set Jaccard scored that a misleading 1.000 — which is precisely
why GAP-C7 added TVD. Per-role emit lists derived from what each role was **observed** doing put
the shape within 0.055 of the source. The clearest evidence in this loop that observation-derived
personas beat hand-authored archetypes.

**Three roles returned** — `log-shipper`, `network-device`, `web-tier` — exactly those whose
synthesized personas now emit the signature their own rule infers from. GAP-C8's fixed point
closing end to end: cast → deploy → observe → re-infer.

### What is still wrong

**`role_edges` TVD 0.563.** The conversation *vocabulary* doubled (0.200 → 0.455) but the *mix* is
badly off: `generic-endpoint -> web-tier` is 298 in the source and 38 in the twin;
`corp-workstation -> web-tier` 76 vs 1.

**The 21+ fan-out bucket is empty** — the source has 11 hosts talking to 21+ peers; the twin none.

Both say the same thing: **the twin has no heavy hitters.** A real network's edge count is dominated
by a handful of very-high-fan-out hosts, and a per-role persona gives every member of a role the
same behaviour, so the twin's busiest host is only as busy as its role's average. Per-role synthesis
cannot produce this; it needs per-**population** variety — which `topology.json` now makes possible,
since it distinguishes the 430 role-less observed hosts from the 356 with roles and carries
per-entity `windows_seen` and dial evidence.

**Three roles remain absent**, all previously declared rather than newly broken: `domain-controller`
(AD promotion needs a domain FQDN no capture supplies), `hypervisor` (`persona: null`), and
`monitoring-collector` (`real-service` no-op whose 9997 signature the twin does not serve).

## Iteration 10 — activity banding, and two axRange findings the run surfaced

**GAP-C12 (activity banding) — built, deploying.** The unconverged axis was
`role_edges` TVD 0.563 with an empty 21+ fan-out bucket: the twin had no heavy
hitters. Measured cause, in the source:

    role                n     median      max          spread
    generic-endpoint    102   2,012       2,862,768    1,423x median
    dns-resolver          4  14,418         839,999       58x median

One persona per role synthesized from the role MEAN makes every host identical,
so a role that spans three orders of magnitude in the source becomes a flat line
in the twin. `activity_bands()` (single source of truth in `synth/personas.py`,
`_BAND_HEAVY/_BAND_LIGHT = 10.0/0.1`) splits each role on its own median;
`compiler.py` groups on `(role, band, segment)`; personas synthesize per
`(role, band)`. 55 node groups (was 38), 123 VMs, 32 assignments (was 7), 12
personas. Rates are genuinely differentiated, not just renamed:

    cast-generic-endpoint-heavy   11 emits   2.190 MB/s/host  (bulk, cyclic:337s)
    cast-generic-endpoint         10 emits   0.000 MB/s/host  (low,  cyclic:31s)
    cast-generic-endpoint-light    8 emits   0.000 MB/s/host  (low,  cyclic:512s)

aximprint suite 1787 passed; determinism invariants hold.

**GAP-C13 (axRange) — a twin config could not opt out of the default-agent
sweep.** `expand.py` emits a FIXED `scenario:` block and `TCBase` sets
`extra="forbid"`, so `scenario.default_agents` — which exists in the IR and is
ON by default — was unreachable from a twin config. There was no workaround
short of hand-editing the materialized IR. Fixed additively: an optional
`TwinConfig.default_agents` emitted ONLY when stated, so every shipped config's
materialized IR stays byte-identical (`plan_id` keys VMIDs, VM names and
standing-deploy lookup — LES-126 — so a cosmetic diff here is a live-cluster
hazard). Two regression tests pin both halves.

For this experiment the flag is off on FIDELITY grounds, not just speed:
node-exporter scrapes and wazuh-agent enrollment are twin-only traffic with no
counterpart in the CorpAPCN source, so they contaminate the fingerprint the twin
is judged by. The speed was the tell, not the reason — 140 of 143 remaining ops
were agent installs at 80-600s each (one timed out at 602s; GAP-015's known
egress dependency), i.e. 3-23 hours. After the change: 2 ops.

**Finding (axRange, no fix needed): an interrupted `--update` leaves the entire
removed generation LIVE.** `apply_update_tail` runs the removal sweep AFTER the
executor finishes. A SIGTERM mid-executor therefore leaves both generations
standing — here 196 VMs (123 declared + 73 awaiting sweep), all running personas.
Capturing in that window would have silently mixed two twin generations into one
fingerprint and looked entirely plausible. Never measure between an interrupted
update and its resume. This is correct-by-design behavior, not a bug — but it is
a measurement trap worth naming.

**Two false alarms of my own, recorded because the method matters more than the
result.** I twice claimed `--update` "grows without shrinking": first from a VM
count that swept in a separate standing twin's `te-*` VMs, then from a
block-buffered log I read as a complete record (the 11 "missing" removals sorted
after the last flushed line). Both times the alarming reading came from an
unreliable source I had not checked was authoritative. The diff was correct
throughout: 73 hosts removed, none of them in the new plan.

### Iteration 10 result — banding is a NET REGRESSION (measured, not predicted)

Deployed the banded cast to the standing twin, re-bound tap + tfc, warmed 20
min, captured 10 min through XPress-SW-A. The twin drove ~490 Mbit/s / 54.7 kpps
sustained (vs effectively flat before), so banding DID produce heavy hitters.
It did not produce fidelity:

    axis                     iter 9    iter 10   verdict
    role_edges TVD            0.563     0.518    marginal gain
    role_edges Jaccard        0.455     0.407    worse
    role_census Jaccard       0.667     0.600    worse
    role_census TVD             --      0.033    excellent
    fan_out TVD               0.055     0.511    BADLY regressed

**The diagnosis was right and the lever was wrong.** GAP-C12 correctly identified
that the twin had no heavy hitters. It fixed HOW MUCH each host talks. The
dominant error is HOW MANY PEERS each host talks to, which banding does not touch
and in fact worsens:

    fan-out degree   source   twin
    1                  230      61     <- source is 65% single-peer hosts
    2-5                 84      10
    6-20                27     104     <- twin piles up here
    21+                 11       2

Every synthesized persona emits to several peer roles, so giving MORE hosts an
active persona destroyed the source's long tail of quiet degree-1 hosts. The one
axis that was converged (fan_out 0.055) was converged partly by accident -- few
hosts were emitting at all.

Two specific edge errors worth keeping:
  * `dns-resolver->generic-endpoint`   source=3    twin=100
  * `corp-workstation->web-tier`       source=76   twin=6
The twin manufactures DNS fan-out it never observed, and under-produces the
source's single heaviest workstation edge.

**GAP-C15 (new, unfixed): the role fixed point fails on live twin traffic.** The
comparison found a TWIN-ONLY `bastion-jump` role -- the twin's own generated
traffic caused aximprint to INFER a role the source never had. `check_role_fixed_
point.py` passes 24/24 against synthesized persona specs; it does not close
against the traffic those personas actually put on the wire. A spec-level fixed
point is not the property that matters.

**Next lever (not yet built): per-role peer-COUNT fidelity.** The source's
fan-out distribution is directly measurable per role, exactly as activity bands
were. A persona should carry a target peer count, with `peer_scope: one` the
default and multi-peer the exception, instead of every persona fanning out to
every role it has an emit for.

**Honest note on method.** I set `role_edges` TVD as the number that decides
convergence, and it improved (0.563 -> 0.518). Reporting only that would have
been true and completely misleading: the same change regressed three other axes,
one of them tenfold. This is exactly why the fingerprint is per-axis and refuses
to emit a single blended score -- a scalar would have moved slightly in the
"right" direction here and hidden a net regression.

### The structural ceiling, measured — and why fan-out was never reachable

Iterations 8-10 chased `fan_out` with persona changes. It cannot be reached that
way, and the numbers say so plainly:

                                    source          twin
    entities                  190 local/596 ext   182/14
    edges with non-local dst  1218/1437 (85%)     74/1184 (6%)
    degree-1 hosts talking ONLY external   197/230        56/61

CorpAPCN is an internet-connected office network: 85% of its edges leave the
site, and 197 of its 230 degree-1 hosts talk ONLY to the internet. The twin is
air-gapped. axRange GAP-048 -- emits at the reserved `internet`/`egress` tokens
put NOTHING on the wire -- means those conversations cannot exist in the twin at
any persona setting.

Restricting to local->local (`fan_out_degree_local`, added this iteration) does
NOT rescue it, and the direction of the error is the tell: local-only TVD is
**0.785**, WORSE than the all-edges 0.511.

    local->local degree   source   twin
    1                        53       6
    2-5                      27       9
    6-20                      8     104

Source hosts have LOW local degree precisely because most of their conversation
is outbound. Twin hosts put 100% of theirs on the local wire because they have
nowhere else to put it. The same missing thing bounds both axes in opposite
directions, which is why no persona budget can satisfy both at once.

**GAP-C17 (new, designed not built): the twin needs an internet edge.** Measured
requirement, from the source:

    132 local hosts have external conversations
    distinct external peers per host: median 2, mean 4.5, max 53
    385 distinct external peers reached overall
    outbound protos: ssl 243, tcp 179, ntp 150, icmp 39, dns 17, http 13
    per-role median: generic-endpoint 3, corp-workstation 10, dns-resolver 10

Shape: an `internet-sink` node presenting a range of aliased addresses (~385
observed; a /24 covers the bulk) on a dedicated segment, with personas emitting
to it at a per-role peer count drawn from the measured distribution above.
Unanswered outbound flows would still be enough for the SHAPE axes -- Zeek
records an S0 conn, so the edge exists even with no responder -- though byte
volumes would need a real listener to be faithful.

**SAFETY CONSTRAINT, load-bearing:** the synthetic external addresses MUST stay
inside the twin, on a segment the gw does not forward. The twin's gw NATs to
vmbr0 and thence to the real lab uplink, so pointing "internet" traffic at
arbitrary public addresses would emit synthetic scan-shaped traffic onto a real
network. That is not acceptable regardless of how much it would improve the
fingerprint, and any implementation must make it structurally impossible rather
than merely avoid it by choice of range.

**What this means for the golden-twin question.** The twin is converging on what
it can control -- role census TVD 0.033, role coverage 1.000, and now per-role
emit budgets measured rather than constant. It cannot converge on fan-out while
it has no internet edge, and reporting that axis as a fidelity failure would
misattribute a structural gap to persona tuning. The fingerprint now states the
bound outright (`external_dst_edge_share`) instead of leaving a permanently-red
axis that invites more futile iteration.

### Iteration 11 — emit budgets: 5 of 6 axes improve, one regresses

Traffic-tier-only iteration (twin config byte-identical, so no VM churn: tfc
re-apply, warm-up, capture).

    axis                    iter 10   iter 11
    role_census TVD           0.033     0.025   better
    role_census Jaccard       0.600     0.667   better
    role_edges Jaccard        0.407     0.478   better
    role_edges TVD            0.518     0.583   WORSE
    fan_out TVD               0.511     0.450   better
    fan_out_degree_local TVD  0.785     0.635   better

The fan-out shape moved where the budget aimed it -- the 6-20 bucket went
104 -> 7 against a source 27, and twin edges fell 1184 -> 561.

**GAP-C15 resolved as a side effect.** Iteration 10's TWIN-ONLY `bastion-jump`
role -- the twin's own traffic causing aximprint to infer a role the source never
had -- is gone; twin-only roles 0, twin-only edges 7 -> 3. An over-emitting
persona was manufacturing a role signature. The role fixed point holds on live
traffic at this budget.

**The regression is diagnosable, and it is a selection bug, not a budget one:**

    generic-endpoint->web-tier      source=298   twin=127   (was 289 -- moved AWAY)
    dns-resolver->generic-endpoint  source=3     twin=103   (spurious; ~= 104 endpoints)

Cutting `generic-endpoint` to its measured 3 emits also cut the source's HEAVIEST
edge, because emits are selected by `ports.most_common(budget)` -- port dial
FREQUENCY -- with no reference to how much of the observed conversation each port
actually carries. A budget of 3 spent on three rare ports is worse than a budget
of 3 spent on the dominant one. **GAP-C18 (next): select emits by observed EDGE
WEIGHT, not dial-port frequency.** The dns-resolver->generic-endpoint edge at 103
vs 3 is the same failure from the other side -- a cheap port winning a slot it
should not hold.

Note the two axes disagree in an informative way: `role_edges` key-set Jaccard
IMPROVED (0.407 -> 0.478, the twin now reproduces more of the source's distinct
edges) while its TVD worsened (0.518 -> 0.583, the WEIGHTS on those edges got
further off). Exactly what a frequency-selected budget predicts: better coverage
of which conversations exist, worse fidelity on how big each one is.

### Iteration 12 — weighted selection: flat, and the measurement is not trustworthy yet

    axis                    iter 11   iter 12
    role_census TVD           0.025     0.025   flat
    role_edges Jaccard        0.478     0.478   flat
    role_edges TVD            0.583     0.608   worse
    fan_out TVD               0.450     0.609   worse
    fan_out_degree_local TVD  0.635     0.627   flat

Selection itself did what it was built to do -- the deployed
`cast-generic-endpoint` carries 1 emit at dns-resolver and 2 at web-tier, ranked
by observed local conversation. Two things swamp it.

**1. Band population, not per-host shape, sets the aggregate.** The LIGHT band is
the most numerous group (47 hosts of 102) and its own weighted budget bought
`dns` + `ntp`, both at dns-resolver, so it contributes zero web-tier edges. Role
edges are `sum(hosts x emits at that role)`, so the largest band decides the
histogram no matter how well each persona is shaped:

    generic-endpoint->dns-resolver  source=69    twin=206
    generic-endpoint->web-tier      source=298   twin=111

A per-role local fit cannot fix that; it needs a GLOBAL fit -- choose per-band
emits so the twin's total role-edge histogram matches the source's, not so each
band matches its own observations. Deferred deliberately (below).

**2. The channel is broken, so these numbers measure the wrong thing.** GAP-C19:
nothing in the twin listens, so every emit is a failed connect and every edge is
a 2-packet husk. Proportions measured through that are proportions of failures.
Tuning them further before fixing it would be fitting noise -- and worse, a fit
achieved against failed connects would have to be UNDONE once conversations
actually complete, since a completed exchange produces different flow counts than
a refusal.

**Order of work, deliberately: fix the cause first.** Iteration 13 deploys the
responders (GAP-C19) and re-measures. Only then is an edge-count fit worth
building, because only then does an edge row mean a conversation instead of a
rejection.

### Iteration 13 — listeners: 6 of 7 axes improve, and the mechanism is confirmed

    axis                    iter 12   iter 13
    role_census TVD           0.025     0.024   better
    role_census Jaccard       0.667     0.700   better
    role_edges TVD            0.608     0.518   better (best yet)
    fan_out TVD               0.609     0.516   better (best yet)
    fan_out_degree_local TVD  0.627     0.536   better (best yet)
    fan_out_degree_local Jacc 0.750     1.000   all buckets shared
    role_edges Jaccard        0.478     0.308   worse (artifact, below)

**The protocol mix proves the mechanism**, which no aggregate score would have:

    iter 11 (no listeners): icmp 225, udp 148, tcp 145, ntp 39, ssl 3, dns 1
    iter 13 (listeners)   : udp 149, icmp 141, tcp  99, http 35, dns 2, ssl 2
    source                : tcp 446, ssl 422, ntp 176, dns 87, udp 85, icmp 76

`http` appears for the FIRST TIME (35 edges) -- a protocol Zeek could classify because
web-tier actually answers. The fabricated `dns-resolver->generic-endpoint` edge
(103 -> 68 -> ABSENT) is gone. `monitoring-collector` became a visible role.

**The residual matches the known bug exactly.** dns is still 2, ntp 1, icmp still 141 --
because the dns-resolver responder's port-53 thread died on EADDRINUSE while its unit
reported `active` and its op PASSED (fixed in `937eeaa`, not yet deployed). The split is
clean and it is the strongest evidence in the run: the host whose listener WORKED
(web-tier -> http 35) improved; the host whose listener SILENTLY FAILED (dns-resolver)
did not move at all. A single blended score would have shown "somewhat better" and hidden
that the twin was still half-broken in a knowable way.

**`role_edges` Jaccard fell for a good reason.** The twin stopped emitting the spurious
ICMP-driven `dns-resolver->*` edges, so it lost several FAKE edges it had been sharing
with the source. Key-set overlap dropped while the distribution got closer (TVD 0.608 ->
0.518). Losing a wrong edge costs coverage and buys fidelity; only reporting both axes
makes that legible.

**Still the dominant error, now measurable through a working channel:**

    generic-endpoint->dns-resolver  source=69    twin=207   3x too high
    generic-endpoint->web-tier      source=298   twin=134   2x too low

That is the band-population imbalance from iteration 12 -- the light band is 47 of 102
endpoints and buys dns+ntp only. Worth fitting NOW, which it was not before: an edge row
finally means a conversation instead of a rejection.

### GAP-C20 — persona emits are port-shaped, not protocol-faithful

Chasing why the twin showed `dns: 2` even with a listener, I found the dns-resolver
nodes were never listener-less at all: they install bind9, and `named` has owned
:53 the whole time. My "nothing in the twin listens" diagnosis was right for
web-tier (which is why `http` appeared the moment it answered) and WRONG for
dns-resolver.

The real reason is one level down. The `scripted` driver emits CONNECTIVITY SHAPE
-- arbitrary bytes at a port number -- so `named` receives garbage on :53, never
answers a real query, and Zeek classifies the exchange as bare `udp`:

    generic-endpoint -> dns-resolver:  udp 103 edges (11054 flows)
                                       icmp 102
                                       dns    2

    source protocol mix: tcp 446, ssl 422, ntp 176, dns 87, udp 85, icmp 76
    twin   protocol mix: udp 149, icmp 141, tcp 99, http 35, dns 2, ssl 2, ntp 1

So the source is ssl/dns/ntp-shaped and the twin cannot be, at any persona
setting, until emits speak their protocols (a real resolver query, a real TLS
handshake, a real NTP exchange) rather than pushing bytes at the matching port.

**Scope, honestly:** this does NOT move the fingerprint's current axes.
`role_census`, `role_edges` and `fan_out` all count EDGES, and an edge row is
recorded whether or not Zeek can name the protocol. It matters for realism --
a defensive tool trained on this twin would see a protocol mix no real network
has -- and it is the ceiling on any future proto_mix axis. Recorded, not fixed:
fixing it means real protocol clients in axRange's scripted driver, which is a
larger change than this loop should absorb mid-flight.

**Also corrected, and it is the same lesson twice:** the responder now SKIPS a
port a real service already serves (EADDRINUSE is a success -- the goal is that
SOMETHING answers, not that our code does). Competing with bind9 for :53 was the
responder undoing the twin's own realism. Verified on all four dns-resolver
nodes: rc=0, unit active, coexisting with named.

### GAP-C21 — a per-persona fit cannot reach an aggregate the population decides

GAP-C18 ranked each group's emits by observed conversation weight. That shapes each
persona correctly and still misses the histogram, because a role edge is
`sum(hosts x emits at that role)` — so the most NUMEROUS band decides the aggregate no
matter how well any individual persona is shaped. Iteration 12 recorded this as "band
population, not per-host shape, sets the aggregate" and deferred it; this is the fix.
Measured across iterations 12/13, the light band is 47 of 102 endpoints and its own
weights bought `dns` + `ntp`, contributing ZERO web-tier edges — against a source whose
heaviest edge is exactly that one:

    generic-endpoint->dns-resolver  source=69    twin=207   3x too high
    generic-endpoint->web-tier      source=298   twin=134   2x too low

**The fix allocates the budget across DESTINATION ROLES first and fills it with ports
second.** Each group's budget is split across destination roles in the source's own
role-edge proportions (`allocate_budget`), then each slot is filled with the
highest-weight port serving that role. Bands inherit their BASE role's proportions,
which is what makes the aggregate come out right: the light band no longer spends its
47 hosts on whatever its own sparse observations happened to rank first.

Largest-remainder rather than plain rounding, for one specific reason: it cannot round
the biggest share away, and dropping the DOMINANT peer is the precise failure being
fixed. A role the source shows no role-to-role edges for falls back to plain weight
ranking — there is nothing to proportion against, and emitting nothing would be worse.

Effect at cast time, before any deploy: `cast-generic-endpoint-light` flips from
`dns`+`ntp` to `http`+`https` at `web-tier`. Projected from band populations,
`->web-tier` 134 -> ~244 (source 298) and `->dns-resolver` 207 -> ~55 (source 69).
Iteration 14 is the measurement of that projection — and what the projection was aimed
AT is itself retracted at the end of that section: the 298 it chased is 295 external
edges and 3 local.

**A note on the numbering, while this file is being reconciled.** C21 had existed since
`82bee14` — in `personas.py`'s comments and in that commit's message — and nowhere in
this journal, which is how its section comes to be written two iterations after its
code. Two other numbers were spent in conversation and never assigned to anything
written down: **C14 and C16 do not exist.** They are left as holes rather than
renumbered, because every reference to a C-number outside this file is a commit message
or a code comment, and renumbering would break all of them silently. The sequence
therefore runs C1-C13, C15, C17-C21. Several of those live inline in the iteration that
found them rather than in a section of their own (C8, C12, C13, C15, C17, C18, C19),
which is fine — C21 was the only one with no entry anywhere.

### Iteration 14 — proportional budgets: best result of the run

    axis                    iter 13   iter 14
    role_census TVD           0.024     0.016   best
    role_census Jaccard       0.700     0.778   best
    role_edges TVD            0.518     0.468   BEST
    role_edges Jaccard        0.308     0.429   better
    fan_out_degree_local TVD  0.536     0.534   flat
    fan_out TVD               0.516     0.552   slightly worse

The targeted error closed almost exactly as projected from band populations
(~244 predicted, 228 measured):

    generic-endpoint->web-tier      source=298   134 -> 228
    generic-endpoint->dns-resolver  source=69    207 -> 113

And the twin is CLEAN in a way it has not been before: zero twin-only roles (was 1),
one twin-only edge (was 6). The role census is effectively matched.

**Full trajectory of the two reachable axes:**

    role_edges TVD    0.563  0.518  0.583  0.608  0.518  0.468
    role_census TVD   0.033  0.025  0.025  0.024  0.016

**What is left, and which kind each is.**

TUNABLE:
  corp-workstation->web-tier  source=76  twin=12   -- 6 Windows hosts, budget 16 but
    only 8 emits available; its ports run out before its budget does.
  generic-endpoint->dns-resolver  69 vs 113 -- still ~1.6x, within reach of the same
    proportional mechanism at a finer grain.

STRUCTURAL (recorded, not chased):
  fan_out degree-1: source 230 vs twin 17. 197 of the source's 230 are hosts that talk
    ONLY to the internet (GAP-C17). Unreachable without an internet edge, and this is
    why `fan_out` is the one axis that did not improve with everything else.
  domain-controller / corp-workstation edges: Windows nodes get no responder (POSIX-only
    script), so they still refuse and under-produce forward edges.
  protocol mix: emits are port-shaped, not protocol-faithful (GAP-C20).

**Assessment.** Not a golden twin, and the distance left is now almost entirely
structural rather than tuning. Every axis the twin CAN control is at its best value of
the run; the axis it cannot control (`fan_out`, 85% external in the source) is the one
that stalled. That is the honest shape of the result.

**RETRACTED (iteration 15, `3dedba8`) — the headline above is the best result on a
MISLEADING metric, not the best result.** `role_edges` counts every source edge whose
two ends both carry a role, including every edge whose destination is on the internet.
**202 of this source's 204 `web-tier` entities are EXTERNAL**, so the 298 that
`generic-endpoint->web-tier` was being scored against is 295 external edges and **3
local** — a target ~99% of which the twin structurally cannot place (GAP-C17). It
closed the gap the only way it could: by MANUFACTURING local web-tier traffic through
its 2 local web-tier hosts. Measured, `generic-endpoint->web-tier` LOCAL is source 3,
twin **225**. The `134 -> 228` move reported above as "closing on 298" is that
manufacture.

`3dedba8` therefore restricted both `proto_weights` and `role_edge_targets` to LOCAL
destinations, and the fingerprint gained **`role_edges_local`** alongside `role_edges` —
the same correction `fan_out_degree_local` had already been given one metric over, and
it should have been made in both places at once. Additive, so every number above stays
readable as exactly what it was.

Re-scored over iteration 14's own capture, the honest axis reads **0.595**, not 0.468:

    axis                     iter 13   iter 14
    role_edges TVD             0.518     0.468   <- reported above; ~99% external
    role_edges_local TVD          --     0.595   <- what the twin can actually place

The two disagree by 0.13, and they disagree in the flattering direction. The "full
trajectory" table above is likewise a trajectory of the misleading axis across six
iterations; it stands as the record of what was optimised, not of what improved.

`role_edges` is not withdrawn and is still reported, because the GAP between the two
axes is itself the measurement of how much of this network the twin cannot reach. It is
no longer the axis that decides whether an iteration converged. `role_edges_local` is.

### Iteration 15 — the internet sink: the outbound axis moves, the local ones do not

GAP-C17, designed at iteration 10 and unbuilt since, is built. Four sub-roles
`internet-sink-{1,2,4,8}` held by 1/2/4/8 instances — 15 VMs — sit on a dedicated
segment `10.200.254.0/24`, and a persona composes its external fan-out from a binary
subset of them via `peer_scope: all`. The buckets are binary because `peer_scope` yields
either ONE peer or ALL of them and never a distribution: a single 16-host sink role
would give every host on the network the identical fan-out of 16, whereas 8/4/2/1
compose any fan-out in 1..15 out of nothing but existing axRange role machinery.
Against the source's measured distribution (median 2, mean 4.5, max 53 distinct
external peers), **7 of the 132 source hosts with external conversations exceed 15 and
are clamped** — a documented under-representation of the busiest hosts rather than a
hidden one.

**Safety, which GAP-C17 made load-bearing, and which is a structural property rather
than a careful choice of range.** The sink is reached by **role**, never by address.
axRange's persona `Emit` type has no address field at all, so a persona structurally
cannot name a destination, and peer resolution only ever yields in-twin host IPs. That
is what makes synthetic "internet" traffic unable to leave a twin whose gw NATs to a
live lab network. Evidence rather than assertion: moving the sink's CIDR leaves
`personas/` **byte-identical** — the address appears in no persona at all. Two further
properties of the range itself, both deliberate. `10.200.254.0/24` is inside
`10.200.0.0/16`, so "did anything leave the twin?" stays a single grep. And its third
octet does not end in zero, because `_node_suffix`'s `rstrip(".0")` eats a whole run of
trailing zeros: `10.200.200.0` collapses to `10-200-2` and `10.200.250.0` to
`10-200-25`, and **both of those are live segments in this twin**.

**Measuring it needed a second change, and that one found an axis that had been
computed and thrown away for fourteen iterations.** `locality` is a **sensor-stamped
entity flag, not an address property** — XPress-SW-A's Zeek carries `Site::local_nets`
over 10/8 — so the sink arrives stamped `local` with `observed`/0.95 evidence that
address-shape inference cannot override. No choice of range avoids that, which makes
the reclassification necessarily a post-ingest override: `fingerprint()`/`compare()`
now take `external_segments`, applied to the **twin side only** (the source has no
sink, and overriding both sides would silently re-measure the reference). `_is_local`
is the single gate for every locality-sensitive axis, so the whole change is one
predicate. In the same pass, `compare()` started **printing `external_dst_edge_share`**,
which had been computed and silently discarded since it was added — which is precisely
why fourteen iterations never saw the axis the sink exists to move.

**Also this iteration: `3dedba8`'s locality fix, completed in the four places it
missed.** Six sites now filter on locality rather than on merely having a role.
`activity_bands` (356 roled entities, 233 of them external; 12 local hosts moved
between bands) and `emit_budgets` (two node groups dropped that had been synthesized
purely from internet peers) are the obvious ones. **The sixth is the interesting one,
and it was found only because fixing `activity_bands` alone INVERTS**: an external
entity that loses its band no longer sorts into a band group, so all 233 collapse into
their role's BASE group and then dominate that group's volume census — driving base
`generic-endpoint` mean bytes/window from **11,622 to 85,726,890**, i.e. `low` -> `bulk`
across 102 endpoints, on the strength of hosts the twin will never deploy. Fixed
together, offered rate lands at **1.33 MB/s against the source's own 1.330**, down from
HEAD's 28.65 — 21.5x.

**And the harness stopped losing three facts to a re-cast**, two of which produce a
plausible-looking wrong measurement rather than an error. `scripts/` was never copied,
and the sink adds four responder filenames: after install `te validate` passed with
**zero findings** and `te plan` died at **exit 2**, because script text is read at
plan-build time and no validator rule checks that a referenced file exists — reached,
of course, with the old twin already destroyed. Now copied, with an install-time guard
that checks every referenced script on disk, where the remedy is free. `--sensors`
fills the tap plan the cast is right to leave blank. And node ids are expanded from
groups to instances, validated against axRange's own expander rather than inferred (it
reproduces all 138 instance ids), idempotently — the asymmetry is the trap, since
"always append `01`" is as wrong as "never append" and roughly a fifth of groups fall
on each side. Separately, `activity_bands`' only CALLER had never landed (`eb66ab4`),
so a fresh clone emitted 38 unbanded node groups where the standing twin has 55; the
deployed twin has depended on banding since iteration 10.

**The result.**

    axis                        iter 14   iter 15   source
    external_dst_edge_share      0.1096    0.8054   0.8476
    fan_out_degree TVD            0.552     0.265
    role_census TVD               0.016     0.033
    role_edges TVD                0.468     0.503
    role_edges_local TVD          0.595     0.618
    fan_out_degree_local TVD      0.534     0.572

The axis the sink was built for moved decisively — 0.1096 -> 0.8054 against the source's
0.8476 — and `fan_out_degree`, which is the same fact counted per host instead of per
edge, halved with it. The two honest local axes both moved slightly the WRONG way.

Deploy: `corpapcn-twin-0e4f7d58`, **138 VMs, 1242 ops, 0 failed**; tap across 148 ports;
tfc 106 node assignments driving **121 of 138 hosts**. The 17 not driven are `gw`, the
15 sink hosts (which listen and never emit), and `hypervisor`. Capture: **300/300
windows, 0 empty, 0 failed, 522 MB** — against 46 GB for the pre-fix twin.

**The finding, which is the point of this iteration.** The sink did what it was built
for and the honest local axes still got slightly worse, and the cause is specific
rather than a tuning miss:

    generic-endpoint->dns-resolver, LOCAL   source 38   twin 117 across 105 hosts
                                            ~0.37/host  ~1.1/host

The deeper number is the one that settles it: the source's **median** local degree for
`generic-endpoint` is **zero**. Most real endpoints have no local conversation at all in
a window — only internet ones. `allocate_budget` hands each host an integer row count
and splits it by proportion, so at a budget of 4 with an 80% external share every host
gets 3 external and 1 local. The smallest non-zero local allocation is one, so the twin
carries **a floor of ~1 local edge per host where the source's median is 0**.
`fan_out_degree_local` says the same thing from the other side:

    local degree   source   twin
    1                  53      9
    2-5                27     65

So the internet edge was **necessary but not sufficient**: it removed the *ceiling* — a
host can now be busy without being locally busy, which was structurally impossible
before — but the *allocator* imposes a local floor. That reframes GAP-C17 rather than
leaving it open: it is **closed as a ceiling**, and the successor constraint is the
per-host integer allocation, which is a different mechanism and gets measured on its
own terms rather than as more of the same tuning.

**One apparent regression that is not one, recorded because it would read as one.**
`role_census` now reports three roles "missing from the twin" — `web-tier`,
`domain-controller`, `hypervisor`. Two of those are the long-standing absences already
on the record: a cast twin's DC is not re-inferable until domain extraction exists
(iteration 7), and `hypervisor` has `persona: null`. `web-tier` is the new one, and it
is the override WORKING. All 235 twin `generic-endpoint->web-tier` edges now resolve to
sink hosts and are correctly classified external, so they leave the local census
entirely: against a source whose LOCAL count for that edge is 3, the twin went
**225 -> 0**. The manufactured local traffic that produced iteration 14's retracted
headline is gone, and losing it costs census vocabulary. That is the same trade
iteration 13 recorded when the twin stopped emitting spurious `dns-resolver->*` edges:
losing a wrong edge costs coverage and buys fidelity, and only reporting both axes
makes it legible.

### Iteration 16 — population-level allocation, and a bundled experiment that could not be read

Three iterations against one standing deploy. `corpapcn-twin-0e4f7d58` — the 138-VM twin iteration
15 stood up — did not move: 16, 17 and 18 are all `tfc`-tier changes with `twin_config.yaml`
byte-identical, verified per iteration rather than assumed. Every capture across the three is
**300/300 windows, 0 empty, 0 failed**.

Iteration 15 named its own successor constraint, and this is the attempt at it. `allocate_budget`
hands each host an integer row count and splits it by proportion, so at a budget of 4 with an 80%
external share every host gets one local row — **a floor of ~1 local edge per host against a source
whose median local degree for `generic-endpoint` is 0**. Nothing inside a persona can go below that
floor, because a persona is one document applied to many hosts: "30 of these 103 hosts talk locally
at all" is not a statement one document can make.

Built: `local_population()` measures, per `(role, band)`, the fraction of hosts with a local roled
peer, the local rows per BEARER rather than per host, and the chatter share. `synthesize_personas`
then emits up to three variants per group — `cast-<g>`, `-local`, `-chatter` — and
`plan_assignments` partitions each group's instances across them by an arithmetic stride. axRange's
traffic profile can already target a NODE and the installer already expands a group to its
instances, which is why a population split costs no redeploy.

**The chatter variant was not in the brief; the data forced it.** 19 of the 35 hosts in the source's
degree-1 local bucket talk **only** to a multicast or broadcast address. A group port can never
carry proto weight — its destination is a wire address with no role, and `proto_weights` counts
role-to-role edges — so it always sorted below every weighted protocol and was dropped the moment
the budget was spent. The most common shape of local host in the source was the one shape the twin
structurally could not emit.

    axis                        iter 15   iter 16   source
    external_dst_edge_share      0.8054    0.6653   0.8476
    role_edges_local TVD          0.618     0.742
    fan_out_degree_local TVD      0.572     0.162*
    role_edges TVD                0.503     0.453
    fan_out_degree TVD            0.265     0.494
    role_census TVD               0.033     0.033

`*` artifact-inflated. The clean value is 0.290 — see iteration 17.

Two things landed where they were aimed, and both are the population measurement reproducing
itself: `generic-endpoint->dns-resolver` LOCAL fell 117 -> 52 against a source 38, and the group's
bearer count went 58 -> 30 against a source's 30 — exactly.

Everything iteration 15's retraction left load-bearing moved the wrong way. `role_edges_local`
0.618 -> 0.742 and `external_dst_edge_share` 0.8054 -> 0.6653, both away from the source, against a
`fan_out_degree_local` much better than projected. (`role_edges`, the axis that retraction demoted,
improved; `role_census` was flat. Neither decides anything.)

Read at the time as two bundled changes pulling in opposite directions — which is unattributable by
construction, so `--no-chatter` was added to make the allocator runnable alone (default ON, so a
cast is byte-identical to before the flag existed). **That reading was wrong.** The axes that got
worse were not the personas at all — see iteration 17.

Also recorded at the time: a new wrong key appeared, `dns-resolver->generic-endpoint`, source 3 twin
23.

### Iteration 17 — the artifact: emit ports and responder ports must move together

**This is the section that matters most.** The regression was not the personas.

The allocator changed every host's budget arithmetic. That changed the `sink_fanout_buckets`
decomposition, and that changed **which ports** carry the sink emits: `53/udp` moved onto sink
sub-roles and `123/udp` onto `dns-resolver`, neither of which the standing twin's responders served.

The cast emitted wider responder scripts to match, and they were deliberately withheld from the
twin — on the reasoning that a standing twin keeps the responders it was deployed with, so repo
content should mirror deployed reality rather than run ahead of it. Correct about reproducibility,
wrong about consequence: the emits moved and the listeners did not.

**228 emit rows landed on dead ports, and each cost twice.** The forward flow stopped completing, so
the conversation it should have produced is simply missing; and the guest's kernel returned an ICMP
port-unreachable, which Zeek recorded with the **replier** as originator and a twin host as
destination — that is, scored as a **local edge**, the exact axis the iteration was being judged on.

    ICMP local rows                     220
    web-tier->generic-endpoint          197   (100% icmp, 100% originated on the sink segment)
    dns-resolver->generic-endpoint       23

Both were predicted from row counting before the run — 197 and 23 — and both measured at 197 and 23.
That is the diagnosis, not a corroboration of it: a fabricated edge whose count is derivable from
the emit table is not a persona behaviour. The 23 is iteration 16's "new wrong key", which was never
a new wrong key.

**Repair, then a genuine single variable.** The correct responders were pushed to 19 guests
out-of-band over QGA, and the listeners were verified IN-GUEST with `ss -lnH -tu` rather than
trusted from `systemctl is-active` — the trap this journal already records, where a responder thread
died on EADDRINUSE while its unit reported active and its op PASSED. Then re-measured with the
personas untouched.

    axis                        iter 16   iter 17   source
    external_dst_edge_share      0.6653    0.8857   0.8476
    role_edges_local TVD          0.742     0.589
    fan_out_degree_local TVD      0.162*    0.290
    role_edges TVD                0.453     0.887
    fan_out_degree TVD            0.494     0.386
    role_census TVD               0.033     0.033

`external_dst_edge_share` recovered 0.6653 -> 0.8857 and `role_edges_local` reported **0 twin-only
keys**. Both phantoms are gone: the manufactured local ICMP, and the external share it was taking.

**Two corrections belong here.**

First: iteration 16's spectacular `fan_out_degree_local` of 0.162 was partly ICMP noise inflating
local degree. The clean value is 0.290, and the table above carries it.

Second, and this is why it matters beyond one number — the projection for iteration 16 had said
0.290, and iteration 17 measured exactly 0.290. That projection was dismissed at the time as
overfitting, on the grounds that a model reproducing the regime it was fitted to proves nothing.
**The model was right about the clean system; the apparatus was broken.** The general lesson
survives — state which emit classes a calibration set contains, and refuse to project one it does
not — but it must not be used to dismiss a projection that a corrupted measurement merely appeared
to refute. A projection disagreeing with a measurement is a question about which of the two is
wrong, and here the default assumption went the wrong way.

**The durable rule: emit ports and responder ports derive from the same census and must move
together.** Updating one while withholding the other silently converts working traffic into ICMP
that scores as local edges, and it presents as a persona problem rather than a plumbing one.

**Record honestly.** Those 19 guests now run responders whose text differs from their persisted
`script_content`. The deploy state no longer fully describes them, and a `te deploy --update` would
read those ops as unchanged when they are not.

### Iteration 18 — protocol fidelity drives role attribution

`_PORT_PROTO` names a protocol the way axRange's persona schema does; `ir_edge.proto` names it the
way the sensor does; and the emit ranking joins the two by string equality. Of the 24 protocol names
`_PORT_PROTO` can produce, **19 are tokens `ir_edge.proto` never carries** — `https`, `kerberos`,
`ldap`, `smb` and more — so those ports scored a permanent zero and always sorted last.

Port 443 could not spend the 243 rows of `ssl` weight it had earned. Port 53 mapped to a real IR
token, scored, and won. The twin dialled the sink on port 53 **773 times**, against a source whose
DNS is 17 of 711 outbound rows.

**This had been deferred once** — GAP-C20 recorded, in as many words, that the axes count edges and
not protocols, so a protocol mix could not move them. That was wrong in a specific way worth
recording: it does not change how many edges exist, but it changes which ports carry them, which
decides the sink's **inferred role**, which relabels hundreds of edges. In iterations 15-16 the sink
served 80/443 and read as `web-tier`; after the iteration-17 repair it served 53 and read as
`dns-resolver`, collapsing `generic-endpoint->web-tier` from 315 to 3 and inflating
`->dns-resolver` to 773.

Built: `_IR_PROTO_ALIASES`, giving all 24 emittable protos an explicit decision — 15 mapped, 9
deliberately unweighted, with a test that fails if a new port arrives without one. The nine are the
group ports (whose conversations are structurally absent from a role-to-role weight table),
protocols with no Zeek analyzer to join a label against, and one left unmapped rather than guessed,
since a wrong token is a silent zero and a silent zero is the exact failure being fixed. `tcp`
previously *matched*, and that match was the bug: it scored the IR's "could not identify the
service" label.

Outbound emit rows at 443 went 0 -> 168.

    axis                        iter 17   iter 18   source
    external_dst_edge_share      0.8857    0.8641   0.8476
    role_edges_local TVD          0.589     0.533
    fan_out_degree_local TVD      0.290     0.280
    role_edges TVD                0.887     0.878
    fan_out_degree TVD            0.386     0.419
    role_census TVD               0.033     0.033

`role_edges_local` at **0.533** is the best of the loop. Its key-set Jaccard went 0.333 -> **0.444**
— eight shared keys instead of six — and two keys the twin had never produced at all appeared:
`generic-endpoint->generic-endpoint` (source 9, twin 14) and `monitoring-collector->log-shipper`
(source 1, twin 1). `external_dst_edge_share` landed within **0.0165** of the source, the closest
yet.

**Two claims were stated before the run and both held.**

The sink would still infer `dns-resolver`, because `ROLE_CATALOG` is first-match-wins and
`dns-resolver` sits at index 5 ahead of `web-tier` at index 6 — so the presence of `53/udp` in the
served set decides the role regardless of volume. Measured: `role_edges` moved only 0.887 -> 0.878.

The DC-bound rows the alias table newly added would not produce
`corp-workstation->domain-controller`, because the twin's Windows DC answers nothing and the
catch-all roles it `generic-endpoint`. Measured: `corp-workstation->generic-endpoint` 6 -> 12, and
`generic-endpoint->generic-endpoint` new at 14. Both are keys the source has, so the misattribution
incidentally improved coverage — a result worth distrusting on principle, since it is right for the
wrong reason.

**And it corrected an earlier error in the working notes.** A `corp-workstation->generic-endpoint`
residual of 6 had been carried as "Windows background chatter". It was never chatter: it was the
Kerberos emit striking a misroled DC. A mislabelled real effect, not a constant — the worse of the
two mistakes, because a constant gets subtracted and forgotten while a real effect keeps moving.

**Full trajectory of the arc**, on the axis iteration 15's retraction established as the one that
decides convergence, and on the axis the sink was built for:

    axis                       i14     i15     i16     i17     i18    source
    role_edges_local TVD       0.595   0.618   0.742   0.589   0.533    --
    fan_out_degree_local TVD   0.534   0.572   0.162*  0.290   0.280    --
    external_dst_edge_share    0.1096  0.8054  0.6653  0.8857  0.8641  0.8476
    role_edges TVD             0.468   0.503   0.453   0.887   0.878    --
    fan_out_degree TVD         0.552   0.265   0.494   0.386   0.419    --
    role_census TVD            0.016   0.033   0.033   0.033   0.033    --

`*` artifact-inflated; the clean value is 0.290.

### The next lever, and the standing constraints

Iteration 19 is diagnosed, not searched for. `proto_weights` weights by `ir_edge.flows` while the
fingerprint counts **rows**. DNS is many small flows and TLS is fewer fat ones, and the orderings
are opposite on every band — on the heavy band 443 wins on rows 158 to 26 while losing on flows 540
to 870. The function's own docstring justifies flows *because* "`role_edges` counts edge rows",
conflating a flow with a row: the error is stated in the one place that should have caught it.
Fixing the column, with the catalog-ordering question beside it, is what would let the sink
re-infer as `web-tier`.

Still structural, and unchanged by all three iterations: `responder.py` is POSIX-only, so the
Windows DC answers nothing and every edge naming it is invisible to a role histogram — roughly a
third of the source's local edge mass. A standing self-check now reports emit rows landing on ports
the cast's own responders do not serve (16 at iteration 18, all at Windows roles), which
distinguishes that known gap from new drift. Note what it does not cover: it compares a cast against
itself, and iteration 16's 228 dead-port emits were a divergence between the cast and the DEPLOYED
twin, whose own responders the repo cannot see. Only the guest can answer that one, which is why
`ss -lnH -tu` is now part of the loop rather than an incident response to it.

No GAP-C number is minted for any of the three. Iteration 15 deliberately left the allocator floor
unnumbered and this arc is its consequence; the responder divergence is a discipline in the harness
rather than a defect in either repo; and the alias table is a fix, not a gap.

### Iteration 19 — the ranking column and the catalog both moved: the sink reads `web-tier`, and it costs a trade

`docs/WORK_ORDER_proto_weight_rows.md`, ruled 2026-09-02 (Authority: user): **D1 = (a) + (b), D2 =
no seam.** Option (c) — a `dns-resolver` row requiring `53/udp` **and** `53/tcp` — was **built,
measured against the real source, and NOT taken.** The census that decided it: 13 of the source's
15 `dns-resolver` hosts carry `{53/udp}` as their **entire** listen evidence — only the two
AD-integrated DNS hosts on `10.80.20.0/24` show `53/tcp` alongside it — so any two-port `requires`
row would strip the reference's own resolvers from 15 down to 1, and the twin's four real resolvers
fare no better (no cast persona dials any of them over TCP at all). After the responder re-install
the sink's 8 responder instances bind `53/tcp` (a responder serves whatever its role's personas
dial), so **8 of the 15 sink sub-roles would still read `dns-resolver`** even under (c). The work
order's own §2 estimate — "16 of 18 port-53 listen votes" — does not reproduce against this
measurement: the closest reading is **15 of 16** distinct port-53 hosts, or **107 of 114**
entity-windows.

**What (b) actually moves, measured exactly rather than assumed.** A brute-force sweep of the
catalog's rule against every one of the `2**16` subsets of the source's observed listen sets
confirms (b) flips exactly the twin's 15 sink sub-role hosts and zero source hosts: every changed
outcome is a listen set containing both `53/udp` and `443/tcp` with no two-port match — precisely
the tie D1b targets and nothing wider.

**D2 taken as recommended: no seam.** `roles.yaml` now declares the `web-tier` row requiring
`443/tcp` ahead of the `dns-resolver` row requiring `53/udp`, applied to `ROLE_CATALOG` itself, not
to a sink-only exception — the sink is re-inferred through the ordinary catalog like any other host,
same as the source is. `ROLE_CATALOG`'s post-sort order at the affected indices: **5 `web-tier`
(443)**, **6 `dns-resolver`**, 7 `web-tier` (80) — reversed from the order the two halves of this
work order (§1) were diagnosed against. One wrinkle is recorded in the row's own comment rather than
left implicit: a 0.6-confidence row now outranks a 0.7-confidence one on this one specificity tie —
confidence is what a row asserts once matched, and has never been the tiebreak.

**Roles are baked at INGEST, not at fingerprint time — which is why re-scoring iteration 18 needed a
re-ingest, not a re-run.** `learners/extractors.py:68-70` writes a role into
`store/belief_store.py:560-575`, `ir/materialize.py:704,756-760` carries it into the IR, and
`scripts/shape_fingerprint.py:81,84` reads the stored role back out — a catalog change is
attributed by **re-ingesting stored Zeek logs** under the new catalog, never by re-running the
fingerprint over an already-materialized IR. Re-ingesting iteration 18's own twin capture this way
projects `role_edges` 0.878 -> **0.411**, with `role_edges_local` (0.533), `fan_out_degree_local`
(0.280), `external_dst_edge_share` (0.8641) and `role_census` (0.033) landing **byte-identical** to
iteration 18 — the reorder alone touches only the axis sensitive to the sink's own role label, and
this re-score is what iteration 19 was measured against before it ran.

**The source itself differs from the archived baseline by one host, so this iteration compares
against a freshly re-materialized source, not the stored `capcn-ir6`.** The current code's IR
compiler places one external IPv6 host (`corp-workstation -> file-server`) differently than the
archived `capcn-ir6.duckdb` does — a code-build difference between when that file was written and
today, not a change in the network. Durable copies of both, and of the re-scored iteration 18,
sit at `/home/rocky/corpapcn-fidelity-artifacts/`: `src-ir-rematerialized-20260902.duckdb`,
`iter19/twin-ir-iter19.duckdb`, `iter19/twin-ir-iter18-rescored.duckdb`, `iter19/measure.log`,
alongside every prior iteration's own `twin-ir-iter14..18.duckdb`.

**Measured on the live loop.** Re-cast from the same PIT (`pit-63994c9543-1787823960`,
`--relocate 10.200.0.0/16`, no occupancy/count cap, chatter on), installed against the same standing
138-VM twin (`corpapcn-twin-0e4f7d58`) — `twin_config.yaml` came back **byte-identical**, so this
stayed `tfc`-tier: no redeploy, the same discipline every iteration since 16 has held to. 15 persona
files and 5 responder scripts changed (the sink's served set moved with the emit table, honouring
iteration 17's rule that the two must move together); 19 of 20 `run-script` guests' scripts differed
from their persisted `script_content` and were pushed out-of-band over QGA to the op's own guest
path, then verified **in-guest** with `ss -lnH -tu`: 20/20 hosts serve every port their new emit
table expects. 20-minute warm-up, 10-minute capture through XPress-SW-A, same window every iteration
in this arc has used.

    axis                        iter 18   iter 18*   iter 19    source
    role_edges                    0.878      0.411      0.406       --
    role_edges_local               0.533      0.533      0.593       --
    fan_out_degree                 0.419        --       0.496       --
    fan_out_degree_local           0.280      0.280      0.290       --
    external_dst_edge_share       0.8641     0.8641     0.8153     0.8476
    role_census                    0.033      0.033      0.057       --

`*` iteration 18's own capture, re-ingested under the reordered catalog — the projection iteration 19
was measured against before it ran, not a separate live run.

**The sink re-infers `web-tier`, the target this work order set.** 14 of 15 sink hosts read
`web-tier`; 1 reads `dns-resolver`. `generic-endpoint->web-tier` moves twin 3 -> 426 (source 298);
its local instance, `generic-endpoint->dns-resolver`, moves twin 77 (source 38) — both now
over-represented rather than absent, the opposite failure of iteration 18. The twin's full role
census over all 165 entities: `generic-endpoint` 108, unroled (`None`) 29, `web-tier` 14,
`corp-workstation` 6, `network-device` 4, `dns-resolver` 2, `monitoring-collector` 1, `log-shipper`
1.

**Verdict, by the work order's own §6 rule: target hit, two of the three must-not-regress axes
regressed — a TRADE, not a win, and it is recorded as one.** `role_edges_local` moved 0.533 ->
0.593, back near iteration 17's 0.589; `external_dst_edge_share` moved 0.8641 -> 0.8153, *away*
from the source's 0.8476 rather than toward it (the gap from source widened 0.0165 -> 0.0323);
`fan_out_degree_local` held roughly flat, 0.280 -> 0.290.

**The projection agreed with the measurement, this time.** The pre-run re-score of iteration 18's
own capture projected `role_edges` at 0.411; the live iteration 19 run measured 0.406. Iteration
17's standing instruction — a projection disagreeing with a measurement is a question about which of
the two is wrong — did not have to fire this round.

**A resolver-census finding, recorded as a measured fact with a labelled hypothesis, never a
cause.** Of the twin's four real `dns-resolver` hosts, only `10.200.5.2` stayed `dns-resolver`
(confidence 0.7, 11 windows); `10.200.10.2`, `10.200.11.5` and `10.200.15.2` re-inferred as
`generic-endpoint` at confidence 0.3-0.35 (11 windows each) — matched via a **dial** row, not a
**listen** row. The only other `dns-resolver` entity in the twin is the sink host `10.200.254.2`.
**Hypothesis, not a measured cause:** a `dns-resolver` match needs an observed 53/udp *listen*, a
listen is recorded only on an accepted inbound flow, and the rows-ranking fix (step (a) above) moved
emits from 53 toward 443 across the whole cast — so three of the twin's four resolvers may simply
have received no accepted inbound DNS in this particular window and fallen through to their own dial
evidence instead. Consistent with `generic-endpoint->dns-resolver` (local) holding at 77 while
concentrating on the one resolver that kept its role, rather than spreading across four. Query
pointer for whoever checks this next: `ir_entity where role='dns-resolver'` in
`iter19/twin-ir-iter19.duckdb`.

**The arc's own deciding axis, across the whole loop:**

    axis                       i14     i15     i16     i17     i18     i19    source
    role_edges_local TVD      0.595   0.618   0.742   0.589   0.533   0.593     --

A band, not a trajectory — six iterations of deliberate, individually-measured changes and the axis
that decides convergence has not left a roughly 0.53-0.62 range since iteration 14.

No GAP-C number is minted. Consistent with the arc's own accounting: this is the loop's diagnosed,
predicted, and confirmed closing iteration, not an undiscovered gap.

## The loop closes here — fidelity becomes a spec, not a distance

Ruled 2026-09-03 (Authority: user), on the strength of this iteration and the five before it: **the
convergence loop's own objective — minimize the distance between two fingerprints — is retired.**
Its **measurements are not**: nineteen iterations of real, recorded numbers against a real reference
stand as written above, and none are retracted.

Six iterations (14 through 19) moved the deciding axis, `role_edges_local`, within a 0.533-0.742
band and never converged it — the table above. This iteration's own outcome, one axis fixed and two
others worse, is the same shape the whole back half of the arc has shown, not a new failure.
Structurally, **12 of this source's 18 local edge keys cannot exist in the twin at all**: the
Windows domain controller answers no connection at all (an unbuilt responder —
`docs/WORK_ORDER_windows_responder.md`), and the source carries no workstation-to-workstation
traffic for a twin to reproduce. Re-weighting the six keys that *can* exist only trades one axis
against another, which is exactly what iterations 16 through 19 did in turn. And the fingerprint
measures **traffic shape**, which is neither this platform's actual goal (attack/defense experiments
against a twin) nor a reliable proxy for what those experiments depend on: real protocols so
detections fire against real traffic, real identity so attack paths exist to walk, a matching
security posture, a matching configuration.

**What replaces it: fidelity to a closed spec — a "golden twin."** Ten dimensions — asset, topology,
configuration, state, protocol, performance, security, dependency, temporal, behavioral — plus a
visual row kept for operator trust rather than as an engineering claim. Each dimension is tagged
with the observation channel that feeds it and an evidence class (`observed` / `referenced` /
`imputed`, aximprint's existing vocabulary), weighted by the experiment it actually serves. The
**behavioral** row is this loop's fingerprint, kept, as a **tolerance**, not a convergence target —
a band that will not close is not a target.

**The operational half becomes a second evidence channel, not a synthesis problem.**
Configuration, state and security posture were never going to emerge from re-weighting
traffic-shape axes; they need to be **observed**, from a source the wire does not carry.
"Reference ingest" — directory exports first (closes the domain-controller gap
`docs/WORK_ORDER_windows_responder.md` names, for real this time), then host inventory, network
device configs, security-tool baselines, and operator-asserted facts where nothing else reaches —
feeds the existing `ir_identity`/`ir_unseen` tables under the existing `referenced` evidence class,
and a compiler step emits the `identity:` block axRange's twin config already accepts today. Posture
is injected into the twin; secrets never cross that channel.

**Consequences for the two work orders this loop leaves open**, recorded in their own STATUS
headers: `docs/WORK_ORDER_windows_responder.md` is **SUPERSEDED in approach** — the DC still answers
nothing, and that is still the largest structural gap in these numbers, but the fix is a real,
promoted domain controller populated from a directory export via identity injection, not a
POSIX-responder-shaped `.NET` listener. `docs/WORK_ORDER_source_locality_filters.md` is **PARKED** —
its proposed A/B is behavior-row tolerance tuning, worth running only if that row fails its own bar,
and its pre-delegated ruling is withdrawn along with the convergence objective it was written to
serve. `docs/WORK_ORDER_proto_weight_rows.md` is **DONE**, recorded as this loop's own closing
iteration.

**Where the numbers live.** This iteration's durable artifacts sit at
`/home/rocky/corpapcn-fidelity-artifacts/` — the re-materialized source, the re-scored iteration 18,
the fresh iteration 19 IR, and the raw measurement log, alongside every prior iteration's own IR.
The standing twin (`corpapcn-twin-0e4f7d58`) and its tap (`tap-corpapcn-twin-0e4f7d58-e4b517fe`) stay
up as the test bed for whatever measures the golden-twin spec's behavioral row next. The next work —
a cross-repo fidelity spec, a reference-ingest work order, a fidelity scorecard, a firewall-policy IR
extension — is named, not yet written; see `docs/CHANGELOG.md` 2026-09-03 and
`docs/SESSION_STATE.md` for the branch plan (`wip/golden-twin`, opened once both repos' `master` are
fast-forwarded).
