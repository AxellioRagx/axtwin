---
description: Run the background subagent pipeline (code → test → review → docs → gated real-capture verify) for a task, checking back only when a decision is needed.
argument-hint: <task description>
---

Run the aximprint implementation pipeline for this task:

**$ARGUMENTS**

You are the **orchestrator**. Drive the specialized subagents in sequence, relay decisions to me, and keep me out of the loop except when a decision is genuinely needed. Full model: `docs/WORKFLOW.md` "Pipeline mode."

## Pipeline
1. **Scope** the task. If it's really a design/scope question, or it would trip the risk gate (reverse a ratified decision in the CHANGELOG or design brief, **ratify a schema** — add version fields / a bump ceremony / compat shims, add a runtime dependency, or shift scope), STOP and ask me first. Note: *changing* an incubating schema is **not** a gate — the event model, feature shapes, plugin contract, and store shapes change freely (DESIGN_BRIEF §0.2, CONVENTIONS §1.2).
2. **code-writer** — implement the change (pass it the task; it consults `docs/LESSONS.md` itself).
3. **tester** — fast subset while iterating, then the full suite, plus `scripts/verify_determinism.py` if the change touched `windowing`/`learners`/`store`/`synth`. On failure, loop back to code-writer with the failure (a sane number of rounds); if it's a real design question, ask me.
4. **code-reviewer** — correctness + optimization/efficiency/duplication from whole-project context, with the layering, determinism, and provenance invariants front of mind. Feed must-fix findings back to code-writer; collect its `LES` candidates.
5. **doc-writer** — CHANGELOG entry (Authority-marked), affected docs, `Q<n>` open/close, `doc-stats --check` clean, and new `LES-NNN` entries (incl. the reviewer's candidates); update SESSION_STATE.
6. **live-verifier** — ONLY if the change needs proof on real data AND I've approved it. It stops and asks before reading any real capture, and before any `axrange` verb.

## Check-back discipline
- Run stages as background subagents. When one returns `DECISION NEEDED: …`, surface it to me with the options, get my answer, then **resume that subagent with `SendMessage`** (context intact) — don't restart it from scratch.
- Gated actions (`git commit`/`push`/`tag`, dependency installs, reading a real capture, any `axrange` verb) always check back with me first. Everything else (reads, tests, `verify_tier.sh`, `verify_determinism.py`, `doc-stats`, `aximprint status`) runs unattended per the allowlist.
- Queue decisions if I'm away; I'll drain them when I check in. Never silently guess past a gate.

## Report
When the pipeline completes (or blocks on me), give a tight status: what changed, the test/determinism/review/doc results, any `LES` entries added, any `Q<n>` touched, and what (if anything) needs my decision or a real-capture approval.
