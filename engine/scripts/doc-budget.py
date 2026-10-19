#!/usr/bin/env python3
"""doc-budget — a tiny, standalone size gate on the docs loaded into every session and
every background pipeline agent's context.

Three files pay a per-invocation context cost regardless of whether a given run needs
their historical content: `CLAUDE.md` (auto-loaded at session start), `docs/SESSION_STATE.md`
(loaded by the SessionStart hook), and `docs/CHANGELOG.md` (read by most doc-writer/reviewer
passes). Measured on this project before its 2026-09-16 archival split: `CLAUDE.md` alone was
145 KB (~36k tokens) x 44 agent runs in one session. This script checks each file's byte size
against a budget and exits non-zero if any is over — a cheap, mechanical alarm that says "time
to archive the old material out", not a byte-exact contract.

The fix for an over-budget file is almost never to summarize or delete content — this
project's "record, don't rewrite history" discipline applies to size-driven moves exactly as
it does to content ones. Move the historical material out **verbatim** into a dated file under
`docs/` (see `docs/DIRECTION_LOG.md`, `docs/changelog/2026-H1.md`, `docs/handoffs/2026-09.md`
for the pattern this repo uses), leave a short dated pointer list behind, and don't just raise
the number here. See `docs/CONVENTIONS.md` "Doc size budgets" for the reasoning behind each
budget below, including why `docs/CHANGELOG.md`'s is well above the round number a first pass
might suggest.

Stdlib only, no dependencies. Read-only — never edits anything.

Usage:
  python3 scripts/doc-budget.py          # print each file's size + verdict; exit 1 if any over
                                         # (WORK_ORDER_*.md over their soft budget: WARN, exit unaffected)
  python3 scripts/doc-budget.py --json   # ... as JSON (same exit code)
"""
from __future__ import annotations

import json
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
    import os

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

# Byte budgets. 1 KB = 1000 bytes here (a human-tuned soft gate, not a binary-KiB contract).
# Edit these numbers here, not in docs/CONVENTIONS.md — that file records *why* each number
# is what it is, so a future change can be checked against its own reasoning.
#
# docs/CHANGELOG.md's budget is deliberately well above the ~250 KB a first pass might
# suggest: recent entries (Console/golden-twin-era work) are individually large enough that
# the 2026-07-01-onward content alone is ~718 KB even right after moving every
# 2026-01-01..2026-06-30 entry out to docs/changelog/2026-H1.md. Treat this budget as a
# regrowth alarm ("time for a 2026-H2 archival split"), not a claim that 250 KB is reachable
# while the live file still holds nine months of dense entries.
BUDGETS: dict[str, int] = {
    "CLAUDE.md": 12_000,
    # A REPLACED-each-time handoff (2026-09-19): branch/HEAD, in-flight, next steps, open decisions,
    # current gotchas. Prior content is archived verbatim to docs/handoffs/, never appended here.
    "docs/SESSION_STATE.md": 8_000,
    "docs/CHANGELOG.md": 800_000,
}

# Soft budgets: glob -> bytes. Reported as WARN, never fail the run. A work order is the ACTIVE
# spec (scope, slices, acceptance, open decisions); its built-slice records move verbatim to
# docs/archive/work_orders/<name>.built.md (docs/CONVENTIONS.md §3). Target <= 30 KB; warn past 40.
WARN_BUDGETS: dict[str, int] = {
    "docs/WORK_ORDER_*.md": 40_000,
}


def check(budgets: dict[str, int] = BUDGETS) -> tuple[list[dict], int]:
    """Return (one result dict per configured file, exit code)."""
    results: list[dict] = []
    exit_code = 0
    for rel, budget in budgets.items():
        path = REPO / rel
        if not path.is_file():
            results.append({"file": rel, "bytes": None, "budget": budget, "over": False,
                             "note": "missing"})
            continue
        size = path.stat().st_size
        over = size > budget
        if over:
            exit_code = 1
        results.append({"file": rel, "bytes": size, "budget": budget, "over": over})
    return results, exit_code


def check_warn(budgets: dict[str, int] = WARN_BUDGETS) -> list[dict]:
    """Soft budgets over globs: one result per file OVER its budget (never affects exit code)."""
    results: list[dict] = []
    for pattern, budget in budgets.items():
        for path in sorted(REPO.glob(pattern)):
            size = path.stat().st_size
            if size > budget:
                results.append({"file": str(path.relative_to(REPO)), "bytes": size,
                                "budget": budget, "warn": True})
    return results


def main() -> int:
    as_json = "--json" in sys.argv
    results, exit_code = check()
    warnings = check_warn()

    if as_json:
        print(json.dumps(results + warnings, indent=2))
        return exit_code

    print("doc-budget — size of the docs loaded into every session/agent context:\n")
    width = max(len(r["file"]) for r in results)
    for r in results:
        if r["bytes"] is None:
            print(f"  ? {r['file']:<{width}}  MISSING (skipped)")
            continue
        marker = "✗" if r["over"] else "✓"
        verdict = "OVER" if r["over"] else "ok"
        kb = r["bytes"] / 1000
        budget_kb = r["budget"] / 1000
        print(f"  {marker} {r['file']:<{width}}  {kb:>8.1f} KB  (budget {budget_kb:.0f} KB)  [{verdict}]")

    if warnings:
        print("\nSoft budgets (warn only — archive built-slice records verbatim, docs/CONVENTIONS.md §3):")
        for w in warnings:
            print(f"  ! {w['file']}  {w['bytes'] / 1000:.1f} KB  (warn over {w['budget'] / 1000:.0f} KB)")

    if exit_code:
        print("\nOver budget — move older/historical content out to a dated docs/ archive,\n"
              "verbatim, with a pointer left behind (see docs/CONVENTIONS.md \"Doc size\n"
              "budgets\"). Don't just raise the number.")
    else:
        print("\nAll files within budget.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
