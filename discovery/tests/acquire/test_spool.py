"""Tests for aximprint.acquire.harvest.spool.

No test here opens a network socket or a real appliance (enforced by
`tests/conftest.py`) — this module is pure filesystem + threading, and every
test uses `tmp_path` for the spool root. Concurrency tests are deterministic:
ordering is established with locks/events, never `sleep`, except the one
timeout test, which is inherently time-based and kept short.
"""

import threading
import time
from pathlib import Path

import pytest

from aximprint.acquire.harvest.spool import (
    EntryState,
    Estimator,
    SizeEstimator,
    Spool,
    SpoolConfigError,
    SpoolDeadlockError,
    SpoolError,
    SpoolTimeoutError,
)


def _spool(tmp_path, cap_bytes, seed_bytes=50, **kwargs):
    return Spool(tmp_path, cap_bytes, estimator=SizeEstimator(seed_bytes=seed_bytes), **kwargs)


# --- Phase 1: accounting ---------------------------------------------------


def test_reconcile_finds_pre_existing_files(tmp_path):
    (tmp_path / "a.pcap").write_bytes(b"1" * 10)
    (tmp_path / "b.pcap").write_bytes(b"2" * 20)
    (tmp_path / "not-a-pcap.txt").write_bytes(b"ignore me")

    spool = Spool(tmp_path, cap_bytes=1000)

    assert spool.usage() == 30
    assert spool.available() == 970
    assert spool.state("a") is EntryState.RESIDENT
    assert spool.state("b") is EntryState.RESIDENT
    assert spool.state("not-a-pcap") is None


def test_usage_tracks_admit_commit_release_exactly(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    reservation = spool.admit("w1")
    assert reservation.estimated_bytes == 50
    assert spool.usage() == 50

    spool.commit("w1", 40)
    assert spool.usage() == 40

    spool.release("w1", delete=False)
    assert spool.usage() == 0


def test_failed_reservation_releases(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    spool.admit("w1")
    assert spool.usage() == 50

    spool.fail("w1")
    assert spool.usage() == 0
    assert spool.state("w1") is None


def test_usage_never_goes_negative_across_many_cycles(tmp_path):
    spool = _spool(tmp_path, cap_bytes=10_000, seed_bytes=100)

    for i in range(20):
        window_id = f"w{i}"
        spool.admit(window_id)
        # actual much smaller than the estimate — the case most likely to
        # expose an accounting bug that lets usage() dip below zero.
        spool.commit(window_id, 1)
        assert spool.usage() >= 0
        spool.release(window_id)
        assert spool.usage() >= 0

    assert spool.usage() == 0


def test_state_transitions_reserved_resident_released(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    assert spool.state("w1") is None
    spool.admit("w1")
    assert spool.state("w1") is EntryState.RESERVED
    spool.commit("w1", 10)
    assert spool.state("w1") is EntryState.RESIDENT
    spool.release("w1")
    assert spool.state("w1") is None


def test_admit_duplicate_window_id_raises(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")

    with pytest.raises(SpoolError):
        spool.admit("w1")


def test_commit_requires_reserved_state(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    with pytest.raises(SpoolError):
        spool.commit("never-admitted", 10)

    spool.admit("w1")
    spool.commit("w1", 5)
    with pytest.raises(SpoolError):
        spool.commit("w1", 5)  # already resident


# --- Phase 2: admission and backpressure -----------------------------------


def test_admit_blocks_and_then_admits_after_a_release(tmp_path):
    spool = _spool(tmp_path, cap_bytes=100, seed_bytes=50)
    spool.admit("a")
    spool.admit("a2")  # spool now exactly full: 50 + 50 == cap_bytes

    estimate_called = threading.Event()
    real_estimate = spool._estimator.estimate

    def _watched_estimate():
        value = real_estimate()
        estimate_called.set()
        return value

    spool._estimator.estimate = _watched_estimate

    results: list[object] = []

    def _admit_b():
        results.append(spool.admit("b"))

    estimate_called.clear()
    thread = threading.Thread(target=_admit_b)
    thread.start()
    # Waiting for this fires strictly while `b`'s admit() call still holds
    # the spool's lock (estimate() is only ever called under it), so the
    # release below cannot race ahead of `b` actually parking on the
    # condition variable — the lock itself serializes the two.
    assert estimate_called.wait(timeout=5), "background admit() never started"

    # This would pass even if backpressure did not exist at all — the assertions below only
    # check the *outcome*, not that admit('b') was actually made to wait for it. Acquiring the
    # spool's own lock here only succeeds once admit('b') has released it by parking on
    # `self._cond.wait()` (see the comment above), so seeing 'b' still absent from `_entries` at
    # this point is proof admission genuinely blocked rather than raced ahead.
    with spool._cond:
        assert "b" not in spool._entries, "admit('b') reserved before being unblocked by fail('a')"

    # `a` never landed a file — this is the same "free a reservation"
    # operation `fail()` exists for, and it must wake the blocked admit().
    spool.fail("a")

    thread.join(timeout=5)
    assert not thread.is_alive(), "admit('b') never unblocked after fail('a')"
    assert len(results) == 1
    assert results[0].window_id == "b"
    assert spool.state("b") is EntryState.RESERVED


def test_oversized_single_window_raises_config_error_without_blocking(tmp_path):
    spool = _spool(tmp_path, cap_bytes=100, seed_bytes=200)

    with pytest.raises(SpoolConfigError):
        spool.admit("too-big")

    # nothing was reserved on the way to the error
    assert spool.usage() == 0
    assert spool.state("too-big") is None


def test_admit_timeout_raises(tmp_path):
    spool = _spool(tmp_path, cap_bytes=100, seed_bytes=100)
    spool.admit("a")  # fills the spool exactly, nothing left for "b"

    with pytest.raises(SpoolTimeoutError):
        spool.admit("b", timeout=0.05)


class _SequenceEstimator:
    """Test-only estimator returning a scripted sequence of estimates —
    used to exercise the deadlock guard that fires *after* an initial
    estimate passed the entry-time check (`SpoolConfigError`'s guard),
    simulating the estimate growing (fed by concurrent commits, in real
    use) while a caller was about to check available room.
    """

    def __init__(self, values):
        self._values = list(values)

    def estimate(self):
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]

    def observe(self, actual_bytes):
        pass


def test_deadlock_guard_fires_when_drained_but_still_cannot_fit(tmp_path):
    # First call (the entry-time SpoolConfigError guard) sees 50, which
    # fits under cap_bytes=100. The very next call, inside the wait loop
    # against an otherwise-empty spool, sees 500 — which does not fit and
    # never will, since nothing is resident or reserved to release.
    spool = Spool(tmp_path, cap_bytes=100, estimator=_SequenceEstimator([50, 500]))

    with pytest.raises(SpoolDeadlockError):
        spool.admit("b")


def test_concurrent_admit_commit_release_keeps_usage_exact(tmp_path):
    # cap_bytes=250 with seed_bytes=100: only two reservations fit at once at the seed estimate
    # (100 + 100 = 200 <= 250, a third needs 100 more but only 50 remain) — the previous
    # cap_bytes=4_000 with 8 threads each holding at most one reservation (peak 800 bytes) never
    # actually contended, so this exercises real interleaving through admit()'s wait loop rather
    # than always finding room immediately.
    spool = _spool(tmp_path, cap_bytes=250, seed_bytes=100)
    n_threads = 8
    n_iterations = 50
    errors: list[BaseException] = []

    def worker(thread_id):
        try:
            for i in range(n_iterations):
                window_id = f"t{thread_id}-{i}"
                reservation = spool.admit(window_id, timeout=10)
                reservation.path.write_bytes(b"x" * 17)
                # No explicit actual_bytes: exercises commit()'s default of stat()-ing the real
                # file that was actually written, under real concurrency, not just a caller's
                # trusted number.
                spool.commit(reservation.window_id)
                spool.release(reservation.window_id, delete=True)
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert all(not t.is_alive() for t in threads)
    assert spool.usage() == sum(f.stat().st_size for f in tmp_path.glob("*.pcap")) == 0
    assert spool.available() == spool.cap_bytes


# --- Phase 3: the estimator -------------------------------------------------


def test_estimator_seed_applies_when_nothing_observed():
    estimator = SizeEstimator(seed_bytes=12_345)
    assert estimator.estimate() == 12_345


def test_estimator_adapts_to_observed_sizes():
    estimator = SizeEstimator(seed_bytes=1)
    running_max = 1
    for size in (10, 200, 50, 30, 5):
        assert estimator.estimate() == running_max
        estimator.observe(size)
        running_max = max(running_max, size)
    assert estimator.estimate() == running_max


def test_estimator_raises_estimate_for_a_sequence_of_large_windows(tmp_path):
    estimator = SizeEstimator(seed_bytes=10)
    sizes = [100, 50, 300, 20, 500]
    for size in sizes:
        estimator.observe(size)
    # never below the largest single window actually observed — a mean
    # would land well under 500 here and would have repeatedly overshot on
    # anything sized like the later, larger windows.
    assert estimator.estimate() == max(sizes)
    assert estimator.estimate() > sum(sizes) / len(sizes)


def test_estimator_history_is_bounded_so_a_spike_eventually_ages_out():
    estimator = SizeEstimator(seed_bytes=1, history=3)
    estimator.observe(1_000)  # one-off spike
    estimator.observe(10)
    estimator.observe(10)
    estimator.observe(10)  # the 1_000 has now aged out of the 3-item window
    assert estimator.estimate() == 10


def test_estimator_rejects_nonpositive_construction_args():
    with pytest.raises(ValueError):
        SizeEstimator(seed_bytes=0)
    with pytest.raises(ValueError):
        SizeEstimator(history=0)


def test_estimator_rejects_negative_observation():
    estimator = SizeEstimator(seed_bytes=1)
    with pytest.raises(ValueError):
        estimator.observe(-1)


# --- Phase 4: release and reap ----------------------------------------------


def test_release_deletes_file_and_frees_accounting(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 123)
    spool.commit("w1", 123)

    spool.release("w1")

    assert spool.usage() == 0
    assert spool.state("w1") is None
    assert not reservation.path.exists()


def test_release_retain_mode_frees_accounting_without_deleting(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 123)
    spool.commit("w1", 123)

    spool.release("w1", delete=False)

    assert spool.usage() == 0
    assert spool.state("w1") is None
    assert reservation.path.exists()
    assert reservation.path.read_bytes() == b"x" * 123


def test_double_release_is_idempotent(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x")
    spool.commit("w1", 1)

    spool.release("w1")
    spool.release("w1")  # must not raise

    assert spool.usage() == 0
    assert not reservation.path.exists()


def test_release_of_an_already_gone_file_is_not_an_error(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")
    spool.commit("w1", 1)  # note: no file was ever actually written to disk

    spool.release("w1")  # unlink(missing_ok=True) must swallow this cleanly


def test_release_before_commit_raises_and_keeps_the_reservation(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")

    with pytest.raises(SpoolError):
        spool.release("w1")

    # never evicted: the reservation is exactly as it was before the call
    assert spool.state("w1") is EntryState.RESERVED
    assert spool.usage() == 50


def test_fail_on_a_resident_window_raises(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")
    spool.commit("w1", 10)

    with pytest.raises(SpoolError):
        spool.fail("w1")

    # the already-landed pcap is untouched by the rejected fail() call
    assert spool.state("w1") is EntryState.RESIDENT
    assert spool.usage() == 10


def test_fail_on_an_untracked_window_is_a_noop(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.fail("never-admitted")  # must not raise


def test_a_failed_consumer_run_leaves_the_pcap_resident(tmp_path):
    """The non-negotiable invariant: a failed Zeek run keeps its pcap. There
    is no dedicated API for "consumer failed" — the caller simply never
    calls `release()`, and the window stays exactly as resident (and
    counted against the cap) as it was after `commit()`.
    """
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"y" * 42)
    spool.commit("w1", 42)

    # ... consumer (Zeek) runs and fails; nothing is called here at all ...

    assert spool.state("w1") is EntryState.RESIDENT
    assert spool.usage() == 42
    assert reservation.path.exists()


# --- Phase 5: resuming a run (finding 1 — a resumed run must not hang forever) ------------------


def test_reconcile_snapshot_and_resident_windows_agree_on_a_fresh_spool(tmp_path):
    (tmp_path / "a.pcap").write_bytes(b"1" * 10)
    (tmp_path / "b.pcap").write_bytes(b"2" * 20)

    spool = Spool(tmp_path, cap_bytes=1000)

    assert spool.reconciled_windows == ("a", "b")
    assert spool.resident_windows() == ("a", "b")


def test_resident_windows_is_the_enumeration_api_reconciliation_was_missing(tmp_path):
    """Before `resident_windows()` existed, nothing could discover *which* ids `_reconcile()`
    found — only that some number of bytes were accounted for. This is the fix: a resumed run can
    now walk exactly the ids a prior run left behind and feed them to Zeek/`release()` them.
    """
    (tmp_path / "orphan-1.pcap").write_bytes(b"x" * 5)
    (tmp_path / "orphan-2.pcap").write_bytes(b"y" * 7)

    spool = Spool(tmp_path, cap_bytes=1000)
    orphans = spool.resident_windows()

    assert orphans == ("orphan-1", "orphan-2")
    # Every orphan is releasable through the ordinary API once discovered this way.
    for window_id in orphans:
        spool.release(window_id, delete=False)
    assert spool.usage() == 0


def test_resident_windows_includes_both_reconciled_and_freshly_committed(tmp_path):
    (tmp_path / "old.pcap").write_bytes(b"z" * 3)
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    reservation = spool.admit("new")
    reservation.path.write_bytes(b"n" * 9)
    spool.commit("new")

    assert spool.resident_windows() == ("new", "old")
    # `reconciled_windows` stays frozen to what construction found, unlike the live
    # `resident_windows()` — this run's own "new" commit must not retroactively appear in it.
    assert spool.reconciled_windows == ("old",)


def test_admit_with_stall_timeout_raises_instead_of_hanging_forever_on_reconciled_orphans(tmp_path):
    """The exact scenario finding 1 describes: a resumed run's spool is near-full with RESIDENT
    orphans `_reconcile()` inherited, nothing is RESERVED (no in-flight extraction could ever
    commit/fail to free room), and the caller never drains `resident_windows()`. Without
    `stall_timeout`, `admit()` would block on `self._cond.wait(timeout=None)` forever. With it,
    `admit()` must return control (as `SpoolTimeoutError`) well within a bounded wait, not hang.
    """
    (tmp_path / "orphan.pcap").write_bytes(b"x" * 90)  # near-full: cap is 100

    spool = Spool(tmp_path, cap_bytes=100, estimator=SizeEstimator(seed_bytes=50), stall_timeout=0.2)

    with pytest.raises(SpoolTimeoutError):
        spool.admit("new-window")  # note: no per-call `timeout=` — only stall_timeout bounds this


def test_admit_stall_timeout_does_not_fire_while_a_reservation_is_in_flight(tmp_path):
    """Guard 3 must not fire just because nothing has changed recently — only when nothing
    RESERVED could *ever* change it. A reservation that is legitimately still in flight (its
    extraction just hasn't finished yet) must let `admit()` keep waiting past `stall_timeout`,
    exactly as it would with `stall_timeout=None`.
    """
    # cap_bytes=60: "a" (estimate 50) leaves only 10 bytes available, not enough for "b" — so
    # "b" actually blocks in the wait loop rather than being admitted immediately.
    spool = Spool(tmp_path, cap_bytes=60, estimator=SizeEstimator(seed_bytes=50), stall_timeout=0.1)
    spool.admit("a")  # RESERVED, never committed or failed — legitimately in flight

    with pytest.raises(SpoolTimeoutError) as exc_info:
        spool.admit("b", timeout=0.5)  # the per-call timeout fires, not a spurious stall error
    # A stall-triggered error names 'nothing is RESERVED'-style reasoning; the per-call timeout
    # error is the ordinary "timed out after Ns waiting for" message — assert we got that one.
    assert "timed out after" in str(exc_info.value)


def test_admit_stall_timeout_resets_on_progress(tmp_path):
    """A `release()` (or commit/fail) must reset the stall clock — a spool that is slowly but
    genuinely progressing must not be mistaken for a dead one just because `stall_timeout` is
    short relative to the total wait.
    """
    spool = Spool(tmp_path, cap_bytes=100, estimator=SizeEstimator(seed_bytes=100), stall_timeout=0.3)
    spool.admit("a")  # fills the spool exactly

    def _release_after_a_moment():
        time.sleep(0.15)  # less than stall_timeout: should reset the clock, not exceed it
        spool.fail("a")

    releaser = threading.Thread(target=_release_after_a_moment)
    releaser.start()
    reservation = spool.admit("b", timeout=5)  # must succeed, not raise SpoolTimeoutError
    releaser.join(timeout=5)

    assert reservation.window_id == "b"


def test_default_stall_timeout_is_none_and_never_fires(tmp_path):
    """Backward compatibility: existing callers that never pass `stall_timeout` keep their
    current (unbounded, per-call-`timeout`-only) behavior — Guard 3 must be entirely inert.
    """
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    assert spool.stall_timeout is None


def test_stall_timeout_must_be_positive(tmp_path):
    with pytest.raises(SpoolConfigError):
        Spool(tmp_path, cap_bytes=1000, stall_timeout=0)
    with pytest.raises(SpoolConfigError):
        Spool(tmp_path, cap_bytes=1000, stall_timeout=-1)


# --- Phase 6: release() ordering (finding 2 — delete before untracking, both under the lock) ----


def test_release_deletes_before_untracking_so_a_failed_unlink_keeps_the_entry_resident(
    tmp_path, monkeypatch
):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 10)
    spool.commit("w1", 10)

    def _boom(self, missing_ok=False):
        raise PermissionError("simulated EIO")

    monkeypatch.setattr(Path, "unlink", _boom)

    with pytest.raises(PermissionError):
        spool.release("w1")

    # A failed delete must NOT free the accounting: the bytes are still on disk, so the cap must
    # still count them, and the window must remain retryable (release() can be called again).
    assert spool.state("w1") is EntryState.RESIDENT
    assert spool.usage() == 10
    assert reservation.path.exists()


def test_release_holds_the_lock_across_the_delete_so_admit_cannot_race_it(tmp_path):
    """The concrete race finding 2 describes: the old `release()` freed the accounting *before*
    deleting, under a separately-released lock — a woken `admit()` could reserve the same
    `window_id` again (the duplicate guard sees it as untracked) and start writing to the very
    path `release()` was about to `unlink`, corrupting a live extraction. Deleting first, still
    under the lock, means `window_id` stays tracked — and therefore rejected by admit()'s
    duplicate guard — for the entire duration of the delete.
    """
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 10)
    spool.commit("w1", 10)

    unlink_entered = threading.Event()
    allow_unlink_to_finish = threading.Event()
    real_unlink = Path.unlink

    def _slow_unlink(self, missing_ok=False):
        if self == reservation.path:
            unlink_entered.set()
            assert allow_unlink_to_finish.wait(timeout=5), "test itself stalled"
        return real_unlink(self, missing_ok=missing_ok)

    release_errors: list[BaseException] = []

    def _release():
        try:
            spool.release("w1")
        except BaseException as exc:  # noqa: BLE001 - surfaced via the assertion below
            release_errors.append(exc)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "unlink", _slow_unlink)
        release_thread = threading.Thread(target=_release)
        release_thread.start()
        assert unlink_entered.wait(timeout=5), "release() never reached unlink()"

        admit_results: list[object] = []

        def _admit_again():
            admit_results.append(spool.admit("w1"))

        admit_thread = threading.Thread(target=_admit_again)
        admit_thread.start()
        # admit('w1') must still be blocked (on the lock release() holds across the delete) —
        # not raced ahead to reserve the same id while the old file is still being deleted.
        admit_thread.join(timeout=0.3)
        assert admit_thread.is_alive(), "admit('w1') proceeded before release() finished deleting"

        allow_unlink_to_finish.set()
        release_thread.join(timeout=5)
        admit_thread.join(timeout=5)

    assert not release_errors, release_errors
    assert not admit_thread.is_alive()
    assert admit_results and admit_results[0].window_id == "w1"


# --- Phase 7: window_id validation (finding 4 — a path-traversal window_id must be rejected) ----


@pytest.mark.parametrize(
    "bad_id",
    ["", ".", "..", "/etc/passwd", "a/b", "../outside/important", "..outside", ".hidden"],
)
def test_admit_rejects_unsafe_window_ids(tmp_path, bad_id):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    with pytest.raises(SpoolError):
        spool.admit(bad_id)
    # nothing was reserved on the way to the error
    assert spool.usage() == 0


def test_admit_rejects_a_window_id_that_would_escape_root_and_never_returns_a_path_outside_it(
    tmp_path,
):
    outside_marker = tmp_path.parent / "outside-important-marker.pcap"
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)

    with pytest.raises(SpoolError):
        reservation = spool.admit("../outside-important-marker")
        # unreachable if the fix holds, but pins the pre-fix failure mode precisely if it regresses
        assert reservation.path == outside_marker  # pragma: no cover


def test_release_of_a_never_admitted_traversal_id_never_touches_disk(tmp_path):
    outside = tmp_path.parent / "outside-important.pcap"
    outside.write_bytes(b"do not delete me")

    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.release("../outside-important")  # never admitted: must be a pure no-op

    assert outside.exists()
    assert outside.read_bytes() == b"do not delete me"


# --- Phase 8: error hierarchy (finding 6 — every raise must be an XtractError, LES-041) ---------


def test_spool_error_is_also_a_value_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(SpoolError, XtractError)
    assert issubclass(SpoolError, ValueError)


@pytest.mark.parametrize("bad_kwargs", [{"seed_bytes": 0}, {"seed_bytes": -1}, {"history": 0}])
def test_size_estimator_construction_errors_are_spool_errors(bad_kwargs):
    with pytest.raises(SpoolError):
        SizeEstimator(**bad_kwargs)


def test_size_estimator_observe_negative_is_a_spool_error():
    estimator = SizeEstimator(seed_bytes=1)
    with pytest.raises(SpoolError):
        estimator.observe(-1)


def test_commit_negative_actual_bytes_is_a_spool_error(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")
    with pytest.raises(SpoolError):
        spool.commit("w1", -1)


# --- Phase 9: commit() defaults to the real on-disk size, and verify() (finding 8) --------------


def test_commit_without_actual_bytes_stats_the_real_file(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 37)

    spool.commit("w1")  # no actual_bytes: must measure the file, not trust a caller's number

    assert spool.usage() == 37
    assert spool.state("w1") is EntryState.RESIDENT


def test_commit_without_actual_bytes_and_no_file_propagates_oserror(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    spool.admit("w1")  # reservation.path never written

    with pytest.raises(OSError):
        spool.commit("w1")

    # the reservation stays exactly as it was — retryable, not silently RESIDENT with a lie
    assert spool.state("w1") is EntryState.RESERVED


def test_commit_explicit_actual_bytes_still_overrides_the_default(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 37)

    spool.commit("w1", 99)  # explicit value wins even though it disagrees with the real file

    assert spool.usage() == 99


def test_verify_matches_when_everything_is_committed_and_on_disk(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 21)
    spool.commit("w1")

    accounted, on_disk = spool.verify()
    assert accounted == on_disk == 21


def test_verify_detects_a_deliberately_wrong_explicit_commit(tmp_path):
    spool = _spool(tmp_path, cap_bytes=1000, seed_bytes=50)
    reservation = spool.admit("w1")
    reservation.path.write_bytes(b"x" * 21)
    spool.commit("w1", 999)  # deliberately wrong, to prove verify() catches the disagreement

    accounted, on_disk = spool.verify()
    assert accounted == 999
    assert on_disk == 21
    assert accounted != on_disk


# --- Phase 10: the Estimator protocol (finding 18) -----------------------------------------------


class _SequenceEstimatorForProtocolCheck:
    def estimate(self):
        return 1

    def observe(self, actual_bytes):
        pass


def test_duck_typed_estimator_satisfies_the_estimator_protocol():
    assert isinstance(_SequenceEstimatorForProtocolCheck(), Estimator)


def test_size_estimator_satisfies_the_estimator_protocol():
    assert isinstance(SizeEstimator(), Estimator)


def test_sequence_estimator_test_double_satisfies_the_estimator_protocol():
    assert isinstance(_SequenceEstimator([1]), Estimator)
