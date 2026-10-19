# Work Order — WO-7: packaging, runbook, and handoff

> **Genre:** work order (see `docs/CONVENTIONS.md` §3) — an execution ticket for the `/dev` Pipeline. Concrete files, concrete gates, time-bounded. Mark it **DONE** in place when its run lands; don't delete it.
>
> **Status:** ✅ DONE for phases 1–4 (packaging, README, runbook, and the handoff/registers pass —
> CHANGELOG 2026-08-04 "`xtract` iteration-1 build complete"). **Phase 4's live-acceptance
> criterion (§4.3 below — the runbook executed end to end) is now *partly* met, as of 2026-08-05:**
>
> - **Exercised live:** `docs/RUNBOOK_CORPAPCN.md` §2 (`xtract streams --json` — 12 streams, plausible
>   ranges) and the core of §4 (`xtract pcap` — a real 60-second, 7,058-packet extract, valid pcap,
>   exit 0) both ran against CorpAPCN and matched their expected evidence. §6's boundary check (Q4)
>   was independently confirmed: two adjacent 30s windows shared no packet after the fix.
> - **Not exercised live in this pass:** §3's `--dry-run` step, and — most notably — **§5's
>   interrupt-and-resume scenario** (Ctrl-C mid-run, confirm `skipped` count and "refetched nothing")
>   was not run against the real appliance; that path is proven only against mocks
>   (`tests/test_runner.py`, `LES-037`). §1's precondition steps (credential-file mode check, TLS
>   posture confirmation) were satisfied as a side effect of the run succeeding at all, but weren't
>   independently walked step-by-step as the runbook prescribes.
> - **This is not full WO-7 §4.3 acceptance** — both of the user's original tasks are now demonstrated
>   live, which is the substance of what §4.3 asks for, but the runbook's own interrupt/resume
>   evidence gate is still open. Treat a future live resume test as the remaining acceptance step, not
>   a new work order.
>
> **Authority for the decisions behind it:** CHANGELOG 2026-08-04 — `xtract` design ratified; iteration-1 scope and work orders

## 1. Goal

`xtract` is installable and runnable by someone who has never seen this repo, and the gated
end-to-end proof against CorpAPCN is written down as a runbook rather than living in one session's
memory. After this lands, iteration 1 is closed: both user tasks work, and the next person can
verify that claim themselves.

**Not a goal:** publishing to an index, containerizing, or CI infrastructure. Local install and a
written runbook.

## 2. Build — phases, in order

### Phase 1 — Packaging

- **Files:** `pyproject.toml`, `README.md`, `.gitignore`
- **Behavior:**
  - Console entry point `xtract` → `xtract.cli:main`. Runtime dependency: `requests` **only**.
    Dev extra: `pytest`, `ruff`.
  - `README.md` covers install, the two commands with a worked example each, the credential
    resolution order, and a pointer to `docs/AXAPI_CONTRACT.md`. **No real hostname, username, or
    key appears anywhere** — placeholders only.
  - `.gitignore` excludes `*.pcap`, `*.pcap.part`, `manifest.jsonl`, and any default output
    directory **before** the first run that could produce them. Committing capture data is the
    failure this prevents, and it is much easier to prevent than to undo.
- **Tests:** a clean-venv install exposes a working `xtract --help`; `xtract --version` reports the
  packaged version.

### Phase 2 — Verification wiring

- **Files:** `.claude-workflow.env`, `scripts/verify_tier.sh` (config only)
- **Behavior:** set `CLAUDE_VERIFY_STEPS` to the real gates — fast unit tests, `ruff check .`, and
  the soft doc-counter check — so `verify_tier.sh` stops auto-detecting and starts asserting.
- **Tests:** `scripts/verify_tier.sh` runs all three steps and fails when any does.

### Phase 3 — The runbook

- **Files:** `docs/RUNBOOK_CORPAPCN.md` (new)
- **Behavior:** the gated live procedure, written so `live-verifier` (or a human) can follow it
  without improvising:
  1. Preconditions — profile configured, credential file mode-checked, TLS posture known.
  2. `xtract streams --json` → expected shape, and how to sanity-check the ranges against Q1's
     recorded answer.
  3. `xtract pcap --dry-run` over a small range → expected window count.
  4. The real ~10-window run → expected outputs, manifest contents, how to read the summary.
  5. Interrupt-and-resume → what "refetched nothing" looks like in the manifest.
  6. The boundary check → adjacent windows share no packet (Q4).
  7. Rollback/cleanup — what to delete locally. **Nothing is ever cleaned up on the appliance**;
    `xtract` creates nothing there but task records.
  - Every step states its **expected evidence**, not just its command. A step whose success
    criterion is "no error" proves nothing.
  - **STOP points** marked wherever the operator must confirm before proceeding.

### Phase 4 — Handoff and registers

- **Files:** `docs/SESSION_STATE.md`, `docs/PRODUCT_GAPS.md`, `docs/LESSONS.md`, `docs/CHANGELOG.md`
- **Behavior:**
  - `SESSION_STATE.md` filled in for real: what works, what's verified against CorpAPCN vs. only
    against mocks, what's next.
  - `GAP-NNN` entries for everything knowingly left incomplete — at minimum: `archive=`/`servers=`
    multi-extract, the async transport, no parallelism, no job file, no `create-key`, and any Q1–Q4
    answer that forced a compromise.
  - Mark WO-0…WO-7 **DONE** in place with their CHANGELOG dates. Don't delete them.
- **Tests:** `python3 scripts/doc-stats.py --check` clean.

## 3. Boundaries — explicitly OUT of scope

- No PyPI publication, no container image, no CI pipeline, no systemd unit.
- No new features. If Phase 3 reveals a missing capability, file a `GAP-NNN` — do not fix it here.
- Anything that would trip the risk gate — surface it as `DECISION NEEDED:` instead of doing it.

## 4. Validation

1. **Unit:** the full suite green in a clean venv created from `pyproject.toml` alone.
2. **Hermetic tier:** `scripts/verify_tier.sh` clean with the real steps wired.
3. **Live (gated):** `docs/RUNBOOK_CORPAPCN.md` executed end to end against CorpAPCN, with the
   evidence from each step recorded. **This is the acceptance of iteration 1** — both of the user's
   original tasks demonstrated on the real system.
4. **Docs:** CHANGELOG entry (Authority-marked), `doc-stats --check` clean, all work orders marked
   DONE, `GAP-NNN` filed for every known incompleteness.

## 5. Depends on / sequence

- **Depends on:** all of WO-0…WO-6, and the live probe.
- **Unblocks:** iteration 2 — whichever of the deferred items the user prioritizes.
