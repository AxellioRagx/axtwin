# Workflow — Collaboration Modes

**Purpose:** A working agreement for how design and implementation stay in sync — a small always-on baseline, plus two optional working modes — without the sources of truth drifting apart.

**Read this before:** invoking **chat-bridge mode** (below). The chat-bridge sections apply only when the user chooses to take design to a Claude chat session; day-to-day work needs none of it.

---

## The baseline (always on, no machinery)

Day-to-day work is just Claude Code in the repo. Four things hold it together, and they apply no matter which optional mode is in play:

1. **The docs are the source of truth.** `CLAUDE.md` is the orientation summary; `docs/` holds the detail. Code that contradicts a doc is a bug in one of them.
2. **Every decision gets a CHANGELOG Authority marker** (`user` / `implementation (Claude Code)` / `design (chat session)` / `retrofit`), plus `**Supersedes:**` when it reverses an earlier one. This is the record that makes a decision auditable a year later.
3. **`docs/SESSION_STATE.md` is the handoff between sessions.** A committed file: branch/HEAD, what's in flight, the ordered next steps, open decisions, the gotchas that bite now. At session wrap **replace** it — move the prior content verbatim to `docs/handoffs/<YYYY-MM>.md` (newest first), carry forward only what is still true and unresolved, stay under its 8 KB budget — and commit it with the work. Git history says *what* changed; this says *why* and *what's next*. To resume, read it. Genuinely mid-flight work goes on an ordinary branch, named in that file. (Solo mode — `wip/<base-sha>/<label>` checkpoint branches and `claude-checkpoint.sh`/`claude-restore.sh` — was retired 2026-08-04; the committed `SESSION_STATE.md` is what replaced it. See the CHANGELOG.)
4. **The user reviews every diff before it's committed.** That review is the safety catch the whole workflow leans on.

Design authority is **not** gated by surface: Claude Code advances design by default and records it; pause-and-confirm only on the risk triggers in `CLAUDE.md` "Design Document Maintenance" (reversing a ratified decision, changing a version-locked schema, or a scope shift). See `CLAUDE.md` "Collaboration & Design Authority."

## Two optional modes

Neither is mandatory. The user routes each piece of work to whichever fits (CHANGELOG 2026-06-11; the earlier "design must originate in chat" default is retired), and they compose — taking design to chat while a pipeline runs is just both at once.

| Mode | For | Transport / anchor | Detail |
|---|---|---|---|
| **Chat-bridge** | Taking a design question to a Claude **chat** session — greenfield, cross-cutting, or a second opinion — then syncing the result back. | `.tar.gz` export/import + `chat-*` tags | this section |
| **Pipeline** | A background subagent pipeline (code → test → review → docs → gated live-verify) that runs while the user does design and interrupts only on decisions. | `/dev` + `.claude/agents/` + the repo | "Pipeline mode" below |

---

# Chat-bridge mode (optional — when invoked)

Everything from here to "Pipeline mode" applies **only when the user invokes chat-bridge mode.** It bridges two amnesiac collaborators — Claude chat (big-picture design, no repo access) and Claude Code (implementation) — with the user as the routing layer.

---

## The Problem This Solves

Two AI collaborators are editing the same design docs:

- **Claude (in chat)** — good for big-picture design, trade-off discussions, cross-cutting decisions. No persistent memory between sessions. Can't see your repo.
- **Claude Code (on your local machine)** — good for implementation, parameter tuning, small doc corrections. Runs against your actual files. No memory of what was discussed in chat.

Neither of them, on their own, knows what the other changed. If you don't actively close that gap, the docs drift. The user sits between them as the memory and routing layer. This workflow's job is to make that routing mechanical, so it isn't something the user has to think about each time.

---

## Output Format Rules (for Claude chat)

When Claude chat produces an end-of-session deliverable, the following rules are non-negotiable. `claude-import.sh` enforces several of them and will reject non-conforming output.

### File format

- **Output is a single `.tar.gz` archive.** Not a zip. Not a zip wrapping a tarball. Not a folder of loose files. One `.tar.gz`.
- Files inside the archive sit either at the top level, or under a single directory named after the project. Both layouts are accepted by `claude-import.sh`.
- **No binary artifacts.** Docs only. No compiled files, no images unless the project's docs actually reference images.

### Required companion file

Every output tarball must include a file named `NEXT_SESSION_PROMPT.md` at the top level. This is the paste-ready opening message for the next Claude Code session. It must contain:

- A one-paragraph summary of what was decided in the chat session.
- A list of files that changed, with a one-line reason per file.
- What implementation work Claude Code is expected to do next.
- Explicit "do not change X" notes where relevant.
- A pointer to read the updated `CLAUDE.md` and the new CHANGELOG entries before starting.

`claude-import.sh` extracts this file to `.claude-sync/NEXT_SESSION_PROMPT.md` (which is gitignored) and prints its contents after a successful import.

### CHANGELOG entry requirements

Every new CHANGELOG entry added during the session must include an `**Authority:**` line. Valid values:

| Marker | Meaning |
|---|---|
| `design (chat session)` | Decision made during a Claude chat session |
| `implementation (Claude Code)` | Discovered/decided during Claude Code implementation |
| `user` | User-authored decision (outside any AI session) |
| `retrofit` | Retrospective entry for a decision made before the workflow was adopted |

If a new entry reverses or replaces an earlier one, it must include a `**Supersedes:**` line linking to the earlier entry.

### What must be preserved

Claude chat must not silently overwrite changes authored by Claude Code since the last `chat-*` tag. The delta in the opening prompt lists those changes. If Claude chat needs to override one, it must do so explicitly:

> *"I am changing X that Claude Code set to Y, because [rationale]."*

Silent overwrites are the single most common failure mode. The user's diff review step exists to catch them.

---

## The Four Inputs Every Chat Session Needs

Whenever a new chat session starts, the opening message (generated by `claude-export.sh`) provides:

1. **Current state of the docs** — the latest files after all Claude Code edits, in the attached tarball.
2. **Diff since last chat session** — what Claude Code (or the user manually) changed since the last `chat-*` tag.
3. **Summary of last chat's conclusions** — one paragraph the user fills in.
4. **What to discuss this session** — the user fills in.

Without all four, Claude chat is working blind and will inevitably produce something that contradicts or overwrites recent changes.

---

## Session Rhythm

### Before a chat session

In the repo:

```bash
git status   # should be clean
./scripts/claude-export.sh
```

The script produces:

- **A tarball** at `/tmp/<project>-exports/<project>-<timestamp>-<hash>.tar.gz`
- **An opening prompt file** at the same location: `<project>-<timestamp>-OPENING_PROMPT.md`

The opening prompt is a single paste-ready message containing the delta, the protocol reminder, and blank lines for the user to fill in "last session" and "today's topic." It embeds the output format rules so that even if the user forgets to reference `docs/WORKFLOW.md`, Claude chat sees the rules.

### Start of chat

Upload the tarball. Paste the opening prompt. Fill in the two blanks before sending.

### During chat

Claude chat must:

- Confirm understanding of the current state before proposing changes.
- Preserve Claude-Code-authored changes unless explicitly asked to override.
- Flag any override with rationale.
- Surface inconsistencies rather than paper over them.
- Add `**Authority:**` markers on every new CHANGELOG entry.

If Claude chat starts making changes without confirming the state first, push back: *"Walk me through your understanding of the current state first. Don't propose changes yet."*

### End of chat

Claude chat generates the output `.tar.gz` including `NEXT_SESSION_PROMPT.md`. Download it.

### After the chat, back in the repo

```bash
./scripts/claude-import.sh ~/Downloads/<project>-updated.tar.gz

# Review — specifically check that Claude Code's recent changes were preserved.
git diff

# If the diff looks wrong, DON'T commit. Go back to chat and push back.

# If it looks good:
git add -A
git commit -m "design: <description>"
git tag chat-$(date +%Y%m%d)
```

After the import, `claude-import.sh` prints the contents of `NEXT_SESSION_PROMPT.md` so the user can see what Claude Code should do next. That file also sits at `.claude-sync/NEXT_SESSION_PROMPT.md` — the user pastes it into the next Claude Code session verbatim.

### Pushing to remote (optional, project-dependent)

Projects using the local-only git policy: remote push is the user's manual responsibility.

Projects using standard feature-branch workflow: push the branch, open a PR, merge. `git push --tags` to preserve the chat anchors.

---

## The Tag Convention

Git tags are the memory anchors between sessions.

| Tag pattern | Meaning | Created |
|---|---|---|
| `chat-YYYYMMDD` | End of a design chat session | Manually after commit |
| `chat-YYYYMMDD-<topic>` | Topic-specific if multiple sessions per day | Manually |
| `chat-YYYYMMDD-init` | Initial workflow setup | By `claude-init-workflow.sh` |
| `chat-YYYYMMDD-adopt` | Retrofit adoption baseline | By `claude-adopt-workflow.sh` |

To find the delta since last chat:

```bash
LAST_CHAT=$(git tag -l 'chat-*' | sort -V | tail -1)
git log $LAST_CHAT..HEAD --oneline -- CLAUDE.md docs/
```

`claude-export.sh` does this automatically.

---

## Authority Markers in CHANGELOG

Every `docs/CHANGELOG.md` entry includes an **Authority** line. This tells future Claude chat sessions which decisions are settled implementation facts (preserve them) versus design proposals (open for revision).

```markdown
## 2026-04-25 — Some decision

**Authority:** implementation (Claude Code)

**What changed:**
- Concrete statement.

**Why:**
- Rationale.

**Affected docs:**
- `path/to/file.md` — section

**Supersedes:** (optional; link to earlier entry this replaces)
```

See `docs/CHANGELOG.md` and the template `CHANGELOG_TEMPLATE.md` for the full schema.

---

## Recovery Protocol — When Claude Chat Seems Unaware of the Workflow

If a chat session starts and Claude chat isn't following the rules (no state confirmation, proposing changes immediately, producing a zip instead of tarball, missing Authority markers), use this recovery prompt:

> You are operating under the workflow defined in `docs/WORKFLOW.md`, which is in the attached tarball. Read that file before proposing any doc changes. Before continuing, confirm in your own words:
>
> 1. The output format rules (single `.tar.gz`, no zip, `NEXT_SESSION_PROMPT.md` required at top level).
> 2. The CHANGELOG Authority marker requirement.
> 3. The rule about not silently overwriting Claude-Code-authored changes.
>
> Then restate what you understand the current project state to be, based on the tarball contents. Only after that, we will continue.

This is also the prompt to use if you're mid-session and realize Claude chat didn't read the playbook at the start. Paste it, wait for acknowledgement, then continue.

---

## Failure Modes and How to Catch Them

### Failure mode 1: Silent overwrite

Claude chat regenerates a file from scratch and loses a small change Claude Code made.

**Detection:** `git diff` after import. Specifically diff against what Claude Code last touched:

```bash
LAST_CHAT=$(git tag -l 'chat-*' | sort -V | tail -1)
git log $LAST_CHAT..HEAD --stat -- CLAUDE.md docs/
# Cross-reference: are those changes still present after import?
```

**Response:** push back in chat. *"You overwrote [specific change] that Claude Code added on [date]. Restore it."*

### Failure mode 2: Contradictory decisions across sessions

Session 1 decides X. Session 3 decides X' without realizing. Docs now have both, or a confused hybrid.

**Detection:** read the CHANGELOG at the start of each session. If two entries about the same topic reach different conclusions, the more recent one wins — but it should explicitly supersede the older one.

**Response:** add a `**Supersedes:**` line to the newer entry.

### Failure mode 3: Drift between CLAUDE.md and docs/

The quick orientation doc (`CLAUDE.md`) says one thing; a detailed doc (`docs/ARCHITECTURE.md` or similar) says another.

**Detection:** every few sessions, ask Claude chat: *"Read CLAUDE.md and docs/. Flag any inconsistencies."*

**Response:** reconcile in a follow-up commit. `CLAUDE.md` should always be a summary of what's in `docs/`, never a contradiction.

### Failure mode 4: Missing `NEXT_SESSION_PROMPT.md`

Claude chat produces a tarball without the companion prompt file. `claude-import.sh` warns about this but does not refuse the import.

**Response:** after import, ask Claude chat to produce the prompt file separately. Save it to `.claude-sync/NEXT_SESSION_PROMPT.md` manually.

### Failure mode 5: Zip output

Claude chat produces a `.zip` (or a `.zip` wrapping a `.tar.gz`). `claude-import.sh` refuses it.

**Response:** paste the recovery prompt above. Ask Claude chat to re-output as a single `.tar.gz`.

---

## When to invoke chat mode

Chat-bridge mode is **optional** — most work never needs it. Invoke it when:

- You want a big-picture or cross-cutting **design** discussion with a fresh perspective (greenfield architecture, a hard trade-off, a second opinion).
- The design will touch multiple docs and you'd rather think it through before writing.

Skip it (work directly in Claude Code, with or without the pipeline) when:

- The work is implementation, a small design refinement, or a scoped doc change — Claude Code advances design directly and records it.
- You just want a quick question answered.

There is no penalty for never invoking chat mode; it's a tool, not a gate.

---

## Onboarding Paths — Which Script to Use

The template repo ships three entry points:

| Script | For | When to use |
|---|---|---|
| `claude-init-workflow.sh` | Greenfield projects | New project, no prior docs. `--with-pipeline` adds the background pipeline |
| `claude-adopt-workflow.sh` | Existing projects with no workflow | Has code and maybe docs, never used this workflow. Same mode flags |
| `claude-add-pipeline.sh` | A project already on the workflow | Add Pipeline mode (subagents, hooks, lessons store) additively |
| `UPGRADE_INSTRUCTIONS.md` | Existing projects on the old workflow | Has `scripts/MERGE_PLAYBOOK.md` and an existing CHANGELOG with Authority markers |

See the template repo's top-level `README.md` for the decision tree.

---

## Quick Command Reference

```bash
# Before a chat
./scripts/claude-export.sh                    # Bundle + opening prompt

# After a chat
./scripts/claude-import.sh <tarball>          # Apply + diff + extract NEXT_SESSION_PROMPT
git diff                                      # Review
git add -A && git commit -m "design: ..."
git tag chat-$(date +%Y%m%d)

# Anytime — what's changed since last chat
LAST_CHAT=$(git tag -l 'chat-*' | sort -V | tail -1)
git log $LAST_CHAT..HEAD --oneline
```

---

## The One-Minute Version

Goal: never let Claude chat see a stale picture of the project, and never let Claude chat's output get committed without review. Mechanism: (a) upload current state via tarball, (b) opening prompt tells Claude chat what changed and how to output, (c) Claude chat confirms understanding before proposing changes, (d) output is a single `.tar.gz` with `NEXT_SESSION_PROMPT.md`, (e) user reviews diff before committing, (f) tag the commit so next session can compute the delta. Tags are the anchors; scripts do the bundling; the user catches the failures.
<!-- PIPELINE-MODE-SECTION:BEGIN -->
---

# Pipeline mode — background subagent pipeline (optional)

Pipeline mode runs implementation **in the background** while the user focuses on design, and interrupts only when a decision is needed. It's the second optional mode, independent of chat-bridge.

## The shape

- **Entry point:** `/dev <task>` (`.claude/commands/dev.md`). It orchestrates five specialized subagents — `code-writer` → `tester` → `code-reviewer` → `doc-writer` → (gated) `live-verifier` (`.claude/agents/*.md`).
- **Fewer stages by default (2026-09-19).** The `code-writer` runs the targeted tests itself and fixes its own failures (no separate inner-loop test stage). The `tester` is the **gate only**: one parallel full-suite run (`.venv/bin/python -m pytest -q -n auto`, ~1 min vs ~4.5 min serial) plus the Console/plugin suites when touched; it writes tests only for a surface the code-writer left untested. The `code-reviewer` runs **only on a review trigger** — the list is defined once, in `.claude/commands/dev.md` "Review triggers" (version-locked contracts, planner/executor/state/locks, adapter write paths, deploy/destroy, auth/API write routes, a > ~300-line diff, or on request); otherwise it is skipped and the report says so.
- **The orchestrator** is the main Claude Code session. Subagents are **children of it** — they run in the background only while it is alive (they are not detached daemons). Keep the orchestrator session running to keep the pipeline moving; for independence from your workstation, run it on a persistent host (e.g. a dedicated worker VM) in `tmux`.
- **Your design/plan session is separate and independent** — a chat, or another Claude Code instance — which you can exit and relaunch at will. The two sessions **coordinate through the repo** (committed docs / CHANGELOG / `docs/LESSONS.md` + the `wip/*` handoff), not shared memory. Taking design to chat while the pipeline runs is just chat-bridge mode composed with pipeline mode.

## Check back on need basis

A background worker never goes silent-and-stuck. When it hits a **decision** (an ambiguous requirement) or a **gated command** (`axrange deploy`/`destroy`/`image build`, `git commit`/`push`, a manual `qm` write), it returns a structured `DECISION NEEDED: …` to the orchestrator, which surfaces it to you and resumes the worker with your answer. Decisions **queue** in the orchestrator; you drain them when you next check in. Everything else — reads, `pytest`, `validate`/`plan`/`status`, the read-only preflight scripts — runs unattended per the `.claude/settings.json` allowlist.

## The lessons store

`docs/LESSONS.md` (`LES-NNN`) is the pipeline's institutional memory: bug-classes, silent-success traps, platform/tool gotchas, ordering invariants, dead-ends, reversal hazards, reuse rules, and positive patterns — captured once and **fed forward**. A hook auto-injects the entries whose `Scope:` matches a file being edited — in **compact form** (`LES-NNN — title` + the `Rule:`'s first sentence) and **once per session per agent** (a repeat edit only lists the ids already surfaced; state lives in `~/.cache/claude-lessons-seen/`, fails open, `CLAUDE_LESSONS_DEDUPE=0` disables it). The full entry is `python scripts/inject_lessons.py --full LES-NNN …`. SessionStart loads the 25 newest titles (`CLAUDE_LESSONS_INDEX_LIMIT`). `code-writer` and `code-reviewer` also grep it directly. `doc-writer` appends a new `LES-NNN` only for a genuinely new class of durable knowledge — a recurrence of a known class gets a one-line pointer/example on the existing entry instead. User/team preferences route to the memory store, not LESSONS.

## Stage briefs — fresh, tight agents (2026-09-19)

Context is the pipeline's dominant cost: a resumed agent re-reads its whole history on every model
call, and pipeline agents measured 230k–450k tokens of context per call before this rule. So:

- **One fresh agent per stage, from a brief.** The orchestrator reads the work order/spec once, at
  scoping, and writes each stage a short brief: the task; the files to touch (with line ranges or
  section anchors); the relevant `LES-NNN` IDs; the acceptance tests; the prior stage's hand-off,
  trimmed to what this stage needs.
- **Stages don't re-read the big docs.** No whole-file reads of work orders, `SESSION_STATE.md` or
  `CHANGELOG.md` — the brief carries section references; read those with offset/limit or `grep`.
  Trust the brief; ask (`DECISION NEEDED:`) if it is genuinely insufficient.
- **A fresh agent per fix round.** After a review, a new code-writer gets only the must-fix
  findings (`file:line` + fix) and the file list. `SendMessage`-resume is reserved for answering a
  `DECISION NEEDED:` — never for looping review rounds.
- **At most 2 fix rounds**, then the orchestrator escalates the residual to the user with options.
- **Screenshots** are shrunk before `Read`: `.venv/bin/python scripts/shrink_image.py <png> [--max-width 1200] [--crop x,y,w,h]` writes `<name>.small.png`. The `read-guard` hook (`scripts/hooks.py`, PreToolUse `Read`, wired in `.claude/settings.json`) **denies** reading an image > ~300 KB or > 1600 px wide and names that command; `CLAUDE_ALLOW_BIG_IMAGES=1` overrides. (Console captures are ad-hoc headless-Chromium runs from scratch dirs, so there is no shared capture script to default smaller — shrink the output.)

## The default doc pass — lighter (2026-09-19)

Every slice gets **a CHANGELOG entry (Authority-marked) + a `SESSION_STATE.md` replace**. Everything
else only when actually affected: a spec/design doc only when behavior or a contract changed;
`PRODUCT_GAPS.md` only for a newly discovered or resolved gap; `LESSONS.md` only for a genuinely new
class of defect or knowledge not covered by an existing `LES` (prefer a one-line pointer/example on
the existing entry); `CLAUDE.md` only for orientation changes. **A work order is the active spec**
(scope, slices, acceptance criteria, open decisions; target ≤ 30 KB): when a slice is built, update
its status line and move its "built as" / defect-trail / round record **verbatim** to
`docs/archive/work_orders/<WORK_ORDER_name>.built.md`, leaving a pointer line per moved section and
any still-binding ruling, briefly (`docs/CONVENTIONS.md` §3). The
enforcement stays: `doc-stats --check` clean, `doc-budget` clean, and the commit guard.

## Verification tiers

The pipeline clears three tiers before the human-gated live-verify: **unit** (targeted tests / `pytest -m "not slow"` in the inner loop; the full suite `pytest -q -n auto` at the gate) → **cluster-free preflight** (`scripts/verify_tier.sh`: validate + `plan --dry-run` + `sdn_preflight` + `verify_resource_locks`) → **live** (dev45, gated). Most regressions die in the first two tiers, off the shared cluster.
<!-- PIPELINE-MODE-SECTION:END -->

