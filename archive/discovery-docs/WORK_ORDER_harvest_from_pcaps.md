# WORK ORDER — `harvest --from-pcaps`: run the Zeek stage over pcaps already on disk

**STATUS:** LANDED 2026-08-11, **tiers 1–3 all done** — see CHANGELOG "`harvest --from-pcaps`: the
Zeek stage runs over pcaps already on disk — and a worker thread that could die silently while the
run still reported success" and its same-day tier-3 addendum. Tier 3 ran user-authorized directly
in the main session (the `live-verifier` subagent, offered the authorization only at one remove,
correctly declined to act on it) — **12/12 windows, exit 0**, stem set and structure matched the
recorded baseline exactly, all 12 source pcaps survived untouched. **Two numbers in that recorded
baseline were themselves found wrong in the process — see Verification, below, before citing "85 MB"
or "111×" from anywhere in this document's history; both are corrected there.**
**Depends-on:** nothing; `harvest` as it stands today
**Closes:** the gap that forced 2026-08-11's live verification to bypass the CLI entirely and drive
`ZeekRunner` from an inline Python script (CHANGELOG 2026-08-11)
**Authority:** the **shape is user-ratified 2026-08-11** — `--from-pcaps` on `harvest` rather than a
separate `acquire zeek` verb (the user's call, and the better one: a separate verb would have needed
harvest's worker pool, `RunReport`, retained-failure limits and drift checks, so it would either
duplicate that machinery or awkwardly borrow it). The **scope boundary is also user-ratified**: no
pcap slicing in this project.

**D1 and D4 are user-ratified 2026-08-11** — including D4's flag rejection set, ratified at **seven**
flags as drafted and then **extended to twelve later the same day, also user-ratified** (ruling B —
see "D4 in full," below, and CHANGELOG "`harvest --from-pcaps`..."). **D2 landed as proposed. D3
landed with its decision intact but its implementation revised during the build** — see "D3 as
landed" immediately after the decision table; that revision was within the "open to revision during
the build" latitude this document already granted D3, so it did not need separate ratification.
**Two further rulings were needed during the build and are also `user`:** ruling A (`--fail-fast` is
implemented, not accepted-but-inert) and ruling C (build step 2's stem-shape resolution, below).

## Why this exists

`harvest` is pcap→Zeek→cold-tier, and the only way to feed it is an appliance. On 2026-08-11 a live
run produced 12 pcaps (9.45 GB) via `acquire pcap`, and there was **no way to run harvest's Zeek
stage over them.** Pre-seeding `<out>/spool` and running `harvest --dry-run` still planned 12
windows to extract — harvest has no resume-from-existing-spool (`plan_resume` lives in `runner.py`
and serves `pcap` only; `H-7` is unbuilt). Running the verb would have re-pulled the same 9.45 GB
and created 12 more appliance task records for files already on disk.

The Zeek stage was therefore driven directly through the `ZeekRunner` protocol from an inline
script. It worked — 12/12 windows, ~79 MB of logs, a ~119.5× reduction (recorded that day as "85 MB"
and "111×" — a stray 13th directory in the comparison inflated both; see Verification for the
correction and how it was found) — but reaching it required knowing the protocol, the image-pinning
rule, the `--pull=never` rule, and the fresh-`out_dir` rule. **An obvious operation with no CLI, four
undocumented preconditions, and a silent failure mode if you get the last one wrong** (a stale
`out_dir` returns `ok=False` rather than raising).

## Scope boundary — ratified, read this before proposing to widen it

**No pcap slicing.** An earlier draft honored `--start`/`--end`/`--interval` with `--from-pcaps`, to
carve a section out of a large pcap. The user withdrew that on 2026-08-11: *"don't want to overly
complicate the harvest --from-pcaps path… let an external tool / script not related to this project
handle pcap cutting."*

Three things that decision avoids, recorded so nobody re-derives them:

- **A runtime dependency, which is gated.** `editcap` is the tool for cutting by absolute time
  (`-A`/`-B`, `-i`). It is **not installed on this host and not in the pinned Zeek image**;
  `tcpdump` is present but BPF has no time predicate, so it cannot slice an existing file by time.
- **A boundary shift in the design brief.** `extract.py` states outright: *"No inspection of the
  pcap bytes beyond a count. `xtract` does not parse [pcap]"* — the sole exception being a 4-byte
  magic check. Slicing means reading per-packet record headers, i.e. this package parsing pcap
  framing for the first time.
- **The alternative to a dependency was ~60 lines of hand-rolled pcap record parsing**, which is
  cheap to write and permanently ours to own.

If a use case needs a section of a large pcap, cut it outside this project and point
`--from-pcaps` at the result. That is the whole answer.

## Decisions

| # | Decision | Proposal | Rejected alternative |
|---|---|---|---|
| D1 | Where the flag lives | `--from-pcaps SRC` on `harvest`. **User-ratified.** | A separate `acquire zeek` verb. It would be ungatable-by-verb (a genuine advantage — it needs no credentials or network, unlike everything else under `acquire`), but it needs harvest's worker pool, reporting, failure limits and drift checks, so it duplicates or borrows them. Conceptual coherence wins: `--from-pcaps` swaps one stage of an existing pipeline. |
| D2 | Gating | `harvest` stays gated unconditionally, including with `--from-pcaps`. | A flag-sensitive permission rule (allow `harvest --from-pcaps …`, deny `harvest …`). That is exactly the pattern `LES-068` was written about — a gate keyed to an invocation string, satisfiable by argv a typo can produce. One prompt for a local-only run is the correct price, and the verb *has* the appliance capability regardless of which flags a given call passes. |
| D3 | Pcap ownership | The spool becomes **non-owning** for `--from-pcaps` entries: admitted for accounting and Zeek scheduling, but never deleted. Ownership is a **per-entry predicate**, not a global run flag — a resumed run has orphans it *does* own sitting beside pcaps it does not. **As landed, the predicate is unchanged but "admitted... into the spool" is not what shipped — see "D3 as landed," immediately below.** | A global "don't delete" switch. It cannot express the mixed case, and the mixed case is exactly `H-7`. |
| D4 | Flags with no receiver | **Rejected as mutually exclusive**, named individually in the error. **Landed at seven flags as drafted, extended to twelve later the same day (ruling B, user-ratified) — see "D4 in full."** | Silently ignoring them. `LES-021`: an option that reaches zero declared receivers is the caller's mistake, not a no-op the run should absorb. `--filter` is the sharpest case — silently accepting a BPF that can no longer be applied would **misdescribe the resulting archive**. |

### D3 as landed — deviates from this document's literal text

**Recorded here, not just in the CHANGELOG, so `H-7` (a future mixed run — orphans this pipeline
owns sitting beside externally-supplied pcaps it does not) finds the reasoning at the decision it
would otherwise re-derive.** The proposal above says the spool becomes "non-owning" for
`--from-pcaps` entries — "admitted for accounting and Zeek scheduling, but never deleted." What
shipped constructs **no `Spool` at all** for this mode; `run_harvest_from_pcaps` passes `spool=None`
through the whole shared worker path. Two independent reasons, either one sufficient on its own:

- A failed Zeek run on an "admitted but non-owning" entry would leave a spool entry RESIDENT —
  accounted bytes, a tracked `window_id` — with **no file** under `<out>/spool` (the file never
  moved there; `--from-pcaps` reads pcaps in place). `Spool.check_drift()`/`verify()` compare
  tracked entries against what is actually on disk, so this shape trips a false `SpoolDriftError` on
  any `--from-pcaps` run with even one Zeek failure — a diagnosis that would blame spool bookkeeping
  for a situation the spool was never in a position to get right.
- **Decisively:** `Spool.admit()` **blocks** for backpressure against `cap_bytes` — that is its
  entire job. Admitting real, supplied pcap bytes against `--spool-cap`'s default (10 GB; not
  raisable here, since D4 rejects `--spool-cap` as a flag under `--from-pcaps`) with **nothing ever
  calling `release()`** to free room for the next one (D3: never released, owned or not) means the
  run advances only until the cap fills, then blocks in `admit()` until `stall_timeout` (30 minutes)
  gives up and raises `SpoolTimeoutError`. Measured against the exact scenario this document exists
  because of: 9.45 GB of supplied pcaps (2026-08-11's live-run figure) would admit fine; the next
  window would hang for half an hour before the failure even surfaces. Not a near-miss.

`spool=None` sidesteps the whole class of bug by construction — there is no cap to block against
because there is no `Spool`. The per-entry `_ZeekWorkItem.owned: bool` predicate itself is exactly as
proposed (required, no default — a permissive default on a delete permission is its own lesson,
`LES-078`) and is unaffected by this: it is what lets `_process_zeek_item` accept `spool=None` for
`owned=False` items today, and what will let a future `H-7` pass a real, populated `Spool` alongside
`owned=False` items without those items ever touching it, once `H-7` exists. **Do not re-attempt the
"admit as non-owning" shape when building `H-7`** — see `LES-081` for the generalized rule.

### D4 in full — the flag table

With `--from-pcaps`:

**Twelve flags, as landed** — the original seven plus five, added by **ruling B** (user, 2026-08-11,
the same day as the original ratification): `--from-pcaps` never builds a `Client` at all
(`_harvest_needs_client` returns `False` unconditionally for this mode — checked, not assumed), so
none of the five credential/transport flags has a receiver either, for the identical reason `LES-021`
already gave the original seven.

| Flag | Behavior | Why |
|---|---|---|
| `--streams` | **rejected** | Zero receivers, verified 2026-08-11 rather than assumed. A stream ID exists **nowhere below `acquire`** — `events`/`ingest`/`windowing`/`learners`/`store`/`synth`/`sinks`/`features` contain none (`ingest/zeek/`'s "streams" are the k-way merge's read iterators, an unrelated sense). Zeek never sees a stream ID, so the cold tier is already stream-free. `harvest` writes **no manifest** and touches `config.streams` in exactly three extraction-time places — validate the spec, expand it for `/axapi/pcap`, seed the size estimator — all of which vanish here. The one place a stream ID is persisted anywhere is `manifest.jsonl`'s header, written by the `pcap` verb alone, consumed only by the collision guard (`runner.py:183`) as extraction resume bookkeeping. |
| `--start`, `--end`, `--interval` | **rejected** | The window set comes from the directory listing. No slicing (scope boundary above). |
| `--filter` | **rejected** | A BPF cannot be applied to bytes already written; accepting it would misdescribe the archive. |
| `--concurrency` | **rejected** | Extract workers, and there is no extraction. |
| `--spool-cap` | **rejected** | The cap governs download growth; nothing is downloading. |
| `--host`, `--profile`, `--api-key-file`, `--ca-bundle`, `--insecure` | **rejected — added by ruling B** | `--from-pcaps` never builds a `Client`, so nothing downstream of authentication/transport ever reads these. `--api-key-file`/`--ca-bundle` are tested for "was this supplied" as `is not None` (their own existing unset semantics in `auth.py`/`client.py`); `--host`/`--profile`/`--insecure` are tested as truthy, matching how `_resolve_host_or_fail`/`auth.resolve_host` already treat them — not one blanket test for all five. |
| `--out` | honored | `<out>/zeek/<window_id>/` exactly as today. |
| `--zeek-workers`, `--zeek-image` | honored | The Zeek stage is unchanged. |
| `--dry-run`, `--yes` | honored | `--yes` still matters: a directory can hold more pcaps than the window ceiling. |
| `--fail-fast` | **honored — genuinely implemented, not merely accepted (ruling A)** | `run_harvest`'s Zeek queue must always fully drain regardless of `--fail-fast`, since its pcaps are disk-resident and cap-consuming and draining is how the space is reclaimed. `--from-pcaps` has no cap and nothing resident, so that reasoning does not transfer: `_zeek_worker_loop`'s new `drain_on_stop=True` (only for this mode) makes `--fail-fast`/a SIGINT actually stop the run instead of merely changing its final exit code. |

`--streams`, `--start` and `--interval` were `required=True` at the parser level; as landed they are
conditionally required, enforced by one function, `_validate_harvest_flags` — required **without**
`--from-pcaps`, refused **with** it — called once from `_dispatch_harvest` before either mode's own
code runs.

## Build steps

1. **`--from-pcaps SRC`** on harvest's parser, `SRC` a directory of `*.pcap`. Make the three
   currently-`required=True` flags conditional, and reject the D4 set with an error naming each
   offending flag (not a generic "incompatible options").
2. **Window discovery** — glob `SRC/*.pcap`, `window_id` from the filename stem. `pcap`'s output is
   already `<start_epoch>.pcap`, which is exactly the spool's `<window_id>.pcap` shape; **assert
   that rather than relying on it.** `spool._validate_window_id` already rejects path separators and
   NUL, but it was written for IDs the tool generates — pointing it at a user-controlled directory
   makes it load-bearing for the first time, so it needs a test that a hostile filename cannot
   escape the spool root.

   **As landed (ruling C, user, resolving an ambiguity in the sentence above):** "assert that"
   is a safety check (a stem must pass `validate_window_id`, now public and reused verbatim, not
   re-implemented), not an epoch-shape requirement. A `window_id` that is safe but does not *look*
   epoch-shaped is still accepted and Zeeked — the ratified scope boundary already directs an
   operator with a large pcap to cut it externally and point `--from-pcaps` at the result, and a
   tool cutting pcap externally generally will not preserve epoch-shaped filenames. Requiring the
   shape would reject the boundary's own intended use. `_looks_epoch_shaped` (a local heuristic,
   matching `timeutil.format_instant`'s two output forms — this layer cannot import `ingest` to ask
   the real parser, CONVENTIONS §3) instead populates `FromPcapsPlan.non_epoch_shaped_window_ids`,
   surfaced as a plan-time note (dry-run and real runs alike) that the resulting `<out>/zeek/<id>/`
   subtree will read downstream as a flat directory of logs rather than a window tree
   (`docs/WORK_ORDER_multi_window_log_read.md`) — informational, never a reason to reject.
3. **Non-owning spool entries** (D3). `_process_zeek_item` calls `spool.release(window_id,
   delete=True)` from exactly one place, guarded by `ZeekResult.ok` — the module docstring calls it
   *"the one invariant this module exists to protect."* That invariant is correct for downloaded
   pcaps and **catastrophic for supplied ones**: a naive implementation deletes the operator's
   source as it goes. On 2026-08-11's run it would have consumed the 9.45 GB extraction and left
   only ~79 MB of logs (recorded that day as "85 MB" — corrected under Verification). Add the
   ownership predicate; do not weaken the delete-only-after-success rule itself.

   **As landed, this step's own title ("non-owning spool entries") is not literally what shipped —
   see "D3 as landed" under Decisions, above.** The ownership predicate (`_ZeekWorkItem.owned`,
   required, no default) is exactly as this step describes; what changed is that `--from-pcaps`
   constructs no `Spool` to admit a non-owning entry INTO, because `Spool.admit()` blocks for
   backpressure and nothing would ever call `release()` to unblock it (a real hang, not a
   near-miss, on exactly this document's own 9.45 GB scenario).
4. **Empty-source guard.** `--from-pcaps` at a directory with no `*.pcap` is a positive-gate
   failure, not a zero-window success (CONVENTIONS §3). **As landed, this also covers
   `--from-pcaps ""`** (an empty string, not an absent flag) — `Path("")` normalizes to `Path(".")`
   and `.is_dir()` is `True`, so an unguarded empty value would have silently pointed the run at the
   current working directory instead of failing (`LES-077`).
5. **Tests.** The ownership predicate is the one that must be mutation-proven: a test that supplied
   pcaps still exist after a successful run, and that downloaded ones are still deleted. Both, or
   the fix is half a fix.

   **As landed:** both halves are tested and mutation-proven, plus coverage this document did not
   originally scope, all found during code review/testing and landed in the same round rather than
   deferred (see CHANGELOG "`harvest --from-pcaps`..." for the full list): a worker thread that could
   raise and die silently while the run still reported success (`LES-076`), a report counter
   (`released`) that used to claim space was reclaimed when it wasn't (`LES-075`), nondeterministic
   ordering of `failed_windows`/`zeek_failed_windows` in the report (12 distinct orderings measured
   across 12 runs before the fix), and a path-escape hardening on the Zeek-output-directory guard
   copied from a sibling call site where the same-looking check does not hold (`LES-079`). Suite grew
   1048 → 1140 tests this round.

## Verification

**Tier 1–2: done, 2026-08-11.** `.venv/bin/python -m pytest -q -m "not slow"` — 1140 tests green.
`./scripts/verify_tier.sh` — all three steps pass (determinism invariants; the CLI round-trip;
three-way transport equivalence), `ruff check src/aximprint/acquire tests/acquire` clean.
`tests/test_acquire_layering.py` — 10/10.

**Tier 3: DONE, 2026-08-11, same day.** User-authorized directly and executed in the coordinator's
main session — not by the `live-verifier` subagent, which was offered the authorization only at one
remove and correctly declined to act on it (no relayed message substitutes for the user's own
say-so). Command: `aximprint acquire harvest --from-pcaps /tmp/aximprint-live-verify-2026-08-11
--out <fresh>` — the 12 real, un-Zeeked pcaps from this session's own live `acquire pcap` run,
compared against the already-Zeeked `/tmp/aximprint-zeek-test/` baseline.

- **12/12 windows ok, `zeek_failed=0`, `released=0`, exit 0, 35.06 s at 4 workers** (inline-script
  baseline: 35.4 s — consistent).
- **35 distinct stems — the stem-name SET diff against the baseline is EMPTY.** The 15 unrecognized
  are the exact set `Q24` already named: `analyzer`, `http`, `ldap`, `ntlm`, `ocsp`, `pe`, `quic`,
  `reporter`, `sip`, `smtp`, `snmp`, `software`, `ssh`, `tunnel`, `x509`. `Q24`'s data is now
  independently reproduced on a second harvest of the same pcaps, not a one-off measurement.
- **Every window logged `zeek ok — source pcap retained`** — the `zeeked_kept` reporting path
  (`LES-075`) exercised on real data for the first time, not just by unit tests.
- **The ownership predicate held on the real corpus.** A smoke test over 2 copied pcaps proved it
  first; then all 12 real source pcaps survived the full run untouched — manifest SHA-256 identical
  before and after, **9,448,439,118 bytes unchanged**.

**Two corrections to the recorded baseline, found while comparing against it — this is why "Why
this exists" and build step 3, above, no longer say "85 MB"/"111×":**

1. **The "~85 MB"/"111× reduction" figures were wrong.** `/tmp/aximprint-zeek-test/` holds **13**
   top-level directories, not 12 — `w1` is a stray leftover (30 files, 6,262,418 bytes), not one of
   the 12 real windows. Measured over the 12 real windows only: baseline **79,068,773 bytes**, this
   run **79,066,413 bytes** — a delta of 2,360 bytes (0.003%), well inside the non-determinism
   described in point 2 below. True reduction: **≈119.5×** (119.496× for the baseline, 119.500× for
   this run), not 111×. Independently reverified: 79,068,773 + 6,262,418 = 85,331,191 ≈ "85 MB", and
   9,448,439,118 / 85,331,191 ≈ 110.7× ≈ "111×" — confirms precisely how the earlier, wrong pair
   arose. Lesson for whoever measures a "the N windows" figure next: glob the `window_id`-named
   entries, don't count directory entries (`LES-082`).
2. **Zeek's own output is not byte-reproducible across two runs over the same pcap — characterized,
   not just asserted.** With the `uid` field stripped and records sorted, `dns.log` and `ssl.log`
   are byte-identical between the two runs. The byte deltas are fully explained by (a) Zeek's
   randomly generated connection UIDs, variable length at 15–18 characters, and (b) record
   ordering. `conn.log` additionally differs by 2 records out of 15,037 (0.013%), likely a
   connection-flush edge case at a pcap-window boundary. Record **counts** are otherwise identical
   everywhere checked — nothing lost or duplicated, only relabeled and reordered. This does **not**
   contradict aximprint's own determinism claim (`LES-002`: same input logs → same outputs, and the
   pipeline gates read a fixed set of logs) — but it does mean "same pcap → same logs" does not
   hold across a re-harvest, which matters precisely because `--from-pcaps` makes re-harvesting
   routine. Opened as `Q25` in `docs/OPEN_QUESTIONS.md`; the strip-and-sort diffing technique itself
   is `LES-083` — needed before trusting any future byte-diff of two independently-Zeeked outputs.
