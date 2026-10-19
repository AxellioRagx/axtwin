"""What produced a run — the build stamp every sink persists (DESIGN_BRIEF §10).

Extracted out of `features/jsonl.py` (`WORK_ORDER_egress_sink_unification.md`,
build step 6): the belief store's `StoreSink` needs the exact same computation
`JsonlSink` already had, because "the same aximprint build on both sides" (§10's
first reconciliation invariant) is a claim about ONE computation, not two
implementations that happen to agree today and can drift tomorrow. Reuse, not
reimplementation.

No dependency on any pipeline layer — only the top-level `aximprint` package
(for `__version__` / `__file__`) and the standard library — so this module sits
below every layer and may be imported from any of them without creating an
upward edge (CONVENTIONS §3).
"""
from __future__ import annotations

import functools
import hashlib
import subprocess
from pathlib import Path

import aximprint


def git_commit(pkg: Path) -> str | None:
    """Short HEAD of the tree the package lives in, or None outside a checkout.

    None, never a placeholder string: an unknown build that reads as a known one
    is exactly the failure §10's reconciliation invariant exists to prevent.
    """
    try:
        out = subprocess.run(["git", "-C", str(pkg), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@functools.lru_cache(maxsize=1)
def build_stamp() -> dict:
    """What produced this run (§10: "the same aximprint build on both sides").

    `code_fingerprint` is the load-bearing one — a digest of the package's own
    sources, so it moves with an uncommitted edit, which a commit hash does not.
    `git_commit` is the human-reproducible handle. Both are stable across runs of
    the same tree, so neither disturbs a manifest's (or a store's) reproducibility.

    Cached (`S2`): re-hashing every `.py` file plus a `git` subprocess is real
    work (~6 ms measured), and before this every OPEN SINK in a fan-out paid it
    again — for a 3-sink run, 3x the cost for a value that is, by definition,
    the same "this run" fact each time. Caching also makes the concurrency
    story *more* correct, not just faster: without it, a source edit landing
    between two sinks' `open()` calls (or between the two passes of
    `scripts/verify_determinism.py`, `LES-018`) could stamp two different
    fingerprints onto ONE run. The returned dict is a shared cached object —
    callers must treat it as read-only, the way every existing caller already
    does (spread or read-only lookups, never mutated in place).
    """
    pkg = Path(aximprint.__file__).resolve().parent
    h = hashlib.sha256()
    for path in sorted(pkg.rglob("*.py")):
        h.update(path.relative_to(pkg).as_posix().encode())
        h.update(path.read_bytes())
    return {"aximprint_version": aximprint.__version__,
            "code_fingerprint": h.hexdigest()[:16],
            "git_commit": git_commit(pkg)}
