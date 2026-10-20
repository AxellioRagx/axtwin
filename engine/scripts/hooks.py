#!/usr/bin/env python3
"""Claude Code hook dispatcher for the background pipeline (Pipeline mode).

Wired from `.claude/settings.json`. Reads the hook JSON on stdin, does one
best-effort job, and ALWAYS exits 0 — a hook must never wedge the session.

  session-start : load the docs/LESSONS.md index into context
  inject        : PreToolUse(Edit|Write) — surface lessons scoped to the edited file
                  (compact form, deduped per session+agent: each lesson once)
  syntax        : PostToolUse(Edit|Write) — cheap syntax check of the edited file
  read-guard    : PreToolUse(Read) — DENY reading a big image (> ~300 KB or > 1600 px
                  wide) and point at scripts/shrink_image.py; CLAUDE_ALLOW_BIG_IMAGES=1
                  overrides; fails open
  commit-guard  : PreToolUse(Bash) — HARD-BLOCK a `git commit` when doc counters
                  drift, or when a new CHANGELOG entry has no **Authority:** line
  stop          : Stop — ADVISORY one-shot nudges: doc-counter drift, and a handoff
                  (docs/SESSION_STATE.md) that has fallen behind (never blocks/loops)

Enforcement model: both commit-guard checks are load-bearing at COMMIT time and
nowhere else. Doc-counter drift must not be committed, but during a multi-stage
pipeline a live counter changes repeatedly while the docs are reconciled once, at
the end, by the doc-writer stage — so transient drift across turns is expected and
must not block. Likewise a CHANGELOG entry is often drafted before its Authority
marker is settled. Hence `commit-guard` is the hard gate; `stop` is a non-looping
advisory that backs off on re-invocation (`stop_hook_active`) so it can never
wedge the turn.

The Authority check lives here because every working mode commits — the equivalent
lint in `claude-import.sh` only ever sees the chat-bridge path, which is now the
least-used one.

Every stage degrades to a silent no-op when its helper script or input file is
absent, so a project can adopt the pieces it wants.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

def _resolve_repo() -> Path:
    """The project this invocation is about.

    The scripts run in two layouts and must work in both:
      * host-global — one copy lives in the workflow template clone and serves every
        project on the machine, so the project is whatever Claude Code is working on;
      * per-project — a copy sits in the project's own scripts/.

    Resolution order: CLAUDE_PROJECT_DIR (set by Claude Code for hooks) → this file's
    own project directory (scripts/..) when it lies inside the git worktree containing
    the cwd → that git worktree → this file's own project directory.

    The per-project check comes BEFORE the git worktree on purpose: in a monorepo the
    worktree root is a level above the package that owns this copy of the script, and
    it carries no `.claude-doc-stats.json` / `docs/LESSONS.md` of its own, so resolving
    to it makes every pipeline script a silent no-op (doc-stats exits 0 with no config,
    inject_lessons finds no entries). A script copy inside the worktree is by definition
    the per-project layout, and its own package is the project.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    here = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            top = Path(out.stdout.strip()).resolve()
            if here == top or top in here.parents:
                return here
            return top
    except Exception:
        pass
    return here


REPO = _resolve_repo()
PY = sys.executable  # the interpreter running this hook (the venv python, per settings.json)

CHANGELOG = "docs/CHANGELOG.md"
# How many LES entries the SessionStart index lists before summarizing the rest.
# 0 = no limit. Override per project in .claude-workflow.env or the environment.
LESSONS_INDEX_LIMIT = os.environ.get("CLAUDE_LESSONS_INDEX_LIMIT", "25")

SESSION_STATE = "docs/SESSION_STATE.md"
# Commits allowed to land before the Stop hook mentions the handoff. Generous on
# purpose: this is a nudge at the end of a working stretch, not a per-turn nag.
# 0 disables it. Override per project in .claude-workflow.env or the environment.
try:
    HANDOFF_STALE_COMMITS = int(os.environ.get("CLAUDE_HANDOFF_STALE_COMMITS", "10"))
except ValueError:
    HANDOFF_STALE_COMMITS = 10

# Lines of docs/SESSION_STATE.md the SessionStart hook loads into context. It rides
# along on EVERY session, so it needs a ceiling the way the LES index does — a
# rambling handoff would otherwise tax every session forever. 0 = no limit.
try:
    HANDOFF_CONTEXT_LINES = int(os.environ.get("CLAUDE_HANDOFF_CONTEXT_LINES", "120"))
except ValueError:
    HANDOFF_CONTEXT_LINES = 120

# PostToolUse syntax checks, by file extension. Each value is an argv template:
# "{py}" expands to this interpreter, "{f}" to the edited file (repo-relative).
_SYNTAX_CHECKS = {
    ".py": ["{py}", "-m", "py_compile", "{f}"],
    ".sh": ["bash", "-n", "{f}"],
    ".bash": ["bash", "-n", "{f}"],
    ".json": ["{py}", "-m", "json.tool", "{f}"],
}


def _is_workflow_project() -> bool:
    """Is the current project actually onboarded onto this workflow?

    Load-bearing in the host-global layout, where these hooks fire in EVERY repo on
    the machine. Without this check, an unrelated project that happens to have a
    `docs/CHANGELOG.md` would find its commits blocked for missing Authority
    markers it never opted into. Onboarding always writes `docs/WORKFLOW.md`; the
    env file covers a project that customized its docs layout.
    """
    return (REPO / "docs" / "WORKFLOW.md").is_file() or (REPO / ".claude-workflow.env").is_file()


def _stdin_json() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _emit(event: str, context: str) -> None:
    if context:
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": event, "additionalContext": context}
        }))


def _script(name: str) -> Path | None:
    """Find a helper script: the project's own copy wins, then the one sitting
    beside this file (the host-global layout)."""
    for candidate in (REPO / "scripts" / name, Path(__file__).resolve().parent / name):
        if candidate.is_file():
            return candidate
    return None


def _run_script(name: str, *args: str):
    """Run one of this project's helper scripts, or return None if it isn't installed."""
    path = _script(name)
    if path is None:
        return None
    try:
        return subprocess.run([PY, str(path), *args], cwd=REPO,
                              capture_output=True, text=True, timeout=120)
    except Exception:
        return None


def _edited_path(data: dict) -> str:
    fp = (data.get("tool_input") or {}).get("file_path") or ""
    if not fp:
        return ""
    try:
        return str(Path(fp).resolve().relative_to(REPO))
    except Exception:
        return fp


def session_start() -> None:
    notes: list[str] = []

    r = _run_script("inject_lessons.py", "--index", "--limit", LESSONS_INDEX_LIMIT)
    if r and r.stdout.strip():
        notes.append(
            "Institutional memory is active — consult docs/LESSONS.md before editing.\n"
            + r.stdout.strip()
        )

    handoff = _handoff_context()
    if handoff:
        notes.append(handoff)

    if notes:
        _emit("SessionStart", "\n\n".join(notes))


def _unfilled_template(text: str) -> bool:
    """Is this still the shipped template, with none of its blanks filled in?

    The template is almost entirely HTML comments and empty bullets. Strip those and
    the headings, and a genuine handoff still has prose left; an untouched one has
    nothing. Injecting the empty scaffold into every session would be pure noise.
    """
    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Scaffold: an empty bullet ("-", "1."), an unfilled field ("**Branch:**"), or
        # a bare label with nothing after its colon ("- Working / verified:").
        # The marker must be followed by space or end of line, or "*" would eat one
        # asterisk of a "**Bold:**" field and leave it unrecognizable.
        stripped = re.sub(r"^([-*]|\d+\.)(\s+|$)", "", line)
        stripped = re.sub(r"\*\*[^*]+:\*\*", "", stripped)
        stripped = stripped.replace("·", "").strip()
        if not stripped or stripped.endswith(":"):
            continue
        return False
    return True


def _handoff_context() -> str:
    """The handoff, for the top of a new session.

    Resuming is reading this file — but the failure mode of *reading* is forgetting to,
    which a command can't fix (you'd have to remember to run it). So it rides in on
    SessionStart, capped, with the reminder that it is a claim rather than a fact.
    """
    path = REPO / SESSION_STATE
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.strip() or _unfilled_template(text):
        return ""

    lines = text.splitlines()
    truncated = 0
    if 0 < HANDOFF_CONTEXT_LINES < len(lines):
        truncated = len(lines) - HANDOFF_CONTEXT_LINES
        lines = lines[:HANDOFF_CONTEXT_LINES]

    header = (
        f"Resuming: `{SESSION_STATE}` is the previous session's handoff. It is what the "
        f"last session BELIEVED, not verified fact — check it against the repo before "
        f"acting on it, or run `/catchup` to have that done properly."
    )
    body = "\n".join(lines)
    if truncated:
        body += (
            f"\n\n… {truncated} more line(s) — read {SESSION_STATE} in full, or raise "
            f"CLAUDE_HANDOFF_CONTEXT_LINES."
        )
    return f"{header}\n\n{body}"


# Where the per-session lesson-dedupe state lives. One small JSON file per
# (session, agent); stale files are pruned opportunistically.
_SEEN_MAX_AGE_S = 3 * 24 * 3600


def _seen_file(data: dict) -> str | None:
    """Per-(session, agent) dedupe state file for `inject`, or None to disable.

    Keyed on session_id + agent_id + transcript_path so a subagent (own agent_id
    and/or own transcript) never inherits its parent's "already seen" set — each
    agent gets each lesson once. No session_id ⇒ no dedupe (old behavior).
    Set CLAUDE_LESSONS_DEDUPE=0 to turn dedupe off.
    """
    if os.environ.get("CLAUDE_LESSONS_DEDUPE", "1") == "0":
        return None
    sid = str(data.get("session_id") or "")
    if not sid:
        return None
    import hashlib
    import tempfile
    key = "|".join([sid, str(data.get("agent_id") or ""), str(data.get("transcript_path") or "")])
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-lessons-seen"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir()) / "claude-lessons-seen"
        base.mkdir(parents=True, exist_ok=True)
    try:  # opportunistic prune; never load-bearing
        import time
        cutoff = time.time() - _SEEN_MAX_AGE_S
        for old in base.glob("*.json"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
    except Exception:
        pass
    return str(base / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json"))


def inject() -> None:
    data = _stdin_json()
    rel = _edited_path(data)
    if not rel:
        return
    try:
        seen = _seen_file(data)
    except Exception:
        seen = None  # fail open: un-deduped injection
    args = [rel] + (["--seen-file", seen] if seen else [])
    r = _run_script("inject_lessons.py", *args)
    if seen and (r is None or r.returncode != 0):
        r = _run_script("inject_lessons.py", rel)  # fail open
    if r and r.stdout.strip():
        _emit("PreToolUse", r.stdout.strip())


def syntax() -> None:
    """Cheap post-edit syntax check — catches a broken file before the next stage
    reads it. Unknown extensions are skipped."""
    rel = _edited_path(_stdin_json())
    if not rel:
        return
    tmpl = _SYNTAX_CHECKS.get(Path(rel).suffix.lower())
    if not tmpl:
        return
    argv = [PY if a == "{py}" else rel if a == "{f}" else a for a in tmpl]
    try:
        r = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=30)
    except Exception:
        return
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        _emit("PostToolUse", f"syntax check FAILED for {rel} ({argv[0]}):\n{detail}")


def stop() -> None:
    data = _stdin_json()
    # Never loop: if this Stop is a re-invocation caused by our OWN prior nudge,
    # stay silent so the turn can actually end. Without this guard a single
    # unresolved drift condition re-fires on every turn-end for a whole pipeline
    # (the "blocked N consecutive times" wedge). Drift is only HARD-enforced at
    # commit (`commit_guard`); here it is a one-shot advisory.
    if data.get("stop_hook_active"):
        return
    # One emit, however many notes: two JSON objects on stdout is not a contract the
    # harness owes us.
    notes: list[str] = []

    r = _run_script("doc-stats.py", "--check")
    if r and r.returncode != 0 and r.stdout.strip():
        notes.append(
            "Doc-counter drift (advisory — hard-enforced at `git commit`; "
            "fine to leave until the doc-writer stage reconciles it). Run "
            "`python3 scripts/doc-stats.py --check`:\n" + r.stdout.strip()
        )

    stale = _stale_handoff()
    if stale:
        notes.append(stale)

    if notes:
        _emit("Stop", "\n\n".join(notes))


def _stale_handoff() -> str:
    """How far docs/SESSION_STATE.md has fallen behind the work it describes.

    The handoff is a baseline doc, but nothing writes it on its own outside a `/dev`
    run's doc-writer stage — so a long stretch of ordinary sessions leaves the next
    session resuming from a description of last month. Counted in commits rather than
    days because a repo that saw no commits needs no handoff.

    Never fires for a file that was never committed: that's a freshly onboarded
    project with the template still in place, which is new, not stale.
    """
    if HANDOFF_STALE_COMMITS <= 0 or not (REPO / SESSION_STATE).is_file():
        return ""
    last = _git("log", "-1", "--format=%H", "--", SESSION_STATE)
    if not last or last.returncode != 0 or not last.stdout.strip():
        return ""
    count = _git("rev-list", "--count", f"{last.stdout.strip()}..HEAD")
    if not count or count.returncode != 0:
        return ""
    try:
        n = int(count.stdout.strip())
    except ValueError:
        return ""
    if n < HANDOFF_STALE_COMMITS:
        return ""
    return (
        f"Handoff is {n} commits behind (advisory — never blocks). "
        f"`{SESSION_STATE}` is what the next session resumes from, and a stale one is "
        f"worse than none because it will be believed. Run `/handoff` at session wrap, "
        f"or set CLAUDE_HANDOFF_STALE_COMMITS in .claude-workflow.env to retune this."
    )


def _git(*args: str):
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, timeout=30)
    except Exception:
        return None


def _new_changelog_headings(cmd: str) -> list[str]:
    """Entry headings (`## YYYY-MM-DD — …`) this commit would add to the CHANGELOG.

    Which diff to read depends on the command: a plain `git commit` takes only
    what is staged, while `git commit -a` also sweeps in unstaged changes to
    tracked files — and `git diff HEAD` is exactly that union. Reading the staged
    diff for an `-a` commit would miss a just-written entry (and reading HEAD for
    a plain commit would flag entries the user isn't committing yet).
    """
    if not (REPO / CHANGELOG).is_file():
        return []
    commits_all = bool(re.search(r"\bcommit\b[^;&|]*(?:\s-[A-Za-z]*a[A-Za-z]*\b|\s--all\b)", cmd))
    # `git diff HEAD` needs a HEAD: before the first commit there are no tracked
    # files for -a to sweep, so the staged diff is the whole story anyway.
    head = _git("rev-parse", "--verify", "-q", "HEAD")
    has_head = bool(head and head.returncode == 0)
    args = ("diff", "HEAD") if (commits_all and has_head) else ("diff", "--cached")
    r = _git(*args, "-U0", "--", CHANGELOG)
    diff = r.stdout if (r and r.returncode == 0) else ""
    return [ln[1:].rstrip() for ln in diff.splitlines()
            if re.match(r"^\+## \d{4}-\d{2}-\d{2}", ln)]


def _headings_missing_authority(headings: list[str]) -> list[str]:
    """Of those headings, the ones whose block in the CURRENT file has no
    `**Authority:**` line.

    Checking the file rather than the diff avoids false positives when an
    existing entry's heading shows up as added because the file was reordered.
    """
    if not headings:
        return []
    try:
        lines = (REPO / CHANGELOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    missing = []
    for heading in headings:
        try:
            start = lines.index(heading)
        except ValueError:
            continue  # not in the current file — nothing to enforce
        found = False
        for ln in lines[start + 1:]:
            if ln.startswith("## "):
                break
            if "**Authority:**" in ln:
                found = True
                break
        if not found:
            missing.append(heading)
    return missing


def commit_guard() -> None:
    """PreToolUse(Bash): block a `git commit` on doc-counter drift or a CHANGELOG
    entry with no Authority marker — the ONE place both are load-bearing.
    Non-commit Bash calls pass through untouched."""
    data = _stdin_json()
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not re.search(r"\bgit\s+commit\b", cmd):
        return

    problems: list[str] = []

    r = _run_script("doc-stats.py", "--check")
    if r and r.returncode != 0 and r.stdout.strip():
        problems.append(
            "Doc-counter drift — refusing to commit stale counters. Reconcile the "
            "docs (or run the doc-writer stage), then re-commit. "
            "`python3 scripts/doc-stats.py --check`:\n" + r.stdout.strip()
        )

    missing = _headings_missing_authority(_new_changelog_headings(cmd))
    if missing:
        problems.append(
            "CHANGELOG entries without an `**Authority:**` line:\n"
            + "\n".join(f"  ✗ {h}" for h in missing)
            + "\n\nEvery entry records where the decision came from — one of: "
              "`user`, `implementation (Claude Code)`, `design (chat session)`, "
              "`retrofit`. Add the marker (plus `**Supersedes:**` if this reverses "
              "an earlier entry), then re-commit."
        )

    if problems:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "\n\n".join(problems),
            }
        }))


# read-guard: a full-resolution screenshot costs a lot of context and every re-read
# pays it again (measured: 1-1.4 MB, 2600x1100 PNGs read 2-3x per UI check).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGE_MAX_BYTES = 300 * 1024
_IMAGE_MAX_WIDTH = 1600


def _image_width(path: Path) -> int | None:
    """Pixel width from the file header, or None if unknown. Pillow when present
    (it reads only the header); a PNG/GIF header parse otherwise."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width)
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big")
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return int.from_bytes(head[6:8], "little")
    except Exception:
        pass
    return None


def _shrink_cmd() -> str:
    script = _script("shrink_image.py") or (REPO / "scripts" / "shrink_image.py")
    return f"{PY} {script}"  # the interpreter running this hook (the shared venv)


def read_guard() -> None:
    """PreToolUse(Read): refuse a big image and name the shrink command instead.
    Anything unexpected (no path, unreadable file, odd input) lets the read through."""
    if os.environ.get("CLAUDE_ALLOW_BIG_IMAGES", "") not in ("", "0"):
        return
    fp = (_stdin_json().get("tool_input") or {}).get("file_path") or ""
    path = Path(fp)
    if path.suffix.lower() not in _IMAGE_EXTS or not path.is_file():
        return
    size = path.stat().st_size
    width = _image_width(path)
    if size <= _IMAGE_MAX_BYTES and (width is None or width <= _IMAGE_MAX_WIDTH):
        return
    dims = f"{width} px wide, " if width else ""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{fp} is a big image ({dims}{size // 1024} KB) — reading it costs a lot "
                f"of context, and again on every re-read. Shrink it first and read the "
                f"result: `{_shrink_cmd()} {fp}` (writes "
                f"<name>.small.png, <= 1200 px wide; add `--crop x,y,w,h` to zoom on a "
                f"region). Set CLAUDE_ALLOW_BIG_IMAGES=1 only if full resolution is "
                f"genuinely needed."
            ),
        }
    }))


_DISPATCH = {
    "session-start": session_start,
    "inject": inject,
    "syntax": syntax,
    "read-guard": read_guard,
    "commit-guard": commit_guard,
    "stop": stop,
}


def main() -> int:
    # In the host-global layout these hooks fire everywhere; stay completely out of
    # the way in projects that never adopted the workflow.
    if not _is_workflow_project():
        return 0
    _DISPATCH.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: None)()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # never wedge the session
