# Work order — an axquery ingest plugin: the second sensor

**Status:** DRAFTED 2026-08-19. **D1 ruled 2026-08-19 and its carve-out ruled 2026-08-20**; D2
and D5 settled by measurement; **D8 ruled**; D3/D4/D6/D7 ruled by Claude Code as delegated
(event-model and transport calls, none of them gated). **BUILD STEP 2 LANDED 2026-08-20** — `/api/connections` -> `FlowEvent` + coverage ->
`SensorHealthEvent`, verified end to end into a belief store and a materialized PIT. Steps 3-5
are **declined on measurement**, not deferred by preference; see "Build result" below.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes nothing.** It is the first exercise of DESIGN_BRIEF §0.1's per-sensor plugin
contract by a **second** sensor, which is the thing that contract was written for and has never
been tested by. `zeek` stays; this is an additional plugin (user-ruled 2026-08-19).

## What axquery is

`fx-cap/core/analytics/axQuery/` in the Axellio Xpress platform monorepo (`/home/rocky/fx-cap`) —
a C++ HTTP server querying the **axAnalytics entity database** directly. Not a log producer: a
query layer over the appliance's own analytics DB, on the same appliance `acquire` already pulls
pcaps from. It serves a Prometheus API (irrelevant here) and REST JSON endpoints (the subject).

This is a **structurally different sensor from Zeek**, and the difference is the whole work order:
Zeek hands us records and we infer; axquery hands us an entity database and has already inferred.

## The instance and the dataset

Running locally, `127.0.0.1:19090`, `--auth-disabled` — **no credentials needed**, which is why
this plugin is buildable without the appliance gating that governs `acquire`.

```
53 chunks · 19.2 B packets · 19.57 TB · 1,420,319 entities · 144.75 M events · 212 threads
site axellio-hq / system corp-apcn-rdr1 / 12 streams
```

**The dataset is NOT the ten-day Zeek capture, and does not overlap it.**

| | span | source |
|---|---|---|
| axquery | 2026-05-24 01:11 → 2026-06-19 01:11 UTC (**26.0 d**) | `/net/devsrv/home/data/v29_test` |
| Zeek PIT `pit-bf1f8c233b-1786978566` | 2026-08-07 15:00 → 2026-08-17 15:00 (10.0 d) | `harvest-corpapcn-10d/zeek` |

Same site, same system, same twelve streams, **49.6 days apart with zero overlap**. The two
sensors can be compared structurally on one network; they cannot be joined or cross-validated
row-for-row on this data. Any acceptance criterion that assumes otherwise is unbuildable today.

## Measured endpoint inventory

Every field below was read off the running instance, not from documentation.

| Endpoint | Fields | Maps to |
|---|---|---|
| `/api/connections` | `origIp` `respIp` `origPort` `respPort` `transport` `protocol` `origBytes` `respBytes` `origPackets` `respPackets` `tsSec/tsNsec` `lastUpdateSec/Nsec` `vlanId` `ethType` `severity` `artifact{present,count}` `location` `systemName` | `FlowEvent` |
| `/api/events` | the above **plus** `community_id`, `ip_ver`, and `extents[]` (`type` `tsOffsetNs` `durationUs` `origPkts` `origBytes` `respPkts` `respBytes`) | `FlowEvent` + sub-bucket truth |
| `/api/entities` | `ip` `mac` `firstSeen` `lastSeen` `apps[{type,connections}]` | `ServiceEvent` |
| `/api/devices` | `ip` `mac` `deviceRoles` `roles[]` `binding{confidence,source}` | role evidence (see D1) |
| `/api/identity/users` | `samAccountName` `realm` `accountType` `authCount` `failCount` `systemCount` `firstSeen` `lastSeen` | `IdentityEvent` |
| `/api/info` | `sites[].location` `systems[].name` `.streams` `.first_ts` `.last_ts` `chunks` | `probe_vantage()` |

**Three things Zeek never gave us**, all real:

- **`coverage`** on every time-filtered response — `{complete, reason, chunksWanted, chunksCovered,
  chunksMissing, fraction, requestedAfterSec, requestedBeforeSec, coveredAfterSec, coveredBeforeSec}`.
  A sensor-supplied completeness statement against the **requested** range. The window health
  record's honesty discipline has never had a native producer for this; now it does.
- **`extents[]`** — measured burst structure per flow. `windowing`'s 12 sub-buckets approximate
  exactly this by construction; here it is stated.
- **`community_id`** — a standard cross-sensor flow key. Zeek can emit it too. It is the only
  mechanism by which the two sensors could ever be joined, and it costs nothing to carry now.

**Two things measured empty in the ENTITY projection — scope of that finding corrected below:**

- `0 of 5,000` entities sampled carry a non-zero MAC; `2 of 925` devices do.
- **`binding{confidence,source}` is unpopulated** — `{0,0}` on `923 of 925` devices.

**Do not generalize this to "axquery has no MAC".** Those are two *derived projections*.
Conn-level MACs were measured on 2026-08-20 and **do** populate (`conn.orig_mac`/`conn.resp_mac`)
— see "Measured 2026-08-20" below, and D8, which is about what they MEAN rather than whether they
are there.

## Decisions

**D1 — evidence class. RULED 2026-08-19 by the user, with one carve-out proposed.**
axquery's API outputs enter the event model as **observations**; aximprint does **not** add a
parallel learner layer to re-derive what the sensor already states. ML enters later as a separate
path (a `source` in the evidence plane, per `docs/WORK_ORDER_ml_layer.md` D1 — unruled).

**The carve-out — RULED 2026-08-20 by the user, as proposed below.** `src/aximprint/catalog/` fixes `has_role` as **`imputed`,
never `observed`** — "nobody observed 'this is a domain controller', only that it answered on 88
and 389." If `/api/devices` roles land as `observed`, the same field carries a *stronger* evidence
class when a foreign system concludes it than when aximprint concludes it from the same kind of
behavior. **Ruled:** axquery's **measurements** (`/api/connections`, `/api/events`, `/api/entities.apps`,
extents, coverage) are `observed`; axquery's **conclusions** (`/api/devices.roles`) are `imputed`
with `source: axquery`. Everything else in D1 stands. This keeps a foreign system's conclusion from
outranking aximprint's own conclusion drawn from the same kind of behaviour, which is what
`catalog/roles.yaml`'s "`has_role` is `imputed`, never `observed`" exists to say.

**D2 — `FlowEvent.state_class` from TCP flags. REVISED 2026-08-19; the first draft was wrong.**
The draft claimed axquery "reports no TCP state at all". False, and it came from generalizing off a
two-row sample that happened to be UDP. `axEvConTcpT` (`core/analytics/analytics/axEvents.h`)
carries `state_history[8]` ("History of connection state as it changes", with an `axTcpHist`
overflow pointer), the flags `no_syn` / `z_win` / `rto` / `keep_alive` / `dup_pkt`, and
`oTCPflags`/`rTCPflags` — the cumulative OR of TCP flags per direction. `connections.cpp:179-192`
emits the last pair as `origTcpFlags`/`respTcpFlags`, **gated on `group == DBIG_TCP_EVENT` /
`DBIG_TCP6_EVENT`**, which is why a UDP sample never shows them.

So `state_class` has a real producer: SYN / SYN-ACK / FIN / RST presence per direction is the same
raw material Zeek computes `conn_state` from. Deriving `SF`/`S0`/`REJ`/`RSTO`/`RSTR`/`SH`/`OTH`
from two flag bytes is a computation over observations, which D1 permits.

Two things still need a ruling. **(a)** `state_history` — the nearest analogue to Zeek's `history`
field — appears in the DB but **not** in the row-field registry and **not** in `connections.cpp`;
no API exposure was found. Confirm, then either derive without it or file it as an axquery request.
**(b)** UDP and ICMP conns carry no flags at all, so `state_class` for non-TCP still needs the
draft's answer: `"unknown"`, counted and disclosed, never silently defaulted (`LES-006`).

**D3 — VLAN is PER-DIRECTION, and the REST field is a lossy projection. REVISED 2026-08-19.**
`FlowEvent` still has no VLAN field and `ingest/base.py` still says enriching downstream is an
event-model change rather than a plugin hack — that part of the draft stands. What the draft got
wrong is the shape. `axEvConEth` carries `OrigVlanId` **and** `RespVlanId` separately, with the
reason stated in the source: *"Asymmetric VLAN tagging happens (capture point sees one side
trunked, the other access), so a single connection-level VLAN was lossy."* The `vlanId` key on
`/api/connections` is the orig-side back-compat projection (`get_VlanId`: *"emits orig-side VLAN
for back-compat with consumers that expected a single VlanId column"*), and `0` means *direction
not observed tagged*, not *untagged*.

Proposed, revised: read `conn.orig_vlan_id`/`conn.resp_vlan_id`, not `vlanId`; add **two** fields
to `FlowEvent` (`vlan_src`/`vlan_dst`, `Optional[int]`), free during incubation (`LES-001`, not a
schema ratification); and treat `0` as unknown rather than as VLAN 0 — the same
`None`-is-not-a-value discipline `probe_vantage` already applies. A single `vlan` field would
reproduce, inside aximprint, exactly the lossiness axquery already backed out of.

Adjacent, same source: `conn.orig_ttl`/`conn.resp_ttl`, `conn.orig_rtt_us`/`conn.resp_rtt_us`,
`conn.orig_oos_pkts`/`conn.resp_oos_pkts`, `conn.dscp`, `conn.tunneled`, `conn.ip_multicast`.
Initial TTL per direction is an OS-fingerprint signal and RTT is a topology-distance signal; both
are `observed` and neither has any producer in this project today. Out of scope for D3, worth a
follow-up.

**D4 — `local_src`/`local_dst` have no producer.** Zeek supplies `local_orig`/`local_resp` from
its own `local_nets` verdict, 100% populated, and `segments.py` reads it. axquery declares no
locality. Every axquery-sourced flow therefore falls to the RFC1918 fallback at `imputed` 0.6 —
the first `imputed` this project ever emitted, and now the *default* for a whole sensor.
Proposed: accept it, and make the plugin's caveat explicit rather than letting the fallback be
discovered downstream. D3 partially rescues this if VLAN lands.

**D5 — paging.** `/api/connections` returns `count` alongside `totalMatches` (2 vs 67 at
`limit=2`); the documented params are `limit`, filters, and `after`/`before` — **no offset or
cursor was observed**. The natural read is therefore to page **by time**, one request per window,
which suits `cli._pipeline`'s windowed driver exactly and makes the per-response `coverage` block
a per-window health record for free. Needs measurement before build: what `limit` does at window
scale, and whether truncation is detectable other than `count < totalMatches`.

**D6 — the source locator and `probe_vantage`.** Proposed `axquery:<base-url>`, mirroring the
existing `parquet:<root>` convention. `probe_vantage()` is implementable properly for the first
time from a non-harvest source: `/api/info` yields `location`, `systemName` and `streams` in one
cheap call, before any event is pulled — precisely the contract's tier-1 shape. Note the vantage
identity axquery reports (`axellio-hq/corp-apcn-rdr1`, 12 streams) is **not** the string the Zeek
side records (`https://10.80.65.7:0-11`), so the same tap has two spellings and `compare` gates on
vantage-set equality. That needs reconciling or the two sensors' PITs are permanently incommensurable.

**D7 — timestamp units are inconsistent across endpoints.** `/api/entities` returns ISO-8601
strings (`"2026-06-07T23:52:53.309Z"`); `/api/identity/users` returns **nanosecond integers**
(`1780906203446124739`); `/api/connections` returns split `tsSec`/`tsNsec`. Three representations
in one API. Proposed: one normalization helper in the plugin, fail-loud on an unrecognized shape,
never a silent `float()` coercion.

## Build steps (after ruling)

1. `ingest/axquery/` — `sources.py` (HTTP transport, time-sliced, coverage extraction) and
   `plugin.py` (the endpoint→event table), mirroring `zeek/`'s transport/semantics split.
2. `/api/connections` → `FlowEvent` (D2, D3, D4) + the `coverage` block →
   `SensorHealthEvent`. This alone is a runnable end-to-end pipeline.
3. `/api/entities.apps` → `ServiceEvent`; `/api/devices.roles` → role evidence at D1's class.
4. `/api/identity/users` → `IdentityEvent`, using `accountType` for the host/user scope split —
   `IDENTITY_SCOPES` already needs a per-field split for `radius`
   (`docs/WORK_ORDER_zeek_stems_wide_vantage.md` D1); this is the same shape from a second
   producer, so **rule those two together**.
5. `/api/events.extents[]` → sub-bucket population from measurement instead of approximation.
6. `probe_vantage()` from `/api/info` (D6). `unparsed_stems`/`unrecognized_stems` stay `None` —
   the honest default for a plugin with no stem inventory (`LES-006`); do **not** emit `{}`.

**Acceptance:** `aximprint ingest --source axquery:http://127.0.0.1:19090` produces a belief store
and a materializable PIT over a bounded slice; the per-window health record carries axquery's own
`coverage.fraction`; no `FlowEvent` is emitted with a silently-defaulted `state_class`; and the
plugin's caveats name D4's locality gap explicitly. **Not** an acceptance criterion: agreement with
the Zeek PIT — the two datasets are 49.6 days apart.

## Measured 2026-08-20, after a restart

The instance was OOM-killed at 20:31:04 on 2026-08-19 under its cgroup cap (`anon-rss 25 GB`)
while this work order was being researched — caused by walking `limit` up
(`/api/entities?limit=5000`, `/api/devices?limit=5000`, `/api/connections?limit=2000`) against a
`--lazy-cold` server, which faults cold chunks in without bound. Restarted under a transient
`systemd-run --user -p MemoryMax=30G` scope; ready in ~90 s from the `--axi-dir` shard reload,
steady-state RSS 5.6 GB. **This is a transport constraint, not just an incident: an ingest plugin
that pages by `limit` can kill this sensor.** Page by time and by `offset`, keep `limit` small.

**Everything below is measured on window `after=1780872000&before=1780875000`.**

### TCP state IS derivable (settles D2)

`origTcpFlags`/`respTcpFlags` populate on **67 of 67** TCP rows. Decoded pairs:

| orig / resp | meaning | n | Zeek analogue |
|---|---|---|---|
| `SYN+PSH+ACK` / same | established, no teardown seen | 18 | `S1` / open at window edge |
| `FIN+SYN+PSH+ACK` / same | clean open **and** close | 14 | `SF` |
| `…+RST…` on one side | aborted by that side | 17 | `RSTO` / `RSTR` |
| `SYN` / `none` | attempt, no reply | 5 | `S0` |
| `PSH+ACK` or `ACK`, no SYN | mid-stream capture | 5 | `OTH` |
| `FIN+SYN+PSH+ACK` / `none` | one-sided | 1 | `SH` |

The 8-class `FLOW_STATE_CLASSES` vocabulary is populatable from two bytes. (Values above 63 carry
ECN `ECE`/`CWR` — decode all 8 bits, not 6.) `state_history[8]` remains **DB-only**: absent from
the row-field registry, from `connections.cpp`, and from every endpoint probed. Derive without it.

### MAC populates — and its MEANING depends on fanout (supersedes the "MAC is absent" finding)

`conn.orig_mac`/`conn.resp_mac` are real: `f0:18:98:ec:c9:d5`, `00:3a:9c:7f:6a:87`, … The earlier
`0 of 5,000` zero-MAC result was an artifact of the **`/api/entities` projection**, not of the data.

But a naive MAC binding here would be a catastrophe. Over 200 rows / 38 distinct MACs:

| MAC | distinct IPs behind it | private / public |
|---|---|---|
| `08:f1:b3:88:42:3a` | **46** | 16 / 30 |
| `40:0f:c1:3c:72:35` | **43** | 1 / 42 |
| `00:3a:9c:7f:6a:87` | 2 | 2 / 0 |
| `c4:14:a2:07:aa:80` | 2 | 1 / 1 |

The high-fanout MACs are **next-hop routers**, not hosts. Binding on them would merge 46 unrelated
hosts into one entity — the unrecoverable false merge (`docs/SESSION_STATE.md`: *"A false merge
fabricates a host that never existed"*). **New decision needed, D8 below.**

### Paging is solved, and `totalMatches` is not a total (revises D5)

`/api/search/rows` returns `offset`, `limit`, `nextOffset`, `sortedBy` and `totalMatches` — real
cursor paging, and the endpoint the plugin should be built on rather than `/api/connections`.
**Caveat, measured:** `totalMatches` grows with `limit` in the same window (67 → 68 → 115 → 185 at
`limit` 2 → 3 → 50 → 120). It is a discovered-so-far count, not a population size. A plugin that
treats it as a total will silently under-read. Page until `nextOffset` is absent, never to a count.

### Two API gotchas that cost time

- **`proto=` filters the L7 label, not the transport.** `proto=tcp` returns `0` in a window holding
  24 TCP rows, because `protocol` is `other`/`dns`/`tls`/`ssh`. Filter transport client-side, or by
  the presence of `origTcpFlags`.
- **`conn.duration_sec` exists** (1132 s, 119 s observed) — `FlowEvent.duration` has a real
  producer; do not derive it from `lastUpdateSec - tsSec`.

## Decision added

**D8 — RULED 2026-08-20 (delegated by the user: *"rule D8 and radius split together"*).**
Ruled jointly with D1 of `docs/WORK_ORDER_zeek_stems_wide_vantage.md`; the combined ruling is
below, and the same text is recorded there.

## Combined ruling — MAC fanout (D8) and the `radius` per-field split

### R1 — the "first per-field scope split" is withdrawn: it already exists

The stems work order says `radius` "is the first stem needing **per-field** scope assignment"
because "every existing kind maps whole-stem to one scope". **That is wrong, and reading the code
is what shows it.** `IDENTITY_SCOPES` is not a `kind -> scope` map; it is
`kind -> Callable[[IdentityEvent], list[Identifier]]`, and **each `Identifier` carries its own
`scope`**. Two kinds already exercise this:

- `_identifiers_dhcp` returns **two** identifiers from one row (`NS_MAC` + `NS_HOSTNAME`), which
  is the whole basis of `_link`;
- `kerberos_identifier` returns `HOST` for a machine account and `USER` for a user principal —
  a per-**value** scope decision, already finer-grained than per-field.

So `radius` needs one extractor (`_identifiers_radius`) and one table row. **No new machinery, no
table redesign, and it is not the novel build step that work order's step 1 says it is.** Reorder
accordingly: it is now the cheapest of the eight stems, not the riskiest.

### R2 — the real gap is the SUBJECT of an identifier, not its scope

Every failure this round shares one shape, and it is not a scope error:

| evidence | scope | who it actually names |
|---|---|---|
| axquery `conn.orig_mac` at high fanout | `host`, correctly | the **next-hop router**, not the endpoint |
| `radius` `mac` + `framed_addr` | `host`, correctly | a host that is **neither endpoint** of the RADIUS flow |
| `dns` answer (already handled) | `referenced` | a third party |

The resolver has always asked *"what can this identifier anchor?"* and never *"whose address does
it belong to?"* — DNS is handled only because `REFERENCED` happens to answer both at once. **The
subject is now a declared, per-kind decision**, on the same fail-closed discipline as scope: a kind
with no declared subject rule anchors nothing and is counted, never defaulted to "the flow's
address".

For `radius` this is load-bearing and easy to get catastrophically wrong. A RADIUS exchange runs
between a NAS and the RADIUS server; **the host being identified is not on either end.** The
subject is `framed_addr` — the assigned address the row itself carries. A parser that binds `mac`
to the packet's source or destination merges every authenticated host in the building onto the
RADIUS server. Same class of error as the router MAC, from the opposite direction.

### R3 — a bare MAC observation is evidence; the merge decision moves to snapshot time

Fanout is the right discriminator and **a per-window fanout threshold is the wrong way to apply
it**. Merges are forward-only and never rewritten (`entity_resolution` D2), so a MAC that shows
fanout 1 in window 3 and fanout 40 in window 4 has already caused a permanent false merge by the
time the evidence arrives. Low fanout in one window is *absence of evidence*, not evidence of
adjacency — `LES-006` in the merge path, where it is unrecoverable.

The architecture already has the right hook, and no new one is needed: **`same_entity_as`
assertions are emitted at window close and APPLIED by `BeliefStore.snapshot(as_of)`**. So:

1. **Window close emits the observation**, never the conclusion: `(address, mac)` co-observation,
   scope `host`, subject per R2. Cheap, streaming, order-independent.
2. **`snapshot(as_of)` decides the merge** with the whole bounded span's fanout in hand — a pure
   function of (store contents, `as_of`), so determinism is preserved and two snapshots over the
   same bound agree.
3. **The gate is positive, not a threshold.** A MAC may anchor only if **every** address observed
   with it within the bound is local (`ir_entity.locality`), **and** the count is 1. **Any** public
   address behind a MAC marks it next-hop permanently — a public host is never L2-adjacent to a
   corporate tap, so that single observation is decisive. Measured: this rejects both 46-IP and
   43-IP MACs on the first public address, and admits the two-private-IP MAC only if its second
   address is a genuine re-lease of the same host.
4. **A rejected MAC is not discarded** — it is emitted as `next_hop` topology evidence. Hosts
   sharing a next-hop MAC share an L2 segment. That is `observed` segment membership, strictly
   better than `segments.py`'s RFC1918 fallback at `imputed` 0.6, and it is the one clear win
   available from a MAC the resolver must refuse to bind on.

**This is not the evidence-plane reversal.** `docs/WORK_ORDER_evidence_plane_snapshots.md` remains
unruled and untouched. R3 uses the merge-application hook that `entity_resolution` D2 **already
ratified** — it changes what `snapshot()` consults, not where the snapshot boundary sits. It does
happen to point the same direction ("compose before you resolve"), which is worth noting when that
work order is ruled, and is not authority to pre-empt it.

### What this rules out, deliberately

Binding a bare conn MAC at window close, even at fanout 1. It is the only option R3 forecloses that
someone might want, and the reason is the asymmetry the project already accepted: a missed merge
leaves two honest address-keyed entities, a false merge fabricates a host that never existed.

## Build result (2026-08-20)

**Landed and verified end to end.** `aximprint ingest --plugin axquery --source
axquery:http://127.0.0.1:19090 --from 1780872000 --to 1780875000 --network corp` produced a
belief store (2 windows, 50,796 flows, 4,606 assertions), and `materialize` turned it into a PIT:
**1,112 entities, 38 segments, 1,558 edges**, vantage `axellio-hq/corp-apcn-rdr1:12`. 29 tests;
tier 1 **1,566**; tier 2 green. The acceptance criterion is met.

### What the transport measurement forced

**No endpoint on this server pages.** `/api/connections`, `/api/entities`, `/api/devices` and
`/api/identity/users` all ignore `offset` and always return `nextOffset: null`. D5's proposed
cursor paging does not exist. Two consequences:

1. **`/api/connections` is read by TIME-BISECTION.** A full page is evidence of truncation, so
   the interval halves; peak server memory stays at one page however dense the traffic, which is
   the opposite of the `limit`-walking that OOM-killed this server. An interval that cannot split
   further and is still full is **raised, not truncated**. The gain is not marginal: one 50-minute
   window returns **500** rows to a naive request and **50,796** to a bisected one.
2. **`after`/`before` select conns that OVERLAP the range, not ones that START in it** — right for
   a connection database, fatal to naive bisection. Before the fix, one window yielded 202,961
   rows carrying 50,887 identities (4x duplication) with timestamps reaching **14.9 days** outside
   the slice. Each leaf now keeps only rows whose own start falls in that leaf, so leaves
   partition exactly, every conn is emitted once, and **no dedup set is needed** — the read stays
   streaming and O(1) in memory, which a global identity set over a 26-day span would not.

### Steps 3-5 are declined on measurement

`/api/entities`, `/api/devices` and `/api/identity/users` neither page nor accept a time bound, so
bisection cannot rescue them and the reachable prefix is **ordered by ascending address** — a
systematically biased sample presented as a census. `/api/entities` holds 1.42 M rows.
`/api/identity/users` is worse than truncated: 500 rows carry **41** distinct `samAccountName`s
and `totalUsers` grows with `limit` (22 -> 67 -> **505**), so it is un-consolidated *and* its
count is a discovered-so-far figure like `totalMatches`. It also carries no address, so under R2's
subject discipline it anchors nothing. All three are declined **by name with the measurement as
the reason**, and reported through `unparsed_stems`. Named path back: axquery gains a cursor, or
these facts are derived from the flows this plugin already reads completely.

### D3/D4/D6/D7, ruled and built

- **D3** — `FlowEvent` gains `vlan_src`/`vlan_dst`, **two fields**, because axquery carries
  per-direction tags and says why (*"asymmetric VLAN tagging happens"*); its REST `vlanId` is the
  lossy orig-side projection. `0` means *not observed tagged* and becomes `None`, never VLAN 0.
  **35,778 of 50,796 flows carry a tag.** **The proposed `SEGMENT_EVIDENCE` row is REFUSED on
  measurement** — the second half of D3 was wrong, and measuring it is what showed that. A tag is
  neither a locality signal (**210 public endpoints carry one**, because a tag is the frame's tag
  at the tap, i.e. the next hop's VLAN for routed traffic) nor a refinement of the address-derived
  segment (**7 of 27 private /24s span multiple VLANs**; `10.80.20.0/24` spans four). It
  CROSS-CUTS the /24 rather than subdividing it. Same shape as D8's MAC finding: an L2 attribute
  observed at a tap describes the PATH, not the endpoint. The fields stay collected, and a later
  analysis treating a tag as a fact about the TAP — which VLANs this vantage sees — is
  well-founded; treating it as a fact about a host is not. A test pins the refusal.
- **D4** — accepted: axquery declares no locality, so `local_src`/`local_dst` stay `None` and every
  flow from this sensor falls to the RFC1918 fallback at `imputed` 0.6. Visible in the result
  above: all 38 segments came from that path.
- **D6** — locator `axquery:<base-url>`, a bare host **refused** rather than defaulted.
  `probe_vantage()` reads `/api/info` before any event — the first plugin able to answer without
  a harvest manifest. **The vantage strings still disagree** (`axellio-hq/corp-apcn-rdr1:12` vs
  the Zeek side's `https://10.80.65.7:0-11`), so PITs from the two sensors remain incommensurable
  until an operator declares them equal; `compare` gates on vantage-set equality.
- **D7** — one `_epoch` normalizer over all three representations, failing loud on a fourth.

### D2, built from the measurement rather than the draft

`state_class` derives from `origTcpFlags`/`respTcpFlags`, decoding **all eight** bits (ECN pairs
like `219/91` are real). On the live window: established 5,431, reset_orig 4,945, reset_resp
1,907, attempted 2,037, other 318, partial 31 — and `unknown` 36,127, every one of them UDP or
ICMP, which carry no flags and are counted rather than bucketed.

## Reproduce## Reproduce

```bash
curl -s http://127.0.0.1:19090/api/info
curl -s 'http://127.0.0.1:19090/api/connections?limit=2&after=1780872000&before=1780875000'
curl -s 'http://127.0.0.1:19090/api/entities?limit=5000' | \
  python3 -c "import sys,json;e=json.load(sys.stdin)['entities'];print(sum(1 for x in e if x['mac']!='00:00:00:00:00:00'),'of',len(e),'have a MAC')"
```

Server: PID 608589, `axquery 19090 --db-dir /net/devsrv/home/data/v29_test --axi-dir
/home/rocky/axq-index --auth-disabled`. The `.axi` shards are its internal index and are **out of
scope** — ingest goes through the API (user-ruled 2026-08-19).

---

## Extension 2026-09-08 — UDP listeners + address-bearing identity (Supersedes the `/api/events` + census declines, in part)

**Authority: user** — the user directed mining axquery's richer data to run the twin-discovery
loop against the CorpAPCN capture (using axquery instead of Zeek). See `docs/CHANGELOG.md`
2026-09-08 for the full entry. This section records what the extension changed against the rulings
above.

**Supersedes "Steps 3-5 are declined on measurement", for the identity half only.** Build step 5
(`/api/events` app-layer) is now BUILT for identity: `/api/events?type=dhcp` and `?type=dns` are
read (bisected on time exactly like `/api/connections`, k-way `heapq.merge`d with the flow stream
by ts so identity events window correctly). Only the app-layer identity half is taken — never a
second flow row — so the original decline's double-count reason still holds for the flow half,
which stays `/api/connections`. `/api/events?type=kerberos|ntlm` are declined **on measurement**
(0 rows in this build's windows); the AD identity here surfaces via the census, not via connection
events.

**The three census endpoints stay declined.** `/api/entities` and `/api/devices` are unchanged
(no cursor, biased prefix; roles now fall out of the UDP-enabled listen census anyway).
`/api/identity/users` stays declined with a fuller reason: beyond truncation, it carries **no
address**, so under R2's subject discipline it anchors nothing in the resolver, and there is **no
IR carrier for an address-less directory dump** — the realm/account overlay is
`docs/WORK_ORDER_reference_ingest.md` **S5** (`ir_policy(scope='domain')`), whose own known
transform is realm-case normalization.

**Also fixed in the same pass (cast side, surfaced by the richer twin, `synth/compiler.py`):**
`_node_suffix` now strips WHOLE trailing zero octets (was `rstrip(".0")`, which collapsed
`10.20.200.0` → `10-20-2` and collided under axRange's `count:` fan-out); and a domain-promoting
node group is clamped to **count 1** (one AD forest root per realm — axRange emits one
`domain-ready[<fqdn>]` barrier per `role: adds` host, so a `count: 2` DC refused the plan). The
observed DC count is disclosed in the `population` sidecar (`capped_by: single_forest_root`) + a
caveat.

**D2 — UDP.** The `_state_class` UDP branch: a responder that returned traffic (`respBytes` or
`respPackets` > 0) is `established` (a live listener, `FLOW_ACCEPTED`); only-originator is
`attempted`; ICMP stays `unknown` (its `dst_port` is the message type). This is a computation over
axquery's own direction-exact orig/resp, so it is D1-`observed`, like the TCP flag decode.

### Reproduce (rich discovered twin)

```bash
# aximprint (this worktree — wip/golden-twin, for the axr-topology/1.5 bundle):
scr=/tmp/disco    # any scratch dir
aximprint ingest --plugin axquery --source axquery:http://127.0.0.1:19090 \
  --store $scr/imprint.duckdb --network corp-apcn --from 1780398299 --to 1780405499 --window 900
aximprint materialize --store $scr/imprint.duckdb --out $scr/ir.store
aximprint cast --store $scr/ir.store --pit <PIT_ID> --out $scr/cast --relocate 10.200.0.0/16
#   NOTE: cast no longer takes --min-occupancy (removed; the deploy gate moved to axRange).
#   The <PIT_ID> is printed by materialize (pit-395a61f04e-1780405200 on this window).

# axRange (validate/plan only):
cd /home/rocky/axRange
.venv/bin/axrange te validate --config <PIT_ID> --twin-configs-dir $scr/cast --images deploy-dev45
.venv/bin/axrange te plan     --config <PIT_ID> --twin-configs-dir $scr/cast --images deploy-dev45 \
  --param DEPLOY_MIN_OCCUPANCY=0.5   # 0.5 replaces the old cast-time --min-occupancy 0.5
```
