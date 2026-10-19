# Changelog — aximprint

Every landed change. Reverse chronological. Each entry carries an `**Authority:**` marker
(`design (chat session)` | `implementation (Claude Code)` | `user` | `retrofit`) and a
`**Supersedes:**` line when it re-homes or reverses ratified behavior — see `docs/WORKFLOW.md`
"Authority markers in CHANGELOG" for the full entry shape.

---

## 2026-09-08 — axquery ingest gains UDP listeners and address-bearing identity; the cast stops emitting a second forest root

**Authority:** **user** — the user directed mining axquery's richer data to run the twin-discovery
loop against the CorpAPCN capture (using axquery instead of Zeek), which is the whole reason to
revisit the census/`events` declines below.
**Supersedes:** the `/api/events` decline in `ingest/axquery/plugin.py::_DECLINED` and
`docs/WORK_ORDER_axquery_ingest.md`'s build-step-5 deferral, **for the APP-LAYER identity half
only** — the flow half stays `/api/connections`, so the original decline's double-count reason
still holds and is preserved in the comment. The three census endpoints
(`/api/entities`/`/api/devices`/`/api/identity/users`) stay declined; `/api/identity/users` gains
a fuller reason (below). Nothing else moves.

Three changes, in the priority the loop needed them.

### 1. UDP listeners — `_state_class` derives UDP outcome from directionality

axquery's `_state_class` collapsed every non-TCP flow to `unknown`, so the listen-census gate
(`windowing/windows.py`, `state_class in FLOW_ACCEPTED and proto in (tcp,udp)`) never fired for
UDP and **no UDP service ever reached the twin.** UDP carries no flags, so the outcome is now
derived from DIRECTION: a responder that returned any traffic (`respBytes>0` or `respPackets>0`,
threaded through `_flow` as `responder_replied`) is `established` — the same `FLOW_ACCEPTED` class
an accepted TCP flow gets — and an only-originator UDP flow is `attempted` (the TCP-`S0`
analogue). **ICMP and every other non-TCP/non-UDP transport stay `unknown`**, and MUST: for ICMP
`dst_port` is the message TYPE, not a service port, so a promoted icmp would mint a bogus
`listens_on 8/icmp` (the gate's own comment). This rides the existing
`listens_on → snapshot["listens"] → ir_entity.listen_ports → services[]` chain untouched, so
DNS/53, NTP/123, DHCP/67, SNMP/161, LDAP/389-udp, Kerberos/88-udp and NetBIOS/137 now appear as
`services[]`, and `catalog/roles.yaml` infers **`dns-resolver`** (30 nodes on the CorpAPCN 2 h
window; 0 before) from `53/udp`.

### 2. Identity — `/api/events?type=dhcp|dns` → `IdentityEvent`

The plugin emitted no identity at all (`bindings=0`); the twin had no identity overlay. It now
reads the two `/api/events` types MEASURED as populated on this build:

* **`dhcp`** → `IdentityEvent(dhcp)` binding the client host name + mac + assigned address
  (`dhcp.your_ip` on the ack, else `dhcp.requested_ip`). **This is the one identity anchor a
  router-mirror otherwise lacks** — every conn-level mac on this rdr vantage is null, but DHCP
  carries the real `dhcp.client_mac` beside the host name in one row, which is the cross-namespace
  link `windowing/entities.py::_identifiers_dhcp` fuses into a single host.
* **`dns`** → `IdentityEvent(dns)` binding each A/AAAA answer address to the query name
  (`referenced`; name answers dropped, never coerced). Populates the golden bundle's
  `observations.dns.names`.

`kerberos`/`ntlm` are DECLINED on measurement (0 rows in this build's windows — the AD identity
here surfaces via the census, not connection events) and wired back the moment a capture populates
them. The `/api/identity/users` census stays declined and its reason now says WHY beyond
truncation: it carries **no address**, so it anchors nothing in the resolver, and there is **no IR
carrier for an address-less directory dump** — the realm/account overlay is
`docs/WORK_ORDER_reference_ingest.md` **S5** (`ir_policy(scope='domain')`), whose own known
transform is realm-case normalization (AXELLIO/Axellio are one realm).

**Event ORDERING (found and fixed in the same pass):** `events()` concatenated all flows THEN all
identity, a ~2 h backward ts jump that made the windower dump every `IdentityEvent` into the final
window. It now does the zeek plugin's k-way `heapq.merge` by ts across the flow stream and one
stream per identity type — measured: `IdentityEvent` counts now distribute across all 9 windows
(3989/5592/…/2325) instead of 45273-in-the-last.

Measured on the CorpAPCN 2 h window (`--from 1780398299 --to 1780405499 --window 900`):
`ir_identity` went 0 → **5302** (5276 `dns` `referenced` + 26 `dhcp` `observed`), and the golden
`observations` block carries **2638 dns names + 13 identity principals** (real MACs + host names
`ENG-AD-CO-LTW11`, `bw-mbp15`).

### 3. Cast — one forest root per realm; `_node_suffix` stops collapsing round octets

Two cast-side fixes, both surfaced by the richer twin:

* **`compiler.py::_node_suffix`** dropped trailing zero octets with `rstrip(".0")`, which ate a
  CHARACTER run rather than a whole octet: `10.20.200.0` → `10-20-2`, indistinguishable from
  `10.20.2.0`. With axRange's separator-less `count:` fan-out (`instance_ids`: `f"{name}{i:02d}"`)
  a `count: 4` node on `.200` then minted `…-201..-204`, colliding with the singleton `.201`/`.204`
  segments — the "duplicate node id after count fan-out" the axRange validator refuses. It now
  strips WHOLE trailing zero octets, so `.200` → `10-20-200`, distinct from every sibling, while
  the common non-round case (`10.80.72.0` → `10-80-72`) is unchanged. (The golden node name also
  ends in a non-numeric rung word — `-core`/`-transient` — which independently separates the
  suffix from the ordinal; this fix removes the collapse at its root, so the suffix is correct and
  collision-safe even where that guard is absent.)
* **`compiler.py` domain promotion** gave every `role: adds` domain-controller GROUP its count of
  observed members. A `count: 2` DC group made axRange emit two `domain-ready[<fqdn>]` barriers
  (one per fanned instance, `pass1_emit.py`) with the same op_id — a plan-build refusal. A forest
  has ONE root and axRange has no replica-DC op, so a domain-promoting group is now clamped to
  **count 1**; the observed count is kept in the `population` sidecar
  (`observed`/`capped_by: single_forest_root`) and a caveat, never silently dropped.

**End to end** (worktree golden bundle `axr-topology/1.5`, `--relocate 10.200.0.0/16`): the rich
twin is **40 segments / 109 node specs**, validates PASS (1 pre-existing `OVR-020` warning), and
plans **rc 0** at both `DEPLOY_MIN_OCCUPANCY` 0.8 (149 `clone-vm`) and 0.5 (162) — no node-id
collision, no duplicate op. (aximprint side only; the axRange deploy/score side is
sensor-agnostic and unchanged.)

**Tests:** `2218 → 2233 passed, 1 skipped` (+15: UDP state x4, dhcp/dns emitters x5, the ts-merge,
the `/api/events`-read-not-declined gate, `_node_suffix` collapse + collision-safety x2, and the
forest-root clamp). All axquery tests stub `urllib.request.urlopen` — no real egress.

---

## 2026-09-05 — the apply contract: a bundle records its own cast, the install is a verb, and the assertions vocabulary is an artifact

**Authority:** **implementation (Claude Code)** — all three rulings, the shapes named below,
and the two deviations recorded at the end. What MOTIVATED them is axRange's **Console apply
contract**: its S1 API is being fixed to consume these three in parallel, and the apply path is
where the reproduce-your-own-cast defect was found. The two repos meet on files only, as they
always have.
**Supersedes:** `bundle_schema: axr-topology/1.4` as the current topology-export version; the
whole body of `scripts/install_cast_as_twin.py`, which becomes a thin wrapper around
`aximprint.synth.install`. Nothing else — no accepted set of `operator_assertions.py` moved, and
that is asserted mechanically, not by eye (see below).

**All three are additive**, and each closes a gap a consumer hit rather than one this repo
predicted.

### 1. `cast_invocation` — a bundle can now reproduce its own cast (`axr-topology/1.5`)

**The defect, found on axRange's Console apply path: a recompile driven from a bundle silently
reproduced the SOURCE network's CIDRs.** `--relocate` survived only as per-field
`transformation:` records inside the *sidecar* — a different file, recording what CHANGED rather
than what was ASKED FOR — and `--max-count` / `--no-chatter` / `--assertions` survived nowhere at
all. A consumer holding the three-file bundle could describe the twin it had and not re-derive it.

The topology export gains a top-level block:

```json
"cast_invocation": {
  "assertions": null, "chatter": true, "code_fingerprint": "0e2878a932b8b0f8",
  "imprint_id": "corpapcn-golden", "max_count": null,
  "pit_id": "pit-03de65ec67-1787823960", "relocate": "10.200.0.0/16"
}
```

**Every value is the EFFECTIVE one, not the switch** (`chatter` is `not --no-chatter`;
`imprint_id` is the resolved id, never `None` because the flag was omitted): the block's job is
reproducing the RESULT, and a reader reconstructing argv from a record of which switches were
typed would have to re-derive every default this CLI holds.

**Two deliberate omissions, and they are not symmetric.** The **store path is not recorded** —
`--store` is a lab-local filesystem path, neither portable to the consumer nor a property of the
cast, and publishing it would put one host's directory layout into an artifact another repo
serves over HTTP; the consumer supplies the store from its own configuration and this block says
which PIT inside it. **`assertions` travels as a BASENAME** — same reason, one notch weaker: the
file is a real input whose identity matters, so the name travels and the directory does not.

`code_fingerprint` is the build that **cast**, not the PIT's own (already in the sidecar, and
naming the build that MATERIALIZED it — a different question). It therefore moves with any edit
to `src/aximprint/**.py`. That is the field working, not a determinism break: two casts from ONE
tree are byte-identical, which is the invariant `scripts/verify_determinism.py` and
`CONVENTIONS.md` §3 actually assert. It bit during this build in the most useful possible way —
see the deviations at the end.

The key is **ABSENT, not `null`**, when a library caller builds a bundle without one — the
opposite posture from `observations` two keys over, deliberately: an empty `observations` is a
real answer ("this PIT holds no DNS names"), while `{}` here could not tell "no flags" from "not
recorded", and "no flags" is itself a reproducible invocation.

`1.5` is additive: every `1.4` key is present and unchanged, so a reader written against `1.4`
stays correct and ignores one key it does not know.

### 2. `aximprint install-cast` — the install is a verb, and there is one body

`install-cast <CAST_DIR> <CATALOG_ROOT> <STABLE_ID> [--sensors NAME ...]`, with exactly the
semantics `scripts/install_cast_as_twin.py` already had. The logic moved into
`src/aximprint/synth/install.py` and **the script's body was deleted, not copied** (`LES-101`):
the script is now ~45 lines that parse argv and call `install_cast()`. It survives because the
fidelity loop's own notes and runbooks invoke it by path.

It lives in `synth/` rather than at the top level because it operates on a cast's output
directory and reuses `synth.personas.node_instances` — the same expansion rule the compiler used
to WRITE those files — so producer and installer share one rule by import. A top-level module
would also have entered `tests/test_acquire_layering.py`'s closed top-level census and required a
classification for something that is post-synthesis packaging.

**Exit codes are three-valued now, and the split is load-bearing** (`LES-134`): **0** installed,
**2** a usage or SHAPE problem (no cast directory, no `twin_config.yaml`, YAML that does not
parse, no `twin_config` mapping, an empty `id`) and **1** a real FINDING in a well-formed cast (an
assignment naming a node that is not an instance, a persona with no file and no fallback, a
referenced `install:` script that is not on disk). `2` means nothing was attempted; `1` means the
cast is internally inconsistent and would have died at `te plan` with the previous twin already
destroyed. Before this the shape failures were uncaught tracebacks, which anything checking only
"nonzero" reads as a finding.

`--sensors` takes `nargs="+"` on the subcommand and one comma-separated string on the script;
`split_sensors` accepts both, so the two spellings are two front doors onto one rule.

### 3. `axr-assertions-1.schema.json` — the vocabulary is an artifact, not prose

`src/aximprint/schemas/axr-assertions-1.schema.json` (JSON Schema draft 2020-12) states what
`operator_assertions.parse_assertions` accepts, and `aximprint assertions schema` prints it
verbatim. **axRange's Console WRITES this document and cannot import the parser** (`CLAUDE.md`:
neither repo imports the other), so until now the only statement of what a valid record is lived
in this module's control flow. A vendorable file is the one shape a contract can take across that
boundary: axRange keeps a byte-identical copy and compares it by test, which is why the file is
canonical (`json.dumps(sort_keys=True, indent=2)` + a trailing newline, ASCII-safe) and why it is
edited HERE and copied there, never the reverse.

**It is a statement ABOUT the parser, not a second parser.** Nothing in `src/` reads it, and
`parse_assertions` remains the only thing that decides. The two can therefore only agree by being
checked against each other, which `tests/test_assertions_schema.py` does three ways: every closed
set compared to the imported constant (never a retyped copy — `LES-105`); a **corpus** of 24
documents the parser accepts, each of which must validate, and 59 it refuses, each of which must
fail; and a canonical-formatting assertion so a hand edit that reformats is caught here rather
than by a byte comparison one repo over.

**`jsonschema` is not a dependency and deliberately did not become one** (adding a runtime
dependency is the user's call, `CONVENTIONS.md` §1.2). The corpus runs through a small
draft-2020-12 subset evaluator in the test file, **gated by a positive check that every keyword
the schema actually uses is one it implements** — a checker that silently ignores what it does not
understand passes everything, which is `LES-134`'s "reports itself as passed when it did not run"
one layer in — and mutation-proven by watching a weakened schema start accepting a document the
strict one refuses. When `jsonschema` IS importable the corpus additionally runs through it and
the two must agree; that test skips here and will not skip in axRange's venv.

**Six refusals no per-instance keyword can reach are NAMED in the file's own `$comment`**, so a
consumer does not read schema-valid as parser-accepted: a non-strict CIDR (`10.80.31.5/24` — host
bits are arithmetic), a self-conduit (comparing two halves of one string), an unparseable `as_of`
(`format` is annotation-only in draft 2020-12), and the three cross-record refusals (a duplicate
merge key, a key in both `add` and `suppress`, a `(key, field)` claimed by both an `add`'s own
field and an `override`). The test carries the same list, closed: a refusal fixture outside it
that the schema lets through fails the suite, **and so does a carve-out the schema turns out to
catch** — a declared exception whose reason is untrue is worse than no exception (`LES-139`).

`$defs.evidenceClass` publishes aximprint's five-class precedence order (`asserted` >
`imported` > `observed` > `referenced` > `imputed`). **Five is the intended shape, not a
divergence from axRange's six-class model**: that model's sixth class, `compiler`, is by
definition a sidecar-only label for a value the compiler invented — it never appears in the IR
and is never assertable, so it cannot be one of the classes this document ranks. The two are
consistent, and the file says so in the `$defs` entry itself rather than leaving a reader to
count and wonder (`LES-143`: a consumer joining on this enum wants exactly these five).

### The re-cast, and what it proves

The golden candidate was re-cast and re-installed with exactly the prior flags
(`--pit pit-03de65ec67-1787823960 --imprint-id corpapcn-golden --relocate 10.200.0.0/16`, no
assertions file) against a read-only copy of the source IR store, whose md5 was verified unchanged
afterwards. **40 of the 41 installed files are byte-identical to what was installed before**; the
one that moved is `corpapcn-golden.topology.json`, and a parsed diff shows exactly two
differences — `bundle_schema` `1.4` → `1.5`, and the new `cast_invocation` block. Two casts of one
tree were byte-identical. axRange still reports `Result: PASS (1 warning)` — `OVR-020`, which the
shipped `corp-enterprise` config produces identically — and still plans
`corpapcn-golden-ea3a42d7`, **ops=695 barriers=105 edges=1700**.

**2219 pytest tests** (2218 pass, 1 skip — the `jsonschema` comparison, which skips only where
that package is absent). All four determinism gates hold.

### Two deviations, both recorded rather than smoothed over

**(1) `code_fingerprint` invalidated an already-installed bundle mid-build, and that is the
design.** The bundle was cast and installed once, then a lint normalization in
`synth/imprint_bundle.py` (`Optional[X]` → `X | None`, taking the file from 8 ruff findings to 0)
moved the fingerprint, so the installed artifact was stale by one field and the whole cast +
install + proof had to be redone against the final tree. Worth stating because it is the standing
operational consequence: **install the bundle LAST, after the source tree is final**, or the
fingerprint names a build that no longer exists.

**(2) `tests/test_cli_acquire_dispatch.py`'s brace-list regex was `[a-z,]+` and `install-cast` is
the first hyphenated verb**, so the pattern stopped matching argparse's rendered choice list *at
all* and the test failed with "no rendered choice list" — the same wrong-reason failure its own
comment describes surviving when `materialize` landed, arriving through the CHARACTER SET instead
of through the list's length. Widened to `[a-z,\-]+` with the reason recorded beside it: a
membership assertion has to be able to spell every verb the CLI can have.

---

## 2026-09-05 — protocol-faithful services: the twin serves what it was seen serving, and the DC is real

**Authority:** **user** for the rulings this implements (axRange
`docs/WORK_ORDER_protocol_faithful_services.md` §10.1, ratified 2026-09-04 — **D1(a)** a real DC
now, **D2(b)** the declared listen set, **D4** the lab realm `corp.axr.lab`, **D8(a)** the echo
survives only as a labelled fallback; the ruling underneath them all is
`docs/design/13-golden-twin-fidelity.md` §8.2, verbatim: *"implement realistic protocol clients and
servers based on the observed data. avoid using synthetic data generation where possible."*).
Implementation, the two seam corrections and the deviations named below: implementation (Claude
Code).
**Supersedes:** the echo responder as a twin's ANSWER to every conversation — `synth/responder.py`
survives, unchanged, as the labelled fallback for ports no real server covers; the 2026-09-04
decision to keep `bundle_schema` at `axr-topology/1.3` ("the slice that lands `services[]` bumps
it" — this is that slice); `roles.yaml`'s `domain-controller` `install: []` revert and the two
`("domain-controller", ...)` entries in `scripts/check_role_fixed_point.py::DECLARED`. Nothing
else.

**This is the aximprint half.** The consumer half (`App.listen`'s first consumer, the `listen:`
passthrough, the inventory publication) is axRange's and landed in parallel; the two repos meet on
files only, as they always have.

### What was measured, and what it cost

Every `dns-resolver`/`web-tier`/`internet-sink` node in the CorpAPCN cast installed one
`/usr/local/lib/axr/responder.py` that binds a port list and reflects bytes. It fixed the defect it
was built for (a bare VM draws ICMP port-unreachable at every client emit, which Zeek scores as a
*fabricated reverse local edge*) and it is exactly the synthetic filler §8.2's ruling removes: `dig`
gets its own query back and `curl -sk https://` never completes a handshake, so a protocol-aware
probe cannot pass by construction. Its port list was also **the wrong input**:
`responder.listener_ports(personas)` derives each role's bound set from every `(port, transport)`
some persona *dials* at that role, which is why `internet-sink-1` bound 88/tcp while the two
`web-tier` nodes bound nothing at all.

### `src/aximprint/synth/services.py` — the new module

**Recipes, driven by the OBSERVED listen census.** bind9 (authoritative for the lab realm +
forwarder), nginx (80 / 443 / 8006, self-signed lab cert), chrony (`allow`, `local stratum 10`),
isc-dhcp-server (scope from the node group's own segment CIDR), and an **assert** for sshd. Each is
a Debian-main package; each writes real configuration, runs a config check (`named-checkconf`,
`nginx -t`, `dhcpd -t`), and asserts `systemctl is-active` — **never `nohup … &`**, which the
guest-agent session that launches it reaps (axRange LES-079/GAP-020).

**ONE SCRIPT PER NODE GROUP, not per role**, and that is a correctness fix rather than a layout
preference: this source's four `dns-resolver` hosts answer on 1, 2 and 9 ports respectively, so a
role-level script would make three of them serve six ports nothing ever saw them serve —
manufacturing the very listeners this work removes.

**The D8 guard fires at RENDER time.** `plan_node_services` partitions a declared set into
(recipe-covered, echo) and refuses, naming the port, if anything would ever bind twice. The runtime
symptom it replaces is invisible: a responder that loses one bind keeps running, its unit still
reports `active`, and the fingerprint blames the personas — measured on dev45, where the echo raced
`named` for :53.

**Packages in `pkg:`, configuration in the script** (work order §7.2), because `--update
--reapply` can re-derive a zone file on a standing twin while adding a package to an existing node
is refused. The script keeps a guarded install anyway, for the node whose `pkg:` actions never ran.

### The DC, and what is imputed about it

`roles.yaml`'s `domain-controller` row carries `install: [{role: adds}]` again. What unblocked it
is **not** new evidence — the 2026-09-04 finding that the realm IS on the wire stands and is
recorded at the row — but **D4's ruling**: the twin's forest is `corp.axr.lab`, a lab constant,
`compiler`-class, so no observed name is reproduced and the original objection (*"emitting a domain
name nothing had extracted would fabricate the single most identifying string in the twin"*) is
answered rather than overridden. The compiler emits `provides: [{domain: corp.axr.lab}]` beside the
install action, `joins:` on every Windows workstation, and an `identity:` block of four lab-default
users marked **`imputed` in full** — this capture carries two Kerberos principals, one of them
`Guest`, and no credentials at all. The four are the canonical exercisable population (an ordinary
user, a domain admin, a kerberoastable SPN account, an AS-REP-roastable user): a domain with none of
them answers Kerberos and is decorative.

**The DC keeps its observed short hostname** (`axdc01`), scoped to that one role, one member, and a
hostname-keyed member. axRange sets a guest's hostname from its node NAME, and a DC's hostname is on
the wire in every Kerberos, LDAP and SMB frame the twin emits — the exact strings a detection rule
reads. Widening the rename to every single-member group is a defensible follow-on and deliberately
not taken: a node name is addressed by `net:`, by every `tfc` assignment and by the installer's
instance expansion.

`scripts/check_role_fixed_point.py`'s two `domain-controller` exceptions are **deleted**, not
re-worded: an exception that no longer describes anything is the vacuous green the script exists to
refuse. **24 catalog rows, 24 are fixed points.**

### The seam — `bundle_schema: axr-topology/1.4`

Per-node **`services[]`**: the listen census joined to the protocol actually spoken on edges to that
listen, so a consumer picks *bind9* rather than *"something on 53"*. Two evidence classes per row,
mirroring `ir_entity`'s own `role`/`role_evidence_class` pattern — `evidence_class` is the listen's
(`observed`), `app`/`app_evidence_class` are the join's, both ABSENT together when no table names
the port. The app token is spelled in the **sensor's** vocabulary (`krb`, `ssl`, `ldap_tcp`), never
the persona schema's, because a consumer joining it against `edges[].proto` under two spellings
joins to nothing (`LES-143`).

New top-level **`observations`**: `dns.names` (a straight projection of `ir_identity`, aggregated
per name+entity with the locality that tells an internal name from an external one) and
`identity.principals` (the `kerberos`/`ntlm`/`dhcp` kinds, verbatim, realm suffix included).

**Two corrections to the work order's §6.1 sketch, both reported rather than papered over.**
(1) DNS rows are `evidence_class: referenced`, not `observed` — the capture saw a NAME in an answer,
not the host — and the sketch's `server`/`qtype`/`count` fields do not exist at the IR boundary at
all. (2) There is **no per-listen `windows_seen`**: `ir_entity.listen_ports` is a bare
`["<port>/<proto>"]` array and the entity's own `windows_seen` is a host's occupancy, not a port's.
Publishing it under that name would be `LES-127`'s failure with a plausible number attached.

**Three sub-blocks are ABSENT, and the reason differs between them.** `identity.realm` and
`identity.spns` need reference-ingest S5 (the realm survives only inside `Identifier.raw`; the
Kerberos parser drops the `service` column). `tls.sni` / `http.vhosts` / `smb.shares` are one
carrier short, and it is worth stating precisely because the facts already exist: the Zeek parsers
project all three into the BELIEF STORE (`SERVICE_PROJECTIONS`: `ssl -> server_name`,
`http -> host`, `smb -> share_type/service`), and measured on the CorpAPCN store they are real
content — 8 distinct `serves_ssl` names, and `serves_http` binds `test-api.swlab.axellio.dom` to the
twin's own `10.80.75.171`. `ir/materialize.py` reads only the `listens_on`/`dials_port`/
`in_segment`/`has_role` predicates, so `serves_*` never crosses into a PIT. Adding a carrier is an
additive IR MINOR — a version-locked change on the one schema this project has ratified — and is a
**decision, not an implementation call**. No speculative `ir_service` shape was written ahead of it
(`LES-101`).

### The internet sink (work order §7.4, aximprint's own call)

The sink cannot observe itself, so its declared set is the ports the personas aim at it — which
exist precisely because nothing local answers them — unioned with the ports **derived from the
census of the population it replaces** (`sink_real_services`). Two filters, doing different work:
the **recipe table** (a port no real server covers can only move from the echo to the echo) and a
**share of the external listener population**, not a top-N — *"the twelve commonest"* bounds output
size, and what the sink needs is a bound on evidence: one external host seen answering somewhere is
not the internet answering there, and a single-host tail would otherwise put a DHCP server on the
sink segment.

Measured on this source (349 external listeners): **443/tcp** (191 hosts), **123/udp** (105),
**53/udp** (11), **80/tcp** (11) and **22/tcp** (5) clear the 1% bar and are served for real;
445/tcp (2) and 389/udp (1) do not and fall to the echo. The work order's §7.4 named the first two;
the census supplies three more on the same evidence. `COMPILER_SINK_PORTS` survives as the fallback
for a PIT with no external census at all, and the whole set is a `compiler`-class provenance row
carrying its own `real_services` list, by name.

**`LES-109` caught a defect here mid-build:** the share's first denominator was
`max(port_counts)` — the busiest port's own host count, 191 — rather than the external listener
population, 349. Both are plausible numbers off the same census and they answer different
questions; the wrong one moved the bar by nearly a factor of two and admitted 445/tcp.

### Measured end to end (read-only copy of the CorpAPCN IR store)

88 node groups, **18 with a declared `listen:`**, of which 4 are Windows and get no POSIX script.
**14 `serve-*.sh` emitted**, running bind9 on 8 node groups, nginx on 7, chrony on 5, an sshd
assertion on 4 (the sinks — no local host in this source was observed accepting on 22) and
isc-dhcp-server on 1. **7 node groups still carry an echo**, and
every remaining port is a stated gap rather than an oversight: 135/tcp ×3 and 389/tcp+udp ×2 and
445/tcp ×1 (Linux equivalents of a second DC — D5(a) declined slapd/samba recipes for this slice),
443/udp ×3 (QUIC — D7(a), no cheap real server without an HTTP/3 bake), 9997/tcp ×1 (Splunk's
forwarder protocol is proprietary — D6), and 137/138/49667/55390/57086/63153 (**not services** —
ephemeral sockets and NetBIOS, the work order's §4 last row). Two runs byte-identical. The cast
installs, **validates through axRange's own validator (`Result: PASS`, one warning — `OVR-020`,
which the shipped `corp-enterprise` twin config produces identically), and plans: ops=695
barriers=105 edges=1700**, including `wait-domain-ready[axdc01-app@corp.axr.lab]`, 4
`create-domain-user` and 6 `join-host-to-domain` ops.

### Two removals in the same pass, both because nothing called them any more

`responder.responder_script()` is **deleted**. Its whole body is now `services._render_echo`,
emitted as one section of a `serve-<node>.sh` beside the real daemons, and a second renderer of
the same shell block called by nothing is exactly `LES-101`'s hazard. `_RESPONDER_PY` — the
payload — did not move, and the four properties its tests asserted (detached start, fail-loud,
bind-before-thread, EADDRINUSE-is-a-skip) moved with them to `tests/test_synth_services.py`, now
asserted against the shipped path rather than a copy. `responder._needs_port_53()` is deleted
too, and it was **dead at HEAD** rather than made dead here — no caller anywhere in `src/`,
`tests/` or `scripts/` before this change either; its job is now a positive branch inside
`_render_bind9`, keyed on the bind9 recipe having fired rather than on a port scan.

### Honest limits, all recorded rather than left to be discovered

**Windows hosts get no recipe.** A POSIX script delivered through `run-script` runs as PowerShell on
a Windows guest and dies on the first heredoc (measured on dev45). The DC is served by its promotion;
three `corp-workstation` groups' 135/137/3389/7680 are genuinely UNSERVED and say so in `caveats`.
**A zone's A records point at the resolver itself.** A cast knows the twin's names and not its
addresses — axRange allocates them at deploy time — so the NAMES are observed and the addresses are
a compiler placeholder; §11 already puts payload out of row 5's scope (the rule is *protocol-valid*,
not *equivalent*). **The zone refuses external names by a positive gate**: only a name bound to a
local, placed entity, with no `_`-prefixed label and no reserved suffix, so the twin's resolver can
never claim authority for `sharepoint.us`. **DHCP is not `authoritative;`** and its pool is the
segment's top 12 addresses, away from axRange's low-end allocator. **The dial set is a cross-check
only**: a port personas aim at a role no node was observed answering is reported in `caveats`, never
added to the declared set. **The internet sink's bind9 is authoritative for the lab realm**, which
is the twin's zone rather than a resolver's stand-in view of the outside — a simplification, and
still strictly better than the echo it replaces, which returned a `dig` its own query.

---

## 2026-09-05 — reference ingest S2: an operator can add what the vantage could not see, and the twin says who said so

**Authority:** **user** for the rulings this implements (`docs/WORK_ORDER_reference_ingest.md` §11
and §4's D10-D18; axRange `docs/design/14-operator-assertions.md` §2-§4, ratified 2026-09-04,
including `axr-assertions/1` as the file's schema). Implementation, the deviations named below and
the three additions to the ruled shape: implementation (Claude Code).
**Supersedes:** `BeliefStore.snapshot()`'s cross-class reduction for `in_segment` and `has_role`
(removed rather than corrected — see below); `ir/materialize.py`'s
`"role_evidence_class": "imputed" if role else None` constant (D4, deferred from S1); the S0/S1
entry's claim that D4 *"trips `_check_schema_matches_this_build` on an old store"* (it does not —
that guard compares DDL columns, and `snapshot()`'s return shape is not one; verified against a
copy of the CorpAPCN belief store). Nothing else.

**Slice S2 of `docs/WORK_ORDER_reference_ingest.md`.** S3-S5 are separate rounds and are
deliberately not started.

### The file

`axr-assertions/1` — `src/aximprint/operator_assertions.py`, a below-every-layer module holding
one thing: the document axRange's Console writes and `aximprint cast --assertions` reads
(`docs/WORK_ORDER_console_canvas.md` §9 seam 3). **It is not inside `ingest/`** and the placement
is the point: two consumers on opposite branches of the layering need the same parse — the
reference plugin maps it into belief-store rows, the cast compiler resolves it against a PIT — so
a module importing nothing from `aximprint` keeps them from inventing a sideways edge, exactly as
`provenance` and `catalog` already do. Spelled `operator_assertions`, not `assertions`, because the
belief store's `assertions` table is this project's uniform *fact* shape and two things named alike
collide in prose, in a traceback, and in `docs/LESSONS.md`'s bare-filename `Scope:` matching
(CONVENTIONS §5's own ruling on that).

**Every vocabulary in it is closed and every refusal names the accepted set** (D10-D13): `add`
takes `segment`/`host`/`conduit`; `override.field` is per-kind (`role`/`image`/`resources`/
`segment` on a host, `zone_type`/`name` on a segment); `kind: route` is refused **by name with the
reason** — a twin config has no route object at all, and conduits are how reachability is authored.
`confidence` is never authored; every record needs a `note`; a key in both `add` and `suppress` is
a refusal, and so — an addition the ruling does not state but its own argument requires — is one
field declared by both an `add` and an `override`, since that is one class contradicting itself and
`resolve_precedence` refuses exactly that. **`suppress` takes host keys only**: removing a segment
that still carries placed hosts would leave every `net:` dangling, which is a topology edit rather
than a suppression.

**Two failure classes, split deliberately.** A **shape error refuses** — always the author's
error, the same error on every PIT. A **resolution MISS reports** — a key this PIT does not carry,
a conduit endpoint that is not a live segment. The PIT moves underneath a standing file by
construction, and refusing there would make every re-imprint an editing session; the misses land in
`caveats` and in `population.assertions.ineffective`.

### The two doors, and what each one cannot do

**`cast --assertions`** applies the whole document to one compile. **`aximprint assertions ingest`**
(the reference-ingest plugin kind, D14) writes the four facts the belief store's own readers consume
— `in_segment`, `has_role`, `listens_on`, `dials_port` — so an override of a *discovered* entity
survives re-materialization. Everything else has no store consumer and is **counted**, not invented
a predicate for (`LES-101`). Neither door substitutes for the other, and both limits are printed
rather than left to be found:

- **A belief-store row is a fact ABOUT a subject; it does not mint one.** An `add: host` for an
  address nothing observed writes nothing — `snapshot()` learns which subjects exist from
  `node_features`, which only a capture writes. Reported per key.
- **The two key vocabularies differ**, and writing without a map fails SILENTLY: a file keys on the
  IR's `address:10.80.20.5` (D10), the store keys on the resolver's `10.80.20.5`. Measured exactly
  that way against a copy of the CorpAPCN store — four correct-looking rows that every PIT ignored.
  The inverse is now re-derived through `materialize`'s own rule rather than reimplemented, in
  `cli.py`, this project's declared wiring point.
- **Rows are dated at the file's `as_of`** (D14, literal), and `snapshot_evidence` bounds an
  unqualified materialization at the last window's start — so a file dated after the capture is
  knowledge the store holds and a default PIT correctly does not carry. Correct under the as-of
  model and a footgun in practice, so `assertions ingest` prints the `--as-of` that fixes it.
- **A reference document is a SNAPSHOT, so a re-ingest REPLACES** the prior one from the same
  `(evidence_class, source)` rather than appending. Appending would resurrect a record the operator
  deleted from the file, which would then win precedence forever with nothing to say where it came
  from. Scoped by `source`, so two different files from one operator clobber each other — honest
  rather than accidental, and named here as a bound.

### The compiler

`compile_twin(..., assertions=)`. **A cast with no file is byte-identical to one compiled before
the mechanism existed** — checked, not asserted, and the determinism gate now compiles both ways.

- **A host carrying an assertion gets its OWN node group.** The group key gained an assertion
  signature, because a group's sidecar rows publish one class and one image for the whole group,
  and a hand-edited host sharing a group would publish its neighbours' inferred role as `asserted`
  or apply its image to them. An overridden group's name gains `-asserted`; an ADDED host takes a
  new off-ladder `ASSERTED_RUNG` instead, since it has no occupancy to place it on the ladder — and
  it is not "unmeasured" either: there is no denominator missing, there is no measurement to have.
- **`population` gained `suppressed`** (D8/D18) with `{key, note, placed, matched}` per record, and
  the reconstruction identity becomes `placed_hosts == sum(observed) + suppressed(placed)`. An
  asserted host reports `asserted: n` beside `observed: 0`, so a host an operator declared cannot
  inflate the population the capture claims to have FOUND. A new `population.assertions` block
  carries the account as data rather than as prose in a caveat.
- **D17's drift gained a "later" test the ruling implies but does not state.** Drift fires only
  when the PIT's `observed_to` falls after the file's `as_of`. Without that clause it fires on every
  override ever written — an operator asserts a role *precisely because* this PIT's census said
  something else — and a flag that is always on says nothing. Measured on the CorpAPCN cast, where
  it fired on the second-DC override the file exists to make. Flag only either way: the assertion
  still wins and both readings stay in the artifact.
- **An asserted `segment` no longer publishes as `observed`.** Found by asking for the `.net`
  provenance row of a segment-overridden group: the group key already carries the effective
  segment, so the row's VALUE was right while its class described where the value came from
  *before* the override — the masquerade the model exists to prevent, invisible in the emitted twin
  config and visible only in the one artifact whose job is to show it. The row is `asserted` now,
  with the members' observed segment and the census's confidence in `overrides`, and drift applies
  to it exactly as it does to a role.
- **A positive gate on node-name uniqueness.** Nothing guaranteed it before: `_node_suffix`'s
  trailing-zero truncation can already collapse two segments onto one suffix (the internet sink's
  own constant dodges it by not ending in a zero, and says so). `net:`, a `tfc` assignment and a
  tap binding all address a node BY NAME, so a duplicate is an ambiguous document.

### D4, landed here rather than in S1 — and the store stopped reducing at all

S1 deferred D4 as *"available to S2, the first slice with a producer that needs them"*, and S2 is
that producer. `ir_entity.role_evidence_class` is a **lookup** now, and `snapshot()`'s `roles` rows
carry a class. What is bigger than the deferral: **the cross-class reduction was removed from the
store, not corrected there.** `snapshot()` returns every class's reading — one row per (entity,
class) for both `segments` and `roles` — and `ir/materialize.py::resolve` picks the winner by
`(evidence_rank, -confidence, value)`. Three reasons, and the first is the one that matters: **the
house rule this whole slice is built around is that the store keeps both readings and an override
is visible, never an erasure**, which a reduction there defeats before anything can name the loser.
D3 already said the resolver belongs on the conclusions side. And `evidence_rank` lives in
`aximprint.ir`, which `store` may not import (CONVENTIONS §3), so the alternative was a second rank
table spelled out down there — the vocabulary drift `LES-101` names.

The old comparison was right by arithmetic accident (`SEGMENT_EVIDENCE` supplies observed/0.95 and
imputed/0.6, which rank the same way twice). S2 breaks the coupling — `asserted`/1.0 today,
`imported` rows outranking `observed` at a per-source confidence below 0.95 when S3 lands.

### CLI, gates, tests

`aximprint cast` gains **`--imprint-id`** (default: the PIT id; also fills the topology bundle's
`imprint_id`, which it never did) and **`--assertions`** (default: beside the IR store, D-f). A
**default** path that does not exist means "no assertions yet"; an **explicit** one that does not
is refused. New verb `aximprint assertions {validate,merge,ingest}` — three acts on one file, kept
as subcommands because folding them into flags would hide that.

`tests/test_ir_schema.py`'s closed-set guard **admitted `axr-assertions/1` in the same change**, per
`LES-141` — and the guard did its job on the way: it caught `verify_determinism.py` re-typing the
literal instead of importing it. `scripts/verify_determinism.py` gained an assertions fixture
**derived from the PIT it was pointed at** rather than checked in (`LES-103`: a file keyed on one
capture's addresses would resolve to nothing on another and leave the gate green over an empty
path), and the sidecar check now verifies the asserted cast actually carries asserted rows.
`test_acquire_layering` classifies `operator_assertions` as **forbidden for `acquire`** on
`catalog`'s meaning-not-cycles grounds. **2038 pytest tests** (80 new); tier 2 and the determinism
gate green.

**Live-verified end to end against a READ-ONLY copy of the CorpAPCN store** — the fixture's 9
records changed the cast exactly and only as declared (a `simple`→`vlan` zone type, a
`dns-resolver` group becoming `domain-controller-heavy-10-80-2-core-asserted` with the asserted
image and sizing, one added host, one added conduit, one suppressed host gone and one suppressed
external changing nothing), two runs byte-identical, and the full store round trip
(file → `assertions ingest` → `materialize --as-of` → `role_evidence_class: asserted`) confirmed.

---

## 2026-09-04 — reference ingest S0+S1: the evidence model gains a precedence order, `defaulted` becomes `compiler`, and the sidecar finally travels with the twin

**Authority:** **user** for the rulings (`docs/WORK_ORDER_reference_ingest.md` §11 — every D-a…D-i
taken as recommended, and the declared-transformation rule adopted into axRange's
`docs/design/14-operator-assertions.md` §2, ratified the same day). Implementation, the deviations
named below, and the two tests-and-gates additions: implementation (Claude Code).
**Supersedes:** `src/aximprint/catalog/roles.yaml`'s `domain-controller` premise and the two
`domain-controller` reason strings in `scripts/check_role_fixed_point.py` (§7.1 — corrected, not
removed); `docs/SPEC_network_ir.md`'s "Evidence classes" section (three → five) and its "the only
versioned schema in the project" scope claim. Nothing else.

**Slices S0 and S1 of `docs/WORK_ORDER_reference_ingest.md`.** S2 (the assertions ingest) is a
separate round and is deliberately not started.

### The evidence model (S1)

`EVIDENCE_CLASSES` is now **five, in precedence order**: `asserted` > `imported` > `observed` >
`referenced` > `imputed`. **The tuple IS the ranking** (D1) — `evidence_rank()` reads its position
rather than a second table that could drift from the vocabulary it ranks (`LES-101`) — and it
**refuses** anything outside it, `compiler` included, so a sidecar-only label structurally cannot
compete against evidence.

`referenced` keeps its meaning **exactly**: second-hand evidence *from inside the capture* — a DNS
answer, an SPN, a redirect, an XFF header, the class behind `ir_unseen`'s census. It was never a
name for out-of-band imports, which sit on the *opposite* side of `observed` in trust, and that is
the whole reason `imported` is a separate class rather than a second job for this one.

**`docs/SPEC_network_ir.md` → `1.4`, carrying two changes** (D-a option (a)). The second is a
correction: `ir_entity.listen_ports`/`.dial_ports` were added 2026-08-27 and **never bumped**,
against the SPEC's own additive→MINOR rule. Naming them in `1.4`'s entry rather than retro-dating a
version avoids minting one no artifact ever carried, and the consequence is disclosed in the
reading rules — **stores written since 2026-08-27 say `1.3` while already carrying those two
columns**, so those two must be detected by column presence, never by version. Verified on disk:
six CorpAPCN IR stores written before that date say `1.3` and lack them; the 2026-09-02
re-materialization says `1.3` and carries them. One more thing a version check cannot catch, so it
is written down instead: a `1.3` reader that hardcoded "`observed` is strongest" **mis-ranks** a
`1.4` artifact, because the column is the same column.

**A precedence resolver in the compiler** (`synth/compiler.py::resolve_precedence`, with a typed
`Fact`). The higher class wins; the sidecar records the runner-up under
`overrides: {class, value}`; **two facts of one class are refused loudly** rather than ordered,
because that is a contradiction inside one producer and choosing by arrival order is the row-order
dependence `LES-009` forbids. **Nothing in the store produces two classes for one fact yet** — S2's
assertions file is the first producer — so it is exercised by unit tests over synthetic pairs,
including every one of the ten ordered pairs of the five classes and every permutation of a
three-way contest.

**The declared-transformation rule, built as a separate mechanism** (`declared_transformation`;
§5.6, adopted as axRange `14` §2). A value the compiler replaces *for deployability* — a relocated
CIDR today, the lab realm when S4 lands — never enters precedence, is applied after resolution, and
records the pre-transformation value. The relocated-CIDR path now goes through it and demonstrably
**not** through the precedence path: its row carries `transformation: {kind, reason, from:
{evidence_class, value}, to}` and no `overrides`, while the internet sink — a `compiler` value
*invented* rather than *moved* — carries no `transformation` at all. That distinction is what lets
a scorecard read the first as *reproduced under a declared transformation* rather than as a
mismatch.

### Sidecar hygiene (S0)

- **`defaulted` → `compiler`** at every `prov()` site in the cast compiler, and in every reader of
  the cast sidecar. Meaning unchanged, which keeps it out of `LES-131`'s hazard — but a consumer
  outside the producer reads the string, so the artifact announces the change itself. **Scoped
  deliberately**: `synth/synthesizer.py`, the superseded v0 draft path, still emits the old
  spelling and is left alone rather than half-migrated (its docstring now says so); the artifact
  this rename is about is the cast sidecar, where a real 656-field cast carries zero occurrences.
- **The sidecar declares `schema: "axr-cast-provenance/1"`** (D6/D-b), distinct from the `schema_version`
  passthrough beside it, which is the *IR's*. Without it the rename is indistinguishable, at
  axRange's reader, from a bug in that reader's own parsing.
- **`signal` on every row, positionally required** (D7). Every value this compiler emits has a
  nameable rule or stem — a catalog row (`catalog/roles.yaml domain-controller (389/tcp+88/tcp)`),
  an IR column (`ir_segment.cidr`), a hash — so `prov()` takes it as a positional argument rather
  than an optional keyword: a lesson that says "prefer X" is honoured only when the signature stops
  you doing otherwise (`LES-114`).
- **`confidence` only where a producer supplied one**, absent — never null, never invented —
  elsewhere (`LES-006`). New on `nodes[].net` from `ir_entity.segment_confidence`, all-or-nothing
  over a group's members so a partial reading never reports as a complete one (`LES-127`). On the
  real CorpAPCN cast: 151 of 656 rows carry one, and **not one of the 269 `compiler` rows does**.
- **`topology.json` declares `bundle_schema`** (D-e), leaving the IR passthrough untouched.
- **`scripts/install_cast_as_twin.py` copies the sidecar** to `<stable>.provenance.json` (D9).
  Verified before the fix: **every cast output directory had one and no installed cast did** — the
  gap three separate drafts found independently. Copied *verbatim*, unlike the three documents whose
  id is rewritten: those are addressing, this is provenance, and its `pit_id` is the only in-file
  record of the origin. A cast without one now warns on stderr rather than installing silently.

### Hand-backs (S0)

- **The `roles.yaml` premise was false and is corrected in place.** *"Nothing in a capture supplies
  an AD forest name; `ir_identity` carries only external DNS"* is the stated reason `role: adds` was
  reverted on the `domain-controller` row. Measured read-only against `capcn-ir6.duckdb` (PIT
  `pit-63994c9543-1787823960`, 11 windows): the realm is on the wire in **observed** rows —
  `BW-L$/AXELLIO.DOM`, `desktop-21n0pq1$/AXELLIO.DOM`, `matthew.admin/AXELLIO.DOM`,
  `Guest@AXELLIO.DOM/AXELLIO.DOM` (Kerberos) and `axdc01.Axellio.dom`, `AxeHyperV-01.Axellio.dom`
  (NTLM, the DC itself). `ir_identity` is 730 rows: 708 `dns`/`referenced` — the external names the
  old comment saw — plus 22 observed kerberos/ntlm/dhcp rows, and the realm is in every one of the
  first two kinds. **The visible majority was not the answer.** What is actually in the way is a
  *parser* gap, now named: the realm survives only inside `Identifier.raw`, because
  `windowing/entities.py` drops it by design and `raw` has no column at the IR boundary. The row
  **stays reverted** — that is S4's change, not this one — but its reason is now true, and
  `check_role_fixed_point.py`'s two exception strings say the same thing. A declared exception whose
  stated reason is untrue is worse than no exception: it argues future work out of existence.
- **The port-389 `serves` tie is fixed in the mechanism** (§7.2). `Counter.most_common` breaks a tie
  on **insertion order**, which here was DuckDB row order, and §2 measured a genuine 2-2 tie on 389
  (`dns-resolver: 2`, `domain-controller: 2`, `file-server: 1` — reproduced exactly during this
  build). So the peer every LDAP emit in a cast twin aimed at was decided by row order, and the
  twin's own domain controller was never dialled for LDAP. Ties now break on the **catalog's
  declaration order** (`LES-136`), which is already most-specific-first, with role name as the final
  total-order key. Pinning `389/tcp` at the DC in the catalog would have fixed one port and left
  every other contended one (445 is 2-1 today) resolving by row order exactly as before.
  Regression-pinned under **ten seeded shuffles** of the entity rows, and both new tests were
  **proven to fail** against the old `most_common` (`LES-031`), while a third — a clear majority
  must still beat catalog order — passes under both, guarding the over-correction.

  **Blast radius measured before shipping, per `LES-136`'s own rule** — on the real CorpAPCN PIT,
  16 ports draw a `serves` vote, **3 have a genuine tie, and exactly those 3 change winner**;
  no non-tied outcome moves. All three land the right way round: **389** `dns-resolver` →
  `domain-controller` (the headline), **123** the same pair (an AD domain's authoritative time
  source *is* its PDC emulator), and **137** `generic-endpoint` → `corp-workstation` — the second
  of which matters beyond NBNS, because `generic-endpoint` asserts *"this host consumes services
  and serves none"* and was being resolved to as an ANSWERER, which the role's own definition
  forbids. A dial-only rule can no longer beat a listen-bearing one on a tie, because the catalog
  already sorts every dial-only row after every listen-bearing one.

### Deliberately deferred, the same way the work order defers them

The three parser gaps of §7.3 are **not** in S0. The Kerberos `service` column
(`ldap/axdc01.axellio.dom`) and the realm-as-a-first-class-fact are **S5** by the work order's own
assignment, and SMB share paths belong to `docs/WORK_ORDER_protocol_faithful_services.md` S4. S0
corrects the *claim* that the evidence does not exist; extracting it is scheduled elsewhere.

### Deviations from the work order, and why

1. **The resolver lives in the compiler, not in `ir/materialize.py::resolve()`** (D3), and
   **D4's two de-constanted sites are untouched.** D4 changes `BeliefStore.snapshot()`'s `roles`
   tuple shape, which trips `_check_schema_matches_this_build` on every existing store — including
   the CorpAPCN stores this round had to read to verify §7.1's premise. The resolver's consumer is
   the sidecar, which is compiler-owned, so nothing is lost by placing it there now; D3/D4 remain
   available to S2, which is the first slice with a producer that needs them.
2. **`bundle_schema` is `axr-topology/1.3`, not the drafted `1.4`.** That number names a shape with
   per-node `services[]` and a top-level `observations` block
   (`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §6.1), and this build ships neither —
   stamping it would announce content that is not in the file, which is the exact defect D-a
   corrects one document over. `1.3` describes the shape the export has actually had and mirrors the
   IR version it had been republishing, so "1.3 → 1.4" keeps meaning what both work orders say.
3. **`overrides` is a `{class, value}` dict naming the runner-up**, not a list of every beaten
   reading. The house rule is "the sidecar names the loser", singular; the store keeps every
   reading, and restating a third-place value would read as a second competition. Called out
   because a three-way contest becomes reachable once S2 and S3 both land.

### The review round: one real bug in this build's own new code, measured and fixed

**`_role_signal` named a catalog row looked up by role NAME, not the row that matched.**
`_rule_for(role)` returns the FIRST row bearing a name, which is correct for `image`/`resources`
(every row of a role carries identical content) and **wrong for provenance**: a role name is a
many-to-one projection of the catalog and the inverse is not a function. Four shipped roles have
several rows with different evidence — `generic-endpoint` ×5, `corp-workstation` ×4,
`monitoring-collector` ×3, `web-tier` ×2. Measured read-only on the CorpAPCN PIT: **70 of 122
roled placed hosts** would have been published under ports that did not produce their match — 45
of them dialling `123/udp` and captioned `->443/tcp` — across 189 of 656 sidecar rows. That is
worse than an absent field: a specific, plausible, *wrong* evidence string is unfalsifiable at the
consumer, inside the artifact whose whole job is keeping observed and invented apart. And the
docstring asserted the opposite of what the code did, claiming the matching row was "already in
hand" — it was, in `listen_ports`/`dial_ports`, and simply was not used.

**Fixed by deriving, not by weakening the claim.** New `_matched_evidence(role, members)` re-runs
the census's own `match_role` over each member's port evidence — one rule applied twice, not a
second guess — and degrades to the bare `catalog/roles.yaml <role>` when the group is
heterogeneous. The real cast now shows `generic-endpoint` correctly split across `->123/udp`,
`->443/tcp`, `->53/udp` and `->1900/udp` with 9 groups honestly degraded, and `web-tier` split
443/80. **A second case surfaced from the same data and is guarded too:** one host carries the
stored role `dns-resolver` while its PIT-*aggregated* ports now match the `domain-controller` row
— the role was inferred per window and the PIT unions ports across all of them — so a rule for a
different role now names no evidence for this one. Both are pinned by tests **proven to fail**
against the old lookup.

**Three more review findings fixed in the same pass.** (1) The new determinism gate serialized
with `sort_keys=True` while `cmd_cast` writes without it — so the gate normalized away exactly the
dict key order the shipped file preserves, and was green on bytes nobody ships (`LES-098` one
notch in: naming the right producer is not enough if you compare a different rendering of its
output). It now serializes identically to the writer, proven by re-serializing a real
`provenance.json` and comparing bytes. (2) `declared_transformation` discarded the replaced
`Fact`'s `signal` — a forensic-completeness gap dressed as a completeness measure; `from` now
carries the replaced reading's own provenance. (3) The sidecar gate's `"defaulted" in p1`
substring test is now scoped to `evidence_class` values, so a future `reason=` string that
legitimately explains the rename cannot trip it, and its `population` access is `.get`-guarded so
a malformed block names itself instead of raising a `KeyError` from a gate whose job is legibility.

**And the `LES-001` guard was converted from an allow-list to a closed set.** `CONVENTIONS.md` §3
says `ir` is *"the one package permitted to declare a schema version"* and cites
`tests/test_ir_schema.py` — but that test scans for the identifier `SCHEMA_VERSION`, so once two
strings landed under deliberately different spellings it enforced a spelling, not the rule. The
new test discovers version-shaped literals structurally and asserts the set equals the ratified
one; **verified by injecting `axr-assertions/1`** — the exact string S2 introduces — and watching
it fail by name. A guard narrowed by the exception it was granted is `LES-014`'s failure arriving
politely.

### A latent defect the widening exposed, found and fixed in the same round

**`ir/merge.py` carried a SECOND evidence rank table** —
`_EVIDENCE_RANK = ("observed", "referenced", "imputed")`, with an `else 99` fallback for anything
outside it — which is precisely the drift D1 made `EVIDENCE_CLASSES` an ordered tuple to prevent
(`LES-101`). It went wrong the moment `1.4` landed: `asserted` and `imported` rank **above**
`observed`, and that fallback would have ranked both **below** `imputed`, silently inverting the
model's whole ordering on every merged PIT — and no test could have seen it, because nothing
produces those classes yet. `_strongest` now ranks through `evidence_rank()`, and an unrecognized
class **refuses the merge** rather than sorting last: ranking an unknown reading last is a claim
that it is the weakest one available, not a default (`LES-006`), and `_check_mergeable` already
establishes that a merge which cannot be made honestly is not made. Narrower than a version
refusal — a higher-MINOR artifact stays readable, and only a row actually carrying an unknown
class stops a merge. Pinned by two tests, one per direction.

**A third rank site was found in the same sweep and deliberately NOT changed.**
`BeliefStore.snapshot()`'s `in_segment` reduction picks the surviving reading on `max(confidence)`
alone — while its own comment claimed the choice was made *"explicitly rather than by relying on
`max(confidence)` and `evidence_class` happening to rank together"*. Read the loop: it relies on
exactly that coupling. The coupling still holds by arithmetic accident (`SEGMENT_EVIDENCE` is
`observed`/0.95 and `imputed`/0.6), and it breaks at **S2**, where an `imported` reading outranks
`observed` while carrying a "per source" confidence that can sit below 0.95. Behavior is
unchanged here because there is no producer yet and the fix belongs with one (D4 is the same shape,
one table over); the **comment is corrected in place** with the trap named, on the same principle
this round applies to `roles.yaml` — an aspirational comment is a claim, and a claim the code does
not honour is the defect `LES-139` is about.

### Also landed (not in the work order's own list)

**`scripts/verify_determinism.py` gains a fourth check: the cast sidecar.** Checks [1]–[3] compiled
through `synth/synthesizer.py` — the **v0 draft path** — so the artifact every cast actually ships,
`synth/compiler.py`'s twin config and its provenance sidecar, had **never been covered by a
determinism gate at all**. That is `LES-098`'s failure one layer above a rendering function. The new
`[3/4]` compares the sidecar's bytes across two passes *and* asserts the invariants a byte-equality
gate is structurally blind to (`LES-105`): the declared schema, that no row has reverted to
`defaulted`, that every row carries a `signal`, that no `confidence` is null, and that
`placed_hosts == sum(observed)` still holds. `PROVENANCE_SCHEMA` is imported from the producer
rather than re-typed. `--relocate` is passed deliberately, since it is the only live producer of a
`transformation` record.

### Verified

**S1's acceptance criterion, checked on the real CorpAPCN belief store rather than a fixture**
(`capcn6.duckdb`, copied to scratch so the original was only ever read): the same
`EvidenceSnapshot` resolved by this build and by this build with `SCHEMA_VERSION` forced back to
`1.3` differ in **exactly one cell across 4,287 rows in 8 tables** —
`ir_pit.schema_version`. The only evidence classes any producer actually emits are still the
original three, which is what "no producer of the new classes yet" is supposed to mean and now the
thing that says so.

**1958 pytest tests pass** (1910 before). Determinism: `verify_determinism.py` green on all four
checks, and a **real** two-process cast of the CorpAPCN store
(`src-ir-rematerialized-20260902.duckdb`, 786 entities, 141 placed) is byte-identical. That cast's
sidecar: 656 fields, **zero occurrences of `"defaulted"`**, `schema: axr-cast-provenance/1`,
`signal` on every row, 32 declared transformations, 0 overrides (as expected — no producer yet), and
the population identity intact at 141. `install_cast_as_twin.py` run against it lands
`corpapcn-proof.provenance.json` byte-identical to the cast's. `check_role_fixed_point.py` still
reports 24/24 fixed points. **No new ruff findings** — checked file-by-file against `git show HEAD:` copies, not only against the enforced gate, whose scope is `src/aximprint/acquire tests/acquire` and would not have seen one.

---

## 2026-09-03 — the convergence loop closes: fidelity becomes a closed spec against a "golden twin," not a distance between two fingerprints

**Authority:** user. **Supersedes:** the convergence *objective* of the CorpAPCN behavioural
fidelity loop, ratified at the top of `docs/JOURNAL_corpapcn_fidelity.md` 2026-08-27 ("source
CorpAPCN, deploy the cast, compare captures, fix both repos, iterate with `te deploy --update`").
The loop's **measurements stand** — nineteen iterations of real, recorded numbers against a real
reference, none retracted. Only the **objective** built on top of them — minimize the distance
between two fingerprints — is retired.

**The trigger.** Iteration 19 (`docs/WORK_ORDER_proto_weight_rows.md`, entry immediately below) hit
its own stated target — the twin's internet sink re-infers `web-tier`, as the source's own census
says it should — and cost two of the three axes the work order itself named must-not-regress. That
is not a new failure; it is the same shape every iteration since 16 has produced: fix what the
measurement points at, watch a different axis give back what the first one gained.

**Why the objective is retired rather than tuned again — three reasons, each sufficient alone.**

1. **Six iterations, one band, no trend.** `role_edges_local` — the axis iteration 15's own
   retraction established as the one that decides convergence — has read 0.595 / 0.618 / 0.742 /
   0.589 / 0.533 / 0.593 across iterations 14 through 19. That is a band, not a trajectory, and the
   nine iterations before that never converged it either.
2. **Most of what the reference has, the twin structurally cannot produce.** 12 of the source's 18
   local role-edge keys are absent from the twin by construction, not by a tuning gap: a Windows
   domain controller that answers no connection at all (`docs/WORK_ORDER_windows_responder.md`), and
   workstation-to-workstation traffic the reference does not carry at all. Re-weighting the six keys
   that *can* exist only trades one axis against another — exactly what iterations 16 through 19 did,
   each in turn.
3. **Shape is not the goal, and is only a loose proxy for what the goal depends on.** The fingerprint
   measures traffic shape. The platform's purpose is attack/defense experiments against a twin, which
   depend on real protocols (so detections fire against real traffic, not a shape-alike), real
   identity (so attack paths exist to walk), a matching security posture, and a matching
   configuration — none of which a traffic-shape distance can certify. The operational half of that
   list (configuration, state, security posture) this loop was never positioned to measure at all: it
   is an **observation gap** in aximprint's evidence, not a reproduction gap in axRange's twin
   compiler.

**What replaces it: fidelity to a closed spec — a "golden twin."** Ten dimensions: asset, topology,
configuration, state, protocol, performance, security, dependency, temporal, behavioral — plus a
visual row, kept for operator trust rather than as an engineering claim. Each dimension is tagged
with the observation channel that feeds it and an evidence class (`observed` / `referenced` /
`imputed`, aximprint's existing vocabulary), and weighted by the experiment it actually serves rather
than by how easy it is to measure. The **behavioral** row is this loop's fingerprint, kept — as a
**tolerance**, not a convergence target, since a band that will not close is not a target.

**The operational half becomes a second evidence channel, not a synthesis problem.** Configuration,
state and security posture were never going to emerge from re-weighting traffic-shape axes; they need
to be **observed**, from a source the wire does not carry. "Reference ingest" — directory exports
first (closes the domain-controller gap `docs/WORK_ORDER_windows_responder.md` names), then host
inventory, network device configs, security-tool baselines, and operator-asserted facts where nothing
else reaches — feeds aximprint's existing `ir_identity`/`ir_unseen` tables under the existing
`referenced` evidence class (reusing both; this decision does not itself ratify or version anything
under `LES-001`), and a compiler step emits the `identity:` block axRange's twin config already
accepts today. Posture is injected into the twin; secrets are never carried across that channel.

**Consequences for the two work orders this loop leaves open, recorded in their own STATUS
headers.** `docs/WORK_ORDER_windows_responder.md` is **SUPERSEDED in approach**, not abandoned: the
domain controller answering nothing is still the largest structural gap in the loop's own numbers,
but the fix is a real, promoted DC populated from a directory export via identity injection, not a
`.NET`-listener responder wearing a Windows accent — a responder makes the host answer, it does not
make it a domain controller. `docs/WORK_ORDER_source_locality_filters.md` is **PARKED**: its
proposed A/B is behavior-row tolerance tuning, worth running only if that row fails its own bar, and
its pre-delegated ruling (§7, "no seam" for one of the three censuses) is withdrawn along with the
convergence objective it was written to serve. `docs/WORK_ORDER_proto_weight_rows.md` is **DONE**,
recorded as this loop's own closing iteration (entry immediately below).

**What this decision does NOT do — stated so nothing here is read as more finished than it is.** No
new work order is written by this entry. Four follow-ons are named, not built: a cross-repo fidelity
spec (design of record on `wip/golden-twin` in **both** repos — the same branch name deliberately
used on both sides; canonical file `docs/design/13-golden-twin-fidelity.md` in axRange),
`WORK_ORDER_reference_ingest.md` (aximprint), a fidelity scorecard (axRange), and a firewall-policy
IR extension (axRange). All four are deliberately left for a fresh design pass rather than drafted
under this entry's own momentum.

**Branch plan.** `wip/persona-fidelity` is strictly ahead of `master` and contains
`wip/axrange-integration` in its history (verified: `git merge-base wip/persona-fidelity master` is
`master`'s own tip, `585bd71`). Close out this branch, fast-forward both repos' `master`, then open
`wip/golden-twin` for the design pass named above. The two repos stay separate — the file contract
between them (a Scenario IR / twin config on axRange's side, a captured IR on aximprint's) is treated
as the architecture, not a temporary inconvenience, and revisiting that split is explicitly deferred
until aximprint's schemas leave incubation (`LES-001`).

**Affected docs:** `docs/JOURNAL_corpapcn_fidelity.md` (closing section), `docs/WORK_ORDER_proto_weight_rows.md`,
`docs/WORK_ORDER_windows_responder.md`, `docs/WORK_ORDER_source_locality_filters.md`,
`docs/SESSION_STATE.md`, `docs/LESSONS.md` (LES-137).
**Affected code:** none — this is a direction ruling; the code it follows is the entry immediately
below.

---

## 2026-09-03 — iteration 19: emit ranking counts rows, not flows, and `web-tier` is declared ahead of `dns-resolver` on their specificity tie

**Authority:** user for the ruling (D1 = (a)+(b), D2 = no seam — `docs/WORK_ORDER_proto_weight_rows.md`
§4) and the catalog reorder it authorizes; implementation (Claude Code) for the census that decided
against option (c), the fixed-point-checker rewrite, and the test suite.

**Supersedes:** the `dns-resolver`-before-`web-tier` declaration order `roles.yaml` has carried
since synth v1 phase 2 (2026-08-14) — `roles.py:186-187`'s own comment called the within-band order
"the author's deliberate tiebreak"; this entry overturns that tiebreak specifically, with a
measurement, not merely a preference.

**The ruling.** **D1 = (a) + (b).** Option (c) — a `dns-resolver` row requiring `53/udp` **and**
`53/tcp` — was built, measured against the real source, and **not taken**: 13 of the source's 15
`dns-resolver` hosts carry `{53/udp}` as their entire listen evidence, so a two-port row would strip
the reference's own resolvers from 15 down to 1, and 8 of the twin's 15 sink sub-roles would still
read `dns-resolver` under it even after the responder re-install. **D2 = no seam** — taken as the
work order's own recommendation: the reorder applies to `ROLE_CATALOG` itself, not a sink-only
exception, since the sink is re-inferred through the ordinary catalog like any other host.

**What was built.**
- `src/aximprint/synth/personas.py::proto_weights` (`:406`) now accumulates **1 per edge row**
  instead of `int(ed.get("flows") or 0)`; the docstring (`:369-380`) is rewritten to state the
  rows-vs-flows distinction directly instead of conflating a row with a flow, keeping the surviving
  argument against ranking by `bytes`.
- `src/aximprint/catalog/roles.yaml`: the `web-tier` row requiring `443/tcp` is declared ahead of
  the `dns-resolver` row requiring `53/udp` (`ROLE_CATALOG`'s post-sort order at the affected
  indices: **5 `web-tier` (443)**, **6 `dns-resolver`**, 7 `web-tier` (80) — reversed from before),
  with the ruling, the zero-source-flip census, D2, and one accepted wrinkle recorded in the row's
  own comment: a 0.6-confidence row now outranks a 0.7-confidence one on this specificity tie, since
  confidence describes what a row asserts once matched and has never been the tiebreak.
- `scripts/check_role_fixed_point.py` — previously **skipped with exit 0** in this repo's own venv
  on every run (`No module named 'ruamel'`, which axRange's persona catalog needs and aximprint
  deliberately does not carry), a **vacuous green** that made the loop's own fixed-point acceptance
  criterion satisfiable by a skip. Rewritten to a real three-way exit contract — **0** clean, **1** a
  finding (a catalog row is not a fixed point), **2** could-not-look (axRange not importable, a
  persona the catalog names that axRange's own catalog does not ship, or an unreadable/malformed
  catalog file) — never conflating a finding with a could-not-look. Repo-anchored catalog path (was
  cwd-relative); optional positional argument to check a different catalog; `AXRANGE_ROOT` override.
  Run under an interpreter that can import axRange (`/home/rocky/axRange/.venv/bin/python`, which
  carries `ruamel`): **24/24 fixed points.**

**The blast radius, measured, not assumed.** Brute-force over all `2**16` subsets of the source's
observed listen sets: the reorder flips exactly the twin's 15 sink sub-role hosts and zero source
hosts — every changed outcome is a listen set containing both `53/udp` and `443/tcp` with no
two-port match, i.e. precisely the tie D1b targets.

**Verification.** `tests/test_persona_synth.py` gains `test_proto_weights_count_edge_rows_not_flows`,
and three existing fixtures are re-shaped so rows and flows order **oppositely** — they had passed
under the old code only because rows and flows happened to agree on those particular fixtures, the
exact trap `LES-135` generalizes. `tests/test_role_catalog.py` gains five: the declaration order
itself, a sink-shaped listen set resolving to `web-tier`, a bare-53/443 tie resolving to `web-tier`,
a UDP-only-53 host staying `dns-resolver`, and a domain controller with both 53 and 443 staying
`domain-controller` (the existing higher-specificity row is unaffected). `tests/test_check_role_fixed_point.py`
is new — six hermetic tests covering all three exit codes. Suite: fast 1882 / **full 1884 passed**
(baseline 1872). `scripts/verify_determinism.py` holds — two casts of one PIT byte-identical.

**A hermetic-tier defect found and fixed in the same pass, unrelated to the ruling itself.**
`scripts/verify_tier.sh` was **red at HEAD** on `ruff (acquire only)` — an import-order violation in
`src/aximprint/acquire/cli.py` untouched since `9ef7b37` (2026-08-27), because nobody had run the
tier since. Fixed with `ruff --fix`, landed as a separate `chore` commit deliberately not bundled
with this entry's own changes — tier now passes 4/4.

**Roles are baked at ingest, not at fingerprint time — so a re-score needed a re-ingest, not a
re-run.** Before spending a live run, iteration 18's OWN capture was re-ingested under the reordered
catalog, isolating what the reorder alone moves:

    axis                        iter 18   iter 18*   source
    role_edges                    0.878      0.411       --
    role_edges_local              0.533      0.533       --
    fan_out_degree_local          0.280      0.280       --
    external_dst_edge_share      0.8641     0.8641     0.8476
    role_census                   0.033      0.033       --

`*` iteration 18's own capture, re-ingested under the reordered catalog. Only `role_edges` moves —
the three must-not-regress axes are byte-identical under re-score, confirming the reorder alone
does not touch them; any further movement comes from the live run itself.

**Outcome, measured against the standing twin — recorded as a trade, per the work order's own §6
rule.** Full axis table, the projection-vs-measurement check, and the resolver-census finding are in
`docs/JOURNAL_corpapcn_fidelity.md`, "Iteration 19." Summary: the live run's `role_edges` (0.406)
agreed closely with the re-score's projection (0.411) — the target was hit, the sink re-infers
`web-tier` (14 of 15 sink hosts) — and two of the three must-not-regress axes regressed:

    axis                        iter 18    iter 19    source
    role_edges                    0.878      0.406       --
    role_edges_local              0.533      0.593       --
    fan_out_degree_local          0.280      0.290       --
    external_dst_edge_share      0.8641     0.8153     0.8476

**What this does not do.** No `te`-tier change and no redeploy — `twin_config.yaml` stayed
byte-identical, verified, so the standing 138-VM twin (`corpapcn-twin-0e4f7d58`) carried this
iteration the same way it carried 16, 17 and 18. No change to the Windows responder gap or the
source-locality-filter question — both stay in their own work orders, now SUPERSEDED and PARKED
respectively by the entry above.

**Affected docs:** `docs/WORK_ORDER_proto_weight_rows.md` (STATUS → DONE), `docs/JOURNAL_corpapcn_fidelity.md`
(Iteration 19), `docs/LESSONS.md` (LES-134, LES-135, LES-136), `CLAUDE.md` (test/lesson counters).
**Affected code:** `src/aximprint/synth/personas.py`, `src/aximprint/catalog/roles.yaml`,
`scripts/check_role_fixed_point.py`, `tests/test_persona_synth.py`, `tests/test_role_catalog.py`,
`tests/test_check_role_fixed_point.py` (new), `src/aximprint/acquire/cli.py` (lint only, separate
commit).

---

## 2026-08-27 — the twin's traffic was a function of the role catalog, never of the network; a behavioral layer lands in the IR

**Authority:** implementation (Claude Code), on a work order drafted and built the same day
(`docs/WORK_ORDER_traffic_profiles.md`, **DRAFTED, unratified**). **Scope ruled by the user**:
wide / IR-first rather than sized to what `cast` can emit. **Supersedes:** nothing — additive to
`docs/WORK_ORDER_network_ir.md`, and it does not move the snapshot boundary
(`docs/WORK_ORDER_evidence_plane_snapshots.md` is untouched).

**The gap, found by the user asking what an IR PIT contains.** `cast` emits a `tfc` document and
axRange accepts it — carrying **role→persona assignments and nothing else**. No intensity, no
schedule, no seed. So the twin's traffic was a pure function of the role catalog: of what we decided
a role *is*, never of what the network *did*. Meanwhile the evidence plane holds, per entity per
window, `flows_out`/`flows_in`/`bytes_out`/`bytes_in`, `client_ratio`, `proto_mix`, three quantile
sketches, a 12-bucket `subbucket_flows` array and a computed `burstiness` — **none of it reaching
the twin**. Separately, `Q29`'s `profile_facts` channel had been written by `StoreSink` since
2026-08-13 and **read by nothing** but a `status` row count.

**The measurement that bounds this is on the CONSUMER side, and it was taken before scoping the
producer.** `axrange/o2control/traffic.py::AssignmentSpec` accepts exactly three optional fields
beyond `role`/`node` and `persona`: `schedule` — a **closed 2-token enum** (`24x7` |
`business-hours`, `_SCHEDULE_ALIASES`), which **is** consumed (it overrides the persona's
cadence-derived emit gate); `seed`, which **is** consumed (`personas/drivers/base.py::effective_seed`
folds it into the traffic-timing RNG); and `intensity`, a float that is parsed, validated and carried
into their DTOs and op registry and **read by no driver** — their own comment: *"optional,
informational (persona.knobs override — follow-on)"*. **Exactly one measured quantity can change twin
behavior today.** There is no field for a rate, a diurnal curve, a burst distribution or a cache
lifetime, which is also why `ocsp_cache_lifetime` — `profile_facts`' only occupant — has no receiver
in this vocabulary at all.

**Why the scope is wide anyway, and that was the user's call.** Sizing this to axRange's three
scalars would build for the consumer with the narrowest vocabulary. The IR has **four** consumers,
and a behavioral layer earns its place on the drift comparator alone: `drift` reports structural
change only and **cannot see that a host's activity shape changed**. The narrow projection is a dozen
lines on top of the wide layer either way.

**This needed nothing from the layers below.** Every input already sits in `node_features` rows,
which carry `window_start`; hour-of-day is arithmetic on it. **No ingest change, no windowing change,
no new event type, no plugin change** — the same shape per-segment coverage took at
`docs/WORK_ORDER_network_ir.md` step 1b. `PROFILE_PARAMETERS`' own preamble had already ruled its
channel out for the job (*"Neither an integer seed nor a diurnal 24-bucket schedule ... fits this
shape"*), and that ruling is **adopted, not revisited**: a diurnal profile has a subject, so it does
not belong in a subject-less channel. `profile_facts` keeps its narrow contract and gains a reader.

**What changed.** `ir/schema.py`: `SCHEMA_VERSION` → `"1.3"`, a `SCHEDULE_CLASSES` closed vocabulary,
and DDL for two new tables — `ir_traffic_profile` (per entity) and `ir_capture_profile`
(subject-less). `ir/materialize.py`: `classify_schedule()`, `_entity_traffic()`, `_pooled_quantiles()`
and `_capture_profile()`; `EvidenceSnapshot` gained `traffic`/`profile_facts`. `synth/compiler.py`:
`_role_seed()` and role-level aggregation projecting `schedule`/`seed`/`intensity` into each
assignment. `ir/store.py` needed **no change** — it is generic over `IR_TABLES`.
`docs/SPEC_network_ir.md` updated in the same change, because `tests/test_ir_schema.py` fails if the
two disagree and that is the point of it.

**Schema `1.2` → `1.3` is additive → MINOR**, the ceremony `1.1` (locality) and `1.2` (vantage +
unseen) both followed. **Not a §0.2 ratification** — the IR is already the one ratified versioned
schema and this follows its existing rules rather than extending versioning to anything new
(`LES-001`).

**Provenance, which is the honesty claim.** The histogram is `observed`; **the classification built
on it is `imputed`** — nobody observed "this is a business-hours host", only when it was busy, the
same posture `role` already carries. The seed is `defaulted`: it is a choice, not a measurement.
`intensity` is `observed` and **emitted with a caveat naming that no axRange driver reads it**, so it
is never mistaken for something that changed the twin — a withheld measurement being
indistinguishable downstream from one never taken.

**The schedule classification is a claim about SHAPE, not about the clock**, because a capture
carries no timezone and nothing in traffic declares one. `business-hours` means activity is
concentrated rather than flat; the histogram is stored in **UTC and labeled as such** rather than
inventing a local alignment (`LES-006`). `cast` discloses that axRange's own gate uses **its** clock,
so a twin in another timezone shifts the band — a fidelity caveat, not a correctness one.

**Two evidence floors, and below one a field is `None`, counted, never fabricated** (`LES-006`; `Q34`
is the cautionary tale, where two counters published a confident `0` for five days because they were
structurally incapable of being anything else). A diurnal claim needs the span to cover a full day
twice; rate/occupancy stats need far less. Entities below either floor are counted in `_stats`.

**Verification.** Tier 1 **1728 → 1768** (+40). `scripts/verify_tier.sh` exit 0, all four pipeline
gates. **The axRange acceptance gate was run on real data**: no checked-in script exists for it (step
4 ran it ad hoc), so it was reconstructed and both consumers run in-process — pure, no cluster,
nothing mutating. Against the real 58,563-entity baseline on a **checksummed throwaway copy**
(`ir-v2.duckdb` MD5-verified untouched): `expand_twin_config_raw` → **19 networks / 86 nodes / 0
warnings**, and `parse_traffic_profile` accepts the new fields. A pre-1.3 PIT compiles unchanged,
verified on that same real file.

**What is NOT done, deliberately and by user decision.** Build **step 5** (behavioral drift in
`ir/compare.py`) and **step 6** (measuring and pinning the thresholds against the baseline) are out of
scope; `_BUSY_BAND_MIN=0.60`/`_QUIET_BAND_MAX=0.10` and both floors are **declared, unmeasured
placeholders** labeled as such. Two gaps found by the tester are **deferred to integration testing**:
**`scripts/verify_determinism.py` does not cover this path at all** — it imports the v0
`synth/synthesizer.py` and never `compile_twin`, so tier 2 gives **zero** coverage of the seed
derivation, which this work order's own acceptance criteria had named it for; and **no test
exercises a genuinely pre-1.3 store file**, since both existing version tests write via this build's
own schema and only patch the version string. **A code review was still running when this was
committed and its findings were never folded in.**

**Four design choices were made inside the implementation and are unratified**, surfaced rather than
buried: `ir_capture_profile`'s cross-window pooling rule (median of quantiles, sum for `n`) — which
`BeliefStore.snapshot()`'s own docstring had **explicitly declined to pick**, saying the choice
*"belongs to whoever builds the `tfc` compiler"*; role-level histogram pooling (unweighted mean,
sub-floor members excluded not zero-filled); `intensity`'s normalization population
(assignment-receiving roles only); and the two band thresholds above.

**Amended hours later, same day:** the review finished after this commit. Its findings are recorded
in `docs/WORK_ORDER_traffic_profiles_review_fixes.md` — **four must-fix, two measured on the real
baseline**, and by user decision they are **recorded, not fixed here**. The headline:
`ir/materialize.py::_entity_traffic` counts `windows_active` by ROW rather than by distinct window,
and `node_features` is pre-merge, so **42 of 58,577 entities publish occupancy above 100%** (worst
418 of 241 windows) — which also means D4's evidence floors are crossed by row count, defeating them
for exactly the resolved hosts they matter most for. The fourth instance of `LES-114`, in a function
whose own docstring cites `LES-114`. Also: `ir/merge.py` stamps `1.3` while emitting **6 of 8**
tables, `intensity` is stamped `observed` when its denominator is compiler policy, and
`classify_schedule` returns bare literals rather than drawing from `SCHEDULE_CLASSES` — the very
"two copies of one fact" hazard this work order's D1 names.

---

## 2026-08-26 — the store's fail-loud guard cannot see inside a JSON column, and two counters are deleted rather than renamed

**Authority:** implementation (Claude Code) — `Q34` closed, by the option the question did not
anticipate. **Supersedes:** nothing. Contains **one wording change to `docs/DESIGN_BRIEF_aximprint.md`
made under a stated reading, flagged below for the user to reverse in a single edit.**

**The finding, which is worth more than the fix.** `BeliefStore._check_schema_matches_this_build` is
this project's fail-loud answer to "an old store disagrees with this build" — it refuses, it does not
migrate, because `LES-001` forbids a compatibility shim on an incubating schema. Asked whether it
would catch this change, the answer is **it does not, and it cannot**. The guard compares
`information_schema.columns` against `_SCHEMA`, generically over every table — and is therefore
**structurally blind to every key inside a JSON column**. Three of those columns carry the richest
and fastest-moving surfaces in the store: `node_features.features` (the whole per-entity bundle),
`window_health.health` (the whole health record), and `entity_roster`'s four
(`identifiers`/`addresses`/`evidence`/`merged_from`). Nothing inside them is policed by anything.
"The store fails loud on a schema change" is true of columns and false of blob keys, and the second
is where most change in this project happens. Recorded as `LES-131`, and qualified in `CLAUDE.md`'s
Belief store row so the guard is not read as coverage it does not give.

**That finding is what settled the fix.** `s0_count`/`rej_count` live inside
`node_features.features`, so a **rename** would have produced a store holding `s0_count` rows written
under Zeek semantics *and* `attempted_count` rows written under normalized semantics, in one table,
distinguishable only by `window_start` against a commit date nobody recorded — with the guard that
exists to prevent exactly that mixture unable to see either key. **Deletion** leaves an old store's
`s0_count` **silently absent** from new windows: not loud, but unambiguous, and the best outcome
available. So the rule the lesson carries is *prefer removal, or a name that cannot collide* — never
rename-with-new-semantics inside a blob.

**What changed:** `s0_count` and `rej_count` are **deleted** from the window node bundle
(`src/aximprint/windowing/windows.py` — `NodeAcc.s0`/`.rej`, the two lines in `_add_flow`, and the
two keys in `_close`'s emitted dict). Nothing replaced them.

**Why:** they gated on `ev.state` — the sensor-native, **un-normalized** token — against Zeek's
verbatim `"S0"`/`"REJ"`, while the line immediately above them used `ev.state_class`, the normalized
value, for `state_mix`. On every non-Zeek sensor the two counters were therefore structurally `0`,
and a confident `0` reads as *observed none*, not *not measured* — `LES-006` exactly inverted.
**Measured: `axquery` published `s0_count: 0` beside 2,037 `attempted` flows on every node since
2026-08-20, and `refused` was never emitted on that sensor at all. Five days, unnoticed, until a
different sensor's gate landed.** Deleted rather than repaired because keying them on `state_class`
makes them **exactly** `state_mix["attempted"]` and `state_mix["refused"]` — same row, same dict,
same window — and `features/vocabulary.py` declares `CategoricalField("state_mix", "dict", "count",
closed=FLOW_STATE_CLASSES)`, so both classes are guaranteed index-map columns whether or not a window
observes one, and an absent class is a **proven** zero because `state_mix`'s keys sum to `flows_out`.
A repaired scalar would have been a second name for a number already published in the same row with
nothing checking the two agree (`LES-101`).

**Nothing moved on the Zeek path**, precisely because this is a deletion and not a re-keying: had it
been repaired, `s0_count` would have widened from `S0` alone to `S0 ∪ SH ∪ RSTOS0` and every existing
Zeek value would have shifted. **Consumers:** none in `src/` outside the emitter —
`learners/extractors.py` reads only `tainted`, and `synth/`, `ir/`, `store/`, `features/jsonl.py`
pass the node dict through wholesale. Only two test assertions read the two names.

**`docs/DESIGN_BRIEF_aximprint.md` §5.2 and §5.4 #1 are re-worded to name `state_mix` — and this is
the one judgment call in the entry.** Both named `S0/REJ/SF` scalar hygiene counters, i.e. a field
that no longer exists. This is recorded as **not** a reversal of a ratified decision: the brief asked
for S0/REJ/**SF** hygiene counts, the code only ever delivered **2 of those 3** and only for **one**
sensor, and `state_mix` delivers **all eight** classes for **all three** sensors over a closed
vocabulary — so the capability is **subsumed, not withdrawn**. **If the user instead reads §5.2 as a
commitment to sensor-NATIVE hygiene counters** (Zeek's `S0` as distinct from `SH`/`RSTOS0`, a real
distinction the normalized `attempted` discards) **then this is a scope change and the wording should
be reverted.** §5.2 carries an in-place amendment note saying exactly that, so the reversal is one
edit; `Q34` records the shape that would honor the stricter reading — a per-sensor `state_raw_mix`
beside `state_mix`, declared **not cross-sensor comparable**, never two scalars named for one
sensor's spellings in an artifact every sensor writes.

**`Q34` CLOSED**, with its reopen condition recorded rather than left to memory. The question
anticipated two options — repair, or repair-plus-rename — and asked "which consumer actually reads
these two columns". The measurement produced a third option because the answer was **none**.

**Three lessons.** `LES-131` — a schema guard that compares columns is blind to a JSON blob's keys;
prefer removal or a non-colliding name when changing a key's meaning there. `LES-132` — after a
normalizing layer lands, a per-class census and a raw-keyed convenience scalar cannot both be right:
the scalar is either a duplicate of the census or a sensor-specific fact wearing a sensor-agnostic
name, and `Q34` sat in the second state while every reader treated it as the first. `LES-133` — a
test proving "the number is right" can pass against the broken build when the broken field is a
*different* field; the first cut asserted `state_mix` correctness under a non-Zeek vocabulary and
passed pre-fix. The form that bites is cross-field: **no published field may contradict the census in
the same row**, and that assertion fails pre-fix and would also have caught a repair that silently
moved the Zeek values.

**Verified:** **1,728** tests; `bash scripts/verify_tier.sh` exit 0; `grep -rn
"s0_count\|rej_count" src/` → 0 hits; `python3 scripts/doc-stats.py --check` clean.

**Affected docs:** `docs/OPEN_QUESTIONS.md` (`Q34` closed, original question kept below the ruling;
**`Q38` opened**),
`docs/LESSONS.md` (`LES-131`–`LES-133`; `LES-123`'s axquery extension moved to past tense with the
five days kept as the finding), `docs/DESIGN_BRIEF_aximprint.md` (§5.2, §5.4 #1, plus the flagged
amendment note), `CLAUDE.md` (test/lesson counters, the `Q34` pointer, and the Belief store row's
new statement of the guard's limit), `docs/PIPELINE_MAP.md` (the `axquery` box line and item 3),
`docs/SESSION_STATE.md`, `docs/WORK_ORDER_axrange_integration.md`.

**Affected code:** `src/aximprint/windowing/windows.py` (`NodeAcc`, `WindowManager._add_flow`,
`_close`), `tests/test_windowing_states.py`, `tests/test_smoke.py`. Untouched and worth noting as
untouched: `src/aximprint/store/belief_store.py` — the guard's blindness is **recorded, not fixed**.
**`Q38` is opened for it**, with three costed candidates (leave-and-narrow-the-docstring; a per-column
declared key manifest; or a narrow refusal on declared-and-*removed* keys only). It is a question
rather than a task on purpose: option (2) is exactly the versioning ceremony `LES-001` forbids on an
incubating schema, so it needs ratification — while the status quo is not a neutral default either,
since the guard already makes a completeness claim it cannot honor.

---

## 2026-08-26 — three sensors, one gate: the docs are reconciled onto master and the asymmetry is named

**Authority:** implementation (Claude Code) — a documentation-only reconciliation after a rebase,
plus one new open question the reconciliation exposed. **Supersedes:** nothing; it corrects the
2026-08-25 Track B entry's own counters and ordinals in place, and that entry now carries an
amendment box saying so. No source file changed.

**What changed:** `wip/axrange-integration` was written against `master` at `243f8e7`, when the
project had **2** ingest plugins. The user then merged `wip/axquery-ingest`, which landed a **third**
plugin (`axquery`, 2026-08-20) and moved Zeek's parser table **28/7 → 36/7** (43 = 36 + 7, the
wide-vantage stems). The branch was rebased forward; four conflicts were resolved mechanically and
two of those — `CLAUDE.md`'s `Ingest plugins` row and `docs/SESSION_STATE.md` — were spliced rather
than authored. Both are now re-authored, and the whole doc set was swept for claims a rebase
falsifies without conflicting.

- **`CLAUDE.md`'s `Ingest plugins` row is re-authored as one row describing three sensors.** The
  spliced version repeated its own "transport is separated from semantics" sentence, still claimed
  **28 + 7 = 35** as current, and had lost `axquery` entirely — which `master`'s side had never
  carried either. The row now states the shared discipline once, then each sensor in turn, and makes
  explicit the thing three plugins made visible: **no fall-through anywhere, but the census unit is
  per-sensor and the three are not interchangeable** — a log *stem* for Zeek (so a directory listing
  IS a census), an `event_type` *field* for Suricata (one stream, discriminated inside each line), an
  *endpoint* for axquery. The `28 + 7 = 35` sentence is kept but dated to the day it was true; the
  live figure is `36 + 7 = 43`, and `LES-120` already says why that is a vantage fact rather than a
  correction.
- **The Track B CHANGELOG entry is corrected in place, with an amendment box.** Its title said "a
  second sensor" (Suricata is the third), its body said "**2** ingest plugins", and its counters read
  `1519 → 1667` against a base that had moved. Restated: **1574 → 1722**, the same **148** tests. Its
  `**Why:**` is re-argued rather than renumbered — `axquery` was already a second witness to the
  event model's sensor-agnostic claim, and a good one *because* it is structurally unlike Zeek;
  Suricata's contribution is being the first sensor that reads the **same plane**, which is what
  makes an automated equivalence gate constructible at all. A missing `---` separator between the two
  2026-08-25 entries was restored.
- **The two 2026-08-25 entries do not contradict each other.** `master`'s entry rules that
  `wip/axquery-ingest` merges unsplit and that new arcs get their own branch; this branch is exactly
  that convention being followed. The only collisions were counters and ordinals, all listed above.

**`Q37` opened, and it is the substantive output of this pass.** The tier-2 acceptance gate compares
**Zeek vs Suricata** and nothing else. `axquery` has no automated equivalence check — what it got was
a hand-tabulated twin comparison on 2026-08-20, on captures **49.6 days apart**, which is a
measurement and not a gate: it does not re-run and it cannot fail. Whether every registered sensor
owes a ground-truth-anchored gate, or only sensors that read the same plane, is unruled. **The
evidence already argues it is not bookkeeping:** `Q34`'s amendment records that `axquery` has been
publishing a confident `s0_count: 0` / `rej_count: 0` on every node since 2026-08-20 — `windowing/`
keys those counters on the Zeek-verbatim strings `"S0"`/`"REJ"` — and **nobody noticed for five days**,
until a *different* sensor's gate went in. A second implementation creates the exposure; only a
comparison converts it into a finding, and the ungated sensor is the one structurally least like the
others. `LES-123` is extended with that second half rather than duplicated.

**`LES-130`, on the hazard the rebase itself was.** Every falsified sentence in this pass was an
**English ordinal** — "the second sensor", "applied to a second sensor", "one witness" — and
`scripts/doc-stats.py --check` was **clean the entire time**, before and after, because it polices
numeric counters it can compute (`N pytest tests`, `N ingest plugins`, `N recorded lessons`) and an
ordinal spelled in words is invisible to it. The mechanical gate made doc drift *look* covered. Write
the identity, not the position; date a position where the position is the point.

**Also swept:** `Q32`'s "cross-branch, the tables live on `wip/axquery-ingest`" caveat is spent — that
branch merged, so parser and tables now sit in one tree and the row lands beside the parser that
motivates it. `Q36` gains axquery's **third** `service` vocabulary (`dns`/`tls`/`ssh`/`other`), which
matters to its option (2): an explicit `other` label is not an absent label, and under `LES-006` a
`service_class` table would have to say which it is. `docs/PIPELINE_MAP.md`'s `ingest/` box, CLI
legend, drift table and item 3 are redrawn for three sensors.

**Verified post-rebase, not taken on faith:** tier 1 **1,722 passed**; `bash scripts/verify_tier.sh`
exit 0 (4/4 pipeline gates + `acquire` lint clean); `ingest.base._REGISTRY` holds exactly
`['axquery', 'suricata', 'zeek']`; `acquire.harvest.zeek` holds **36** parsed + **7** skipped = 43;
`suricata.plugin._PARSERS` 18 + `_SKIP` 25 = 43 disjoint; `python3 scripts/doc-stats.py --check`
clean.

**Affected docs:** `CLAUDE.md` (the `Ingest plugins` row re-authored, the `--plugin` prose, the
tier-2 gate's scope, the open-questions pointer, the lesson counter), `docs/CHANGELOG.md` (the
2026-08-25 Track B entry corrected in place + this entry), `docs/OPEN_QUESTIONS.md` (`Q37` opened;
`Q32`, `Q34`, `Q36` amended), `docs/LESSONS.md` (`LES-130` added, `LES-123` extended),
`docs/PIPELINE_MAP.md`, `docs/WORK_ORDER_axrange_integration.md`, `docs/SESSION_STATE.md`.
**Affected code:** none.

---

## 2026-08-25 — a third sensor, and the discovery that an A-vs-B gate can pass while both sides are broken

**Authority:** implementation (Claude Code) — design advanced and recorded here, per CONVENTIONS
§1.1. **One point was ruled by the user:** the first parser table's **breadth**. The work order
said "alert + flow records" in its `Do:` line and "ingests and windows equivalently to a Zeek
capture" in its `Done when:` line, which are not the same table; the user ruled for **parity with
the Zeek table** rather than the narrow pair, folded into this build rather than deferred to a
second dispatch. Everything else below — the transport/semantics split, the kind-spelling reuse,
every decline, the equivalence gate's definition of "equivalent" — is Claude Code's, recorded here
for review.
**Supersedes:** nothing. The Zeek plugin, the event model, `windowing/` and every projection table
are untouched; this change adds a producer beside an existing one and adds a gate leg.

**Amended 2026-08-26** — this entry was written against `master` at `243f8e7`, before
`wip/axquery-ingest` merged. Rebasing it forward moved three things and nothing else: the project
had **2** plugins under it and has **3**, Suricata is the **third** sensor rather than the second
(`axquery` landed 2026-08-20), and the test counters below are restated against the post-rebase
base. Every finding, ruling and measurement in this entry is unaffected — none of them touch the
axquery path.

**What changed:** Track B of `docs/WORK_ORDER_axrange_integration.md` — a **Suricata ingest plugin**
normalizing `eve.json` into the same event model the Zeek plugin produces. `src/aximprint/ingest/
suricata/{__init__,plugin,sources}.py`, registered in `ingest/__init__.py`, so the project now has
**3 ingest plugins** (`zeek`, `axquery`, `suricata`). Tier 1 goes **1574 → 1722** tests (**148**
added, the figure the pre-rebase `1519 → 1667` also carried); `bash scripts/verify_tier.sh` exits
0, re-run post-rebase.

- **Transport is separated from semantics**, mirroring `ingest/zeek/`: `suricata/sources.py`
  locates and decodes (a bare file, or a directory searched non-recursively for `eve.json` and its
  rotation siblings — an unrecognized shape is refused, never guessed at), `suricata/plugin.py`
  owns the parser table, ordering and merge. **The stem analogue is `event_type`, not a filename**:
  Suricata puts every record type in ONE stream and discriminates on a field inside each line,
  which is why a directory *listing* can never be a census here the way it is for Zeek.
- **No fall-through** — `Q24`'s rule applied to a third sensor. `_PARSERS` (18) and `_SKIP` (25)
  are asserted **disjoint at import**, and together name **43** entries covering all **35** of
  Suricata's own `eve-log: types:` list (the extra 8 are alternate spellings such as
  `files`/`fileinfo`, or schema objects a stock build never emits). An `event_type` in neither is
  *reported* as unrecognized, never silently dropped.
- **Kind spellings deliberately reuse Zeek's** — `tls`→`ssl`, `krb5`→`kerberos`, `dcerpc`→`dce_rpc`,
  `ldap`→`ldap_op` — so `windowing.windows.SERVICE_PROJECTIONS` / `IDENTITY_PROJECTIONS` /
  `SENSOR_HEALTH_AGGREGATION` behavior is **inherited rather than re-declared**. A parallel
  vocabulary would have landed silently in an `unmodelled_*_kinds` counter instead.
- **`FlowEvent.service` is deliberately NOT normalized across sensors** (Zeek says `krb`, Suricata's
  `app_proto` says `krb5`): it is each sensor's own open L7 vocabulary, and the gate asserts the
  divergence as **intentional** rather than tolerating it. `Q36` records the question.
- **`_stats` declines every counter, including `capture.kernel_drops`**: Suricata's counters are
  cumulative-since-start unless the deployment set `deltas: yes`, and one JSON record cannot prove
  which mode produced it, so `pkts_dropped` would be unprovable. A Suricata run therefore reports
  `None` — "not observed", never "no drops" (`LES-006`). The one-measurement path back is `Q35`.
- **Each plugin declares its own reorder horizon** (`LES-119`). The sensor-neutral machinery —
  `reorder_by_key` / `in_order` / `is_ip` — was hoisted out of `zeek/sources.py` into a new
  `src/aximprint/ingest/reorder.py` sitting **below** every plugin (the `provenance.py`/`vantage.py`
  precedent), so `suricata` no longer depends on `zeek` staying importable for a helper about
  *time*; `zeek/sources.py` re-exports it, so nothing that already read it there changed. The
  **constants did not move**: Zeek's 600 s horizon is a measurement of one sensor's write behavior
  (169.6 s observed displacement), and Suricata's is honestly labeled **unmeasured**.
- **`IngestPlugin.events`'s contract was corrected, not extended**: it said a plugin that cannot
  honor a `--from`/`--to` slice "may accept and ignore both". Both implementations already
  **refuse**, and they are right (CONVENTIONS §3) — the doc had drifted from the code, the `LES-005`
  shape one level up from a signature.
- **A fourth tier-2 gate leg.** `scripts/verify_pipeline_gates.sh` now runs **4** steps, not 3:
  step 4 is **sensor equivalence** — one seeded traffic plan rendered as Zeek logs *and* as
  `eve.json`, both through the real plugins and the real `WindowManager`
  (`scripts/verify_suricata_equivalence.py`), plus `verify_determinism.py --plugin suricata` for the
  per-plugin determinism half. Steps 3 and 4 are different null tests and neither substitutes for
  the other: 3 varies the **transport** under one sensor and demands byte-identical features; 4
  varies the **sensor** and demands structural equivalence, which is the most two sensors can be.

**Why:** seam ① of the axRange integration needs a sensor that can be held *against* Zeek before
the event model's claim to be sensor-agnostic means anything. `axquery` (2026-08-20) was already a
second witness, and a valuable one precisely because it is structurally unlike Zeek — but that is
also why it cannot be gated against Zeek: it reads an appliance's already-inferred entity database
over HTTP, not a sensor's records off disk, so there is no single seeded input that can be rendered
into both. Suricata is the first sensor that reads the **same plane** as Zeek, which is what makes
an automated equivalence gate constructible at all. Whether the sensor that *cannot* take this gate
therefore owes a different one is `Q37`, opened 2026-08-26 and deliberately not answered here.

**The finding worth more than the plugin: the equivalence gate passed while both sensors were
broken identically.** The Zeek fixture wrote `ws000$@CORP.AXR.LAB`; the Suricata plugin wrote the
same; `windowing.entities.kerberos_identifier` splits an RFC 4120 principal on `/`, so both
resolved at **`user`** scope, anchored nothing, and **agreed** — "identical bindings (20)", a tick.
Agreement-in-brokenness read as equivalence. Widening the comparison key from the raw string to
`(ip, raw, scope, namespace)` made it **visible**; only a **positive assertion** — dhcp and kerberos
bindings must be `host`-scoped — made it **fail**. That is the general rule now recorded as
`LES-123`: two implementations over one shared lower layer agree perfectly whenever that layer
regresses, so every axis of an A-vs-B gate needs at least one assertion anchored to shared ground
truth (here, the seeded plan). Mutation-proven: widening `events.FLOW_ACCEPTED` to include
`refused` now fails the gate with both sensors inventing `23/tcp` on 20 hosts **while remaining
perfectly equal to each other**. Every axis of the gate now carries a plan-derived expectation, and
three of them (`listen_ports`, `state_mix`, `__longtail__`) are derived from the plan's own wire
outcomes rather than through the product tables they check — deriving `listen_ports` through
`FLOW_ACCEPTED` would make the expectation grow exactly when the bug does.

**The second finding, and it indicts a guard this project would otherwise have trusted.**
`_EXPECTED_ATTRS` was built to catch a wrong field spelling, and shipped as an **ANY-gate** — it
fired only when an emitted event carried **none** of its kind's declared keys. Measured against the
six real spelling defects that motivated it, it caught **0 of 6**: any one correct sibling key kept
the event out of the count. Fixed with a per-`(kind, declared key)` "ever populated this run" tally
(`declared_attrs_seen`), reported by name in the closing summary. `LES-125`.

**Six field-spelling defects, and confidence was uncorrelated with correctness.** Re-resolving all
43 dotted paths against Suricata's own `etc/schema.json` — the vendor's machine-readable artifact,
not recollection — found **40 of 43 correct, including every path the author had flagged
low-confidence**, and all six defects in fields nobody doubted: `ldap.response.result_code` and
`sip.reason_phrase` do not exist, `sip.user_agent` does not exist at all, `smtp.rcpt_to` is an
array (silently mangled), `mdns`/`llmnr`/`pop3`/`doh2` were in **neither** table (the
no-fall-through discipline failing by *omission*), and **`arp` had been declined on a false
premise** — "vanilla Suricata emits no `arp` event_type" — contradicted by the schema. That last
one cost the plugin its single highest-value record type for the entity resolver, since mac↔ip is
the host-scoped evidence `Q1` D1 makes the only class that may merge two addresses. `LES-124`.

**Four more defects, each its own class.** (1) A 500-row file carrying no `event_type`, and a file
that is not JSON at all, both **drained** perfectly and published `unparsed_log_types: []` — which
per `LES-006` positively means *audited, nothing left over*. An unreadable source was
indistinguishable downstream from full coverage; the census now requires **two** conditions (read
to exhaustion **and** something to take a census over) and refuses to publish otherwise (`LES-126`).
(2) One `if ts_flags is not None and tc_flags is not None:` wrapped **four independent** TCP-flag
readings, so one missing byte disabled all four and fell through to `established` — inside
`FLOW_ACCEPTED` — re-arming the phantom-listener bug a previous round had already removed. Each
reading is now gated on exactly the byte it reads, and the one reading that genuinely needs both
degrades toward `refused`, the *under*-claiming direction (`LES-127`). (3) `_FileStats` was
constructed, threaded through two layers and **read by nothing** — and its `.capped` field means
"the reorder horizon was NOT honored", which `LES-004` requires be disclosed (`LES-128`). (4) A
docstring claimed scoping `arp` as host evidence was "a one-line change in `IDENTITY_SCOPES`".
Proxy ARP puts a router's MAC beside someone else's address and VRRP/HSRP share one virtual MAC
across a group, so host-scoped ARP without a many-addresses-per-MAC guard collapses N hosts into
one entity — **forward-only and irreversible** under `Q1` D2. The claim was a safety claim wearing
a convenience claim's clothes (`LES-129`); the question is `Q32`.

**ARP's honest current state is asserted, with a trip-wire.** The parser produces the evidence;
`IDENTITY_SCOPES` has no `arp` row; so the resolver **counts** it (`unscoped_identity_kinds`) and
binds nothing. The gate asserts exactly that three-part state and **fails loudly the day a scope
row lands**, because on that day ARP stops being a named divergence and becomes an identity axis
that must be compared like `dhcp`/`kerberos` — with the awkward consequence, which is why it must
not pass silently, that Suricata would then anchor hosts Zeek structurally cannot.

**Lint, declined deliberately and correctly.** `ruff check src/aximprint/ingest/` reports **22**
findings, **none in new code** — all pre-date this branch, and the branch's net effect is **−2**.
Reaching clean requires an `I001` autofix that **reorders the plugin-registration imports** in
`ingest/__init__.py`, whose order is the registration order. CLAUDE.md already scopes whole-repo
lint to a separate work order and the tier-2 lint gate is `acquire`-only by ruling
(`docs/WORK_ORDER_merge_xtract_acquire.md` D7); nothing here changes that.

**Affected docs:** `CLAUDE.md` (test/plugin counters, the `Ingest plugins` row, the CLI prose, the
tier-2 table's three-gates → four-gates claim), `docs/PIPELINE_MAP.md` (the `ingest/` box),
`docs/WORK_ORDER_axrange_integration.md` (Track B state), `docs/LESSONS.md` (`LES-123`–`LES-129`),
`docs/OPEN_QUESTIONS.md` (`Q32`–`Q36` opened), `docs/SESSION_STATE.md`.
**Affected code:** `src/aximprint/ingest/suricata/{__init__,plugin,sources}.py` (new),
`src/aximprint/ingest/reorder.py` (new), `src/aximprint/ingest/__init__.py`,
`src/aximprint/ingest/base.py`, `src/aximprint/ingest/zeek/sources.py` (re-export),
`tests/test_suricata_ingest.py` (new), `scripts/verify_suricata_equivalence.py` (new),
`scripts/verify_determinism.py`, `scripts/verify_pipeline_gates.sh`.

---

## 2026-08-25 — the branch is not split; the naming drift is fixed forward, not backward

**Authority:** **user** — ruling on a question `docs/SESSION_STATE.md` carried as open (*"Split the
branch before merge? It holds two unrelated bodies of work; the name covers one"*). Analysis:
Claude Code. **Supersedes:** nothing; no source changed.

**`wip/axquery-ingest` merges as it stands.** The handoff's premise was wrong in the direction that
mattered: the branch holds **three** bodies of work, not two — axquery ingest (`55ebb82`,
`05a8eac`, `cd7a7af`), the Zeek wide-vantage stems (`0bb2ffe`, `2e0614f`, `ecddfd4`, `bfe6347`,
`9fb4b9c`) and the evidence plane (`8550f2f`, `047d1d8`) — plus the ML work order's docs riding
inside `1030ce7`. A plan built on "two" would have split along a seam that does not exist.

**Four things make the split mechanically unavailable:**

- **The arcs interleave; they do not stack.** Oldest-first: axquery → both → Zeek x4 →
  three-at-once → axquery → Zeek → IR → axquery → IR. No linear prefix is one arc, so this is
  cherry-pick-and-resolve, not `rebase --onto`.
- **Three commits are individually cross-cutting** and would each have to be split by hand:
  `9728589` touches two work orders, `1030ce7` three, and `8550f2f` mixes the `ir/materialize.py`
  refactor with a `windowing/segments.py` change and an axquery work-order edit.
- **11 of 12 commits touch `docs/CHANGELOG.md` and/or `CLAUDE.md`** — append-narrative files whose
  entries are ordered and cross-referencing. Every reordering conflicts, and every resolution is a
  judgment call about a decision record rather than a mechanical merge.
- **A semantic dependency crosses the proposed seam.** `8550f2f`'s VLAN refusal in `segments.py`
  exists *because* `05a8eac` added `FlowEvent.vlan_src`/`vlan_dst` and proposed a VLAN
  segment-evidence row; the comment cites `docs/WORK_ORDER_axquery_ingest.md` D3 by name and
  refutes it with measurement (**210 public endpoints tagged**, **7/27** private /24s spanning
  multiple VLANs). Split them and the refutation ships without the proposal it refutes.

**And the split buys nothing.** Its three usual justifications all fail here: review already
happened commit by commit (baseline rule 4); nothing is being held back (tier 1 **1,574** green,
tier 2 green, and the evidence plane's incompleteness is by design, with step 1 pinned
byte-identical on the real ten-day store); and revertability is per-commit, which these already are.

**The defect was the branch NAME, not the branch**, so it is fixed forward rather than backward.
New arcs get their own branch from here: `wip/axquery-ingest` closes where it is, and evidence-plane
step 2 starts on `wip/evidence-plane-step2` off it — which stops the drift without rewriting a
single commit. Recorded as baseline rule 3 in `docs/WORKFLOW.md`, so the convention outlives this
entry.

---

## 2026-08-20 — the axquery page size is sized against concurrency, and it is semantic

**Authority:** **user** — *"why is axquery ingest only for 50 mins when the data represents 26
days?"*, which found a real defect. Fix and measurement: Claude Code. **Supersedes:** nothing.

**The honest answer to the question is that 50 minutes was an arbitrary smoke-test window.**
Widening it is what exposed the bug: a **four-hour read failed outright** — correctly refusing
rather than truncating — because `PAGE_ROWS = 500` was sized against RATE when the binding
constraint is **CONCURRENCY**. `after`/`before` match conns that OVERLAP an interval, so as the
interval shrinks the row count converges not to zero but to the number of connections ALIVE at
that instant, a floor bisection cannot get under. Measured at the second that hit it:
**525 overlapping conns, of which only 101 actually start there.**

Raised to **5,000**, ~10x the observed peak. This is **not** the request shape that OOM-killed the
server on 2026-08-19 (unbounded `limit=2000` over 26 days; `limit=5000` against a 1.42 M-row
table): measurement shows `limit=2000` and `limit=8000` return the identical 525 rows on a bounded
one-second window, so a larger cap costs nothing when the window is small. The effect is large —
one hour went from **1,103 requests / 108 s** to **31 requests / 6.2 s** (17x), and four hours
went from failing to 131 requests / 19.9 s.

**`PAGE_ROWS` is a SEMANTIC constant, not a tuning dial**, and only counting caught it: the same
hour yields **61,066** flows at 500 and **61,089** at 5,000. axquery consolidates a connection's
cross-chunk fragments per response and its own docs say a truncated response is *"page semantics
(merge-so-far)"* — a heavily truncated page merges only what it has seen. **Determinism still
holds as this project defines it** (window close is a pure function of events, resolver state and
**code build**; the constant is part of the build, and two runs at one value are byte-identical,
verified) — but changing it changes the DATA, so it must be treated like a parser change. Recorded
at the constant and pinned by two tests, one of which asserts the floor error tells the reader
that **narrowing the range will not help** — the path that already failed.

**Extrapolated cost of the full span:** 624 hours of capture is ~**52 minutes** of API reads and
~38 M flows. Reads are cheap; windowing and store writes will dominate. Tier 1 **1,574**.

---

## 2026-08-20 — two sensors, one network: the twins agree on topology and invert on provenance

**Authority:** **user** — *"compare the twin generated using zeek data vs axquery data in detail"*.
Measurement: Claude Code. **Supersedes:** nothing; no source changed.

**Three twins were cast** — axquery 50 m, Zeek 1 h, Zeek 10 d — with the one-hour Zeek PIT
materialized specifically so the sensor difference is not swamped by 288x more data. Every number
below is from those runs.

| | axquery 50m | zeek 1h | zeek 10d |
|---|---|---|---|
| segments / node entries / hosts | 8 / 10 / 14 | 11 / 17 / 26 | 19 / 35 / 86 |
| conduits / tfc assignments / provenance fields | 5 / 4 / 67 | 8 / 5 / 111 | 28 / 5 / 237 |
| IR entities (local : external) | 1,112 (232 : 880) | 3,420 (317 : 3,102) | 58,563 (753 : 57,808) |
| placed hosts | **181** | **195** | 430 |
| segment evidence `observed` : `imputed` | **0 : 181** | **195 : 0** | 430 : 0 |
| `ir_identity` / `ir_unseen` | **0 / 0** | 3,526 / 4,814 | 755,567 / 38,252 |
| `key_kind` hostname / mac | **0 / 0** | 54 / 11 | 78 / 18 |

**The evidence classes invert completely.** The two sensors place nearly the same number of hosts
— **181 vs 195** — and every one of axquery's rests on RFC1918 imputation at **0.6**, while every
one of Zeek's is `observed` at **0.95**. That is D4 of `docs/WORK_ORDER_axquery_ingest.md` visible
in the output: Zeek's `local_orig`/`local_resp` carries the operator's own `local_nets`
declaration and axquery declares no equivalent. **The entire axquery twin's topology is imputed.**

**And yet it cross-validates.** **All 8 of axquery's segments appear in the Zeek ten-day twin**;
6 of 8 appear in the span-matched hour, and the two that do not (`10.80.34.0/24`, `10.80.78.0/24`)
appear in the ten-day one. Two independent sensors, on captures **49.6 days apart**, agree on the
topology. So the RFC1918 imputation is **weak in provenance and right in substance** — which is
precisely the distinction `evidence_class` exists to carry, and the first time this project has
been able to check it against a second sensor.

**Three capabilities are absent from axquery, all traceable to one decision.** No identity
(`ir_identity` 0; every entity address-keyed, so cross-PIT drift claims are impossible), no
referenced-but-unseen census (0 rows, so DESIGN_BRIEF §8's visibility model has no input), and no
`dns-resolver` role (Zeek finds 20 in a single hour). All three follow from declining
`/api/entities`, `/api/devices` and `/api/identity/users` — endpoints that neither page nor accept
a time bound, whose reachable prefix is ascending-address-ordered and therefore biased.

**Two smaller findings.** Zeek yields **54 hostnames in ONE HOUR against 78 in ten days** — the
48x identity burstiness from a new angle, and a caution against assuming longer captures buy
proportionally more identity. And **`Q31`'s role leakage appears identically in both** twins
(`web-tier` dominating on internet peers), which confirms it lives in the role classifier rather
than in either sensor.

**What would close the gap, and neither is a build.** An operator-declared `local_nets` equivalent
for axquery would flip 181 hosts from `imputed` 0.6 to `observed`; re-admitting the two census
endpoints would restore roles and the unseen census at the cost of a knowingly biased sample.
Both are decisions. Still unrun for **any** twin: axRange's expander validation, the real
acceptance gate, which is harness-gated.

---

## 2026-08-20 — the wide-vantage work order closes, and a re-ingest reproduces ten days to the row

**Authority:** **user** — *"run step 5 in the background"*. Execution and measurement: Claude Code.
**Supersedes:** nothing. `docs/WORK_ORDER_zeek_stems_wide_vantage.md` is **COMPLETE** — all five
build steps landed and verified at full scale.

**The full re-ingest ran**: all 1,440 windows, **3 h 24 m** ingest + **51 m** materialize, both
clean, into `beliefs-v2.duckdb`/`ir-v2.duckdb` so the 2026-08-18 baseline survives as the
comparison rather than being overwritten.

**`unrecognized_log_types` went from eight stems to `{}`** — the headline acceptance criterion,
confirmed across every window rather than a six-window sample. Assertions rose **+541**:
`serves_known_cert` 245, `serves_mqtt` 137, `serves_rdp` 78, `serves_syslog` 72, `serves_rfb` 8,
`serves_websocket` 1. That is exactly the sum; **no other table moved by a single row** —
node_features, edge_features, identity_bindings, profile_facts, window_health and all five IR
tables are identical to the baseline.

**The withdrawn acceptance criterion was right to withdraw.** `entity_roster` held at **105** and
`ir_entity.key_kind` at **58,467 address / 78 hostname / 18 mac**, unchanged to the row — exactly
as predicted once `radius`'s `mac`/`framed_addr` measured 0/22 and `rdp`'s `client_name` 0/116.
Had "host-named entities and strong-keyed IR entities both rise" still stood, a correct build
would have reported failure.

**An unplanned determinism result, and the strongest this project has.** A re-ingest across a code
change reproduced 2.59 M assertions and every other table byte-for-byte except the 541 intended
additions. Tier 2 exercises that invariant twice over a small fixture; this is the same claim at
1,440 windows and ten days of real capture.

**Honest read on yield: 541 new assertions against 2.59 M is 0.02%.** The eight stems were
admitted for coverage and honesty — nothing unaccounted for, no field refused — and delivered
exactly that. They were *argued for* on identity grounds and delivered nothing there, both sources
having measured empty. `serves_known_cert` at 245 is the real gain: the host-attributed
certificate fact that `x509` structurally could not produce.

---

## 2026-08-20 — a second sensor ingests, and no endpoint on it pages

**Authority:** **user** — *"get started on the work orders"*, having ruled the four blocking
decisions. Implementation, measurement and the D3/D4/D6/D7 rulings (delegated, none gated):
Claude Code. **Supersedes:** nothing. `docs/WORK_ORDER_axquery_ingest.md` build step 2 is landed;
steps 3-5 are **declined on measurement**.

**aximprint has a second ingest plugin.** `axquery` — the first exercise of DESIGN_BRIEF §0.1's
per-sensor plugin contract by anything other than Zeek. `aximprint ingest --plugin axquery
--source axquery:http://127.0.0.1:19090 --from ... --to ...` produced a belief store (2 windows,
50,796 flows, 4,606 assertions) and `materialize` turned it into a PIT — **1,112 entities, 38
segments, 1,558 edges**, vantage `axellio-hq/corp-apcn-rdr1:12`. 29 tests; tier 1 **1,566**;
tier 2 green.

**No endpoint on this server pages, and that shaped the whole build.** `/api/connections`,
`/api/entities`, `/api/devices` and `/api/identity/users` all ignore `offset` and always return
`nextOffset: null` — D5's proposed cursor paging does not exist. So `/api/connections` is read by
**time-bisection**: a full page is evidence of truncation and the interval halves, keeping peak
server memory at one page however dense the traffic. That matters because walking `limit` up is
what **OOM-killed this server** on 2026-08-19. The gain is not marginal — one 50-minute window
returns **500** rows to a naive request and **50,796** to a bisected one.

**`after`/`before` select connections that OVERLAP a range, not ones that start in it.** Right for
a connection database, fatal to naive bisection: before the fix one window yielded 202,961 rows
carrying 50,887 identities (**4x duplication**) with timestamps reaching **14.9 days** outside the
requested slice. Each leaf now keeps only rows whose own start falls in that leaf, so leaves
partition exactly and **no dedup set is needed** — the read stays streaming and O(1) in memory,
which a global identity set over a 26-day span would not be. Assigning a flow by its start also
matches Zeek, where `conn.log`'s `ts` is the connection's start.

**Three endpoints are declined by name, on measurement.** `/api/entities` (1.42 M rows),
`/api/devices` and `/api/identity/users` neither page nor accept a time bound, so the reachable
prefix is **ascending-address-ordered** — a systematically biased sample presented as a census.
`/api/identity/users` is worse: 500 rows carry **41** distinct account names and `totalUsers`
grows with `limit` (22 -> 67 -> **505**), so it is un-consolidated *and* its total is a
discovered-so-far figure like `totalMatches`. **No `total*` field on this API is a total.**

**`FlowEvent` gains `vlan_src`/`vlan_dst` — two fields, not one** (D3). axquery carries
per-direction tags and states why in its own source (*"asymmetric VLAN tagging happens (capture
point sees one side trunked, the other access), so a single connection-level VLAN was lossy"*);
its REST `vlanId` is the lossy orig-side projection, and collapsing the pair here would rebuild
that loss inside aximprint. `0` means *not observed tagged* and becomes `None`, never VLAN 0
(`LES-006`). **35,778 of 50,796 flows carry a tag.** Not yet consumed — `windowing/segments.py`
still imputes locality from address shape at 0.6 and does not read these fields.

**`state_class` is derived from the TCP flag bytes** (D2), decoding all **eight** bits since ECN
pairs like `219/91` are real. Measured on the live window: established 5,431, reset_orig 4,945,
reset_resp 1,907, attempted 2,037, other 318, partial 31 — and 36,127 `unknown`, every one UDP or
ICMP, counted rather than bucketed into a neighbour.

Also ruled and built: **D4** — axquery declares no locality, so `local_src`/`local_dst` stay
`None` and all 38 segments came from the RFC1918 fallback at `imputed` 0.6, visibly. **D6** — the
`axquery:<base-url>` locator refuses a bare host rather than defaulting a scheme, and
`probe_vantage()` answers from `/api/info` before any event, the first plugin able to do so
without a harvest manifest; **the two sensors' vantage strings still disagree**, so their PITs
remain incommensurable until an operator declares them equal. **D7** — one timestamp normalizer
over three representations (split sec/nsec, ISO-8601, nanoseconds), failing loud on a fourth.
`urllib` rather than `requests`, so the optional `[acquire]` extra does not become mandatory for
a discovery-side plugin.

---

## 2026-08-20 — the snapshot boundary moves to the evidence plane, and the thin-dependency floor is raised

**Authority:** **user**, four rulings taken together — *"rule D8 and radius split together"* earlier,
then on being asked for every blocking decision at once: **approve the full evidence-plane
reversal**, **Tier A as a core dependency**, **axquery measurements observed / conclusions
imputed**, and **start with the axquery ingest build**.
**Supersedes:** the **snapshot boundary** chosen by `docs/WORK_ORDER_network_ir.md` **D1–D9,
ratified by the user 2026-08-14**. The IR **schema** (`docs/SPEC_network_ir.md`, `1.2`), its
versioning, `cast`, `drift`, `visibility` and `merge --axis vantage` are **not** superseded. This
is the project's second reversal of a ratified decision, and the first to move a boundary set by
its only §0.2 schema ratification.

**1. The PIT snapshot boundary moves into the evidence plane — the full reversal, D1–D5 as
drafted.** The measurement that forced it, from the ten-day capture: a host-named entity is
**active** in a median of **124** windows and **named** in a median of **2** — a **48×** ratio,
with 70% named in three windows or fewer. Slicing into per-window PITs strands the name in ~2
slices while the host appears in ~122 others as a bare address, and `merge` cannot re-key what was
already keyed (`LES-118`). Evidence composes; conclusions do not. The durable artifact becomes the
**evidence PIT**; the IR stays *persistable* and versioned (external consumers still read it, so
`LES-001`'s discriminator is unchanged) but is no longer the snapshot unit; `merge --axis time`
moves to the evidence plane while `--axis vantage` stays at IR level. `W_snap` must be >= `W_ingest`
and a snapshot finer than the store's own windowing is **refused, not rounded**. Not yet started.

**2. Tier A becomes a CORE runtime dependency, not an extra.** `numpy`/`scipy`/`scikit-learn` join
`duckdb`/`pyyaml`. The `[ml]`-extra option — mirroring how `requests` sits in `[acquire]` — was
offered and **declined**: the ML path is to be part of the product, not an opt-in. Consequence to
honour when the ML work order builds: a bare `pip install -e .` carries ~100 MB it may never use,
and the "deliberately thin" note beside the dependency list in `CLAUDE.md` and the design brief
must be updated rather than left contradicting the manifest.

**3. axquery's measurements are `observed`; its conclusions are `imputed`.** The carve-out on
D1, ruled as proposed. `/api/connections`, `/api/events`, `/api/entities.apps`, extents and the
coverage block are `observed`; `/api/devices.roles` is `imputed` with `source: axquery`. This
stops a foreign system's conclusion from carrying a stronger evidence class than aximprint's own
conclusion from the same kind of behaviour — which is exactly what `catalog/roles.yaml`'s
"`has_role` is `imputed`, never `observed`" exists to prevent.

**4. The axquery ingest build starts**, ahead of the evidence-plane refactor and the ML layer.
D3/D4/D6/D7 of that work order are ruled by Claude Code as delegated — event-model and transport
calls, none of them gated. No source changed in this entry; tier 1 **1,537** unchanged.

---

## 2026-08-20 — 43 = 36 + 7: every stem a twelve-stream vantage produces is named

**Authority:** **user** — *"build steps 2-5"*, against `docs/WORK_ORDER_zeek_stems_wide_vantage.md`
(D1–D3 ruled the same day). Implementation, measurement and one self-correction: Claude Code.
**Supersedes:** nothing ratified. **Build steps 1–4 are landed; step 5 is not** (see below).

**Seven stems admitted, closing the wide-vantage gap.** `known_certs` as its own `known_*`-shaped
parser (R4 — it keys on `host`/`port_num`, which the plugin's module comment already excludes from
`_responder_service`), and `rdp`/`rfb`/`redis`/`websocket`/`syslog`/`mqtt_connect` through the
shared helper. **36 parsed + 7 skipped = 43.** 18 tests across the round; tier 1 **1,537** green;
tier 2 green.

**Every projection tuple was chosen from measured population**, not from the work order's field
list — the correction made earlier the same day. `known_cert` `subject`/`issuer_subject` 649/649,
`syslog` `facility`/`severity` 350/350, `rdp` `security_protocol` 111/116, `mqtt`
`connect_status` 511/511, `websocket` `server_extensions` 1/1, `rfb` the 8/8 server version pair
carrying the 3/8 `authentication_method`.

**`redis` is declined by name, and reading the code is what forced it.** The revision written
hours earlier said "project nothing" and argued a bare `serves_redis` with an empty key would
still be a true fact. `_add_service`'s `if not parts: return` disproves it: an empty projection
tuple silently produces **nothing** while still claiming a projection exists. It moved to
`_SERVICE_NOT_PROJECTED` with the measurement as its reason, and a test now forbids an empty tuple
anywhere in `SERVICE_PROJECTIONS`.

**Two rules that had been caught twice by eye are now tests.** No responder-keyed projection may
name a client-emitted field (`R1`'s `smtp`/`sip` reversal, then R6's `rdp`/`websocket`/`mqtt`), and
no projection tuple may be empty. Both were review findings that no mechanism enforced.

**`rdp` emits no `IdentityEvent`, by decision rather than by data.** `client_name` is collected
and never minted even when populated; `cookie` likewise — 53 of 105 real values are opaque
`@@`-prefixed tokens and 10 are `MS-MGLN`, a product string, so minting it would fill the user
census with non-users.

**Verified on six real windows** of the ten-day cold tier through the full chain — 383,489 rows ->
423,928 events across 33 log types, **`unrecognized_stems` `{}` and `unrecognized_log_types` `[]`
on every window** (the headline acceptance, met on real data). Five of seven new predicates fire:
`serves_known_cert` 15, `serves_mqtt` 32, `serves_rdp` 10, `serves_syslog` 8, `serves_rfb` 3.
`serves_websocket` is 0 because that stem appears in 1 window of 1,440; `bound_radius` 0 as
measured; `serves_redis` 0 by decline.

**Step 5 is deliberately not done.** Re-ingesting the full ten-day tier is ~3 h 22 m plus ~50 m to
materialize, and `aximprint ingest` is harness-gated. Its stated purpose was re-measuring
`entity_roster` and `ir_entity.key_kind`, which the withdrawn acceptance criterion covered; what
it would still buy is the service censuses at scale and a refreshed store and PIT.

---

## 2026-08-20 — measured before building, and it corrected the ruling made four hours earlier

**Authority:** **user** — *"measure rdp's client_name before step 2"*. Measurement and the
revision it forced: Claude Code. **Supersedes:** nothing ratified. It **revises the R5/R6 table
ruled earlier today in the same work order**, and **withdraws an acceptance criterion**.

**`rdp` contributes no identity, and structurally cannot on this network.** `client_name` is
**0/116**, along with the whole client info block. Not a sampling artifact: `result` is
`encrypted` on **111/116** at `security_protocol=HYBRID_EX` (CredSSP/NLA) and the other **5** are
`HYBRID_REQUIRED_BY_SERVER`. The client info block is sent after the handshake goes encrypted, so
an NLA-enforcing deployment never exposes it in cleartext. The work order's *"`rdp` carries
`client_name` from a THIRD independent source alongside NTLM and Kerberos"* describes a field
modern RDP does not show. **Build step 2's `IdentityEvent` half is cancelled.** The one
identity-bearing field is `cookie` (**105/116**, the `mstshash` token) — USER-scoped, so it never
merges (D1), and **truncated**: every value is <=10 chars, 92/105 exactly 9 (`david.gus`,
`neal.eide`, `AzureAD\m`).

**Having been bitten three times, the remaining five stems were measured too — and that caught
four bad rows in a table ruled earlier the same day.** The R5/R6 projections had been chosen from
the work order's "fields it carries" column, which is a SCHEMA listing; three of the seven named a
field that is empty on the entire ten-day capture. `mqtt_connect`'s `proto_version` **0/511** ->
`connect_status` **511/511** (and it is the responder's answer, which a responder-keyed projection
should have carried anyway); `websocket`'s `subprotocol` **0/1** -> `server_extensions` **1/1**;
`redis`'s `cmd.name` **0/2** -> project nothing, letting a bare `serves_redis` be the fact;
`rfb`'s `authentication_method` is only **3/8**, so the **8/8** server version pair joins it.
`known_certs` (**649/649**) and `syslog` (**350/350**) were right as ruled.

**An always-empty projection field would not have failed anything** — `_attrs()`'s positive gate
keeps it out and the assertion degrades to a bare `serves_X`. It is a content failure, not a
correctness one, which is precisely why it survived a ruling and why only measurement finds it.

**An acceptance criterion is withdrawn.** *"Host-named entities and strong-keyed IR entities both
rise"* cannot be met: measurement killed both of this work order's identity sources. A criterion
that cannot be satisfied is worse than none. What remains is real — seven service censuses and
eight stems off the unrecognized list. **Lesson candidate, now three-for-three (`snmp`'s
`display_string`, `radius`'s `mac`/`framed_addr`, `rdp`'s `client_name`): a work order's field
list is a schema until someone counts it, and a ruling that reads one inherits its errors.**
No source changed; tier 1 **1,531** unchanged.

---

## 2026-08-20 — `radius` parses, and the field that justified it is empty on every row

**Authority:** **user** — *"build step 1"*, against `docs/WORK_ORDER_zeek_stems_wide_vantage.md`
(D1 ruled earlier the same day). Implementation and measurement: Claude Code.
**Supersedes:** nothing ratified; it **falsifies a finding in that work order's own draft.**

**Landed.** `radius` -> `IdentityEvent`, subject `framed_addr` per R2, with `_identifiers_radius`
splitting the row's two scopes (`mac` -> HOST, `username` -> USER unless the AD machine-account
conventions say otherwise) and `IDENTITY_PROJECTIONS["radius"] = ("bound_radius", 0.9)` — 0.9 with
`dhcp`, infrastructure asserting an assignment it controls. **29 log types parsed, 7 skipped.**
12 tests; tier 1 **1,531** green; tier 2 green. `radius_username` is its own parse of RFC 4282 NAI
plus Microsoft down-level names and deliberately does **not** reuse `kerberos_identifier`: the two
grammars differ, and running one over the other's strings is the shape-guessing `LES-008` forbids.

**Five places mirror the Zeek stem tables; three drift guards caught the ones missed.**
`_PARSERS`, `_COLUMNS`, `acquire/harvest/zeek.py::KNOWN_PARSED_STEMS`, that module's `_SCHEMAS`
fake-runner header, and `docs/SPEC_zeek_metadata_archive.md`'s column table. None was findable by
reading the work order — the tests are what made the set complete. The `_SCHEMAS` header was
transcribed from the ten-day cold tier and verified byte-identical across all 17 windows that
produced the stem, per the discipline `LES-087`/`LES-090` set.

**The step's premise is false on this capture.** All 22 rows in all 17 windows: `mac` **0/22**,
`framed_addr` **0/22**, `result` `unknown` **22/22**, `username` **7/22** — six `nessus` from an
internal scanner probing six hosts, one `steve` from a public address. Probe traffic, not
authentications. The work order's finding read *"it carries `mac` and `framed_addr` on one record
— structurally the same evidence a DHCP lease provides"*; **no row carries either**, because that
table's "fields it carries" column listed `radius.log`'s SCHEMA and was read as a measurement.
`radius` supplied **zero** identity evidence here. This is the second admit justified by an
unmeasured field (`snmp`'s `display_string`, `R2`) — **lesson candidate: a work order's field list
is a schema until someone counts it.** `docs/WORK_ORDER_ml_layer.md` leans on the same unmeasured
column when it says to parse `radius`/`rdp` before modelling identity; **`rdp`'s `client_name`
needs measuring before build step 2**, not assuming.

The parser is still right to have: a no-op by construction where there is no subject (reaching
`entity_resolution` D4's safety property from the parser side), it clears `radius` off the
unrecognized list, and it binds the moment a capture carries real authentications.

**A deliberate diagnostic now states a wrong cause, and was left alone.** The plugin's generic
drift tripwire fires *"PRODUCED NO EVENTS from a non-empty log — parser and source schema likely
disagree"* on all 17 windows, where they agree perfectly and the rows simply carry no subject.
Firing is arguably correct; only the stated cause is wrong. Changing shared, intentional behavior
was outside this step — recorded as a follow-up, since the clause collapses exactly `LES-006`'s
"no evidence vs evidence of none".

---

## 2026-08-20 — collect everything, project almost nothing: the eight wide-vantage stems are ruled

**Authority:** **user, delegated** — *"rule D2 and D3 of the stems work order"*. Ruling and the
code reading behind it: Claude Code, same day. **Supersedes:** nothing ratified; it corrects two
framings in the unratified draft. With D1 ruled earlier today,
`docs/WORK_ORDER_zeek_stems_wide_vantage.md` is **fully ruled and ready to build**.

**Admit all seven** (`known_certs`, `rdp`, `rfb`, `redis`, `websocket`, `syslog`, `mqtt_connect`).
Nothing declined — each has a real responder-side durable fact and forensic completeness settles
collection. Three corrections to the draft, all pointing one way.

**`known_certs` is not a `_responder_service` stem.** It keys on `host`/`port_num`, and the
plugin's own module comment already excludes that shape: *"both key on `host`/`host_p`, not
`id.resp_h`/`id.resp_p`, so they are not the same shape at all."* It groups with
`known_services`/`known_hosts`. Noted in passing: `known_certs` supplies the address `x509`
structurally lacks — the join that decline could not make — and that decline cites `Q1`, which
**closed 2026-08-14**, so its stated reason is now partly stale.

**The projection tuple is the cardinality surface, which dissolves D3.** The draft asked for a new
`MAX_KEYS`-style bound on `syslog`'s free-text `message`. `SERVICE_MAX_KEYS = 32` already bounds
distinct key-tuples per entity with overflow counted; the defect a bound cannot fix is putting a
free-text field **in the projection tuple**, where 350 distinct log lines burn all 32 slots and
evict the real facts. The rule the existing tables already obey without stating it: **collect
everything, project only responder-side, low-cardinality, durable facts** — forensic completeness
governs collection, `LES-017` store economics governs projection. **No new bound is needed.**

**`R1` is recurring, and this is the second time review caught it rather than a mechanism.**
`smtp`/`sip` were reversed last round for attaching a CLIENT-emitted field to a RESPONDER-keyed
event. The same defect is latent in three of these seven — `rdp`'s `client_name`/`keyboard_layout`/
`client_build`, `websocket`'s `host`/`uri`/`user_agent`, `mqtt_connect`'s `client_id`/`will_topic`.
Collected, never projected. **Lesson candidate recorded** in the work order: a responder-keyed
event must never project a client-emitted field — twice caught by eye, never encoded.

**Three name-shaped fields deliberately do not become identity.** A certificate `subject` is
routinely a wildcard or shared SAN list across many hosts — the same fanout failure as the router
MAC, arriving through TLS instead of Ethernet; a VNC `desktop_name` is user-set and only
incidentally the computer name; an MQTT `client_id` names an application instance, which is neither
a machine nor a person and has no scope in the vocabulary. All three are collected and available;
none is minted. Cert-derived identity is the natural next attempt now that `Q1` is closed, and it
must go through R2's subject discipline first. No source changed; tier 1 **1,519** tests unchanged.

---

## 2026-08-20 — an identifier's SUBJECT is a separate question from its scope

**Authority:** **user, delegated** — *"rule D8 and radius split together"*. Ruling, and the code
reading that changed it: Claude Code, same day. **Supersedes:** nothing ratified. It **withdraws
two claims from unratified drafts**: `docs/WORK_ORDER_zeek_stems_wide_vantage.md` D1's "first stem
needing per-field scope assignment", and `docs/WORK_ORDER_axquery_ingest.md` D8's per-window
fanout threshold. Full ruling in the axquery work order; D1 of the stems work order is now RULED.

**The per-field scope split already exists, and reading the code is what showed it.**
`IDENTITY_SCOPES` is `kind -> Callable[[IdentityEvent], list[Identifier]]`, not `kind -> scope`,
and every `Identifier` carries its own `scope`. `_identifiers_dhcp` already returns two from one
row; `kerberos_identifier` already returns `HOST` or `USER` by parsed value, which is finer than
per-field. `radius` therefore needs one extractor and one table row — the cheapest of the eight
stems, not the riskiest, and its build step loses its novelty rationale.

**The real gap is the SUBJECT of an identifier.** Three failure modes found this round share one
shape, and none of them is a scope error: an axquery conn MAC at high fanout is correctly
host-scoped and names the **next-hop router**; `radius`'s `mac`/`framed_addr` are correctly
host-scoped and name a host that is on **neither end** of the RADIUS exchange (it runs between a
NAS and the server); a DNS answer names a third party and is handled only because `REFERENCED`
happens to answer both questions at once. The resolver has always asked *what can this anchor* and
never *whose address is this*. The subject is now a declared, per-kind decision on the same
fail-closed discipline as scope. For `radius` the subject is `framed_addr`; a parser that binds to
the packet's endpoints merges every authenticated host onto the RADIUS server. For `rdp`,
`client_name` binds to the originator, never to the server that logged it.

**A per-window fanout threshold would have been unrecoverable, so the merge decision moves to
snapshot time.** Merges are forward-only and never rewritten (`entity_resolution` D2), so a MAC
showing fanout 1 in window 3 and fanout 40 in window 4 has already caused a permanent false merge
by the time the evidence arrives — low fanout in one window is *absence of evidence*, `LES-006` in
the one code path where it cannot be undone. Ruled instead: window close emits the `(address, mac)`
co-observation; **`BeliefStore.snapshot(as_of)`, which already applies `same_entity_as`, decides
the merge** with the whole bounded span's fanout in hand. The gate is positive rather than a
threshold — a MAC may anchor only if **every** address seen with it in the bound is local and the
count is 1, and **any** public address behind a MAC marks it next-hop permanently, since a public
host is never L2-adjacent to a corporate tap. Measured: that rejects the 46-IP and 43-IP MACs on
their first public address. A rejected MAC is not discarded but emitted as **`next_hop` topology
evidence** — hosts sharing a next-hop MAC share an L2 segment, which is `observed` segment
membership and strictly better than `segments.py`'s RFC1918 fallback at `imputed` 0.6.

**This is not the evidence-plane reversal.** `docs/WORK_ORDER_evidence_plane_snapshots.md` stays
unruled and untouched; the ruling uses the merge-application hook `entity_resolution` D2 already
ratified, changing what `snapshot()` consults rather than where the snapshot boundary sits. That it
points the same direction ("compose before you resolve") is worth noting when that work order is
ruled, and is not authority to pre-empt it. No source changed; tier 1 **1,519** tests unchanged.

---

## 2026-08-20 — a second sensor, and the first ruling on what a foreign system's conclusions are worth

**Authority:** **user** — *"create a wip branch to work on axquery ingest"*, *"use API not .axi
files for ingest"*, *"additional plugin"*, and the D1 ruling *"treats axquery's api outputs as
evidence based observations — no need to have additional learners to re-derive"*. Research,
measurement and the drafted work order: Claude Code, same day. **No source changed. Nothing in the
work order is ratified beyond D1.** **Supersedes:** nothing.

**aximprint is getting a second sensor, and the plugin contract has never been tested by one.**
`axquery` (`fx-cap/core/analytics/axQuery/`) is a C++ HTTP server over the appliance's own
axAnalytics entity database — the same appliance `acquire` already pulls pcaps from. It is
structurally unlike Zeek: Zeek hands us records and we infer, axquery hands us an entity database
and has already inferred. `zeek` stays; this is an additional plugin.
`docs/WORK_ORDER_axquery_ingest.md` is drafted, D1 ruled, D2–D8 open.

**D1, ruled by the user:** axquery's API outputs enter the event model as **observations**, with no
parallel learner layer re-deriving what the sensor already states; ML enters later as a separate
path. One carve-out is proposed and unruled: `src/aximprint/catalog/` fixes `has_role` as
**`imputed`, never `observed`**, so admitting `/api/devices` roles as `observed` would give a
foreign system's conclusion a *stronger* evidence class than aximprint's own conclusion from the
same kind of behavior. Proposed split: axquery's **measurements** are `observed`, axquery's
**conclusions** stay `imputed` with `source: axquery`.

**Three drafted findings were wrong and were corrected by measurement — the user caught the first
two.** (1) "axquery reports no TCP state" was generalized off a two-row sample that happened to be
UDP; `axEvConTcpT` carries `state_history[8]`, `no_syn`/`z_win`/`rto`/`keep_alive`/`dup_pkt`, and
`oTCPflags`/`rTCPflags`, and `/api/connections` emits the flag pair on **67 of 67** TCP rows —
enough to populate `FLOW_STATE_CLASSES` (`SF`, `S0`, `RSTO`/`RSTR`, `OTH` all observed). (2) VLAN
is **per-direction** (`OrigVlanId`/`RespVlanId`) because asymmetric tagging is real at a capture
point; the REST `vlanId` is a lossy orig-side back-compat projection, so a single `vlan` field on
`FlowEvent` would rebuild inside aximprint the lossiness axquery already backed out of. (3) "MAC is
absent" was an artifact of the `/api/entities` projection — `conn.orig_mac`/`conn.resp_mac`
populate.

**The finding that matters most is what a MAC MEANS here.** Over 200 rows / 38 distinct MACs, two
MACs front **46** and **43** distinct IPs — next-hop routers, not hosts. Binding identity on them
would merge 46 unrelated hosts into one entity, the unrecoverable false merge. **MAC fanout is
measurable in-window and is the discriminator**: low fanout is host identity, high fanout is
`next_hop` topology evidence — which is direct L2-segment-membership evidence, and strictly better
than `segments.py`'s RFC1918 imputation at 0.6. Drafted as D8, and it now collides with `radius`'s
per-field split (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` D1): three producers of host-scoped
identity want one table.

**An operational constraint learned the expensive way.** Walking `limit` up against a `--lazy-cold`
axquery (`/api/entities?limit=5000`, then `/api/connections?limit=2000`) **OOM-killed the server**
at 25 GB anon-rss under its cgroup cap. An ingest plugin that pages by `limit` can kill this
sensor. `/api/search/rows` offers real `offset`/`nextOffset` cursor paging and is the endpoint to
build on — but its `totalMatches` **grows with `limit`** in a fixed window (67 → 185 at limit
2 → 120), so it is a discovered-so-far count, not a population: page until `nextOffset` is absent,
never to a count. Two more gotchas recorded: `proto=` filters the **L7 label**, not the transport
(`proto=tcp` returns 0 in a window holding 24 TCP rows), and `conn.duration_sec` exists.

**The two sensors' datasets do not overlap.** axquery serves 2026-05-24 → 2026-06-19 (26 d,
`v29_test`); the Zeek PIT covers 2026-08-07 → 2026-08-17. Same site, same twelve streams, **49.6
days apart** — so cross-sensor agreement is explicitly not an acceptance criterion for this plugin.
The vantage strings also disagree (`axellio-hq/corp-apcn-rdr1` vs `https://10.80.65.7:0-11`), and
`compare` gates on vantage-set equality, so without reconciling them the two sensors' PITs are
permanently incommensurable (D6). Tier 1 **1,519** tests unchanged; no source changed.

---

## 2026-08-19 — ten days of the real network, and the snapshot boundary turns out to be in the wrong plane

**Authority:** **user** — *"prep the last 10-day corp-apcn run"*, *"run the ingest"*, *"run the
materialize"*, then a design discussion that reframed the architecture. Implementation and all
measurements: Claude Code, same day. **Nothing in the three new work orders is ratified.**
**Supersedes:** nothing yet — but `docs/WORK_ORDER_evidence_plane_snapshots.md` **proposes**
reversing the snapshot boundary chosen by `docs/WORK_ORDER_network_ir.md` D1–D9, and
`docs/WORK_ORDER_zeek_stems_wide_vantage.md` **amends** the closing claim of
`docs/WORK_ORDER_zeek_stem_coverage.md`.

**The whole chain ran end to end on real capture for the first time.** Ten days of corp-apcn, all
twelve streams: `harvest` moved **6.17 TB** through a host that never held more than 60 GB of it
(1,440 windows, 11.6 h, `extract_failed=0 zeek_failed=0 released=1440`), `ingest` produced a
0.32 GB belief store in 3 h 22 m (2.59 M assertions, 241 windows), and `materialize` produced the
first real PIT in 50 m — **58,563 entities, 82 segments, 109,173 edges, 38,252 unseen**, schema
`1.2`, vantage `corpapcn:0-11`, network `corp`.

**Two recorded constants were wrong by more than an order of magnitude, and the first one nearly
wedged the run.** Extraction is **~200 MB/s**, not the recorded 13.9; a Zeek worker does **618
MB/s** on real 8.27 GB input, not the recorded 3.5 (that figure was container startup measured on a
2.87 MB file). The planner's 1.10 GB/window estimate met a real **23.7 GB** window — the traffic is
violently bursty, one minute carrying 67.6 MB and the next 3.48 GB — and a 30 GB spool cap was
nearly consumed by a single window. Re-measured, re-launched at 60 GB, clean. `LES-119`.

**A completeness claim turned out to be vantage-scoped.** `docs/WORK_ORDER_zeek_stem_coverage.md`
closed with "every stem this network produces is accounted for by name, 28 + 7 = 35". At twelve
streams the network produces **43**, and **8 are unrecognized**: `known_certs`, `syslog`, `rdp`,
`radius`, `rfb`, `mqtt_connect`, `redis`, `websocket`. Nothing was lost silently —
`unrecognized_log_types` named all eight — but their content never reached the event model. The
claim was true of streams 1-4 and false at 0-11, which nobody could know until there were two
vantages to compare. `LES-120`.

**The measurement that reframes the architecture.** Host-scoped identity evidence is rare and
bursty: **105** named entities across ten days, named in a median of **2** windows while active in
a median of **124** — a **48×** ratio. Slicing into per-window PITs strands the name in ~2 slices
while the host appears in ~122 others as a bare address, and `merge` cannot re-key what was already
keyed (`LES-118`). So **snapshots belong in the evidence plane, not the conclusions plane** — the
user's framing, and the belief store is the composable artifact while the PIT is the frozen answer.
`BeliefStore.snapshot()` is already the exact split point. Also measured: **22 addresses are
claimed by more than one entity, one address served 12 different hosts**.

**An ML evaluation that came back negative, and is worth more than a positive one would have
been.** Unsupervised behavioural fingerprinting of host identity, validated against 421 true pairs
from `entity_roster` with zero identity leakage and zero new dependencies: three iterations, ending
at AUC 0.741 but **precision@1,2,3,5 = 0.00** — the highest-confidence predictions are all false,
which is precisely where a fail-closed design operates. Root cause is evidence density, not model
quality: 41 of 131 roster addresses have **zero** behaviour rows and the rest a median of **3**
windows. **Parse the logs that declare identity before modelling it** — `radius` carries
MAC→address, `rdp` carries a computer name. `LES-121`, `LES-122`.

Three work orders drafted, none started: `docs/WORK_ORDER_zeek_stems_wide_vantage.md`,
`docs/WORK_ORDER_evidence_plane_snapshots.md`, `docs/WORK_ORDER_ml_layer.md` (which carries the
full evaluation record and the density table showing which ML tasks are dense and untested —
`tfc` parameter learning chief among them). Tier 1 **1,519** tests unchanged; no source changed.

---

## 2026-08-17 — a PIT knows which tap it is, and the twin holds only hosts that do something

**Authority:** **user** — *"ratify D1-D12 as drafted"* (`docs/WORK_ORDER_visibility_and_vantage.md`,
drafted 2026-08-14, D5a included). Implementation and the three additive findings below: Claude
Code, the same day.
**Supersedes:** `ir/materialize.py::_run_context`'s mixed-source refusal (D3), `ir/compare.py`'s
source-equality check (D3/D4), and `synth/compiler.py`'s emit-every-placed-node behavior (D5).

**A PIT was one tap point's view and nothing recorded which tap.** That gave the comparator two
failure modes, and the second is the dangerous one because it *answers*: re-harvesting one tap into
a new directory was falsely **refused** (`source` differs), and two *different* taps writing to the
same path were falsely **accepted**, with their view difference reported as network drift. The
identity was never an inference problem — `acquire` knows the appliance and the streams, and both
were discarded at the disk boundary.

**All ten build steps landed.** `harvest` now writes a `vantage.json` beside the cold tier before
the first window (an interrupted harvest still produced real logs from a real tap); the zeek plugin
reads it *without pulling an event*, so a `--vantage` that contradicts it is refused up front rather
than after forty days of ingest; `runs` carries `vantage`/`network`; and `ir_pit` carries them
through. `network` is operator-declared and separates *which network* from *which capture* — that
distinction is what lets two vantages be merged and two networks be refused. Schema **`1.2`**, every
change additive. The IR gained **`ir_unseen`**, DESIGN_BRIEF §8's referenced-but-unseen census,
which the store already held and the IR had been dropping. `aximprint merge --axis {vantage,time}`,
`aximprint visibility`, and `cast --min-occupancy` are new verbs/flags.

**Measured on the 2026-08-11 baseline, re-ingested with a vantage.** Row counts identical to
before (22,870 assertions, 12,914 bindings) — vantage was metadata being thrown away, not a change
to what is learned. The census is **2,418** rows, exactly D7's figure; **9** are not on the
internet and **0** of those are inside an observed segment (all nine are IPv6 link-local mDNS or
`ipv4only.arpa`), so on a near-full-visibility tap the estate blind spot is genuinely empty — a
stronger statement than "9", and visible only because `locality` and `segment` answer different
questions. The twin went from **38 segments / 197 hosts, 178 of them role-less** to **9 segments /
19 hosts / 8 conduits**, with 29 orphaned segments pruned and named; **axRange's own expander
accepted it: 9 networks, 20 nodes, zero warnings.** `--min-occupancy 0/0.5/1.0` produces 19/19/17
hosts off **one** PIT with no re-materialize, and the sidecar's population block balances at every
threshold (`197 = emitted + role-less + occupancy-excluded`). D12 fires for real: `corp-workstation`
and `file-server` are `diurnal` and the PIT spans 3.0 h.

**Three additions to the ratified list, each additive and none a reversal.** (1) `ir_pit.vantages`
/`.merge_axis`/`.merged_from`: D4 requires a merged PIT and D10 requires it to name its axis, and an
artifact that cannot say which taps it combines cannot support D4's own same-vantage-set refusal.
(2) **`vantage` and `since` joined the PIT identity** — two different taps writing to one directory
shared a `pit_id` and could not coexist in a store at all, which is D1's own scenario; and `since`,
added by the previous work order without revisiting identity, made `[W1,W2]` and `[W2,W2]` collide,
the exact pair D10's preferred workflow produces first. An unbounded, vantage-less PIT's id is
unchanged (`LES-113`). (3) **`snapshot()` was reading `identity_bindings` unbounded** — a real
defect, surfaced because the census inherited it: a `since`-bounded PIT carried every binding the
store ever held.

**And one measured correction to D10.** *"Materializing over the span is preferred"* was reasoned;
it is now a number. A time merge combines rows that were already **keyed**, so it cannot re-key
them, while entity resolution works across the seam it cannot see: **3,237 entities merged against
3,231 materialized**, the six extras being hosts whose hostname or MAC evidence fell in only one
window (`10.80.20.178` → `hostname:idrac-c28pf33`, five more). A combined PIT is a **superset** — it
fragments identity, never loses a host.

`LES-116`–`LES-118` record what generalizes. Tier 1 **1,519** tests (+80); tier 2 green.

---

## 2026-08-14 — non-host bindings become a lossless LRU working set, not a census cap

**Authority:** **implementation (Claude Code)** at user challenge — *"why do we need
`MAX_HOST_BINDINGS`/`MAX_NONHOST_BINDINGS` limited when generating IR as PIT? It makes sense when
translating PIT-IR to twin config."* The question was right and the code was wrong.
**Supersedes:** `MAX_NONHOST_BINDINGS`'s refuse-when-full behavior and its name.

**The two bounds were the same shape and are not the same kind of thing**, which the docstrings
already said without the code acting on it. `MAX_HOST_BINDINGS` caps *"the resolver's working
state, the map every `resolve()` consults"* — a genuine bound on a hot structure, hit twice per
flow, and **41 of 4,096 used** on the baseline. `MAX_NONHOST_BINDINGS` bounded *"what is merely
RECORDED"*: verified **zero references from `resolve()`**, a pure output accumulator. Capping it
bounded the artifact, not the run.

**Its eviction policy was worse than the cap.** `_observe_nonhost` refused *new* bindings once full,
so the retained set was first-come-first-served. Over a 41-day capture that keeps whatever DNS names
appeared in the first hours and discards every later one — not a sample of the capture but its
earliest slice — while reporting itself merely "incomplete". And it capped precisely the
referenced-but-unseen census that `docs/WORK_ORDER_visibility_and_vantage.md` D7 names as the
primary input to DESIGN_BRIEF §8's visibility model, so a resource bound was silently deciding a
discovery question. It also contradicted that work order's own **D9** — filtering belongs at compile
time, never upstream of the IR.

**Now `NONHOST_WORKING_SET`, an LRU dedup window whose eviction is lossless.** The evicted binding
is **published** as a `final` record carrying its accumulated interval and count, exactly as
`flush_open` does at end of run (`LES-108`: a delta artifact must flush what it stops tracking, and
eviction is a stop-tracking event). A key seen again after eviction opens a new interval, so one
(address, name) pair may reach the store as several records whose counts sum to the true total —
an honest, visible gap rather than a silent absence. Touch-on-hit makes the order reflect use, so
the names a capture keeps seeing outlive ones seen once.

Consequences: `roster()["state"]["complete"]` now keys on **host refusals alone**, because an
eviction no longer truncates anything and calling it incomplete would be a false alarm on the
commonest path in a long capture. Evictions are counted **separately** from refusals
(`identity_bindings_evicted` / `nonhost_bindings_evicted`) — a refusal loses a fact, an eviction
splits an interval, and summing them would report data loss that did not happen.

**One existing test encoded the opposite contract** and was rewritten rather than adjusted: it
asserted that a full map refuses and that `complete` goes false. Two tests were added for the new
properties — that an evicted binding's `final` carries its counters, and that a repeatedly-observed
name outlives one seen once (the property FIFO did not have).

Re-ingesting the 2026-08-11 baseline produces **identical row counts** (22,870 assertions, 12,914
identity bindings), because a 3-hour capture never fills the window — the change manifests only on
the long captures it was made for. `LES-115` records the general rule. Tier 1 1,439 tests (+2);
tier 2 green.

---

## 2026-08-14 — fix: `windows_seen` counted a merged host's overlapping windows twice

**Authority:** **implementation (Claude Code)**, found while answering a user design question about
combining PIT snapshots over a time range.
**Supersedes:** `BeliefStore.snapshot()`'s per-spelling `count(*)` summation.

`snapshot()` summed the window counts of an entity's pre- and post-merge spellings instead of
counting **distinct** windows. A merged host has rows under both its pre-merge address and its
`ent-NNNN` id (`Q1` D2, forward-only) over **overlapping** ranges, so the sum over-counts:
`ent-0001` appeared as `10.80.78.114` in 1 window and as `ent-0001` in 3, and was reported as
**4 of 3**. Measured on the 2026-08-11 baseline: **20 of 24 resolved entities claimed more windows
than the store contained** — i.e. exactly the hosts entity resolution worked hardest on.

**Why it mattered rather than being an arithmetic slip.** `windows_seen` is the within-PIT presence
shape (`WORK_ORDER_network_ir.md` D4a) — the field that distinguishes an always-on host from an
occasional one, and the signal a compile-time occupancy threshold would rest on. It was wrong for
the best-resolved hosts. Fixed by collecting distinct `window_start` values per canonical entity.

**Third instance of the pre-merge/post-merge trap** (`LES-114`, whose own origin was the second).
The invariant that caught it is now a test — *a host cannot be present in more windows than exist* —
which is the kind of assertion that would have caught all three.

Measured context recorded while diagnosing it, since it bears on capture planning: of 196 placed
hosts on a 3-window PIT, 118 appear in all 3, 32 in 2 and **26 (13%) in only one**, so window width
materially changes which hosts a PIT contains.

Tier 1 1,437 tests (+2).

---

## 2026-08-14 — `docs/WORK_ORDER_visibility_and_vantage.md` drafted: a PIT is one tap's view, and nothing records which tap

**Authority:** **direction is the user's** (this session) — a pcap is only the tap point's view;
several taps combined give a richer picture of what is present *and what is missing*; most IR nodes
will legitimately carry no role; the twin holds only role-bearing nodes, scaled to fit a cluster;
and drift evaluation must account for all of it. **Extended the same session with a second axis:** a
PIT shows only the nodes *active* in its window, so windowing interval and traffic schedule make it
a partial view **in time** as well as in vantage, and how much to combine should be tunable. D1–D12
are **Claude Code proposals awaiting ratification**.
**Supersedes:** nothing yet. When built it will supersede `ir/materialize.py`'s mixed-source
refusal (D3) and `synth/compiler.py`'s emit-every-placed-node behavior (D5).
**Documentation only — no code changed, nothing built.**

The user's framing turns out to restate **DESIGN_BRIEF §8** almost line for line — partial
visibility inverts the problem, estimate the blind spots first, referenced-but-unseen hosts feed a
per-segment visibility model, and that model is itself an actionable output. So this is the design
of record catching up, not a new direction.

**Three things measured before designing anything.** `ir_pit.source` is a filesystem path and says
nothing about where the tap sat. **`harvest` writes no manifest at all** — the appliance host and
stream ids `acquire` knows are *discarded at the disk boundary*, so vantage identity is metadata
that was thrown away rather than an inference problem. And **2,418 addresses appear as DNS answers
and were never observed in any flow** (`identity_bindings`, `scope=referenced`) — §8's
referenced-but-unseen census, sitting in the store, dropped by the IR. Only 9 are inside the estate
on this near-full-visibility baseline; on a partial vantage that number *is* the blind spot.

**The comparator has two failure modes today and the second is the dangerous one:** re-harvesting
one tap into a new directory is **falsely refused**, and two *different* taps writing to the same
path are **falsely accepted**, with their view difference reported as network drift.

**One thing the user's framing settles, having been argued the other way this morning.** Drift is
compared at the **IR** — the full tap-point view — never at the twin, which is filtered and scaled
down; comparing twins would read a scale-down cap as network change. That the IR sits between them
is what makes multi-tap work expressible at all, and is the strongest post-hoc argument for
`WORK_ORDER_network_ir.md`'s D1 reversal.

**The decision most worth the user's attention is D5a** — whether the twin filters on *role* or on
*role and persona*. The draft says role-bearing, because `dns-resolver` has a role and no persona
yet the surviving workstations structurally need it; filtering it out yields a twin whose remaining
hosts cannot function. That is a judgment about what a useful twin is.

**D8 records a distinction worth keeping regardless of what gets built:** much of what is naturally
attributed to ML here is **missing metadata, not missing inference**. No model can recover which tap
a PIT came from, or what the compiler filtered, if nobody wrote it down — and recording it makes the
comparator substantially smarter with no model at all. Metadata first, model second.

**D10–D12 fold in the time axis, and two of them push back on the request as stated.** The time
union **already exists** — `materialize(store, since=…, as_of=…)` *is* the union of that span, added
by the IR work order's build step 5 — so D10 makes it the preferred mechanism rather than adding a
second one, and confines artifact-combination to the pruned-store case where it must **refuse
overlapping spans** (summing two overlapping PITs double-counts their shared windows). D11 argues
the twin's real tunable is **not window width but a compile-time occupancy threshold**: because
`windows_seen`/`windows` survives into every entity row, one wide PIT serves many thresholds
without re-materializing, it lands where filtering already belongs (D5/D9), and it composes with
`last_seen` so a long PIT need not drag decommissioned hosts into the twin. D12 observes that
**axRange's own persona cadences give the required capture width** — 22 `diurnal` emits and a
`cyclic:24h` cannot be observed at all in under 24 h — which makes the 3-hour baseline
**structurally too short** for the behavior it would claim to model. A capture-planning finding, not
a code gap.

**The tension D10–D12 exist to resolve, stated once:** union is exactly what destroys the absence
signal. Twin building wants a **wide** PIT (a host absent from a narrow window is missing entirely);
drift detection wants a **narrow** one (a wide window makes everything look present). One global
window setting cannot serve both, which is why "how much was captured" and "how much reaches the
twin" are separated into different knobs at different layers.

Measured while drafting: of 196 placed hosts on a 3-window PIT, **118 appear in all 3, 32 in 2, and
26 (13%) in only one** — so window width materially changes which hosts a PIT contains.

---

## 2026-08-14 — network IR build step 4: axRange expanded a discovered twin, and `docs/WORK_ORDER_network_ir.md` is COMPLETE

**Authority:** **implementation (Claude Code)** at user ratification ("start build step 4"), against
D7/D9 as ratified.
**Supersedes:** nothing. `synth/synthesizer.py`'s v0 draft path is untouched and still what tier 2
pins; the IR compiler is a second, additive entry point (`aximprint cast`).

`synth/compiler.py` compiles a PIT into axRange's three documents plus a provenance sidecar.
**Every build step of that work order landed the day it was ratified** — 1, 1b, 2, 3, 4, 5.

**The acceptance gate identified at the very start of this arc was run, and it passed.** The
compiled `twin_config` went through **axRange's own `expand_twin_config_raw`, in their venv**, which
produced **38 networks and 197 nodes** — per-host IPs auto-allocated, per-network gateways
auto-assigned at `network+1`, the synthetic `gw` multi-homed across all 38 legs, zero warnings. That
answers *would this deploy* without deploying anything, and it is the first time this project's
output has been validated by the system that consumes it.

**The chain reconciles end to end**, which was worth checking rather than assuming: 3,302 entities
in the store → **197 placed** (phase 1's provisionability gate) → **50 node groups** (this step's
collapse) → **197 nodes** after axRange's own `count:` fan-out → **19 carrying a role** (phase 2's
figure, unchanged through three layers).

**Five caveats the compiler emits about its own output**, each tracing to a measurement rather than
to caution: 117 local entities are not nodes (on nothing provisionable, each already present under
its routable address); `gw` is synthetic because this vantage carries neither of DESIGN_BRIEF §8's
gateway signals; `dns-resolver` has no axRange persona and generates no traffic; **37 of 49 node
groups carry no role**, so most of the twin is inert until client-side inference lands; and the tap
plan is **incomplete by design** — every binding omits `sensors`, which names Lab (`o2`) sensors
aximprint cannot observe. A plan that fails their validator with "sensors missing" is a better
artifact than one that passes carrying a fabricated sensor name.

**324 provenance fields, in a sidecar rather than inline** — axRange's vocabulary is
`extra="forbid"`, so the v0 stub's `{value, provenance}` wrappers are *rejected by their schema*.
This is also where **`defaulted` legitimately enters**: it is deliberately not an IR evidence class,
because it is what a compiler does when a catalog supplies what discovery never observed. Roles
arrive `imputed`, segments `observed` with their coverage, images and `gw` `defaulted`.

`catalog/roles.yaml` gained a `default:` block — 178 of 197 placed hosts match no rule, so a node
with no role is the common case, and `image` is required. **D3 stays open**: `--max-count` exists,
the default is uncapped, and a cap that bites is reported as a caveat naming the true host count.

Two test failures during the round were the fixture's fault rather than the compiler's, and both
are now commented at the assertion: the fixture's default port 445 makes a host a `file-server`, and
the `dns-resolver` rule requires `53/udp` so a tcp flow correctly never matched it.

Tier 1 1,435 tests (+23); tier 2 green.

---

## 2026-08-14 — network IR build step 5: two PITs answer "what changed", and D4's absence classification works on the first try

**Authority:** **implementation (Claude Code)** at user ratification ("start build step 5"), against
D4/D4a/D8 as ratified. Step 4 (compile) deliberately skipped for now — step 5 is what tests whether
the design decisions were right, and it is independent of the compiler.
**Supersedes:** nothing. `since` is an additive parameter on `materialize()` and
`BeliefStore.snapshot()`; no schema change, no default behavior changed.

`ir/compare.py` and the `aximprint drift` verb produce a structural drift report between two PITs.
Run on two real single-window PITs from the 2026-08-11 baseline: **local entities +43 / −16 / 2
changed / 258 unchanged; segments +1 / −1; edges +1,693 / −1,679; external peers +1,031 / −932.**

**D4's absence classification did real work immediately, with no tuning.** Of 16 removed entities,
**12 are `observed_absent` and 4 are `not_observed`** — and the 4 are exactly the hosts of the one
segment that stopped being observed between the two PITs. The comparator declines to claim they left
the network, because nothing was watching where they were. That is precisely the distinction the
coverage block exists for, and it is the first time the design has been tested rather than argued
for. `observed_absent` is documented as *evidence* of absence, never proof: a host that talks once a
day is absent from a perfectly covered three-hour PIT too, so the coverage ratio is published beside
every verdict.

**Partitioning by `locality` (schema `1.1`) is what makes the report legible.** External churn is
**1,963 changes against 61 local ones** — a naive diff would be 97% noise from CDN addresses
rotating for reasons unrelated to the monitored network. They are counted, never itemized, and the
count is published so nobody wonders whether they were dropped.

**The gap step 5 exposed, and the reason it was worth building the comparator before the compiler.**
`as_of` is **cumulative** — everything known up to T — so **any two PITs from one store have nested
extents and are refused as incommensurable.** The refusal is correct; the problem was that a single
store could then never produce a comparable pair, which would have made the artifact unusable for
its second stated purpose. Fixed with a **`since` bound** on `materialize()` and
`BeliefStore.snapshot()`. The two purposes want different shapes and both are legitimate: a twin
wants everything known, drift wants a span. `as_of` keeps its meaning as the upper bound. Aliases
stay bounded by `as_of` alone — a merge is knowledge the resolver holds, not an event inside the
span, so a host identified before `since` is still that host during it.

**Refusals** (each makes a report *wrong*, not merely uncertain): different sources, differing
schema MAJOR, a PIT against itself, a zero-length window, and durations differing by more than 2×.
**Caveats** (weaken without invalidating): a build change between the PITs (`LES-113`), weak
`address` joins — 279 of 303 local entities on this pair, which is step 2's 88% seen in use — and
capture loss or its absence. Keeping those two categories apart is deliberate: refusing on a caveat
makes the tool unusable, and downgrading a refusal to a caveat produces confident nonsense.

One test was written wrong and caught by its own docstring: it cited the real 3× case and then
built a 2× one, which is *exactly* `MAX_DURATION_RATIO` and therefore allowed. Fixed to 3×, with a
second test pinning the boundary itself so a later edit to the constant has to face it.

Tier 1 1,412 tests (+19); tier 2 green.

---

## 2026-08-14 — network IR build step 3: the first PIT exists, and materializing it found a hole in the schema

**Authority:** **implementation (Claude Code)** at user ratification ("start build step 3"), against
D2/D3/D4/D4a/D9 as ratified.
**Supersedes:** nothing. Schema `1.0` → `1.1` is an **additive** change under DESIGN_BRIEF §11, not
a reversal.

`ir/materialize.py` turns a belief store into a PIT; `ir/store.py` persists many PITs in one
separate DuckDB (D2); `aximprint materialize` is the verb. **Step 1b landed with it** rather than in
the windowing layer — per-segment coverage turned out fully derivable from `node_features`, which
already records each entity's segments per window, so an accumulator upstream would have computed
what the store could already answer.

Measured on the 2026-08-11 baseline: **3,302 entities, 38 segments, 4,893 edges, 4,796 identity
bindings**, with the observation extent and `capture_loss_pct_max = 4.57`. Two materializations of
one store are byte-identical. Re-writing an existing `pit_id` is **refused** unless `--replace`,
because something may already have compared against it.

**The schema went to `1.1` because reading the first real PIT found a hole in `1.0`.**
`segment IS NULL` was conflating two entirely different populations — **117 local hosts on nothing
provisionable** (IPv6 link-local, multicast) and **2,988 internet peers** — so a comparator
filtering the monitored network on that column would have silently dropped all 117. That is the
exact `LES-006` failure this artifact exists to prevent, and no review caught it; materializing and
looking at the output did. `ir_entity.locality` closes it.

**The bump was deliberate where an edit was available.** Nothing outside a scratchpad had ever read
`1.0`, so the schema could have been corrected in place. It was not: *"nobody has read it yet"* is
the reasoning that erodes a versioning discipline the first time it is inconvenient, and an additive
bump costs nothing because it is backward-readable by construction.

**A pre-documented bug walked into anyway, recorded because that is the interesting part.**
`_entity_locality`/`_entity_extents` read `node_features` **without canonicalizing across merges**,
so every merged entity silently lost its rows — one entity surfaced with a segment and no locality.
`node_features` is pre-merge (`Q1` D2's forward-only design) while `snapshot()` is post-merge, and
**`WORK_ORDER_synth_v1.md`'s own phase-1 finding names this exact hazard**. Writing a hazard down is
not the same as being protected from it; `_entity_extents` now requires the alias map as a
positional argument rather than defaulting it, so the next caller cannot omit it by accident
(`LES-114`).

Two refusals, both because a PIT that cannot say what it describes is not a PIT: a store nothing
ever cleanly closed into (no `runs` row, no source), and a store several runs from **different**
sources wrote into (no single answer, and picking one would be false for the rest).

One unrelated test was made robust rather than updated: `test_top_level_help_lists_acquire_among_the
_choices` hardcoded the full verb list and failed when `materialize` joined it — a new verb is not a
regression in `acquire`'s wiring, so it now asserts membership in the rendered choice list instead
of the list's exact contents.

Tier 1 1,393 tests (+21); tier 2 green.

---

## 2026-08-14 — network IR build step 2: two PITs can finally be joined, and 88% of the twin joins weakly

**Authority:** **implementation (Claude Code)** at user ratification ("start build step 2"), against
D6 as ratified.
**Supersedes:** nothing. Step 2 derives keys and writes no rows; wiring to the store is step 3.

`src/aximprint/ir/keys.py` (24 tests) turns an entity's identifiers into a cross-PIT natural key.
This is the prerequisite the work order flagged as *"the one item genuinely unbuilt rather than
merely unassembled"*: `Q1` D3 made belief-store ids **run-scoped**, so `ent-0001` on Monday and
`ent-0001` on Friday name unrelated hosts and no two snapshots could be joined at all.

Precedence `hostname` → `mac` → `address`, **one identifier chosen, never a composite** — a
composite would make an entity a *different* entity the moment a second NIC started or stopped
appearing, which is exactly the join it exists to support. Keys are self-describing
(`hostname:bw-l`), duplicating `key_kind` on purpose: consumers will join on `entity_key` alone
because it is one column and it works, and a bare value would let a hostname that looks like an
address join to one.

**The precedence was decided by a measurement, not a preference.** On the 2026-08-11 baseline one
host carries one computer name and **two MACs across two addresses** — a laptop, wired and wireless.
Keyed on mac it splits in two across PITs depending on which interface was active; keyed on hostname
it stays one host, which is what it is.

**Measured: 3,302 of 3,302 entities keyed, zero collisions. But the number that matters is smaller
and it is the honest headline — only 24 of 197 placed entities (12%) join on strong evidence**; the
other 173 join on `address` alone, which D6 already labels the weak kind ("asserts only that the
address did not change hands"). The design handles it — `key_kind` is precisely what lets a
comparator weight the two differently — but on a DHCP network 88% is a real limit on cross-PIT drift
claims about client hosts, and it is recorded rather than rounded. A hypothesis worth testing and
**explicitly not a finding**: anchor coverage should grow with capture duration relative to lease
time, since a host anchors when it renews or authenticates, and this baseline is ~3 hours.

**Two namespaces are refused by name** (`NOT_A_KEY`): a Kerberos **user principal** — `Q1` D1 one
layer downstream, since keying on it would collapse every machine one administrator logged into
into a single entity, and worse the more diligent the administrator — and a **DNS name**
(`scope=referenced`). An unrecognized namespace is **counted**, never silently used, which is what
keeps "the precedence table is complete" checkable. Neither refusal fires on this baseline, so both
are currently exercised only by tests; said out loud so "0 declined" is not read as "the rule never
has to fire".

**One hazard recorded as `LES-113`:** the key is the resolver's parsed computer name and
deliberately does not re-normalize it (two copies of a normalization rule is how they drift) — which
makes the key's stability a property of a function in another package. Change `computer_name()` and
every subsequent key differs from every key already persisted, so PITs either side do not join while
both stay schema-valid and every test passes. `ir_pit.code_fingerprint` is what lets a comparator
detect it; the escape hatch is §11's own backfill.

Tier 1 1,372 tests (+24); tier 2 green.

---

## 2026-08-14 — network IR build step 1: the schema exists, and it is the only versioned one

**Authority:** **implementation (Claude Code)** at user ratification ("ratify D1-D9 and D4a/D4b as
drafted, start build step 1"). **All eleven decisions ratified as drafted; none overruled.**
**Supersedes:** nothing. Build step 1 defines a schema and writes no rows.

`docs/SPEC_network_ir.md` is the schema of record at version **`1.0`**;
`src/aximprint/ir/schema.py` is its executable half; `tests/test_ir_schema.py` (23 tests) fails if
the two disagree. That three-part pairing is copied from `docs/SPEC_zeek_metadata_archive.md` ↔
`ingest/zeek/plugin.py::_COLUMNS`, for the reason `LES-014` gives: a schema defined only in prose is
a schema nothing checks.

Five tables, every one keyed on `pit_id` so many PITs share one database and a drift comparison is a
join rather than N file attaches (D2): `ir_pit` (identity, observation extent, global capture loss),
`ir_entity` (natural key + `key_kind`, segment, role, and **within-PIT window occupancy** per D4a),
`ir_segment` (**per-segment coverage** per D4a), `ir_edge`, `ir_identity`.

**Versioning applies DESIGN_BRIEF §11 rather than inventing a scheme**, since §11 became binding for
this artifact on the same ratification. `MAJOR.MINOR`: additive → MINOR (a reader ignores an unknown
column, which is what §11's "named fields, not positions" buys), subtractive → MAJOR, **semantic
redefinition in place → forbidden outright** rather than versioned — a column whose meaning changes
while its name stays is undetectable by *any* version check, which is why §11 makes it a prohibition
and not a bump rule. `can_read` fails closed on an unknown MAJOR and accepts any MINOR within one,
including a higher one. The version lives in a **row** (`ir_pit.schema_version`), not only in the
producing build: an artifact has to state its own version to be readable by a build that did not
write it, which is precisely the difference between this and the belief store's build-fingerprint
guard, which correctly refuses every mismatch because nothing is meant to read it across builds.

**Two design points the schema forced, both worth keeping.**

*`defaulted` is not an IR evidence class.* The code disagreed with itself: `learners/extractors.py`
documents three classes, `synth/synthesizer.py` four. Both were right about their own layer —
`defaulted` is what a **compiler** does when a catalog supplies a value discovery never observed, so
it belongs to `twin_config`, not here. **The IR carries only what was discovered**, and a value
nothing discovered is absent rather than present-and-defaulted. That is what stops a twin's
`image: debian-12-cloud@v1` from re-entering a drift comparison looking like evidence about the
monitored network.

*`ir_edge.evidence_class` is what makes the GNN-comparison use work.* DESIGN_BRIEF §6 names link
prediction for imputing unobserved edges; that column is where an imputed edge lands **beside** an
observed one, distinguishable rather than merged.

**The scope of the ratification is enforced mechanically, not just stated.**
`test_no_other_package_has_acquired_a_version_field` fails the suite if `SCHEMA_VERSION` appears
outside `aximprint.ir`. `LES-001` is amended, not repealed, and CONVENTIONS §1.2 — which said in
advance that ratifying one schema version-locks *"that schema (only)"* — fired for the first time
here and did exactly what it promised.

`ir` is classified **forbidden for `acquire`**, like `catalog`, on meaning rather than cycles;
`ir/schema.py` imports nothing at all, so a cycle-based check would wave it through. Tier 1 1,348
tests (+23); tier 2 green.

---

## 2026-08-14 — the network IR is ratified: the first versioned schema in this project, and a same-day reversal

**Authority:** **user**, this session — both the direction and, decisively, the **DESIGN_BRIEF §0.2
ratification**. This is the first genuine schema ratification the project has made; §0.2 said
versioning would be ratified later, by the user, and this is that moment.
**Supersedes:** `docs/WORK_ORDER_synth_v1.md` **D1** ("direct compiler"), ruled by the user earlier
the same day on a Claude Code recommendation. Also renames
`docs/WORK_ORDER_axrange_handoff_ir.md` → `docs/WORK_ORDER_network_ir.md` and redrafts it whole.
**Documentation only — no code changed.** D1–D9 of the redrafted work order are Claude Code
proposals awaiting ratification; nothing is built.

**Scope of the ratification, stated narrowly because `LES-001` is the most likely thing to go wrong
next: ONLY the IR is versioned.** The event model, feature shapes, plugin contract and belief-store
schema stay incubating and free to change. That asymmetry is the design, not a compromise — the
belief store can stay free *because* it stays internal, and only the IR crosses a boundary.

**Why the morning's ruling was wrong.** The recommendation behind D1 presented cross-run drift as
"the argument on the other side" — a tiebreaker in a build-sequencing decision — when it is a
**separate product requirement that the direct route structurally cannot satisfy**. The user's
2026-08-11 ruling named two purposes (build a point-in-time twin; track drift across runs) and the
recommendation optimized around one. A capability only one option provides does not break a tie; it
decides the question. The asymmetry that should have settled it: the §0.2 ratification cost being
avoided is paid **once**, while the capability traded away is foregone **permanently**.

**The strongest argument was already in the code, and nobody looked.** `BeliefStore.snapshot()`
returns `{as_of, entities, listens, segments, roles, edges, bindings}` — `segments` added by phase
1 and `roles` by phase 2, one key per phase. That is *already* an IR: a point-in-time structural
representation, undesigned, unnamed, unversioned, in-memory only, growing by accretion. The choice
was never "IR or no IR" but "designed IR or accidental one", and an accidental one has all the
coupling of a schema with none of the guarantees (`LES-112`).

**What the IR is, beyond the axRange handoff** — which is why the file is renamed. It is the
**structural half of DESIGN_BRIEF §10's own decomposition**: §10 splits drift into structural and
behavioral, specifies that behavioral drift is compared in feature space, and never named the
artifact for the structural half. It is also the right altitude for comparing GNN-derived output
against evidence-derived output (the assertion table is too low, `twin_config` too high), and the
natural input to topology-diagram generation. And it is where the project's honesty claim stops
being discarded at the exit: `twin_config` carries no confidence, no `evidence_class`, no
observation window and no coverage caveats, so today every piece of provenance machinery is thrown
away at exactly the boundary where the artifact leaves the project.

**Nine decisions drafted** (`docs/WORK_ORDER_network_ir.md`), of which three matter most. **D6**:
cross-PIT entity identity needs a **natural key**, because `Q1` D3 made `ent-NNNN` run-scoped and
two snapshots therefore cannot be joined at all today — the one genuinely unbuilt prerequisite.
**D4**: the IR records what it saw plus its own coverage, and the *comparator* classifies absence,
because an IR row cannot know about a future PIT — without which every diff conflates "changed"
with "we were blind", and for drift detection absence is the signal. **D7**: the compiler stays in
aximprint, reversing the 2026-08-11 "transformer lives in axRange" ruling, on the reasoning that
**the IR is what buys independent evolution, not the compiler's repo** — and that one
implementation beats two, since `LES-111` is exactly the lesson that a cross-repo mapping has no
test that can protect it.

**Phases 1 and 2 are unaffected and no work is lost**: segments and roles are discovery, and feed
an IR exactly as well as a direct compiler. `WORK_ORDER_synth_v1.md` phase 3 is re-scoped from one
step into two (materialize the IR, then compile it) and blocked on D1–D9.

---

## 2026-08-14 — synth v1 phase 2: hosts get a role, and the catalog it implies is content

**Authority:** **implementation (Claude Code)**, at user request ("start phase 2"). No gate tripped:
no ratified decision reversed, no schema ratified, no runtime dependency added (`pyyaml` is already
one of the two declared), no change of iteration direction.
**Supersedes:** nothing. The synthesizer stub's `role: {value: "unknown", provenance: defaulted}`
is untouched — phase 3 replaces it.

`src/aximprint/catalog/` is a new **bottom-of-stack shared package**, imported down into by
`learners` now and `synth` in phase 3, importing nothing from `aximprint` itself. It holds
`roles.yaml` — **content**, per CONVENTIONS §3 — where one row joins the three things that must not
drift apart: the evidence that infers a role (`requires`), the axRange persona that role is assigned
in a `tfc` profile, and the twin content (`image`/`resources`/`install`) traffic metadata can never
supply. `roles.py` validates it on load with a closed key vocabulary and refuses anything it cannot
trust, because a hand-edited data file read without checking is a silent-default trap with a YAML
extension. Packaging: `[tool.setuptools.package-data]` now ships the YAML — without it the module
imports fine from a checkout and fails only on an installed wheel.

**`has_role` is asserted `imputed`, never `observed`, and that is the phase's central call.** Nobody
observed "this host is a domain controller"; what was observed is that it answered on 88 and 389.
The `listens_on` assertion underneath stays `observed`. Levelling the two would put an inference
into the twin config's per-field provenance wearing an observation's face, which is the specific
thing CONVENTIONS §3 forbids. Matching is per-window, and that is measured rather than assumed:
every window of every domain controller on the baseline carries both 88 and 389, and only 7 of 1,363
entities with listen evidence lose any port to per-window splitting.

Measured on the 2026-08-11 baseline: **19 of 197 placed entities get a role** — 8 `corp-workstation`,
5 `web-tier`, 3 `dns-resolver`, 2 `domain-controller`, 1 `file-server` — and the phase-3 collapse
preview is **197 hosts → 49 node entries**. The 10% coverage is what the work order predicted in
advance, so it is a confirmed expectation, not a shortfall.

**A trap found by reading the sibling repo rather than assuming it.** axRange's persona catalog
looks like it could generate this table directly — every persona declares `emits[].ports`. It
cannot: that field's meaning depends on `drive:`. For `drive: scripted` the driver calls
`send_tcp(tag, peer, port, …)`, so the ports belong to the PEER; for `drive: real-service` the
driver is "a no-op emitter" that never reads them and the block documents the persona's OWN
listening ports. `corp-workstation`'s `[88, 464]` and `file-server`'s `[445]` therefore point in
opposite directions, and a uniform derivation would have labelled every client with its server's
role (`LES-111`).

**Two findings recorded rather than acted on**, both in the work order's Phase 2 RESULT. Build step
1's instruction to feed `known_services.log` into the matcher is **declined with a path back**: the
`Q27`-ruled `SERVICE_PROJECTIONS` row drops `port_num`, so those facts carry no port at all
(`tcp|DCE_RPC`, `udp|DNS`), and making them usable means editing a user-ruled table for a measured
8 entities. And the highest-value next step is **client-side** role inference — 168 of the 197
placed hosts never accept a connection, so no port-signature rule can reach them, while what they
CONNECT TO is exactly what the persona catalog describes from the other side.

`BeliefStore.snapshot()` now carries `roles`. `catalog` is classified **forbidden for `acquire`** —
not on cycle grounds, which would exempt it as they do `provenance`, but on meaning (CONVENTIONS §3).
Tier 1 1,325 tests (+31, `tests/test_role_catalog.py`); tier 2 green.

---

## 2026-08-14 — synth v1 phase 1: hosts get a network, and the largest segment discovered was fiction

**Authority:** **implementation (Claude Code)**, at user request ("go with the direct compiler for
D1, start phase 1"). **D1 is a user ruling** (`docs/WORK_ORDER_synth_v1.md`): `aximprint synth`
compiles axRange YAML **directly**, rather than the IR-plus-axRange-transformer route
`docs/WORK_ORDER_axrange_handoff_ir.md` records from 2026-08-11. **D2** (fixed `/24` segment
prefix) was taken as recommended by Claude Code, since phase 1 could not start without it.
**Supersedes:** nothing. `WORK_ORDER_axrange_handoff_ir.md` is **not** superseded — its second
purpose, cross-run drift comparison, is unserved by this and stays open on its own merits.

DESIGN_BRIEF §13 step 3 named a "CIDR/gateway/domain" extractor and only the listen census was ever
built, leaving `twin_config.segments[].cidr` — a **required** axRange field — with no producer.
`src/aximprint/windowing/segments.py` is the CIDR half: `FlowEvent` gains `local_src`/`local_dst`
from `conn.log`'s `local_orig`/`local_resp` (measured 100% populated, and previously discarded
entirely), the window layer accumulates per-entity segment membership, `learners/` asserts
`in_segment`, and `BeliefStore.snapshot()` carries it with its evidence class.

**The finding worth keeping is that the first end-to-end run was confidently wrong, and only real
data said so.** It minted 46 segments; the **largest, at 105 hosts — bigger than any real subnet in
the capture — was `fe80::/64`**, IPv6 link-local. Four more were multicast, IPv4 link-local and the
unspecified address. Every unit test passed throughout, because no unit test knew what a network
is. `segments.is_provisionable` is now a positive gate on unicast host addressing and the honest
count is **38 segments over 197 placed entities, 39 conduits derivable**. The fix deliberately
leaves the LOCALITY verdict alone: a host holding an `fe80::` address genuinely *is* on-link, it is
simply not on a provisioned network, and collapsing "inside" into "placeable" would have been the
same mistake one layer up (`LES-110`).

**`imputed` finally has a producer.** `SEGMENT_EVIDENCE` declares both class and confidence in one
row: a sensor-declared verdict is `observed` at 0.95, and the RFC1918 fallback used when a Zeek
never declared `Site::local_nets` is `imputed` at 0.6 — below `bound_dns`'s 0.7, because a DNS
answer is at least a claim somebody made and address shape is not an observation at all. The class
had been in the assertion vocabulary since v0 with **no code path emitting it**
(`docs/PIPELINE_MAP.md`), so the honesty claim it exists to support was untested end to end until
now. On this baseline the sensor declares `local_nets`, so all 787 rows are `observed` and the
imputed path is exercised only by tests.

**Also corrected: two docs overstated how blocked `tfc` is.** `CLAUDE.md` and `docs/PIPELINE_MAP.md`
both say the traffic-profile half is "not yet buildable end to end" pending a fitting learner layer.
Measured against the consumer, `axrange/o2control/traffic.py`'s `AssignmentSpec` already carries
`intensity`, `schedule` and `seed` — DESIGN_BRIEF §9's three fields — and **all three are
optional**, so a `{role, persona}` profile is valid and deployable today. `tfc` is gated on roles
(phase 2), not on Hawkes fits.

Tier 1 1,294 tests (+38: `tests/test_segments.py`, plus four parser cases in
`tests/test_zeek_ingest.py`); tier 2 green, including transport equivalence across all three
transports with the two new `conn` columns projected into the Parquet archive.

---

## 2026-08-14 — Entity resolution v1: addresses become time-bounded host entities (**closes `Q1`**, **closes `Q9`**)

**Authority:** **implementation (Claude Code)**, at user request ("build `Q1`"). The four forks in
`docs/WORK_ORDER_entity_resolution.md` — drafted 2026-08-04 and explicitly *not ratified to build*
— are **ratified as drafted**: D1 (only host-scoped evidence may merge), D2 (causal, forward-only),
D3 (sequential run-scoped ids), D4 (an unanchored address resolves to itself). None of them
reverses a ratified decision, ratifies a schema, adds a dependency, or shifts the iteration's
direction, so none was a gate (CLAUDE.md "Design authority and the risk gate").
**Supersedes:** the v0 placeholder resolver (`entity == ip`, DESIGN_BRIEF §5.1's "v0 ships with a
trivial resolver"), `EntityResolver.ambiguity_rate()`'s hardcoded `0.0`, `IdentityEvent.valid_for`,
the `identity_bindings` table shape, and identity assertions keyed on the address.

`src/aximprint/windowing/entities.py` is now the real resolver: evidence is **classified before it
is weighted** (`IDENTITY_SCOPES`: `host` / `user` / `referenced`, a kind outside it counted as
`unscoped_identity_kinds`), bindings are intervals with generations, entities are minted
sequentially, and merges are emitted forward as `same_entity_as` assertions that
`BeliefStore.snapshot(as_of)` applies at read time. `Q9`'s change-detection landed in the same file:
`observe()` returns records on **change** — opened / closed — with every still-open binding
published once at end of run as `final`.

**The work order's own premise was stale, and measuring first is what caught it.** It was drafted
against a capture with **no `dhcp.log`** and concluded most identity evidence at this vantage
cannot anchor a host. The 2026-08-11 baseline has **455 `dhcp.log` rows, 158 `ntlm.log` rows, and
86 Kerberos MACHINE-account rows** — host-scoped evidence on three independent sources — so D4's
null test is not vacuous here and the resolver actually resolves. Had the draft been built as
written, its central finding ("v1 is provably an identity function here") would have been recorded
as a result rather than refuted.

**The design decision the measurement forced.** Kerberos calls a host `WORKSTN1$`, NTLM calls it
`workstn1.corp.example.test`, DHCP calls it whatever option 12 said. As raw strings those are three
identifiers and one host torn three ways — and **7 of the 25 resolved entities carry both an NTLM
name and a Kerberos machine principal**, so this is the common case here, not a corner. Each format
is therefore PARSED down to the computer name it contains (`computer_name`,
`kerberos_identifier` — a parse of documented conventions, never a regex over an opaque blob,
`LES-008`) and that is the resolver's key, while the raw value stays on every record as
`bound_to`. The one cross-namespace link (a mac to a host name) comes from **co-observation in a
single sensor row**, not from co-location in time at an address — the inference address reuse would
poison — which is why this needs no "contemporaneous" horizon parameter to tune or defend.

Measured on the real baseline (3 windows, 209,381 `conn` rows):

| | v0 | v1 |
|---|---|---|
| entities resolved | 0 (`entity == ip`) | **25**, from 41 host-scoped bindings |
| `identity.jsonl` rows | 145,319 | **12,914** (−91.1 %) |
| feature archive | 34 MB | **9.6 MB** (−72 %) |
| wall clock | 15.8 s | **14.5 s** |
| peak RSS | 327.2 MB | **316.0 MB** |
| `resolver_ambiguity` | `0.0`, hardcoded | `0.0`, **measured** over 85,228 anchored resolutions |

The resolver is **cheaper than the placeholder it replaces**: 3.07 M `resolve()` calls cost one
dict miss each for an address with no evidence (D4's fast path), and change-based emission removes
far more allocation and I/O than the ~6.5 k retained bindings add.

Everything else that moved, and why:

- **`IdentityEvent.valid_for` is split into `lease_seconds` and `record_ttl`.** One field answering
  two questions is the `LES-015` failure: a DHCP lease bounds how long the BINDING is true, a DNS
  TTL bounds how long the ANSWER may be cached and says nothing about the mapping's lifetime. The
  resolver bounds validity with the first and never the second, so a single field structurally
  could not be consumed correctly. (Work-order finding #4, answered: **not salvageable as one
  field.**)
- **`evidence_class="referenced"` finally has a producer.** It has been in the vocabulary and in
  §6's uniform assertion shape since v0 with **no code path at all** — every assertion this project
  emitted was `observed`, including `bound_dns`, a claim about a host the sensor never saw at that
  address. `SCOPE_EVIDENCE_CLASS` derives it from the binding's scope. "Imputed never masquerades
  as observed" was two-thirds implemented; it is now three-thirds.
- **Identity assertions are keyed on the ENTITY, not the address.** `bound_dhcp` says a host has
  that mac; the address it was seen at is preserved on the binding record. Under D4 this is
  character-for-character v0's output on a vantage with no host-scoped evidence.
- **The entity roster** (`manifest.json`'s `entities` block, and the new `entity_roster` table)
  declares **all four mint paths** — a bound `ent-NNNN`, D4's bare address, the `"?"`
  empty-address sentinel, and `__longtail__`, which the windowing layer mints as an edge `dst`
  with no node row. `LES-017`'s rule is that a published namespace is the union of every path that
  can mint a member; three of these four are invisible from the rows alone. The bare-address path
  is **counted, not enumerated** — unbounded by construction, so listing it would put the very
  uncapped map the caps exist to prevent into the artifact. This does not fix `Q11`; it makes
  `Q11`'s dangling adjacency readable from the manifest.
- **Two caps, not one** (`MAX_HOST_BINDINGS = 4096`, `MAX_NONHOST_BINDINGS = 16384`), enforced at
  insert, both disclosed, and `state.complete` goes false the moment either fires. They are
  separate because the populations are unrelated: **41 host-scoped bindings against 6,416
  user/referenced ones** on the same baseline, 156×. Sharing one cap would let DNS starve the
  resolver's working state. The non-host cap has only 2.6× measured headroom and **will** truncate
  on a longer capture — that is the design working, not a limit to raise reflexively.
- **`entity_roster` is keyed on the run's SOURCE, not a wall-clock run stamp.** A timestamp would
  have forced the table into `NONDETERMINISTIC_TABLES` and out of every cross-run comparison that
  now checks it, while answering a weaker question: two runs over one source mint the same ids and
  are a duplicate, not a conflict; two runs over different sources are the case that needs telling
  apart. What this does **not** fix is recorded as a finding — `node_features`/`edge_features`/
  `assertions` still carry no attribution of their own, a pre-existing store-wide gap.
- **The determinism gate is now derived from `WindowResult`'s own field list**, per `LES-098`'s own
  prescribed fix. `entity_merges` is the fifth channel, and the hand-written key literal would have
  silently narrowed the gate for the **second time in two rounds** — see that lesson's amendment.
- **One `synth/` edit**, against the work order's "do not touch `synth/`" boundary and stated here
  rather than buried: `"__longtail__"` becomes the imported `LONGTAIL_ENTITY`. A literal-to-constant
  swap with no behavioural change; entity *semantics* reaching the twin remains that boundary's own
  round.

**Findings recorded, not fixed** (the work order's own list, answered against the real baseline):

1. **Is any Kerberos principal a machine account?** **Yes** — 86 machine-account rows to 9 user
   rows. The draft's "if none is, v1 is provably an identity function here" does not apply.
2. **Any address reassignment across the capture?** **No REUSE** — 14 addresses, 14 macs, 1:1, no
   address ever held by two hosts. But **one entity spans two addresses**: roaming is present,
   reassignment is not. The churn the interval machinery exists for is still largely unrepresented
   in the only real data we have, which is why the eight synthetic fixtures are the acceptance
   criteria and the real capture is a null test.
3. **What a wide vantage does to the binding map:** 6,414 distinct (answer address, queried name)
   pairs from 144,886 DNS-derived events, over ~3 h at a narrow vantage. Extrapolation is the
   reason `MAX_NONHOST_BINDINGS` exists and the reason its headroom is thin.
5. **Should entity ids become cross-capture pinnable?** Still open. Mac is the only candidate that
   survives a reboot; 14 of them are present. The `--vocabulary` analogue for entity ids is a real
   follow-up and was explicitly out of scope.
6. **34 originators / 12 responders** — 445,321 of 530,549 resolutions (84 %) fell through to D4's
   bare-address path, so most addresses really are external peers rather than hosts on the
   monitored network. Whether they should be *marked* rather than resolved, and what the
   synthesizer does with an entity that is a bare address seen only as a peer, is untouched here.

**Tests:** 1,227 → 1,256. `tests/test_entity_resolution.py` is new and carries the work order's
eight validation cases (a)–(h) verbatim as `test_churn_*`, plus the caps, the ambiguity firing
condition, the roster's four mint paths, the scope→evidence-class mapping, and the snapshot merge.
`scripts/verify_tier.sh` green on all three gates; two passes over the **real** capture are
byte-identical across all four artifacts, with `wallclock` the only differing manifest key.

## 2026-08-14 — `docs/CONVENTIONS.md` §2 declares the **state map**, the first genre this project added rather than inherited

**Authority:** **user**. Raised four times across the 2026-08-13 session as a recommendation and
deliberately not executed — house rules are the user's call — then ruled 2026-08-14.
**Supersedes:** nothing. §2 gains a genre; no existing genre changes meaning.

`docs/PIPELINE_MAP.md` landed 2026-08-13 (CHANGELOG that date) describing *itself* as "outside
§2's genre list", which was honest at the time and is the state a doc taxonomy is not supposed to
stay in — a document whose own header has to explain why the taxonomy does not cover it is
evidence about the taxonomy, not about the document. §2 now names the genre and
`PIPELINE_MAP.md`'s header cites the declaration instead of apologizing for its absence.

**What the genre carries, beyond a name.** A state map is *derived*, and the two obligations
written into §2 both follow from that:

- **It holds no authority of its own.** Where the map and the document governing a claim disagree,
  that document wins. Stated explicitly because the failure mode is a summary quietly becoming a
  second source of truth — which is what `LES-097` describes happening to a compressed task spec,
  one genre over.
- **It carries a drift table, not a promise.** `scripts/doc-stats.py` reads only `CLAUDE.md` and
  `README.md`, so **nothing in a state map is machine-policed**. Every volatile claim names the
  source that governs it and says "no" in the policed column. The discipline has to be written
  where the reader is, or it is not a discipline (`LES-093`).

**One adjacent correction, flagged rather than folded in silently.** The `RUNBOOK_CORPAPCN.md`
clause described it as "outside this five-(now eight-)genre list". That running total was already
unreconcilable against the bullets it claimed to count *before* this change, and adding a genre
would only have made it wronger. Dropped rather than recomputed — a count in prose that nothing
checks is `LES-093`'s own failure mode, and the list is the list.

**Authority:** implementation (Claude Code), executing `Q24`/D5 — a ruling the user delegated
2026-08-12 ("rule on the rest of `Q24`"). The `unrecognized_log_types` addition and the three
review-round fixes below are inside that delegation: none claims new authority, none reverses a
ratified decision, so no `Supersedes:`.

**What changed.** `docs/WORK_ORDER_zeek_stem_coverage.md` D5: "log type X was present in a run's
input and this build does not parse it" used to die with the process — a store with no coverage was
indistinguishable from one with full coverage. Now it reaches the store, on both halves D5 named:

- **`unparsed_log_types`** — a sorted list of stem names, per window, in `WindowResult.health`
  (`windowing/windows.py`). Sourced from a zero-arg provider callable `WindowManager` reads **fresh
  at every `_close()`**, never cached at construction — because the fact it exposes
  (`IngestPlugin.unparsed_stems`, `ingest/base.py`) is discovered lazily, no earlier than the first
  event pulled from a plugin's own `events()`, and `WindowManager` is constructed before that pull
  happens. `cli._pipeline` wires the callable as a closure over its own `plugin` local, so
  `windowing` never imports `ingest` (the sibling-package rule, CONVENTIONS §3).
- **`unrecognized_log_types`** — additive, from code review, **not** in D5's own text and not a
  reversal of it: the subset of `unparsed_log_types` with no `_SKIP` entry, i.e. a stem this build
  declined *by name* versus one it has never heard of. Recorded at write time, not derived at read
  time, because a store consumer may not import `ingest`'s `_SKIP` to recompute the split later
  (CONVENTIONS §3), and because `_SKIP` is this build's own table while the store is read as-of-T
  across many builds — a stem skipped today and parsed tomorrow would re-read history wrong under a
  read-time derivation.
- **Counts, once per run, in `runs`** — two new JSON columns, `unparsed_log_types` /
  `unrecognized_log_types`, written by `StoreSink.close()` from two new `WindowSink` methods
  (`note_unparsed_log_types`/`note_unrecognized_log_types`, default no-op, called by `cli._pipeline`
  exactly once per run, after the event stream is fully consumed and before `close()`, inside the
  same `try:` that already fans `write_window` out to every opened sink).
- **`None` means unaudited, throughout** — the same three-state discipline `ingest_unordered`
  already carries (`LES-006`): `None` is "nobody looked," `[]`/`{}` is "looked, and clean."
- `ZeekSource.inventory_is_complete` (new field on the source ABC, `ingest/zeek/sources.py`):
  whether a source's own `inventory` is a genuine census of every stem present, or only of what the
  transport can represent. `LogDirSource` sets it per instance inside `streams()` — `True` for every
  branch that does a real recursive filesystem walk, `False` only for a single-log-file locator;
  `ParquetArchiveSource` is unconditionally `False`. This is the mechanism `plugin.py::events` reads
  to decide whether `unparsed`/`unrecognized` may be assigned a real dict at all, or must stay `None`.
- `TRANSPORT_DEPENDENT_HEALTH_FIELDS` (`windowing/windows.py`) — the two health-record keys named
  above, exported so `scripts/verify_pipeline_gates.sh`'s transport-equivalence gate can *declare*
  the one place the flat/window-tree and archive transports are allowed to disagree, rather than
  hand-duplicating the field-name set at the comparison site.

**What review found — three defects, each demonstrated by running it, not merely argued.**
1. **The completeness flag defaulted to a lie.** `inventory_is_complete` first landed as
   `bool = True` on the source ABC — the optimistic answer, free to every future transport whether or
   not it had audited anything — and declared once per *class* though completeness is a property of
   which locator *shape* one instance's `streams()` call actually found. Measured: a single-log-file
   `LogDirSource` reported `unparsed_log_types: []` across four real windows while seventeen other
   stems sat unaudited beside the one file it read — a false "every stem accounted for." Fixed:
   defaults `False`; `LogDirSource` assigns it per instance inside `streams()`, reset at the top of
   every call the same way `inventory`/`_notes` already are.
2. **A store from the previous build died after doing all the work.** `_SCHEMA` is
   `CREATE TABLE IF NOT EXISTS`, which handles a new table and silently does nothing for a new
   *column* on one that already exists. `runs` gaining two columns is the first column addition to an
   existing table in this project's history: a store a previous build created committed every window
   of an hours-long run successfully, then raised `BinderException` on the very last statement —
   `write_run`'s own `INSERT`, at `close()` — leaving 292 node rows and 4 health rows written with
   zero `runs` rows for them. Fixed with `BeliefStore._check_schema_matches_this_build`, a guard run
   at `__init__` before any write: it derives the live schema's expected columns by *executing*
   `_SCHEMA` against a throwaway in-memory connection (cached, so it can never itself drift from the
   DDL it checks), compares against the opened store's own `information_schema.columns` for every
   table `_SCHEMA` names — generic over all seven, not special-cased to `runs` — and raises, naming
   the mismatched table, before a single row is written. Not a migration and not a version check
   (`LES-001` still applies): a refusal, not a reconciliation.
3. **The transport-equivalence carve-out had quietly downgraded byte equality to semantic equality
   for the whole `windows.jsonl` file.** The first version of the `TRANSPORT_DEPENDENT_HEALTH_FIELDS`
   carve-out in `scripts/verify_pipeline_gates.sh` compared *parsed* dicts with `==`, which passes
   `{"late_events": 0}` against `0.0` — exactly the int-vs-float drift a DuckDB/Parquet transport
   introduces on every field, not only the two declared ones; re-declared the exempted key set as a
   local string literal instead of importing it; never checked D5's own second trap (the same
   run-level fact must reach every window identically); and passed trivially on two empty files.
   Fixed: the gate now rebuilds each carved key's exact serialized JSON fragment from its own parsed
   value, asserts it occurs exactly once per line, and diffs the **string residual** — real byte
   equality outside the declared exemption — plus the window-count, per-window-agreement, and
   non-empty checks the first version skipped.

**Why.** D5 named two traps for the build — no synthetic `ts`, and the fact must reach every window
of the run, not only the first — and both were real, both caught (proven by tests that pull exactly
one event and by a three-window fixture asserting the identical list on every window). **The third
hazard was not in D5's text at all, and is what most of the review round was about**: D5 assumed a
source can always say what it did not parse. `ParquetArchiveSource` cannot — its own converter
(`scripts/make_zeek_archive.py`) never writes a skipped or unrecognized stem into the manifest, so
`unparsed`/`unrecognized` would read `{}` on every archive-transport run regardless of what the
capture actually contained: not "audited, and clean," but "structurally incapable of finding out,"
and indistinguishable from the honest case by anyone just reading the value. `inventory_is_complete`
closes that — the "no evidence" vs. "evidence of none" distinction (`LES-006`) applied one layer
below the health record, at the transport that supplies it.

**With this, `docs/WORK_ORDER_zeek_stem_coverage.md` is COMPLETE** — build steps 1, 2, 2b, 3, 4 and 5
have all landed; nothing in that document remains open.

**Affected docs:**
- `docs/WORK_ORDER_zeek_stem_coverage.md` — STATUS header LANDED; step 4 marked DONE with the
  third-hazard finding recorded in its own section.
- `CLAUDE.md` — test/lesson counters; `Ingest plugins`, `Zeek transports`, `Windowing + features` and
  `Belief store` rows gain the new fields and the schema guard; `Q24`'s open-questions paragraph
  drops "still outstanding: build step 4."
- `docs/OPEN_QUESTIONS.md` — new `Q30` (the Parquet archive's own converter never records a stem it
  declines to convert, so this transport's completeness gap is structural — the sibling of `Q28` one
  layer downstream, from the converter rather than the sensor).
- `docs/LESSONS.md` — `LES-103`–`LES-106` (new): a completeness flag on a base contract defaults
  closed; a column added to an incubating store needs an open-time schema guard, not a migration; a
  byte-equality gate's carve-out must carve at the byte level and re-check the fixture constraints it
  invalidates; a generator's before-first-`yield` computation is fragile to any wrapper or reordering.
- `docs/PIPELINE_MAP.md` — `windowing/` box gains a health-record line, `store/` box gains a `runs`
  line, "What drifts here" gains a row, `OPENED SINCE` gains `Q30`.
- `docs/SESSION_STATE.md` — handoff rewritten: the work order is complete; what remains is `Q30`,
  `smtp`'s referenced-entity half (`S8`), the unnumbered `tfc` population-vs-per-entity split, `Q1`.
- `docs/SPEC_zeek_metadata_archive.md` — a short pointer to `Q30` added near `manifest.json`'s
  description of `logs`, naming the gap this spec was silent on.

**Affected code:**
- `src/aximprint/ingest/base.py` — `IngestPlugin.unparsed_stems`/`.unrecognized_stems`.
- `src/aximprint/ingest/zeek/plugin.py` — both computed synchronously in `events()` before its own
  first `yield`, reset at the top of every call, gated on `src.inventory_is_complete`.
- `src/aximprint/ingest/zeek/sources.py` — `ZeekSource.inventory_is_complete` (default `False`);
  `LogDirSource` sets it per instance in every `streams()` branch; `ParquetArchiveSource` overrides it
  `False` unconditionally and documents why in its own docstring.
- `src/aximprint/windowing/windows.py` — `TRANSPORT_DEPENDENT_HEALTH_FIELDS`;
  `WindowManager.__init__` gains the two provider parameters; `_close()` reads them fresh and writes
  `health["unparsed_log_types"]`/`["unrecognized_log_types"]`.
- `src/aximprint/sinks/base.py` — `WindowSink.note_unparsed_log_types`/`.note_unrecognized_log_types`,
  default no-op, documented timing (at most once, after the stream, before `close()`).
- `src/aximprint/sinks/store_sink.py` — the one override; stores both, writes them at `close()`.
- `src/aximprint/features/jsonl.py` — `JsonlSink` declines both, by name, with its own reason (not
  `EvidenceSink`'s "no run-scoped artifact" — `manifest.json` has one, and declines this to avoid a
  second transport-dependent-bytes carve-out).
- `src/aximprint/store/belief_store.py` — `runs` gains two JSON columns; `write_run` takes the two
  new keyword parameters, sorted at the JSON boundary (`LES-009`); `_check_schema_matches_this_build`
  and its `_canonical_columns`/`_live_tables`/`_live_columns` helpers; `status()` surfaces
  `last_run_unparsed_log_types`/`last_run_unrecognized_log_types`, parsed back into Python objects.
- `src/aximprint/cli.py` — `_pipeline` wires the two providers into `WindowManager` and calls both
  `note_*` methods on every opened sink, inside the existing try/abort block.
- `scripts/verify_determinism.py` — fixture gains a declared-skip and a genuinely-unrecognized log
  file so both fields exercise their real, populated shape, not only `[]`/`None`; `canonical_windows`
  wires the same providers `cli._pipeline` does.
- `scripts/verify_pipeline_gates.sh` — the byte-level carve-out described above.

**Verification:** 41 new tests (`test_unparsed_stems_is_final_after_exactly_one_event_is_pulled`,
`test_unparsed_stems_is_final_even_when_events_yields_nothing_at_all`,
`test_single_file_locator_cannot_claim_a_complete_inventory`,
`test_unparsed_stems_diverges_between_transports_over_identical_content`,
`test_belief_store_refuses_a_store_whose_runs_table_predates_unparsed_log_types`,
`test_belief_store_schema_guard_is_generic_over_every_table`,
`test_belief_store_schema_guard_closes_the_connection_before_raising`,
`test_pipeline_wires_unparsed_log_types_into_every_window_and_into_runs`,
`test_note_unparsed_log_types_failure_releases_every_opened_sink`, and 32 more across
`test_sinks.py`/`test_windowing_states.py`/`test_zeek_ingest.py`/`test_zeek_parquet_source.py`).
**1227 tests total** (was 1186); `scripts/verify_tier.sh` all three gates green (determinism, CLI
round-trip, transport equivalence — now checked at the byte level through the declared carve-out);
`ruff check` on `acquire` clean; verified against the real baseline
(`/tmp/aximprint-zeek-test/`); `python3 scripts/doc-stats.py --check` clean.

---

## 2026-08-13 — `Q29` closed: a channel for facts with no subject, `ocsp` becomes its first occupant, and review found the determinism gate was never actually checking it

**Authority:** **user ruling.** Asked how build step 3 should handle `ocsp` — admitted to parse by
`Q24`'s third amendment, with an explicit prohibition on inventing a landing shape for it — the user
chose "answer `Q29` now, build the subject-less channel" over three alternatives (declare `ocsp`
skipped citing `Q29`; leave it unrecognized; give it a subject via the `files` join). The design was
proposed by Claude Code the same day and approved by the user before any code
(`docs/WORK_ORDER_subject_less_facts.md`). The three review-round fixes below are implementation
(Claude Code) — none reopen the ruling itself.

**What changed.** `ProfileEvent(ts, parameter, value)` (`events/model.py`) — deliberately **no
subject field**, not a nullable-`ip` variant of `ServiceEvent`: the absence of a subject is the
point, and a reserved sentinel entity (`__capture__`) would only re-encode the same ambiguity as a
*value* every consumer must know to filter, rather than a property the schema itself states.
`windowing/windows.py` gains `PROFILE_PARAMETERS` (parameter → accumulator + unit, same
declared-table discipline as `SERVICE_PROJECTIONS` — a parameter absent from it is counted
(`unmodelled_profile_parameters`), never silently accumulated) and a fourth `WindowResult` channel,
`capture_profile` — named that, not `profile`, so a generic `for k, v in win.____.items()` can never
read identically for the per-entity channel and this subject-less one. `WindowManager._add_profile`
refuses and counts (`profile_values_refused`) a negative value rather than accumulate it. Close-time
summarization uses `sketches.Quantiles` at four fixed points (p10/p50/p90/p99, `_PROFILE_QUANTILE_POINTS`,
passed explicitly with `zip(..., strict=True)` so the labels can never silently desynchronize from
the sketch's own default) plus `n`, the sample count. `store/belief_store.py` gains `profile_facts
(window_start, parameter, stat, value, unit)` — **no subject column**, per-window like
`window_health` (not per-run like `runs`: DESIGN_BRIEF §10's overfitting guard needs the finer
resolution), and deliberately **not** in `NONDETERMINISTIC_TABLES` — a window's profile facts are as
pure a function of (events, resolver state, code build) as any other window-scoped table.
`store_sink.py` writes it inside the existing single-transaction window write.
`ingest/zeek/plugin.py::_ocsp` yields the parameter `ocsp_cache_lifetime` as `nextUpdate −
thisUpdate`, read through `_num` (never `_f`) so an unset timestamp is never coerced to `0.0` and
subtracted into a manufactured delta. `ocsp` is the eleventh and last of build step 3's admits to
land, joining `_PARSERS`/`_COLUMNS`/`KNOWN_PARSED_STEMS`/`_SCHEMAS` once this channel existed for it
to land in — `docs/WORK_ORDER_zeek_stem_coverage.md`'s 28 + 7 = 35 arithmetic is now fully built, not
just declared.

**What review found — three gaps, all fixed before landing.**
1. **The determinism gate had two halves and only one was growing.** `scripts/verify_determinism.py`'s
   `canonical_windows()` omitted `capture_profile` from its rendered payload while
   `belief_store.py`'s own module docstring already claimed the table was byte-deterministic — a
   claim invariant 1 was never actually checking. Fixed by adding the key; the fixture also gained
   real `ocsp.log` rows so growing the channel is exercised, not just parsed.
2. **A negative delta reached a sketch with every health counter reporting clean — `LES-005`
   recurring verbatim, through the sensor's own payload this time rather than the ingest reorder
   buffer.** `_ocsp`'s two timestamps are both genuinely set (`_num` guards that), but nothing gated
   the *sign* of their difference — a responder's own clock skew would have published a corrupted
   cache-lifetime reading into the sketch wearing an observed face. Fixed with a `>= 0` gate in
   `_add_profile`, refused readings counted in `profile_values_refused` rather than silently dropped
   or clamped.
3. **A declared `unit` was computed and then discarded before reaching the store — `LES-015`
   recurring.** `PROFILE_PARAMETERS` always declared `"seconds"` for `ocsp_cache_lifetime`, but
   nothing carried it past the accumulator. Fixed: `profile_facts` gained a `unit` column, looked up
   from `PROFILE_PARAMETERS` at flatten time (`belief_store.profile_facts_rows`, the one place this
   shape is computed — both the insert's guard and `StoreSink`'s row-count summary call it, rather
   than two independently hand-written statements of "how many rows does this window contribute").

**What was measured, as the work order demanded before a second occupant leans on the channel's
assumption.** `ocsp_cache_lifetime`: 1031 events, three distinct deltas overall — but segmented by
`issuerNameHash`, **each issuing CA emits a single constant value**. This is a **CA policy constant**,
not a network-behavioral signal, and it is materially weaker justification for the channel than the
ruling assumed when it was made. Recorded here and in `Q29`'s closure rather than quietly discovered
by whoever builds the second parameter.

**What this deliberately does not do.** It does not write the `tfc` compiler — `synth/` stays a
stub; this lands the store *shape*, not a consumer of it. `snapshot()` does not read `profile_facts`:
aggregating a per-window quantile summary across windows (pool raw values? weight by `n`? something
else?) is a real design question that belongs to whoever builds the `tfc` compiler, and picking one
silently here would pre-commit that design. It does not do the `files`-join alternative (attributing
an OCSP response to the client that fetched it) — declined, not deferred, because it costs a stateful
cross-log join and "arguably invents per-host precision the evidence does not have." It does not give
`ProfileEvent` a subject of any kind, sentinel included.

**Why.** `tfc` (DESIGN_BRIEF §9) is declared to be made of "fitted intensities/schedules/seeds," most
of which are population-scoped by nature, and every existing ingress into the store required a
subject — `ServiceEvent` returns early on `if not ev.ip`, `IdentityEvent` binds an address, the
assertion tuple leads with `subject`. Until this lands, the `tfc` half of the synthesizer has nowhere
to write regardless of what the learner layer eventually fits. After this, that half is unblocked
**structurally**, not **practically** — the compiler and the learner layer that would fit its
parameters still do not exist, and the CHANGELOG says so explicitly because the difference is the
whole honesty claim.

**Affected docs:**
- `docs/WORK_ORDER_subject_less_facts.md` — **LANDED**, with the CA-policy-constant measurement its
  own Validation section demanded.
- `docs/OPEN_QUESTIONS.md` — `Q29` **CLOSED**, pointing at this entry and the work order.
- `docs/WORK_ORDER_zeek_stem_coverage.md` — records `ocsp` as the eleventh admit landing, completing
  step 3's set.
- `CLAUDE.md` — `events`/`windowing`/`store` layer rows gain `ProfileEvent`/`PROFILE_PARAMETERS`/
  `profile_facts`; open-questions paragraph moves `Q29` to closed.
- `docs/PIPELINE_MAP.md` — the `synth/` box's `! the tfc HALF IS BLOCKED — Q29` caveat is rewritten:
  the channel exists, so `tfc` is no longer *architecturally* blocked, though the compiler and the
  learner layer that would fit it remain unbuilt; `store/` box gains `profile_facts`.
- `docs/LESSONS.md` — `LES-098`–`LES-102` (new); `LES-005` amended to state the general form (any
  derived quantity that is a difference of two sensor-supplied numbers needs a domain gate with a
  counted refusal before it reaches a sketch, regardless of which layer supplies the two numbers).
- `docs/OPEN_QUESTIONS.md` also gains an unnumbered follow-on, flagged for a future `Q<n>`: which of
  `tfc`'s declared contents are population-scoped (this channel) and which are per-entity after all —
  nobody has enumerated §9's fields against that split.

**Affected code:**
- `src/aximprint/events/model.py`, `src/aximprint/events/__init__.py` — `ProfileEvent`.
- `src/aximprint/windowing/windows.py` — `PROFILE_PARAMETERS`, `_PROFILE_QUANTILE_POINTS`,
  `WindowResult.capture_profile`, `WindowManager._add_profile`, close-time quantile summarization.
- `src/aximprint/windowing/sketches.py` — `Quantiles.__len__` (the sample-count reader every intended
  v0 replacement tracks natively).
- `src/aximprint/ingest/zeek/plugin.py` — `_ocsp`, `_COLUMNS["ocsp"]`.
- `src/aximprint/store/belief_store.py` — `profile_facts` table, `profile_facts_rows`, `write_window`
  widened to six statements in one transaction, `snapshot()`'s docstring stating the read gap.
- `src/aximprint/sinks/store_sink.py` — writes `profile_facts` via the shared flatten function.
- `scripts/verify_determinism.py` — `canonical_windows()` gains `capture_profile`; fixture gains
  `ocsp.log` rows (two distinct deltas, matching the two dominant real-baseline values).
- `scripts/make_zeek_archive.py` — `("ocsp", "thisUpdate")`/`("ocsp", "nextUpdate")` typed `DOUBLE`
  in `_STEM_TYPES`, the same numeric-accessor safety `conn.duration` already gets.

**Verification:** new tests including `test_ocsp_produces_a_profile_event_never_a_service_event`,
`test_ocsp_never_imputes_a_delta_when_a_timestamp_is_unset`,
`test_ocsp_reaches_window_profile_with_no_node_edge_or_binding_created`,
`test_an_unruled_profile_parameter_is_counted_not_silently_dropped`,
`test_add_profile_refuses_and_counts_a_negative_value`,
`test_ocsp_clock_skew_produces_a_negative_delta_refused_not_published`,
`test_profile_quantiles_are_byte_stable_across_two_runs`,
`test_store_sink_accepts_a_window_with_no_profile_evidence_at_all`,
`test_store_sink_persists_profile_facts_with_no_subject_column`,
`test_ocsp_is_equivalent_across_the_text_and_archive_transports`. 1186 tests total;
`scripts/verify_tier.sh` all three gates green (determinism, CLI round-trip, transport equivalence);
`ruff check` on `acquire` clean; `python3 scripts/doc-stats.py --check` clean.

---

## 2026-08-13 — `stats.log` widened to its real 26 columns; `pkts_proc`/`bytes_recv` join `SensorHealthEvent`, and the health record's blanket `max()` was wrong for a delta counter

**Authority:** **user ruling** — minting `pkts_proc`/`bytes_recv` as new `SensorHealthEvent` kinds,
and reducing them with `sum`, is a decision about what those values mean, made by the user on
2026-08-13 after the measurement below was shown. Widening `_COLUMNS["stats"]` to the log's full
column set is implementation (Claude Code), continuing the same forensic-completeness discipline
build step 2b already applied elsewhere (CHANGELOG 2026-08-13, earlier entry).

**What changed.** `_stats` was investigated as a possible defect — `pkts_dropped`/`pkt_lag` had never
once produced a value on the `harvest`-derived baseline — and found **not broken**: both are `-` in
all 39 `stats.log` rows across the 12-window baseline (`/tmp/aximprint-zeek-test/`) because `harvest`'s
cold tier comes from `zeek -r <pcap>` (offline replay), which has no live link counters to report.
That is a property of the sensor mode, not a parser defect. `_COLUMNS["stats"]` widened from 4 columns
to the full 26 the log actually carries (forensic completeness, same class of fix as build step 2b) —
`_stats` itself still reads only 5 of them (`peer`, `pkts_dropped`, `pkt_lag`, `pkts_proc`,
`bytes_recv`), a deliberate, narrow departure from every other `_COLUMNS` entry (each of which lists
exactly what its parser reads): the cold tier and any Parquet archive built from it should not
silently narrow what a future reader can reach for, even where `_stats` has nowhere to put a value
yet. `docs/SPEC_zeek_metadata_archive.md`'s Columns table and `scripts/make_zeek_archive.py`'s
`_STEM_TYPES` widened to match.

`pkts_proc`/`bytes_recv` join `_stats`'s emitted kinds — the only two `stats.log` fields **always**
set on this baseline, the practical "did the sensor see traffic" signal at this vantage, distinct
from `capture_loss.log`, which remains the only *loss* signal here. `windowing/windows.py` gains
`SENSOR_HEALTH_AGGREGATION` (`SensorHealthEvent.kind` → reducer), replacing two hardcoded
per-window lists (`_pkts_dropped`/`_pkt_lag`) with one `dict[str, list[float]]` keyed by kind,
reduced at close through `_reduce_sensor_health(reducer, values)` — `None`, not `0`, when a window
collected no readings for a kind (the same "unaudited, not clean" discipline `capture_loss_pct_max`
already carries), and a `ValueError` for a reducer string the function does not implement, so an
undeclared reducer can never silently fall through to one already coded. `pkts_dropped`/`pkt_lag`
keep `max` (unchanged behavior, unchanged key names — `sensor_pkts_dropped_max`,
`sensor_pkt_lag_max`); `pkts_proc`/`bytes_recv` get `sum`.

**Why `sum`, checked rather than assumed.** Both are **per-interval deltas**, not cumulative
counters: `pkts_proc` visibly *decreases* within a single window on the baseline (`400053 → 482875`
in one run, then `465161 → 458630` — dropping — in the next run's own rows), which a running total
could never do. Each `stats.log` row reports activity since the *previous* report, so `max` would
report only the single busiest ~5-minute interval as if it were the window's whole traffic; `sum` is
the reducer that actually recovers the window total. This holds because a `harvest`-derived cold
tier's `stats.log` rows are deltas *within* one Zeek run over one ~600s pcap window, and a 1h
aximprint window spans roughly six such runs — summing *across* runs is still correct because each
run's own rows are already deltas. That premise would not hold for a future sensor emitting
cumulative counters instead; re-check it before reusing `sum` for a new kind. The first row of every
Zeek run is a startup artifact (`pkts_proc = 1`, `bytes_recv ≈ 80`, measured) — harmless inside a sum,
noted so a future reader never mistakes it for a parsing bug.

**Affected docs:**
- `docs/WORK_ORDER_zeek_stem_coverage.md` — the `pkts_proc`/`bytes_recv` addition recorded as a
  separate, later addendum to build step 3's own review round.
- `docs/SPEC_zeek_metadata_archive.md` — `stats` row widened from 3 optional columns to 25.
- `CLAUDE.md` — `windowing` layer row gains `SENSOR_HEALTH_AGGREGATION`.
- `docs/LESSONS.md` — `LES-005` amended (see the `Q29` entry above — the same recurrence, generalized
  once for both incidents rather than once each).

**Affected code:**
- `src/aximprint/ingest/zeek/plugin.py` — `_stats` loop widened to 4 kinds; `_COLUMNS["stats"]`
  widened to 26 columns; docstring records the offline-replay finding.
- `src/aximprint/windowing/windows.py` — `SENSOR_HEALTH_AGGREGATION`, `_reduce_sensor_health`,
  `WindowManager._sensor_health` replacing `_pkts_dropped`/`_pkt_lag`.
- `scripts/make_zeek_archive.py` — `_STEM_TYPES` gains `("stats", "pkts_proc")`/
  `("stats", "bytes_recv")`, typed `DOUBLE`.
- `scripts/verify_determinism.py` — two `stats.log` rows exercising both reducers on values that
  would disagree under the wrong one (`pkts_dropped` smaller on the second row, `pkts_proc` larger).

**Verification:** new tests
`test_sensor_pkts_proc_and_bytes_recv_sum_not_max_across_the_window`,
`test_absent_pkts_proc_and_bytes_recv_are_unknown_not_zero`,
`test_every_declared_sensor_health_reducer_is_one_reduce_sensor_health_implements`,
`test_reduce_sensor_health_rejects_an_undeclared_reducer`,
`test_reduce_sensor_health_none_for_no_readings_regardless_of_reducer`. 1186 tests total;
`scripts/verify_tier.sh` all three gates green; `ruff check` on `acquire` clean;
`python3 scripts/doc-stats.py --check` clean.

---

## 2026-08-13 — `docs/WORK_ORDER_zeek_stem_coverage.md` build step 3 lands: ten more Zeek parsers, and review reverses two of `Q24`'s own projection rows

**Authority:** implementation (Claude Code), executing the already-closed `Q24` ruling (delegated by
the user 2026-08-12, amended through 2026-08-13) — parsing ten stems is not a new decision. Two
findings from code review **are** new user rulings, made 2026-08-13: `R1` (decline `smtp`/`sip`'s
service-fact projections) and `R2` (keep `snmp`'s admission as ruled, despite a reopened measurement).
**Supersedes:** two rows of `Q24`'s own closed ruling (`docs/OPEN_QUESTIONS.md`, CLOSED 2026-08-12,
operationalized in `docs/WORK_ORDER_zeek_stem_coverage.md` build step 3's table, amended 2026-08-13)
— its `smtp` and `sip` rows (`("helo",) → serves_smtp, 0.9`; `("user_agent",) → serves_sip, 0.9`)
are reversed by `R1`. Recorded in place rather than rewritten, this document's own house style.

**What changed.** Ten stems join `_PARSERS`/`_COLUMNS`/`KNOWN_PARSED_STEMS`/`_SCHEMAS`: `http`,
`ssh`, `quic`, `snmp`, `tunnel`, `ntlm`, `smtp`, `sip`, `ldap` (kind `ldap_op` — `ldap_search`'s
parser already owns kind `"ldap"`), `x509`. (`ocsp`, the eleventh, landed separately once `Q29` gave
it a shape — see the entry above.) Six earn a `SERVICE_PROJECTIONS` entry at confidence `0.9`
(`http`, `ssh`, `quic`, `snmp`, `tunnel`, `ldap_op` — the same evidence class as `ssl`/`dce_rpc`/`smb`,
an observed protocol exchange; `ssh` is the one judgment call, kept at `0.9` rather than `software`'s
`0.7` because `server` is read off the handshake with the responder itself). `ntlm` rides a new
`IDENTITY_PROJECTIONS` table instead (`bound_ntlm`, `0.9`) — mirrors `SERVICE_PROJECTIONS` one layer
over, closing a silent-default gap review found (`M5`): before this table, `extract_assertions` minted
`f"bound_{kind}"` and picked a confidence from an inline literal
(`0.9 if kind in ("dhcp", "kerberos") else 0.7`) for *whatever* kind showed up, so a new
`IdentityEvent.kind` would have silently gotten both a predicate and a confidence with nothing
declared or counted — the identical failure `SERVICE_PROJECTIONS` exists to prevent, one layer over.
A kind absent from the new table is counted (`unmodelled_identity_kinds`), never silently assigned.
Caught by the same review: `ntlm`'s own inline confidence had been silently inheriting `dns`'s `0.7`
("a third party's claim") despite its own docstring arguing the opposite ("at the address itself") —
corrected to `0.9` (`M4`). `x509` gets no projection at all — it carries no `id.resp_h` whatsoever
(verified against the baseline), so `_add_service`'s own `if not ev.ip: return` already makes it
structurally inert before `SERVICE_PROJECTIONS` is ever consulted.

Thirteen responder-keyed parsers (the pre-existing five plus this round's eight) were factored into
one shared `_responder_service(r, kind, keys)` (`S6`) — the near-identical bodies this replaces were
the one seam in the Zeek-stem table web that was never actually a table, just repetition, so nothing
checked the copies stayed identical. Each stem's attrs tuple is named once, module-level (`S5`), and
reused by `_COLUMNS` — the field list a parser reads and the columns a columnar transport projects
were previously written out twice with nothing checking they agreed.

**Two must-fix transport hazards found and fixed, neither anticipated by this document's own step 3
text:**
- **A Hive-partition-key collision.** The Parquet archive partitions on `log=<stem>/date=<D>`, and
  `smtp.log`/`sip.log` each carry a real `date` field (the message's own `Date:` header) that
  collides with it. DuckDB's default hive-partitioning inference silently **shadows** a same-named
  data column with the partition-derived value, no error raised — verified empirically. A first
  attempt worked around this by excluding `date` from `_smtp`/`_sip`'s columns entirely; **wrong**,
  a permanent forfeiture of a real field for a transport-layer bug. Fixed at the actual defect:
  `ParquetArchiveSource._read` now passes `hive_partitioning=false` (`ingest/zeek/sources.py`) —
  exact for what this reader needs (every partition column it cares about is already read from the
  directory structure, never from a row) and immunizes every column name, present and future, not
  just the two that happened to collide today.
- **A writer-side mirror of `LES-092`.** `scripts/make_zeek_archive.py::_coerce` reprinted a
  JSON-lines vector field's native Python `list` via bare `str()`, emitting a repr
  (`"['10.62.0.2', '198.51.100.7']"`) rather than a comma-joined string — silently violating the
  archive's own documented contract. Caught only because neither field it was tested with
  (`smtp.path`, `x509.san.dns`) was ever projected, so `_add_service` never read the corrupted value
  back out (`LES-091`'s inertness trap, from the writer's side this time). Fixed by joining a
  `list`/`tuple` on `,` before the unset check, mirroring `ingest/zeek/plugin.py::_s()`'s reader-side
  convention exactly. Related: `snmp.duration` and `smtp.date` were each excluded on a first landing
  attempt for the same class of reason (a bare-column-name type collision in `make_zeek_archive.py`'s
  old `_TYPES`) and are now restored, since `_TYPES` is split into `_STEM_TYPES: dict[(stem, column),
  type]` — `("snmp", "duration")` stays VARCHAR while `("conn", "duration")` stays DOUBLE, both safe
  at once.

**`R1` — `smtp` and `sip` move from "parse and project" to "parse, do not project"**
(`windowing/windows.py::_SERVICE_NOT_PROJECTED`). `helo` (smtp) and `user_agent` (sip) are each the
**client's** own self-announced field, but `_smtp`/`_sip` key the event on `id.resp_h`, the
**server** — projecting either publishes the client's claim as a durable property of the server that
merely received it, a keying bug `Q24`'s original ruling never checked for. Measured on the real
baseline: `(50.209.234.65, serves_sip, "friendly-scanner")` — a SIPVicious scanner's own tool name
published as a property of the host it scanned. Every field, including `helo`/`user_agent`, stays
collected on the event regardless (forensic completeness) — this reverses a *keying* judgment, not a
disclosure one. The way back for either needs a client-keyed fact (e.g. `runs_smtp_client`, keyed on
`id.orig_h`), which needs a second subject on the event and overlaps `Q1` — not a re-ruling of this
table. `smtp`'s referenced-entity half is a **named remainder** (`S8`), specified but not built:
`path`/`x_originating_ip` are real IP addresses of hosts the sensor never saw, usable under today's
resolver, and would be the first code anywhere to emit the `referenced` evidence class — but the
subject/predicate/object shape a referenced-only fact takes needs a real design this round did not
attempt, rather than one `Q1`'s eventual resolver work might contradict.

**`R2` — `snmp` stays exactly as ruled.** `display_string` was measured **empty in 7/7** real
baseline rows, tripping the ruling's own stated reopen condition — but the user ruled to keep the
admit: an unset field never enters `attrs` (the positive-gate discipline `_attrs()` already
enforces), so nothing false publishes; the tuple activates automatically the moment a future capture
populates the field; and 7 rows is too small a sample to reverse an admit on.

**Other content measured, recorded rather than acted on:** `ssh.server` is `2717/2864` rows of one
opaque token, `SSH-2.0-7f27de7`, all responders in GitHub's documented range — the evidence-quality
rationale for confidence `0.9` stands (genuinely observed, not spoofed), but the content rationale
(admin-surface diversity, an OS hint) is not borne out at this vantage. `ntlm`: 158 rows → 10 distinct
`(ip, hostname)` bindings, **0** supplied by `dns.log` — the affirmative answer to this document's own
verification item 3. `x509`: 610 events, `ip` empty on every one — provably never creates a phantom
entity. `http`: max 3 distinct hosts per entity, no `SERVICE_MAX_KEYS` truncation on this baseline.

**Affected docs:**
- `docs/WORK_ORDER_zeek_stem_coverage.md` — step 3 marked **LANDED**; `R1`/`R2` and the transport
  hazards recorded in place; `S8` (smtp's referenced-entity remainder) named explicitly.
- `docs/OPEN_QUESTIONS.md` — `Q24` gains a "content measured 2026-08-13" postscript (`ssh`, `ntlm`,
  `x509`, `http` findings above); `Q26` candidate 2 (`snmp.display_string`) updated from "unmeasured"
  to measured-empty-and-ruled-to-keep.
- `docs/SPEC_zeek_metadata_archive.md` — Columns table gains the ten stems; a normative note on
  `hive_partitioning=false`.
- `CLAUDE.md` — ingest-plugins row: 17 → 28 parsed; learners row gains the identity projection table.
- `docs/PIPELINE_MAP.md` — counter line: `17 log types parsed` → `28`.
- `docs/LESSONS.md` — `LES-094`–`LES-097` (new).

**Affected code:**
- `src/aximprint/ingest/zeek/plugin.py` — ten new parsers, `_responder_service` refactor,
  `_COLUMNS` widened.
- `src/aximprint/windowing/windows.py` — `SERVICE_PROJECTIONS` +6, new `IDENTITY_PROJECTIONS`,
  `_SERVICE_NOT_PROJECTED` +3 (`smtp`, `sip`, `x509`).
- `src/aximprint/learners/extractors.py` — identity-binding loop now reads `IDENTITY_PROJECTIONS`
  instead of an inline literal; an unruled kind is counted, never silently assigned.
- `src/aximprint/ingest/zeek/sources.py` — `hive_partitioning=false` on the archive read.
- `scripts/make_zeek_archive.py` — `_STEM_TYPES` (keyed `(stem, column)`, replacing the global
  `_TYPES` for stem-dependent columns), `_coerce` joins `list`/`tuple` before the unset check.
- `src/aximprint/acquire/harvest/zeek.py` — `KNOWN_PARSED_STEMS` +10, ten new `_SCHEMAS` entries
  transcribed from the 2026-08-13 baseline headers; five stale prose-counter sites (module docstring,
  the `KNOWN_PARSED_STEMS` comment, `ScriptPolicy`'s docstring, `DEFAULT_SCRIPT_POLICY`'s live-claim
  parenthetical, and the `_SCHEMAS`-coverage comment) reconciled from `17`/`24` to `28`/`35` — the
  three-bump-one-anchor discipline `LES-093` established had gone stale again the moment step 3
  landed, since nothing re-ran it; caught closing out build step 5 (`docs/WORK_ORDER_zeek_stem_coverage.md`).

**Verification:** new tests including `test_http_collects_credentials_and_projects_serves_http`,
`test_ssh_collects_host_key_and_projects_at_point_nine`, `test_quic_mirrors_ssl_fields`,
`test_snmp_collects_community_and_duration`, `test_tunnel_projects_terminates_tunnel_not_serves_tunnel`,
`test_ntlm_binds_hostname_preferring_dns_name_over_netbios_name`,
`test_ntlm_binding_reaches_the_store_as_bound_ntlm_at_confidence_point_nine`,
`test_an_unruled_identity_kind_is_counted_not_silently_assigned_a_predicate`,
`test_smtp_collects_relay_chain_originating_ip_and_date_but_does_not_project_helo`,
`test_sip_collects_date_but_does_not_project_user_agent`,
`test_ldap_op_is_a_distinct_kind_from_ldap_search`,
`test_x509_has_no_connection_tuple_and_stays_structurally_inert`,
`test_new_stems_are_equivalent_across_transports`,
`test_json_lines_vector_field_round_trips_through_the_archive_writer`,
`test_hive_partitioning_false_prevents_partition_key_from_shadowing_a_data_column`. 1186 tests total;
`scripts/verify_tier.sh` all three gates green (determinism, CLI round-trip, transport equivalence —
including the ten new stems); `ruff check` on `acquire` clean; `python3 scripts/doc-stats.py --check`
clean.

---

## 2026-08-13 — `docs/WORK_ORDER_zeek_stem_coverage.md` build steps 2/2b land: `pe`/`analyzer`/`reporter` skipped by name, the privacy-era field drops actually reversed — and review found the reversal is, for now, structurally inert

**Authority:** implementation (Claude Code), executing two decisions already ratified rather than
making a new one — the `Q24` ruling (user-delegated 2026-08-12, amended through 2026-08-13) and
forensic completeness (**user**, 2026-08-12). Code, tests and docs.

**What changed — build step 2.** `pe`, `analyzer`, `reporter` join `_SKIP`
(`ingest/zeek/plugin.py`) and `KNOWN_SKIPPED_STEMS` (`acquire/harvest/zeek.py`), 4 → 7, kept in sync
by the D1 sync test (`tests/test_acquire_zeek_stem_sync.py`). Each `_SKIP` value is a reason, not a
bare name: `pe` cites `Q26` (candidate 3 contests this skip — `files`' direct connection 4-tuple
defeats the "no entity" reason), `analyzer` cites `Q28` (a **disabled** analyzer is evidence of what
the sensor could not see, a state nothing else here represents), `reporter` is declined outright (no
connection fields anywhere in the log). **No `_SKIP` reason cites privacy** — none of the seven do.

**What changed — build step 2b.** Three parsers that predate forensic completeness still
contradicted it in code, not merely in the docs whose reasons CHANGELOG 2026-08-12 had already
voided:
- `_ldap_search` now collects `base_object`, `filter`, `attributes`, `diagnostic_message` (was:
  `scope`, `result`, `result_count` only).
- `_smb_files` now collects `name`, the filename (was: `action`, `path` — `path` was already there).
- `_ntp` now collects `poll` (was: `version`, `mode`, `stratum` only).

`_COLUMNS` widened to match in `ingest/zeek/plugin.py`, and so did
`docs/SPEC_zeek_metadata_archive.md`'s Columns table for the Parquet path. `plugin.py`'s module
docstring no longer claims parsers drop "the densest PII a log holds when it buys nothing
downstream" — that clause described a bug, not a design, and is deleted. `_SERVICE_NOT_PROJECTED`'s
three reason strings for `ldap`/`smb_files`/`ntp` (`windowing/windows.py`) are rewritten off the void
grounds CHANGELOG 2026-08-12 already named (privacy, "already covered", "no field") onto the real
question each kind now raises. **No decline was flipped** — all three kinds stay unprojected; the
store gains no new service-fact rows this round, only new reasons for the ones it still declines.

**This is the CHANGELOG 2026-08-12 "forensic completeness" entry getting executed, not restated.**
That entry declared these three field drops **void** and logged reversing them as a pending build
step rather than a mid-conversation patch. This entry is that patch landing: the fields are now
actually on the events, not merely un-forbidden in prose.

**What review found — the reversal is structurally inert, today, and step 2b's own text was wrong
about the reason why.** `_add_service` (`windowing/windows.py`) returns on `proj is None` — true for
`ldap`/`smb_files`/`ntp`, all three absent from `SERVICE_PROJECTIONS` — **before it ever reads
`ev.attrs`**. `.attrs` is read in exactly one line in the whole `src/` tree, downstream of that gate.
So the newly-collected fields ride the event and go nowhere: no feature byte moves, and step 2b's own
"expect tier-2 fallout... must be re-baselined" was wrong, confirmed by running tier 2 unchanged
rather than by inspection alone. Worse for this document's confidence than the wrong prediction
itself: tier 2's synthetic fixture (`scripts/verify_determinism.py::build_synthetic_capture`) emits
exactly six log types — `conn`, `dhcp`, `kerberos`, `ssl`, `notice`, `capture_loss` — and **never
exercises `ldap_search`, `smb_files`, or `ntp` at all**, so tier 2 passing was never going to be
evidence either way, wrong prediction or right one. `LES-091`.

**What review also found — step 2's own "independently landable" claim was wrong, and `LES-087`,
recorded from this same document two amendments earlier, should have caught it.** Declaring a skip
grows `KNOWN_SKIPPED_STEMS`, half of `KNOWN_ZEEK_STEMS`, and `tests/acquire/test_zeek_runner.py`
checks `KNOWN_ZEEK_STEMS <= set(_SCHEMAS)` unconditionally — no carve-out for a skip-only stem.
Three `_SCHEMAS` entries were required and are new this round, transcribed from real
`#fields`/`#types` headers read out of the 2026-08-13 `/tmp/aximprint-zeek-test/` baseline and
verified byte-for-byte against them (`analyzer`/`reporter` from the same headers `Q24`'s "Headers
verified" block already records; `pe` from one of the two windows it actually appears in — a real
observed header, not the documented-shape guesswork `dhcp`'s own entry is stuck with, now the only
entry in `_SCHEMAS` without a captured reference). `LES-087` was recorded from this document's D1
work and amended build step 3 (the *parsed*-stem step) the same day — but step 2, a sibling
*skip*-only step amended twice more after `LES-087` already existed, was never re-checked against
it. `LES-090` names the general trap: a lesson recorded from one step of a work order does not
retroactively cover a sibling step the same invariant governs, especially when the lesson's own
worked example (the parsed case) silently narrows how the next reader applies it. `LES-087` itself
is amended in place to state its invariant over `KNOWN_ZEEK_STEMS`'s union, not the parsed-only
retelling.

**Two smaller findings from the review round, both durable:**
- `_s()` (`ingest/zeek/plugin.py`) used to test `v in _UNSET` directly, which raises
  `TypeError: unhashable type: 'list'` the moment a `vector`/`set` field (`ldap_search.attributes`,
  now collected) arrives as a native ordered `list` — the shape both the JSON-lines transport and a
  Parquet `LIST` column hand back, unlike TSV's comma-joined string. Fixed by joining a `list`/`tuple`
  on `,` before the unset check ever sees it, matching Zeek's own TSV convention for the same field
  family (`answers`/`TTLs` already relied on this). `docs/SPEC_zeek_metadata_archive.md` gained a
  matching normative rule: a converter may type an `_attrs()`-only column however it likes **as long
  as it is VARCHAR** — a numeric type is a conforming archive that silently breaks transport
  equivalence, since `str(64.0) != "64.000000"` even though nothing was lost. `LES-092`.
- The SPEC's Columns table had drifted from `_COLUMNS` with nothing checking it — missing the
  `software` row entirely since the 2026-08-11 round, and this round's widened `ldap_search`/
  `smb_files`/`ntp` rows would have gone the same way a second time with nothing to stop it. Added
  the missing row and a new test, `test_spec_columns_table_matches_the_code`, that parses the SPEC's
  own markdown table and asserts it against `plugin._COLUMNS` directly — a **seventh** table in the
  Zeek-stem vocabulary `LES-087` already named six of.

**Why.** Two ratified decisions — the `Q24` skip ruling and forensic completeness — were fully
decided and fully documented as void or pending, but not fully built; this entry is the build
catching up to the docs, not a new decision. The review findings are the more durable part: a "the
reversal changes feature bytes" prediction and a "no `_SCHEMAS` entry needed" claim were both wrong,
in ways neither a passing test suite nor a re-read of the prose would have caught without tracing the
actual consumer path — which is exactly the discipline `LES-091` and `LES-090` generalize.

**Affected docs:**
- `docs/WORK_ORDER_zeek_stem_coverage.md` — steps 2/2b marked LANDED; step 2's "independently
  landable" claim and step 2b's "must be re-baselined" claim both corrected in place, kept visible
  rather than rewritten (this document's own house style); step 5 corrected to name **four**
  `\b16\b` sites in `acquire/harvest/zeek.py`, not two, and to date-anchor rather than bump the one
  that is a 2026-08-06 measurement, not a live claim.
- `docs/OPEN_QUESTIONS.md` — `Q27`'s `smb_files` bullet corrected (`path` was already collected
  before this round; the cardinality delta belongs to `name` alone) and its `ntp` bullet gains the
  keying objection review surfaced (`_ntp` keys the server, `poll` on a mode-3 row is the client's,
  so it survives `Q29` closing); `Q24`'s "Headers verified" block updated now that `pe`'s header has
  actually been read, rather than absent from the sampled window.
- `docs/SPEC_zeek_metadata_archive.md` — Columns table widened for `ldap_search`/`smb_files`/`ntp`,
  gained the missing `software` row, and a new normative VARCHAR rule for `_attrs()`-only columns.
- `docs/LESSONS.md` — `LES-090`–`LES-093` (new, 89 → 93); `LES-087` amended to state its invariant
  over `KNOWN_ZEEK_STEMS`'s union rather than the parsed-only case it was demonstrated with;
  `LES-006` gained a third clause extending "no evidence vs. evidence of none" to a decline reason
  resting on an unmeasured property.
- `CLAUDE.md` — "17 log types parsed, 4 skipped" → "17 parsed, 7 skipped" (the skip half of step 5,
  due now that step 2 landed; the parsed half stays 17 until step 3), 89 → 93 recorded lessons.
- `docs/PIPELINE_MAP.md` — diagram counter "4 skipped" → "7 skipped" (new this session, untracked,
  not yet part of CONVENTIONS §2's doc taxonomy).
- `docs/SESSION_STATE.md` — handoff updated, not committed (solo-mode convention).

**Affected code:**
- `src/aximprint/ingest/zeek/plugin.py` — `_SKIP` gains `pe`/`analyzer`/`reporter`; `_ldap_search`,
  `_smb_files`, `_ntp` widened; `_COLUMNS` widened to match; `_s()` made list-safe; module docstring's
  "densest PII" clause deleted.
- `src/aximprint/acquire/harvest/zeek.py` — `KNOWN_SKIPPED_STEMS` gains the same three; three new
  `_SCHEMAS` entries; four `16`→`17` comment sites reconciled, one of them (the
  `DEFAULT_SCRIPT_POLICY` 2026-08-06 measurement) date-anchored instead of bumped.
- `src/aximprint/windowing/windows.py` — `_SERVICE_NOT_PROJECTED`'s three reason strings rewritten.
- `tests/test_zeek_ingest.py`, `tests/test_zeek_parquet_source.py` — 3 new tests (list-safety,
  archive-transport widening + text/Parquet equivalence, SPEC-table-matches-code), plus stronger
  assertions on the existing skip-announcement and unrecognized-stem tests.

**Verification:** 1154 tests (+3 from this round); `scripts/verify_tier.sh` all three gates —
determinism, CLI round-trip (`--vocabulary`-pinned re-run included), transport equivalence
(flat log directory / window tree / Parquet archive byte-identical); `ruff check` on `acquire`
(source + tests) clean; `python3 scripts/doc-stats.py --check` clean. Every new/changed test
mutation-proven (`LES-031`).

---

## 2026-08-13 — `Q24`'s third amendment: the test was scoped to one *output*. `ocsp` parses, `ntp.poll` was being dropped, and headers finally got verified

**Authority:** Claude Code, from a **user challenge** — that OCSP data could inform a persona /
traffic profile. It could, and the ruling that declined it had never asked. Docs only.
**Supersedes:** `Q24`'s `ocsp` decline (recorded 2026-08-12) and, in part, `Q27`'s `ntp` projection
decline (a user ruling of 2026-08-11 — **flagged, not re-decided**).

**The error.** All fourteen stems were judged against `twin_config` — *does this name an entity or a
durable property* — and never once against **`tfc`**, which DESIGN_BRIEF §9 declares a first-class
artifact whose content is "fitted **intensities/schedules/seeds**." A stem can name nothing new and
still be load-bearing for what the twin must *emit* to look real.

**`ocsp` parses.** `nextUpdate − thisUpdate` is the response's cache lifetime — a schedule parameter
that decides when the next check is due, and one `conn` structurally cannot supply: `conn` sees that
a connection happened, never the validity window explaining its interval. A twin that re-checks per
handshake instead of honoring the cache window gets OCSP volume wrong by a large factor, invisibly.
The original reason ("revocation status of a CA's cert, naming no host here") was **correct on the
facts and wrong on the question**. Tally: **11 parse / 3 skip → 28 + 7 = 35.**

**`ntp` was worse than a wrong projection — the field never arrives.** `_SERVICE_NOT_PROJECTED["ntp"]`
reads *"sensor trivia the synthesizer has no field for"*; `ntp.log` carries **`poll`**, a poll
interval, which is precisely what §9's second artifact is made of. And `_COLUMNS["ntp"]` takes only
`version`/`mode`/`stratum`, so the parser **drops `poll` before anything can weigh it** — a
forensic-completeness violation, and the reason the projection cannot be re-ruled until it is fixed.
Queued in build step 2b with the other field drops.

**Headers verified against the baseline — the first time this round's structural claims were checked
rather than recalled.** Reading `/tmp/aximprint-zeek-test/` is ungated since 2026-08-12, and six
`#fields` lines settled everything at once:

- **`ocsp` confirmed to carry no connection fields at all** — which is why its landing shape is now an
  open question rather than a `ServiceEvent` (see `Q29`).
- **`analyzer` confirmed to carry `uid` and the full 4-tuple**, as `Q28` hoped: a "this host's traffic
  could not be parsed" fact would be attributable, not run-level.
- **`reporter` confirmed to have none** (`ts level message location`), so `Q28`'s sub-question (c) is
  genuinely a separate design rather than the same one twice.
- **`ntp.poll` confirmed present**, and **`files`** confirmed to carry the 4-tuple, `is_orig` and
  `mime_type` (`Q26` candidate 3).
- **`x509` corrected.** Its key column is **`fingerprint`, not `id`**, SANs are four columns
  (`san.dns`/`san.uri`/`san.email`/`san.ip`), cert fields are `certificate.*` dotted names, and there
  are `host_cert`/`client_cert` booleans. This **validates admitting `x509` for SANs** and hands the
  build a `_SCHEMAS` entry nobody would have guessed right — exactly the silent-wrong-fixture failure
  `LES-087` describes.

**Opened `Q29` — the belief store has no channel for a fact with no subject.** `ocsp` is the first
admitted stem in that shape, and the gap is structural, not incidental: `_add_service` returns early
on `if not ev.ip`, `IdentityEvent` binds an address, and the assertion shape starts with `subject`.
But most of what `tfc` consumes — request rates, diurnal shapes, think times, cache lifetimes, seeds —
is **population-level by nature.** If every fact must be keyed on an entity, the traffic-profile arc
has nowhere to put its output. `Q29` notes the symmetry with `Q1`: one is a fact whose subject exists
but cannot be represented, the other a fact with no subject at all. Both surfaced this round, from
opposite directions, and both block real evidence today. The build is told explicitly **not to invent
a subject** to get `ocsp` landed.

**`LES-089` generalized, because its narrow form had now failed three times.** Same shape each time,
each producing a *confident* decline: scoped to observed entities → rejected LDAP/SMTP/x509; scoped by
disclosure → rejected `smtp`/`sip` and stripped fields; scoped to `twin_config` → rejected `ocsp` and
left `ntp.poll` dropped. The rule is now stated over the three declared outputs, with a three-clause
test (a fact no source carries, **or** an entity no source names, **or** a behavioral parameter no
source supplies) and the asymmetry that makes the failure stubborn: **a wrong admit costs parse time
and shows up as an unused table; a wrong decline is invisible, because the evidence simply never
arrives and nothing reports its absence.**

**`analyzer`/`reporter` were re-tested under the new lens and held** — they describe the sensor's own
parsing, not network behavior, so a traffic profile has nothing to fit from them. That is now a
decline tested against all three lenses. Of the fourteen, only **`pe`, `analyzer`, `reporter`** remain
skipped, and every one has a named path back (`Q26`, `Q28`).

**Files touched:** `docs/OPEN_QUESTIONS.md` (`Q24` third amendment + verified-header block, `ocsp` row,
`Q27`'s `ntp` flag, **`Q29` opened**), `docs/WORK_ORDER_zeek_stem_coverage.md` (status, amendment box,
D3, build steps 2/2b/3), `docs/LESSONS.md` (`LES-089` generalized), `CLAUDE.md`,
`docs/SESSION_STATE.md`.

---

## 2026-08-12 — `Q26` widened into the single home for `image:` sourcing: three candidates, and only the one that failed has been measured

**Authority:** Claude Code, from a **user observation** — that a host downloading a Windows executable
is probably a Windows host, and that this could be another datapoint affecting node-config confidence.
Docs only.

**Why widen rather than open `Q29`.** `Q26` was a narrow question about mining `unparsed_version`.
`Q24`'s ruling added `snmp.display_string` as a second `image:` candidate, and the user's observation
added a third; the same question — *where does `image:` actually come from* — was about to be asked in
three places. It now has one home, with a table stating what is known about each source:

| source | yields | coverage | measured? |
|---|---|---|---|
| `software.unparsed_version` | OS token in a User-Agent / SSH banner | 58 hosts, ~1% | **yes** — 187 rows, **zero `OS`-typed rows** |
| `snmp.display_string` (sysDescr) | device description, hits infrastructure `software` cannot see | 4/12 windows | no |
| `files.mime_type` (+ `pe.machine`) | executable-transfer inference: OS family, architecture | unknown | no |

**Nothing is measured except the one that failed.** That is the honest state of `image:`.

**Three corrections the third candidate needed**, each changing the design rather than refining it:

1. **`files.log` is the stem, not `pe.log`** — verified against this repo's own `_SCHEMAS["files"]`:
   `files.log` carries `uid`, the full connection 4-tuple, `is_orig` and `mime_type` **directly, with
   no join**, while `pe.log` has only a file id and cannot name a host at all. So the attributable
   form of the inference is `mime_type = application/x-dosexec` on `files`, and `pe` is a refinement.
2. **`files.mime_type` is three-way where `pe` is one-way.** PE is the Windows executable format and
   Zeek has no ELF or Mach-O analyzer, so `pe` can only ever emit "Windows" and its **absence must
   never be read as evidence against Windows** — an asymmetry that would quietly bias any fusion rule.
   `mime_type` separates `x-dosexec`, `x-executable` and `x-mach-binary`.
3. **Take `pe.machine`, never `pe.os`.** `os` is the PE header's *minimum* target version, routinely
   set low for compatibility — reading it as the host's OS would put dead operating systems into twin
   configs. **That is `Q26`'s own failure mode repeating**: a field whose name promises what its
   content does not deliver, which is exactly how `software` was picked and exactly how it failed.
   `machine` (`I386`/`AMD64`/`ARM64`) is a hard constraint and a twin config field in its own right.

**`is_orig` is the correctness hinge** — a file flowing *to* a host is evidence about it; a file
flowing *from* one is not, since a Linux web server or patch mirror serves Windows binaries all day.
Getting the direction backwards inverts the inference on precisely the hosts where it does most harm.

**Two things this would be the first of**, both recorded rather than assumed:
- **The project's first `imputed` assertion.** Grepped and confirmed: `imputed` appears in `src/` only
  as `FlowEvent.counters_imputed` and window-health counters; `_assert()` defaults to `observed` and
  nothing emits an imputed assertion. Like `referenced` (`Q24`'s amendment), the class is in the
  design with no emitter. The split here is unambiguous — *observed*: a file of type X reached
  `10.1.2.3`; *imputed*: `10.1.2.3` runs Windows.
- **The first fact wanting confidence to scale with observation count.** `SERVICE_PROJECTIONS`
  declares a **fixed** confidence per kind. One download is weak, forty over a week is strong — that
  is a new mechanism to decide deliberately, not a parameter tweak. Proposed below `software`'s 0.7.

**Honest about its value (`LES-089`):** this mostly **corroborates rather than discovers** — a host
pulling Windows binaries is almost certainly also doing NTLM, SMB or DCE-RPC, all stronger and all now
admitted. Its worth is the narrow case: a host that only does HTTP through a proxy, where a downloaded
`x-dosexec` may be the only OS evidence there is.

**Cross-references fixed so the documents cannot disagree silently (`LES-062`):** `Q24`'s ruling table
and amendment box both now mark `pe` as **contested by `Q26` candidate 3** rather than plainly
declined — `files`' direct 4-tuple is what defeats the "no entity" reason it was declined on — and
build step 2 requires `pe`'s `_SKIP` string to cite `Q26`, as `analyzer`'s cites `Q28`. Of the four
skipped stems, only **`ocsp` and `reporter`** are now declined outright.

**Files touched:** `docs/OPEN_QUESTIONS.md` (`Q26` restructured into three candidates + a consolidated
decision list; `Q24` cross-references), `docs/WORK_ORDER_zeek_stem_coverage.md` (build step 2),
`docs/SESSION_STATE.md`.

---

## 2026-08-12 — **Forensic completeness replaces privacy as a governing principle.** aximprint collects everything it observes; axrange owns disclosure

**Authority:** **user**, ratified explicitly and unprompted: *"IGNORE all 'privacy' concerns. Collect
everything as if it's forensic evidence. aximprint MUST NOT worry about privacy as it's just
collecting what it sees — the facts. The consumer end — axRange — will worry about any privacy
issues."* Docs only; no code changed in this entry.

**Supersedes:** the **Privacy** rule in CONVENTIONS §3 and DESIGN_BRIEF §7 (ratified 2026-08-03,
widened 2026-08-10 with the `acquire` merge), `LES-003` in its collection scope, `LES-071` in its
motive, and every privacy-motivated decision downstream of them — `Q24`'s `smtp`/`sip` declines and
its per-parser field refusals (recorded hours earlier the same day), `Q27`'s `smb_files` projection
decline, and the tier-3 gate's stated rationale.

**The principle.** aximprint is an **evidence collector**: it records what the sensor observed, in
full, and refuses no field on disclosure grounds. Cleartext credentials, usernames, filenames, mail
recipients, SIP extensions, LDAP directory names, certificate subjects — collected. The disclosure
decision belongs to **`axrange`**, which knows what its output is for and who will read it.

**The engineering argument, which is why this is a better rule and not merely a different one:**
collection-time redaction is not a neutral act. A field dropped for disclosure reasons is
**indistinguishable downstream from a field that was never observed** — and telling those two apart
is the entire purpose of `evidence_class` (§7) and the stated point of this project. The same round
that ruled `Q24` also built D5 (`unparsed_log_types`) and opened `Q28` precisely to close "saw
nothing" versus "could not have seen" gaps; privacy-dropping was manufacturing that ambiguity on
purpose, one layer down, in the parsers. The two positions were never compatible.

**What this does NOT license**, because neither is a disclosure judgment and conflating them would
lose two real rules:
- **Cardinality bounds stay.** `MAX_KEYS`, `SERVICE_MAX_KEYS`, `TOP_PEERS` are store economics
  (`LES-017`; `Q9`, where `bound_dns` alone was 93% of the store), and truncation is always
  disclosed. `software.unparsed_version` stays off the categorical vocabulary — for cardinality, not
  privacy. A field can be carried on an event and still not become a published feature.
- **Run data is still not a repo artifact.** `LES-003` survives, rewritten from a privacy rule into
  repo hygiene: a belief store or pcap is a build output of a run — large, unreviewable in a diff,
  not reproducible from the repo, permanent once committed. Same reason a `.venv` is not committed.
  And **aximprint's own appliance credentials were never observations at all** (`config.toml`,
  `AXIMPRINT_API_KEY`, `--ca-bundle`): forensic completeness covers what the sensor saw on the wire,
  which does not include this tool's authentication to the appliance.

**Consequences already applied in this entry's files:**
- **`Q24` amended** (see the entry below, and its own amendment box): `smtp`, `sip` promoted from
  skipped to parsed; every "refused, deliberately" field list void; `http`'s
  `username`/`password`/`uri`, `snmp`'s `community`, `ssh`'s `host_key`, `ntlm`'s
  `username`/`domainname` all collected. Combined with the entity-blindness correction in the same
  round, the tally moved from 6 parse / 8 skip to **10 parse / 4 skip** — 27 + 8 = 35.
- **`Q27`'s `smb_files` decline is void** in its privacy half. Recorded rather than silently re-ruled,
  because it was a user ruling of the previous day. What replaces it is a genuine bounding question:
  once `name` and `path` are collected in full, `smb_files` stops being a 3-fact kind.
- **Live code now contradicts the principle**, logged as build step **2b** rather than patched
  mid-conversation: `_ldap_search` drops `base_object`/`filter`/`attributes`/`diagnostic_message`
  ("the log's densest identity content", per its own docstring — and exactly the referenced-entity
  fields the other amendment is about); `_smb_files` drops `name`; `plugin.py`'s module docstring
  advertises dropping "the densest PII a log holds." Reversing these changes feature-file bytes, so
  the determinism and transport-equivalence gates need **re-baselining**, not just re-running — which
  is why it is a build step with tests, not an edit.
- **Tier 3's gate stands on a restated rationale**: cost and blast radius (a live production
  appliance, multi-GB transfers, `podman`), not disclosure. Reading an already-harvested local
  baseline is no longer gated in any sense. **`.claude/settings.json`'s permission lists are
  unchanged** — retuning them is a separate call, and the file's rationale comment is now the only
  place still citing the superseded rule.
- **Two values are unrecoverable.** The appliance hostname and TLS fingerprint redacted from
  `docs/RUNBOOK_CORPAPCN.md` and `docs/VENDOR_API_DEFECTS.md` on 2026-08-10 were deleted, not
  archived, and `LES-071` confirmed they never entered git history. If they are wanted, re-observe
  them.

**Files touched:** `docs/DESIGN_BRIEF_aximprint.md` §7 (the design of record — new **Forensic
completeness** bullet), `docs/CONVENTIONS.md` §3, `CLAUDE.md` (conventions bullet, tier-3 gate,
harness-gate paragraph, risk-gate note), `docs/LESSONS.md` (`LES-003` rewritten to repo hygiene,
`LES-071` re-scoped, `LES-089` added), `docs/OPEN_QUESTIONS.md` (`Q24` status + amendment box,
`Q27`'s voided declines), `docs/WORK_ORDER_zeek_stem_coverage.md` (status, D2/D3 amendments, build
steps 2/2b/3, Verification), `docs/SESSION_STATE.md`.

---

## 2026-08-12 — `Q24` closed by ruling: six stems parse, eight are declared, and every log type this network produces is accounted for by name

> **AMENDED the same day, twice — read the two entries above this one.** The tally below (6 parse / 8
> skip) became **10 parse / 4 skip**, and every field-refusal list in it is void. What follows is the
> reasoning as first recorded, kept because both corrections are about *why* it was wrong.

**Authority:** **Claude Code, ruling delegated by the user** ("rule on the rest of `Q24`"). The user
had already ruled `software` in on 2026-08-11 as a partial D2. Docs only — **no code changed in this
entry**; build steps 2–5 of `docs/WORK_ORDER_zeek_stem_coverage.md` are the whole remaining job.
**Supersedes:** that work order's own **D2 recommendation** (the eight 12/12 stems), drafted by
Claude Code 2026-08-11 from presence data. It was not accepted, and not by a margin — see below.

**The ruling.** Of the fourteen unruled stems, **six parse** (`http`, `ssh`, `quic`, `ntlm`,
`tunnel`, `snmp`) and **eight are declared skipped** (`x509`, `ocsp`, `ldap`, `smtp`, `sip`, `pe`,
`analyzer`, `reporter`). "Leave unrecognized" was rejected for all fourteen. After the build,
`KNOWN_PARSED_STEMS` is 23 and `KNOWN_SKIPPED_STEMS` is 12 — **23 + 12 = 35**, so every stem this
production network emits is accounted for *by name*, and the unrecognized set is empty. That is what
gives a future unrecognized stem any meaning: a permanently non-empty list trains an operator to
ignore the field, which is the failure D1 already named for over-reporting.

**The test that produced a different answer than the recommendation:** a stem earns a parser when it
carries a durable fact about a host or a link **that no already-parsed stem carries.** Not "is it
on-mission" — all fourteen are on-mission by topic, which is exactly why topic could not decide it.
Two of the recommended eight fail the real test:

- **`x509` is redundant, not merely awkward.** `_COLUMNS["ssl"]` already carries the server
  certificate's `subject` and `issuer` into the event model *today*; they are parsed and simply not
  projected. Certificate identity is one line in `SERVICE_PROJECTIONS["ssl"]` away — no new stem, no
  cert-chain join, no `_SCHEMAS` entry. Admitting `x509` would have bought a join to reach data the
  pipeline already has.
- **`ocsp` describes a certificate's revocation status** — a property of a CA's certificate, not of
  any host on this network.

And two that were never recommended go on, **on content rather than frequency**: `tunnel`, because an
overlay a twin cannot see yields a *wrong* topology rather than a thinner one, and `snmp`, because
`display_string` is sysDescr — a first-class device description, landing on the switches and routers
`software.log` never observes. **`snmp` at 4/12 is admitted while `smtp` at 8/12 is refused**, which
is the intended shape: presence says a stem *appears*, never that its content is new, attributable,
or safe to keep. That is `Q26`'s lesson, applied before the fact this time instead of after.

**Every admit names the fields it refuses, and that half is load-bearing.** `http` refuses `uri`,
`referrer`, `user_agent`, `username`, `password` — Zeek's `http.log` carries basic-auth credentials
in the last two; `ssh` refuses `host_key`, the same class as the TLS certificate fingerprints
`LES-003` already names; `snmp` refuses `community`, a credential; `ntlm` refuses `username` and
`domainname`, taking only the `server_dns_computer_name` → `hostname` binding. A projection can
decline to *publish* an attr, but a parser that puts a password on an event has already put it in
the event model — there is nothing downstream of the refusal (`LES-003`, `LES-088`).

**D4 resolved downward.** `ldap` is skipped, `ldap_search` stays parsed. `ldap.log`'s durable content
(`opcode`, `result`, `version`, and `argument` — a bind DN) is strictly *thinner* than
`ldap_search.log`'s, whose own facts `Q27` declined from projection the previous day: *"one query's
outcome, not something the entity IS."* That reason applies verbatim and with more force to
`ldap.log`, so resolving the asymmetry upward would add a parser whose output the store has already
ruled it does not want. Named rather than left implicit: this leaves `ldap_search` parsed with **no
fact reaching the store** — `_add_service` returns early for a declined kind, so only its event count
survives, in `health["event_counts"]`. Retiring that parser is the consistent end-state and is **not
done here**, because it would reverse a user ruling made the previous day; recorded as a cleanup
candidate, a cost rather than a defect.

**D5: "unrecognized" reaches the belief store, split by what is honestly per-window.** Stem **names**
go in the window health record as `unparsed_log_types` (sorted — nothing set-iteration-ordered may
reach an output); row/file **counts** go in the `runs` table, once per run. The split is not
fussiness: `plugin.events()` derives `unparsed` from `src.inventory`, which is **source**-level, and
mirroring a source-level count onto every window would manufacture double-counting. A name *set* is
idempotent under union and cannot. `None` means unaudited, not clean — the discipline
`ingest_unordered` already carries in that dict, for the same reason. Two traps recorded for the
build, both consequences of `unparsed` being source-level and discovered lazily inside a generator:
**do not mint a synthetic `ts`** (a `ts=0` health event creates a window at the epoch; one at the
first event's ts attributes a whole-run fact to window 1 and leaves the rest reading clean — the
very bug D5 exists to fix), and **it must reach every window of the run**.

**Opened, not buried: `Q28`.** `analyzer` is declared skipped as "Zeek's own diagnostics, not
traffic" — a correct classification and *not* the same claim as "of no use here." A **disabled**
analyzer is direct evidence of a protocol the sensor could not see, and D5's inventory mechanism
structurally cannot notice it: no stem is produced, so there is no unparsed stem to name. That is the
"could not have seen" state `docs/WORK_ORDER_axrange_handoff_ir.md` D4 names as unrepresentable.
Filed as a question rather than resolved inline because the baseline's `analyzer.log` **contents have
never been looked at**, and that measurement comes before the design.

**What this ruling deliberately does not do.** It vocabularizes nothing (`NODE_CATEGORICAL` stays
opt-in, per `Q27`), does not touch `SERVICE_MAX_KEYS`, and does **not** claim `snmp` closes `Q26`:
`display_string`'s population on the baseline is **unmeasured**, and the build's tier-3 step must
confirm it before anyone reads it as an `image:` feed. It is the one unmeasured claim in the ruling,
and it is labeled as one. The other five admits are ruled on structure, not on hoped-for content.

**Files touched:**
- `docs/OPEN_QUESTIONS.md` — `Q24` **CLOSED** with the full per-stem ruling and its organizing
  principle; `Q28` opened.
- `docs/WORK_ORDER_zeek_stem_coverage.md` — D2/D3/D4/D5 all **RULED**; build steps 2–5 rewritten
  into the operative tables (fields taken, fields refused, projection, confidence, per stem), plus
  five specific questions the tier-3 run must answer and the note that `KNOWN_ZEEK_STEMS <=
  set(_SCHEMAS)` cannot catch a *present* entry whose columns are wrong.
- `docs/LESSONS.md` — `LES-088`.
- `CLAUDE.md`, `docs/SESSION_STATE.md` — statuses and cross-references. **The prose counters
  ("17 log types parsed, 4 skipped") stay as they are**: they describe the code, and no code changed.

---

## 2026-08-12 — `Q27` closed: service facts reach the belief store, and the volume turned out not to be the problem

**Authority:** **user** — ruled option **A** (generalize the `software` per-entity capped-set
pattern) **with C's per-kind projection table**. Implementation and the measurement that reframed
the question: Claude Code.

**The question was opened on a wrong assumption, and measuring it first is what saved the design.**
`Q27` was framed as "the high-volume kinds need a bounding design — 78,890 `ssl` events in a single
window," implying sketches or a probabilistic structure. Measured on the 12-window baseline, a
service log's volume is **repetition, not cardinality**: collapse ratios of 45x (`ssl`), 38x
(`ntp`), 113x (`ldap_search`), 49x (`dce_rpc`) — and decisively, **no entity anywhere exceeded 13
distinct facts for any kind.** The cap shipped with `software` (32) already covered everything
observed. The simple pattern was sufficient; no new machinery was built.

Also checked, because it was the actual cardinality risk rather than the assumed one:
`NODE_CATEGORICAL` is an **explicit opt-in list**, so node features do not auto-enter the
categorical vocabulary. Landing these facts costs nothing against `MAX_KEYS = 512`, and
vocabularizing them later stays a separate, deferrable decision with its own real cost.

**What changed.** `SERVICE_PROJECTIONS` (public — `learners/extractors.py` reads it, so one table
declares both what is accumulated and what it is asserted as, and a kind cannot be kept under one
name and published under another) maps each admitted `ServiceEvent.kind` to the attrs forming its
durable fact, its predicate, and its confidence. Predicate naming follows the evidence: every
admitted kind except `software` is keyed on the **responder**, so `serves_*`; `software` is keyed
on `host`, which for `HTTP::BROWSER` is the **client**, so `runs_*`. Confidence follows evidence
quality — Zeek's own census 0.95, an observed handshake 0.9, a self-reported and trivially
spoofable banner 0.7.

`_SERVICE_NOT_PROJECTED` declines the rest **by name with a reason**, the discipline `_SKIP`
already applies to log stems: `ntp` (describes the exchange, not the host), `ldap` (one query's
outcome; the serving fact already arrives via `known_service`), `smb_files` (per-action, and `path`
carries share paths — refused on privacy grounds, `LES-003`, for 3 distinct facts from 278 rows).
An omission and a decision look identical in a table that only lists what it keeps; this is the
half that says which is which. A kind in **neither** table increments
`health["unmodelled_service_kinds"]` rather than vanishing.

**A node is materialized only where an admitted kind yields an identifying fact** — evidence the
pipeline has decided to ignore cannot put a host on the map that nothing else observed.

`software`'s key format changed from `HTTP::BROWSER/Chrome 120.0` to the uniform pipe-joined
`HTTP::BROWSER|Chrome|120|0`, and its node feature moved from `software` to `services["software"]`.
Generalizing the pattern is what was ruled; a bespoke formatter for one kind would have undercut it.

**Verification.** 1151 tests, `./scripts/verify_tier.sh` all three steps (`windowing` touched —
`LES-002`). Tier 3 on the real capture: **2,955 service assertions over 1,177 distinct subjects**,
up from 100 over 58 when only `software` was wired; confidence banded exactly as declared; the cap
never fired; `unmodelled_service_kinds` zero in all three windows.

**Affected docs:** `docs/OPEN_QUESTIONS.md` (`Q27` CLOSED), `CLAUDE.md`, `docs/SESSION_STATE.md`.

**Affected code:** `src/aximprint/windowing/windows.py`, `src/aximprint/learners/extractors.py`,
`tests/test_zeek_ingest.py`.

---

## 2026-08-11 — `software` parses, and the ServiceEvent channel that was silently dropping ~93,000 events a run

**Authority:** **user** — "fix the software stem parsing so we can get image info", a **partial D2
ruling** on `docs/WORK_ORDER_zeek_stem_coverage.md` (one stem of fifteen; the other fourteen stay
unruled). Implementation and the scope beyond the parser: Claude Code.

**What changed.** `software` joins the parser table -> `ServiceEvent(kind="software")`, carrying the
structured `software_type`/`name`/version triple plus the raw banner in `attrs`. All six tables of
the Zeek-stem vocabulary moved together — `_PARSERS`, `_COLUMNS` (`ingest/zeek/plugin.py`),
`KNOWN_PARSED_STEMS`, `_SCHEMAS` (`acquire/harvest/zeek.py`) — and **`tests/test_acquire_zeek_stem_sync.py`
verified that on its first real use**, which is exactly what D1 was landed for the day before.
`FakeZeekRunner` got the matching `_SCHEMAS` header entry per that work order's amended build step 3
(`LES-087`), so the fake exercises the new parser instead of feeding it a two-column stub.

**Parsing it alone would have produced nothing, and that is the larger finding.** `WindowResult`
carries `nodes`, `edges`, `identity_delta` and `health` — and no channel a service fact can ride. A
comment in `windowing/windows.py` claimed ServiceEvents "pass through to extractors"; they could
not, and did not. Measured on 2 h of real traffic: **~93,000 ServiceEvents parsed and discarded**
(`ssl` 78,890, `ntp` 10,783, `ldap_search` 1,127, `dce_rpc` 1,084, `known_services` 856,
`smb_files` 278, `smb_mapping` 8). Every assertion in the resulting store traced to a Flow, Identity
or Alert event; **none to a Service one.** `software` was given a home because it is bounded per
entity; the high-volume kinds need a bounding design first (78,890 `ssl` events in one window) and
that is `Q27`, not something to improvise beside a parser. The false comment is corrected in place.

**It does not deliver `image:`, and the record says so.** Measured on the 12-window baseline:
**187 rows, 58 distinct hosts (~1% of observed entities), zero `OS`-typed rows** — 149
`HTTP::BROWSER`, 26 `SSH::CLIENT`, 12 `HTTP::SERVER`. Zeek's software framework reads banners, not
OS fingerprints. What landed is a real installed-software census and an indirect OS hint at best;
the OS token, where it exists, is buried in `unparsed_version` (`Q26`). The raw banner is kept on
the event and deliberately **not** published as a node feature — a full User-Agent is near-unique
per client and would enter the categorical vocabulary as an unbounded dimension (`LES-017`). The
published key is the bounded triple, capped at `SOFTWARE_MAX_KEYS` with truncation disclosed as a
node feature rather than dropped.

**Verification.** 1147 tests, `./scripts/verify_tier.sh` all three steps (determinism, transport
equivalence, CLI round-trip — this touched `windowing`, `LES-002`). Tier 3 on the real capture:
17 log types (was 16), `software` 187 rows -> 187 events, `software` no longer reported
unrecognized, and **100 `runs_software` assertions over 58 distinct hosts naming 31 products**,
zero truncation — the 58 matching the raw-log measurement exactly.

**Also opened.** `Q26` (is the OS token worth mining from `unparsed_version`, and does 1% coverage
change any decision), `Q27` (what bounded shape lets the high-volume ServiceEvent kinds reach the
store), and **`docs/WORK_ORDER_axrange_handoff_ir.md`** — drafted, not started: the user's direction
for a timestamped intermediate representation persisted to a database, schema owned by aximprint,
transformer owned by axRange, serving both point-in-time twin construction and cross-run drift
detection. It records, without deciding, that a schema two repositories depend on is precisely the
condition DESIGN_BRIEF §0.2 deferred versioning until (`LES-001`) — the first thing in this project
that genuinely needs a ratification.

**Affected docs:** `CLAUDE.md` (17 log types; `Q23`–`Q27`; test counter),
`docs/WORK_ORDER_zeek_stem_coverage.md` (D2 partially ruled, with what the ruling taught),
`docs/OPEN_QUESTIONS.md` (`Q26`, `Q27`), `docs/WORK_ORDER_axrange_handoff_ir.md` (new).

**Affected code:** `src/aximprint/ingest/zeek/plugin.py`, `src/aximprint/acquire/harvest/zeek.py`,
`src/aximprint/windowing/windows.py`, `src/aximprint/learners/extractors.py`,
`tests/test_zeek_ingest.py`.

---

## 2026-08-11 — `docs/WORK_ORDER_zeek_stem_coverage.md` D1 lands: the two Zeek-stem lists check each other now, and two silent gaps found closing the seam — no coverage changed

**Authority:** implementation (Claude Code). D1 is the one decision in that work order needing no
user ruling, by its own design — it changes no coverage, only checks a claim that was already true.
D2–D5 remain **blocked on the user**, untouched this round; `Q24` stays open.

**What changed:**

- `tests/test_acquire_zeek_stem_sync.py` (new): asserts `KNOWN_PARSED_STEMS`
  (`acquire/harvest/zeek.py`) == `_PARSERS` (`ingest/zeek/plugin.py`), and `KNOWN_SKIPPED_STEMS` ==
  `_SKIP`. The four names describe one coverage claim from opposite sides of a boundary that
  **forbids the import** (`acquire` must not import `ingest`, AST-walk enforced, CONVENTIONS §3), so
  in `src/` neither pair can ever check the other — they were kept in agreement by hand, with nothing
  checking it, until now. A *test* may import both sides: the layering rule constrains `src/`'s own
  import graph, and `tests/test_acquire_layering.py`'s two walks both visit only paths under
  `src/aximprint` (confirmed by reading it first) — a module under `tests/` sits structurally outside
  either walk regardless of what it imports.
- The skipped pair is checked too, beyond D1's own literal text (which names only the parsed pair) —
  a deliberate scope call, endorsed in review: build step 2 is literally "extend
  `KNOWN_SKIPPED_STEMS`", so leaving that pair unchecked would have made D1 useless for the very next
  step.
- Prep for D2/D3, test-only, no coverage claims: `tests/acquire/test_zeek_runner.py` dropped the
  exact-count pins (`len(KNOWN_PARSED_STEMS) == 16` / `== 4`) — pure obstruction once D2/D3 change
  these sizes on purpose, superseded in value by the new sync test's far stronger property — and kept
  the disjointness + union checks; replaced all four `x509`-as-"the unrecognized stem" usages with a
  synthetic `__not_a_zeek_stem__`, since `x509` is D2's own recommended promotion candidate and all
  four would otherwise have broken with no hint why.
  `tests/test_zeek_ingest.py::test_skipped_types_are_skipped_by_name` now iterates
  `sorted(zp._SKIP)` instead of a hardcoded 4-tuple, so it keeps exercising every skipped stem once
  D3 adds to that set rather than silently under-covering it. `tests/conftest.py`'s enumeration of
  root-level acquire-importing files: four → five.
- `acquire/harvest/zeek.py`: comment-only correction — the `_SCHEMAS` comment claimed `dhcp` falls
  back to `_FALLBACK_SCHEMA`; it doesn't, it has its own entry. Fixed while re-reading the file this
  work order exists to de-drift.

**Coverage is unchanged.** `_PARSERS`, `_SKIP`, `KNOWN_PARSED_STEMS`, `KNOWN_SKIPPED_STEMS` are all
untouched — still 16 parsed, 4 skipped, 20 known. `src/aximprint/ingest/zeek/plugin.py` has an empty
diff. No parser added, no stem promoted, no stem newly declared skipped.

**Two findings, worth more than the test itself:**

1. **A guard's failure message must be computed from the same expression its consumer evaluates.**
   The first cut of the sync test's failure-message helper classified a mismatch two ways only
   (`acquire`-only → under-report, `ingest`-only → over-report). But the actual consumer,
   `ZeekResult.unrecognized_stems()`, tests membership against each side's **union** (parsed ∪
   skipped), not one table alone — so a stem known to *both* sides but filed under different tables
   (reproduced under D3's own scenario: `analyzer` declared skipped on the acquire side, wired into
   `_PARSERS` on the ingest side) leaves `unrecognized_stems()` correctly unaffected, while the
   two-way message reported it as *both* an under-report and an over-report — two confident,
   contradictory, both-false diagnoses for one disagreement. Fixed with a third bucket, MISFILED,
   computed from each side's full opinion rather than the table under comparison, with openable file
   paths and an imperative remedy matching `plugin.py:667`'s own precedent. `LES-085`.
2. **`_SCHEMAS` is the unconnected table in this vocabulary, and it fails silently.** The Zeek-stem
   vocabulary spans **six** tables, not two: `_PARSERS`, `_COLUMNS`, `_SKIP` (`plugin.py`),
   `KNOWN_PARSED_STEMS`, `KNOWN_SKIPPED_STEMS`, and `_SCHEMAS` (`zeek.py`, `FakeZeekRunner`'s header
   table). `_COLUMNS` needs no separate check — already pinned to `_PARSERS` at import. `_SCHEMAS` had
   nothing checking it, and reads through `_SCHEMAS.get(stem, _FALLBACK_SCHEMA)`, a silent two-column
   (`ts uid`) fallback. Demonstrated: add `http`/`ssh`/`x509` to `KNOWN_PARSED_STEMS` with no matching
   `_SCHEMAS` entry, and `FakeZeekRunner` emits two-column logs for all three, `ZeekResult.unrecognized`
   stays **empty** (they're "known" now), and `tests/test_acquire_ingest_chain.py` — the
   harvest→ingest→store→synth gate — keeps passing while exercising none of the new parsers. Exactly
   the failure the module's own comment already named for `dhcp`'s special case ("a fake whose output
   silently yields zero events is worse than no fake at all"), just not generalized into a check
   before now. Guarded by `assert KNOWN_ZEEK_STEMS <= set(_SCHEMAS)`
   (`tests/acquire/test_zeek_runner.py`), mutation-proven. `docs/WORK_ORDER_zeek_stem_coverage.md`'s
   build step 3 is amended to require it explicitly. `LES-087`.

**Why:** D1 exists because two tables describing the same 20-stem claim are duplicated by hand across
a boundary the layering rule itself forbids bridging in `src/` — in sync today (verified 2026-08-11)
by luck plus care, not guarantee, and D2/D3 are about to edit both at once, exactly when drift enters
silently in either direction. Landing the check *before* the coverage change, rather than folded into
it, is what surfaced both findings above while nothing yet depended on them — a misclassifying
diagnostic and an unconnected schema table would otherwise have shipped invisibly inside the very
round meant to close `Q24`. That is the argument, demonstrated rather than merely asserted, for the
sequencing the work order already prescribed.

**Affected docs:**
- `docs/WORK_ORDER_zeek_stem_coverage.md` — D1 marked landed; build step 3 amended to require a
  `_SCHEMAS` entry per newly-parsed stem; a note recording that landing D1 first is what surfaced the
  `_SCHEMAS` gap and four D2/D3 test-obstruction sites, closed in the same round.
- `docs/LESSONS.md` — `LES-084` (a fact duplicated across a boundary that forbids the import can only
  be checked from `tests/`), `LES-085` (finding 1, above), `LES-086` (a test needing "something
  outside the known set" must pin that premise or synthesize it — the `x509` landmine, and the
  hardcoded-copy-of-the-list-under-test mirror image), `LES-087` (finding 2, above).
- `CLAUDE.md` — test counter 1140 → 1143, lessons 83 → 87. `Q24`'s status and "16 log types parsed, 4
  skipped" are **unchanged** — this round changed no coverage, so neither claim moved.
- `docs/SESSION_STATE.md` — D1 done; D2–D5 (and `Q24`) still blocked on the user.

**Affected code:**
- `tests/test_acquire_zeek_stem_sync.py` — new.
- `tests/acquire/test_zeek_runner.py`, `tests/test_zeek_ingest.py`, `tests/conftest.py` — test-only,
  no coverage change.
- `src/aximprint/acquire/harvest/zeek.py` — comment-only.
- `src/aximprint/ingest/zeek/plugin.py` — **untouched, empty diff.**

**Verification:** 1143 tests (1140 + 3 net new: 2 in `tests/test_acquire_zeek_stem_sync.py`, 1 in
`test_zeek_runner.py`'s new `_SCHEMAS` coverage test; two exact-count-pin tests deleted, one renamed),
`ruff check src/aximprint/acquire tests/acquire` clean.

---

## 2026-08-11 — `harvest --from-pcaps`: the Zeek stage runs over pcaps already on disk — and a worker thread that could die silently while the run still reported success

**Authority:** the shape (`--from-pcaps` on `harvest`, not a separate verb) and the scope boundary
(no pcap slicing) are user-ratified 2026-08-11, recorded in the entry directly below this one
alongside D1/D4's original seven-flag rejection set. Three further rulings this round are **user**
as well — explicit calls during the build, not implementation defaults: **(A)** `--fail-fast` is
implemented, not accepted-but-inert; **(B)** D4's rejection set is extended from seven flags to
twelve; **(C)** build step 2's ambiguous "assert that rather than relying on it" is resolved as
"accept any safe stem, warn at plan time if it isn't epoch-shaped," not "require epoch-shaped
filenames." The build itself — D3's implemented shape (which deviates from the work order's literal
text, see below) and every fix found beyond the work order's stated scope (a worker thread that
could die silently, a reporting lie, nondeterministic report ordering, a path-escape hardening) — is
`implementation (Claude Code)`, found and fixed during code review and testing. **(D)** Tier 3 itself
is a fourth `user` action, same day: the user authorized it directly and it ran in the coordinator's
own main session — not the `live-verifier` subagent, which was offered the authorization only at one
remove and correctly declined to act on it, honoring the rule that no relayed agent message is ever
a substitute for the user's own say-so.

**What changed:**

- **`harvest --from-pcaps SRC`**: runs harvest's Zeek stage alone over a directory of `*.pcap` files
  already on disk — no appliance, no `/axapi` call, no credentials, no `Spool`. Wired through
  `_dispatch_harvest` → `_validate_harvest_flags` (one function now owns "required without
  `--from-pcaps`, refused with it," replacing two halves that used to agree only by a comment
  asserting the invariant across a function boundary) → `_cmd_harvest_from_pcaps` →
  `pipeline.plan_from_pcaps`/`pipeline.run_harvest_from_pcaps`, a **separate top-level entry point**
  sharing `_ZeekWorkItem`, `_process_zeek_item`, `_zeek_worker_loop`, `_fresh_zeek_out_dir` and the
  `RunReport` shape with `run_harvest`, rather than a branch threaded through a function whose
  `Spool`/credential/window-plan machinery is entirely inapplicable to this mode (D4).
- **D4 extended to twelve flags (ruling B).** The original seven (`--streams`/`--start`/`--end`/
  `--interval`/`--filter`/`--concurrency`/`--spool-cap`) are joined by `--host`/`--profile`/
  `--api-key-file`/`--ca-bundle`/`--insecure` — verified, not assumed, that `--from-pcaps` never
  builds a `Client` at all (`_harvest_needs_client` returns `False` unconditionally for this mode),
  so none of the five has a receiver either (`LES-021`). All twelve are rejected together, named
  individually in one error message, split internally into a "value flags default to `None`" table
  and a "truthy flags" table so each is tested for "was this supplied" against **its own** existing
  unset-semantics elsewhere in the module (`--filter`/`--host`/`--profile` are unset-when-empty
  where they're actually read; `--api-key-file`/`--ca-bundle` are unset only when `None`) — never one
  blanket `is not None` for all twelve.
- **`--fail-fast` is real (ruling A).** `run_harvest`'s own Zeek queue must always fully drain
  regardless of `--fail-fast` — those pcaps are disk-resident and cap-consuming, and draining (not
  skipping) them is how the space gets reclaimed. `--from-pcaps` has no cap and nothing resident, so
  that justification does not transfer: a new `drain_on_stop` parameter on `_zeek_worker_loop`
  (`False`, the existing behavior, for every `run_harvest` thread; `True` for every
  `run_harvest_from_pcaps` thread) makes a dequeued item get skipped once `stop_requested` is set,
  instead of run through Zeek — so `--fail-fast` on the first `zeek_failed`, and a SIGINT, both
  actually stop the run rather than merely changing its eventual exit code once everything already
  queued has finished anyway.
- **Stem shape (ruling C).** Any `spool.validate_window_id`-safe stem is accepted and Zeeked — no
  epoch-shape requirement, since the ratified scope boundary directs operators to cut pcaps
  externally and point `--from-pcaps` at the result, and those files generally won't be
  epoch-named. `_looks_epoch_shaped` is a local, best-effort heuristic (matching
  `timeutil.format_instant`'s two output forms) used **only** to populate
  `FromPcapsPlan.non_epoch_shaped_window_ids`, printed as a plan-time note (dry-run and real runs
  alike) that the resulting `<out>/zeek/<id>/` subtree will read downstream as a flat directory,
  never a window tree (`docs/WORK_ORDER_multi_window_log_read.md`) — informational, never a reason
  to reject.
- **Content guards at plan time**, reusing what the package already owns rather than inventing
  parallel checks: `path.is_file()` (`Spool._reconcile`'s own guard), zero-byte rejection
  (`runner.plan_resume`'s own rule), and the leading-4-bytes `PCAP_MAGICS` check (`extract.py`'s own
  whitelist — the scope boundary's explicitly-blessed "one permitted inspection" of pcap bytes).
  `extract._leading_magic` and `spool._validate_window_id` are now public (`leading_magic`,
  `validate_window_id`) specifically so `plan_from_pcaps` reuses the real functions instead of a
  second, parallel implementation — the first time either has ever validated input from a
  user-controlled directory rather than input this tool generated or fetched itself.
- **Empty-source guard.** `--from-pcaps` at a directory with no `*.pcap` files is a positive-gate
  `FromPcapsSourceError`, not a zero-window success (CONVENTIONS §3); `--from-pcaps ""` is rejected
  too, before it can silently resolve to the current working directory (`Path("")` normalizes to
  `Path(".")`, and `.is_dir()` is `True` — `LES-077`).
- **`max_windows_for(yes)`** (`src/aximprint/acquire/windows.py`) replaces three hand-spelled copies
  of `sys.maxsize if yes else DEFAULT_MAX_WINDOWS` (`cmd_pcap`, `plan_harvest`, `plan_from_pcaps`)
  with one function.
- **`ZeekRootNotWritableError`/`_check_zeek_root_writable`**, new: `run_harvest` used to reach an
  unwritable `<out>/zeek` via a bare `mkdir` that surfaced as a raw `OSError` — an "unexpected
  error," exit 4 — rather than the same clear, Phase-1, no-work-attempted-yet diagnosis
  `SpoolRootNotWritableError` already gave the spool root. Shared by `preflight()` (for
  `run_harvest`) and `run_harvest_from_pcaps`, which has no spool root to check at all but still
  needs this one.

**D3 as landed deviates from the work order's literal text — recorded so `H-7` doesn't re-derive
it.** The document says the spool becomes "non-owning" for `--from-pcaps` entries, "admitted for
accounting but never deleted." What shipped constructs **no `Spool` at all**. Two independent
reasons: a failed Zeek run would leave a RESIDENT entry with accounted bytes and no file under
`<out>/spool`, so `check_drift()`/`verify()` would fire a false `SpoolDriftError` on any run with one
Zeek failure; and, decisively, `Spool.admit()` **blocks** for backpressure against `cap_bytes` —
admitting 9.45 GB of supplied pcaps (2026-08-11's own live-run figure) against the 10 GB default cap
with nothing ever releasing them wedges the run until `stall_timeout` (30 minutes). Not a near-miss
— a real hang. `spool=None` sidesteps the class of bug by construction. The per-entry predicate
itself (`_ZeekWorkItem.owned`, required, no default) is unchanged and is the right primitive: `H-7`'s
mixed run (orphans it owns beside pcaps it doesn't) is already supported by the shared worker
(`LES-081`).

**Beyond the work order's scope — found during code review and testing, all landed in this round:**

- **A pre-existing product bug: `_zeek_worker_loop` had no exception guard.** A worker thread that
  raised died silently and the run reported success — measured, `planned=3 zeeked=2 zeek_failed=0
  failed=0`, exit 0, a window vanished with nothing that looked wrong. Reachable today by three real
  routes, no mutation needed. Now catches `BaseException`, records it via `stats.record_worker_fatal`,
  keeps draining, and re-raises once every worker thread has joined; `check_from_pcaps_accounting`
  (`FromPcapsAccountingError`) is a second, structural belt-and-suspenders check —
  `zeeked + zeek_failed == planned` unless the run stopped early on purpose (`LES-076`).
- **A reporting lie: `released` used to increment alongside `zeeked` on every Zeek success**, correct
  only while "Zeek succeeded" and "the pcap was released" were the same fact. The ownership predicate
  broke that equivalence; a real `--from-pcaps` run would have printed `released=N` having deleted
  nothing. `record_released()` now increments only at the actual `spool.release()` call site; an
  unowned success emits a distinct `zeeked_kept` event, printed as "zeek ok — source pcap retained"
  (`LES-075`).
- **A determinism defect in the report.** `failed_windows`/`zeek_failed_windows` were reported in
  thread-completion order — a stress test (8 workers × 24 failing windows × 12 runs) produced **12
  distinct orderings**. Both are now `sorted()` in `_RunStats.to_report`, matching the discipline
  `unrecognized_stems` already used.
- **Path-escape hardening on `_fresh_zeek_out_dir`**, which deletes stale `*.log` files before reuse.
  Its guard was copied verbatim from `Spool._path_for`'s unresolved `parent ==` check, which is sound
  there only because that function interpolates a suffix (`f"{window_id}.pcap"`), so `..` can never
  be the final path component. Here the bare stem *is* the final component:
  `(zeek_root / "..").parent == zeek_root` is `True` (pathlib does not normalize `..`), so the copied
  guard admitted the one traversal it looked like it defended against. Now compares **resolved**
  paths; mutation-proven (`LES-079`).
- **`_ZeekWorkItem.owned` is required, no default.** A permissive default (`True`) would make
  "forgot to consider ownership at a new call site" silently equivalent to "this pipeline owns it and
  may delete it" — the exact mistake the field exists to prevent (`LES-078`).
- **A flaky test caught before it shipped.** The SIGINT-drain test's hand-measured `zeeked=1` for the
  interrupt scenario depended on an unsynchronized race that resolved to `zeeked=0` just as
  reproducibly (30/30 each way). Synchronized, then re-measured, before pinning it (`LES-080`).

**Why:** closes the gap that forced 2026-08-11's earlier live verification to bypass the CLI entirely
and drive `ZeekRunner` from an inline script to Zeek the 12 pcaps `acquire pcap` had just extracted —
"an obvious operation with no CLI, four undocumented preconditions, and a silent failure mode if you
get the last one wrong."

**Verification.** Tier 1: **1140 tests** (up from 1048), `.venv/bin/python -m pytest -q -m "not
slow"` green in 17.7 s. Tier 2: `./scripts/verify_tier.sh` — all three steps pass (determinism
invariants; the CLI round-trip; three-way transport equivalence), `ruff check src/aximprint/acquire
tests/acquire` clean, `ruff check .` still exactly **113** pre-existing findings confined to the
non-acquire half (confirmed unchanged by this round — `CLAUDE.md`'s scoping note is accurate, not
stale). `tests/test_acquire_layering.py`: **10/10**. Every number in this paragraph was re-run and
confirmed directly by this doc-writer pass, not transcribed unverified.

**Tier 3 ran, 2026-08-11, same day — and passed.** The user authorized it directly; it ran in the
coordinator's main session rather than through `live-verifier` (offered the authorization only at
one remove, it correctly declined to act on it — no relayed message is a substitute for the user's
own say-so). Command: `aximprint acquire harvest --from-pcaps /tmp/aximprint-live-verify-2026-08-11
--out <fresh>` — the 12 real, un-Zeeked pcaps from this session's own live `acquire pcap` run,
compared against the already-Zeeked `/tmp/aximprint-zeek-test/` baseline this work order recorded.

- **12/12 windows ok, `zeek_failed=0`, `released=0`, exit 0, 35.06 s at 4 workers** (inline-script
  baseline: 35.4 s).
- **35 distinct stems — the stem-name SET diff against the baseline is EMPTY**, and the 15
  unrecognized are the exact set `Q24` already named. `Q24`'s data is now independently reproduced
  on a second harvest of the same pcaps.
- **Every window logged `zeek ok — source pcap retained`** — the `zeeked_kept` path (`LES-075`)
  exercised on real data for the first time.
- **The ownership predicate held.** A smoke test over 2 copied pcaps proved it first; then all 12
  real source pcaps survived the full run — manifest SHA-256 identical before and after,
  **9,448,439,118 bytes unchanged**.

**Two corrections to the recorded baseline, found while comparing against it — the honest kind of
tier-3 result: it passed, and the number it was compared against had two errors.**

1. **"~85 MB"/"111× reduction" were wrong.** `/tmp/aximprint-zeek-test/` holds 13 top-level
   directories, not 12 — `w1` is a stray leftover (30 files, 6,262,418 bytes), not one of the 12
   real windows. Measured over the 12 real windows only: baseline **79,068,773 bytes**, this run
   **79,066,413 bytes** — a delta of 2,360 bytes (0.003%). True reduction **≈119.5×**
   (119.496×/119.500×), not 111×. Reverified independently: 79,068,773 + 6,262,418 = 85,331,191 ≈
   "85 MB"; 9,448,439,118 / 85,331,191 ≈ 110.7× ≈ "111×" — confirms exactly how the wrong pair
   arose. `docs/WORK_ORDER_harvest_from_pcaps.md` is corrected everywhere it quoted either number;
   the earlier CHANGELOG entry below (already committed as `4af16e9`) is left as originally written,
   per this file's own practice of recording history rather than rewriting it — this entry is the
   correction of record. (`LES-082`)
2. **Zeek's own output is not byte-reproducible across two runs over the same pcap.** With `uid`
   stripped and records sorted, `dns.log`/`ssl.log` are byte-identical between the two runs; the
   byte deltas are fully explained by Zeek's randomly generated, variable-length (15–18 char)
   connection UIDs plus record ordering. `conn.log` additionally differs by 2 records out of 15,037
   (0.013%), likely a connection-flush edge case at a pcap-window boundary. Record counts are
   otherwise identical everywhere checked. This does not contradict aximprint's own determinism
   claim (`LES-002`: same input logs → same outputs; the pipeline gates read fixed logs) — but
   "same pcap → same logs" does not hold across a re-harvest, which starts to matter now that
   `--from-pcaps` makes re-harvesting routine. Opened as **`Q25`**.

**Affected docs:**
- `docs/WORK_ORDER_harvest_from_pcaps.md` — STATUS to LANDED (tiers 1–3, all done); D4's table
  extended to twelve flags; D3 annotated with its as-landed shape and why; build step 2's ambiguity
  resolved; Verification section carries the full tier-3 result and both corrections, and every
  other "85 MB"/"111×" mention in the document is fixed alongside it.
- `docs/LESSONS.md` — nine new entries, `LES-075`–`LES-083` (seven from the initial round, plus
  `LES-082`/`LES-083` from the tier-3 addendum: the stray-directory measurement error, and Zeek's
  own UID/ordering non-reproducibility as a diffing gotcha).
- `docs/OPEN_QUESTIONS.md` — `Q25` opened (Zeek's cold-tier output is not byte-reproducible across a
  re-harvest of the same pcap); `Q24` gains a one-line note that its stem list was independently
  reproduced.
- `CLAUDE.md` — test counter 1048 → 1140, lessons 74 → 83, `Q25` cross-referenced, `--from-pcaps`
  named in the CLI-verbs paragraph and the Acquisition layer-table row.
- `docs/SESSION_STATE.md` — handoff rewritten twice this round: first tier 1–2 verified/tier 3
  pending, now tier 3 done — with the `w1`-stray-directory and Zeek-UID findings recorded in
  "gotchas" so nobody re-derives either.
- `docs/PLAN_metadata_harvester.md` — `H-7`'s row gets a forward-pointer to the `_ZeekWorkItem.owned`
  primitive this round built and the `LES-081` hazard to avoid re-deriving when `H-7` is picked up.

**Affected code:** `src/aximprint/acquire/cli.py`, `src/aximprint/acquire/extract.py`,
`src/aximprint/acquire/harvest/pipeline.py`, `src/aximprint/acquire/harvest/spool.py`,
`src/aximprint/acquire/windows.py`; `tests/acquire/test_cli_harvest.py`,
`tests/acquire/test_extract.py`, `tests/acquire/test_pipeline.py`. 2,141 insertions, 81 deletions
across 8 files.

---

## 2026-08-11 — three operator-reported `acquire` defects: an unlisted verb, a migration that ate its own diagnostics, and a permanent failure retried as a transient one

**Authority:** user (both defects reported from a real host, running the installed console
script); implementation (Claude Code) for the fixes.

Neither was found by the suite. Both were found in the first minutes of someone trying to use the
verb the merge had just landed.

**1. `aximprint --help` did not list `acquire`.** It printed
`{ingest,features,status,synth}` and mentioned `acquire` only in the epilog beneath. `acquire` is
dispatched by intercepting argv before the top-level parser is built, so a subparser for it was
judged decorative dead code — a test pinned that reasoning explicitly. The operator read the brace
list, concluded `acquire` was not a subcommand, and reported it. That is the correct reading of
that output: the choice list is where a CLI says what its verbs are.

`acquire` is now a registered choice, and not a decorative one — the stub carries
`fn=_dispatch_acquire`, `add_help=False`, and a `REMAINDER` catch-all, so it forwards to the same
dispatcher the interception uses. The two paths now agree by construction rather than by comment,
none of acquire's own flags are re-declared (the thing `_dispatch_acquire`'s docstring forbids,
and the only thing that could drift), and `aximprint acquire --help` still renders acquire's own
parser verbatim — asserted. The epilog, no longer needed to announce the verb's existence, was
trimmed to the two things the choice line has no room for. `LES-073`.

**2. A moved config left its own contents pointing at the old directory.** The operator followed
`_warn_deprecated_config_path`'s advice — `mv ~/.config/xtract ~/.config/aximprint` — and the next
run failed on a bare `No such file or directory` for a `password_file` still written as an
absolute path under the old location. `config.toml` holds absolute paths to sibling files
(`password_file`, `api_key_file`), read exactly as written and resolved relative to nothing, so
relocating the directory orphans both.

**Not a code bug in the resolution logic — a diagnostics bug, and the diagnostics failed in the
specific way that matters:** they got *worse* as the operator got closer to correct. Before the
move the deprecation notice explained the situation on every run; after the move the config
resolved at its new home, the notice fell silent, and what remained was an errno naming a
directory the operator had just deleted on the tool's own advice. Two fixes, one for each side:

- The deprecation notice now names what the move will break — `password_file` and `api_key_file`
  are read exactly as written, so either one pointing into the old directory keeps pointing there
  afterward. (Phrased without a second "deprecated": the warn-once regression test from the
  2026-08-10 entry counts that word, and a message is not a licence to break the test that guards
  the message.)
- A missing secret file now gets a positive-gate hint: fires only when the path is under the old
  directory, the old directory is **gone**, and the new one **exists** — the signature of "already
  moved," never for a host that mistyped a path or has not migrated. It names the path to try.

`LES-072`. Nothing is rewritten automatically; the 2026-08-10 read-only ruling stands.

**3. `SSLError` was retried four times, then reported as one word.** The operator's first symptom,
before either of the above: `POST /axapi/login failed after 4 attempt(s): SSLError`. Two separate
defects behind that one line.

`requests` models a TLS verification failure as `SSLError`, a **subclass of its own
`ConnectionError`** — so `_do_request`'s `except (ConnectionError, Timeout)` claimed it and gave it
the transient-fault retry budget: four handshakes and 3.5 s of backoff to re-learn that a
certificate the appliance will present again is still not verifiable. The docstring three lines
above the bug already stated the principle it was breaking — "`418` (and every other 4xx) is never
retried — it's a stable answer, not a transient failure (`LES-035`)". At HTTP level the stable
answers arrive as status codes you opt *into* retrying; at transport level they arrive as
exceptions you must opt *out* of, and the default is the one that bites. Now a `TlsVerificationError`
(a sibling of `ConnectionFailedError`, deliberately not a subclass — that type means "persisted
past the budget," this one means the budget was never spent) raised on the first attempt, from an
`except` clause placed **ahead** of the tuple. That ordering is load-bearing; it is commented as
such and pinned by a test asserting the attempt count under `max_retries=2` — a budget that exists
and must go unspent, since `max_retries=0` could not fail.

The message was also just the exception class name — correct, and for a real reason (`LES-039`:
`requests`' own text embeds the full URL, hostname and query string included, and this string
becomes `Failed.detail` in `manifest.jsonl`, which must never carry either). But one word,
"SSLError," spans a no-SAN certificate, an untrusted CA, an expired certificate and a protocol
mismatch, which have different fixes — and this appliance's documented no-SAN defect
(`docs/VENDOR_API_DEFECTS.md`) needs `--insecure` specifically, a security-relevant choice an
operator should make knowingly rather than by trial. The message now names both remedies and which
condition each answers, still interpolating neither `exc` nor the URL: **naming a fix costs no
privacy, because a flag name is not a hostname.** `LES-074`.

**Why the suite could not have caught any of the three.** The help defect was pinned as correct by
a passing test — the suite asserted the bug. The migration defect needs a host that *transitions*
between config states; every test redirects `HOME` to its own `tmp_path` and builds a config from
scratch, so none inhabits the intermediate state a real migration passes through. The retry defect
was invisible because every existing transport test drives `ConnectionError` or `Timeout` directly;
none had ever raised the subclass, so nothing observed that the taxonomy quietly widened
"transient" to include something permanent. That is the same structural blindness that hid the
double deprecation warning one entry above, found the same way — by running it — which makes twice
in two rounds that live use has caught what 1000+ synthetic tests could not.

**Verification:** 1048 tests (8 new: 4 on the migration hint, 1 on the interception surviving the
new choice entry, 3 on the TLS branch including a guard that plain `ConnectionError` still spends
its budget), all three `CLAUDE_VERIFY_STEPS` green. Every fix mutation-proven — neutering the hint,
the added warning sentence, or the `SSLError` clause fails exactly the tests written for each.
The migration scenario was reproduced end to end against a throwaway `HOME`; the TLS path against
a fake transport.

**Tier 3 ran, and `acquire pcap` ran live for the first time in this project's history.** Against
the real appliance: `acquire --insecure streams` exit 0, 12 streams, ~50 days of coverage —
authentication still works after this round's changes to `auth.py`/`client.py`/`errors.py`/`cli.py`.
**Fix #3 confirmed on the appliance that produced the original report:** without `--insecure`, one
attempt and **0.304 s** (was four attempts and ~3.5 s of backoff), exit 4, message naming both
remedies, `attempt(s)` absent, no hostname — verified by cross-checking every string value in the
resolved config against the message. **Fix #2 was NOT exercised live** and remains proven only by
its unit tests and a throwaway-`HOME` reproduction: the operator's config had already been
corrected, so the stale-path state never arose. Recorded as unverified rather than passed.

`acquire pcap`, 12 windows × 600 s over a 2 h span, all 12 streams: **12 written, 0 empty, 0
failed, 0 skipped**, 9.45 GB in 135 s (~70 MB/s), exit 0. Output is 12 flat per-window files named
by start epoch, plus `manifest.jsonl` (one header row + 12 per-window rows). Findings worth
carrying: the appliance returns **nanosecond-resolution pcap** (`0x4d3cb2a1`), which nothing in
`harvest`'s Zeek path has been tested against; per-window size varied **5.4×** (321 MB–1724 MB) at
constant window duration, which the disk governor's planning should not assume away; every
per-window manifest row carries a `task_guid`, confirming the appliance-side task records the gate
exists for; and `manifest.jsonl`'s stated no-hostname invariant **holds** on real output (checked
against the resolved host, boolean only). This partially closes `GAP-011` for the `pcap` path.

**Harvest's Zeek stage then ran over those 12 pcaps — real Zeek, real capture, first time.**
`PodmanZeekRunner` against the pinned `docker.io/zeek/zeek:8.0` (resolved 8.0.9): **12/12 windows
ok, 0 failed**, 35.4 s wall at harvest's own default of 4 workers, 9.45 GB of pcap in, 85 MB of
logs out — a **111× reduction**, which is the number that justifies the cold tier's whole shape.
The nanosecond-resolution pcap flagged above is a non-issue: Zeek read it at ~43 MB/s per worker
without complaint.

**It could not be done through the `harvest` verb, and that is itself a finding.** `harvest` has no
resume-from-existing-spool: `plan_resume` lives in `runner.py` and serves `pcap` only; harvest's
`pipeline.py` has no equivalent (`H-7` is unbuilt). Pre-seeding `<out>/spool` with the 12 pcaps and
running `harvest --dry-run` still plans **12 windows to extract** — so running the verb would have
re-pulled the same 9.45 GB and created 12 more appliance task records for pcaps already on disk.
The Zeek stage was therefore driven directly through the `ZeekRunner` protocol. **The verb
end-to-end (extract→Zeek→reap) is still untested live.**

**What the run found: real traffic produces 35 distinct Zeek log stems; the plugin knows 20.** All
16 of `KNOWN_PARSED_STEMS` appeared in real traffic — the parser table is fully exercised and holds
nothing speculative, which the synthetic fixtures could not establish. But **15 stems are
unrecognized**, eight of them in 12/12 windows and squarely on-mission: `http`, `ssh`, `x509`,
`ocsp`, `quic`, `ldap`, `ntlm`, `software`. `ldap_search` is parsed while `ldap` is not, though both
appear in every window. Opened as **`Q24`**, with the split between discovery-relevant traffic logs,
intermittent protocol logs (`smtp`, `snmp`, `sip`, `tunnel`, `pe`) and Zeek's own diagnostics
(`analyzer`, `reporter` — which look like undeclared `KNOWN_SKIPPED_STEMS` members) left for the
user to rule on. Nothing is lost on disk, the cold tier keeps every log; this is about what reaches
the event model, and therefore what the synthesizer can ever cast.

Two estimation errors from the same round, both worth keeping because both were the confident kind.
Projected volume was **~35 GB against an actual 9.45 GB** — a 3.7× overshoot from extrapolating the
catalog's 50-day stored total to a uniform hourly rate; the abort threshold was never approached,
but the number was wrong, not conservative. And a pre-flight gap check reported "0 gaps
intersecting the window" when the catalog exposes gaps only as `{"count": N}` with **no bounds** —
the loop had nothing to iterate and printed zero. An empty iteration reported as a clean result is
`LES-020`'s shape exactly. Gap-versus-window overlap is not determinable from this catalog; all 12
windows returned data, so nothing was lost, but that was luck rather than the check working.

**Affected docs:**
- `docs/LESSONS.md` — `LES-072` (migration advice must name what the migration invalidates; a
  deprecation path is a state machine and every state needs a message), `LES-073` (an intercepted
  verb still belongs in the choice list), `LES-074` (a permanent failure modeled as a subclass of a
  transient one inherits the retry policy; check what inherits from a base class before catching it).
- `docs/OPEN_QUESTIONS.md` — `Q24` opened from the live evidence.
- `docs/WORK_ORDER_harvest_from_pcaps.md` — **new, READY.** `harvest --from-pcaps` so the Zeek
  stage can run over pcaps already on disk without an appliance, since this round's verification
  had to bypass the CLI entirely to do it. Shape and the seven-flag mutual-exclusion set are
  user-ratified; the spool-ownership fix (D3) is the part that matters — harvest deletes a pcap
  after Zeek succeeds with it, which is correct for downloaded pcaps and destroys supplied ones.
- `docs/WORK_ORDER_zeek_stem_coverage.md` — **new, OPEN**, blocked on a user ruling. Closes `Q24`,
  and closes a duplication seam found while drafting it: `KNOWN_PARSED_STEMS` (in `acquire`) and
  `_PARSERS` (in `ingest`) describe the same 16 stems across a boundary that **forbids the import**,
  kept in agreement by hand with nothing checking it. In sync today, verified; but the `Q24` build
  changes both at once, which is exactly when drift enters — and drift makes
  `ZeekResult.unrecognized` lie in whichever direction it goes. A test importing both is the fix,
  and it needs no ruling, so it can land first.
- `CLAUDE.md` — test counter 1040 → 1048, lessons 71 → 74, `Q24` cross-referenced.

**Correction made during this round:** the first version of the deprecation warning named
`ca_bundle` as a path written inside `config.toml`. It is not — `config.toml` supports
`default_profile` and per-profile `host` / `user` / `api_key_file` / `password_file`; `--ca-bundle`
is a CLI flag only. Fixed in the warning, `LES-072` and this entry before landing. A migration
notice that sends the operator hunting for a key that does nothing is the same defect class the
notice was being fixed for.

---

## 2026-08-10 — tier 3 runs for the first time; the deprecation warning it caught, deduped

**Authority:** user (requested the live verification explicitly); implementation (Claude Code) for
the fix.

**The gated live verification ran, and passed** — the first time in any round.
`aximprint acquire --profile <p> --insecure streams --json` against the real appliance: exit 0,
12 streams, a real `POST /axapi/login`, and the credential resolved **through the deprecated config
fallback** on a host where `~/.config/xtract/config.toml` genuinely exists and the new location
does not. That is the one path 1039 synthetic tests could not reach: every unit test redirects
`HOME` to its own `tmp_path` and writes a config it invented, so the suite proved the logic handles
configs the tests imagined, never the operator's real one.

`--insecure` is required against this appliance and is a documented vendor defect (self-signed
certificate with no SAN, `docs/VENDOR_API_DEFECTS.md`); `--ca-bundle` does not help, because the
SAN mismatch is what fails, not the chain of trust. Not a regression from the merge — the first,
verification-less attempt failed the same way before any flag was passed.

**What it caught:** `_warn_deprecated_config_path` fired **twice per invocation**. `resolve_host`
and `resolve_credential` each resolve the config location independently and on purpose, so one CLI
run calls `_resolve_default_config_path` twice and printed the identical notice twice. Deduped by
path, warn-once-per-process.

The obvious fix was wrong and is worth recording: threading a resolved `config_path` through the
CLI puts `resolve_credential` on its explicit-path branch, whose `searched` list names ONE location
instead of the four it must disclose when nothing resolves. Quieting the noise that way would have
silently regressed the "no credential found" error — so the dedupe lives in the warning, not the
resolution. Mutation-proven: neutering the dedupe fails the new regression test, which drives both
entry points the way the CLI does.

**Still untested live:** `acquire pcap` (writes to disk, creates appliance task records) and
`acquire harvest` (podman + Zeek). 1040 tests, all three `CLAUDE_VERIFY_STEPS` green.

---

## 2026-08-10 — step 12: `../xapi-pcap-extract` archived and removed — and the orientation file that never heard about it

**Authority:** user (step 12 of `docs/WORK_ORDER_merge_xtract_acquire.md` — *"archive the source
repo — only after validation passes. Do not delete it"* — ratified with the merge 2026-08-10);
implementation (Claude Code) for the execution and for this entry.

**Written late, on 2026-08-11.** The archival ran at the tail of the merge round, between
`15b372b` and `b3ad012`. It was recorded in the work order and in `docs/SESSION_STATE.md` — but
not here, and the second half of this entry is about what that cost.

**What changed:**

- **`../xapi-pcap-extract` no longer exists.** Archived to
  `/home/rocky/xapi-pcap-extract-archived-2026-08-10.tar.gz` (2.6 MB); the tree was removed only
  **after** a restore from that tarball was verified at 16/16 commits — in that order, which is
  what the work order's "do not delete it" flag was protecting.
- The tarball is the **only** copy of three docs-only commits (recorded in `docs/SESSION_STATE.md`
  as `b42ab97`, `ae8ddf7`, `bfe115d`) that `git filter-repo` drops by design. Those hashes are
  unverifiable from this tree by construction — the tarball being the sole copy is precisely the
  claim. Everything else came across as the four carried-through commits under `de0f841`.
- **`CLAUDE.md` corrected.** Its "Current state" paragraph asserted the opposite — that
  `../xapi-pcap-extract` "is **not yet archived** — the work order holds that step until this
  round's validation passes — but is down to 5.5 MB" — while pointing the reader at
  `docs/SESSION_STATE.md`, the one file that contradicted it.

**Why the stale claim survived, which is the part worth keeping:** it outlived a later commit that
edited the very same file (`ab92f53`, some seven hours after the archival), because nothing
connected the two. Three mechanisms that normally catch drift each missed it for a different
reason. **No diff showed it** — archiving a sibling directory is a filesystem action outside this
repo, so `git status` here was clean throughout. **No counter policed it** — `doc-stats` checks
numbers against source, and this was a claim, not a number; the file passed `--check` while
asserting something false. **And the citation it hung on was empty**: the sentence sources itself
to "CHANGELOG 2026-08-10", so a reader who followed the reference to verify would have found the
merge recorded and the archival absent, with nothing to signal the gap. An unrecorded change is
not a small omission when a summary file cites the record it was omitted from — the citation keeps
working, and keeps returning the wrong answer.

Found by `/catchup` reconciling the handoff against the repo, not by any gate. The handoff was
right; the always-injected orientation file was wrong; only comparing them surfaced it.

**Affected docs:**

- `CLAUDE.md` — "Current state", the pcap → Zeek paragraph: the not-yet-archived clause replaced
  with the landed fact, keeping a pointer to `docs/SESSION_STATE.md` for the reason that still
  matters (the tarball's docs carry pre-renumbering `LES-`/`Q<n>` identifiers that misresolve
  against this repo's — extract it outside this tree).
- **Not** corrected here: `docs/DESIGN_BRIEF_aximprint.md` §0.1's amendment block still describes
  `xtract` (`../xapi-pcap-extract`) as an external tool that "is merging into this repo," present
  tense. It reads as a dated amendment rather than a live claim, and editing the design of record
  is its own change — but it is now the last place in the live doc set narrating the two-repo
  world as current.

---

## 2026-08-10 — xtract lands as `acquire/`: the merge build completes, docs merged, privacy rule widened

**Authority:** user (D3, D4, D7, D8 ratified 2026-08-10, at the start of the build; the `xtract`
user-visible-naming retirement ratified 2026-08-10, landed and committed separately as `a7ba73c`
during this entry's own doc-writer pass) for the decisions; the execution — build steps 4–10, two
code-review fix rounds, and two doc-writer passes — is `implementation (Claude Code)` against
`docs/WORK_ORDER_merge_xtract_acquire.md`, whose steps 1–3 landed already (`3032c8d`, `777f9c5`,
`de0f841`).

**What changed:**
- **Steps 4–8 (code, reviewed by code-writer/tester/code-reviewer, this entry does not re-verify
  the code):** the `acquire`→`events` layering rule is now an AST-walk test, checked both directions
  and mutation-proven; `aximprint acquire {streams,pcap,harvest}` dispatches via argv interception
  from the top-level CLI, no `xtract` alias; the `[acquire]` packaging extra, Python floor `>=3.11`;
  a repo-wide network guard (`tests/conftest.py`) denying non-loopback egress, added after a test was
  found reaching a live production appliance for real; two fix rounds closing findings in the merged
  test suite (root `tests/__init__.py`, a corrected `.gitignore` fix protecting `tests/acquire/data/`,
  `.claude/settings.json` gates extended to cover both `aximprint acquire pcap/harvest` invocation
  spellings and `podman`).
- **The `xtract` name is retired from the user-visible surface**: `prog=` (1 site) and every
  stderr/stdout prefix (39 sites — `"aximprint acquire[ <verb>]: "`; 35 by the initial measurement,
  4 more stdout status-line prefixes found during the work, left unrenamed would have shipped a CLI
  whose errors said one name and whose run summaries said another) now say `aximprint acquire`, never
  `xtract`; `XTRACT_API_KEY`→`AXIMPRINT_API_KEY`; `~/.config/xtract/config.toml`→`~/.config/aximprint/config.toml`
  with a read-only deprecation fallback for the old credential locations, precedence new-wins-old,
  nothing auto-migrated. Landed and committed separately, `a7ba73c` — code, not re-verified by this
  entry beyond the doc-writer's confirmation of exact prefix/message strings needed for the runbook
  fix below.
- **Step 9 — `LES-003` rewritten.** Drafted for Zeek metadata only; now explicitly covers raw pcap,
  credential material (the profile-keyed config file, its mode-0600 password file, the API-key env
  var, `--ca-bundle`), and appliance identity (hostnames, addresses, TLS certificate CNs and
  fingerprints), as four named categories rather than one implicit one. Old scope kept below the
  rewrite, per this file's own "never delete, supersede in place" discipline.
- **Step 10 — the docs merged**, sourced only from the pre-filter-repo staged copy (already
  renumbered), never from `../xapi-pcap-extract`: `docs/AXAPI_CONTRACT.md`, `docs/VENDOR_API_DEFECTS.md`,
  `docs/PRODUCT_GAPS.md` moved as-is; `docs/RUNBOOK_CORPAPCN.md` moved with one redaction (below);
  13 `docs/WORK_ORDER_*.md` joined this repo's; the two vendor PDFs landed in `docs/vendor/` (D8);
  `docs/CHANGELOG_acquire.md` holds xtract's own 51 KB history, structurally unmodified (D6) plus one
  new prefatory note explaining its un-renumbered `Q1`–`Q4` citations; `docs/PLAN_metadata_harvester.md`
  landed with its status blockquote, §6 table and §7 questions corrected — `H-0`/`H-6` **deleted**,
  `H-5` **optional, not blocked**; `docs/OPEN_QUESTIONS.md` gained a historical block (`Q19`/`Q21`/`Q22`,
  `Q20` deliberately unused — xtract's own closed `Q1`/`Q3`/`Q4`, folding in `Q2`) and one genuinely
  new live question, `Q23` (does `IdentityEvent.kind` need the `FlowEvent.state`/`state_class`
  open-vocabulary pattern applied to it — found while checking whether the merge actually dissolved
  every item in the now-superseded `docs/BRIEF_for_aximprint_event_model.md`, which did **not** move
  into this repo). `docs/LESSONS.md` gained xtract's 29 lessons as `LES-033`–`061` (Scope-token
  hygiene applied on landing — see below) plus 9 new entries, `LES-062`–`070`, capturing what this
  round itself found. `docs/CONVENTIONS.md` §2 gained the three new doc genres this landed (formal
  spec, plan, runbook) and §3/§5 record the layering-rule-as-test and the naming-collision ruling.
  `CLAUDE.md` gained an `acquire` layer-table row and reconciled every §0.1/CLI/gate/doc-list mention
  of the merge from future tense to landed.
- **Scope-token hygiene** (the round's most important finding — `LES-062`): renumbering `LES-NNN`
  was not sufficient by itself. Audited and fixed: `docs/LESSONS.md` `Scope:` tokens for the 29
  incoming lessons, qualified to `acquire/<file>` wherever a bare filename (`windows.py`,
  `manifest.py`, `client.py`, `errors.py`, `cli.py`, and everything under `harvest/`) collides with
  aximprint's own tree or could plausibly collide with a directory nobody tracked in `git` (the bare
  `zeek` token, pre-dating this merge, was found matching `acquire/harvest/zeek.py` via a *gitignored*
  `metadata/zeek/` directory — `LES-063`); stale `tests/test_*.py`-shaped Scope citations rewritten to
  `tests/acquire/test_*.py`; two pre-existing entries' over-broad bare `tests/` token narrowed since it
  now also matches the new suite. Verified empirically with `scripts/inject_lessons.py` against files
  from both halves, not by hand-tracing the matcher.
- **One privacy finding, disposed and reported, not silently landed:** `docs/RUNBOOK_CORPAPCN.md`
  carried a real appliance's TLS certificate SHA-256 fingerprint (out-of-band MITM-check material,
  §1 precondition 3) — redacted on landing, replaced with instructions to re-obtain it out-of-band.
  The certificate's Common Name appeared unredacted in both that file and the sendable
  `docs/VENDOR_API_DEFECTS.md` — reported to the user rather than sanitized unilaterally, since
  `VENDOR_API_DEFECTS.md` is one of the two vendor-facing documents this round was told not to edit
  without a human call. **The user ruled: redact it from both**, replaced with
  `<appliance hostname, redacted>` and a note that it is obtainable out-of-band — done in a follow-up
  pass to this entry.
- **Self-correction, same pass:** the doc-writer's own write-up of the finding above — not the chat
  report, which correctly never quoted the value — pasted the certificate's Common Name into this
  entry and into `docs/SESSION_STATE.md` while explaining where it appeared unredacted. Both are
  "docs" and "CHANGELOG," the exact nouns `LES-003` names; caught by the orchestrator, fixed by
  removing the value from both (confirmed via `git log --all -S` that it never entered version-control
  history), and recorded as `LES-071` so the failure mode — a privacy finding's own write-up inherits
  the rule the finding is about — is checked before the next one, not just this one.
- **D3 closed:** `docs/RUNBOOK_CORPAPCN.md`'s `xtract`-branded commands, expected output, and
  credential-location instructions rewritten to `aximprint acquire` now that the naming retirement
  (`a7ba73c`) confirmed the surface. Not a blind substitution — of 45 lines matching `xtract` as a
  substring, ~40 were the tool name (renamed) and ~5 were the English words "extract"/"extraction"
  (left alone); dated/historical prose describing the standalone-project era and the 2026-08-05 live
  probe was left as it was. Two code-verified corrections found in the process, unrelated to naming:
  the `pcap` summary line's expected-output block was missing the `reextracted=` field (`LES-037`,
  predates this round) and one code-path citation pointed at a `CLAUDE.md` section
  (`xtract`'s own, superseded) that does not exist in this repo's. `docs/PLAN_metadata_harvester.md`
  and the 13 landed work orders were checked for the same class of defect and found clean — their
  `xtract` mentions are inside `DONE`/historical Build and Validation sections describing what was
  built and verified under that name at the time, which is the exception the task itself named, not
  a forward-looking "run this" instruction; not rewritten, so as not to risk corrupting a historical
  record other lessons (`LES-047`) already cite.

**Why:** `docs/WORK_ORDER_merge_xtract_acquire.md` D5's own rationale generalizes past `LES-NNN`:
a merge that renumbers one shared-document identifier namespace and calls the job done leaves every
other namespace — `Q<n>`, `LESSONS.md` `Scope:` tokens, doc-genre self-references, a runbook's
appliance-identifying detail — exactly as exposed as before. This round's job was to find and close
those, not just the one everyone thinks of first.

**Affected docs:** `CLAUDE.md`; `docs/CONVENTIONS.md`; `docs/LESSONS.md`; `docs/OPEN_QUESTIONS.md`;
`docs/AXAPI_CONTRACT.md`, `docs/VENDOR_API_DEFECTS.md`, `docs/RUNBOOK_CORPAPCN.md`,
`docs/PRODUCT_GAPS.md`, `docs/CHANGELOG_acquire.md`, `docs/PLAN_metadata_harvester.md`, 13
`docs/WORK_ORDER_*.md`, `docs/vendor/*.pdf` (new); `docs/SESSION_STATE.md`.

**Affected code:** none from this doc-writer pass — `src/aximprint/acquire/`, `src/aximprint/cli.py`,
`tests/acquire/`, `tests/conftest.py`, `tests/__init__.py`, `.gitignore`, `.claude/settings.json`,
`.claude-workflow.env`, `pyproject.toml` all changed in the concurrent code-writer pass this entry
records but does not re-verify.

## 2026-08-10 — §0.1 amended: the pipeline never sees a packet, the repository will

**Authority:** user — ruled in session: the two projects are "supposed to be modules within the same
project," `xtract` "becomes the plugin, no archive seam." This reverses a framing decision ratified
up front, which is a gated change (CLAUDE.md "Design authority and the risk gate"); the ruling is the
gate being passed, and this entry is the record.

**Supersedes:** DESIGN_BRIEF §0.1 as ratified 2026-08-02 — *"aximprint is NOT a sensor … owns no
capture path and never sees a packet."* Amended, not deleted: the property survives, its enforcement
changes.

**What changed, and why it is a strengthening rather than a concession.** The old §0.1 was a claim
about the **repository**. `xtract` (`../xapi-pcap-extract`) pulls pcap off an Axellio Xpress
appliance over `axapi` and runs a pinned Zeek over it; merging it in as a sibling package makes the
repo-level claim false. But the repo boundary was never what made the decision load-bearing — that
was the fact that everything from `ingest` downward speaks the event model and nothing else, and it
was guaranteed only by an accident of packaging. An accident of packaging evaporates the moment the
code arrives. Restated as a layering rule — **`acquire` must not import `events`** — it is checkable,
and CONVENTIONS §3 already checks it. The seam moved from a repo boundary to a package boundary and
acquired a check on the way.

**What must not be read into this.** The producer's own brief
(`../xapi-pcap-extract/docs/BRIEF_for_aximprint_event_model.md` §2) rests on a fact the merge does
not touch: **Zeek is a stopgap.** The Xpress platform will produce metadata at line rate through a
different API, and the event model is the only part of the pipeline stable across that swap. One
repo does not make the plugin boundary negotiable — it is the whole point of the boundary. Xpress
arrives as a new `acquire` path plus a new parser table, with nothing between them renegotiated.

**What this deletes.** The archive seam was never an interchange contract by necessity; it was
economics — extraction runs at ~13.9 MB/s single-flight, Zeek runs once, aximprint re-reads many
times. Persistence stays; the *contract* goes:

- The producer's `H-0` (an agreed `SPEC_metadata_archive.md`) and `H-6` (manifest + atomic publish)
  are **deleted, not blocked**. `H-5` (the Parquet converter) was the item blocking that project and
  is now **optional** — an internal, regenerable read-cache — because the 2026-08-10 multi-window
  round made the Zeek cold tier directly readable.
- Brief §9's requests 1, 3, 4 and 5 (publish protocol, spec ownership, two version numbers,
  `(sensor, stream)` keying) dissolve into internal choices with no ceremony. Request 2 (late-row
  policy) survives as a property of the data and is already handled by that round's merge. Request 6
  (extension rule) survives and is already answered by precedent — `_CONN_STATE_CLASS`: closed
  vocabulary, unmapped values counted rather than bucketed.
- **`Q15` is dissolved rather than answered.** It existed because this was the first artifact whose
  producer lived outside the repo and could drift independently of its reader. With the producer
  inside, a mismatch is a build error, and `LES-001` holds unchallenged.

**Costs priced but not yet paid** — recorded so the merge round does not rediscover them:

- **`LES-NNN` identifiers collide.** aximprint holds `LES-001`–`LES-032`; `xtract` holds
  `LES-001`–`LES-029`. Same identifiers, entirely different lessons, and both repos auto-inject by
  path scope through the same hook. Renumber one side mechanically **before** anything else in the
  merge, or institutional memory corrupts in both directions. Same class for `Q1`–`Q18` against
  `xtract`'s `Q1`–`Q4`; its `GAP-001`–`GAP-017` has no counterpart here.
- **A runtime dependency.** `xtract` needs `requests`, plus `podman` as an external tool and a
  Python ≥3.11 floor (stdlib `tomllib`). This repo's list is deliberately thin (`duckdb`, `pyyaml`)
  and adding one is itself a gated decision.
- **Privacy blast radius.** `LES-003` was written for Zeek *metadata*. `xtract` handles credentials
  (a profile-keyed config with a password file) and **raw pcap**. The merged repo's privacy rules
  need writing for that, not inheriting.
- **Tier 2 must stay unattended.** `xtract`'s tests need `podman` and network mocks; this project's
  gate runs the pipeline twice for determinism. Both repos already run workflow template v7 — same
  hooks, same `doc-stats`, same `SESSION_STATE` — so the scaffolding merges cleanly; the gate
  composition does not.

**Affected docs:** `DESIGN_BRIEF_aximprint.md` §0.1 (rewritten), §0.2 (note on why the amendment
strengthens it; stale `§8` cross-reference corrected to `§11`), §2 ("never even sees packets"
qualified to the pipeline); `CLAUDE.md` (framing decision 1, and the pcap→Zeek paragraph);
`OPEN_QUESTIONS.md` (`Q15` dissolved); `SPEC_zeek_metadata_archive.md` (status: an internal cache
format, no longer an external read contract; the cold tier is now a first-class input, not only a
rebuild source).

**Not done here.** The merge itself — package layout, the move, dependency and privacy rules, doc
reconciliation, test fusion — is a separate round and needs its own work order. No code moved.

---

## 2026-08-10 — Multi-window Zeek log-directory read (closes `Q18`)

**Authority:** implementation (Claude Code) — drafted at the user's request
(`docs/WORK_ORDER_multi_window_log_read.md`); the goal (a tree of per-window Zeek log directories
becomes a first-class source, k-way merged on `ts`) was ratified by the user in the task that
produced the work order. **`M1`'s memory-vs-repair-quality trade (below) is a user ruling** — the
one this round needed; every other decision (`D1`–`D7`, `M2`–`M4`, `S1`–`S7`, and the second fix
round's window-id-gate hardening) is Claude Code's, landed as proposed, consistent with the
free-to-change-during-incubation policy (CLAUDE.md "Design authority and the risk gate").

**Framing:** the sibling producer project `../xapi-pcap-extract` was blocked on aximprint — its own
H-5 work order (a Parquet converter) could not start without a ratified archive contract. The user
has since ruled that the two projects **merge into one**, with **no archive seam**, so the cold
tier of Zeek logs becomes the thing aximprint reads directly and the Parquet converter becomes
optional/deferred rather than blocking. This round is what makes that possible. It does **not**
document the merge itself (a separate, later round) and does not touch `../xapi-pcap-extract` or
rewrite DESIGN_BRIEF §0.1.

**What changed:**
- **A tree of per-window Zeek log directories (`<out>/zeek/<window_id>/conn.log, ...`, one
  directory per pcap window — the shape `xtract harvest`'s cold tier writes) is now a first-class
  `LogDirSource` mode**, auto-detected: `root`'s immediate children are all directories, none of
  them a log file itself, and every name parses as the digit shape `timeutil.format_instant` emits
  (`_looks_like_window_id`) — a **positive gate**, not a structural sniff (`M3`, `LES-030`). One
  non-conforming name falls the WHOLE root back to flat mode, and every branch of `streams()` (flat
  via a direct log file, flat via a failed gate, a genuine window tree, a single file, an empty
  root) now emits a `notes()` line naming the shape found and why.
- **One `ZeekStream` per log stem in window-tree mode**, each internally k-way-merging that stem's
  file from every window directory that has one, on the row's own `ts` — never on the window
  directory's name, which is not evidence of the `ts` range its rows carry (a late-closing flow can
  land in a *later* window's directory than the one its own `ts` belongs to).
- **`reorder_by_key`** — the bounded, horizon-in-seconds reorder buffer — generalized out of
  `plugin.py`'s previously-private `_in_order` into shared machinery in `sources.py`, over any item
  and any float key, so the identical shape now runs at two layers: row-level (window-tree
  sub-streams, archive partitions) and event-level (`plugin.py`'s own top-level merge, behavior
  unchanged). `REORDER_HORIZON_SECONDS`/`REORDER_ROWS` moved to `sources.py`, re-exported from
  `plugin.py`.
- **`ParquetArchiveSource._read()` rewritten**: simultaneously-opened per-partition cursors merged
  with `heapq.merge`, keyed on `_archive_row_key = (ts, _zeek_run, _row_no)`, replacing sequential
  path-order concatenation — fixing a **pre-existing** bug (`D5`): an archive that grows
  incrementally (`OVERWRITE_OR_IGNORE` can add a `part-NNN.parquet` to an *earlier* date's directory
  once a later conversion batch discovers a row belonging there) could concatenate two mutually
  out-of-order partition files and silently read them in the wrong order.
- **A new health field, `_LogStats.source_capped`** (`M2`), disclosing the window-tree merge's
  INNER, per-window-directory cap-forced flushes — distinct from the pre-existing `capped` (the
  OUTER, per-stem pass), because unlike `unordered`, `capped` has no second observer, and silencing
  it the same way LOST the fact rather than avoiding a double-report.
- **`load_manifest` now type-gates the merge-key columns** (`ts`=DOUBLE, `_zeek_run`=VARCHAR,
  `_row_no`=any DuckDB integral type) for every DECLARED occurrence, refusing at open rather than
  silently degrading the merge back to path-order concatenation (`S2`).
- **`scripts/split_zeek_window_tree.py`** (new, test tooling) splits the synthetic fixture into a
  window tree, deliberately displacing 1-in-7 rows into the next window's directory;
  `verify_pipeline_gates.sh` step 3 is now **three-way transport equivalence** (flat directory /
  Parquet archive / window tree), all byte-identical (`M4`).
- CLI `--source` help text and DESIGN_BRIEF §3 now name the window-tree shape;
  `scripts/make_zeek_archive.py` shares `sources.py`'s `_is_log_file`/`_stem` instead of a fourth
  hand-inlined copy of the same predicate (`S3`).
- Suite **252 → 323 tests** (271 after the first consolidated fix round, 274 after `S2`'s
  parametrize cases, 322 once tester's re-verification surfaced the window-id-gate hardening below,
  323 after its own regression test). `scripts/verify_pipeline_gates.sh` green on all three steps.

**Why this shape:** `ParquetArchiveSource` already used one stream per log TYPE rather than one per
partition file, for memory reasons (296 partitions × 16 types would be 4,736 buffers) — the
identical reasoning applies one layer up to a window tree (N windows × M stems would be exactly as
bad), so `LogDirSource` consolidates to one stream per stem the same way. But a naive
per-window-file stream fed straight into `plugin.py`'s existing flat merge would be *correct*
(nothing lost) yet *dishonest under the one trap this round exists to avoid*: it would report a
manufactured `ingest_unordered` for a row that was never actually disordered, only discovered late
because of how aximprint chose to read the tree. So each window directory's own file is smoothed on
the same bounded horizon a flat directory already uses (a window directory is just as much a real
Zeek run as any other), and the smoothed per-file streams are combined by `ts` before `plugin.py`'s
own per-stem pass ever sees them.

**Measured — `M1`, the round's central finding, and the user's ruling:** the module docstring's
original claim — consolidating to one `ZeekStream` per stem bounds memory the way it bounds the
STREAM COUNT `plugin.py` sees — was **false**. `heapq.merge`'s own initialization primes every
sub-stream (pulls each one's first item) regardless of how many there are, and a pcap window's own
`ts` span typically sits *inside* the 600 s horizon, so only end-of-file — never the horizon — ever
releases a buffer:

| corpus | pre-`M1` | post-`M1` |
|---|---|---|
| 100 dirs x 500 rows (60 s windows) | 71 MB | 30.4 MB |
| 200 x 500 | 125 MB | 37.3 MB |
| 150 x 3,000 (5-min windows) | 508 MB | 34.6 MB |
| 300 x 2,000 | 670 MB (417 MB for the same corpus read flat) | 48.9 MB |
| 500 x 2,000 (1M rows, harvest scale) | not measured | 65.5 MB |

669 MB was resident to produce the **first** event — every inner buffer swallowed its whole file
before the merge could even begin comparing heads. Three agents measured the 300 x 2,000 fix by
three different methodologies (25.8 MB in-process RSS delta, 39 MB external measurement, 48.9 MB
subprocess-isolated `ru_maxrss` — the table's figure, and the most conservative): they disagree on
the constant and agree on a ~14–17x reduction; tester's later re-verification independently
reconfirmed 48.9 MB and found it holding at 65.5 MB on the 500 x 2,000 harvest-scale corpus.

**The user ruled option (b):** bound the stem's **aggregate** row budget
(`max_rows = max(reorder_rows // max(len(paths), 1), 64)`, the floor so division cannot starve
repair to zero at very high fan-out) rather than giving every sub-stream the full `reorder_rows`
each. Rejected: (a) accept O(corpus) memory and correct the docstring instead of the behavior; (c)
size the per-substream cap from the horizon rather than from the row budget. The accepted,
disclosed cost is **repair quality**: on a harsh 120-directory / 25%-displaced-to-900s corpus,
unrepaired inversions went from 0 (never a designed guarantee — an accident of unbounded memory,
for displacements beyond the 600 s horizon the contract actually promises) to 3,976, now visible
via `source_capped` (`M2`) rather than silently paid for in RAM.

**The archive side (`S1`) — right as reasoned, wrong about which constant mattered:** a
400-partition sweep confirmed the *correctness fix* genuinely costs more than the bug it replaces —
218 MB / 3.15 s (old, buggy concatenation) vs 239–303 MB / 3.7–4.0 s (correct merge) — an **18–26%
wall-clock cost**, the honest price of not laundering disorder into a clean-looking archive read.
But the separate, smaller `MERGE_FETCH_BATCH` constant this round first introduced to bound that
cost, reasoned from the same "`heapq.merge` primes every argument" principle as `M1` (the identical
`LES-010`/`LES-029` shape), measured **worse**, not better: 256 cost 303 MB and 39x the round trips
against `FETCH_BATCH`'s original 10,000, which cost 290 MB. The dominant term is DuckDB's own
per-cursor state (~175–215 KB/cursor — 1,200 simultaneous cursors at 4 stems × 300 partitions is
+68 MB on its own), untouched by `fetchmany` batch size either way. `MERGE_FETCH_BATCH` is deleted;
`_read` reuses `FETCH_BATCH`.

**Two behavior changes a future `live-verifier` must not misread as regressions:**
(a) **`S7`** — window-tree mode's `SensorHealthEvent.value` (displacement) is now measured against
a running max spanning **every contributing window directory** for a stem, not one file's own —
flat mode judges order per file (unchanged), window-tree mode judges order across the fully merged
per-stem sequence. Arguably the more informative number (it reflects what the sensor's disorder
actually looked like once fully assembled), but it **will differ from flat mode on the same
capture**, and that difference is not a bug.
(b) the correctness fix itself (not the deleted batch-size tuning) costs a genuine **18–26% more
wall-clock time** than the concatenation it replaces (see `S1` above) — expected, not a regression.

**Defects found and fixed in the same round:**
- **`M2` — a health counter silently lost.** `_DiscardedTally` (now `_PerFileTally`) was introduced
  to stop `unordered` double-reporting — correct, because `heapq.merge` preserves each stream's own
  internal order, so `plugin.py`'s outer, per-stem pass genuinely re-judges the fully merged
  sequence rather than re-seeing the same evidence twice. Applied uniformly to `capped`, it was
  wrong: `capped` has **no second observer** (`_LogStats.capped` is read in exactly one place, the
  stderr summary, and that line only ever saw the outer pass). Demonstrated: 40,000 dns rows
  reported `capped=0`, identical to flat, while the inner row buffers cap-flushed **34,880 times**.
  Fixed by `_LogStats.source_capped`, forwarded via a property setter that carries each increment's
  DELTA (not "+1"). Post-fix: `source_capped=13,897` on a disordered tree, `0` on flat, `0` on
  Parquet; a minimal 100-row-prefix-plus-one-late-row fixture produces exactly 1
  `SensorHealthEvent` (outer pass) and `source_capped=37` (inner pass), not double-counted. `LES-028`.
- **`M3` — the flat/window-tree mode switch was a structural sniff, not a positive gate.** "No log
  file directly under root, but at least one subdirectory" was the entire test. Measured failure
  modes: a stray root-level `.log` flipped 1 stream to 51 with no note at all;
  `archive`/`backup`/`scratch` was announced as "3 window directories"; Zeek's own native
  `<date>/<time-range>.log.gz` rotation layout was silently reinterpreted as a window tree (output
  stayed correct, but unannounced, for the single most common real deployment shape); a
  `.git`-shaped subdirectory swept in and reported UNRECOGNIZED. Fixed with a genuine positive gate,
  `_looks_like_window_id`, plus a `notes()` line on **every** branch naming the shape chosen and
  why. A later re-verification found the first version of that gate — `float(name)` parses as
  finite — still accepted `-1`, `1e5`, PEP-515 underscore grouping (`1_000`, and the realistic
  collision, `2026_08_10` — a plausible dated-backup directory sitting next to a genuine harvest
  tree), surrounding whitespace, a redundant `+`, and Arabic-Indic digits (`٣٤`). Tightened to
  `re.compile(r"[0-9]+(\.[0-9]+)?").fullmatch(name)` plus `math.isfinite(float(name))` as a
  necessary second gate (a ~400-digit all-nines string still passes the shape regex and overflows to
  `inf`) — `[0-9]` over `\d` because the explicit range is not Unicode-aware; `fullmatch` over
  `match` + `$` because `$` matches before a trailing newline. No false rejects at any stage: every
  shape `format_instant` actually emits still passes. `LES-030`.
- **`S2` — a wrong-typed archive column would have silently restored the exact bug this round
  fixes.** `_row_ts` returns `None` for anything `float()` refuses, so an archive declaring
  `"ts": "TIMESTAMP"` would make every merge key `-inf` and silently degenerate the merge back into
  path-order concatenation. `load_manifest` now type-gates `ts`=DOUBLE, `_zeek_run`=VARCHAR,
  `_row_no`=integral for every declared occurrence, refusing at open, naming the column and the
  wrong type, before a single row is read.
- **The mutation-testing finding.** Reintroducing the original double-report bug into `sources.py`:
  code-writer's exactly-once test failed (good), tester's new write-at-close test failed (good) —
  but tester's **original shuffle-based fuzz test passed**, silently. After `M1`'s aggregate floor,
  a uniform shuffle of a small per-window file essentially never drives the inner pass's own
  inversion branch, so the fix that bounded memory also, as a side effect, disarmed a test written
  before it existed. The old test was kept (it still proves no data loss) and a companion built on
  the real write-at-close shape, verified by mutation to actually catch the bug. `LES-031`.

**The equivalence proof.** code-reviewer built the check the first draft lacked: the seeded
synthetic fixture split into a 37-directory window tree, and again into a 38-directory tree with
15% of rows deliberately written into the *next* window's directory — feature files byte-identical
to the flat directory in both cases. `scripts/split_zeek_window_tree.py` (`M4`) wires an equivalent
check permanently into `verify_pipeline_gates.sh` (every 7th row into the next window's directory).

**`Q18` — closed, not deferred.** Opened mid-round as an unmeasured tuning question (the
`MERGE_FETCH_BATCH` constant and the window-tree stream count, both at real harvest/archive scale),
it is now measured by three agents at three methodologies, on both sides — see "Measured" and "The
archive side" above. Full resolution in `docs/OPEN_QUESTIONS.md`. `Q14` (TopK tie-break) is
**unaffected** — a different layer (`windowing/sketches.py`), not touched by this round.

**Not done, deliberately:**
- **Did not touch or read `../xapi-pcap-extract`.** The repo merge is a separate, later round; the
  verified input layout was given, not re-derived.
- **Did not rewrite DESIGN_BRIEF §0.1** or any other framing decision — §3 gains one clause noting
  the window-tree shape exists, nothing more.
- **Did not remove or demote `ParquetArchiveSource` or `docs/SPEC_zeek_metadata_archive.md`.** Both
  transports stay first-class.
- **No schema versioning** (`LES-001`) — no `windowtree:` locator prefix, no version field, no
  compat shim.
- **No capture contents anywhere** (`LES-003`) — every fixture in this round, including the
  cross-window-disorder determinism spot-check, is synthetic.
- **No new runtime dependency** — `heapq`/`math`/`re` (stdlib); DuckDB usage in `_read()` is the
  existing dependency, used differently (many simultaneous cursors), not a new one.
- **`--from`/`--to` refusal kept for a window tree**, extended to name the shape explicitly:
  pruning which window directories to open is unsound for the identical reason the merge itself
  exists (a directory's name is not evidence of the `ts` range its rows carry), and a
  full-scan-then-filter fallback would silently become a full scan wearing a slice's name.

**Affected docs:**
- `docs/WORK_ORDER_multi_window_log_read.md` — STATUS IN-PROGRESS → LANDED
- `docs/OPEN_QUESTIONS.md` — `Q18` opened and closed within this round
- `docs/LESSONS.md` — `LES-028`–`LES-032` added (five entries); `LES-013` gains a forward pointer to
  `LES-032`
- `docs/SPEC_zeek_metadata_archive.md` — "Provenance columns" corrected: the reader has not issued
  `ORDER BY ts, _zeek_run, _row_no` since `LES-019`, and states what the merge actually requires of
  partition sort order
- `docs/DESIGN_BRIEF_aximprint.md` — §3: the zeek `<dir>` locator note extended to mention the
  window-tree shape
- `CLAUDE.md` — layer table (`ingest/zeek/sources.py` row), verification-tier table, test/lesson
  counters, `Q1`–`Q18`
- `docs/SESSION_STATE.md` — rewritten for this round's handoff

**Affected code:**
- `src/aximprint/ingest/zeek/sources.py` — window-tree `LogDirSource` mode, shared
  `reorder_by_key`, `ParquetArchiveSource._read()` k-way merge, `_PerFileTally`, manifest
  type-gating, `_looks_like_window_id`/`_WINDOW_ID_SHAPE`, `_is_log_file`/`_stem`
- `src/aximprint/ingest/zeek/plugin.py` — `_in_order` wraps `reorder_by_key`;
  `_LogStats.source_capped`; `events()` threads the reorder bound through to `LogDirSource`
- `src/aximprint/cli.py` — `--source` help text
- `scripts/make_zeek_archive.py` — shares `sources._is_log_file`/`_stem`
- `scripts/verify_pipeline_gates.sh` — step 3 is now three-way transport equivalence
- `scripts/split_zeek_window_tree.py` — new, test tooling
- `tests/test_zeek_ingest.py`, `tests/test_zeek_parquet_source.py` — new/updated coverage; suite
  252 → 323

---

## 2026-08-07 — Workflow scaffolding upgraded to template v5

**Authority:** user — "check if the current workflow for this project is the latest, if not update
it to match." The v5 design decisions are the template's; adopting them here is the user's call.

**Supersedes:** the solo-mode handoff described in CLAUDE.md "Working modes" and the per-project
hooks block in `.claude/settings.json`. Both are removed, not deprecated.

**What changed:**
- **Solo mode is gone.** `claude-checkpoint.sh`, `claude-restore.sh`, `claude-clean-wips.sh` deleted
  along with the `wip/<base-sha>/<label>` convention. The template's own field evidence: the
  checkpoint machinery went unused while `SESSION_STATE.md` thrived, so it kept the cheap half and
  dropped ~25 KB of git surgery.
- **`docs/SESSION_STATE.md` is now a committed baseline file** — removed from `.gitignore`, the
  `CHECKPOINT-META` block dropped. It no longer needs a wip branch to travel, which was the one
  thing that kept this project's handoff from surviving a clone.
- **Mechanism moved host-global; policy stayed here.** `~/.claude/` already carried the v5 install
  (five agents and `/dev` symlinked into the template clone, hooks merged into its `settings.json`)
  — this repo had simply not been migrated to match, so **every hook was registered twice** and
  fired twice, once via the global copy and once via `.claude/settings.json`. Observable all
  session as the doc-drift Stop hook reporting itself twice under two different interpreters. The
  project hooks block is removed; permissions stay, because a list of what is destructive *here*
  belongs in this repo's history.
- **`scripts/verify_tier.sh` is now the stock template runner** and this project's gates are
  config: `CLAUDE_VERIFY_STEPS` in `.claude-workflow.env` names the fast tests plus the new
  `scripts/verify_pipeline_gates.sh`, which carries the three aximprint-specific gates
  (determinism, CLI round-trip, archive equivalence) that share one seeded fixture.
- Refreshed from the template: `claude-export.sh`, `hooks.py`, `inject_lessons.py`, `doc-stats.py`,
  `verify_tier.sh`, `docs/WORKFLOW.md`. The five agents, `/dev`, and `.claude/settings.json` were
  **not** refreshed — they are customized for this project and the refresh tool leaves them alone
  by design.

**Why the gates moved into their own script, specifically:**
The refresh **clobbered** `verify_tier.sh`, deleting all four gates, because this project had
hand-written its steps into a file the template owns. That is the failure the v5 mechanism/policy
split exists to prevent, and it is worth recording as the reason rather than as an accident: a
project-specific gate written into template mechanism is lost on the next refresh. Splitting it out
means `verify_tier.sh` can be refreshed forever without touching what this project verifies.

**Determinism / verification:** unchanged and re-run through the new path. `verify_tier.sh` green —
252 tests, then all three pipeline gates, with the synthetic archive digest still
`99b97b747058e6ec`. `doc-stats --check` clean.

**Affected docs:** `CLAUDE.md` (Working modes rewritten as baseline + two modes; the host-global vs
per-repo split and the do-not-add-hooks warning stated; tier-2 row), `docs/WORKFLOW.md` (v5),
`docs/SESSION_STATE.md` (committed baseline), `.claude-workflow.env` (solo knobs removed, verify
steps added), `.gitignore`.

---

## 2026-08-06 — Egress: one sink registry, both output paths through it

**Authority:** implementation (Claude Code) — work order `docs/WORK_ORDER_egress_sink_unification.md`;
the goal ("do the egress half now") was ratified by the user when the work order was drafted, the
five lettered decisions below (D1–D5) are Claude Code's implementation choices, landed as proposed.

**What changed:**
- **One driver, both verbs.** `cmd_ingest` and `cmd_features` are now thin wrappers over a single
  `cli._pipeline(plugin_name, source, ..., specs, open_kwargs)`: build the plugin, open every
  configured sink, run `wm.process`/`wm.finalize`, compute `extract_assertions(win)` **once per
  window** (D2) and hand the identical list to every open sink, fan out `write_window`, close every
  sink, return one summary per sink. The duplicated window loop the work order called out — only
  `cmd_features` used to stamp a run into its output, `cmd_ingest` stamped nothing — cannot drift
  again because there is structurally one loop.
- **The sink contract and registry moved out of `features/` into a new neutral `src/aximprint/sinks/`
  package** (D1): `WindowSink` (renamed from `FeatureSink`), `register`, `get_sink`. Import
  direction: `sinks → windowing`; nothing but the CLI (and `features/jsonl.py`, which now imports
  *down* into `sinks`) imports it. `features/base.py` is **deleted outright, no re-export shim** —
  `LES-001` treats a compatibility shim for an incubating contract as exactly the ceremony this
  project does not pay for.
- **Two new sinks register into it:** `StoreSink` (wraps `BeliefStore`) and `EvidenceSink` (wraps
  `EvidenceLog`, D5 — a sink of its own, `evidence=<path>`, rather than a flag on the store sink,
  because the evidence log is the source of truth and the store is its projection; inverting that in
  the CLI surface would have made the derived artifact own the primary one). `jsonl`
  (`features/jsonl.py`) still registers into the same registry from the sibling package.
- **`write_window(win, assertions)` widened** (D2) on the contract every sink implements;
  `assertions` is computed once by the driver, never per sink.
- **`--sink NAME=DEST`, repeatable**, on both verbs (D3), with the pre-existing flags kept as aliases
  that resolve to sink specs ahead of any explicit `--sink`: `--store` → `store=`, `--evidence-log` →
  `evidence=` (both on `ingest`), `--out` → `jsonl=` (on `features`). `ingest` still defaults to a
  store; `features` still defaults to jsonl; either verb can now add any other registered sink. An
  unknown sink name fails loud with the registry's available names.
- **`--vocabulary` stays CLI-verb-owned and sink-declared** (D4): it reaches only a sink whose own
  `accepts: frozenset[str]` names it (today, only `jsonl`) — routed by that declaration, never by
  inspecting `open`'s call signature.
- **`src/aximprint/provenance.py`** (new) — `build_stamp()`, extracted out of `features/jsonl.py`'s
  private `_build_stamp()` so `StoreSink` gets the byte-identical computation §10's "same aximprint
  build on both sides" actually requires, instead of a second implementation that could drift from
  the first. `@functools.lru_cache(maxsize=1)`.
- **The belief store gains a `runs` table** (build step 6) — one row per cleanly-closed run: what it
  was (`source`/`plugin`/`window_seconds`/`subbuckets`/`ts_from`/`ts_to`) and what build produced it
  (`aximprint_version`/`code_fingerprint`/`git_commit`), written once at `StoreSink.close()`, never
  per window. `status()` now reports the `runs` count and the most recent row. An exported
  `NONDETERMINISTIC_TABLES = ("runs",)` names the one table whose *contents* legitimately differ
  between two otherwise-equivalent runs, so an equality check imports the exemption instead of
  hand-maintaining a second table list that can silently omit it.
- **`IngestPlugin.events`'s abstract signature widened** to
  `events(self, source, ts_from=None, ts_to=None)` — `cli._pipeline` always calls with all three
  positional arguments, and `ZeekPlugin.events` already carried this shape (metadata-archive round,
  2026-08-05); the *contract*, not that one implementation, had drifted.
- **`scripts/verify_determinism.py` now drives the store + jsonl sink through `cli._pipeline`** — the
  same driver the CLI runs — instead of a second, hand-rolled ingest/window/write loop: exactly the
  kind of duplicate loop that drifted once already (see "Why").
- Suite **189 → 252 tests** (63 new/moved): `tests/test_sinks.py` is new (the registry contract, both
  new sinks, the `--sink NAME=DEST` CLI surface, multi-sink fan-out, and every partial-failure
  surface); `test_feature_sink.py` loses its registry tests (moved) and gains `abort()` coverage;
  `test_feature_vocabulary.py` and `test_zeek_ingest.py` each gain a couple of tests for the contract
  this round widened.

**Why:**
The ingest half became plugin-capable at both the transport and semantic layers (2026-08-05); the
egress half was **half-built** — `features/base.py` had a sink contract and registry that only
`cmd_features` used, while `cmd_ingest` bypassed it entirely and hardcoded `BeliefStore` +
`EvidenceLog`. That made a second output format free on one path and a CLI change on the other, and
it let the two verbs' window loops drift (§10's run-stamp requirement landed only on the side that
already had a sink). Closing it: one registry, every writer behind it, one driver — so a third output
format, or a third verb built on the same driver, costs a registration, not a rewrite.

**Determinism — the null test, and it is the acceptance criterion:** for the seeded synthetic
capture, `verify_determinism.py`'s three invariants hold (both passes byte-identical), and the
recorded synthetic feature-archive digest — `99b97b747058e6ec`, captured *before* touching `src/` per
`LES-018` — is **unmoved**, independently re-confirmed after the fix round. `./scripts/verify_tier.sh`
is green on all four steps, **unchanged** from the metadata-archive round (its own acceptance
criterion — this is plumbing, and the CLI invocations it makes were required to keep working through
the new alias resolution). Beyond the archive: all five pre-existing belief-store tables
(`node_features`, `edge_features`, `assertions`, `identity_bindings`, `window_health`) are
row-identical to what the pre-round code produced on the same capture, and the evidence log is
byte-identical. A run writing store + evidence + jsonl together produces all three, each identical to
a single-sink run of the same kind
(`tests/test_sinks.py::test_multisink_run_matches_three_separate_single_sink_runs`).

**Defects found and fixed in the same round — all four reproduced, not theorised, and this is the
real story of this round:**
- **A repeated destination silently doubled the belief store.** `--store b.duckdb --sink
  store=b.duckdb` exited 0 and wrote everything twice (584/1452/1828/192/8 rows against a true
  292/726/914/96/4 across the five row-bearing tables), two `runs` rows each independently claiming
  the full window count — and each sink's own `close()` summary reported its own correct half, so the
  summary an operator reads disagreed with the file it was describing. `snapshot()` then doubled
  `sum(flows)`/`sum(bytes)`, which is exactly what the synthesizer compiles into the twin. **DuckDB
  does not refuse this**: a second connection to a path already open in the same process, with the
  same configuration, succeeds, and both connections write — it only refuses on a *configuration*
  mismatch (e.g. a `read_only=True` connection racing a read/write one). Fixed by
  `cli._reject_repeated_destination`: every `--sink`/alias destination is resolved to an absolute path
  and checked against every other, across sink *types*, before any of them opens (`M1`).
- **A false provenance claim.** `features --sink store=… --vocabulary M` (no `--out`, so `jsonl` never
  opened) printed `"vocabulary pinned to … (map sha256 …)"` and exited 0 having pinned **nothing** —
  `store` never declares `vocabulary` in its `accepts`, so the option reached no open sink, and
  nothing said so. Fixed: after every sink has had a chance to claim an `open_kwargs` entry, whatever
  is left unclaimed is a hard `SystemExit` naming the option and which sinks actually opened (`M2`).
- **`inspect.signature`-based option routing was a silent-drop mechanism.** The routing for
  sink-specific options inspected `open`'s call signature to decide whether to pass `vocabulary`
  through — which fails silently: a decorator on `open` missing `functools.wraps`, or a renamed
  parameter, drops the option with nothing raising. Replaced by a declared `accepts: frozenset[str]`
  on the contract, checked by plain membership (`M3`).
- **The fan-out had three failure surfaces and the old code only handled the middle one.** A failure
  during `close()` used to still let an earlier or later `close()` write its own completion artifact
  (`manifest.json`, whose *sole* meaning is "this run finished") for a run that, as a whole, did not.
  A failure during `open()` leaked whatever the sinks ahead of it had already opened and surfaced as a
  raw traceback with no indication which destinations had been touched. Fixed by splitting the
  lifecycle: `close()` keeps meaning "finished, summarize," and a new `abort()` means "release what you
  hold, without the completion ceremony" — called from all three surfaces (open failure, write
  failure, close failure), releasing every sink that has not yet completed while leaving any sink that
  already closed cleanly untouched (its own completion is real even though the run as a whole is not)
  (`M4`/`M5`).
- **Pre-existing, surfaced by the tester, not introduced by this round:** `BeliefStore.write_window`
  crashed on a fully empty window (`executemany` raises on an empty parameter-set list; only the
  `assertions` insert was guarded), and a window with nodes but no edges (reachable independently —
  `_node()` fires from `AlertEvent` handling without a matching flow) wrote a `node_features` row and
  then crashed before `window_health` — a partial window `snapshot()` could see and `status()` could
  not. Fixed with an empty-list guard on every `executemany` (`T1`) **and** wrapping the whole write in
  a transaction (`begin()`/`commit()`/`rollback()` on any exception, `S4`) — the guard alone stops the
  common crash but does nothing for a genuine mid-write failure on real data (forced in a test on the
  fourth of five statements), and the transaction alone does nothing about `executemany` refusing to
  run on an empty list at all. Both were needed.

**Findings the work order asked for, answered:**
1. **Is the feature archive supposed to be the belief store's equal? No — and now it is a decided
   gap, not a silent one.** §5.4 lists deterministic assertions as artifact #4; the archive has four
   files and none of them is assertions, while the store path has an `assertions` table.
   `extract_assertions(win)` is computed once and handed to every sink (D2) — `JsonlSink.write_window`
   receives it and discards it on one visible line, by design (adding `assertions.jsonl` would change
   the archive's bytes, which this plumbing round must not do). Opened as `Q16`.
2. **What run metadata does the belief store need for §10's "same build on both sides"? Stamped, and
   the reconciliation limit reported.** `runs` carries the full `run_meta` plus `build_stamp()`'s
   three fields, written once at a clean close. What a reconciler still cannot do: attribute a
   specific `window_health` row to the run that wrote it — `write_run` records a run's window
   *count*, not its `window_start` values, and with no forgetting yet (`Q5`) and re-ingest appending
   duplicate `window_start` rows that `snapshot()` sums, there is no schema-level way to answer "which
   run wrote this window." Opened as `Q17`.
3. **Does any sink hold run-level state the way `JsonlSink` holds its `VocabularyObserver`? No.**
   `StoreSink` holds a `BeliefStore` connection and `EvidenceSink` holds a file handle — both bounded
   independently of the sink contract, neither a data-keyed map. Both report `vocabulary: {}` in
   their `close()` summary — present and empty, never absent, per the contract.
4. **Does the `close()` summary's `rows` field still mean the same thing now that a store sink
   reports through it? No, and that is now stated rather than assumed.** For `JsonlSink`, "rows
   written" and "rows in the file" coincide, because a fresh archive is opened every run. For
   `StoreSink`/`EvidenceSink`, the destination accumulates across many runs (no forgetting yet), so
   `rows` counts what *this run* inserted — never the table's running total. Recorded as a finding,
   not resolved by a rename: which meaning `rows` should carry is a decision the driver's other
   callers would all need to agree on.

**Not done, deliberately:**
- **`assertions.jsonl`** — a real, now-open gap (`Q16`), not fixed here per the work order's own
  boundary.
- **The split-registry trap itself** — `import aximprint.sinks` alone still registers only
  `store`/`evidence`; `jsonl` needs `aximprint.features` imported too, or `get_sink("jsonl")` raises
  (with a better message than before, naming where `jsonl` actually lives, `S10`).
  `docs/WORK_ORDER_retire_features_package.md` is drafted and queued to close this **structurally**,
  by moving `jsonl.py` (and, more contentiously, `vocabulary.py`) beneath `sinks/`; it is not ratified
  and has not started.
- **Run→window attribution** (`Q17`) and **the archive/store `rows` asymmetry** (finding 4, above) —
  recorded, not resolved.

**Affected docs:**
- `docs/WORK_ORDER_egress_sink_unification.md` — STATUS OPEN → LANDED, `## Outcome` appended
- `docs/OPEN_QUESTIONS.md` — `Q16`, `Q17` opened
- `docs/LESSONS.md` — `LES-014`, `LES-018` re-scoped to their new homes; `LES-020`–`LES-027` added
  (eight entries)
- `docs/CONVENTIONS.md` — §3: the output-sink layer described as the two packages it actually is,
  with the split-registry caveat stated in the convention itself
- `docs/DESIGN_BRIEF_aximprint.md` — §3: the plugin contract's `events()` signature note
- `CLAUDE.md` — layer table (`sinks`, `provenance` rows; `features` row narrowed), layering bullet,
  counters, `Q1`–`Q17`
- `README.md` — Layout (`sinks/` added, `features/` narrowed), Quickstart's `--sink jsonl` comment
  (see Supersedes)

**Affected code:**
- `src/aximprint/sinks/{__init__,base,store_sink,evidence_sink}.py` — new
- `src/aximprint/provenance.py` — new
- `src/aximprint/features/base.py` — deleted
- `src/aximprint/features/{__init__,jsonl}.py` — import from `sinks`/`provenance`;
  `write_window(win, assertions)`
- `src/aximprint/cli.py` — rewritten around `_pipeline`
- `src/aximprint/store/belief_store.py` — `runs` table + `write_run`, transactional `write_window`,
  `NONDETERMINISTIC_TABLES`, `status()`
- `src/aximprint/store/evidence_log.py` — opens its file handle eagerly at construction, not per call
- `src/aximprint/ingest/base.py` — `IngestPlugin.events` signature widened
- `scripts/verify_determinism.py` — drives through `cli._pipeline`
- `scripts/inject_lessons.py` — `_MODULES` gains `sinks`, `provenance`
- `tests/test_sinks.py` — new; `tests/test_feature_sink.py`, `tests/test_feature_vocabulary.py`;
  `tests/test_zeek_ingest.py` (two contract tests only — the rest of that file's diff is the prior
  round's, see CHANGELOG 2026-08-05)

**Supersedes:**
- The 2026-08-05 entry's closing line — "the egress half... `cmd_ingest` still bypasses the sink
  registry and hardcodes `BeliefStore` + `EvidenceLog`. Its own round." — that round is this one.
- The CLI's bare-name `--sink jsonl` (single-value, `pf.add_argument("--sink", default="jsonl")`) is
  superseded by the repeatable `--sink NAME=DEST` (D3); the bare form now fails loud (`--sink must be
  NAME=DEST with both parts non-empty`). `README.md:33`'s `# --sink jsonl` comment documented the old
  form and is corrected in this round.

---

## 2026-08-05 — Zeek metadata archive: read contract + Parquet transport

**Authority:** design + implementation (Claude Code) — plan approved by the user in-session; the
tiering, pcap-boundary policy, converter placement, and scale target are **user decisions** (see
"Decided with the user" below).

**Framing:** pcap extraction and the Zeek run are expensive and happen **once, outside this
project**; aximprint re-reads their output constantly. That is DESIGN_BRIEF §0.1 applied one layer
earlier than the code had it, and it rhymes with §7's own evidence-log/projection split.

**Decided with the user:**
- **Two tiers** — Zeek `.gz` as immutable cold evidence, Parquet as a rebuildable hot tier.
- **Converter is a standalone tool outside this repo.** aximprint therefore *validates* the archive
  rather than trusting it — the first artifact here whose producer can drift independently.
- **pcaps are already time-aligned**, so boundary-split flows are a recorded assumption, not a
  mitigation.
- **Scale target 100x+ and growing**, which makes time-range slicing the primary access pattern.

**What changed:**
- **`docs/SPEC_zeek_metadata_archive.md`** (new) — the read contract: `log=`/`date=` partitioning on
  the row's own `ts`, `ts` as **DOUBLE never TIMESTAMP** (microsecond quantization can move an event
  across a window boundary), real nulls instead of the `-` marker, required-vs-optional columns per
  log type, provenance columns, and the manifest shape.
- **Transport separated from semantics.** New `src/aximprint/ingest/zeek/sources.py` owns discovery
  and decode (`LogDirSource`, `ParquetArchiveSource`); `plugin.py` keeps the parser table, the
  reorder guarantee, and the merge. Adding a transport now costs no parser change. `Q2`'s "live tail
  is a plugin detail" is more true than when it was written.
- **`parquet:<root>` locator and `--from`/`--to`** on `ingest` and `features`, pushed into partition
  pruning. A slice against a log directory is **refused**, not silently ignored — it has no
  partition index, and a run that looks sliced but read everything is worse than an error.
- **Read-time validation, six checks**, each naming what it refused: missing/unparseable manifest,
  undeclared `logs`, a `sorted_by` that is not `ts`, `disorder.measured != true`, missing required or
  provenance columns, Parquet present but undeclared, a declared log with no files — plus a generic
  `0 events from >0 rows` tripwire in the run summary for drift no column check anticipated.
- **The disorder contract.** The archive is ts-sorted (row-group pruning needs it), and sorting
  destroys the signal `SensorHealthEvent(ingest_unordered)` carries. So the spec requires a nullable
  `_displacement_seconds` measured **before** the sort, and the reader re-emits it — otherwise every
  window built from a disordered sensor reads clean, which is `LES-006` exactly.
- **`scripts/make_zeek_archive.py`** (new, test tooling) — the reference conversion, used by the
  suite and the tier. The production converter remains external.
- Suite **168 -> 189 tests**. `verify_tier.sh` gains a 4th step: **transport equivalence**.

**The acceptance criterion is a null test, and it holds.** The same capture through the log directory
and through a Parquet archive produces **byte-identical** `nodes/edges/identity/windows.jsonl`; the
manifest differs only in `source` and the isolated `wallclock` block, and `categorical.map_sha256`
matches — the same capture encodes to the same columns either way.

**Measured, and it contradicts the plan's own prediction** (400k synthetic rows; `LES-019`):

| | text dir | parquet archive |
|---|---|---|
| full scan | 9.02 s / 38.7 MB RSS | 9.33 s / 116.3 MB RSS — **1.02x, no speedup** |
| storage | 53.1 MB | 9.2 MB — **5.8x smaller** |
| 10 % time slice | not possible | 0.50 s vs its own 4.96 s full scan — **9.9x** |

The plan predicted 2–4x on a full scan. **That was wrong.** Row production from Parquet is *slower*
than splitting TSV (1.88 s vs 1.47 s) because building a Python dict per row costs more than the
columnar read saves; typed columns do pay off after that (3.36 s -> 2.83 s) and the two cancel. The
transport is justified by **selectivity and storage**, which is precisely the user's stated access
pattern — not by scan speed. Recorded rather than quietly restated, because the wrong reason would
have been inherited by the next person to touch it.

**Found and fixed inside this round:**
- **A redundant `ORDER BY` on read** re-sorted per partition what the writer had already sorted:
  4.9x peak RSS for nothing. Dropped, with `preserve_insertion_order` set explicitly and the declared
  `sorted_by` now checked — and `_in_order` still judges live order, so a producer that lies about
  its sort surfaces as `ingest_unordered` rather than as scrambled features.
- **The reference converter did not scale**, in the artifact whose whole premise is scale: 400k rows
  cost **19 min of CPU and 917 MB RSS** through `executemany`, and were still CPU-bound with 5k-row
  multi-row `INSERT`s. Rewritten onto DuckDB's CSV bulk reader: **10.6 s**.
- **Two streaming tests could not fail.** They monkeypatched `zp._rows`, which this change moved to
  `sources`; repointing them was necessary, but both asserted only an *upper* bound on rows read, so
  `0` satisfied them — a dead patch would have passed while measuring nothing. Lower bounds added.
- **`_COLUMNS` and `_PARSERS` are now checked against each other at import.** A parser with no column
  declaration would project nothing on the archive transport and yield zero events from a full log
  (`LES-017`).

**Open, and it needs your call — `Q15`:** this spec carries **no version field**, per `LES-001`. But
it is the first artifact whose producer lives outside the repo and can drift independently, which is
the condition versioning exists for. Read-time validation catches drift the reader can *detect*; it
cannot catch a converter changing a field's **meaning** while keeping its name and type.

**Not done, deliberately:** the egress half of "ingest and egress plugin capable" — `cmd_ingest`
still bypasses the sink registry and hardcodes `BeliefStore` + `EvidenceLog`. Its own round.

---

## 2026-08-04 — Pinned categorical vocabulary in the feature manifest (closes `Q6`)

**Authority:** implementation (Claude Code) — work order `docs/WORK_ORDER_categorical_vocabulary.md`

**What changed:**
- **`manifest.json` gains a `categorical` block.** Per field: the index map, **where the index came
  from** (`observed` / `pinned` / `closed`), per-key row counts, mass total, coverage in mass *and*
  in rows, OOV rows / mass / itemized keys, composite marking, and a `map_sha256` over the index
  maps themselves. An observed map is sorted; a **pinned map is honored verbatim**; a closed table
  keeps its declared order. The block is incubating like every other shape here (`LES-001`).
- **`aximprint features --vocabulary <prior manifest.json | vocabulary file>`** pins a second
  capture to the first's columns. A key outside the pin is counted as out-of-vocabulary — never
  appended, dropped, or allowed to shift another key's index.
- **The acceptance criterion is a test, not a claim:** a consumer builds its full index map from
  `manifest.json` alone, never by pre-scanning `nodes.jsonl`. The test re-derives every published
  statistic from the row files and fails if the manifest cannot be reproduced from them.
- **The observer is bounded — `MAX_KEYS = 512`**, applied independently to the observed index and to
  the itemized OOV tail, enforced **at insert, not at close** (`LES-010`: eviction at close bounds
  output size, not peak memory), with `vocabulary_truncated` / `oov_keys_truncated` spill counters.
  Truncated mass counts toward `mass_total` but **not** toward `coverage`, so a capped map can no
  longer claim completeness.
- **`weight: proportion | count | indicator` now sits beside `container`.** `container` is a shape;
  it does not say whether a number may be summed or normalized.
- **`pinned_to` carries `file_sha256` *and* `map_sha256`**, and every archive carries
  `categorical.map_sha256`.
- **`--vocabulary ""`** — an unexpanded shell variable — used to produce an unpinned archive
  indistinguishable from a deliberate one. It now fails loud and leaves no directory.
- Suite **99 → 168 tests** (129 after the build, 168 after the review-and-fix round), all passing.
  `./scripts/verify_tier.sh` green, its step 3 now including a pinned re-run and the empty-pin
  rejection.

**Why:**
- `proto_mix` and `listen_ports` are sensor-discovered vocabularies whose **keys are data, not
  record shape**. With no published index map a tensor assembler had to pre-scan the archive before
  it could build its first tensor — which defeats streaming consumption — and two captures encoded
  to different columns. `manifest.json` was already the read-first artifact; it simply had nothing
  to say about the categorical fields. This is the last of the three §5.4 gaps that made the archive
  un-encodable from.

**Determinism: unchanged — and that is the proof of correctness.**
`verify_determinism.py --source metadata/zeek`: all three invariants hold and the manifest's
differing-key set is exactly `{wallclock}`. Window close `893fa7ec579fcac7`, synth
`5e1c49b1066dcd3e`, feature archive `cb885fe14673579b` — the **archive hash is identical to
2026-08-03**, because `check_features` digests only the four row files and a manifest-only change
cannot move it. All four row files were separately verified byte-identical to the prior archive.

**What review and test found, fixed in the same round:**
- **The observer was unbounded, and pinning did not help.** Measured: 36,000 rows minting fresh
  dynamic-port labels → 144,000 distinct keys, **17.6 MiB retained pinned *or* unpinned** — the
  per-key OOV counters are retained regardless — with the `categorical` block at 6.36 MiB (3.66 MiB
  even pinned to two keys). One wide row (5,000 labels) produced a manifest **3.8× the
  `nodes.jsonl` it described**, defeating the read-the-manifest-then-stream premise; unpinned it
  emitted a 16,385-column index reporting `coverage: 1.0` — honest-looking and useless. Post-cap:
  **17.54 → 0.10 MiB**, block 6.25 → 0.02 MiB, and coverage honestly reports **0.003556** where it
  had claimed 1.0.
- **The bound was claimed in a comment that cited the lesson forbidding exactly that.**
  `features/base.py` still stated that sinks "hold nothing across windows" — false, in a file this
  change edited. The contract now reads: a sink retains no *window*, but may hold run-level state
  provided the bound is stated where the state is declared, enforced at insert, and every truncation
  disclosed (`LES-014`).
- **`pinned_to.sha256` digested the pin *file*, which contains a wallclock block**, so two archives
  pinned to an identical map reported different digests — the one field meant to answer "did these
  encode to the same columns?" could not (`LES-015`).
- **Weight units were undeclared** — found independently by the reviewer and the tester.
  `proto_mix` and `state_mix` both reported `container: "dict"` while carrying a byte proportion
  (1,942.9984 over 1,943 rows) and a raw flow count (1,535,320, exactly `event_counts.FlowEvent`) —
  three orders of magnitude apart, with nothing telling a manifest-only consumer whether to
  normalize.
- **A `sorted()` citing `LES-009` for a property it did not provide.** Every mutation in that loop
  is per-key, so cross-key order cannot affect a float; the real order-dependence sat one line below
  in float accumulation — measured drift `…607.18976` vs `…607.18758` at 200k rows, surviving
  `round(x, 6)`. Sort dropped, Neumaier compensation added (`LES-013`).
- Plus: an untracked-categorical-field tripwire, a `coverage_rows` companion for the zero-mass edge,
  explicit `index_*` / `observed_*` scoping in the composite block, the section built once rather
  than twice, the artifact name carried on the spec set, and the streaming test widened — it scanned
  `vars()` for `list` / `tuple` and therefore could not see the new state at all.

**Findings the work order asked for** (answered in full in its Outcome section; shapes only,
`LES-003`):
- **Composite splitting stays deferred, and that is now measured rather than asserted.** 2 of 14
  `proto_mix` keys are composite, carrying **28.52 % of byte mass across 74.56 % of rows**.
  Splitting on `,` yields 13 atoms but moves the mean row sum **1.0000 → 1.3082** (max 1.8984),
  pushing 91.4 % of rows out of the [0,1] simplex; dividing instead asserts a byte split nobody
  measured. And **52.9 % of rows carry a composite *and* one of its own atoms bare**, so either
  policy silently merges two distinct observations into one column. The manifest ships
  `index_atoms_if_split` so the follow-up needs no re-derivation.
- **Vocabulary stability splits three ways.** 3 transport fallbacks (`tcp` / `udp` / `icmp`,
  **45.99 % of mass**) are maximally durable; 9 bare analyzer labels are durable as strings; 2
  composites are combinatorial and not expected to reproduce — 14 % of the vocabulary, 28.5 % of the
  mass. For `listen_ports`, 11 of 12 labels are assigned-range ports and 1 is IANA-dynamic
  (≥ 49152), which will differ on the next capture and after a reboot.
- **`listen_ports` should not be a vocabulary at all.** It is a `(port, transport)` tuple
  string-joined on `/`: 12 labels decompose to 10 ports × 2 transports, and the pairing carries real
  information (two ports appear under both transports), so it cannot be split into two marginal
  one-hots either. It is **not** the same defect as the comma composites (`LES-016`), and the tuple
  already exists upstream as a genuine `Counter[(port, proto)]`, string-joined purely for JSON.

**Chosen here, worth ratifying** — not defects; recorded so they are inherited deliberately rather
than silently:
- **`MAX_KEYS = 512` is this round's own number**, defended against three: 36× the reference
  capture's largest vocabulary, the recurring part of the port space, and worst-case tens of KiB
  against MiB row files. Nothing ratifies it.
- **Retention under the cap is first-arrival, and therefore order-dependent.** Order-free
  (lexicographic-min) retention was rejected because it preferentially discards the *durable* keys
  (`"53/udp"` sorts after `"49152/tcp"`); eviction was rejected because a re-arriving evicted key
  makes the spill counter meaningless. Reproducibility is unaffected — upstream row order is pinned
  (`LES-009`).
- **A pin whose index exceeds the cap is rejected rather than truncated** — a new loud failure.

**Known gaps left deliberately unfixed** (now `Q12`–`Q14`) — all three land in `windowing/`, which
this work order was explicitly forbidden to touch: `listen_ports`' key shape; `NodeAcc.listen_ports`
growing unbounded upstream (the features-layer cap bounds the *manifest*, not the accumulator); and
`TopK.normalized()` breaking count ties on insertion order — harmless before, but it now decides
membership of a published, pinnable index map, i.e. it can renumber columns.

**Affected docs:**
- `docs/WORK_ORDER_categorical_vocabulary.md` — STATUS OPEN → LANDED, with findings answered
- `docs/OPEN_QUESTIONS.md` — `Q6` closed; `Q12`–`Q14` opened
- `docs/LESSONS.md` — `LES-013`–`LES-018` (six entries consolidated from this round)
- `docs/DESIGN_BRIEF_aximprint.md` — §5.4 "Delivered vs. designed": gap (c) closes
- `docs/WORKFLOW.md` — tier-2 line: the CLI round-trip now includes a pinned re-run
- `CLAUDE.md`, `README.md` — counters, layer table, the `features` verb's new flag

**Affected code:**
- `src/aximprint/features/vocabulary.py` — new
- `src/aximprint/features/{__init__,base,jsonl}.py`, `src/aximprint/cli.py`
- `scripts/verify_tier.sh`
- `tests/test_feature_vocabulary.py` — new; `tests/test_feature_sink.py`

## 2026-08-03 — Window layer hardened for bounded disorder; identity, health, and audit honesty fixes

**Authority:** implementation (Claude Code)

**What changed:**
- **The consumer side of the reorder change.** Trading an exact global sort for a bounded reorder
  (entry below) made every downstream accumulator that assumed monotonically non-decreasing `ts` a
  silent corruption site. Fixed in the window layer: negative interarrivals no longer reach the
  quantile sketch and `last_flow_ts` no longer moves backwards; `EdgeAcc.last_ts` can no longer
  precede `first_ts`; the same interval inversion is fixed on `entities.py` binding `first_seen`.
- **Sub-bucket index.** `int()` truncates toward zero, so a flow up to one sub-bucket behind the
  open window's start was credited to sub-bucket 0 while `late_events` read `0` — a `0 <= i` guard
  passes for the whole first bucket. Index is now `math.floor` with the negative side rejected
  explicitly, and **lateness is counted for every event class**, not only flows: a late
  `SensorHealthEvent` was moving capture-loss evidence to the neighbouring window.
- **Identity binding no longer guesses at types.** `_dns` classified an answer as IPv4 by
  `a.count(".") == 3`. On the reference capture **1,355 of 3,936 dns-derived `IdentityEvent`s
  (34.4 %) were binding a non-address string as `ip` at `evidence_class="observed"`** — fabricated
  provenance, the exact failure the honesty claim exists to prevent. Answers are now parsed as
  addresses; the corrected count is **2,581**.
- **Fail-loud at the parse boundary:** a column-count mismatch no longer produces a
  complete-looking event via `dict(zip(...))`; non-finite `ts` is rejected (`if not ts` accepted
  NaN/inf); unset/empty markers are taken from the log's own `#unset_field` / `#empty_field`
  header rather than hardcoded constants; ICMP no longer manufactures listeners at 0.95
  confidence.
- **`capture_loss_pct_max` is `None`, not `0.0`, when no loss evidence arrived** — "no evidence"
  and "evidence of none" are different facts and a health record must not conflate them.
- **The ingest disorder audit is persisted, not printed.** A row that could not be put back in
  order is emitted as `SensorHealthEvent(kind="ingest_unordered")` and lands in the window health
  record; previously it reached stderr only, where no artifact could inherit it.
- **New health fields** on the window record: `interarrival_skipped`, `unknown_state_flows`,
  `flows_imputed_counters`, `unmodelled_health_kinds`, `ingest_unordered`. `counters_imputed`
  flags flows where an unset counter is not honestly zero — **176 / 1,535,320 = 0.011 %** on the
  reference capture.
- Suite **2 → 99 tests**, all passing; `./scripts/verify_tier.sh` green.

**Why:**
- A bounded guarantee is not a weaker version of an exact one — it is a different contract, and the
  side that receives it has to be rewritten to match. Every bug in this round is downstream of that
  single substitution, which is why they are recorded together (`LES-005`).
- The identity fix is the project's central honesty claim under test: an `observed` binding that was
  never observed is worse than a missing one, because it propagates into the synthesized config's
  provenance unchallenged.

**Known gaps left deliberately unfixed** (now `Q6`–`Q11`): no categorical-vocabulary artifact for
`proto_mix` / `listen_ports`; §5.4's OS hints and §5.2's packet-size quantiles have no field; the
exact sketch stand-ins are O(input) rather than O(output); `identity.jsonl` restates rather than
emits deltas; `__longtail__` has no unit test and no consumer contract; `#set_separator` is still
hardcoded as `,` in `_dns`. Entity resolution remains `entity == ip` (`Q1`, out of scope).

**Affected docs:**
- `docs/LESSONS.md` — `LES-004`–`LES-012` (nine entries consolidated from this round's findings)
- `docs/OPEN_QUESTIONS.md` — `Q6`–`Q11` opened
- `.claude/agents/live-verifier.md` — the health-honesty step told the agent to confirm
  `capture_loss_pct_max` is "populated"; after this change an unaudited window honestly reports
  `None`, and the runbook would have read that as a failure. Also refreshed for the new log
  coverage and the retired `Q2` limits.
- `CLAUDE.md`, `README.md` — counters, layer table, verification tier

**Affected code:**
- `src/aximprint/windowing/windows.py`, `src/aximprint/windowing/entities.py`
- `src/aximprint/ingest/zeek/plugin.py`, `src/aximprint/events/model.py`
- `tests/` — `test_windowing_states.py`, `test_zeek_ingest.py`, `test_feature_sink.py`, `test_smoke.py`

## 2026-08-03 — Pluggable feature-vector sink: `features` layer + `aximprint features`

**Authority:** implementation (Claude Code) — work order `docs/WORK_ORDER_feature_vector_sink.md`

**What changed:**
- New layer `src/aximprint/features/`: a sink contract + registry deliberately mirroring
  `ingest/base.py` (one registry pattern in this codebase, not two), and a `jsonl` sink emitting
  the five DESIGN_BRIEF §5.4 artifacts — `nodes.jsonl`, `edges.jsonl`, `windows.jsonl`,
  `identity.jsonl`, `manifest.json`. Sinks write on window close and hold nothing across windows.
- **Import direction:** `features` reads `windowing` outputs and `events`; nothing imports
  `features` except the CLI (CONVENTIONS §3).
- New CLI verb `aximprint features --source <dir> --out <dir> [--sink jsonl]`, which does
  ingest → window → sink and **never touches the belief store**. `ingest` / `status` / `synth` are
  unchanged.
- `scripts/verify_determinism.py` gained a third check — a byte-stable feature archive, with
  wall-clock confined to one named manifest key so the check asserts the differing key *set*
  rather than maintaining a skip list. `verify_tier.sh` step 3 exercises the verb end to end.
- The sink writes a `.gitignore` containing `*` into its own output directory: a feature archive is
  capture-derived and inherits the capture's sensitivity, and the repo's `*.jsonl` rule does not
  cover `manifest.json` (`LES-011`).

**Why:**
- `BeliefStore` was the only consumer of `WindowResult`, and it is the wrong shape for model
  training: it is a *belief* projection, not a feature archive. The registry exists before the
  second sink so Parquet can be added later without touching a consumer.

**Measured on the reference capture** (shapes only, `LES-003`):
- 22,147 rows / 5.5 MB, **31.3 s / 72.7 MiB peak**. 0 of 15,307 edges dangle.
- A node row is **37 fixed floats** (13 scalars + 3×4 quantiles + 12 sub-buckets) plus `state_mix`.
- **≈ 75 KiB per window-hour** → 12.9 MB/week, 56 MB/month at this vantage's 44 entities. The real
  axis is ~1.9 KB per *entity*-hour, so 1,000 entities is ≈ 2.1 GB/week worst case.
- `identity.jsonl` is **98.4 % restatement** — 4,386 rows for 68 distinct bindings, 17 % of the
  archive — because `EntityResolver.observe()` returns a record on every observation. §5.4 #3 calls
  this artifact a *delta*; as emitted it is not one (`Q9`).

**Affected docs:**
- `docs/WORK_ORDER_feature_vector_sink.md` — STATUS OPEN → LANDED, with findings answered
- `CLAUDE.md` — layer table gains `features`; verification tier now names three determinism checks
- `README.md` — Layout, Quickstart, Verification
- `docs/DESIGN_BRIEF_aximprint.md` — §5.2 / §5.4 notes on what is delivered versus designed

**Affected code:**
- `src/aximprint/features/{__init__,base,jsonl}.py` — new
- `src/aximprint/cli.py` — `features` verb
- `scripts/verify_determinism.py`, `scripts/verify_tier.sh`
- `tests/test_feature_sink.py` — new

## 2026-08-03 — Streaming Zeek ingest at real-capture scale (closes `Q2`)

**Authority:** implementation (Claude Code) — work order `docs/WORK_ORDER_streaming_zeek_ingest.md`

**What changed:**
- `ZeekPlugin.events()` no longer materializes the capture: `out.sort()` is replaced by a
  **per-file bounded reorder buffer feeding `heapq.merge`**. Peak RSS at ingest **926.6 MB →
  67.4 MB (13.7×)**; a full ingest + windowing pass over the reference capture measures
  **39.7 MB / ~32 s**.
- **The reorder bound is stated in seconds, not rows.** `REORDER_HORIZON_SECONDS = 600.0` is 3.5×
  the worst displacement measured on the reference capture (169.6 s); `REORDER_ROWS = 8192` is a
  memory backstop only. A row bound's real time horizon is `capacity / row_rate`, so it weakens
  exactly as captures grow — the opposite of what a bound is for. A cap-forced flush is counted and
  disclosed as "the horizon was not honored."
- **Closed flow-state vocabulary** — `FLOW_STATE_CLASSES` (8 classes) and `FLOW_ACCEPTED` in the
  event model, covering all 13 `conn_state` values present in the reference capture. v0
  discriminated only `S0`/`REJ`/`SF`, leaving **41 % of flows (623,669 `RSTR`)** invisible to the
  hygiene features. An unmapped state is counted as `unknown_state_flows`, never silently bucketed.
- **Log-type coverage +8**: `dce_rpc`, `ldap_search`, `smb_mapping`, `smb_files`, `ntp`,
  `known_services` → `ServiceEvent`; `known_hosts` → `IdentityEvent`; `stats` →
  `SensorHealthEvent`. Four types are **skipped by name** with no fall-through; an unrecognized log
  type is reported, per "fail loud".
- **Listener census gating** changed from `state == "SF"` to "the responder answered"
  (`FLOW_ACCEPTED`), raising listen-port coverage **+69.9 %** — a service that resets after
  answering is still a service.
- Progress reporting to stderr so a multi-minute ingest does not look hung.

**Why:**
- `Q2` stopped being theoretical: the reference capture's `conn.log` alone is 1.5 M rows / 188.8 MB,
  and the v0 sort needed ~926 MB to start work. The work order made the memory bound a validation
  criterion rather than an aspiration.
- The state-class work is not incidental to streaming: a capture whose dominant state is invisible
  produces hygiene features that are confidently wrong, which is worse than absent.

**Determinism:** holds on the reference capture — 72 windows, two full passes byte-identical:
window close `sha256=893fa7ec579fcac7`, synth compiler `5e1c49b1066dcd3e`, feature archive
`cb885fe14673579b`. The merge needed a total order that terminates before the payload
(`LES-009`); an unstable tie-break surfaces here and nowhere else.

**What the real capture taught** (shapes only, `LES-003`):
- **Zeek logs are not internally ts-sorted.** They key on connection *start* and are written at
  *close*: `conn.log` has 5.2 % of rows below the running max (worst 878 rows / 169.6 s),
  `dns.log` 65 % (worst 154 rows / 33.8 s), `known_services` worst-by-time 294.8 s. The work order
  assumed each log was internally sorted and the merge therefore exact; it is not (`LES-004`).
- **Every one of the 72 windows carries capture loss**, 5.87 %–24.92 %, median 9.17 %. There is no
  clean window. `sensor_pkts_dropped` / `pkt_lag` are `None` in all 72 because this deployment's
  `stats.log` has both columns present-but-unset in 1706/1706 cells, so `capture_loss.log` is the
  only loss evidence available (`LES-012`).
- `service` is `-` on ~95 % of `conn` rows. `proto_mix` is byte-weighted so it is not degenerate,
  but Zeek's `service` is a comma-joined **set**, so composite keys fragment the space and coexist
  with their own singletons (`Q6`).
- Reference-capture audits all read zero: 0 unordered rows, 0 cap-forced flushes, 0 malformed rows,
  0 unknown conn states, 0 late events.

**Affected docs:**
- `docs/WORK_ORDER_streaming_zeek_ingest.md` — STATUS OPEN → LANDED, with findings answered
- `docs/OPEN_QUESTIONS.md` — `Q2` closed
- `docs/DESIGN_BRIEF_aximprint.md` — §3 plugin contract: "yields in ts order (best effort)" is now
  a measured bound, not an assumption
- `CLAUDE.md` — layer table, counters

**Affected code:**
- `src/aximprint/ingest/zeek/plugin.py`, `src/aximprint/events/model.py`,
  `src/aximprint/events/__init__.py`
- `src/aximprint/windowing/windows.py`, `src/aximprint/learners/extractors.py`
- `tests/test_zeek_ingest.py`, `tests/test_windowing_states.py` — new

## 2026-08-03 — Workflow framework imported from axrange; pipeline mode wired

**Authority:** user

**What changed:**
- Imported the axRange collaboration framework (source: `../axr-product`) and wired all three
  working modes — **Solo** (cross-session handoff), **Pipeline** (background subagents), and
  **Chat-bridge** (design-session transport). `docs/WORKFLOW.md` is the model; CONVENTIONS §4's
  previously-notional pipeline route is now live.
- **Five subagents** in `.claude/agents/` — `code-writer`, `tester`, `code-reviewer`, `doc-writer`,
  `live-verifier` — plus the `/dev <task>` orchestrator command in `.claude/commands/dev.md`.
- **Harness config** `.claude/settings.json`: permission allowlist so background workers run
  reads/tests/preflight unattended, with aximprint's own gates — reading real capture data, any
  `axrange` verb, `git commit`/`push`/`tag`, and dependency installs. Hooks wire the lessons store
  (SessionStart index, PreToolUse per-file injection), `py_compile` on edit, an advisory doc-drift
  nudge at Stop, and a hard doc-drift block at `git commit`.
- **`docs/LESSONS.md`** upgraded from a stub to the `LES-NNN` store the hooks consume, seeded with
  the three lessons this import earned: `LES-001` (never version an incubating schema),
  `LES-002` (re-verify determinism; never assume it), `LES-003` (capture-derived content never
  leaves the working directory).
- **`CLAUDE.md`** added — the orientation anchor the subagents read. A summary of `docs/`.
- **Scripts** in `scripts/`: `claude-{checkpoint,restore,clean-wips,export,import}.sh` copied
  verbatim (project-agnostic); `hooks.py`, `inject_lessons.py`, `doc-stats.py` adapted to
  aximprint's layers and counters (tests / ingest plugins / recorded lessons);
  `verify_tier.sh` and `verify_determinism.py` written for this project.
- `.gitignore` gains `.claude-sync/`, `.claude/settings.local.json`, `docs/SESSION_STATE.md`.
  `pyproject.toml` registers the `slow` pytest marker under `--strict-markers` so the pipeline's
  fast/slow split is honest.

**Why:**
- CONVENTIONS §4 already named the subagent route as available-but-optional; nothing implemented it.
  The user asked for the subagents to be usable here as they are in the sibling project.
- The framework's value is the feed-forward loop: lessons captured once are auto-injected before the
  next edit to the same file, so a bug-class is paid for once rather than per-session.

**Two deliberate adaptations — this is not a verbatim copy:**
- **The risk gate is inverted on schemas.** axRange gates version-locked schema changes; aximprint
  gates *ratification* instead. Changing the event model, feature shapes, plugin contract, or
  belief-store schema is **free** during incubation (§0.2, CONVENTIONS §1.2) — copying axRange's
  gate verbatim would have contradicted a ratified aximprint decision. `LES-001` records the hazard.
- **The live tier is real capture data, not a cluster.** aximprint owns no infrastructure. Tier 3 is
  a gated run against a real Zeek capture (coverage by log type, health-record honesty, determinism
  on real logs, scale numbers, synth sanity); tier 4 is the round-trip self-test against an
  axrange-deployed twin (§10) when it becomes buildable. The gate is privacy, per §7 / CONVENTIONS §3.

**New — the determinism tier:**
- `scripts/verify_determinism.py` runs the whole pipeline twice over a seeded synthetic capture
  (80 hosts, ~4 windows, sized to force top-K edge eviction and the `__longtail__` spill) and
  compares canonical window features and the synthesized YAML byte-for-byte. It is the aximprint
  analogue of axRange's cluster-free preflight checks, and it makes brief §5.4 (idempotent window
  close) and §9 (deterministic compiler) enforceable rather than merely stated — the unit suite
  cannot catch a determinism break, because a single run always agrees with itself.
- Both invariants **hold** as of this entry.

**Affected docs:**
- `docs/WORKFLOW.md` — new; three modes, verification tiers, and a "what differs from axRange" table
- `docs/LESSONS.md` — stub → LES store, 3 entries
- `docs/CONVENTIONS.md` — §4 updated: the pipeline route is wired, with its two adaptations named
- `CLAUDE.md` — new
- `docs/CHANGELOG.md` — this file gains the Authority-marker convention

**Affected code:**
- `.claude/settings.json`, `.claude/agents/*.md`, `.claude/commands/dev.md`
- `scripts/` — 5 copied verbatim, 3 adapted, 2 new
- `.gitignore`, `pyproject.toml`

## 0.0.1 — 2026-08-02

**Authority:** retrofit *(entry predates the Authority-marker convention adopted 2026-08-03)*

- Project inception. Design brief folded from axrange design discussion (2026-07-30/08-02 chat).
- Boilerplate: event model, ingest plugin registry + zeek plugin v0, windowing layer v0
  (entity=ip resolver, exact sketch stand-ins, hourly windows + 5-min sub-buckets), deterministic
  extractors v0 (listen census, dhcp bindings), evidence log + DuckDB belief store, synthesizer
  stub, CLI, smoke test.
