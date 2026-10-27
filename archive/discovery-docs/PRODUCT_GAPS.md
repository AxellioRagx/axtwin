# Product Gaps — living register

A running list of **gaps in the implemented product discovered during real use** — things that are missing, brittle, or under-finished and would block or surprise a real user/operator. Entries are added *as they are hit*, so nothing is lost track of.

**How this differs from neighbouring docs:**

- `docs/OPEN_QUESTIONS.md` — *design* topics not yet locked into the specs (forward-looking).
- `docs/CHANGELOG.md` — what *was decided/built* (backward-looking, authoritative).
- `docs/LESSONS.md` — *rules* extracted from experience, auto-injected into the agents that need them.
- **This file** — what's *built but incomplete/brittle* (the gap between "works in our verified path" and "robust for an arbitrary user/environment").

**Entry format:** one `## GAP-NNN` section. Keep `Status` current; when closed, leave the entry with `Status: ✅ Resolved` plus the CHANGELOG entry / commit that closed it — **don't delete it, the trail is the point.** Severity: **High** (blocks a core flow), **Medium** (painful workaround), **Low** (polish).

A gap that yields a durable rule should also get a `LES-NNN` entry in `docs/LESSONS.md` with `Origin: GAP-NNN`.

---

<!-- BEGIN GAP ENTRIES — newest first; never renumber -->

## GAP-019 — `serves_ssl` / `serves_http` / `serves_smb` never cross the IR boundary, so a cast cannot configure a server from an observed name

**Status:** 🔴 Open
**Severity:** Medium
**Discovered:** 2026-09-05 — the protocol-faithful services build (CHANGELOG same date; axRange
`docs/WORK_ORDER_protocol_faithful_services.md` §6.1's `tls.sni` / `http.vhosts` / `smb.shares`).

**Symptom:** the twin's nginx serves a **default vhost named after its own node**, because the
cast has no observed vhost or SNI to give it — while the belief store has held both all along.
`windowing/windows.py::SERVICE_PROJECTIONS` projects `ssl -> server_name` (the TLS SNI),
`http -> host` (the vhost) and `smb -> share_type/service`, and measured read-only on the
CorpAPCN store those are real content: **8 distinct `serves_ssl` names**, and `serves_http` binds
`test-api.swlab.axellio.dom` to the twin's own `10.80.75.171`. `ir/materialize.py` reads only the
`listens_on`, `dials_port`, `in_segment` and `has_role` predicates, so `serves_*` is invisible to
every PIT consumer — the twin compiler, the topology bundle, the drift comparator.

**Why it matters more than a nicer default page:** a detection rule matches a protocol's own
names (a DNS query name, an HTTP `Host`, a TLS SNI), never a port number. The whole point of
`docs/design/13-golden-twin-fidelity.md` §8.2's ruling is that names come from observation; this
is the one place the names exist and cannot be reached.

**Same shape, second instance:** `services[].windows_seen` (axRange's §6.1 asks for a **per-listen
occupancy** and `ir_entity.listen_ports` is a bare `["<port>/<proto>"]` array — the entity's own
`windows_seen` is a host's occupancy, not a port's, so publishing it under that name would be
`LES-127`'s failure with a plausible number attached). One carrier would close both.

**Why it is not fixed here:** the carrier is an additive **IR MINOR** — a version-locked change on
the one schema this project has ratified (CONVENTIONS §1.2, DESIGN_BRIEF §11) — and both prior IR
bumps went through an explicit user decision. No speculative `ir_service` shape was written ahead
of that decision, on `LES-101`'s reasoning. The bundle omits the three keys rather than emitting
empty or invented ones.

**Options when it is taken:** (a) a new `ir_service` table (`pit_id, entity_key, kind, attribute,
value, evidence_class`), which also gives per-listen occupancy somewhere to live; (b) extend
`ir_identity` with `ssl`/`http`/`smb` kinds, cheaper but conflates *"a name this host was called"*
with *"a name this host served"*; (c) leave it, and accept that every twin vhost is a compiler
default. Reference-ingest **S5** already schedules the neighbouring `realm`/`spns` half.

---

## GAP-018 — a Windows cast node gets no service recipe at all

**Status:** 🔴 Open
**Severity:** Medium
**Discovered:** 2026-09-05 — the protocol-faithful services build (CHANGELOG same date).

**Symptom:** `cli._write_serve_scripts` skips every Windows-imaged node and says so in `caveats`.
On the CorpAPCN cast that is 4 of the 18 node groups carrying a declared listen set; the
`domain-controller` is served by its own AD promotion (`install: [{role: adds}]` brings DNS, the
KDC, LDAP, SMB, the GC and W32Time), but **three `corp-workstation` groups are genuinely
unserved** on 135/tcp, 137/udp, 3389/tcp+udp and 7680/tcp.

**Why:** the recipes are POSIX shell delivered through axRange's `run-script` (OP-APP-030), which
on a Windows guest runs the file as PowerShell — measured on dev45: `cat > ... <<'EOF'` fails with
*"Missing file specification after redirection operator"*. This is the same wall
`docs/WORK_ORDER_windows_responder.md` §§1-2 measured for the echo, and its cost is measured
there too: a silent host is not merely unmeasured, it is measured as something else — each dead
port draws an ICMP unreachable that Zeek scores as a fabricated reverse local edge.

**What would close it:** a PowerShell branch in `synth/services.py`'s renderer. Two of the three
groups' ports are Windows built-ins that a **domain join** already turns on (3389 needs one
`run-script` to enable RDP; 135/137 answer as domain-member RPC/NBNS), so joining the members —
which this build now does — narrows the gap without closing it. Named rather than silent, so a
scorecard reports these ports in the state they are in.

---

## GAP-017 — `timespec` raises a bare `TypeError` for a wrong-typed `now`

**Status:** 🔴 Open
**Severity:** Low
**Discovered:** 2026-08-06 — H-1 build (`docs/WORK_ORDER_timespec.md`).

**Symptom:** Passing a non-`Instant` `now` (e.g. a raw `float`/`int`) to `resolve_start`/`resolve_end`
raises a bare `TypeError` from `_require_instant`, not a `TimeSpecError`/`XtractError`-rooted
exception — inconsistent with the module's own typed-error convention (every *documented* rejection in
the grammar raises a typed error, per `LES-041`'s single-root rule) and outside `cli.py`'s top-level
`except XtractError` handler, so it would surface as an unhandled traceback instead of a clean exit
code.

**Root cause:** Phase 3's purity work range-checked the *value* of an arithmetic `Instant` (`LES-057`)
but didn't add an explicit type guard raising a typed error at the two entry points; `_require_instant`
predates that hardening and was never revisited once the rest of the module's errors were reparented.

**What would close it:** Replace the bare `raise TypeError(...)` in `_require_instant` with a
`TimeSpecError` (or a dedicated subclass) naming the received type, and add a regression test.

## GAP-016 — `docs/PLAN_metadata_harvester.md` §5's `:8.0` Zeek pin is a moving tag

**Status:** 🔴 Open
**Severity:** Low
**Discovered:** 2026-08-06 — H-2 build (`xtract/harvest/zeek.py` `DEFAULT_ZEEK_IMAGE`).

**Symptom:** `docker.io/zeek/zeek:8.0` will retag to a later patch (e.g. 8.0.10) whenever the registry
updates it. `_validate_pinned_image` refuses a bare `:latest`/tagless image, but nothing refuses a
minor-version-moving tag like `:8.0` — a future `podman pull` of the same reference can silently start
resolving to a different Zeek patch version than the one `_SCHEMAS` was derived against.

**Root cause:** A deliberate choice for now, not an oversight — pinning by digest trades
reproducibility for a pin that goes stale relative to Zeek's own security patches, with no automatic
way to notice. Drift is detected downstream, after the fact, via `ZeekResult.version` (recorded per
run, comparable across runs), not prevented at construction.

**What would close it:** Switch `DEFAULT_ZEEK_IMAGE` to a digest pin once the image is expected to stop
moving for a while, or add a startup check (in `probe()`) comparing the observed `zeek --version`
against an expected value and failing loud on mismatch instead of only recording it.

## GAP-015 — No cross-process lock on the spool root

**Status:** 🔴 Open
**Severity:** Medium (silent double-counting / possible mid-write deletion, not just a wasted run)
**Discovered:** 2026-08-07 — H-4 build (`xtract/harvest/pipeline.py`, `xtract/harvest/spool.py`).

**Symptom:** Two `harvest` processes pointed at the same `--out`/spool root would each reconcile the
same on-disk bytes into their own independent in-memory accounting at startup, each admit against the
same cap believing the full cap is available to them alone, and each run a reaper that can delete a
pcap the *other* process still considers `RESIDENT` and un-Zeeked — silent double-counting, and a
plausible mid-write deletion by the "other" process's reaper.

**Root cause:** `Spool` has no cross-process lock (e.g. `flock`) on `root`; single-process ownership is
assumed and has held so far only by operational convention, not by anything enforced in code.

**What would close it:** An advisory lock file (e.g. `root/.lock` via `fcntl.flock(LOCK_EX |
LOCK_NB)`) acquired at `Spool` construction and released at close, failing loud with a clear message if
already held.

## GAP-014 — `FakeZeekRunner`'s `dhcp`/`kerberos`/`dce_rpc`/`ldap_search`/`smb_mapping`/`smb_files` schemas are still transcribed, not observed from the pinned image

**Status:** 🔴 Open
**Severity:** Low
**Discovered:** 2026-08-06 — H-2 live verification (`xtract/harvest/zeek.py` `_SCHEMAS` comment).

**Symptom:** 6 of the 19 schemas `FakeZeekRunner` ships — `dhcp`, `kerberos`, `dce_rpc`, `ldap_search`,
`smb_mapping`, `smb_files` — are still transcribed from `aximprint`'s reference capture (produced by a
different Zeek version, of unconfirmed provenance) rather than observed from the pinned 8.0.9 image:
the 2026-08-06 verification pcap carried none of that traffic. Every other stem `FakeZeekRunner` and
`_SCHEMAS` carry was corrected against the pinned image's real output that same day (see CHANGELOG
2026-08-06 "Zeek pinned to 8.0.9 with `local` loaded").

**Root cause:** Not a defect — no harvested window has yet carried DHCP, SMB, Kerberos, or LDAP traffic
to cross-check against. `dhcp` is additionally the sole schema with **no** cross-check at all, from
either capture (see the comment at its `_SCHEMAS` entry).

**What would close it:** The first live harvest window that happens to capture one of these protocols;
re-derive that stem's schema against the pinned image's real output and drop it from this gap's list.

## GAP-013 — `check_drift()` runs only at end-of-run, so a multi-day unattended run self-checks its disk accounting exactly once

**Status:** 🔴 Open
**Severity:** Medium (a real, structural accounting drift mid-run — spool root touched externally, a
regression reintroducing an accounting bug — would go undetected for the rest of a multi-day run)
**Discovered:** 2026-08-07 — H-4 build (`xtract/harvest/pipeline.py` `check_drift`).

**Symptom:** `check_drift()` compares `Spool.verify()`'s accounted-vs-on-disk totals exactly once, at
the very end of `run_harvest`, not on any interval during the run itself.

**Root cause:** Deliberate, not an oversight: `Spool.verify()`'s own docstring documents that the two
totals are *expected* to disagree transiently while any entry is `RESERVED` (an estimate with no file
landed yet, or a file still mid-write) — which is true for essentially the entire duration of a
concurrent run. Checking on a fixed interval mid-run would produce constant false-positive drift
errors: by the time every extract and Zeek worker has finished, no `RESERVED` entries remain, so a
disagreement only *then* is a genuine, structural drift. The end-of-run check is correct given that
signal; the gap is that nothing currently distinguishes "drift" from "still concurrent" earlier than
the very end.

**What would close it:** A periodic check gated on a moment of quiescence (e.g. when the `RESERVED`
count transiently hits zero between windows) rather than a fixed interval, or a per-window post-hoc
check keyed to the specific window instead of the whole spool total.

## GAP-012 — `xtract pcap` cannot detect an expired/revoked session token

**Status:** 🟡 OPEN — appliance-side behavior, no client-side fix available.

`GET /axapi/pcap` accepts a session token that `POST /axapi/logout` has revoked (measured
2026-08-05; see `docs/VENDOR_API_DEFECTS.md` §S1). It returns `200` and real capture bytes where
every other probed endpoint returns `401`.

**Consequence for `xtract`:** the 401 → refresh → re-login recovery in `client.py` is correct and
*works* — verified live against `GET /axapi/fs/streams`, where a revoked token produced a `401`,
an automatic re-auth, a new token, and a usable response. But it will **never fire during a
`xtract pcap` run**, because that endpoint does not reject a dead token. A long unattended extract
therefore neither needs nor exercises the recovery path.

**Not a client bug, and not fixable here.** Pre-emptively re-authenticating on a timer would be
guessing at a lifetime the appliance never discloses (the token is opaque — `docs/VENDOR_API_DEFECTS.md`
§D4), and would paper over an appliance-side authorization gap rather than address it.

**Resolved 2026-08-05:** natural expiry behaves identically to revocation. Tokens expire at exactly
15 minutes (sliding), every other endpoint returns `401`, and `/axapi/pcap` still serves packet data.
So the recovery path can never fire during a `pcap` run for either reason. Measured via a cohort of
tokens each tested once — polling a single token had falsely suggested a >110-minute lifetime,
because each probe refreshed the sliding window.


## GAP-011 — Several `axapi` endpoints and behaviors remain untested against a live appliance

**Status:** 🔴 Open
**Severity:** Low (nothing in this list is exercised by `xtract`'s core two commands today; each is
either an adjacent/unused endpoint or a specific behavior the 2026-08-05 probe didn't reach)
**Discovered:** 2026-08-05 — end of the live-probe pass; carried forward from
`docs/VENDOR_API_DEFECTS.md`'s "Not tested" list rather than left implicit only there.

**Symptom:** The 2026-08-05 live probe closed Q1–Q4 and found six vendor-doc defects, but a specific,
named list of endpoints/behaviors this project's contract doc (`docs/AXAPI_CONTRACT.md`) transcribes
were not exercised against the real appliance and so carry only the same confidence as before the
probe (i.e., "the PDFs say this, unconfirmed"):

- ~~`POST /axapi/refresh` and `POST /axapi/logout` — status codes and response bodies~~ — **now
  exercised** (see `docs/VENDOR_API_DEFECTS.md` §S1 and its "Not tested" section): `POST /axapi/logout`
  was probed directly (it revokes access to every endpoint except `/axapi/pcap` — see `GAP-012`);
  `POST /axapi/refresh` was exercised indirectly via the 401 → refresh → re-login recovery path
  against a revoked token, which returned a new working token and a usable response. `GAP-010` (the
  missing `Content-Type` on `_refresh()`) stays open regardless — the recovery path succeeding doesn't
  confirm whether the appliance enforces the header, only that it isn't rejecting this particular
  bodyless request as sent today.
- `GET /axapi/task/*` (list/by-guid/latest, `DELETE /axapi/task/<guid>`) — task-tracking and
  SIGINT-abort path (`extract.py` `_abort_task`).
- `GET/POST /axapi/extract` (the async transport) — deferred by design (`GAP-007`), and now also
  unverified even as documentation.
- `GET /axapi/minute|hour|day/<start_time>` — the datapoint endpoints recorded as a possible Q3
  cross-check; never called.
- `GET /axapi/system/*` beyond `information` (license, utilization — the latter vendor-marked "NOT
  YET IMPLEMENTED" anyway).
- `GET/DELETE /axapi/apikeys*` — moot on CorpAPCN specifically (see `GAP-001`'s reclassification,
  below), but still unverified as *documentation* for a deployment that does have API keys.
- `archive=`/`servers=` query parameters on `/axapi/pcap` — deferred by design (`GAP-006`), and their
  documented behavior is unconfirmed.
- **418 (no-data) behavior** — `xtract` implements handling for it (`LES-035`) but the probe never
  actually triggered a 418 live; it's implemented against the documented meaning, not an observed one.
- The single-stream form `GET /axapi/fs/streams?stream=<s>` (§5.2) — given that its sibling list-all
  endpoint (§5.1) turned out not to match its documented wrapper shape, this one's documented
  `{"stream": {...}}` object wrapper is now actively suspect, not just unconfirmed.

**Root cause:** Not a code defect — the live probe was scoped (by its own work order, deliberately)
to the smallest set of read-only requests that answers Q1–Q4, not a full API surface audit.

**What would close it:** A follow-on, still-read-only probe pass exercising each item above and
recording the result in `docs/AXAPI_CONTRACT.md`/`docs/VENDOR_API_DEFECTS.md`, same discipline as
2026-08-05. Worth doing opportunistically (e.g. the next time SIGINT-abort or 418 handling actually
needs to be trusted in a real run) rather than as a dedicated work order up front.

## GAP-010 — `_refresh()` sends no `Content-Type` header, though the contract documents one

**Status:** 🔴 Open
**Severity:** Low (only matters if the appliance is strict about the header on a bodyless POST)
**Discovered:** 2026-08-04 — code review during the WO-0…WO-7 pipeline run.

**Symptom:** A real appliance that validates `Content-Type` on every POST (rather than only on
requests carrying a body) could reject `POST /axapi/refresh` with a 4xx that `xtract` would surface
as an unexpected transport/server error, not the clean 401-recovery-then-relogin path it's designed
to take.

**Root cause:** `docs/AXAPI_CONTRACT.md` §1.2 documents `Content-Type: application/json` as a
required header for `POST /axapi/refresh`, but `client._refresh()` calls `_do_request(..., json=None,
...)` — `requests` only sets `Content-Type` automatically when `json=` is a real object, so a `None`
body means no header is sent at all. **Still unverified as of the 2026-08-05 live probe** — that
probe closed Q1–Q4 and found six other defects, but `POST /axapi/refresh` itself was not exercised
live (see `GAP-011`), so this remains filed as a gap rather than "fixed" against a guess.

**What would close it:** A live call to `POST /axapi/refresh` (part of `GAP-011`'s follow-on probe
scope) to test whether the appliance actually enforces the header on this endpoint; if so, send it
explicitly even with an empty/`None` body.

## GAP-009 — No job-file / batch-config format

**Status:** 🔴 Open
**Severity:** Low
**Discovered:** 2026-08-04 — scoped out of iteration 1 in the original work orders; noted here as
the register entry rather than only living in `CLAUDE.md` "What NOT to Do" by omission.

**Symptom:** Every `xtract pcap` invocation takes its stream/window/filter parameters on the command
line; there's no file an operator can save and re-run, version-control, or hand to another operator
for a recurring extraction job.

**Root cause:** Not built — deliberately deferred, not discovered as broken.

**What would close it:** A `--job-file` flag reading the same parameters `argparse` accepts today,
probably TOML (consistent with the credential config) or JSON.

## GAP-008 — No parallelism; `xtract pcap` extracts windows strictly serially

**Status:** 🔴 Open
**Severity:** Low (correctness is unaffected; only throughput)
**Discovered:** 2026-08-04 — `runner.execute` walks `windows`/`decisions` in a single `for` loop.

**Symptom:** A long `--start`/`--end` range with a short `--interval` extracts one window at a time;
wall-clock time for a big batch is the sum of every window's extract time plus the between-window
overhead, with no option to run several windows concurrently.

**Root cause:** `runner.execute` (`xtract/runner.py`) has no concurrency; a design choice for
iteration 1, not an oversight — sequential execution keeps the resume/manifest invariants (append
one record per window, in order, before moving on) trivially correct.

**What would close it:** Bounded concurrency (a small thread/process pool) plus a manifest-append
strategy that's still safe under concurrent writers (the current `fsync`-per-append is only proven
safe for one writer). Worth doing only once real batch sizes make serial extraction the actual
bottleneck — not before.

## GAP-007 — The async `POST /axapi/extract` transport is not implemented

**Status:** 🔴 Open
**Severity:** Low (deferred by design, not broken)
**Discovered:** 2026-08-04 — CHANGELOG 2026-08-04 "`xtract` design ratified" named this deferred.

**Symptom:** `xtract pcap` only uses the synchronous `GET /axapi/pcap` transport. If a window's
extract runs long enough to hit a client or proxy timeout on the synchronous path, there's no
fallback — the async `POST /axapi/extract` → poll `task/<guid>` → `GET /output` path documented in
`docs/AXAPI_CONTRACT.md` is unused.

**Root cause:** Not built — `client.py`'s request layer is written so the swap is "an envelope
change" (per the ratifying CHANGELOG entry) but the envelope was never written.

**What would close it:** Implement the async path behind the same `extract_window` interface once a
real long-window timeout is actually hit — premature before then.

## GAP-006 — `archive=`/`servers=` multi-extract and cluster targeting not implemented

**Status:** 🔴 Open
**Severity:** Low (deferred by design)
**Discovered:** 2026-08-04 — deferred explicitly in the design ratification; filed here so the
deferral has a register entry, not just a line in `CLAUDE.md` "What NOT to Do".

**Symptom:** `xtract` cannot target a specific archive tier or a specific server in a CorpAPCN
cluster — every request goes to whatever `--host` resolves to, with no `archive=`/`servers=` query
parameters ever sent.

**Root cause:** Not built — out of scope for iteration 1 (`CLAUDE.md` "Current State" — "Explicitly
deferred").

**What would close it:** `--archive`/`--servers` flags threaded through to `client.py`'s request
params once a real multi-server or tiered-archive CorpAPCN deployment needs them — unknown until the
"Starting Work" question about whether CorpAPCN is one system or a cluster is answered.

## GAP-005 — `Client.request()` is a general escape hatch; the read-only guarantee is conventional, not structural

**Status:** 🔴 Open
**Severity:** Medium (a safety property the project states as load-bearing — `CLAUDE.md` "What NOT
to Do" — is not actually enforced in code)
**Discovered:** 2026-08-04 — code review during the WO-0…WO-7 pipeline run.

**Symptom:** Nothing prevents a future change (or a bug) from calling `client.request("POST",
"/axapi/clear", ...)` or any other mutating endpoint — `Client.request()` (`xtract/client.py`)
accepts an arbitrary method and path with no allow-list or deny-list. The project's stated safety
model ("code that doesn't exist cannot be called by mistake" — `CLAUDE.md` "What NOT to Do") holds
today only because no caller happens to invoke it that way, not because the client refuses to.

**Root cause:** `client.request()`/`client._do_request()` (`xtract/client.py`) take a free-form
`method`/`path` with no validation against the documented read-only endpoint set in
`docs/AXAPI_CONTRACT.md`.

**What would close it:** An assert or allow-list in `_do_request()` (or `request()`) checking
`(method, path)` against the read-only endpoint table in `docs/AXAPI_CONTRACT.md` §0, raising a
programming-error exception (not a typed `axapi`-response error) on anything else — turns the
guarantee structural instead of conventional. Small change; not done because no caller currently
needs it and it wasn't in any work order's scope.

## GAP-004 — `manifest.jsonl` re-parsed and `Window.wire_params()` rebuilt repeatedly per invocation

**Status:** 🔴 Open
**Severity:** Low (correctness is unaffected; only wasted work)
**Discovered:** 2026-08-04 — code review during the WO-0…WO-7 pipeline run.

**Symptom:** No user-visible symptom yet at realistic manifest sizes; would show up as `xtract pcap`
startup latency scaling worse than expected on a `--out` directory with a very large `manifest.jsonl`
(a long-running batch with many windows).

**Root cause:** `cmd_pcap` (`xtract/cli.py`) calls, in sequence, `runner.check_collision`,
`runner.plan_resume`, `runner.ensure_manifest_header`, and `runner.execute` — each independently
calls `manifest.read_manifest(out_dir)`, so the full `manifest.jsonl` is parsed from scratch four
times per invocation instead of once and threaded through. Separately, `Window.wire_params()` is
called independently in `cli.py`, `extract.py`, and twice in `runner.py` (`plan_resume` and
`execute`) — cheap per call, but rebuilt rather than computed once per window and passed down.

**What would close it:** Parse the manifest once in `cmd_pcap` and pass the parsed `ManifestData`
into `check_collision`/`plan_resume`/`ensure_manifest_header`/`execute`; memoize or precompute
`wire_params()` once per `Window`. Both are refactors, not correctness fixes — no test currently
fails because of this.

## GAP-003 — ~150 lines of test fixtures duplicated across five test modules

**Status:** 🔴 Open
**Severity:** Low (maintenance burden, not a correctness risk — verified: `tests/` is hermetic and
green either way)
**Discovered:** 2026-08-04 — code review during the WO-0…WO-7 pipeline run; `tests/conftest.py` was
added late in the fix pass to start absorbing this but does not yet cover everything.

**Symptom:** `class FakeAdapter(requests.adapters.BaseAdapter)` is defined near-identically four
times (`tests/test_extract.py`, `tests/test_client.py`, `tests/test_cli_pcap.py`,
`tests/test_runner.py`) and `make_response(...)` twice (`tests/test_extract.py`,
`tests/test_client.py`) — roughly 150 lines that would be one shared definition in
`tests/conftest.py`. A change to the fake-adapter behavior (e.g. the `max_retries=0` gotcha in
`LES-046`) has to be made, and kept consistent, in every copy.

**Root cause:** `tests/conftest.py` was introduced during the fix pass but only absorbed part of
the shared setup; the four `FakeAdapter` definitions and two `make_response` definitions were not
migrated.

**What would close it:** Move `FakeAdapter`/`make_response` into `tests/conftest.py` as fixtures or
importable helpers and delete the four/two duplicate definitions. Mechanical, low-risk — good
first task for a future session.

## GAP-002 — No runtime notice that `xtract streams`/`xtract pcap` output rests on unverified wire-contract assumptions (Q1/Q3/Q4)

**Status:** ✅ **Resolved 2026-08-05** — CHANGELOG 2026-08-05 "Q1–Q4 resolved by live probe; six
vendor-contract corrections applied". The concern this gap named (an operator acting on numbers that
turn out to rest on a wrong wire-contract assumption) is moot now that Q1/Q3/Q4 are resolved by direct
measurement rather than assumption — `FS_FIELD_NS_PER_UNIT`, `stream_id_for_name`, and
`Q4_END_TIME_INCLUSIVE` all carry `# Qn: RESOLVED` comments, not open-question markers. No runtime
notice was built (the original "what would close it" proposal), and none is needed now — the
provisional state it would have warned about no longer exists. If a *future* question opens behind a
similar unverified-constant pattern, revisit whether a runtime notice is worth building at that point;
this entry is closed for the reason actually applicable to it, not because the general idea was
rejected.

**Original entry, kept for the trail (Discovered 2026-08-04 — noted at the end of the WO-0…WO-7
pipeline run, which never ran the live probe):** *"`xtract streams`' reported first/last timestamps
rest on `FS_FIELD_NS_PER_UNIT` (Q1, currently assumed = seconds) and `stream_id_for_name` (Q3,
currently assumes the `fs/streams` directory name is the decimal stream id) being correct; `xtract
pcap`'s window boundaries rest on `Q4_END_TIME_INCLUSIVE` (Q4, currently assumed exclusive). None of
the three is verified against a real appliance yet, and the CLI's output carries no indication of
that."*

## GAP-001 — `xtract auth` subcommand does not exist; `list_api_keys`/`revoke_api_key` are unreachable from the CLI — ⚠️ 2026-08-05: confirmed real, but untriggerable on CorpAPCN specifically

**Status:** 🔴 Open — reclassified, not closed
**Severity:** Medium in general (the emergency credential-revocation path the design ratified is built
but inaccessible to an operator without writing Python) — **Low-and-moot for CorpAPCN as probed**, see
below.
**Discovered:** 2026-08-04 — code review during the WO-0…WO-7 pipeline run. **Reclassified:**
2026-08-05, live probe.

**Symptom:** `client.list_api_keys()`/`client.revoke_api_key()` are implemented and covered by
tests, but there is no `xtract auth list-keys` / `xtract auth revoke-key` (or any `auth`) subcommand
in `xtract/cli.py`. `xtract --help` shows only `streams` and `pcap`.

**2026-08-05 finding — this gap is real but currently untriggerable on the deployment this project
targets:** the live probe found **CorpAPCN has no API keys at all** — authentication is
username/password → JWT only. There is nothing to list or revoke on this appliance, so the missing
`xtract auth` subcommand cannot surface as an operator-visible problem here, no matter how the CLI is
extended. This is a statement about *this deployment*, not about whether the gap is worth fixing in
general — a different CorpAPCN deployment, or a future one, may have API keys, and the gap remains
open and real for that case.

**Root cause:** CHANGELOG 2026-08-04 "`xtract` design ratified" explicitly puts "list-keys/revoke-key
... in scope" (and only `create-key` out of scope), and WO-2 implemented the client methods
accordingly — but no work order was ever written for the CLI subcommand that would expose them, so
nothing in the WO-0…WO-7 sequence built it.

**What would close it:** A `docs/WORK_ORDER_*.md` for `xtract auth list-keys [--include-revoked]`
and `xtract auth revoke-key <id>`, thin `cli.py` wrappers over the already-tested `client.py`
methods — the smallest version of this is close to a one-work-order, one-session task. **Not urgent
for the CorpAPCN deployment probed 2026-08-05** given the finding above; worth doing only once a
deployment with actual API keys needs it.
