# Work Order — Persisted op inputs no longer describe the guests: out-of-band `run-script` drift

> **STATUS: BUILT (option A) — 2026-09-02, Authority: user** (ruling on §4, over options B and C;
> the original framing below was Authority: report, Claude Code, 2026-09-02, for user decision).
> `axrange te deploy --update --reapply` is implemented, tested and documented (commit `4c2b7679`,
> merged `f2162e55`; CHANGELOG 2026-09-02) — see §4 "Option A" for the design this build
> implements. Options B and C remain unbuilt, not chosen — Option A does not preclude them if a
> future need (e.g. `provision-app`/`pkg:` drift) arises. **Gated `dev45` live-verify is PENDING**,
> per §7's own plan: against a **throwaway** two-host twin, never the standing one. The standing
> twin's **19 out-of-band-pushed responder guests are the natural live-verify subject in spirit but
> are NOT a substitute for the gated run** — and they are, if anything, a *worse* case than when
> this file was written: the iteration-19 CorpAPCN pass (commit `128f498c`, 2026-09-02) re-pushed
> corrected responders to those same 19 guests over QGA **again**, rather than exercising the new
> `--reapply` path (a `tfc`-tier persona iteration, not a `run-script` content change, so
> `--reapply` did not apply to it) — the state store remains stale for exactly those rows.
> Registered per §8: `docs/PRODUCT_GAPS.md` **GAP-054**. §4 deliberately presented three options and
> **recommended none**; picking one was a design call with a version-locked consequence in two of
> the three cases, and belonged to the user (`CLAUDE.md` "Design Document Maintenance" risk
> triggers) — the historical framing below is kept as written, not rewritten with hindsight.
> **Route:** decide §4 first, then Pipeline (`/dev`) — code-writer → tester → code-reviewer →
> doc-writer → **gated `dev45` live-verify** (still pending, see above). A gated `dev45` live-verify
> was required for options A and C (both change what `te deploy --update` does against the standing
> 138-VM twin); option B was state-store-only.
> **Version-locked:** **depends on the option.** **A** (supported content-only re-apply) implements
> a row of `docs/PLANNER.md` §9.2's deliberately-unbuilt redo-rules table — a `PLANNER` spec change,
> Authority: user. **B** (record out-of-band drift) needs a new `ops` column or table ⇒
> `axrange/planner/state.py:71`'s `SCHEMA_VERSION 3 → 4`, which **fails every existing `state.db`
> loud** (`StateStoreSchemaError`, `state.py:370-378`) and forces a re-init on every cluster —
> including the one the standing twin lives on. **C** (fail loud on a content-hash mismatch) needs a
> hash **the system does not currently record anywhere** (see §3.3), so it is an `OP_CATALOG`
> outputs change for `OP-APP-030` — also Authority: user. Op count (40) and rule count (114) are
> unchanged in all three.
> **Spec:** this file + `docs/PLANNER.md` §9.1/§9.2 + `docs/WORK_ORDER_twin_incremental_update.md`
> §3.1. **Not yet registered in `docs/PRODUCT_GAPS.md`** — the next free id at time of writing is
> **GAP-054**.
> **Standing-cluster note:** the affected twin (`te corpapcn-twin-0e4f7d58`, 138 VMs) is **UP on
> dev45 under a standing do-not-destroy instruction.** Nothing in this work order may destroy or
> redeploy it.

## 1. What happened

Iteration 16 of the CorpAPCN twin-fidelity loop moved **which ports the personas emit at**, and
deliberately withheld the matching responder scripts from the deployed twin — on the reasoning that
a standing twin should keep the responders it was deployed with, so repo content mirrors deployed
reality rather than running ahead of it. Correct about reproducibility; wrong about consequence.

**228 emit rows landed on dead ports, and each cost twice.** The forward flow never completed, so
the conversation it should have produced is simply missing; and each guest's kernel returned an
**ICMP port-unreachable**, which Zeek recorded with the **replier** as originator and a twin host as
destination — i.e. scored as a **local edge**, the exact axis the iteration was being judged on.
**220 ICMP local rows**, split 197 (`web-tier→generic-endpoint`) + 23
(`dns-resolver→generic-endpoint`), both predicted from row-counting before the run and both measured
exactly. A fabricated edge whose count is derivable from the emit table is not a persona behavior.

**The repair pushed corrected responder scripts to 19 guests out-of-band over QGA**, with listeners
verified in-guest via `ss -lnH -tu` rather than trusted from `systemctl is-active`. The
`script_content` a `run-script` op delivers is part of its persisted `inputs_resolved`
(`axrange/planner/pass1_emit.py:1162`), so a content-only edit trips `--update`'s mutation refusal
(§3.1). **The alternative was rebuilding 138 VMs to change five shell scripts.** The out-of-band
push was the right call under the circumstances; it left a defect behind.

Full narrative: aximprint `docs/JOURNAL_corpapcn_fidelity.md`, "Iteration 16" and "Iteration 18";
the axRange-side record is commit `b573e39a`'s message, which states the divergence in as many
words.

## 2. The resulting defect

**The 19 guests run responders whose text differs from the `script_content` persisted in their
`run-script` op rows. The deploy state no longer describes the guests.**

Three consequences, in ascending order of how quietly they fail:

1. **A future `te deploy --update` classifies those ops `Unchanged` when they are not.** The diff is
   computed **row vs. new plan** (`axrange/planner/plandiff.py:175-190`) and never consults the
   guest. Both sides still carry the *old* text, so they compare equal and the op is skipped by the
   resume path with no adapter call.
2. **It silently reverts nothing — but never restores the recorded state either.** This is the
   subtle half. An `Unchanged` classification is not a decision to keep the guest's newer content;
   it is a decision *not to look*. The guest keeps the newer responders (fine, today), and the
   engine keeps asserting a state it is not maintaining. The moment anything *else* forces those ops
   to re-run — `--fresh`, a redeploy, an escalate-retry after a failure — the guests silently revert
   to the stale persisted text with no diff, no warning and no record that a revert occurred.
3. **The printed diff is false.** `--update` prints its classification before executing
   (`docs/PLANNER.md` §9.3, `api/service.py:380-386`) and requires confirmation on removals. An
   operator reading "N unchanged" is being told something the system has not checked and, as built,
   cannot check.

**Blast radius:** 5 distinct scripts × 19 guests on one standing twin today. But the mechanism is
general — it applies to **every** out-of-band change to any `config`-class op's delivered content,
on every twin, and there is no reason to think this is the last time the escape hatch gets used
(§5 argues it will keep being used until option A exists).

## 3. Why the mechanism is what it is

### 3.1 The refusal is correct, and it is why the escape hatch was needed

`plandiff._diff_fields` (`plandiff.py:131-145`) compares the freshly-built op's resolved inputs
against the persisted `inputs_resolved_json` **key by key**. Any difference ⇒ **Mutated** ⇒
`api/service.py:381` refuses the whole `--update`, naming op + field + old→new (`:442-446`). This is
the documented, deliberate v1 semantic: *"Mutation is refused, not silently redone … a
partially-correct redo table is worse than none"*
(`docs/WORK_ORDER_twin_incremental_update.md` §3.1). Nothing here argues the refusal is wrong. The
gap is that **there is no supported path that a content-only script change can take instead**, so
the only routes available were a 138-VM redeploy or going around the engine.

### 3.2 `docs/PLANNER.md` §9.2 already names this exact case

The redo-rules table's own illustrative rows include **`apps[*].install` → "Redo affected install
action ops"** — and `OP-APP-030 run-script` already carries
`redo_on=("apps[*].install",)` in the op catalog (`axrange/ops/registry.py:662`). So the *trigger*
is declared; the *machinery that consumes it* is what `docs/WORK_ORDER_twin_incremental_update.md`
deferred. This defect is a concrete argument for building exactly one row of that table, and the
counter-argument is equally concrete: `run-script` is `cleanup_strategy: ESCALATE`,
`idempotency_class: CONFIG` (`registry.py:658-659`) — an **opaque, non-idempotent** shell script.
Re-running one is not obviously safe in general, even though it is obviously safe for the responder
scripts specifically (they are written to be re-runnable: `systemctl stop … || true` then
`systemd-run --unit=…`).

### 3.3 Nothing records what was actually delivered

`ConfigBackend._run_script` (`axrange/config_exec/backend.py:218-252`) writes the file and executes
it, then returns **`{"script_ran": True}`** — matching the op catalog's declared
`outputs={"script_ran": "bool"}` (`registry.py:653`). No hash, no length, no timestamp of the
delivered bytes. `state.py`'s `ops` table (`:268-283`) persists `inputs_resolved_json` and
`outputs_json` and nothing else that could stand in for one. **There is no way, from the state
store alone, to tell a guest that matches its op row from one that does not** — which is precisely
what makes option C a schema change rather than a check.

## 4. Options — framed, not chosen

### Option A — a supported content-only re-apply against a standing deploy

Add a narrow, opt-in path (`te deploy --update --reapply <op-selector>`, or a `--allow-redo` scoped
to `config`-class ops) that re-executes a `Mutated` op whose **only** differing input is
`script_content`, then updates the persisted row. Effectively §9.2's `apps[*].install` row, built
for one op type.

- **For:** it removes the reason the escape hatch exists. It is the smallest possible slice of §9.2
  and it is the one row with a declared `redo_on` already in the catalog. It fixes the class, not the
  instance.
- **Against:** `run-script` is `ESCALATE`/non-idempotent by declaration — the engine would be
  re-running arbitrary operator shell against a live twin on the strength of a content diff. The
  blast radius is whatever the script does. It also needs a story for `provision-app` (the `pkg:`
  sibling), which has the same shape and is not obviously in or out.
- **Version-locked:** `PLANNER` §9.2. Authority: user.

### Option B — record out-of-band drift in the state store

Give the engine somewhere to write "this op's delivered content was changed outside the engine at
time T, to content with hash H", set by whatever tool performs the out-of-band push, and have
`--update` **report** it in the diff (a fifth class alongside unchanged/added/removed/mutated/retry)
rather than silently classifying `Unchanged`.

- **For:** it makes the honest thing (the escape hatch) visible instead of invisible, without
  claiming the engine can re-derive guest state. `PlanDiff` already carries a fifth bucket beyond
  §9.1's four (`retry`, `plandiff.py:96-107`), so a sixth is structurally cheap.
- **Against:** it is bookkeeping the operator must remember to write — an out-of-band push that
  forgets to record itself leaves exactly today's defect. And it costs `SCHEMA_VERSION 3 → 4`, which
  **hard-fails every existing `state.db`** (`state.py:370-378`) and forces a re-init on the cluster
  currently hosting the standing twin. That is a genuinely expensive migration for a bookkeeping
  field.
- **Version-locked:** state-store schema bump. Authority: user.

### Option C — accept the drift, and make `--update` fail loud on it

Leave the escape hatch as-is, but have `--update` **verify** before it classifies: hash the
persisted `script_content`, read the delivered file's hash from the guest, and refuse (or report) on
a mismatch instead of skipping.

- **For:** it never claims `Unchanged` about something it has not checked, which is the actual
  defect. No new operator discipline required.
- **Against:** **it needs a hash the system does not currently record** (§3.3). Deriving it live
  means the diff — today a **pure, offline, no-I/O function** (`plandiff.py`'s module contract:
  *"pure, no I/O"*) — would need a guest agent and a live cluster, which is a significant
  architectural regression for a module whose purity is the reason it is unit-testable at all.
  Recording it at write time instead means adding an output to `OP-APP-030`, i.e. an `OP_CATALOG`
  change; and it only helps ops deployed *after* the change — the 19 guests standing today have no
  recorded hash and never will.
- **Version-locked:** `OP_CATALOG` outputs for `OP-APP-030`. Authority: user.

**Cross-cutting note:** A and C are complementary, not alternatives — C detects, A repairs. B is the
cheap-to-reason-about one that is expensive to migrate. A "do nothing but document it" fourth option
is legitimate and should be considered on equal footing, provided the defect is registered in
`docs/PRODUCT_GAPS.md` rather than living only in this file.

## 5. The general rule this instance illustrates

Worth stating separately, because the specific defect above is a symptom and this is the cause:

> **Emit ports and responder ports derive from the same census and must move together.** Updating
> one while withholding the other silently converts working traffic into ICMP that scores as local
> edges — and it presents as a *persona* problem rather than a *plumbing* one.

That misattribution is the expensive part. Iteration 16's `fan_out_degree_local` of 0.162 looked
like the best result of the loop and was partly ICMP noise; the clean value after repair was 0.290.
Worse, a **correct** model projection of 0.290 had been dismissed at the time as overfitting,
because it disagreed with a measurement taken through broken apparatus. A projection disagreeing
with a measurement is a question about **which of the two is wrong** — and the default assumption
went the wrong way for a whole iteration.

Two operational corollaries follow, independent of which §4 option is chosen:

- **Verify listeners in-guest, not from the launcher.** `systemctl is-active` reports the *unit*,
  and `responder.py`'s own comment records the trap it hides: a per-port thread that died on
  `EADDRINUSE` while the unit stayed active and the op **passed**
  (`twin-configs-cast/corpapcn-twin/scripts/responder-dns-resolver.sh`). Use `ss -lnH -tu`.
- **A withheld half is a change, not a non-change.** "Do not push the responders, so the repo
  mirrors the deployed twin" reads as conservative and was the more invasive option: it changed the
  *relationship* between two things that must move together.

## 6. Out of scope

- **The redo-rules table in general.** Only the `run-script` / `apps[*].install` row is in view, and
  only under option A. `docs/PLANNER.md` §9.2's other rows stay deferred.
- **Sibling-tier reconciliation.** `tap`/`tfc`/`exr` deploys bound to an affected host are a
  separate, already-documented v1 limit (`docs/WORK_ORDER_twin_incremental_update.md` §3.5).
- **Anything that redeploys or destroys the standing twin.** Non-negotiable.
- **Repairing the 19 guests' op rows retroactively.** No option restores a hash that was never
  recorded; whichever path is chosen applies from its landing forward. Say so in the docs rather
  than implying a retroactive fix.
- **The protocol-fidelity question** these responders exist to serve —
  `docs/WORK_ORDER_protocol_faithful_traffic.md`.

## 7. Validation

**Unit/suite (all options):**
1. A regression pinning today's behavior first: an op whose persisted `script_content` differs from
   the guest's delivered text classifies `Unchanged` — the defect, asserted, so the fix is provably
   a change.
2. Whatever new class/refusal the chosen option adds is exercised in both directions (drifted and
   not-drifted), including the no-op re-run (`--update` twice must be clean, per
   `docs/WORK_ORDER_twin_incremental_update.md` §6(c)).
3. **Option A only:** a content-only mutation is re-applied and the persisted row updated; a
   mutation touching **any other** input key is still refused, naming op + field + old→new. Pin this
   hard — the narrowness is the entire safety argument.
4. **Option B only:** the schema migration fails an old `state.db` loud, with a message naming the
   re-init step (`state.py:370-378`'s existing contract).
5. **Option C only:** `plandiff` stays pure — assert the module still performs no I/O, whatever the
   verification step is wired into.

**Gated `dev45` live-verify (A and C):** against a **throwaway** twin, never the standing one —
deploy a two-host twin with a `run-script`, push a changed script out-of-band over QGA, then run
`te deploy --update` and confirm it does the chosen thing (re-applies, or refuses naming the drift)
instead of reporting `Unchanged`. Then destroy the throwaway twin and re-confirm
`te corpapcn-twin-0e4f7d58` is untouched.

## 8. Depends on / sequence

- **Depends on:** nothing unbuilt. `docs/WORK_ORDER_twin_incremental_update.md` (W2) is DONE and
  live-verified; this is a follow-on to it.
- **Blocks (in practice):** `docs/WORK_ORDER_protocol_faithful_traffic.md` §5.2 — that work order's
  rollout onto a standing twin needs exactly the supported path option A would provide, and its
  honest answer today is "redeploy or use the escape hatch."
- **Should be registered:** in `docs/PRODUCT_GAPS.md` regardless of which option is chosen, so the
  defect survives this file being marked DONE.
