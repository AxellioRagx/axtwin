#!/usr/bin/env python3
"""Cross-process verification harness for the resource-lock correctness layer
(`docs/PLANNER.md` §6.2 lease + §8.4 `resource_locks`).

A single `axrange deploy` never exercises the cross-process path — the locks
only matter when *separate processes* contend. This harness spawns real OS
processes against a shared SQLite `state.db` and checks the two correctness
guarantees directly:

  - **locks**: N processes, each a distinct `deploy_id`, contend on ONE shared
    correctness resource key (e.g. `ovs:<node>`). With locking, their
    critical sections must NOT overlap in time. Without it, they would.
  - **lease**: N processes race to take the per-scenario lease for the SAME
    scenario within a tiny window; exactly ONE must win while it holds.

It needs no Proxmox — run it locally as a smoke check, or on the orchestrator
host against the real `state.db` to validate the actual backend under its real
filesystem (WAL behavior, busy_timeout, etc.).

Usage:
  python scripts/verify_resource_locks.py                 # both checks, temp db
  python scripts/verify_resource_locks.py --db /var/lib/axrange/state.db
  python scripts/verify_resource_locks.py --workers 8 --hold 0.25
  python scripts/verify_resource_locks.py --check locks   # one check only

Exit code 0 iff every selected check passes.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

# Allow running as `python scripts/verify_resource_locks.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axrange.planner.locks import acquire_blocking  # noqa: E402
from axrange.planner.state import StateStore  # noqa: E402


# ---------------------------------------------------------------------------
# Workers (top-level so they pickle under the 'spawn' start method)
# ---------------------------------------------------------------------------


def _lock_worker(db_path, key, idx, hold, barrier, q):
    """Acquire `key`, record the critical-section interval, release."""
    store = StateStore(db_path)
    deploy_id = f"verify-deploy-{idx}"
    op_id = f"verify-op-{idx}"
    try:
        barrier.wait()  # everyone races from the same instant
        ok = acquire_blocking(
            store, [key], deploy_id, op_id,
            ttl_s=60.0, poll_s=0.02, timeout_s=30.0,
            sleep_fn=time.sleep,
        )
        if not ok:
            q.put(("timeout", idx, 0.0, 0.0))
            return
        enter = time.time()
        time.sleep(hold)            # occupy the critical section
        exit_ = time.time()
        store.release_resource_locks_for_op(op_id)
        q.put(("held", idx, enter, exit_))
    finally:
        store.close()


def _lease_worker(db_path, scenario, idx, hold, barrier, q):
    """Race for the per-scenario lease; if won, HOLD it across the window so
    contenders are refused while it's valid."""
    store = StateStore(db_path)
    deploy_id = f"verify-deploy-{idx}"
    try:
        barrier.wait()
        res = store.acquire_lease(scenario, deploy_id, ttl_s=60.0)
        if res.acquired:
            time.sleep(hold)        # keep the lease valid while others attempt
            store.release_lease(scenario, deploy_id)
        q.put(("lease", idx, bool(res.acquired), res.held_by_deploy_id))
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _spawn(ctx, target, args_list):
    procs = [ctx.Process(target=target, args=a) for a in args_list]
    for p in procs:
        p.start()
    for p in procs:
        p.join()


def check_locks(db_path, workers, hold) -> bool:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    barrier = ctx.Barrier(workers)
    key = "ovs:verify-node"
    _spawn(ctx, _lock_worker,
           [(db_path, key, i, hold, barrier, q) for i in range(workers)])

    results = [q.get() for _ in range(workers)]
    held = sorted([(enter, exit_, idx) for tag, idx, enter, exit_ in results
                   if tag == "held"], key=lambda r: r[0])
    timeouts = [idx for tag, idx, *_ in results if tag == "timeout"]

    print(f"\n[locks] {workers} processes contended on {key!r} "
          f"(hold={hold}s each)")
    if timeouts:
        print(f"[locks] FAIL: workers timed out acquiring: {timeouts}")
        return False
    if len(held) != workers:
        print(f"[locks] FAIL: only {len(held)}/{workers} recorded a hold")
        return False

    # Mutual exclusion: no two critical sections may overlap.
    overlaps = []
    for (e1, x1, i1), (e2, x2, i2) in zip(held, held[1:]):
        if e2 < x1 - 1e-6:
            overlaps.append((i1, i2, round(x1 - e2, 4)))
    if overlaps:
        print(f"[locks] FAIL: overlapping critical sections (lock not "
              f"mutually exclusive): {overlaps}")
        return False

    span = held[-1][1] - held[0][0]
    print(f"[locks] PASS: {workers} non-overlapping critical sections, "
          f"serialized over {span:.2f}s (~{span / workers:.2f}s each, "
          f"expected ~{hold}s)")
    return True


def check_lease(db_path, workers, hold) -> bool:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    barrier = ctx.Barrier(workers)
    scenario = "verify-scenario"
    _spawn(ctx, _lease_worker,
           [(db_path, scenario, i, hold, barrier, q) for i in range(workers)])

    results = [q.get() for _ in range(workers)]
    winners = [idx for _, idx, acquired, _ in results if acquired]
    print(f"\n[lease] {workers} processes raced for scenario {scenario!r} "
          f"(winner holds {hold}s)")
    if len(winners) == 1:
        print(f"[lease] PASS: exactly one winner (deploy {winners[0]}); "
              f"{workers - 1} refused while the lease was valid")
        return True
    print(f"[lease] FAIL: expected 1 winner, got {len(winners)}: {winners}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None,
                    help="state.db path (default: a fresh temp file)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--hold", type=float, default=0.2,
                    help="seconds each winner occupies the lock/lease")
    ap.add_argument("--check", choices=["locks", "lease", "both"], default="both")
    args = ap.parse_args()

    tmp = None
    if args.db:
        db_path = args.db
    else:
        tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(tmp.name) / "verify-state.db")
        # Materialize the schema once before workers race on it.
        StateStore(db_path).close()

    try:
        ok = True
        if args.check in ("locks", "both"):
            ok &= check_locks(db_path, args.workers, args.hold)
        if args.check in ("lease", "both"):
            ok &= check_lease(db_path, args.workers, args.hold)
        print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
        return 0 if ok else 1
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
