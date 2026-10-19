"""spool — the pcap spool and its disk governor (H-3,
`docs/WORK_ORDER_disk_governor.md`).

A 30-day, all-stream harvest moves ~12.9 TB through a 168 GB disk
(`docs/PLAN_metadata_harvester.md` §5). `Spool` is what makes a run of that
shape possible: it admits extraction work only when there is room, accounts
for what actually lands, and releases space once a pcap's consumer (Zeek)
reports success.

**The crux this module is built around:** a pcap's size is not known before
it is extracted — the appliance streams a body of unannounced length. So this
cannot be a classic reserve-exact-then-commit allocator:

  1. **Admission** (`admit`) reserves an *estimate* — the estimate improves
     as real sizes are observed (`SizeEstimator`).
  2. **Accounting** (`commit`) replaces the estimate with the *actual* size
     once the file has landed.
  3. **Overshoot is possible and is handled, not assumed away.** A window
     that lands 5x larger than estimated can push `usage()` past
     `cap_bytes`. The correct response is to stop *admitting* new work until
     the reaper drains resident pcaps — never to delete one that has not
     been processed. `available()` can therefore go negative; that is the
     signal, not a bug.

**Non-negotiable: never evict unprocessed work.** The cap is enforced by
`admit()` blocking, never by discarding a reservation or a resident pcap
under pressure — see "Boundaries" in the work order. `release()` is the only
thing that ever deletes a file, and only after its caller reports success.
A failed consumer (Zeek) simply never calls `release()`; the pcap stays
resident, still counted against the cap, so the window can be retried.

Every entry moves through exactly one path: `reserved` (via `admit`) ->
`resident` (via `commit`) -> gone (via `release`) — or `reserved` -> gone
directly (via `fail`, for an extraction that never produced a file).
`release()` on a `reserved` entry and `fail()` on a `resident` entry are both
rejected: the state machine itself is what makes "delete only after success"
structural rather than a convention callers have to remember.

Thread-safe: the extract pool and the reaper run concurrently. One lock
(`threading.Condition`), every mutation under it, waiters notified on every
release/fail/commit.

Integers only, no floats, anywhere sizes or the cap are involved.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from aximprint.acquire.errors import XtractError

__all__ = [
    "DEFAULT_ESTIMATOR_HISTORY",
    "DEFAULT_SEED_BYTES",
    "EntryState",
    "Estimator",
    "Reservation",
    "SizeEstimator",
    "Spool",
    "SpoolConfigError",
    "SpoolDeadlockError",
    "SpoolError",
    "SpoolTimeoutError",
    "validate_window_id",
]


class SpoolError(XtractError, ValueError):
    """Base class for every error `spool.py` raises.

    Also a `ValueError` — matching every other `xtract` module's error root (`LES-041`) — so an
    existing `except ValueError` call site keeps working while `cli.py`'s `except XtractError`
    catches it too.
    """


class SpoolConfigError(SpoolError):
    """A single estimated window cannot fit even in a completely empty spool.

    Raised at admission, before any blocking — `cap_bytes` (or the
    estimator's seed/margin) is misconfigured for the workload, and blocking
    on a request that can never succeed would just deadlock the caller.
    """


class SpoolDeadlockError(SpoolError):
    """Nothing is resident and nothing is reserved, and admission still
    cannot proceed.

    This is `SpoolConfigError`'s failure surfaced from a different
    direction: instead of being caught at entry (the estimate available
    then already exceeded `cap_bytes`), it is caught here when the spool has
    fully drained *while a caller was waiting* and still cannot admit that
    caller — most often because the estimator's prediction grew (fed by
    other windows' actual sizes) past `cap_bytes` during the wait. Nothing
    outstanding can ever free more room than an empty spool already offers,
    so this raises instead of waiting forever.
    """


class SpoolTimeoutError(SpoolError):
    """`admit()`'s `timeout` elapsed before room became available.

    Turns a stalled reaper (or a genuinely undersized cap that isn't quite
    bad enough to trip the deadlock guards) into a caught error instead of
    an indefinite hang.
    """


class EntryState(Enum):
    """Where one tracked pcap is in its lifecycle. See the module docstring
    for the two legal paths through these states.
    """

    RESERVED = "reserved"
    RESIDENT = "resident"


@dataclass(frozen=True)
class Reservation:
    """What `admit()` returns: room has been reserved for `window_id`, and
    `path` is where the caller must write the resulting pcap. `estimated_bytes`
    is the estimate the reservation was admitted under — informational only,
    accounting is updated to the real number on `commit()`.
    """

    window_id: str
    path: Path
    estimated_bytes: int


@dataclass
class _Entry:
    state: EntryState
    size: int  # the estimate while RESERVED; the actual size once RESIDENT


# A generic, deliberately arbitrary starting guess used only until real sizes
# have been observed. `harvest`'s own wiring (H-4) should override this with
# a domain-informed seed once real window/stream sizing is known
# (`docs/PLAN_metadata_harvester.md` §5) — this module stays agnostic of any
# particular stream/window shape so it can be reused unchanged elsewhere.
DEFAULT_SEED_BYTES = 64 * 1024 * 1024  # 64 MiB

# How many of the most recent observed sizes the estimator's rolling
# statistic considers. Bounded (rather than "the max ever seen") so a single
# historical outlier eventually ages out and throughput recovers once the
# workload actually shrinks back down — see `SizeEstimator`.
DEFAULT_ESTIMATOR_HISTORY = 8


@runtime_checkable
class Estimator(Protocol):
    """What `Spool` needs from *some* way of predicting a window's pcap size — satisfied by
    `SizeEstimator` and by any duck-typed stand-in a test supplies (mirrors `zeek.ZeekRunner`'s
    protocol pattern). Declaring this explicitly means a test's scripted estimator (which
    implements `estimate`/`observe` without subclassing `SizeEstimator`) is a legitimate
    `Estimator`, not a type-checker error.
    """

    def estimate(self) -> int:
        """The current predicted size, in bytes, for the next window to be admitted."""
        ...

    def observe(self, actual_bytes: int) -> None:
        """Feed one real, landed size back into whatever history the estimator keeps."""
        ...


class SizeEstimator:
    """Predicts the next window's pcap size from sizes already observed.

    **Deliberately conservative — this is the load-bearing design decision,
    not an implementation detail.** `estimate()` returns the *maximum* of the
    last `history` observed sizes (the seed when nothing has been observed
    yet), never the mean. Under-estimating lets admission over-commit and
    overshoot `cap_bytes`; over-estimating only costs throughput (fewer
    windows admitted concurrently than the disk could truly support) —
    that asymmetry is exactly why "erring high" is the requirement, not a
    preference (`docs/WORK_ORDER_disk_governor.md` §2). A future tuning pass
    will be tempted to swap this for `statistics.mean(history)`; it would
    read cleaner and it would be wrong: a window that runs 5x larger than
    everything before it is precisely the case that threatens the cap, and
    the mean of everything smaller that preceded it says nothing useful
    about that one window. Tracking the max instead means one big window
    immediately raises the floor for every window admitted after it.

    The history is bounded (not "the max ever observed") so a one-off
    outlier eventually ages out of the window and the estimate — and
    therefore admitted concurrency — recovers once the workload actually
    gets smaller again, rather than throttling the rest of a multi-day run
    off one early spike.

    Not shared across `Spool` instances: each `Spool` owns its own estimator
    (or the caller-supplied one), since the rolling history is per-spool
    state.
    """

    def __init__(
        self,
        *,
        seed_bytes: int = DEFAULT_SEED_BYTES,
        history: int = DEFAULT_ESTIMATOR_HISTORY,
    ):
        if seed_bytes <= 0:
            raise SpoolConfigError(f"seed_bytes must be positive, got {seed_bytes}")
        if history <= 0:
            raise SpoolConfigError(f"history must be positive, got {history}")
        self._seed_bytes = seed_bytes
        self._observed: deque[int] = deque(maxlen=history)

    def estimate(self) -> int:
        """The current prediction: the seed if nothing has been observed
        yet, otherwise the maximum of the recent observed sizes.
        """
        if not self._observed:
            return self._seed_bytes
        return max(self._observed)

    def observe(self, actual_bytes: int) -> None:
        """Feed one real, landed size back into the rolling history. Called
        from `Spool.commit()` — never call this directly against a `Spool`'s
        estimator from outside the spool, or the two fall out of sync.
        """
        if actual_bytes < 0:
            raise SpoolError(f"actual_bytes must not be negative, got {actual_bytes}")
        self._observed.append(actual_bytes)


def validate_window_id(window_id: str) -> None:
    """Reject a `window_id` that could escape `root` once interpolated into `_path_for`'s
    `f"{window_id}.pcap"`. This module deletes files for a living (`release()`) — an unvalidated
    id is a path-traversal vector, not a naming nicety: `admit("../outside/important")` previously
    returned a `Reservation` pointing outside `root`, and a later `release()` for that same id
    deleted whatever was there. Called from `admit()`, the primary place a caller-supplied
    `window_id` first enters the spool's tracking.

    Public (not `_`-prefixed) since `docs/WORK_ORDER_harvest_from_pcaps.md`: `harvest --from-pcaps`
    derives a `window_id` from a filename stem in a user-controlled directory — the first caller of
    this check outside `admit()`, and the first time it has ever validated an id this project did
    not generate itself. Reusing this exact function (rather than a second, parallel check) is what
    makes that validation load-bearing instead of assumed.

    Rejects: not a non-empty string; any path separator (`/`, or `os.sep` on a platform where
    that differs, e.g. `\\` on Windows); a NUL byte (also unrepresentable in a POSIX filename);
    and any id that is `.`, `..`, or starts with `.` (blocks both traversal segments and
    accidental dotfiles).
    """
    if not isinstance(window_id, str) or not window_id:
        raise SpoolError(f"window_id must be a non-empty string, got {window_id!r}")
    if "/" in window_id or (os.sep != "/" and os.sep in window_id) or "\x00" in window_id:
        raise SpoolError(f"window_id must not contain a path separator or NUL byte: {window_id!r}")
    if window_id.startswith("."):
        raise SpoolError(f"window_id must not start with '.': {window_id!r}")


class Spool:
    """The pcap spool: a capped, reconciled, thread-safe disk-usage tracker
    with admission backpressure. See the module docstring for the full
    reserved -> resident -> released lifecycle and the estimate/actual/
    overshoot design this exists to survive.

    `root` is created if it does not already exist. On construction, the
    spool **reconciles against the filesystem**: every `*.pcap` file already
    in `root` is summed into `usage()` as a `RESIDENT` entry keyed by its
    stem, before anything else runs. A resumed run that instead started from
    zero would blow straight past `cap_bytes` on the very first batch of new
    admissions, oblivious to everything a prior run left behind.

    **Resuming a run leaves orphans only `resident_windows()` can find.**
    Reconciliation accounts for a prior run's leftover pcaps but has no way
    to hand their `window_id`s back to a caller that never admitted them
    itself — `resident_windows()` (and the construction-time snapshot,
    `reconciled_windows`) is that enumeration. A resumed pipeline must walk
    `resident_windows()` before doing anything else, so it can feed those
    windows to Zeek and eventually `release()` them; skip that and the spool
    can fill with unreleasable RESIDENT entries that never came from this
    run's own `admit()` calls, and nothing in this module's `admit()` API
    alone can rediscover which ids they are.

    `stall_timeout`, if given, is the second half of that fix: even with
    `resident_windows()` available, a caller that forgets to drain it (or a
    reaper thread that has died) leaves `admit()` blocked with nothing
    RESERVED to ever wake it — indistinguishable, from inside `admit()`,
    from a spool that is simply making slow-but-real progress. After
    `stall_timeout` seconds with no commit/fail/release *and* nothing
    currently RESERVED, `admit()` raises `SpoolTimeoutError` instead of
    parking forever (see Guard 3 in `admit()`). `None` (the default)
    disables this — existing callers that rely on an unbounded wait keep
    that behavior unchanged; a caller wiring this up for an unattended,
    multi-day run should set a real value.
    """

    def __init__(
        self,
        root: Path,
        cap_bytes: int,
        *,
        estimator: Estimator | None = None,
        stall_timeout: float | None = None,
    ):
        if cap_bytes <= 0:
            raise SpoolConfigError(f"cap_bytes must be positive, got {cap_bytes}")
        if stall_timeout is not None and stall_timeout <= 0:
            raise SpoolConfigError(f"stall_timeout must be positive or None, got {stall_timeout}")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cap_bytes = cap_bytes
        self.stall_timeout = stall_timeout
        self._estimator = estimator if estimator is not None else SizeEstimator()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._entries: dict[str, _Entry] = {}
        self._reconcile()
        # A snapshot of exactly what `_reconcile()` inherited from a prior run, taken once here
        # before any admission can add to `_entries` — distinct from `resident_windows()`, which
        # reflects the live set (this run's own commits included) at call time.
        self.reconciled_windows: tuple[str, ...] = tuple(sorted(self._entries))
        # Updated on every commit/fail/release — Guard 3 in `admit()` compares against this to
        # decide whether the spool has genuinely stalled rather than just being slow.
        self._last_progress = time.monotonic()

    def _reconcile(self) -> None:
        """Sum what is actually in `root` into `usage()` as RESIDENT entries.
        Runs once, from `__init__`, before the spool is shared with any
        other thread — no lock needed here.
        """
        for path in sorted(self.root.glob("*.pcap")):
            if not path.is_file():
                continue
            window_id = path.stem
            size = path.stat().st_size
            self._entries[window_id] = _Entry(state=EntryState.RESIDENT, size=size)

    def _path_for(self, window_id: str) -> Path:
        path = self.root / f"{window_id}.pcap"
        # Belt-and-suspenders against a validation bug in `admit()` (or a future call site that
        # forgets to validate): a `window_id` that passed validation can never legitimately
        # produce a path whose parent isn't `root` — see `validate_window_id`.
        assert path.parent == self.root, f"constructed path escaped spool root: {path!r}"
        return path

    # --- accounting, read-only -------------------------------------------

    def usage(self) -> int:
        """Total bytes currently accounted for — the sum of every RESERVED
        entry's estimate and every RESIDENT entry's actual size. Never
        negative: it is a sum of non-negative integers by construction.
        """
        with self._cond:
            return self._usage_locked()

    def _usage_locked(self) -> int:
        return sum(e.size for e in self._entries.values())

    def available(self) -> int:
        """`cap_bytes - usage()`. Can go **negative** during overshoot — that
        is intentional (see module docstring §"overshoot"): a negative value
        is the deficit signal that tells `admit()` to keep blocking until
        the reaper has released enough to clear it, not an error state to
        hide.
        """
        with self._cond:
            return self.cap_bytes - self._usage_locked()

    def resident_windows(self) -> tuple[str, ...]:
        """The ids of every window currently tracked as RESIDENT, sorted for a stable order —
        both windows this run itself admitted and committed, and windows `_reconcile()` inherited
        from a prior interrupted run (see `reconciled_windows` for that snapshot specifically).
        This is the enumeration API a resumed run needs: without it, `_reconcile()`'s accounting
        has no way to hand back *which* `window_id`s it found, so nothing could ever feed them to
        Zeek and `release()` them (see the class docstring's "Resuming a run" section).
        """
        with self._cond:
            return tuple(
                sorted(wid for wid, entry in self._entries.items() if entry.state is EntryState.RESIDENT)
            )

    def verify(self) -> tuple[int, int]:
        """`(accounted_bytes, on_disk_bytes)`: `usage()`'s bookkeeping total versus the actual sum
        of every `*.pcap` file's real size in `root`, measured independently by re-globbing `root`
        the same way `_reconcile()` does — a bug in `usage()`'s own math can't also corrupt this
        cross-check. Lets a caller (H-4's periodic self-check, or a test) assert the two agree
        instead of only ever trusting `commit()`'s arithmetic. The two are expected to disagree
        transiently while a RESERVED entry's estimate has no file on disk yet — that is not itself
        a bug, just something a caller comparing the two numbers needs to know.
        """
        with self._cond:
            accounted = self._usage_locked()
        on_disk = sum(path.stat().st_size for path in self.root.glob("*.pcap") if path.is_file())
        return accounted, on_disk

    def state(self, window_id: str) -> EntryState | None:
        """The tracked state of `window_id`, or `None` if it is not
        currently tracked (never admitted, or already released).
        """
        with self._cond:
            entry = self._entries.get(window_id)
            return entry.state if entry is not None else None

    # --- admission ----------------------------------------------------

    def admit(self, window_id: str, *, timeout: float | None = None) -> Reservation:
        """Block until the current estimate for a new window fits under
        `cap_bytes`, then reserve that many bytes and return a `Reservation`.

        This *is* the backpressure the whole pipeline settles around: the
        extract pool blocks here, so the pipeline naturally runs at whatever
        stage is slowest, instead of racing ahead of the reaper.

        Raises `SpoolError` immediately (never blocks first) if `window_id`
        fails `validate_window_id` — see that function for exactly what is
        rejected and why (this module deletes files for a living; an
        unvalidated id is a path-traversal vector, not a naming nicety).
        Raises `SpoolConfigError` immediately (never blocks first) if the
        current estimate cannot fit even in a completely empty spool.
        Raises `SpoolDeadlockError` if, after waiting, the spool has fully
        drained (nothing reserved, nothing resident) and the request still
        cannot be admitted — the same condition, caught after the wait
        instead of before it (see `SpoolDeadlockError`). Raises
        `SpoolTimeoutError` if `timeout` elapses first, or if `stall_timeout`
        elapses with no in-flight reservation left that could ever unblock
        this call (Guard 3 — see the class docstring's "Resuming a run").
        """
        validate_window_id(window_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            if window_id in self._entries:
                raise SpoolError(
                    f"window {window_id!r} is already tracked "
                    f"(state={self._entries[window_id].state.value}); "
                    "admit() must not be called twice for the same window_id"
                )

            # Guard 1 — fail fast, before ever blocking: if the estimate
            # available *right now* cannot fit in an empty spool, no amount
            # of waiting will ever change that. Named explicitly so a
            # misconfigured cap_bytes reads as a config error, not a hang.
            initial_estimate = self._estimator.estimate()
            if initial_estimate > self.cap_bytes:
                raise SpoolConfigError(
                    f"window {window_id!r} is estimated at {initial_estimate} bytes, "
                    f"which cannot fit even in an empty spool "
                    f"(cap_bytes={self.cap_bytes}); this is a configuration error, "
                    "not transient pressure"
                )

            while True:
                estimate = self._estimator.estimate()
                if self.cap_bytes - self._usage_locked() >= estimate:
                    self._entries[window_id] = _Entry(
                        state=EntryState.RESERVED, size=estimate
                    )
                    return Reservation(
                        window_id=window_id,
                        path=self._path_for(window_id),
                        estimated_bytes=estimate,
                    )

                # Guard 2 — the same failure, surfaced after the fact: the
                # spool has fully drained (nothing left to release) and it
                # is still not enough. Raise instead of parking on the
                # condition variable forever.
                if not self._entries:
                    raise SpoolDeadlockError(
                        f"window {window_id!r} needs {estimate} bytes "
                        f"(cap_bytes={self.cap_bytes}) and nothing is resident or "
                        "reserved to release — this admission can never succeed"
                    )

                # Guard 3 — a resumed run's reconciled orphans (or any RESIDENT
                # entry) keep `_entries` non-empty forever unless something
                # RESERVED is in flight to eventually commit/fail, or a caller
                # is actively releasing residents it discovered via
                # `resident_windows()`. If nothing is RESERVED right now *and*
                # `stall_timeout` seconds have passed with no commit/fail/
                # release at all, this is exactly as dead as Guard 2's empty
                # spool — just with unreleasable residents sitting in it
                # instead of nothing. Only armed when `stall_timeout` is set;
                # see the class docstring.
                has_in_flight_reservation = any(
                    entry.state is EntryState.RESERVED for entry in self._entries.values()
                )
                stall_remaining = None
                if self.stall_timeout is not None and not has_in_flight_reservation:
                    stall_remaining = self.stall_timeout - (time.monotonic() - self._last_progress)
                    if stall_remaining <= 0:
                        # Build the resident list directly from `_entries` rather than calling
                        # `self.resident_windows()`: that method re-acquires `self._cond`, and
                        # `self._cond`/`self._lock` is a plain, non-reentrant `threading.Lock` —
                        # already held here, so calling it from inside this `with self._cond:`
                        # block would self-deadlock instead of raising.
                        resident_now = tuple(
                            sorted(
                                wid
                                for wid, e in self._entries.items()
                                if e.state is EntryState.RESIDENT
                            )
                        )
                        raise SpoolTimeoutError(
                            f"admit({window_id!r}) stalled for {self.stall_timeout}s with no "
                            "in-flight reservation and no commit/fail/release progress "
                            f"(usage={self._usage_locked()}, resident_windows="
                            f"{resident_now!r}) — nothing can free room without "
                            "external action; a resumed run must drain resident_windows() "
                            "before admitting new work"
                        )

                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise SpoolTimeoutError(
                        f"admit({window_id!r}) timed out after {timeout}s waiting for "
                        f"{estimate} bytes (cap_bytes={self.cap_bytes}, "
                        f"usage={self._usage_locked()})"
                    )
                if stall_remaining is not None:
                    remaining = stall_remaining if remaining is None else min(remaining, stall_remaining)
                self._cond.wait(timeout=remaining)

    # --- landing / abandoning a reservation -------------------------------

    def commit(self, window_id: str, actual_bytes: int | None = None) -> None:
        """Record that `window_id`'s pcap has landed at its reserved `path`:
        replaces the estimate with the actual size in `usage()`, feeds the
        observation back into the estimator, transitions the entry RESERVED
        -> RESIDENT, and wakes waiters (the actual size may be smaller than
        the estimate, freeing room).

        `actual_bytes`, if omitted (the default), is measured directly —
        `self._path_for(window_id).stat().st_size` — rather than trusted
        from the caller: a caller-supplied number that drifts from what
        actually landed on disk is exactly the kind of accounting error
        `verify()` exists to catch, and not trusting it here in the first
        place is cheaper than catching it later. Pass an explicit
        `actual_bytes` only when the caller has a reason to assert a
        specific value (e.g. a test).

        Raises `SpoolError` if `window_id` is not currently RESERVED (never
        admitted, already committed, or already released) — committing
        twice, or committing something that was never reserved, is a caller
        bug this must not silently accept. Raises `SpoolError` if an
        explicit `actual_bytes` is negative. Propagates `OSError` (e.g.
        `FileNotFoundError`) if `actual_bytes` is omitted and no file exists
        at the reserved path — the reservation stays RESERVED, retryable.
        """
        if actual_bytes is not None and actual_bytes < 0:
            raise SpoolError(f"actual_bytes must not be negative, got {actual_bytes}")
        with self._cond:
            entry = self._entries.get(window_id)
            if entry is None or entry.state is not EntryState.RESERVED:
                state = entry.state.value if entry is not None else "untracked"
                raise SpoolError(
                    f"cannot commit window {window_id!r}: state is {state!r}, "
                    "expected 'reserved' (was admit() called for this window_id?)"
                )
            if actual_bytes is None:
                actual_bytes = self._path_for(window_id).stat().st_size
            entry.state = EntryState.RESIDENT
            entry.size = actual_bytes
            self._estimator.observe(actual_bytes)
            self._last_progress = time.monotonic()
            self._cond.notify_all()

    def fail(self, window_id: str) -> None:
        """Release a reservation whose extraction never produced a file —
        frees the estimated bytes and wakes waiters. Idempotent: failing an
        already-untracked `window_id` (e.g. a resumed run replaying a
        failure it already recorded) is a no-op, not an error.

        Raises `SpoolError` if `window_id` is currently RESIDENT: a window
        that already landed successfully cannot retroactively "fail" its
        reservation — that state only ever moves forward via `release()`.
        """
        with self._cond:
            entry = self._entries.get(window_id)
            if entry is None:
                return
            if entry.state is not EntryState.RESERVED:
                raise SpoolError(
                    f"cannot fail window {window_id!r}: state is "
                    f"{entry.state.value!r}, not 'reserved' — its pcap already "
                    "landed, use release() (or leave it resident for a retry) instead"
                )
            del self._entries[window_id]
            self._last_progress = time.monotonic()
            self._cond.notify_all()

    def release(self, window_id: str, *, delete: bool = True) -> None:
        """Free a RESIDENT window's accounting because its consumer
        **succeeded**. `delete=True` (the default) also deletes the pcap;
        `delete=False` ("retain mode") frees the accounting but leaves the
        file on disk, for keeping a bad window around to inspect by hand.

        **Never call this for a failed consumer run.** A failed Zeek run
        must leave the pcap resident (simply don't call `release()`) so the
        window can be retried — that is what makes the pipeline resumable
        rather than lossy (see module docstring). This method has no way to
        know *why* a caller is releasing a window; that judgment belongs to
        the caller, not this module.

        Idempotent both ways: releasing an already-released (or never
        admitted) `window_id` is a no-op that never touches disk — and,
        when `window_id` *is* tracked, deleting a file that is already gone
        is not an error either (`unlink(missing_ok=True)`).

        **Deletes before untracking, both under the same lock.** A prior
        version freed the accounting first and deleted after releasing the
        lock — which let a woken `admit()` reserve the same `window_id`
        again (the duplicate guard sees it as untracked) and start writing
        to the path this call was about to `unlink`, corrupting a live
        extraction. Deleting first, still under the lock, closes that
        window entirely: `window_id` stays tracked (and therefore
        unreachable through `admit()`'s duplicate guard) until the delete
        has actually finished. It also means a failed delete (permission
        error, EIO — `unlink(missing_ok=True)` only swallows
        `FileNotFoundError`) propagates *before* the accounting is freed:
        the entry stays RESIDENT, still counted against the cap, and is
        retryable — never "deleted on disk but the cap thinks it's still
        there," which would silently let the cap be exceeded for the rest
        of the run.

        Raises `SpoolError` if `window_id` is currently RESERVED: its pcap
        has not landed yet, so there is nothing a consumer could have
        succeeded on. Use `fail()` to abandon an in-flight reservation
        instead — this is exactly the state-machine guard that makes
        "never evict unprocessed work" structural rather than a convention
        callers have to remember.
        """
        with self._cond:
            entry = self._entries.get(window_id)
            if entry is not None and entry.state is EntryState.RESERVED:
                raise SpoolError(
                    f"cannot release window {window_id!r}: state is 'reserved', "
                    "its pcap has not landed yet — use fail() to abandon an "
                    "in-flight reservation instead of release()"
                )
            if entry is None:
                return
            if delete:
                self._path_for(window_id).unlink(missing_ok=True)
            del self._entries[window_id]
            self._last_progress = time.monotonic()
            self._cond.notify_all()
