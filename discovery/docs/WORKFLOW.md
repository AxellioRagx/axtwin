# Workflow — Collaboration Modes

**Purpose:** A working agreement for how design and implementation stay in sync — a small always-on baseline, plus two optional working modes — without the sources of truth drifting apart.

**Read this before:** invoking **chat-bridge mode** (below). The chat-bridge sections apply only when the user chooses to take design to a Claude chat session; ordinary work needs none of it.

---

## The baseline (always on, no machinery)

Day-to-day work is just Claude Code in the repo. Four things hold it together, and they apply no matter which optional mode is in play:

1. **The docs are the source of truth.** `CLAUDE.md` is the orientation summary; `docs/` holds the detail. Code that contradicts a doc is a bug in one of them.
2. **Every decision gets a CHANGELOG Authority marker** (`user` / `implementation (Claude Code)` / `design (chat session)` / `retrofit`), plus `**Supersedes:**` when it reverses an earlier one. This is the record that makes a decision auditable a year later.
3. **`docs/SESSION_STATE.md` is the handoff between sessions.** A committed file: what this session did, what's half-done, the next concrete step, gotchas. Update it at session wrap and commit it with the work. Git history says *what* changed; this says *why* and *what's next*. To resume, read it. Genuinely mid-flight work goes on an ordinary branch, named in that file — **one arc per branch**. When the work turns to an unrelated body of work, branch again rather than extend: a branch that outgrows its name cannot be split back afterwards, because interleaved commits and append-narrative doc files (`CHANGELOG.md`, `CLAUDE.md`) make that split a rewrite of the decision record, not a rebase (2026-08-25 ruling).
4. **The user reviews every diff before it's committed.** That review is the safety catch the whole workflow leans on.

Design authority is **not** gated by surface: Claude Code advances design by default and records it; pause-and-confirm only on the risk triggers in `CLAUDE.md` "Design Document Maintenance" (reversing a ratified decision, changing a version-locked schema, or a scope shift). See `CLAUDE.md` "Collaboration & Design Authority."

## Two optional modes

Neither is mandatory. The user routes each piece of work to whichever fits, and they compose — taking design to chat while a pipeline runs is just both at once.

| Mode | For | Transport / anchor | Detail |
|---|---|---|---|
| **Chat-bridge** | Taking a design question to a Claude **chat** session — greenfield, cross-cutting, or a second opinion — then syncing the result back. | `.tar.gz` export/import + `chat-*` tags | this section |
| **Pipeline** | A background subagent pipeline (code → test → review → docs → gated live-verify) that runs while the user does design and interrupts only on decisions. | `/dev` + `.claude/agents/` + the repo | "Pipeline mode" below |

---

# Chat-bridge mode (optional — when invoked)

Everything from here to "Pipeline mode" applies **only when the user invokes chat-bridge mode.** It bridges two amnesiac collaborators — Claude chat (big-picture design, no repo access) and Claude Code (implementation) — with the user as the routing layer.

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

**Bundle size.** A mature project's `docs/` outgrows a chat context window — an append-only CHANGELOG and a `LES` store reach hundreds of KB each. The export warns past `CLAUDE_EXPORT_MAX_MB` (default 2) and suggests what to drop. Two knobs, both set in `.claude-workflow.env`:

- `CLAUDE_EXPORT_EXCLUDE` — globs left out of the tarball (typically `docs/LESSONS.md docs/PRODUCT_GAPS.md`; never `CLAUDE.md` or `docs/WORKFLOW.md`, which chat cannot work without).
- `CLAUDE_CHANGELOG_ENTRIES` — bundle only the newest N entries.

Anything excluded or truncated is **listed in the opening prompt**, with an instruction to ask for it rather than assume it doesn't exist. A silently missing decision is exactly the drift this workflow exists to prevent.

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

## Chat-bridge one-minute version

Goal: never let Claude chat see a stale picture of the project, and never let Claude chat's output get committed without review. Mechanism: (a) upload current state via tarball, (b) opening prompt tells Claude chat what changed and how to output, (c) Claude chat confirms understanding before proposing changes, (d) output is a single `.tar.gz` with `NEXT_SESSION_PROMPT.md`, (e) user reviews diff before committing, (f) tag the commit so next session can compute the delta. Tags are the anchors; scripts do the bundling; the user catches the failures.


<!-- PIPELINE-MODE-SECTION:BEGIN -->
---

# Pipeline mode — background subagent pipeline (optional)

Pipeline mode runs implementation **in the background** while the user focuses on design, and interrupts only when a decision is needed. It's the second optional mode, independent of chat-bridge.

## The shape

- **Entry point:** `/dev <task>` (`.claude/commands/dev.md`). It orchestrates five specialized subagents — `code-writer` → `tester` → `code-reviewer` → `doc-writer` → (gated) `live-verifier` (`.claude/agents/*.md`).
- **The orchestrator** is the main Claude Code session. Subagents are **children of it** — they run in the background only while it is alive (they are not detached daemons). Keep the orchestrator session running to keep the pipeline moving; for independence from your workstation, run it on a persistent host in `tmux`.
- **Your design/plan session is separate and independent** — a chat, or another Claude Code instance — which you can exit and relaunch at will. The two sessions **coordinate through the repo** (committed docs / CHANGELOG / `docs/LESSONS.md` / `docs/SESSION_STATE.md`), not shared memory.

## What's installed, and where

Pipeline mode splits into **mechanism** (identical in every project) and **policy** (only this repo knows it). The mechanism can live once on the host or inside the repo; the policy always lives in the repo.

| Path | Role | Layout |
|---|---|---|
| `.claude/commands/dev.md` | The `/dev` orchestrator prompt | mechanism |
| `.claude/agents/{code-writer,tester,code-reviewer,doc-writer,live-verifier}.md` | The five stage subagents | mechanism |
| `hooks.py` | Hook dispatcher: lessons index, lessons injection, syntax check, commit guard, drift nudge | mechanism |
| `.claude/settings.json` | Permission allowlist — what runs unattended vs. what checks back | **policy** |
| `.claude-workflow.env` | `CLAUDE_VERIFY_STEPS` — this project's hermetic gates | **policy** |
| `.claude-doc-stats.json` | Counters and size budgets | **policy** |
| `docs/LESSONS.md` | The `LES-NNN` institutional-memory store | **policy** |
| `scripts/inject_lessons.py`, `scripts/doc-stats.py`, `scripts/verify_tier.sh` | Commands the agents and docs call by name | project-local either way |

**Host-global layout** (`install.sh --global`) — the mechanism lives in `~/.claude/` as symlinks into the template clone, plus a hooks block merged into `~/.claude/settings.json`. Every project on the machine gets the pipeline, including ones onboarded later, and a `git pull` in the template clone updates all of them at once. Onboarding detects this and writes only the policy half; the project's `.claude/settings.json` carries permissions and no hooks.

**Standalone layout** — everything sits in the repo, so it travels to any machine and to collaborators without a host install. Force it with `claude-add-pipeline.sh --standalone`.

Global hooks fire in **every** repo on the host, so each one resolves the project at runtime (`CLAUDE_PROJECT_DIR`, else the git worktree of the cwd) and **no-ops entirely in any project that never onboarded** — no `docs/WORKFLOW.md`, no enforcement. Without that guard an unrelated repo that merely has a `docs/CHANGELOG.md` would find its commits blocked for missing Authority markers it never opted into.

Each agent file and `settings.json` ships with `[REPLACE]` markers — the pipeline is only as good as the project-specific commands, invariants, and gates you fill in. In the global layout, edit the agents once, in the template clone, and every project sees it.

## Check back on need basis

A background worker never goes silent-and-stuck. When it hits a **decision** (an ambiguous requirement) or a **gated command**, it returns a structured `DECISION NEEDED: …` to the orchestrator, which surfaces it to you and resumes the worker with your answer (`SendMessage` — context intact, not a restart). Decisions **queue** in the orchestrator; you drain them when you next check in. Everything else — reads, tests, validate/status/dry-run, the read-only preflight scripts — runs unattended per the `.claude/settings.json` allowlist.

The permission file is the contract that makes this work:

- **allow** — anything that cannot change state. Under-filling this is the #1 reason a pipeline stalls: the worker sits on a permission prompt nobody is watching.
- **ask** — every mutating, outward-facing, or money-spending command. These are the check-back points by design.
- **deny** — commands the project's Git Policy forbids outright (inbound sync, remote reconfiguration).

Personal, machine-local additions belong in `.claude/settings.local.json` (gitignored), never in the shared file.

## The lessons store

`docs/LESSONS.md` (`LES-NNN`) is the pipeline's institutional memory: bug-classes, silent-success traps, platform/tool gotchas, ordering invariants, dead-ends, reversal hazards, reuse rules, and positive patterns — captured once and **fed forward**. A hook auto-injects the entries whose `Scope:` matches a file being edited; `code-writer` and `code-reviewer` also consult it directly. `doc-writer` appends a new `LES-NNN` whenever a review finding or a bug fix surfaces durable knowledge. Never renumber, never delete — supersede in place. User/team *preferences* route to the memory store, not LESSONS.

## The hooks and the enforcement model

| Hook | Fires | Does |
|---|---|---|
| `SessionStart` | session open | loads the `LES` index into context |
| `PreToolUse(Edit\|Write)` | before an edit | surfaces the lessons scoped to that file |
| `PostToolUse(Edit\|Write)` | after an edit | cheap syntax check of the edited file |
| `PreToolUse(Bash)` | before any Bash | **hard-blocks `git commit`** on doc-counter drift, an over-budget doc, or a new CHANGELOG entry with no `**Authority:**` line |
| `Stop` | turn end | **advisory** one-shot drift nudge |

Both commit checks are load-bearing **only at commit time**. Doc-counter drift must not be committed, but during a multi-stage pipeline a live counter changes repeatedly while the docs are reconciled once, at the end, by the `doc-writer` stage — so transient drift across turns is expected and must not block. Likewise an entry is often drafted before its Authority marker is settled. Hence the split: `commit-guard` is the hard gate, `Stop` is a non-looping advisory that backs off on re-invocation (`stop_hook_active`) so it can never wedge a turn. Hooks always exit 0 and no-op when their inputs are absent — a hook must never wedge the session.

The Authority check lives on the commit path deliberately. `claude-import.sh` has an equivalent lint, but it only ever sees the chat-bridge path — an optional mode most work never invokes. Every mode commits; that's where the invariant belongs.

## Keeping the registers usable as they grow

The append-only registers are the workflow's memory, and on a long-running project they get big — a CHANGELOG in the hundreds of KB and a `LES` store in the hundreds of entries are both normal. Neither may be rewritten, so bound them instead:

- **`CLAUDE.md` has a budget** (~500 lines). It loads into every session; detail belongs in `docs/`. `doc-stats`' `limits` key enforces it — advisory by default, blocking with `"enforce": true`.
- **The `LES` index is capped** at `CLAUDE_LESSONS_INDEX_LIMIT` (default 60) entries per session load; the rest are summarized as a count and still auto-inject in full on a `Scope:` match.
- **Archive the CHANGELOG by year** into `docs/CHANGELOG_ARCHIVE_<year>.md`, linked from the live file. Archiving isn't rewriting.
- **Chat exports get a slimmed bundle** — see "Session Rhythm" above for `CLAUDE_EXPORT_EXCLUDE` / `CLAUDE_CHANGELOG_ENTRIES`, and note that whatever is dropped is disclosed to chat rather than silently missing.

## Verification tiers

The pipeline clears three tiers before the human-gated live verification:

1. **Unit** — the fast subset in the inner loop, the full suite at the gate. Hermetic; no shared environment.
2. **Hermetic tier** — `scripts/verify_tier.sh`: the project's non-mutating gates (lint, validate, dry-run plan, invariant checks) in one shot. Configure them in `.claude-workflow.env` via `CLAUDE_VERIFY_STEPS`.
3. **Live** — the real environment, **human-gated**, run by `live-verifier` only when a change needs on-environment proof.

Most regressions die in the first two tiers, off the shared environment. That is the point of the split.

## Work orders

A `docs/WORK_ORDER_<topic>.md` (see `docs/CONVENTIONS.md` §3) is the unit of work the pipeline consumes best: goal, phased build, explicit out-of-scope boundaries, and the validation gates that mean "done". Hand one to `/dev` rather than a one-line task when the change spans several files or sessions. Mark it DONE in place when its run lands — don't delete it.

## Setup checklist

1. Install the assets (`claude-init-workflow.sh --with-pipeline`, `--all`, or `claude-add-pipeline.sh` on an existing project).
2. Fill every `[REPLACE]` in `.claude/agents/*.md` — test commands, project invariants, the version-locked specs behind the risk gate, and the live runbook (or delete `live-verifier.md` if there's no live environment).
3. Fill the `[REPLACE]` allow/ask entries in `.claude/settings.json` with the project's real read-only and mutating commands.
4. Define `CLAUDE_VERIFY_STEPS` in `.claude-workflow.env`.
5. Optional: `python3 scripts/doc-stats.py --init`, then configure the counters your docs actually quote.
6. Seed `docs/LESSONS.md` with the gotchas you already know — the store is worth most when it starts non-empty.
7. Open a fresh session and confirm the `SessionStart` hook reports the lessons index. If it's silent, the hooks aren't wired.

## Failure modes

- **The pipeline stalls with no decision queued.** A command it needs is neither in `allow` nor `ask` — the worker is waiting on a prompt. Fix the allowlist, don't loosen the gates.
- **A hook wedges the turn.** Only `commit-guard` may block. If a `Stop` nudge repeats, its `stop_hook_active` back-off is missing.
- **Lessons stop being injected.** A `Scope:` that's too broad matches everything (and gets ignored); one that's too narrow matches nothing. Scope to a path fragment, a filename, or a real directory name.
- **The reviewer only sees the diff.** It's prompted for whole-project context — duplication and reuse findings are the reason it's the expensive model in the chain.
- **The orchestrator dies and the pipeline dies with it.** Subagents are children of that session. Run it in `tmux` on a persistent host if it must outlive your workstation.

## Pipeline-mode one-minute version

`/dev <task>` turns the main session into an orchestrator that drives code-writer → tester → code-reviewer → doc-writer → (gated) live-verifier as background subagents. The `.claude/settings.json` allowlist decides what runs unattended; everything mutating returns `DECISION NEEDED:` and queues for you. `docs/LESSONS.md` feeds forward what the project already learned — injected by hook on every edit, appended by the doc-writer on every finding. Three verification tiers (unit → hermetic → gated live) keep regressions off the shared environment, and doc-counter drift is hard-blocked exactly once, at `git commit`.
<!-- PIPELINE-MODE-SECTION:END -->
