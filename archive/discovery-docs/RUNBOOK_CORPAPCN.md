# Runbook — `aximprint acquire` end-to-end against CorpAPCN

> Written for the standalone `xtract` project (see §0's own precondition note and the `xtract`-named
> paths inside it, both historical); the tool it exercises landed in this repo as `acquire/` and its
> user-visible surface was renamed 2026-08-10 (`aximprint acquire`, no `xtract` binary any more —
> CHANGELOG 2026-08-10, commit `a7ba73c`). Commands, expected output, and credential locations below
> are rewritten to the current surface; dated/historical prose describing what ran under the old name
> is left as it was.

> **This document is written, not executed, by `docs/WORK_ORDER_packaging_runbook.md` (WO-7).**
> Nobody has a CorpAPCN hostname, credentials, or standing permission to touch the appliance at the
> time this runbook was written. Every command below is a plan the operator (a human, or
> `live-verifier` under human supervision) follows step by step when that permission exists — it is
> not evidence of anything having run. See §0 for exactly what's gated and why.
>
> **Genre:** one-off verification runbook (`docs/CONVENTIONS.md` §3 — named by subject, outside the
> five-genre taxonomy, same as `SESSION_STATE.md`).

## 0. What this proves, and what it deliberately does not touch

This is the acceptance test for iteration 1 (`docs/WORK_ORDER_packaging_runbook.md` §4.3, written
and run under the standalone `xtract` project — historical): both of the user's original tasks —
"what data do we have" (`aximprint acquire streams`) and "give me the packets"
(`aximprint acquire pcap`) — demonstrated against the real appliance, not just against the
mock-backed test suite.

**Every command in this runbook is read-only from `axapi`'s perspective**, with one narrow,
documented exception: `aximprint acquire pcap` creates task records on the appliance for the
duration of each extraction (that's what makes an in-flight extract abortable —
`docs/AXAPI_CONTRACT.md` §3) and, naturally, an extraction call itself. Nothing here ever calls a
mutating endpoint from `docs/AXAPI_CONTRACT.md` §7 — no `DELETE /axapi/fs/*`, no `POST /axapi/clear`,
no service control, no key creation. **Nothing is ever cleaned up on the appliance** by this runbook
or by `aximprint acquire` itself; §7 below is local-only cleanup, and that boundary is deliberate,
not an oversight — see `docs/AXAPI_CONTRACT.md` §7's closing note ("absent code cannot be called by
mistake").

**A step whose success criterion is "no error" proves nothing.** Every step below states the
**expected evidence** — the specific output, file, or manifest content that has to be true, not
just the absence of a traceback. **STOP** markers are points where the operator confirms before
continuing; do not script past one.

### Precondition: Q1–Q4 are resolved — ✅ done, 2026-08-05

`docs/OPEN_QUESTIONS.md` Q1 (units of `fs/files` timestamps), Q2 (integer vs. sub-second
`start_time`/`end_time`), Q3 (does a stream directory `name` equal its numeric `stream_ids`), and
Q4 (is `end_time` inclusive) were resolved by `docs/WORK_ORDER_live_probe.md` on 2026-08-05 (now
DONE). Answers, in brief (full evidence: `docs/AXAPI_CONTRACT.md` §10, `docs/VENDOR_API_DEFECTS.md`):
Q1 = unix epoch **seconds**, delivered as strings; Q2 = sub-second precision is honored, but a **zero**
fractional part widens to cover the whole second; Q3 = stream directory names are `streamNN`, not bare
integers; Q4 = `end_time` is **inclusive**, with the same zero-fraction-widens-to-the-second behavior
as Q2. This runbook, and the two commands it exercises, have themselves now been run once end to end
against CorpAPCN using these answers (CHANGELOG 2026-08-05 "Live verification against CorpAPCN") —
what follows is no longer a hypothetical procedure.

**If you are re-running this on a *different* CorpAPCN deployment or after a firmware update,** do
not assume these answers transfer unchanged — re-run `docs/WORK_ORDER_live_probe.md`'s Phase 3/4
checks first and compare against the values recorded here and in `docs/VENDOR_API_DEFECTS.md`. §6
below re-checks Q4 independently at the scale of a real run for exactly this reason — a second,
larger-scale confirmation is cheap and it's the one fact `windows.py` cannot self-correct if the
answer turns out to depend on stream or load characteristics the original probe didn't exercise.

---

## 1. Preconditions

Confirm each of these before running anything against the appliance.

1. **Profile configured.** **CorpAPCN, as probed 2026-08-05, has no API keys at all** — auth is
   username/password → JWT only (`GAP-001`'s reclassification note in `docs/PRODUCT_GAPS.md`). Use a
   `~/.config/aximprint/config.toml` profile with `[profile.<name>]` `user` + `password_file` (see
   `README.md` "Credentials" / "Unattended runs" for the exact shape and the `chmod 600` step) — this
   is the way to configure credentials for this deployment, TTY or not. **If a profile already exists
   at the old `~/.config/xtract/config.toml`,** it is still read as a deprecated fallback (a warning
   is printed) — that is enough to keep this runbook working unattended, but new setups should use the
   `aximprint`-named path; nothing auto-migrates the file (CHANGELOG 2026-08-10). `--api-key-file`
   remains supported by `aximprint acquire` in general (a different deployment may have keys), but do
   not expect it to apply here.
   **Evidence:** `aximprint acquire --profile <name> streams --json` does not fail with exit code
   `3` (`docs/AXAPI_CONTRACT.md` §1; `README.md` "Exit codes"). A `3` here means credential
   resolution or authentication failed — fix it before continuing, not by retrying blindly.
2. **Credential file mode-checked.** For a profile's `password_file` (or `--api-key-file` on a
   deployment that has keys), confirm its permissions before the first run: `stat -c '%a' <path>`
   must print `600`. `aximprint acquire` itself refuses (not silently reads) a group-/world-readable
   key or password file (`acquire/auth.py`, `InsecureCredentialFileError`) — this precondition just
   means you won't discover that from a failed run instead of a deliberate check. **Evidence:**
   `stat` output is `600`.
3. **TLS posture — known and fixed for CorpAPCN as probed 2026-08-05: `--insecure` is required, and
   always will be for this appliance.** The certificate is **self-signed with no Subject Alternative
   Name** (`CN=<appliance hostname, redacted>` only). Python has ignored bare `CN` for hostname verification since
   3.7 (PEP 476 / RFC 6125), so certificate verification **cannot succeed under any trust anchor**
   against this specific appliance — pinning the certificate as a private CA (`--ca-bundle`) does not
   help, because the SAN mismatch is what fails, not the chain of trust. This is why the vendor's own
   example script hardcodes `verify=False`. **SHA-256 fingerprint, for out-of-band confirmation this
   is the expected appliance and not a MITM:** **[REDACTED — doc-writer, 2026-08-10, `LES-003`:** a
   certificate fingerprint uniquely identifies one real appliance and is now treated as
   appliance-identifying material, the same as a hostname. Re-obtain it out-of-band before relying on
   this step — `openssl s_client -connect <host>:<port> -servername <appliance hostname> </dev/null
   2>/dev/null | openssl x509 -noout -fingerprint -sha256` — confirmed against the appliance
   administrator through a channel other than this repo. Do not restore the old value from git
   history.**]**
   **Evidence:** the first `streams` call in §2 succeeds with `--insecure` and does not fail with a
   TLS error. If a different deployment presents a proper CA-signed or SAN-bearing private cert,
   `--ca-bundle` is preferred there instead — this precondition is deployment-specific, not a general
   `aximprint acquire` default (don't make `--insecure` the default; the citation this line originally
   carried, `CLAUDE.md` "What NOT to Do", was `xtract`'s own now-superseded `CLAUDE.md` and does not
   resolve in this repo's — found while rewriting this file for the naming retirement, fixed in
   passing).
4. **Q1–Q4 resolved.** See the precondition above. **Evidence:** `docs/OPEN_QUESTIONS.md` shows all
   four resolved, linked to CHANGELOG 2026-08-05.
5. **Scope agreed for the real run (§4).** Before touching the appliance, the operator has picked:
   which `--streams`, a `--start`/`--end` pair that is deliberately small (the work order's own
   target is **~10 windows**, not a production-scale pull), and an `--interval`. Write these down
   for this run (again, not in this file) — §5's interrupt test and §6's boundary check both re-run
   against the *same* choices.

**STOP — confirm all five before proceeding to §2.**

---

## 2. `aximprint acquire streams --json` — what data do we have?

```console
$ aximprint acquire --profile <name> streams --json
```

**Expected shape:** a JSON array, one object per stream, each with `stream`, `first`/`first_utc`,
`last`/`last_utc`, `duration_ns`, `files`, `bytes`, `gaps.count` (`acquire/cli.py`
`_record_for_json`). A stream with no stored data has `first`/`last`/`duration_ns` all `null` — not
an error, not a missing entry (`acquire/catalog.py` `StreamRange`: "an empty stream yields no range,
not a zero-length one").

**Sanity-check against Q1's recorded answer, now with a known-good baseline (measured 2026-08-05,
values will drift over time on a live-capturing system — treat these as "the right order of
magnitude and shape," not exact numbers to match):**

- **Stream count:** 12 streams, named `stream00`…`stream11` (Q3 — `docs/AXAPI_CONTRACT.md` §6.2). If
  the count or naming differs, that's a real finding about this deployment, not necessarily a bug.
- Pick one stream known to hold recent data. Its `first_utc`/`last_utc` must read as a **plausible
  recent date** — the exact test Phase 3 of `docs/WORK_ORDER_live_probe.md` used to establish Q1 in
  the first place; on 2026-08-05, each stream held on the order of **~48 days** of capture data. If
  `first_utc` reads as, say, the year 1970 or a date centuries in the future, the unit conversion in
  `acquire/timeutil.py` (`instant_from_fs_field`) disagrees with what the live probe actually found on
  this appliance — **stop and re-open Q1**, don't adjust the run to work around it.
- Cross-check `duration_ns` against a mental estimate of how long that stream has plausibly been
  capturing. An order-of-magnitude mismatch (e.g. a stream you know has run for hours reporting a
  `duration_ns` implying milliseconds) is the same class of unit problem.
- **Gap noise:** expect the gap breakdown to show a small number of real gaps, not dozens — the
  `catalog.DEFAULT_GAP_THRESHOLD` (2s) floor already absorbs the second-resolution rounding noise
  that would otherwise show up as ~1s gaps at nearly every file boundary (CHANGELOG 2026-08-05
  "Gap-detection threshold defaults to 2s"; measured on one stream: 125 raw gaps → 1 real gap once the
  floor was applied). A gap count in the hundreds after filtering suggests the threshold isn't being
  applied, not that the stream is unusually unhealthy.
- If `--streams` narrows the request (e.g. `--streams "2-4"`), confirm the returned `stream` values
  match the requested ids exactly, and that an id **not** on the appliance produces exit code `2`
  with a clear "unknown stream id(s)" message (`acquire/cli.py` `_select_listings`) — not a traceback.

**Evidence this step passed:** the JSON parses, at least one stream shows a plausible non-null
range, and the unit sanity checks above hold. Also run the table form once
(`aximprint acquire streams --gaps`, no `--json`) and confirm it's human-readable and the gap
breakdown lines match the JSON's `gaps.count`/`total_duration_ns`/`largest_duration_ns` for the
same streams.

**STOP — confirm the ranges look right before choosing the `--start`/`--end` for §3/§4.** Pick a
range that sits well inside one stream's reported `[first, last]`, not at its edges — you want real
data in every window, not a run whose main finding is "the edge of stored data is exactly where I
put it."

---

## 3. `aximprint acquire pcap --dry-run` — plan before you pull

```console
$ aximprint acquire --profile <name> \
    pcap --streams "<spec>" --start <start> --end <end> --interval <interval> \
    --out <local-dir> --dry-run
```

Recall from `README.md`: with an **explicit** `--end`, `--dry-run` makes no network call at all and
needs no credentials (`acquire/cli.py` `_pcap_needs_client`) — a good first sanity check even before
§1's credential preconditions are fully wired up, if you already know the range you want.

**Expected output** (`acquire/cli.py` `_print_pcap_dry_run`):

```
aximprint acquire pcap: dry run — no /axapi/pcap calls made
  out:      <local-dir>
  manifest: <local-dir>/manifest.jsonl
  end:      explicit
  windows:  N (0 already done, N to run)
  first:    <start> .. <window-1-end>
  last:     <window-N-start> .. <end>
  total duration: <end-start>
```

**Evidence:** `N` matches your own arithmetic — `ceil((end - start) / interval)`, the same tiling
`acquire/windows.py plan()` performs (final window clipped to `--end`, never dropped, never
extended). **This is the point to confirm `N` is close to the ~10-window target from §1's
precondition 5** — if it's off by an order of magnitude, the `--interval` or the `--start`/`--end`
pair is wrong, not `aximprint acquire`. Also confirm **no** `manifest.jsonl` or `*.pcap` file appears
in `<local-dir>` after this step (`--dry-run` never touches either).

**STOP — confirm `N` matches expectations before running §4 for real.** A dry run that produces
1,000 windows because of a typo'd `--interval` is cheap to catch here; the same mistake in §4 is 1,000
real extraction requests against the appliance.

---

## 4. The real run — ~10 windows

```console
$ aximprint acquire --profile <name> \
    pcap --streams "<spec>" --start <start> --end <end> --interval <interval> \
    --out <local-dir>
```

**Expected behavior while it runs:** one progress line per window (`acquire/cli.py`
`_print_pcap_progress`; the progress lines themselves carry no `aximprint acquire`/`xtract` prefix
either way — they are bare `[i/N] <start_time>: <event>`, unaffected by the naming retirement):

```
[1/10] <start_time>: written
[2/10] <start_time>: empty (418: ...)
...
```

`written` means a non-empty `<start_time>.pcap` landed in `<local-dir>`; `empty` (whether from a
`418` or a zero-byte `200`) is a **normal, expected outcome**, not a failure
(`docs/AXAPI_CONTRACT.md` §6.1; `LES-035`) — do not treat a run with several `empty` lines as
having gone wrong.

**Expected outputs, evidence to check once it finishes:**

1. **Files.** `ls <local-dir>/*.pcap` — one file per `written` window, named exactly the
   `start_time` wire string sent on the request (no stream ids in the name — that's why the
   manifest is load-bearing, per the CHANGELOG). No `*.pcap.part` files left behind (a `.part` is
   renamed atomically into place only on success; a leftover `.part` after a clean exit is itself a
   bug worth filing as a `GAP-NNN`, not something to work around).
2. **Manifest.** `cat <local-dir>/manifest.jsonl` — the **first** line is a `"kind": "header"`
   record carrying this run's `streams`/`filter`/`interval_ns`/`tool_version`
   (`acquire/manifest.py`). Every subsequent line is a `"kind": "window"` record, one per attempted
   window, with `outcome` one of `written`/`empty`/`failed`, plus `bytes`, `task_guid`,
   `duration_s`, `attempt`, `timestamp`. Count the window records: it must equal the plan's `N` from
   §3. Confirm no record contains a hostname, credential, or capture byte — the manifest format has
   no field for any of them (`acquire/manifest.py` module docstring), so this is really "confirm the
   format wasn't violated," not a search for a leak.
3. **Summary line** (`acquire/cli.py` `_print_pcap_summary`; **the field set changed from what
   iteration 1 originally documented here — `reextracted=` was added for the clipped-window-resume
   case, `LES-037`, unrelated to the naming retirement but caught while re-verifying this block against
   the real code rather than doing a bare find/replace on the tool name):**
   ```
   aximprint acquire pcap: written=W reextracted=R empty=E failed=F skipped=0 bytes=<total> elapsed=<duration>
   ```
   `W + R + E + F` must equal `N`. `skipped=0` on a fresh `--out` (nothing to resume yet — §5
   exercises the non-zero case). `reextracted=0` here and, checked against `runner.plan_resume`,
   **also stays `0` in §5** — it fires only when a resumed run's plan computes a *different* `end`
   for an already-recorded `start` than the earlier run did (`LES-037`'s clipped-window case, e.g. an
   omitted `--end` that grew between runs), and §5 deliberately re-runs with identical
   `--start`/`--end`/`--interval`, so its plan can never disagree with itself. **This runbook's
   procedure never exercises `reextracted` going non-zero** — worth knowing before reading a `0` there
   as "resume is untested," and worth filing as a `GAP-NNN` if that coverage gap matters later; not
   fixed here; found while re-verifying this block against the real code rather than doing a bare
   find/replace on the tool name. `failed` should be `0` on a first clean run against known-good
   data; if it isn't, read the "failed windows:" line printed to stderr, which names each failed
   window's `start_time` (cross-reference against the manifest's `detail` field for that record)
   before deciding whether to retry.
4. **Exit code.** `0` if `failed == 0`, `1` if any window failed (`README.md` "Exit codes") — check
   `echo $?` after the run, not just the printed summary.

**STOP — do not proceed to §5 until `written + reextracted + empty` accounts for every window and the
manifest line count matches.** A short manifest (fewer window records than windows attempted) means a
crash mid-run that didn't go through `aximprint acquire`'s own exit path — worth understanding before
layering the interrupt test in §5 on top of it.

---

## 5. Interrupt-and-resume — "refetched nothing"

This step deliberately re-runs against the **same** `--out` from §4, so plan it as a fresh directory
if you want §4's evidence to stay intact for later reference — or accept that this step's manifest
supersedes §4's (same file, appended to).

1. Start a **fresh** run into a **new** `--out` (so this test's evidence isn't tangled with §4's):
   the same `--streams`/`--start`/`--end`/`--interval`, different `--out`.
2. Interrupt it with `Ctrl-C` partway through — after at least one window has completed (watch the
   progress lines; interrupt after `[2/N]` or later, not before `[1/N]` finishes).

   **Expected evidence of the interrupt itself:**
   - Exit code **`130`** (`README.md`'s exit code table; `acquire/cli.py`
     `_SIGINT_EXIT_CODE`).
   - stderr prints an instruction containing the word "resume" — the exact current message is
     `"aximprint acquire: interrupted — re-run the same command (same --streams/--filter/--interval/--out)
     to resume"` (`acquire/cli.py` `cmd_pcap`; verified against the source for this rewrite, not
     assumed from the old wording).
   - No `*.pcap.part` file left in `<local-dir>` — the in-flight window's task was aborted
     server-side (best-effort `DELETE /axapi/task/<ctid>`, `acquire/extract.py` `_abort_task`) and
     its partial file cleaned up, not left as debris.
   - The manifest contains exactly the window records for windows that **completed** before the
     interrupt — the interrupted window itself has **no** record (neither `written` nor `failed`);
     it will be attempted fresh on resume.
3. **Re-run the identical command** (same flags, same `--out`).

   **Expected evidence — this is the "refetched nothing" proof:**
   - The summary's `skipped` count equals the number of windows that had already completed before
     the interrupt.
   - Every progress line for an already-done window reads `skip (pcap file already present)` or
     `skip (recorded empty in the manifest)` (`acquire/runner.py` `plan_resume` — a window is
     "already done" if its `.pcap` exists on disk, **or** the manifest's *latest* record for it is
     `empty`; a `failed` record is retried on purpose, that's the point of resume).
   - The manifest's window-record count after the resumed run equals `N` (the original plan size) —
     not `N` plus a duplicate set from windows that were already done. `runner.execute` only appends
     a record for a window it actually attempts.
   - `bytes` in the resumed run's summary line reflects only the newly-fetched windows' bytes, not
     a re-sum of everything — confirming the already-written files were never re-requested, not just
     that they weren't re-*written*.
   - Exit code `0` (assuming no window fails on the resumed attempt).

**STOP — if any already-done window shows a fresh progress line other than `skip (...)`, or the
manifest gained a second record for a `start` that already had one, resume is not behaving as
designed. Do not proceed to §6 on a resume that re-fetched anything — file a `GAP-NNN` instead.**

---

## 6. The boundary check (Q4) — adjacent windows share no packet

Even with Q4 resolved by the live probe (§0's precondition), this step re-confirms it at the scale
of the real §4/§5 run, not just the live probe's single ~1-second pair — a cheap, independent
check worth doing before calling iteration 1 accepted.

**Known-good reference (measured 2026-08-05, on this appliance):** splitting a 60s window starting
at second `1785934195` into two adjacent 30s windows and applying the Q4 fix produced
`…195.002029→224.993575` for the first and `…225.025223→254.998116` for the second — no shared
timestamp. Sent unadjusted (the pre-fix, exclusive-end assumption), the same 60s span returned 7,058
packets; split and corrected, the two windows returned 4,328 + 2,653 = 6,981 packets — the 77-packet
difference is exactly the duplicated final second the fix removes. If your run's numbers look
qualitatively like this (a small, boundary-sized packet-count difference between "one big window" and
"two adjacent windows", not zero and not a large fraction of the total), that's consistent with Q4
still being correctly resolved on this appliance.

1. From §4 or §5's output, pick two **adjacent** windows — window *i*'s `end_time` equals window
   *i+1*'s `start_time` (`acquire/windows.py plan()` tiles them contiguously; confirm this from the
   manifest's `start`/`end` fields for the two records, not by assumption).
2. Using a local pcap tool (e.g. `tcpdump -r <file> | tail -1` for window *i*'s last packet,
   `tcpdump -r <file> | head -1` for window *i+1*'s first packet, or `capinfos`/`tshark` if
   available), compare the two files' packet sets **at the boundary timestamp** — not just packet
   counts, since two different quiet windows could coincidentally have the same count.

**Expected evidence:**

- **No packet's timestamp+contents appears in both files.** If `Q4_END_TIME_INCLUSIVE` is correctly
  set (`acquire/windows.py`) for what the live probe found, adjacent half-open windows `[t,
  t+interval)`/`[t+interval, t+2·interval)` never both claim the packet at exactly `t+interval`.
- If a duplicate boundary packet **does** turn up despite Q1–Q4 supposedly being resolved, that's a
  real finding, not something to route around locally: `docs/OPEN_QUESTIONS.md` Q4 needs
  re-examination (possibly the appliance's behavior is data- or load-dependent in a way the small
  live-probe pair didn't surface) — **stop, do not "fix" it by trimming files locally**; the fix, if
  any, belongs in `windows.py`'s `Q4_END_TIME_INCLUSIVE` constant, the one place that's designed to
  absorb it.

**STOP — record the outcome (duplicate found / not found) before moving to cleanup.** This is part
of the acceptance evidence for iteration 1, not a step to skip because §0–§5 already went well.

---

## 7. Rollback / cleanup — local only

**Nothing is ever cleaned up on the appliance.** `aximprint acquire` creates nothing there but task
records for the duration of each extraction (`docs/AXAPI_CONTRACT.md` §3), and those are
appliance-managed, not something this runbook — or `aximprint acquire` itself — deletes, lists for
deletion, or otherwise touches after the fact. There is no `DELETE /axapi/fs/*` call anywhere in this
tool, on purpose
(`docs/AXAPI_CONTRACT.md` §7); a runbook step that "cleaned up the extract" on CorpAPCN would be
introducing exactly the capability that section exists to keep absent.

**What to delete locally**, once the evidence above has been recorded (screenshots, copied terminal
output, or notes — not the committed runbook itself, which stays generic):

```console
$ rm -rf <local-dir-from-§4> <local-dir-from-§5>
```

That removes the `*.pcap` files and `manifest.jsonl` this runbook produced. `.gitignore` already
excludes `*.pcap`, `*.pcap.part`, and `manifest.jsonl` repo-wide (`docs/WORK_ORDER_packaging_runbook.md`
§2 Phase 1) — so even a run pointed at the repo root by accident (`--out`'s default is `.`) can't
land capture data in a commit. Confirm with `git status` that nothing capture-shaped is showing as
untracked before doing anything else in this repo.

**Evidence this step is complete:** `git status` shows no `*.pcap`/`manifest.jsonl` entries, and the
local run directories from §4/§5 no longer exist.
