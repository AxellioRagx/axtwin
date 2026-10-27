# WORK ORDER — Zeek stem coverage: close `Q24`, and stop the two stem lists from drifting

**STATUS:** LANDED — **COMPLETE 2026-08-13**: all six build steps (1, 2, 2b, 3, 4, 5) have landed;
nothing in this document remains open. Drafted 2026-08-11. **D1 LANDED 2026-08-11** (test-only —
`_PARSERS`/`_SKIP`/`KNOWN_PARSED_STEMS`/`KNOWN_SKIPPED_STEMS` all untouched,
`ingest/zeek/plugin.py` has an empty diff — see CHANGELOG 2026-08-11). **D2/D3/D4/D5 ALL RULED
2026-08-12, then AMENDED THREE TIMES** (twice 2026-08-12, once 2026-08-13) — **eleven** stems parse,
**three** are declared skipped, **no parser refuses any field**, `unrecognized` reaches the store.
28 parsed + 7 skipped = 35 is the target once step 3 lands. **Build steps 2 and 2b LANDED
2026-08-13** (CHANGELOG same date, code-review round included): the three skips
(`KNOWN_SKIPPED_STEMS`/`_SKIP` now 7, was 4) and the field-drop reversals in `_ldap_search`/
`_smb_files`/`_COLUMNS["ntp"]`. **Build step 3 LANDED 2026-08-13, all eleven of eleven admits**
(CHANGELOG same date, two code-review rounds): the ten stems this document's own table specifies,
plus `ocsp` — excluded from the first landing pending `Q29`, then landed the same day once
`docs/WORK_ORDER_subject_less_facts.md` gave it a channel. `KNOWN_PARSED_STEMS`/`_PARSERS` are now
**28** — 28 + 7 = 35, exactly as targeted, every stem this network produces accounted for by name.
**Step 5 (the prose-counter sweep) executed the same round — see its own section below for what a
"verify it held" check found.** **Step 4 LANDED 2026-08-13** (CHANGELOG same date, code-review
round): `unparsed_log_types`/`unrecognized_log_types` reach the window health record and `runs`,
`None` for unaudited. **With this, every build step is landed and this work order is COMPLETE** —
see step 4's own section, below, for what review found that D5's own text did not anticipate.

> **READ THIS BEFORE THE D-SECTIONS.** Three user interventions across 2026-08-12/13 changed the
> answers after they were written, and the sections below are amended in place rather than rewritten,
> so the reasoning that failed stays visible. All three are the same failure — a usefulness test
> scoped to one consumer and blind to the others (`LES-089`):
> 1. **The "already covered elsewhere" test was entity-blind.** "Serves LDAP already arrives via
>    `known_service`" holds only for hosts the tap *observed*. A stem naming hosts it never saw is
>    the sole witness for those entities, not a duplicate. This promotes `ldap`, `smtp` (whose `path`
>    and `x_originating_ip` carry addresses of unseen hosts) and `x509` (SANs list many hostnames,
>    unlike `ssl`'s single `subject`).
> 2. **Privacy was retired as a project principle** (CHANGELOG 2026-08-12, CONVENTIONS §3, DESIGN_BRIEF
>    §7). aximprint collects everything it observes; `axrange` owns disclosure. Every
>    privacy-motivated decline and every "refused, deliberately" field list in this document is
>    **void**. This promotes `smtp` and `sip`, and deletes the refusal column from step 3's table.
>
> 3. **The test was also scoped to the wrong OUTPUT** (2026-08-13). Everything was judged against
>    `twin_config` — *does this name an entity or a durable property* — and never against **`tfc`**,
>    which DESIGN_BRIEF §9 declares a first-class artifact wanting "fitted intensities/schedules/
>    seeds." **`ocsp` therefore parses**: `nextUpdate − thisUpdate` is a cache lifetime, i.e. a
>    schedule parameter, and `conn` sees the connection but never the validity window that explains
>    its interval. The same lens reopens **`ntp`'s** projection decline and exposes that
>    `_COLUMNS["ntp"]` drops `poll` (step 2b). `analyzer`/`reporter` are unaffected — they describe
>    the sensor, not network behavior.
>
> **Parse (11):** `http`, `ssh`, `quic`, `ntlm`, `tunnel`, `snmp`, `smtp`, `sip`, `ldap`, `x509`,
> `ocsp`. **Skip (3):** `pe` (executable metadata, no entity — contested, `Q26`),
> `analyzer`/`reporter` (Zeek diagnostics — `Q28`). **Every remaining skip has a named path back.**
>
> **Headers verified against the baseline 2026-08-13** — see `Q24`'s "Headers verified" block for the
> six `#fields` lines. Two things the build must not get from memory: **`ocsp` has no connection
> fields at all** (so its landing shape is `Q29`'s question, not a `ServiceEvent` with an `ip`), and
> **`x509`'s key column is `fingerprint`, not `id`**, with SANs split across `san.dns`/`san.uri`/
> `san.email`/`san.ip` and `certificate.*` dotted names.
>
> **The binding constraint is now neither privacy nor parse cost.** `windowing/entities.py` drops any
> binding without an IP, and `resolve()` is address-in/address-out, so **a host known only by name has
> no subject to assert about**; the `referenced` evidence class exists in the design and no code emits
> it. `smtp`'s addresses are usable under today's resolver. `ldap`/`sip`/`x509` *names* are not, and
> that work belongs to `Q1`. **Split the build accordingly** — do not bolt a second subject type onto
> the placeholder resolver to get these landed.
**Depends-on:** nothing in code; the evidence is already captured (see "The evidence")
**Closes:** `Q24` — **CLOSED 2026-08-12** by the D2–D5 rulings, amended through 2026-08-13. Opens two
named follow-ups rather than loose ends: `Q28` (`analyzer.log` as a sensor-completeness input) and
**`Q29`** (the store has no channel for a subject-less fact, which is what `ocsp` produces and what
`tfc` is made of). Also closed a duplication seam nobody had noticed (D1) — landed 2026-08-11.
**Authority:** drafted by Claude Code from live evidence 2026-08-11. **D1 was a Claude Code proposal
with no ruling needed** — it changed no coverage, only added a check for a claim already true, and
has landed. **D2–D5 ruled by Claude Code 2026-08-12, delegated by the user** ("rule on the rest of
`Q24`") — the user had already ruled `software` in on 2026-08-11 as a partial D2. The rulings
**supersede this document's own D2 recommendation**; see each section.

## The evidence

The first live `acquire pcap` (CHANGELOG 2026-08-11) produced 12 windows of real production
traffic, and harvest's pinned Zeek 8.0.9 was run over all of them. Reproducible baseline, no
appliance needed to re-check it: `/tmp/aximprint-zeek-test/`, 12 per-window log directories.

**35 distinct log stems produced. The plugin accounts for 20.**

The good half first, because it is a real result: **all 16 of `KNOWN_PARSED_STEMS` appeared in real
traffic.** Nothing in the parser table is speculative or dead — every type it claims to handle,
this network actually emits. The synthetic fixtures could not have established that.

The other 15 are unrecognized — produced, but neither parsed nor deliberately declined. They are
not one category:

| stem | windows | logs | category |
|---|---|---|---|
| `http` | 12/12 | 0.9 MB | service + endpoint fingerprinting |
| `ssh` | 12/12 | 0.8 MB | admin-access topology |
| `x509` | 12/12 | 0.3 MB | identity |
| `ocsp` | 12/12 | 0.2 MB | identity |
| `quic` | 12/12 | 0.2 MB | absorbs what `http` used to carry |
| `ldap` | 12/12 | 0.1 MB | **see D4 — `ldap_search` is parsed, `ldap` is not** |
| `ntlm` | 12/12 | — | authentication |
| `software` | 12/12 | — | endpoint behavior, directly §1's language |
| `tunnel` | 10/12 | — | topology |
| `smtp` | 8/12 | — | service |
| `snmp` | 4/12 | — | service (often infrastructure) |
| `sip` | 3/12 | — | service |
| `pe` | 2/12 | — | file/executable metadata |
| `analyzer` | 9/12 | — | **Zeek's own diagnostics, not traffic** |
| `reporter` | 12/12 | — | **Zeek's own diagnostics, not traffic** |

Nothing is lost on disk — the cold tier keeps every log regardless. This is about what reaches the
event model, and therefore what the synthesizer can ever cast.

**The `category` column above is the 2026-08-11 framing, and the 2026-08-12 ruling does not follow
it.** Read it as the raw observation it was, not as an answer: `x509`/`ocsp` are categorized as
"identity" and are declared *skipped*; `snmp` is categorized as "service (often infrastructure)"
and is *parsed*, on that parenthetical. The per-stem rulings are in D2–D4 below and in
`docs/OPEN_QUESTIONS.md` `Q24` "The ruling".

## Decisions

### D1 — the two stem lists are duplicated across a boundary that forbids the import, unchecked

**Proposal, no ruling needed. Land this first, independently of D2–D4.**

`KNOWN_PARSED_STEMS` lives in `src/aximprint/acquire/harvest/zeek.py`. The parsers it describes live
in `src/aximprint/ingest/zeek/plugin.py`'s `_PARSERS`. **`acquire` must not import `ingest`**
(CONVENTIONS §3, AST-walk enforced), so the two can never be derived from one another in `src/` —
they are kept in agreement by hand, and **nothing checks that they are.**

They *are* in sync today: 16 and 16, both directions empty, verified 2026-08-11. That is luck plus
care, not a guarantee — and D2/D3 change both lists at once, which is precisely when drift enters.

Drift is silent and lies in both directions: a stem in `KNOWN_PARSED_STEMS` that `_PARSERS` doesn't
handle makes `ZeekResult.unrecognized` **under-report** (the operator is told coverage is fine when
it isn't); a stem in `_PARSERS` missing from the list makes it **over-report** noise until someone
learns to ignore the field, which is worse than no field.

**Fix:** a test that imports both and asserts set equality. A *test* may import across the boundary
— the layering rule constrains `src/`, and this is exactly the check that boundary makes impossible
to express in `src/`. Add it before touching either list.

**Landed 2026-08-11** (`tests/test_acquire_zeek_stem_sync.py`, CHANGELOG 2026-08-11): both pairs are
now checked — `KNOWN_PARSED_STEMS`/`_PARSERS` and, beyond this section's own literal text,
`KNOWN_SKIPPED_STEMS`/`_SKIP` too (added because build step 2 is exactly where its drift would enter
next, so leaving it unchecked would have made this test useless for the very next step). Landing the
check *before* the coverage change surfaced two more things while nothing yet depended on them: a
failure-message classification bug in the check itself, and an unrelated, silently-unconnected
`_SCHEMAS` table — both fixed in the same round. See Build steps, below, and `docs/LESSONS.md`
`LES-084`–`LES-087`.

### D2 — which unrecognized stems join the parser table? **RULED 2026-08-12.**

Claude Code's recommendation: the eight at 12/12 that speak to §1's stated goal — `http`, `ssh`,
`x509`, `ocsp`, `quic`, `ldap`, `ntlm`, `software`. Every one is present in every window of real
traffic, and each maps to something the discovery pipeline claims to learn: services, endpoint
behavior, identity, topology. `quic` matters more over time, not less — parsing `http` alone decays
as traffic migrates.

**PARTIALLY RULED 2026-08-11 (user): `software` parses. The other fourteen remain unruled** —
including the other seven of the recommended eight. Do not read "D2 ruled" anywhere; it was one
stem, requested by name for a specific purpose (feeding axRange's `nodes[].image:`), not an
acceptance of the recommendation.

**RULED 2026-08-12 — six parse: `http`, `ssh`, `quic`, `ntlm`, `tunnel`, `snmp`.** This
**supersedes the recommendation above**, and not by a margin: `x509` and `ocsp` come *off* the
recommended eight, and `tunnel` and `snmp` — from D3, never recommended — go *on*. The full
per-stem reasoning is in `docs/OPEN_QUESTIONS.md` `Q24` "The ruling"; the operative table for the
build is step 3 below.

**AMENDED 2026-08-12 — ten parse.** `smtp`, `sip`, `ldap` and `x509` join, per the two amendments in
the box at the head of this document; the per-parser field-refusal lists this section referred to are
void. Read the rest of this section as the reasoning that got four rows wrong, kept in place because
*why* it went wrong is the transferable part (`LES-089`).

The principle that produced a different answer than the recommendation: **a stem earns a parser
when it carries a durable fact about a host or a link that no already-parsed stem carries.** Being
on-mission by topic is not the test — all fourteen pass that. Two of the recommended eight fail the
actual test:

**And here is the flaw in that principle, found the same day:** "no already-parsed stem carries" was
evaluated **only over entities the tap observed.** Scoped that way it is nearly a tautology — `conn`
sees every host the sensor sees, so any *service* fact about an observed host looks partly redundant.
The stems it wrongly rejected are precisely those whose value is about **entities not in `conn` at
all**: LDAP DNs, SMTP relay chains, x509 SANs. The corrected test has two clauses — *a fact no other
stem carries*, **or** *an entity no other stem names.*

- **`x509` is redundant, not merely awkward.** `_COLUMNS["ssl"]` already carries the server
  certificate's `subject` and `issuer` into the event model today; they are parsed and simply not
  projected. Certificate identity is one line in `SERVICE_PROJECTIONS["ssl"]` away — no new stem, no
  cert-chain join, no new `_SCHEMAS` entry. Adding `x509` would have bought a join to reach data the
  pipeline already has.
- **`ocsp` describes a certificate's revocation status** — a property of a CA's certificate, not of
  any host on this network.

Both of D2's earlier lessons held up and are now discharged rather than pending: the `software`
content check is applied per stem in step 3 (and is why `snmp` was admitted at 4/12 and `smtp`
refused at 8/12), and `Q27` — the prerequisite this document could not see when drafted — closed
2026-08-12, so a new `ServiceEvent` kind now has somewhere to land.

Two things that ruling taught, which should inform the rest of D2 rather than be rediscovered
per stem:

- **`software` does not deliver what it was picked for.** Measured on the same baseline: 187 rows,
  58 hosts (~1% of observed entities), and **zero `OS`-typed rows**. Zeek's software framework
  reads banners, not OS fingerprints. It landed as a genuine installed-software census and only an
  indirect OS hint (`Q26`). **Check what a stem actually contains on the baseline before ruling it
  in for a stated purpose** — the 12/12 presence figures in the table above say a stem *appears*,
  not that it carries what someone wants from it.
- **Parsing a stem is not the same as its facts arriving anywhere.** `software` needed a windowing
  and extractor change on top of the parser, because `WindowResult` had no channel for a service
  fact and every `ServiceEvent` was being dropped (`Q27`, ~93,000 events on the baseline). Six of
  the remaining fourteen (`http`, `ssh`, `x509`, `ocsp`, `quic`, `ldap`) would parse into
  `ServiceEvent`s and land in that same hole. **`Q27` is effectively a prerequisite for most of
  D2**, and was not visible when this work order was drafted.

### D3 — the intermittent stems: parse, declare skipped, or leave unrecognized? **RULED 2026-08-12.**

`smtp` (8/12), `tunnel` (10/12), `snmp` (4/12), `sip` (3/12), `pe` (2/12). Real services, sampled.
`tunnel` is topology evidence and arguably belongs with D2's set. The rest are a judgment call about
scope, and "leave unrecognized" is a legitimate answer **provided** D5 lands, so the fact stays
visible instead of dying with the process.

Separately and with more confidence: **`analyzer` and `reporter` are Zeek's own diagnostics, not
traffic.** They look like `KNOWN_SKIPPED_STEMS` members (`loaded_scripts`, `packet_filter`,
`telemetry`, `files`) that nobody got around to declaring. Recommend declaring them skipped so they
stop diluting the unrecognized signal.

**RULED 2026-08-12.** Frequency turned out to decide nothing here; content decided everything.
**`tunnel` and `snmp` were promoted into D2** — `tunnel` because an overlay a twin cannot see is a
*wrong* topology rather than a thinner one, `snmp` because `display_string` is sysDescr, a
first-class device description that lands on the infrastructure `software.log` never observes. That
`snmp` at 4/12 is admitted while `smtp` at 8/12 is refused is the intended shape of this ruling.

~~**`smtp`, `sip`, `pe` are declared skipped.**~~ `smtp` is the highest-PII log Zeek writes
(`mailfrom`/`rcptto`/`subject`/`to`/`cc`); what survives redaction is a self-reported `helo` and
"serves SMTP", which `conn`/`known_services` already give. `sip`'s identifying fields are SIP URIs —
extensions and DIDs. `pe` is the file-analysis family, where `files` is already declared skipped for
this exact stated reason; declaring `pe` separately keeps the pair consistent instead of letting one
hide behind the other.

**AMENDED 2026-08-12 — `smtp` and `sip` PARSE; only `pe` stays skipped.** Both declines above rested
entirely on privacy, which is no longer a project concern, and the reasoning was doubly wrong on
`smtp`: "what survives redaction" is the wrong question, and it caused me to read the whole log as
mail content when `path` (the `Received:` relay chain) and `x_originating_ip` are **IP addresses of
hosts the sensor never saw** — the single most valuable thing in the stem, and I dismissed it. `sip`
follows for the same reason at one remove: an extension is an endpoint identifier, and identifying
fields are the point of an evidence collector, not a hazard. `pe`'s decline is unaffected — it is
file metadata naming no entity, and `files`'s existing skip reason is a value argument, not a
disclosure one.

**AMENDED AGAIN 2026-08-13 — of D3's original five, only `pe` stays skipped, and it is contested.**
`Q26` candidate 3 argues that `files`' direct connection tuple defeats the "no entity" reason and
makes `files.mime_type` + `pe.machine` an `image:`/architecture source; the reopening is `Q26`'s to
make, not this section's. Separately, D3's *other* half — `analyzer`/`reporter` — was re-examined
under the `tfc` lens that promoted `ocsp` and **survives it**: both describe the sensor's own parsing
rather than network behavior, so a traffic profile has nothing to fit from either. That is a decline
now tested against all three lenses (entity, disclosure, output) and held under each.

**`analyzer` and `reporter` are declared skipped, as recommended** — with one amendment the
recommendation did not anticipate. "Zeek's own diagnostics, not traffic" is a correct
classification and *not* the same claim as "of no use here": a **disabled** analyzer is direct
evidence of a protocol the sensor could not see, and D5's inventory mechanism structurally cannot
notice it (no stem is produced, so there is no unparsed stem to name). Opened as **`Q28`** rather
than resolved inline, because the baseline's `analyzer.log` contents have never been looked at and
that measurement comes before the design. The skip reason string for `analyzer` must cite `Q28`, so
the next reader finds the follow-up instead of re-deriving it.

**"Leave unrecognized" is rejected for every stem**, including the ones D5 would have made
tolerable. After this ruling the unrecognized set is **empty on this baseline** — which is what
gives a *future* unrecognized stem any meaning at all. A permanently non-empty unrecognized list
trains an operator to ignore the field, the same failure D1 named for over-reporting.

### D4 — `ldap` vs `ldap_search`. **RULED 2026-08-12: resolved downward — `ldap` is skipped.**

`ldap_search` is parsed; `ldap` is not; both appear in 12/12 windows. Whatever the general answer to
D2, this specific split wants an explicit decision rather than inheritance.

**RULED 2026-08-12.** The asymmetry was real; it is resolved by declaring `ldap` skipped, not by
parsing it. `ldap.log`'s durable content (`opcode`, `result`, `version`, and `argument` — a bind DN,
credential-adjacent) is strictly **thinner** than `ldap_search.log`'s, and `ldap_search`'s own facts
were declined from projection one day earlier by the `Q27` ruling: *"scope/result is one query's
outcome, not something the entity IS; the fact that it serves LDAP already arrives via known_service
and listen_ports"* (`_SERVICE_NOT_PROJECTED["ldap"]`). That reason applies verbatim, and with more
force, to `ldap.log`. Resolving the split upward would add a parser whose output the store has
already ruled it does not want.

**Named rather than left implicit:** this leaves `ldap_search` parsed with **no fact reaching the
store**. `_add_service` returns early for a declined kind, so only its event count survives, in
`health["event_counts"]`. The consistent end-state is to retire that parser too — **not done here**,
because it would reverse a user ruling made the previous day. Recorded as a cleanup candidate for
whoever revisits `_SERVICE_NOT_PROJECTED`; it is a cost, not a defect.

### D5 — should "unrecognized" reach the belief store? **RULED 2026-08-12: yes.**

Today the information exists only in `ZeekResult.unrecognized` and **dies with the process.** A
network whose most interesting traffic is entirely unparsed produces a belief store indistinguishable
from one with full coverage. That is a provenance gap in a project whose central claim is that
imputed never masquerades as observed — "we saw nothing of this kind" and "we cannot see this kind"
are different statements, and only one of them is currently representable.

The natural home is the window health record, which already separates "no evidence" from "evidence
of none" (`windowing/`). This is the decision with the longest reach and the least urgency.

**RULED 2026-08-12: yes — and split by what is honestly per-window.** The health record is the home,
as proposed, but names and counts do not go to the same place, because only one of them is per-window
data at the layer that knows it.

- **Names → the window health record**, as `unparsed_log_types`: a **sorted** list, per window, of
  stems present in the input and not parsed. A name *set* is idempotent under union, so an operator
  aggregating windows cannot double-count it. Sorted, because nothing set-iteration-ordered may
  reach an output (CONVENTIONS §3, `LES-002`).
- **Counts → the `runs` table**, once per run. `plugin.events()` derives `unparsed` from
  `src.inventory`, which is **source**-level, not window-level; mirroring a source-level count onto
  every window would manufacture exactly the double-counting the name list avoids. `runs` already
  exists for run-scoped facts, and is already named in `NONDETERMINISTIC_TABLES`.
- **`None` means unaudited, not clean.** The same discipline `ingest_unordered` already carries in
  that dict, and for the same reason: a transport that reports no inventory yields `None`; an
  inventory that reported and found everything accounted for yields `[]`. A field that cannot say
  "I did not look" reproduces the D5 gap in a quieter form.

**Two traps for the build**, both consequences of `unparsed` being source-level and discovered
lazily inside a generator:

1. **Do not mint a synthetic `ts`** to carry this as an event. A `SensorHealthEvent` at `ts=0`
   creates a window at the epoch; one at "the first event's ts" attributes a whole-run fact to
   window 1 and leaves windows 2..N reading clean, which is the bug D5 exists to fix.
2. **It must reach every window of the run**, not only the one open when the inventory is read.
   Passing it as run-level state from the driver (`cli._pipeline`, which already wires plugin →
   windowing) is the shape that satisfies both traps; note that `unparsed` is computed on first
   iteration of `events()`, so a driver that reads it before iterating gets nothing.

A per-window refinement is possible later and deliberately not taken now: in **window-tree** mode
each window subdirectory has its own stem set, so unparsed-by-window is genuinely knowable there.
Flat and Parquet transports have no such resolution, and a field that means "per window" on one
transport and "per run" on another is worse than one that consistently means "per run".

## Build steps

1. **D1's sync test**, first and standalone. It can land with no coverage change and no ruling.
   **DONE, 2026-08-11** (`tests/test_acquire_zeek_stem_sync.py`, CHANGELOG 2026-08-11). Landing it
   *before* the coverage change in steps 2–3, rather than folded into them, is what surfaced two
   things while nothing yet depended on them: the `_SCHEMAS` gap named in step 3's amendment below,
   and four test sites that would otherwise have silently obstructed steps 2/3 — a hardcoded `x509`
   used as "the unrecognized stem" in four tests (`x509` is on D2's own promotion list), a hardcoded
   skip-tuple in `test_zeek_ingest.py` that would stop exercising a newly-skipped stem the moment
   step 2 adds one, and two exact-count pins (`== 16` / `== 4`) that step 3 would otherwise have had
   to fight. All four closed in the same round, test-only, no coverage change. This is the argument,
   demonstrated rather than merely asserted, for the sequencing this document already prescribed:
   land the check first, and it does the next two steps' homework for them.
2. **DONE, 2026-08-13** (CHANGELOG same date). **Declare the three skips** (amended 2026-08-12 to
   four, 2026-08-13 to three — `ocsp` moved to the parse list). `_SKIP` (`ingest/zeek/plugin.py`)
   **and** `KNOWN_SKIPPED_STEMS` (`acquire/harvest/zeek.py`) both gained: `pe`, `analyzer`,
   `reporter` — 4 → 7. The sync test from step 1 enforced that the two moved together. Each `_SKIP`
   value is the *reason*, in the register the existing four use; `analyzer`'s cites `Q28`, and
   **`pe`'s cites `Q26`** (candidate 3 contests this skip: `files`' direct connection 4-tuple defeats
   the "no entity" reason, and `files.mime_type` + `pe.machine` is a candidate
   `image:`/architecture source). Only `reporter` is declined outright — `ocsp` is no longer in this
   set at all (see above), so it was never a "declined outright" member here either; `pe` and
   `analyzer` are declined *with a named path back*, and a skip reason that hides that is the same
   silent-disagreement failure `LES-062` describes. **No `_SKIP` reason cites privacy** — none of
   these three do, which is why they survived the amendment.

   ~~**Independently landable**: no parser, no `_SCHEMAS` entry, no projection, and on its own it
   takes unrecognized from 14 to 11.~~ **False, and the build proved it wrong.** Declaring a skip
   grows `KNOWN_SKIPPED_STEMS`, which is half of `KNOWN_ZEEK_STEMS`
   (`= KNOWN_PARSED_STEMS | KNOWN_SKIPPED_STEMS`) — and `tests/acquire/test_zeek_runner.py` checks
   `KNOWN_ZEEK_STEMS <= set(_SCHEMAS)` **unconditionally**, with no carve-out for a stem that is
   skipped rather than parsed. So three `_SCHEMAS` entries (`acquire/harvest/zeek.py`) *were*
   required, and were written from real `#fields`/`#types` headers read out of the 2026-08-13
   baseline rather than from memory — `analyzer`/`reporter` from the same headers `Q24`'s "Headers
   verified" block records, `pe` from one of the two windows it actually appears in, a real observed
   header rather than the documented-shape guesswork `dhcp` is stuck with. **This is `LES-087`
   contradicted by text written after it, in this same document — see `LES-090`.** "No parser" and
   "no projection" both held; only "no `_SCHEMAS` entry" was wrong, and it was wrong in exactly the
   direction that would have let a fake-backed test exercise nothing while still passing — the
   failure mode `LES-087` exists to name.

   **2b. DONE, 2026-08-13** (CHANGELOG same date). **Reverse the privacy-motivated drops in the
   parsers that already exist.** Separate from the new stems, and was live code that contradicted a
   ratified principle:
   - `_ldap_search` now collects `base_object`, `filter`, `attributes`, `diagnostic_message` — its
     docstring used to call them "the log's densest identity content"; it now explains why they are
     collected instead of why they were dropped. These are also exactly the referenced-entity fields
     amendment 1 is about.
   - `_smb_files` now collects `name` (the filename). `_COLUMNS["smb_files"]` gained `name` too, so
     the transport requests the column on every path — including the Parquet one:
     `docs/SPEC_zeek_metadata_archive.md`'s Columns table widened to match, and a new test
     (`test_spec_columns_table_matches_the_code`) now checks the SPEC table against `_COLUMNS`
     directly rather than trusting the two to stay in step by hand.
   - `plugin.py`'s module docstring no longer claims parsers drop "the densest PII a log holds when
     it buys nothing downstream." That clause described a bug; it is deleted.
   - **`_COLUMNS["ntp"]` now takes `poll`** too — verified present in the baseline's `ntp.log`
     header. A poll interval is a *schedule*, which is what `tfc` is made of (DESIGN_BRIEF §9), so
     dropping it was both a forensic-completeness violation and the reason `Q27`'s `ntp` decline
     could not be re-ruled: there was no `poll` to project. Fixing the column does not, by itself,
     re-rule the projection — review surfaced a second, independent reason it stays declined even
     with `poll` in hand: `_ntp` keys the event on the *server* (`id.resp_h`), but `poll` on a
     mode-3 row is the *client's* own interval, so projecting it here would misattribute a client's
     schedule to the server. See `Q27`'s `ntp` bullet — that reason survives `Q29` closing, unlike
     "no field."
   - `_SERVICE_NOT_PROJECTED["smb_files"]`, `["ldap"]` and `["ntp"]` cited privacy, `known_service`
     and "no synthesizer field" respectively — all three were **rewritten off those void grounds**,
     onto the genuine question each kind now raises (`smb_files`: cardinality once `name` is in
     hand; `ldap`: `Q1`, a name-only subject; `ntp`: `Q29` plus the keying mismatch above). **No
     decline flipped** — all three kinds stay unprojected; only the *reason* changed, from void to
     real.
   - ~~Expect tier-2 fallout: adding attrs changes feature-file bytes, so the transport-equivalence
     and determinism gates must be re-baselined, not just re-run.~~ **False, and more instructive
     than step 2's own wrong claim.** `_add_service` (`windowing/windows.py`) returns on
     `proj is None` **before it ever reads `ev.attrs`**, for any kind absent from
     `SERVICE_PROJECTIONS` — and `ldap`, `smb_files`, `ntp` all are, today. `ev.attrs` is read in
     exactly one place in the whole `src/` tree, downstream of that gate. So the new attrs are
     structurally inert: no feature byte moves, and no re-baseline was needed (confirmed — tier 2
     passed unchanged). Worse for this document's confidence than the wrong claim itself: tier 2's
     synthetic fixture (`scripts/verify_determinism.py::build_synthetic_capture`) emits exactly six
     log types — `conn`, `dhcp`, `kerberos`, `ssl`, `notice`, `capture_loss` — and **never exercises
     `ldap_search`, `smb_files` or `ntp` at all**, so the gates passing was never going to be evidence
     either way, wrong claim or right one. See `LES-091`.
3. Per the D2/D3 ruling: add parsers to `_PARSERS` and the matching stems to `KNOWN_PARSED_STEMS`
   — the sync test from step 1 now enforces that both move together. **Amended 2026-08-11: also add
   a matching `_SCHEMAS` entry (`acquire/harvest/zeek.py`) for every newly-parsed stem.** `_SCHEMAS`
   is a sixth table keyed by the same stem vocabulary, outside D1's own two checks, and it fails
   *silently*: `FakeZeekRunner` reads it via `_SCHEMAS.get(stem, _FALLBACK_SCHEMA)`, a two-column
   (`ts uid`) fallback. A stem added to `KNOWN_PARSED_STEMS`/`_PARSERS` with no matching `_SCHEMAS`
   entry makes every fake-backed test — including the harvest→ingest→store→synth gate,
   `tests/test_acquire_ingest_chain.py` — pass while exercising a parser that has never seen a
   realistic row (demonstrated with `http`/`ssh`/`x509`, CHANGELOG 2026-08-11). Guarded now by
   `assert KNOWN_ZEEK_STEMS <= set(_SCHEMAS)` (`tests/acquire/test_zeek_runner.py`), mutation-proven
   — but that guard only fires if an entry is missing *entirely*, not if a present entry's columns
   are unrealistic, so treat it as a floor, not a substitute for a real fixture per new stem.

   **The six admits, as the ruling specifies them.** Each needs five things — `_PARSERS`,
   `_COLUMNS`, `KNOWN_PARSED_STEMS`, `_SCHEMAS`, and (for the five `ServiceEvent` kinds) a
   `SERVICE_PROJECTIONS` entry. `ntlm` needs no projection: it is an `IdentityEvent` and rides the
   resolver, not `_add_service`.

   **AMENDED 2026-08-12 — the "refused, deliberately" column is deleted, not narrowed.** Under
   forensic completeness a parser takes **every field the log offers**; the `_attrs()` list is "what
   this log has," not "what we decided to keep." `http`'s `username`/`password`/`uri`/`referrer`/
   `user_agent`, `snmp`'s `community`, `ssh`'s `host_key`, `ntlm`'s `username`/`domainname` are all
   collected. The `projection` column is unaffected — it governs what becomes a *published node
   feature*, which is a cardinality decision (`LES-017`), not a disclosure one, and a field can be
   carried on the event while staying out of the categorical vocabulary exactly as
   `software.unparsed_version` already is.

   | stem | event | projection → predicate, confidence |
   |---|---|---|
   | `http` | `ServiceEvent(kind="http")`, `ip=id.resp_h`, `port=id.resp_p` | `("host",)` → `serves_http`, **0.9** |
   | `ssh` | `ServiceEvent(kind="ssh")`, responder | `("server",)` → `serves_ssh`, **0.9** |
   | `quic` | `ServiceEvent(kind="quic")`, responder | `("server_name", "version")` → `serves_quic`, **0.9** |
   | `snmp` | `ServiceEvent(kind="snmp")`, responder | `("version", "display_string")` → `serves_snmp`, **0.9** |
   | `tunnel` | `ServiceEvent(kind="tunnel")`, responder | `("tunnel_type",)` → `terminates_tunnel`, **0.9** |
   | `ntlm` | `IdentityEvent(kind="ntlm")`, `ip=id.resp_h`, `hostname=server_dns_computer_name` (else `server_nb_computer_name`) | n/a — identity, not a service fact |
   | `smtp` | `ServiceEvent(kind="smtp")`, responder **+ referenced addresses from `path`/`x_originating_ip`** | `("helo",)` → `serves_smtp`, **0.9**; referenced hosts are a separate assertion, see below |
   | `sip` | `ServiceEvent(kind="sip")`, responder | `("user_agent",)` → `serves_sip`, **0.9**; extensions need `Q1` |
   | `ldap` | `ServiceEvent(kind="ldap_op")`, responder | `("opcode",)` → `serves_ldap`, **0.9**; DNs need `Q1` |
   | `x509` | `ServiceEvent(kind="x509")`, **joined to a connection via `ssl`'s cert-chain file ids**. Key column is **`fingerprint`**, SANs are `san.dns`/`san.uri`/`san.email`/`san.ip`, cert fields are `certificate.*` — verified, not guessed | SANs need `Q1` for the hostnames; no projection until then |
   | `ocsp` | **no event mapping decided — `Q29`.** Verified to carry *no* connection fields, so it cannot be a `ServiceEvent` (which requires `ip`). Its value, `nextUpdate − thisUpdate`, is a population-level cadence, and the store has no channel for a subject-less fact | blocked on `Q29`; do **not** invent a subject to get it landed |

   Note `ldap` takes kind `ldap_op`, because `ldap_search`'s parser already owns kind `ldap` — two
   stems, one kind name, and `SERVICE_PROJECTIONS` is keyed by kind. Getting this wrong silently
   merges two different facts under one predicate.

   **The last four rows are blocked on `Q1`, and only for their referenced-entity half.** Their
   responder-keyed service facts land today; their *names without addresses* (LDAP DNs, SIP
   extensions, x509 SANs) have no subject under `entity == ip`. `smtp` is the exception worth taking
   first: `path` and `x_originating_ip` yield **IP addresses**, which are valid subjects right now.

   Four things about the table that are load-bearing, not incidental:
   - **`0.9` everywhere is deliberate consistency, not laziness.** All five are observed protocol
     exchanges — the same evidence class as `ssl`/`dce_rpc`/`smb`, which already sit at 0.9. `http`'s
     `host` and `quic`'s `server_name` are client-asserted, exactly like `ssl`'s `server_name`, so
     they get `ssl`'s number or the banding stops meaning anything.
   - **`ssh` at 0.9 is the one judgment call.** A version banner is editable, which is why `software`
     sits at 0.7. It is kept at 0.9 because `ssh.log`'s `server` is read off the handshake with the
     responder itself rather than inferred from a third party's header, and the entity it describes
     is the one that emitted it. If tier 3 shows the banners are junk, drop it to 0.7 — on the data.
   - **`terminates_tunnel` departs from the `serves_*` convention on purpose.** The rule in
     `SERVICE_PROJECTIONS`'s docstring is that the predicate must not lie about the keying: this is
     responder-keyed like the rest, and the responder does terminate the tunnel, but "serves" would
     imply an offered service. Accuracy wins; document the departure at the entry so the next reader
     does not "fix" it.
   - **`http` is the expected truncation site.** `SERVICE_MAX_KEYS = 32` was measured against a
     population where no entity exceeded 13 distinct facts — but a **forward** proxy keyed on
     `id.resp_h` accumulates one `host` per external site visited, and an enterprise network is more
     likely to have one than the CDN case that docstring already names. Truncation is counted, not
     silent, so this is a number to *read* in tier 3, not a reason to raise the cap pre-emptively.

   **`snmp` carries the one unmeasured claim in the whole ruling.** `display_string`'s population on
   the baseline is unknown; it was admitted on what sysDescr *is*, not on what this capture contains.
   Confirm it in tier 3 **before** anyone describes it as an `image:` feed — `Q26`'s lesson applied to
   the one admit that could repeat it. If `display_string` is empty across all 4 windows, `snmp`
   reduces to `("version",)`, which is thin enough to reconsider the admit entirely.

   **LANDED 2026-08-13, all eleven stems.** Ten landed first (`ocsp` deliberately excluded at that
   point, per D2's third amendment above — still `Q29`-blocked, no event mapping decided); `ocsp`
   landed the same day, once `docs/WORK_ORDER_subject_less_facts.md` closed `Q29` and gave it a
   subject-less channel to land in (see that work order and its own CHANGELOG entry — `ocsp` needed
   no re-review here, since it was designed and built against this round's already-settled `_SCHEMAS`/
   `_COLUMNS` conventions). **Code review of the first ten found five must-fix items and several
   should-fixes; two needed, and now have, a user ruling — both REVERSE rows of this section's own
   table, recorded here rather than silently edited in place:**
   - **`R1` — `smtp` and `sip` move from the table's ruling above to DECLINED**
     (`windowing.windows._SERVICE_NOT_PROJECTED`), reversing their `parse` rows. Both `helo` and
     `user_agent` are the CLIENT's own self-announced fields, but `_smtp`/`_sip` key the event on
     `id.resp_h`, the SERVER — projecting either publishes the client's claim as a property of the
     server that merely received it, a KEYING bug the original ruling did not check for. Measured
     on the real baseline: `(50.209.234.65, serves_sip, "friendly-scanner")` — a SIPVicious
     scanner's own tool name published as a durable property of the host it scanned. Every field
     (`helo`, `user_agent`, and everything else) stays collected on the event regardless — this is
     not a disclosure reversal.
   - **`R2` — `snmp` stays exactly as ruled.** `display_string` was measured EMPTY in 7/7 real
     baseline rows (this section's own "unmeasured claim" reopened and tripped), but the user ruled
     to keep the admit: an unset field never enters `attrs`, so nothing false publishes; the tuple
     activates automatically if a later capture populates it; and 7 rows is too small a sample to
     reverse an admit on. See `windowing.windows.SERVICE_PROJECTIONS`'s own comment for the
     recorded measurement.
   - **`ntlm`'s confidence moved from `0.7` to `0.9`** (`learners/extractors.py`, now declared in
     `windowing.windows.IDENTITY_PROJECTIONS`) — a tester-caught gap, not a table row this
     document ever ruled on: the inline confidence literal `extract_assertions` used predated
     `ntlm` and was never revisited when it landed, so it silently inherited `dns`'s "third
     party's claim" tier despite `_ntlm`'s own docstring arguing the opposite.
   - **`smtp`'s referenced-entity half is a NAMED REMAINDER, not built (`S8`).** The table above says
     `path`/`x_originating_ip` yield IP addresses usable today, and they ARE collected on the
     event — but turning them into `referenced`-evidence-class assertions (DESIGN_BRIEF §7, never
     emitted anywhere in `src/` today) needs a real design for what subject/predicate/object shape
     a referenced-only fact takes, which this round did not attempt rather than guess at one that
     `Q1`'s eventual resolver work might contradict. Whoever picks up `Q1` should treat this as the
     first concrete consumer waiting on it, not rediscover that `smtp` was the exception worth
     taking first.
   - Two hazards found and fixed that this document's step 3 text did not anticipate: a
     Hive-partition-key collision (`smtp`/`sip`'s `date` field name collides with the archive's own
     `log=<stem>/date=<D>` layout, fixed with `hive_partitioning=false` at the reader rather than
     by excluding the field — `docs/SPEC_zeek_metadata_archive.md` "Columns"), and a writer-side
     mirror of `LES-092` (`scripts/make_zeek_archive.py::_coerce` reprinted a JSON-lines vector
     field's native `list` instead of joining it, caught only because neither field it was tested
     with was ever projected — `LES-091`'s mirror trap, from the writer's side).
   - `stats.log`'s own two live counters, `pkts_proc`/`bytes_recv`, joined `_stats`'s emitted
     `SensorHealthEvent` kinds in the same round (a separate, later user ruling) — see
     `windowing.windows.SENSOR_HEALTH_AGGREGATION` for why they reduce with `sum`, not the `max`
     every other kind here uses.

   This round's own arithmetic still holds — the "28 + 7 = 35" self-check counts PARSED vs
   SKIPPED stems, not which parsed ones project to an assertion, so `R1`'s decline of `smtp`/
   `sip` moves nothing in it: both stay parsed, and with `ocsp` now landed the same day, **28 + 7 =
   35 is built, not merely declared** — `KNOWN_PARSED_STEMS`/`_PARSERS` are 28 in code today. Note
   for whoever reads that self-check next: it was never a claim about projection, and should not
   be read as one now that this round made the parsed/projected distinction matter more. Prose
   counters (`CLAUDE.md`, the module docstrings) were step 5's job, executed the same round — see
   that step's own section below for what a second application of its site-list discipline found.
4. Per D5: `unparsed_log_types` (sorted names) into the window health record, counts into `runs`,
   `None` for unaudited. **Read D5's two traps first** — no synthetic `ts`, and every window of the
   run rather than only the first.

   **DONE, 2026-08-13** (CHANGELOG same date, code-review round). Landed as D5 specified —
   `unparsed_log_types` (sorted stem names, per window, in the health record) and counts
   (`runs.unparsed_log_types`, once per run) both real, `None` meaning unaudited throughout — plus
   one addition D5 did not ask for: `unrecognized_log_types`, the subset with no `_SKIP` entry,
   because a store consumer may not import `ingest`'s `_SKIP` to recover that split later
   (CONVENTIONS §3), and `_SKIP` is this build's own table while the store is read as-of-T across
   builds. **D5's own two named traps were both real and both caught.** No synthetic `ts`: the
   mechanism runs entirely inside `ZeekPlugin.events()`, before its own first `yield`, and reaches
   `WindowManager` through a zero-arg provider callable read fresh at every `_close()`, never cached
   at construction — proven by a test that fills the provider's value only *after* `WindowManager` is
   already constructed. Every window of the run, not only the first: proven by a three-window fixture
   asserting the identical list on every window, including window 1.

   **A third hazard, named nowhere in D5's own text, is what most of the review round was actually
   about.** D5 assumed a source can always say what it did not parse. `ParquetArchiveSource` cannot —
   its own converter (`scripts/make_zeek_archive.py`) never writes a skipped or unrecognized stem into
   the manifest at all, so `unparsed`/`unrecognized` would read `{}` on every archive-transport run
   regardless of what the underlying capture actually contained: not "audited, and clean," but
   "structurally incapable of finding out," indistinguishable from the honest case by anyone just
   reading the value — the D5 gap reopening one layer down, inside a transport rather than inside the
   plugin. Closed by a new `ZeekSource.inventory_is_complete` contract, defaulting `False` (reversing
   this field's own first landing, which defaulted `True` and would have handed every future transport
   a free "I audited everything"). `LogDirSource` sets it **per instance** inside `streams()`, not as
   a class constant, because completeness varies by locator *shape* within that one class: measured, a
   single-log-file locator reported `unparsed_log_types: []` across four real windows with seventeen
   unaudited stems sitting beside the one file it read — a false "every stem accounted for." Review
   also found a `runs` schema-drift bug (a store built by a pre-D5 build commits every window, then
   dies on the final `INSERT` because `CREATE TABLE IF NOT EXISTS` does nothing for a new column,
   fixed with a schema guard at `__init__`) and a transport-equivalence gate carve-out that had quietly
   downgraded from byte equality to semantic equality for the whole `windows.jsonl` file (fixed by
   carving at the byte level). See the CHANGELOG entry for both in full; `LES-103`–`LES-106` record all
   three as durable rules, and `Q30` records the archive-manifest limitation itself, which
   `inventory_is_complete=False` works around rather than closes.
5. **EXECUTED 2026-08-13, same round as step 3.** Update `CLAUDE.md`'s "17 log types parsed, 4
   skipped" → **28 parsed, 7 skipped** once step 3 lands — a counter in prose that `doc-stats` does
   **not** police (it polices tests/plugins/lessons only). **The skip half landed with steps 2/2b,
   2026-08-13**: "4 skipped" became "7 skipped" in `CLAUDE.md` and everywhere else the pair is
   quoted; the parsed half ("17" → "28") landed in this same pass, now that step 3 has.

   **This step undercounted its own site list once already, and the correction is worth recording
   because it is the same shape of miss as step 2's.** It named exactly two sites beyond
   `CLAUDE.md`: "the same claim appears twice more, in `acquire/harvest/zeek.py`'s module docstring
   and in the comment above `KNOWN_PARSED_STEMS`." **There are four `\b16\b` sites in that one file,
   not two.** Three are live claims about the current parser count and move together: the module
   docstring, the `KNOWN_PARSED_STEMS` comment, **and the `ScriptPolicy` docstring** ("`aximprint`'s
   16 parsed types") — all three had gone stale by one since the `software` round and were bumped to
   17 in the same 2026-08-13 pass that fixed the skip counter. **The fourth is different in kind and
   must NOT be bumped**: the `DEFAULT_SCRIPT_POLICY` comment at `zeek.py:226` — "5 of the 16 log
   types `aximprint` parses" — is a **measured denominator from a 2026-08-06 probe**
   (`zeek -r <pcap>` with no scripts vs. with `local`), not a live claim about today's parser count.
   Treating it as the third stale site and bumping it to 17 would misrepresent a measurement that was
   never re-taken at the new count. It needs **date-anchoring** instead of bumping — "5 of the 16 log
   types `aximprint` parsed *at that date*," plus a note that the measurement's denominator and
   today's parser count are not the same claim (applied 2026-08-13). See `LES-093`.

   **"Verify it held" (this round, closing out step 5 a second time): the *discipline* held, the
   *numbers* did not.** The three-bump-one-anchor classification from the paragraph above was still
   the right shape once step 3 landed eleven more parsers — but nothing had re-*applied* it: all
   three live-claim sites were still sitting at `17` in the working tree right up until this pass,
   because build step 3 added parsers without revisiting the prose that counts them, exactly the gap
   step 5 exists to close. Bumped to `28` (module docstring, `KNOWN_PARSED_STEMS` comment,
   `ScriptPolicy` docstring); the `DEFAULT_SCRIPT_POLICY` comment's *own* live-claim parenthetical —
   "(`aximprint` parses 17 today, `software` having joined 2026-08-11 …)", added when the fourth site
   was date-anchored rather than bumped — needed the identical treatment, since it is a live claim in
   its own right and was never one of the original four `\b16\b` hits (it already read `17`, correctly,
   when it was written). **A fifth site neither this document's step-5 text nor `LES-093` ever named:**
   the `_SCHEMAS`-coverage comment above `_SCHEMAS` itself (`KNOWN_ZEEK_STEMS`'s own header-shape
   count, distinct from `KNOWN_PARSED_STEMS` alone) had drifted from `20/20` through the `software`
   round to `24/24` through build step 2 — correctly, at the time — and was still reading `24/24`
   after step 3 landed eleven more parsed stems, the exact `LES-087`/`LES-090` shape recurring a
   second time in the same comment. Bumped to `35/35`. All five sites now agree with
   `KNOWN_PARSED_STEMS`/`KNOWN_SKIPPED_STEMS`/`KNOWN_ZEEK_STEMS` as built. **28 + 7 = 35 exactly, now
   true of the code as well as the arithmetic** — the self-check this document has quoted since D1
   finally has nothing left to reconcile it against.

## Verification

The cold-tier baseline at `/tmp/aximprint-zeek-test/` is 12 windows of real logs covering all 35
stems — **run the ingest path over it and confirm the newly-parsed stems produce events**, rather
than trusting that a parser compiles. **Amended 2026-08-12:** reading that baseline needs no
permission and carries no disclosure constraint — privacy is retired (CONVENTIONS §3). What remains
is `LES-003` as repo hygiene: the run's *output* is not a repo artifact, and this document records
counts and behavior rather than captured rows because a doc that dumps rows is unreadable, not
because the rows are forbidden.

Five things the tier-3 run must actually answer, added with the 2026-08-12 ruling — a parser that
compiles and emits is the floor, not the check:

1. **`snmp.display_string` — populated, or empty?** The ruling's one unmeasured claim. Empty across
   all 4 windows reduces `snmp` to `("version",)` and reopens the admit.
2. **`http`'s per-entity `host` cardinality**, and whether `services_truncated["http"]` fires. If a
   forward proxy pins the cap at 32, that is the measurement that justifies revisiting
   `SERVICE_MAX_KEYS` — with a number, the way `Q27` did.
3. **`ntlm`'s hostname yield**: how many distinct `(ip, hostname)` bindings, and how many of them
   `dns`/`kerberos`/`dhcp` did **not** already supply. A second source of a binding nothing else
   provides is the whole argument for the stem; if it duplicates DNS, say so.
4. **`ssh.server` banner quality** — real version strings, or junk? This is the input to the one
   confidence judgment in the table (0.9 vs 0.7).
5. **The unrecognized set is empty.** The arithmetic (28 + 7 = 35) says the tables are consistent;
   only a run over the baseline says they match what Zeek actually writes.

**ANSWERED 2026-08-13**, against `/tmp/aximprint-zeek-test/`, recorded in full in `Q24`'s "content
measured 2026-08-13" postscript (`docs/OPEN_QUESTIONS.md`) rather than duplicated here: (1)
`display_string` is empty in 7/7 rows — the reopen condition tripped, and the user ruled to keep the
admit anyway (`R2`, this document's own step 3 section above). (2) `http` tops out at 3 distinct
hosts per entity on this baseline; `SERVICE_MAX_KEYS` truncation never fires. (3) `ntlm` yields 10
distinct `(ip, hostname)` bindings from 158 rows, **0** already supplied by `dns.log` — the
affirmative case for the stem. (4) `ssh.server` is one opaque, genuinely-observed token
(`SSH-2.0-7f27de7`, GitHub's own range, 2717/2864 rows) — the evidence-quality judgment behind
confidence `0.9` stands; the content hope (admin-surface diversity) does not. (5) Confirmed empty —
`ZeekResult.unrecognized` is `()` over the full baseline once all 28 parsers and 7 skips are in
place.

And the check the ruling itself cannot do: `KNOWN_ZEEK_STEMS <= set(_SCHEMAS)` fires on a *missing*
entry, never on a present one whose columns are wrong. Six new `_SCHEMAS` entries written from
documented shapes rather than observed headers is six chances for a fake-backed test to exercise a
parser against a row Zeek would never write. Diff each new entry's field list against the baseline's
own `#fields` header for that stem while the tier-3 run is already open — it is the cheapest
verification in this document and the only one that catches a plausible-but-wrong schema.

Synthetic fixtures will need extending for the new stems too, but they are the weaker check here —
this whole round exists because 1000+ synthetic tests could not see what one real capture showed.
