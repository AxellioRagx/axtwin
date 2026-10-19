#!/usr/bin/env python3
"""doc-stats — compute this project's volatile counters from source, and flag doc
claims that have drifted from them.

Docs quote counters that go stale constantly — test counts, endpoint counts, rule
counts, supported-platform counts. This script computes each counter from the
*source of truth* (a command you configure) and, in --check mode, compares it
against the numbers written in the docs, so the doc-writer stage (and the commit
guard) catch drift automatically instead of a human noticing months later.

Configuration lives in `.claude-doc-stats.json` at the repo root — run
`doc-stats.py --init` to scaffold one. With no config, every mode is a quiet
no-op that exits 0, so an unconfigured project is never blocked.

Usage:
  python3 scripts/doc-stats.py                 # print current counters
  python3 scripts/doc-stats.py --json          # ... as JSON
  python3 scripts/doc-stats.py --check         # compare docs vs source; exit 1 on drift
  python3 scripts/doc-stats.py --init          # write a starter .claude-doc-stats.json

Read-only. Never edits docs (the doc-writer applies fixes).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
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
CONFIG = REPO / ".claude-doc-stats.json"

_STARTER = {
    "$comment": (
        "doc-stats config. `counters` computes a number from source: `command` is run "
        "from the repo root (string = shell, list = argv) and `extract` is a regex whose "
        "first capture group is the number; the token `{python}` in a command is replaced "
        "by the interpreter running doc-stats. A counter whose command exits non-zero is "
        "skipped, not trusted — a failed command still prints, and a partial count is "
        "worse than none; set \"allow_nonzero\": true for a command like `grep -c` that "
        "exits non-zero as data. `claims` are the phrasings the docs use for "
        "each counter — every match in `files` is compared against the computed value. "
        "Keep CHANGELOG/LESSONS out of `files`: their dated entries are historical and "
        "are SUPPOSED to quote the number that was true then. `limits` bounds a doc's "
        "size — advisory by default, and blocking at commit time only with "
        "\"enforce\": true. NOTE: `command` values run through the shell on every "
        "--check, i.e. at turn-end and before every commit — treat this file as "
        "executable code and review it like any other hook. Delete it to disable "
        "drift checking entirely."
    ),
    "counters": {
        "tests": {
            "command": "python3 -m pytest --collect-only -q",
            "extract": r"(\d+)\s+tests?\s+collected",
        }
    },
    "claims": [
        {"counter": "tests", "pattern": r"(\d+)\s+tests\b", "label": "tests"}
    ],
    "files": ["CLAUDE.md"],
    "limits": {
        "CLAUDE.md": {"max_lines": 500, "enforce": False},
        # Loaded into every session by the SessionStart hook, and truncated past
        # CLAUDE_HANDOFF_CONTEXT_LINES — so an over-long handoff is silently a
        # partial one. Advisory: a busy session legitimately has a lot to hand over.
        "docs/SESSION_STATE.md": {"max_lines": 120, "enforce": False},
    },
}


def load_config() -> dict | None:
    if not CONFIG.is_file():
        return None
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"doc-stats: {CONFIG.name} is not valid JSON ({exc}) — skipping.", file=sys.stderr)
        return None


def compute(config: dict) -> dict[str, int | None]:
    stats: dict[str, int | None] = {}
    for name, spec in (config.get("counters") or {}).items():
        stats[name] = _compute_one(name, spec)
    return stats


def _compute_one(name: str, spec: dict) -> int | None:
    """Compute one counter, or None if its number can't be trusted.

    A command that fails still prints — a pytest run that errors after collecting
    part of the suite, a script that dies halfway through counting. Without the
    returncode check below, that partial output yields a confident wrong number
    that then blocks or passes a commit for the wrong reason, which is worse than
    having no counter at all. So: non-zero exit means no number, and a line on
    stderr saying why, since a counter that goes quiet is a counter that has
    stopped policing anything. Commands that use a non-zero exit as data
    (`grep -c` with no matches) opt out with "allow_nonzero": true.
    """
    cmd = spec.get("command")
    if not cmd:
        return None
    # `{python}` stands for the interpreter running doc-stats itself (the venv python the
    # hook / test invoked it with), so a config never has to hardcode where the venv lives
    # — a per-package `.venv/bin/python` stops existing the moment the package joins a
    # monorepo with one shared venv at the worktree root.
    try:
        if isinstance(cmd, str):
            cmd = cmd.replace("{python}", shlex.quote(sys.executable))
            proc = subprocess.run(cmd, cwd=REPO, shell=True, capture_output=True,
                                  text=True, timeout=spec.get("timeout", 180))
        else:
            argv = [sys.executable if str(c) == "{python}" else str(c) for c in cmd]
            proc = subprocess.run(argv, cwd=REPO, capture_output=True,
                                  text=True, timeout=spec.get("timeout", 180))
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"doc-stats: counter {name!r} did not run ({exc}) — not checked.", file=sys.stderr)
        return None
    if proc.returncode != 0 and not spec.get("allow_nonzero"):
        print(f"doc-stats: counter {name!r} exited {proc.returncode} — its output is not a "
              f"trustworthy count, so {name!r} is not checked. Fix the command, or set "
              f'"allow_nonzero": true if a non-zero exit is normal for it.', file=sys.stderr)
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    pattern = spec.get("extract") or r"(\d+)"
    try:
        m = re.search(pattern, out, re.MULTILINE)
    except re.error as exc:
        print(f"doc-stats: counter {name!r} has a bad `extract` regex ({exc}) — not checked.",
              file=sys.stderr)
        return None
    if not m:
        print(f"doc-stats: counter {name!r} ran but its output matched no number — not checked.",
              file=sys.stderr)
        return None
    try:
        return int(m.group(1))
    except ValueError:
        # `extract`'s first group caught something that isn't a number. Skipping beats
        # raising: --check is the commit gate, and a traceback here denies the commit
        # with nothing useful printed.
        print(f"doc-stats: counter {name!r} matched {m.group(1)!r}, which is not a number — "
              f"`extract`'s first group must capture the digits. Not checked.", file=sys.stderr)
        return None


def _doc_files(config: dict) -> list[Path]:
    files: list[Path] = []
    for pattern in config.get("files") or ["CLAUDE.md"]:
        if any(ch in pattern for ch in "*?["):
            files.extend(sorted(REPO.glob(pattern)))
        else:
            p = REPO / pattern
            if p.is_file():
                files.append(p)
    return files


def check_limits(config: dict) -> tuple[list[str], list[str]]:
    """Size budgets for docs that are read every session (CLAUDE.md above all).

    Returns (blocking, advisory) violation messages. A limit is advisory unless it
    sets "enforce": true — a doc a few lines over budget should nag, not wedge a
    commit, but a project that wants a hard ceiling can have one.
    """
    blocking: list[str] = []
    advisory: list[str] = []
    for rel, spec in (config.get("limits") or {}).items():
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, actual, unit in (
            ("max_lines", len(text.splitlines()), "lines"),
            ("max_bytes", len(text.encode("utf-8")), "bytes"),
        ):
            cap = spec.get(key)
            if cap is None or actual <= cap:
                continue
            msg = (f"{rel}: {actual} {unit} — budget {cap}, over by {actual - cap}. "
                   f"Move detail into docs/ and keep this file a summary.")
            (blocking if spec.get("enforce") else advisory).append(msg)
    return blocking, advisory


def check(config: dict, stats: dict[str, int | None]) -> int:
    claims = config.get("claims") or []
    limit_blocking, limit_advisory = check_limits(config)

    if not claims:
        if limit_blocking or limit_advisory:
            _report_limits(limit_blocking, limit_advisory)
            return 1 if limit_blocking else 0
        print("doc-stats: no claims configured — nothing to check.")
        return 0

    compiled = []
    for claim in claims:
        try:
            compiled.append((claim["counter"], re.compile(claim["pattern"]),
                             claim.get("label", claim["counter"])))
        except (KeyError, re.error) as exc:
            print(f"doc-stats: skipping malformed claim {claim!r} ({exc})", file=sys.stderr)

    drift: list[str] = []
    for path in _doc_files(config):
        rel = path.relative_to(REPO)
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for key, pat, label in compiled:
                actual = stats.get(key)
                if actual is None:
                    continue
                for m in pat.finditer(line):
                    claimed = int(m.group(1))
                    if claimed != actual:
                        drift.append(f"{rel}:{i}  claims {claimed} {label}, source says {actual}")

    if drift:
        print("DOC DRIFT — quoted counters disagree with source:")
        for d in drift:
            print(f"  ✗ {d}")
        print(f"\n{len(drift)} stale counter(s). Update the docs or re-check.")
        _report_limits(limit_blocking, limit_advisory)
        return 1

    if limit_blocking or limit_advisory:
        print("No counter drift in the policed docs.")
        _report_limits(limit_blocking, limit_advisory)
        return 1 if limit_blocking else 0

    print("No counter drift in the policed docs.")
    return 0


def _report_limits(blocking: list[str], advisory: list[str]) -> None:
    if blocking:
        print("\nDOC SIZE — over an enforced budget:")
        for m in blocking:
            print(f"  ✗ {m}")
    if advisory:
        print("\nDOC SIZE (advisory — not blocking):")
        for m in advisory:
            print(f"  ⚠ {m}")


def init() -> int:
    if CONFIG.is_file():
        print(f"{CONFIG.name} already exists — leaving it alone.")
        return 0
    CONFIG.write_text(json.dumps(_STARTER, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {CONFIG.name}. Edit `counters` / `claims` / `files` for this project;")
    print("the example counts tests via pytest — replace it with your real command.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="compare docs vs source; exit 1 on drift")
    ap.add_argument("--json", action="store_true", help="print counters as JSON")
    ap.add_argument("--init", action="store_true", help="write a starter .claude-doc-stats.json")
    args = ap.parse_args()

    if args.init:
        return init()

    config = load_config()
    if config is None:
        # Unconfigured is a valid state: stay quiet and never block a commit.
        if not args.check:
            print(f"doc-stats: no {CONFIG.name} — nothing to compute. "
                  "Run `doc-stats.py --init` to set counters up.")
        return 0

    stats = compute(config)

    if args.check:
        return check(config, stats)

    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    print("Counters (from source):")
    for key, val in stats.items():
        print(f"  {key:<12} {val if val is not None else '(unavailable)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
