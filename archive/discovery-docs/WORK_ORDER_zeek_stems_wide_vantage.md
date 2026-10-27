# Work order — eight Zeek stems a wide vantage produces and this build does not parse

**Status:** **D1–D3 ALL RULED 2026-08-20** (see below, and `docs/WORK_ORDER_axquery_ingest.md`
for D1's other half). **COMPLETE 2026-08-20** — all five build steps landed and verified at full scale.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes nothing.** It **amends the closing claim** of `docs/WORK_ORDER_zeek_stem_coverage.md`
(COMPLETE 2026-08-13) — that work order's "every stem this network produces is accounted for by
name, 28 parsed + 7 skipped = 35" was true **of the vantage it was measured on** and is false at a
wider one. That is not a defect in it; it is `Q24`'s premise being vantage-dependent, which nobody
knew until there were two vantages to compare.

## The finding

The 2026-08-19 ten-day harvest of **all twelve** corp-apcn streams (`corpapcn:0-11`, 1,440 windows)
produces **43 distinct Zeek log stems**. This build accounts for **35** (28 parsed + 7 declared
skipped) and reports **8** as `unrecognized_log_types`. Zero accounted-for stems were absent.

The prior baseline was **streams 1-4**. Every one of the eight is invisible at that tap. The
network was always producing them.

| stem | windows (of 1,440) | rows | fields it carries |
|---|---|---|---|
| `known_certs` | 522 | 649 | `host` `port_num` `subject` `issuer_subject` `serial` |
| `syslog` | 58 | 350 | `facility` `severity` `message` |
| `rdp` | 54 | 116 | `client_name` `keyboard_layout` `client_build` `security_protocol` |
| `radius` | 17 | 22 | `username` `mac` `framed_addr` `tunnel_client` `result` |
| `rfb` (VNC) | 7 | 8 | `desktop_name` `authentication_method` `share_flag` |
| `mqtt_connect` | 6 | 511 | `client_id` `will_topic` `proto_version` |
| `redis` | 1 | 2 | `cmd.name` `cmd.key` `cmd.value` `success` |
| `websocket` | 1 | 1 | `host` `uri` `user_agent` `subprotocol` |

**Nothing is being lost silently.** `unrecognized_log_types` (D5 of the prior work order) names all
eight per-window and once in `runs`. What is lost is their *content* reaching the event model.

## Why this matters more than the row counts suggest

`radius` is **22 rows** and is the most valuable row in the table. It carries `mac` **and**
`framed_addr` on one record — a MAC bound to an assigned address, which is structurally the same
evidence a DHCP lease provides, and host-scoped evidence is the **only** kind permitted to merge
two addresses (`docs/WORK_ORDER_entity_resolution.md` D1). `rdp` carries `client_name`, a computer
name — the resolver's preferred key (`LES-008`) — from a **third** independent source alongside
NTLM and Kerberos.

This is measured, not speculative: `docs/WORK_ORDER_ml_layer.md` shows unsupervised inference of
host identity failing on this capture at **precision@5 = 0**, while these two stems *state* the
same fact deterministically. **Parse the logs that declare identity before modelling it.**

## Decisions needed

**D1 — `radius`: RULED 2026-08-20** (delegated by the user: *"rule D8 and radius split
together"*). Ruled jointly with D8 of `docs/WORK_ORDER_axquery_ingest.md`; **the full combined
ruling lives there** and is summarized here.

Admit. `mac`→host, `framed_addr`→host, `username`→user, no field refused. But **two things in the
draft above are withdrawn**:

1. **"The first stem needing per-field scope assignment" is false.** `IDENTITY_SCOPES` is
   `kind -> extractor`, not `kind -> scope`, and every `Identifier` already carries its own scope:
   `_identifiers_dhcp` returns two from one row, and `kerberos_identifier` already returns `HOST`
   or `USER` by parsed value. `radius` needs one extractor and one table row — **no new
   machinery**. It is the cheapest of the eight stems, not the riskiest, so **build step 1 loses
   its "do it first because it is novel" rationale** (doing it first is still fine — it is now
   first because it is easy and carries the most identity value per row).
2. **The subject must be declared, and it is not either endpoint.** A RADIUS exchange runs between
   a NAS and the RADIUS server; the host being identified is on neither end. The subject is
   **`framed_addr`**, carried in the row. A parser that binds `mac` to the packet's source or
   destination merges every authenticated host in the building onto the RADIUS server — the same
   catastrophic false merge as binding on a router MAC, reached from the opposite direction.

`rdp`'s `client_name` (build step 2) inherits the same subject discipline: it names the **client**,
so it binds to the connection's originator, never to the RDP server that logged it.

**D2 and D3 — RULED 2026-08-20** (delegated by the user: *"rule D2 and D3 of the stems work
order"*). **Admit all seven.** Nothing is declined: every one has a real responder-side durable
fact, and forensic completeness (CONVENTIONS §3) settles the collection question. But the draft's
two framings are both wrong, and they are wrong in the same direction.

### R4 — `known_certs` is a `known_*` parser, not a `_responder_service` one

The draft says `known_certs` "is keyed identically on `(host, port_num)`" and "drops into
`SERVICE_PROJECTIONS` with no new machinery". Right about the table, wrong about the parser.
`ingest/zeek/plugin.py`'s own module comment excludes exactly this shape: *"`_known_services`/
`_software` are also not converted: both key on `host`/`host_p`, not `id.resp_h`/`id.resp_p`, so
they are not the same shape at all."* `known_certs` groups with `known_services`/`known_hosts` and
needs its own small parser. Four stems use `_responder_service`, not five.

Worth recording while here: `known_certs` supplies the address that `x509` structurally lacks
(`_SERVICE_NOT_PROJECTED["x509"]` — *"the certificate carries no connection tuple at all"*). It is
the join `x509` could not make. That decline also cites `Q1` as its blocker, and **`Q1` closed
2026-08-14** — so the `x509` entry's stated reason is now partly stale. Out of scope here; flagged
so the next reader does not inherit it as current.

### R5 — the projection tuple IS the cardinality surface; that settles D3

D3 asks for "a `MAX_KEYS`-style bound" on `syslog`'s `message`. **A bound already exists** —
`SERVICE_MAX_KEYS = 32` distinct key-tuples per entity, overflow counted, never silently dropped.
The problem is not that the bound is missing; it is that putting a free-text field **in the
projection tuple** burns all 32 slots on noise and evicts the real facts. One collector, 350
distinct log lines, cap gone.

So the rule, which the existing tables already obey without ever stating it: **collect everything,
project only responder-side, low-cardinality, durable facts.** That is precisely the split the
house rules already draw — forensic completeness governs *collection*, store economics
(`LES-017`) governs *projection*. No new mechanism, and D3 needs no new bound.

### R6 — three stems carry CLIENT fields into a RESPONDER-keyed event

This is `R1` recurring. `smtp` and `sip` were projected in the first landing of the previous round
and reversed as a keying bug — *"both attach a CLIENT-emitted field to a RESPONDER-keyed event"*.
The same defect is latent in three of these seven, and the draft's "admit all five" does not catch
it: `rdp`'s `client_name` / `keyboard_layout` / `client_build`, `websocket`'s `host` / `uri` /
`user_agent`, and `mqtt_connect`'s `client_id` / `will_topic` are all client-emitted. Collect them;
**never project them**.

### The ruled table — REVISED 2026-08-20 against measurement

**The first version of this table was built from the "fields it carries" column above, which is
`radius.log`'s and friends' SCHEMA, not a measurement. Four of its seven projection choices were
wrong.** Corrected below; the `measured` column is population across the whole ten-day cold tier.

| stem | kind | parser | **project** | measured | predicate | conf |
|---|---|---|---|---|---|---|
| `known_certs` | `known_cert` | `known_*` shape (R4) | `subject` `issuer_subject` | **649/649** | `serves_known_cert` | 0.95 |
| `syslog` | `syslog` | `_responder_service` | `facility` `severity` | **350/350** | `serves_syslog` | 0.9 |
| `rdp` | `rdp` | `_responder_service` | `security_protocol` | **111/116** | `serves_rdp` | 0.9 |
| `rfb` | `rfb` | `_responder_service` | `server_major_version` `server_minor_version` `authentication_method` | **8/8, 8/8, 3/8** | `serves_rfb` | 0.9 |
| `mqtt_connect` | `mqtt` | `_responder_service` | `connect_status` | **511/511** | `serves_mqtt` | 0.9 |
| `redis` | `redis` | `_responder_service` | *(none)* | — | `serves_redis` | 0.9 |
| `websocket` | `websocket` | `_responder_service` | `server_extensions` | **1/1** | `serves_websocket` | 0.9 |

Collect every field in every case (forensic completeness). What changed and why:

- **`mqtt_connect`: `proto_version` -> `connect_status`.** `proto_name`/`proto_version`/`client_id`/
  `will_topic`/`will_payload` are **0/511**; `connect_status` is **511/511** and is the
  RESPONDER's answer, which is what a responder-keyed projection should have carried anyway.
- **`websocket`: `subprotocol` -> `server_extensions`.** `subprotocol` is **0/1**;
  `server_extensions` is **1/1** and server-side. `host`/`uri`/`user_agent` are populated but are
  client-emitted, so R6 still bars them.
- **`redis`: DECLINED by name**, in `_SERVICE_NOT_PROJECTED`. The first draft of this row said
  "project nothing" and reasoned that a bare `serves_redis` with an empty key would still be a
  true fact. **Reading `_add_service` disproved it**: `if not parts: return` means an empty
  projection tuple silently produces NOTHING while still claiming a projection exists. A decline
  states the same outcome where a reader can find it, and a test now forbids an empty tuple
  anywhere in `SERVICE_PROJECTIONS`. Named path back: the moment `cmd.name` populates, that alone
  is the durable fact.
- **`rfb`: add the server version pair.** `authentication_method` is only **3/8**;
  `server_major_version`/`server_minor_version` are **8/8** and responder-side.

An always-empty projection field is not a crash — `_attrs()`'s positive gate keeps it out and the
assertion degrades to a bare `serves_X`. It is a *content* failure, not a correctness one, which
is exactly why it survived a ruling: nothing would have gone red.

### `rdp` contributes NO identity, and structurally cannot here

Build step 2 was to add `IdentityEvent(client_name, host scope, subject = the originator)`. **There
is nothing to add.** `client_name` is **0/116**, along with the entire client info block
(`keyboard_layout`, `client_build`, `client_dig_product_id`, `desktop_width/height`,
`requested_color_depth`, `client_channels`, `encryption_level/method` — all 0/116).

**This is not a sampling artifact.** `result` is `encrypted` on **111/116** with
`security_protocol=HYBRID_EX` (CredSSP/NLA), and the remaining **5** are
`HYBRID_REQUIRED_BY_SERVER` — the server refusing a downgrade. The client info block is sent after
the handshake goes encrypted, so on any NLA-enforcing deployment Zeek will never see it. The
finding above — *"`rdp` carries `client_name` ... from a THIRD independent source alongside NTLM
and Kerberos"* — describes a field modern RDP does not expose in cleartext.

The one identity-bearing field is **`cookie`, 105/116** — the RDP `mstshash` token. It is
**USER**-scoped (a person: `david.gus` x37, `neal.eide` x2, `AzureAD\m` x3), so it can never merge
two addresses (D1), and every observed value is <=10 characters with 92/105 at exactly 9 — the
names read as **truncated**. Admit it if you want a user census, keep `raw` as always, and record
that the value is lossy so nobody downstream treats `david.gus` as a whole name. It is not a
substitute for `client_name` and it is not host-scoped.

### Three identifiers deliberately NOT minted

`known_certs.subject`, `rfb.desktop_name` and `mqtt_connect.client_id` each look like a name and
each fails the subject test (R2):

- a **certificate subject** is routinely a wildcard or a SAN list shared across many hosts — the
  fanout failure of `R3`, arriving through TLS instead of through a MAC;
- a **VNC desktop name** is user-set and only incidentally the computer name;
- an **MQTT `client_id`** names an *application instance*, not a machine or a person — a subject
  the vocabulary has no scope for, and inventing one is not this ruling's business.

All three are **collected** and available; none becomes an `IdentityEvent`. A later work order
wanting cert-derived identity must go through R2's subject discipline first — and `known_certs`
plus `x509`'s SANs is the natural place to try it, now that `Q1` is closed.

**Lesson candidate:** *a responder-keyed event must never project a client-emitted field* — twice
now (`R1`, then R6), caught in review both times, never encoded. The parser-level fix is to make
the projection tuple's provenance explicit rather than leaving it to a reviewer's eye.

## Build steps

1. ~~`radius` parser + `_identifiers_radius` + one `IDENTITY_SCOPES` row, subject =
   `framed_addr`.~~ **LANDED 2026-08-20** — and it found that this step's premise is false on
   this capture. See "Build step 1 result" below.
2. `rdp` parser: `ServiceEvent` only. **The `IdentityEvent` half is cancelled** — `client_name`
   is 0/116 and structurally invisible under NLA (measured 2026-08-20, above). Optionally admit
   `cookie` as a USER-scoped, explicitly-truncated identifier.
3. `rfb`, `redis`, `websocket` via `_responder_service`; `known_certs` as its own
   `known_*`-shaped parser (R4). Projection tuples exactly as the R5/R6 table gives them.
4. `syslog` + `mqtt_connect` via `_responder_service`. **No new cardinality bound** — R5:
   `message`/`client_id`/`will_topic` are collected and simply never projected.
5. ~~Re-ingest the ten-day cold tier; re-measure.~~ **DONE 2026-08-20** — see below.

**Acceptance:** `unrecognized_log_types` is `[]` on this capture; no parser refuses a field.
**The "host-named entities and strong-keyed IR entities both rise" criterion is WITHDRAWN** —
measurement killed both of this work order's identity sources (`radius`'s `mac`/`framed_addr`
0/22, `rdp`'s `client_name` 0/116). Nothing here will move those counts on this capture, and a
criterion that cannot be met is worse than none. What remains is genuine and worth landing: seven
service censuses and eight stems off the unrecognized list.

## Build step 1 result (2026-08-20)

**Landed**, 12 tests, tier 1 1,531 green, tier 2 green (determinism, transport equivalence, ruff).
`radius` is parsed, and is in neither `unrecognized_stems` nor `unparsed_stems`. Five places
mirrored the stem tables and all five needed the row — `_PARSERS`, `_COLUMNS`,
`acquire/harvest/zeek.py::KNOWN_PARSED_STEMS`, that module's `_SCHEMAS` fake-runner header, and
`docs/SPEC_zeek_metadata_archive.md`'s column table. Three drift guards caught the ones missed;
none of them was findable by reading the work order.

**The premise of this step is false on this capture, and the table above is what misled it.**
Measured across all 22 rows in all 17 radius-bearing windows:

| field | populated |
|---|---|
| `ts` `uid` `id.orig_h` `id.orig_p` `id.resp_h` `id.resp_p` | 22 / 22 |
| `username` | **7** / 22 |
| `mac` `framed_addr` `tunnel_client` `connect_info` `reply_msg` `ttl` | **0** / 22 |
| `result` | 22 / 22 — **all `unknown`** |

This work order's "fields it carries" column listed `radius.log`'s **schema**, not a measurement,
and the finding section built on it: *"it carries `mac` and `framed_addr` on one record — a MAC
bound to an assigned address, structurally the same evidence a DHCP lease provides."* **No row
carries either.** The 7 usernames are `nessus` x6 from an internal scanner (10.80.75.125 probing
six hosts) and `steve` x1 from a public address — probe traffic, which `result=unknown` on every
row corroborates. **`radius` supplied zero identity evidence here**, and the ML work order's
recommendation to *"parse `radius`/`rdp` before modelling what they state outright"* rests on the
same unmeasured column for `radius`. **Re-measure `rdp`'s `client_name` before building step 2.**

The parser is still correct and still worth having: it is a no-op by construction on rows with no
`framed_addr` (`entity_resolution` D4's safety property, reached from the parser side), it moves
`radius` off the unrecognized list, and it will bind the moment a capture carries real
authentications. **Same shape as `snmp`'s `display_string` (`R2`), and the second time an admit
has been justified by a field nobody measured** — lesson candidate: *a work order's field list is
a schema until someone counts it.*

**One diagnostic now lies, deliberately left alone.** `plugin.py`'s generic drift tripwire fires
`"PRODUCED NO EVENTS from a non-empty log — parser and source schema likely disagree"` on all 17
windows, where parser and schema agree perfectly and the rows simply carry no subject. The
tripwire is intentional (*"it should never pass as quietly as a log that legitimately had nothing
to say"*) and firing here is arguably right; only its stated CAUSE is wrong. Changing shared,
deliberate behavior was out of this step's scope. Recommended follow-up: soften the clause to
name both possibilities, since `LES-006`'s "no evidence vs evidence of none" is exactly the
distinction it currently collapses.

## Build steps 2-4 result (2026-08-20)

**Landed.** 36 parsed + 7 skipped = **43** — every stem the twelve-stream vantage produces is now
accounted for by name. 6 more tests (1,537 tier 1), tier 2 green.

Verified on **six real windows** of the ten-day cold tier, full chain (plugin -> `WindowManager`
-> `extract_assertions`), 383,489 rows -> 423,928 events across 33 log types:

- **`unrecognized_stems` is `{}` and `unrecognized_log_types` is `[]` on every window** — the
  headline acceptance criterion, met on real data.
- Five of the seven new projections fire: `serves_known_cert` 15, `serves_mqtt` 32,
  `serves_rdp` 10, `serves_syslog` 8, `serves_rfb` 3. `serves_websocket` 0 (that stem is in 1
  window of 1,440 and not in this sample), `bound_radius` 0 (measured-empty, as expected),
  `serves_redis` 0 (declined by name).
- Assertions carry `observed` at the ruled confidence, subject resolved through the entity layer
  (`ent-0004` where anchored, the bare address where not — `entity_resolution` D4).

**Two rules are now enforced by tests rather than by a reviewer's eye**, both of which had already
been caught twice manually: no responder-keyed projection may name a client-emitted field
(`R1`/R6), and no projection tuple may be empty (the `redis` correction above).

**Step 5 (re-ingest the full ten-day tier) is NOT done** — ~3 h 22 m ingest plus ~50 m
materialize, and `aximprint ingest` is harness-gated. Its original purpose was to re-measure
`entity_roster` and `ir_entity.key_kind`, which the withdrawn acceptance criterion covered; what
it would still add is the service censuses at scale and a refreshed store/PIT.

## Build step 5 result (2026-08-20) — the work order is COMPLETE

Full re-ingest of all 1,440 windows: **3 h 24 m** ingest + **51 m** materialize, both exit 0,
written to `beliefs-v2.duckdb`/`ir-v2.duckdb` so the 2026-08-18 baseline survives as the
comparison.

| metric | baseline | v2 | delta |
|---|---|---|---|
| `unrecognized_log_types` | **8 stems** | **`{}`** | cleared |
| assertions | 2,587,271 | 2,587,812 | **+541** |
| `entity_roster` | 105 | 105 | **+0** |
| `ir_entity.key_kind` | 58,467 addr / 78 host / 18 mac | identical | **+0** |
| node_features, edge_features, identity_bindings, profile_facts, window_health | — | — | **+0** |
| `ir_entity`, `ir_segment`, `ir_edge`, `ir_identity`, `ir_unseen` | — | — | **+0** |

New facts: `serves_known_cert` **245**, `serves_mqtt` **137**, `serves_rdp` **78**,
`serves_syslog` **72**, `serves_rfb` **8**, `serves_websocket` **1**. That sums to exactly 541 —
**nothing else in the store moved by a single row.**

**The withdrawn acceptance criterion was right to withdraw.** `entity_roster` and `key_kind` are
unchanged to the row, exactly as predicted once `radius`'s `mac`/`framed_addr` measured 0/22 and
`rdp`'s `client_name` 0/116. Had that criterion stood, a correct build would have "failed".

**An unplanned determinism result.** A re-ingest across a code change reproduced 2.59 M assertions
and every other table **byte-for-byte identical** except the 541 intended additions. Tier 2 runs
the pipeline twice on a small fixture; this is the same invariant at 1,440 windows and 10 days,
and it is the strongest evidence the project has that window close is a pure function of (events,
resolver state, code build).

**Honest read on yield.** 541 new assertions against 2.59 M is **0.02%**. The eight stems were
admitted for coverage and honesty — no stem unaccounted for, no field refused — and that is what
they delivered. They were also *argued for* on identity grounds, and on that they delivered
nothing: both identity sources measured empty. `serves_known_cert` at 245 is the real prize, and
it is the join `x509` could never make.

## Reproduce

Cold tier: `/home/rocky/harvest-corpapcn-10d/zeek` (1,440 windows, 9.4 GB, vantage
`https://10.80.65.7:0-11`, network `corp`).

```
cd <cold tier> && ls */*.log | sed 's|.*/||; s|\.log$||' | sort -u
```
