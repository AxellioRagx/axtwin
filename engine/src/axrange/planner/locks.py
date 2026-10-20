"""Cross-process resource locking — the correctness layer (PLANNER §8.4).

Ratified 2026-06-03 (CHANGELOG "cross-process resource locking"). The
concurrency model splits each op's `resource_categories` into two classes:

  - **Correctness** (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`) — these MUST
    serialize across *different* concurrent deploy processes, not just within
    one process's semaphores. They are held as rows in the state store's
    `resource_locks` table so the serialization lives in the authoritative
    store, not process memory.
  - **Throughput** (`proxmox-cluster`, `proxmox-node:*`, `ansible-pool`, the
    global cap) — per-process semaphores (PLANNER §8.2), unaffected here.
    Aggregate over-subscription across deploys is a rate-limit/perf risk the
    cluster API absorbs, not a correctness bug.

This module owns three things:

  - `correctness_lock_keys()` — resolve an op's correctness categories to
    concrete lock keys (`vm:1204`, `ovs:node-3`, `gretap:sensor2`).
  - `acquire_blocking()` — poll the store until the whole key-set is held or a
    timeout elapses (the store call is all-or-nothing, so this never deadlocks).
  - `Heartbeat` — a background thread renewing the scenario lease + every held
    resource lock so a long-running deploy doesn't lose them to expiry.

The intra-deploy executor stays sequential (PLANNER §8.4 "Sequencing"); the
parallel executor + tuned throughput semaphores are a separate, later slice.
"""
from __future__ import annotations

import threading
from string import Template
from typing import Any, Callable, Sequence

from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.state import (
    DEFAULT_LEASE_TTL_S,
    StateStore,
    default_process_info,
)

# The resource-category prefixes whose serialization is *correctness* (§8.2:
# "fixed at 1; serialization is correctness"). Everything else is throughput.
CORRECTNESS_PREFIXES: tuple[str, ...] = ("ovs:", "vm:", "gretap:")


class LockResolutionError(Exception):
    """A correctness category referenced a `$var` we could not resolve from the
    op's scope / resolved inputs / cluster context. This is a planner/catalog
    bug (the op should carry every var its categories name) — fail loud."""


def _is_correctness(category: str) -> bool:
    return category.startswith(CORRECTNESS_PREFIXES)


def correctness_lock_keys(
    op: OpInstance,
    resolved_inputs: dict[str, Any],
    cluster: ClusterContext,
) -> list[str]:
    """Resolve `op`'s correctness `resource_categories` to concrete lock keys.

    Categories are `$var` templates (`vm:$vmid`, `ovs:$target_node`,
    `gretap:$sensor`). We substitute from, in increasing precedence:

      1. the op's `scope` (`host`, `net`, `scenario`, …),
      2. its resolved inputs (`vmid` from clone-vm's output, `target_node`,
         the various `*_vmid`/`*_node` planner-derived values),
      3. `sensor` <- `cluster.sensor_id` as the default capture target.

    Returns a sorted, de-duplicated list. The sort gives every process a
    single global acquisition order, which (together with the store's
    all-or-nothing acquire) is what rules out cross-process deadlock.
    """
    subst: dict[str, str] = {}
    for k, v in op.scope.items():
        if v is not None:
            subst[k] = str(v)
    subst.setdefault("sensor", cluster.sensor_id)
    for k, v in resolved_inputs.items():
        if v is not None:
            subst[k] = str(v)

    keys: set[str] = set()
    for category in op.resource_categories:
        if not _is_correctness(category):
            continue
        try:
            keys.add(Template(category).substitute(subst))
        except (KeyError, ValueError) as e:
            raise LockResolutionError(
                f"op {op.op_id}: cannot resolve correctness category "
                f"{category!r} — missing substitution {e}"
            ) from e
    return sorted(keys)


def acquire_blocking(
    store: StateStore,
    keys: Sequence[str],
    deploy_id: str,
    op_id: str,
    *,
    ttl_s: float = DEFAULT_LEASE_TTL_S,
    poll_s: float = 1.0,
    timeout_s: float = 600.0,
    process_info: dict[str, Any] | None = None,
    sleep_fn: Callable[[float], None],
) -> bool:
    """Poll `store.acquire_resource_locks` until the whole set is held.

    Returns True once acquired, or False if `timeout_s` of waiting elapses
    (the caller then fails the op cleanly rather than hanging forever — a
    live holder that crashes will release via expiry within `ttl_s`).

    Elapsed time is accumulated from `sleep_fn` calls rather than a wall clock
    so an injected fake-sleep advances it deterministically in tests.
    """
    keys = list(keys)
    if not keys:
        return True
    pinfo = process_info if process_info is not None else default_process_info()
    waited = 0.0
    while True:
        if store.acquire_resource_locks(
            keys, deploy_id, op_id, process_info=pinfo, ttl_s=ttl_s
        ):
            return True
        if waited >= timeout_s:
            return False
        sleep_fn(poll_s)
        waited += poll_s


class Heartbeat:
    """Background thread renewing a deploy's lease + resource locks.

    Opens its **own** StateStore on the same DB file (WAL permits the second
    connection; see StateStore.__init__) so it can write from a separate
    thread without sharing the executor's connection. Renews every
    `interval_s` until `stop()`; a missed renewal (process wedged) lets the
    rows expire so another deploy can reclaim them.

    Split-brain guard (code-review finding #2, 2026-07-07; wiring + the
    detection signal fixed 2026-07-07 follow-up review): a missed/lost
    renewal used to be silently dropped — `renew_lease`'s return value was
    never inspected, so this process kept issuing adapter calls even after
    another process had legitimately taken over as the scenario's writer.
    `lost` is set from **lease-renewal failure alone**:

      - `renew_lease` returns False -> the lease itself was reclaimed. This
        is the unambiguous, race-free signal: the lease is held for the
        entire deploy and only transitions ours->other on a genuine >TTL
        stall or takeover.

    An earlier version of this guard ALSO set `lost` on a per-resource-key
    "held as of the prior tick, now held by a different deploy_id" diff. That
    was racy: correctness resource keys (`ovs:<node>`, `vm:<vmid>`,
    `gretap:<sensor>`) are SHARED across scenarios by design, and a
    concurrent, entirely valid deploy of a DIFFERENT scenario legitimately
    acquiring a key right after we released it per-op would flip the
    key's holder and false-trigger `lost`, spuriously aborting a healthy
    deploy. That diff has been removed; we still call
    `renew_resource_locks(...)` every tick so our own held locks don't expire,
    but we no longer infer loss from a holder change on them.

    `lost` is exposed for a caller (`Executor(..., lost_event=heartbeat.lost)`)
    to poll between ops and abort rather than continue as a phantom writer.
    """

    def __init__(
        self,
        db_path: str,
        *,
        deploy_id: str,
        scenario_id: str,
        interval_s: float = 60.0,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> None:
        self._db_path = db_path
        self._deploy_id = deploy_id
        self._scenario_id = scenario_id
        self._interval_s = interval_s
        self._ttl_s = ttl_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Set once a lease/lock renewal is lost. Sticky: once split-brain is
        # possible, this heartbeat never claims to be safe again.
        self.lost = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name=f"axrange-heartbeat-{self._deploy_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 5.0)
            self._thread = None

    def __enter__(self) -> "Heartbeat":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _loop(self) -> None:
        store = StateStore(self._db_path)
        try:
            while not self._stop.wait(self._interval_s):
                if not store.renew_lease(
                    self._scenario_id, self._deploy_id, ttl_s=self._ttl_s
                ):
                    self.lost.set()
                    continue
                # Still renew our own resource locks every tick so they don't
                # expire out from under a long-running deploy -- we just no
                # longer infer `lost` from a per-key holder-change diff here
                # (see the class docstring: that signal was racy against a
                # legitimate concurrent different-scenario deploy).
                store.renew_resource_locks(self._deploy_id, ttl_s=self._ttl_s)
        finally:
            store.close()
