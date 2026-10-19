---
description: Run the background subagent pipeline (code → test → review → docs → gated live-verify) for a task, checking back only when a decision is needed.
argument-hint: <task description>
---

Run the axRange implementation pipeline for this task:

**$ARGUMENTS**

You are the **orchestrator**. Drive the specialized subagents in sequence, relay decisions to me, and keep me out of the loop except when a decision is genuinely needed. Full model: `docs/WORKFLOW.md` "Pipeline mode."

## Pipeline
1. **Scope** the task. If it's really a design/scope question, or it would trip the risk gate (reverse a ratified decision, change a version-locked schema `IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST`, or shift scope), STOP and ask me first. Read the work order / spec **once, here** — only the sections the task needs (`grep -n '^#'` for the outline, then `Read` with offset/limit).
2. **code-writer** — implement the change from a stage brief (below). It runs the **targeted** tests itself (the affected test files / `-k`) and fixes its own failures — there is no separate test stage in the inner loop.
3. **tester (the gate)** — one full-suite run, `.venv/bin/python -m pytest -q -n auto` (~1 min), plus the Console/plugin suites if they were touched; reports counts. It writes tests only for a runtime surface the code-writer left untested.
4. **code-reviewer — only on a review trigger** (below). Otherwise skip it and say so in the report. Brief: the diff's files + the acceptance criteria.
5. **Fix rounds** — for must-fix findings or a real gate failure, start a **fresh** code-writer with only the must-fix findings (`file:line` + fix) and the file list; then re-run the gate (and re-review only the touched files, if review ran). **At most 2 fix rounds**; if must-fixes remain after round 2, stop and bring me the residual with options.
6. **doc-writer** — the default doc pass: a CHANGELOG entry (Authority-marked) + a `docs/SESSION_STATE.md` **replace** (prior content archived verbatim). Everything else only when actually affected (see `doc-writer.md`); `doc-stats --check` and `doc-budget` clean.
7. **live-verifier** — ONLY if the change needs on-cluster proof AND I've approved the deploy. It stops and asks before any deploy/destroy.

## Review triggers — run code-reviewer only when one applies
(Defined here; `docs/WORKFLOW.md` points at this list.)
- touches a version-locked contract (`IR_SCHEMA`/`PLANNER`/`OP_CATALOG`/`VALIDATOR`/`ADAPTER`/`IMAGE_MANIFEST`) or its implementing code;
- touches the planner/executor, the state store, or the lease/resource locks;
- touches an adapter **write** path (`adapter_proxmox/`, `config_exec/` ops that mutate a cluster), or deploy/destroy;
- touches auth or an API **write** route;
- the diff is **> ~300 changed lines** (`git diff --stat`), or I ask for a review.

## Stage briefs — fresh, tight agents
Each stage is a **fresh** agent started from a short brief you write — not a long-lived agent resumed across stages or rounds (a resumed agent re-reads its whole history on every call). A brief is terse and carries:
- **Task** — one or two sentences; **files to touch / read** (paths, with line ranges or section anchors where known);
- **Relevant `LES-NNN` IDs** (from `python scripts/inject_lessons.py <file>` on the files in play);
- **Acceptance tests** — the test names or commands that prove it;
- **Prior-stage hand-off** — the previous agent's final report, trimmed to what this stage needs.

Stages do **not** re-read whole work orders, `docs/SESSION_STATE.md` or `docs/CHANGELOG.md`: pass section references (`WORK_ORDER_x.md §3 S2`, `CHANGELOG 2026-09-16 "…"`) and let them read those with offset/limit or `grep`. Screenshots: shrink before `Read` — `.venv/bin/python scripts/shrink_image.py <png> [--crop x,y,w,h]` (≤ 1200 px wide); the `read-guard` hook refuses images > ~300 KB or > 1600 px wide.

## Check-back discipline
- Run stages as background subagents. When one returns `DECISION NEEDED: …`, surface it to me with the options, get my answer, then **resume that subagent with `SendMessage`** (context intact). That is the **only** use of `SendMessage`-resume — never to loop review or fix rounds (those get a fresh agent, step 5).
- Gated commands (`axrange deploy`/`destroy`/`image build`, `git commit`/`push`, manual `qm`) always check back with me first. Everything else (reads, tests, `validate`/`plan`/`status`, preflight) runs unattended per the allowlist.
- Queue decisions if I'm away; I'll drain them when I check in. Never silently guess past a gate.

## Report
When the pipeline completes (or blocks on me), give a tight status: what changed, the gate counts, whether review ran (and which trigger) or was skipped, the doc results, any `LES` entries added, and what (if anything) needs my decision or a live-verify approval.
