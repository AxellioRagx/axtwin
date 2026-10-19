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

Output forms:
  * default (per-file) — COMPACT: `LES-NNN — title` plus the entry's `Rule:`
    clipped to its first sentence (≤ RULE_CLIP chars). This runs before every
    Edit/Write, so it is the hot path for context cost; the full entry is one
    command away (`--full`).
  * `--full LES-NNN ...` — the complete entries, verbatim from docs/LESSONS.md.
  * `--seen-file PATH` (per-file mode) — per-session dedupe: skip ids already
    listed in PATH, then record the newly surfaced ids there. The hook passes one
    file per (session, agent) so each agent sees each lesson once. Any error in
    this path falls back to the un-deduped output (fail open).

Usage:
  python3 scripts/inject_lessons.py src/api/client.ts
  python3 scripts/inject_lessons.py --index
  python3 scripts/inject_lessons.py --full LES-012 LES-300
"""
from __future__ import annotations

import argparse
import json
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
# Per-file injection clips each Rule to its first sentence, capped at this many
# characters. The title already states the lesson; `--full` returns the rest.
RULE_CLIP = 320


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
    # The target's own top-level directory is never a discriminating token: it
    # is an ancestor of everything the matcher could return, so matching on it
    # is matching every file. It is also exactly where a project's own name
    # collides with ordinary prose -- a repo whose package directory is named
    # after the product ("axrange/") has that word in Scope text constantly as
    # a CLI command ("`axrange deploy --fresh`", "`axrange destroy`"), never as
    # a path reference. Real directory scopes are written as paths
    # ("axrange/planner/state.py") and match on the path branch above, so
    # excluding the root costs nothing and stops whole-tree over-matching.
    # A lesson that genuinely applies repo-wide rides the SessionStart --index.
    root_segment = target.split("/", 1)[0]
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
        # bare token: only a directory that exists in this repo counts, and
        # never the target's own root directory (see root_segment above).
        # `tok in segments` is checked FIRST so the repo walk behind
        # repo_dir_names() is skipped entirely unless the token could match.
        if tok in segments and tok != root_segment and tok in repo_dir_names():
            return True
    return False


def full_entries(ids: list[str]) -> list[str]:
    """The verbatim markdown block of each requested LES id, in request order."""
    want = [i.strip().upper() for i in ids if i.strip()]
    want = [i if i.startswith("LES-") else f"LES-{i}" for i in want]
    want = [f"LES-{int(i[4:]):03d}" if i[4:].isdigit() else i for i in want]
    if not LESSONS.is_file() or not want:
        return []
    blocks: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for line in LESSONS.read_text(encoding="utf-8").splitlines():
        h = _HEADER.match(line)
        if h:
            cur = blocks.setdefault(h.group(1), []) if h.group(1) in want else None
        elif line.startswith("## "):
            cur = None
        if cur is not None:
            cur.append(line)
    return ["\n".join(blocks[i]).rstrip() for i in want if i in blocks]


def clip_rule(rule: str, limit: int = RULE_CLIP) -> str:
    """First sentence of a Rule, capped at `limit` chars (marked with … if cut)."""
    rule = rule.strip()
    first = rule
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z`(\"*])", rule):
        # Not a sentence end after an abbreviation ("e.g.", "i.e.", "vs.", "etc.").
        if re.search(r"(?:\b[a-z]\.[a-z]|\bvs|\betc|\bcf)\.$", rule[: m.start()], re.I):
            continue
        first = rule[: m.start()]
        break
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + " …"
    elif len(first) < len(rule):
        first += " …"
    return first


def _read_seen(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8") or "[]")
    return {str(x) for x in data}


def _write_seen(path: Path, ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a concurrent reader sees old or new, never half


def render_matched(target: str, matched: list[dict], seen_file: str | None) -> str:
    """The per-file injection text, deduped against `seen_file` when given.

    Dedupe fails OPEN: any error reading/writing the seen file degrades to the
    full (un-deduped) compact list — a hook must never cost the agent a lesson.
    """
    fresh, already = matched, []
    if seen_file:
        try:
            path = Path(seen_file)
            seen = _read_seen(path)
            fresh = [e for e in matched if e["id"] not in seen]
            already = [e for e in matched if e["id"] in seen]
            if fresh:
                _write_seen(path, seen | {e["id"] for e in fresh})
        except Exception:
            fresh, already = matched, []
    out: list[str] = []
    if fresh:
        out.append(f"⚑ {len(fresh)} recorded lesson(s) apply to {target}:")
        for e in fresh:
            out.append(f"  {e['id']} — {e['title']}")
            if e.get("rule"):
                out.append(f"      Rule: {clip_rule(e['rule'])}")
    if already:
        ids = ", ".join(e["id"] for e in already)
        out.append(f"(also applies, surfaced earlier this session: {ids})")
    if fresh:
        out.append("Full entry: `python scripts/inject_lessons.py --full <LES-ids>` "
                   "or grep docs/LESSONS.md.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", help="file path being edited")
    ap.add_argument("--index", action="store_true", help="print the LES index")
    ap.add_argument("--full", nargs="+", metavar="LES-ID",
                    help="print the complete entries for these ids, verbatim")
    ap.add_argument("--seen-file", metavar="PATH",
                    help="per-session dedupe state (JSON list of already-injected ids)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="with --index, list only the N newest entries (0 = all). "
                         "Keeps the SessionStart load proportionate as the store grows.")
    args = ap.parse_args()

    if args.full:
        blocks = full_entries(args.full)
        if blocks:
            print("\n\n".join(blocks))
        return 0 if blocks else 1

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

    text = render_matched(args.target, matched, args.seen_file)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
