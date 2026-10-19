#!/usr/bin/env python3
"""inject_lessons — surface the docs/LESSONS.md entries relevant to a file.

Two roles:
  * The PreToolUse(Edit|Write) hook calls `inject_lessons.py <file>` and the
    matching lessons are surfaced to the agent about to edit that file, so a
    known bug-class / gotcha / reuse-rule is honored *before* the edit.
  * The SessionStart hook (or an agent) calls `inject_lessons.py --index` to load
    the whole LES index (one line per lesson) into context.

Matching is by the entry's `**Scope:**` field: path- or module-like tokens
(e.g. `src/api/client.ts`, `api`) are matched against the target path. Phrase-only
scopes ("Postgres 16", "any in-guest write") don't match a path — they ride the
--index instead. Read-only, dependency-free, no project-specific configuration:
bare tokens are resolved against the directories that actually exist in the repo.

Usage:
  python3 scripts/inject_lessons.py src/api/client.ts
  python3 scripts/inject_lessons.py --index
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


def _resolve_repo() -> Path:
    """The project this invocation is about.

    The scripts run in two layouts and must work in both:
      * host-global — one copy lives in the workflow template clone and serves every
        project on the machine, so the project is whatever Claude Code is working on;
      * per-project — a copy sits in the project's own scripts/.

    Resolution order: CLAUDE_PROJECT_DIR (set by Claude Code for hooks) → the git
    worktree containing the cwd → this file's parent directory. The last is the
    legacy per-project layout and stays as the final fallback.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent


REPO = _resolve_repo()
LESSONS = REPO / "docs" / "LESSONS.md"

_FIELD = re.compile(r"^\*\*(\w+):\*\*\s*(.*)$")
_HEADER = re.compile(r"^##\s+(LES-\d+)\s+—\s+(.*)$")
_FILENAME = re.compile(r"[a-z0-9_./-]*[a-z0-9_-]\.[a-z0-9]{1,8}")

# Directories that never name a meaningful lesson scope.
_IGNORED_DIRS = {
    ".git", ".claude", ".claude-sync", "node_modules", "__pycache__", ".venv",
    "venv", "env", "dist", "build", "target", "out", "coverage", "htmlcov",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".next", ".nuxt",
    ".idea", ".vscode", "vendor", ".gradle", ".terraform",
}
_MAX_DIRS = 2000  # bound the scan on very large repos


def parse_entries() -> list[dict]:
    if not LESSONS.is_file():
        return []
    entries: list[dict] = []
    cur: dict | None = None
    for line in LESSONS.read_text(encoding="utf-8").splitlines():
        h = _HEADER.match(line)
        if h:
            if cur:
                entries.append(cur)
            cur = {"id": h.group(1), "title": h.group(2).strip()}
            continue
        if cur is None:
            continue
        f = _FIELD.match(line)
        if f:
            cur[f.group(1).lower()] = f.group(2).strip()
    if cur:
        entries.append(cur)
    return entries


@lru_cache(maxsize=1)
def repo_dir_names(max_depth: int = 3) -> frozenset[str]:
    """Directory names that exist in this repo — the vocabulary a bare Scope token
    may use. Derived from the tree instead of a hard-coded list so the matcher
    works in any project.

    Walking the tree costs real time on the hot path (this runs before EVERY
    edit), so it is cached and, more importantly, only called when a Scope
    actually contains a bare token. Most scopes are paths or filenames, which
    resolve without it.
    """
    names: set[str] = set()

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth or len(names) >= _MAX_DIRS:
            return
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name in _IGNORED_DIRS or name.startswith("."):
                continue
            names.add(name.lower())
            walk(child, depth + 1)

    walk(REPO, 1)
    return frozenset(names)


def scope_matches(scope: str, target: str) -> bool:
    """Match a lesson to a file by its Scope tokens.

    Precise on purpose: only path fragments (`foo/bar`), filenames (`foo.py`,
    `a/b/foo.ts`) and directory names that really exist in the repo count.
    Generic prose words ("deploy", "retry"), lone separators, and phrase-only
    scopes ("Postgres 16") never match here — they'd match every file, or they
    ride the SessionStart --index instead.
    """
    target = target.replace("\\", "/").lower()
    segments = set(re.split(r"[/.]", target))
    for raw in re.split(r"[,\s;]+", scope):
        tok = raw.strip().strip("`").strip("().;:").lower()
        if not tok:
            continue
        # filename token (foo.py / a/b/foo.ts)
        if _FILENAME.fullmatch(tok):
            if "/" in tok:
                if tok in target:
                    return True
            elif target.rsplit("/", 1)[-1] == tok:
                return True
            continue
        # path fragment with a separator (e.g. "src/api/")
        if "/" in tok:
            frag = tok.rstrip("/")
            if frag and re.fullmatch(r"[a-z0-9_./-]+", frag) and frag in target:
                return True
            continue
        # bare token: only a directory that exists in this repo counts.
        # `tok in segments` is checked FIRST so the repo walk behind
        # repo_dir_names() is skipped entirely unless the token could match.
        if tok in segments and tok in repo_dir_names():
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", help="file path being edited")
    ap.add_argument("--index", action="store_true", help="print the LES index")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="with --index, list only the N newest entries (0 = all). "
                         "Keeps the SessionStart load proportionate as the store grows.")
    args = ap.parse_args()

    entries = parse_entries()
    if not entries:
        return 0  # no lessons yet — stay quiet

    if args.index:
        total = len(entries)
        shown = entries
        omitted = 0
        if args.limit and total > args.limit:
            # Entries are appended newest-last, so the tail is the recent history.
            shown = entries[-args.limit:]
            omitted = total - args.limit
        print(f"docs/LESSONS.md — {total} lessons on record"
              + (f" ({args.limit} newest shown):" if omitted else ":"))
        if omitted:
            print(f"  … {omitted} older entries not listed — read docs/LESSONS.md, or "
                  f"`inject_lessons.py --index` for the full list. They still "
                  f"auto-inject when their Scope matches a file you edit.")
        for e in shown:
            print(f"  {e['id']} — {e['title']}")
        return 0

    if not args.target:
        ap.error("provide a target file path or --index")

    matched = [e for e in entries if scope_matches(e.get("scope", ""), args.target)]
    if not matched:
        return 0  # nothing scoped to this file — stay quiet

    print(f"⚑ {len(matched)} recorded lesson(s) apply to {args.target}:")
    for e in matched:
        print(f"  [{e['id']}] {e['title']}")
        if e.get("rule"):
            print(f"      → {e['rule']}")
        if e.get("origin"):
            print(f"      (origin: {e['origin']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
