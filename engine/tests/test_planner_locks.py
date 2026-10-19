"""Cross-process resource-locking tests (PLANNER §6.2/§8.4).

Covers the three pieces of the correctness layer ratified 2026-06-03:

  - StateStore leases (acquire / refuse-while-valid / takeover-on-expiry /
    renew / release).
  - StateStore resource locks (all-or-nothing acquire, cross-deploy refusal,
    expiry reclaim, release by op and by deploy).
  - locks.correctness_lock_keys (only correctness categories, $var resolution,
    deterministic global order) and locks.acquire_blocking (poll-until-held,
    timeout).
  - Heartbeat keeping a lease/lock alive past its TTL.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.ops.registry import OpKind
from axrange.planner.locks import (
    CORRECTNESS_PREFIXES,
    Heartbeat,
    LockResolutionError,
    acquire_blocking,
    correctness_lock_keys,
)
from axrange.planner.state import StateStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = StateStore(Path(tmp) / "state.db")
        yield s
        s.close()


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "state.db")


# ---------------------------------------------------------------------------
# Leases (§6.2)
# ---------------------------------------------------------------------------


def test_acquire_lease_then_refuse_second_holder(store):
    r1 = store.acquire_lease("scn", "deploy-1", now=1000.0, ttl_s=300.0)
    assert r1.acquired

    # A different live process for the same scenario is refused.
    r2 = store.acquire_lease("scn", "deploy-2", now=1010.0, ttl_s=300.0)
    assert not r2.acquired
    assert r2.held_by_deploy_id == "deploy-1"


def test_acquire_lease_refused_even_for_same_deploy_id_while_valid(store):
    """A fresh process must not assume it owns a still-valid lease just because
    the deploy_id matches — that means another live process holds it."""
    store.acquire_lease("scn", "deploy-1", now=1000.0, ttl_s=300.0)
    again = store.acquire_lease("scn", "deploy-1", now=1010.0, ttl_s=300.0)
    assert not again.acquired


def test_lease_takeover_after_expiry(store):
    store.acquire_lease("scn", "deploy-1", now=1000.0, ttl_s=300.0)
    # Past expiry (1000 + 300): a new deploy reclaims it.
    r = store.acquire_lease("scn", "deploy-2", now=1400.0, ttl_s=300.0)
    assert r.acquired
    assert store.get_lease("scn").deploy_id == "deploy-2"


def test_renew_lease_extends_only_for_holder(store):
    store.acquire_lease("scn", "deploy-1", now=1000.0, ttl_s=300.0)
    assert store.renew_lease("scn", "deploy-1", now=1100.0, ttl_s=300.0)
    assert store.get_lease("scn").expires_at == pytest.approx(1400.0)
    # A non-holder's renew is a no-op.
    assert not store.renew_lease("scn", "deploy-2", now=1100.0, ttl_s=300.0)


def test_release_lease(store):
    store.acquire_lease("scn", "deploy-1", now=1000.0)
    store.release_lease("scn", "deploy-1")
    assert store.get_lease("scn") is None
    # After release a new holder gets it immediately, no expiry wait.
    assert store.acquire_lease("scn", "deploy-2", now=1001.0).acquired


# ---------------------------------------------------------------------------
# Resource locks (§8.4)
# ---------------------------------------------------------------------------


def test_acquire_resource_locks_all_or_nothing(store):
    # deploy-1 holds vm:100.
    assert store.acquire_resource_locks(
        ["vm:100"], "deploy-1", "op-a", now=1000.0, ttl_s=300.0
    )
    # deploy-2 wants {ovs:n1, vm:100}: vm:100 is taken, so it gets NEITHER.
    assert not store.acquire_resource_locks(
        ["ovs:n1", "vm:100"], "deploy-2", "op-b", now=1010.0, ttl_s=300.0
    )
    held = {r.resource_key: r.deploy_id for r in store.held_resource_locks()}
    assert held == {"vm:100": "deploy-1"}  # ovs:n1 was never written


def test_resource_lock_reclaim_after_expiry(store):
    store.acquire_resource_locks(["vm:100"], "deploy-1", "op-a", now=1000.0, ttl_s=300.0)
    # Another deploy reclaims it past expiry.
    assert store.acquire_resource_locks(
        ["vm:100"], "deploy-2", "op-b", now=1400.0, ttl_s=300.0
    )
    assert store.held_resource_locks()[0].deploy_id == "deploy-2"


def test_same_deploy_can_reacquire_its_own_lock(store):
    assert store.acquire_resource_locks(["vm:100"], "d1", "op-a", now=1000.0)
    # Same deploy, different op — allowed (idempotent upsert).
    assert store.acquire_resource_locks(["vm:100"], "d1", "op-b", now=1001.0)
    assert store.held_resource_locks()[0].op_id == "op-b"


def test_release_resource_locks_for_op(store):
    store.acquire_resource_locks(["vm:100", "ovs:n1"], "d1", "op-a", now=1000.0)
    store.acquire_resource_locks(["gretap:s1"], "d1", "op-b", now=1000.0)
    assert store.release_resource_locks_for_op("op-a") == 2
    remaining = [r.resource_key for r in store.held_resource_locks()]
    assert remaining == ["gretap:s1"]


def test_release_resource_locks_for_deploy(store):
    store.acquire_resource_locks(["vm:100"], "d1", "op-a", now=1000.0)
    store.acquire_resource_locks(["vm:200"], "d1", "op-b", now=1000.0)
    store.acquire_resource_locks(["vm:300"], "d2", "op-c", now=1000.0)
    assert store.release_resource_locks_for_deploy("d1") == 2
    assert [r.deploy_id for r in store.held_resource_locks()] == ["d2"]


def test_renew_resource_locks_extends_holder_rows(store):
    store.acquire_resource_locks(["vm:100", "ovs:n1"], "d1", "op-a", now=1000.0, ttl_s=300.0)
    assert store.renew_resource_locks("d1", now=1200.0, ttl_s=300.0) == 2
    for r in store.held_resource_locks("d1"):
        assert r.expires_at == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# correctness_lock_keys resolver
# ---------------------------------------------------------------------------


def _op(name, scope, resource_categories, *, catalog_id="OP-X"):
    return OpInstance(
        op_id=f"plan:{name}",
        op_name=name,
        op_catalog_id=catalog_id,
        kind=OpKind.ACTION,
        scope=scope,
        resource_categories=tuple(resource_categories),
    )


def test_resolver_filters_to_correctness_categories_only():
    cluster = ClusterContext.single_node("node-3", sensor_transport_ip="10.0.0.1")
    op = _op(
        "attach-vm-to-vnet",
        {"host": "h-a"},
        ("proxmox-cluster", "proxmox-node:$target_node", "vm:$vmid", "ovs:$target_node"),
    )
    resolved = {"vmid": 1204, "target_node": "node-3"}
    keys = correctness_lock_keys(op, resolved, cluster)
    # proxmox-cluster / proxmox-node:* (throughput) are excluded; correctness
    # keys come back sorted.
    assert keys == ["ovs:node-3", "vm:1204"]


def test_resolver_resolves_sensor_from_cluster_default():
    cluster = ClusterContext.single_node("node-3", sensor_transport_ip="10.0.0.1")
    # cluster.sensor_id defaults to "default".
    op = _op(
        "attach-gre-tunnel",
        {"net": "corp"},
        ("proxmox-cluster", "ovs:$target_node", "gretap:$sensor"),
    )
    resolved = {"target_node": "node-3"}
    keys = correctness_lock_keys(op, resolved, cluster)
    assert keys == ["gretap:default", "ovs:node-3"]


def test_resolver_resolved_inputs_override_scope():
    cluster = ClusterContext.single_node("node-3")
    op = _op("o", {"vmid": "scope-val"}, ("vm:$vmid",))
    keys = correctness_lock_keys(op, {"vmid": 999}, cluster)
    assert keys == ["vm:999"]  # resolved input wins over scope


def test_resolver_raises_on_unresolvable_var():
    cluster = ClusterContext.single_node("node-3")
    op = _op("o", {"host": "h-a"}, ("vm:$vmid",))
    with pytest.raises(LockResolutionError):
        correctness_lock_keys(op, {}, cluster)  # no vmid anywhere


def test_correctness_prefixes_are_the_fixed_at_one_categories():
    # Guards against drift: the three "serialization is correctness" categories
    # (PLANNER §8.2) and nothing else.
    assert set(CORRECTNESS_PREFIXES) == {"ovs:", "vm:", "gretap:"}


# ---------------------------------------------------------------------------
# acquire_blocking
# ---------------------------------------------------------------------------


def test_acquire_blocking_succeeds_immediately(store):
    slept: list[float] = []
    ok = acquire_blocking(
        store, ["vm:1"], "d1", "op-a",
        sleep_fn=slept.append, poll_s=1.0, timeout_s=5.0,
    )
    assert ok
    assert slept == []  # got it on the first try, no polling


def test_acquire_blocking_times_out_when_held(store):
    store.acquire_resource_locks(["vm:1"], "other", "op-z", ttl_s=10_000.0)
    slept: list[float] = []
    ok = acquire_blocking(
        store, ["vm:1"], "d1", "op-a",
        sleep_fn=slept.append, poll_s=1.0, timeout_s=3.0,
    )
    assert not ok
    # Polled until the accumulated wait hit the timeout.
    assert sum(slept) >= 3.0


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_heartbeat_keeps_lease_and_locks_alive(db_path):
    """A short TTL would expire mid-deploy; the heartbeat renews it so a
    contender still sees a valid lease/lock."""
    writer = StateStore(db_path)
    writer.acquire_lease("scn", "d1", ttl_s=2.0)
    writer.acquire_resource_locks(["vm:1"], "d1", "op-a", ttl_s=2.0)

    hb = Heartbeat(db_path, deploy_id="d1", scenario_id="scn", interval_s=0.2, ttl_s=2.0)
    hb.start()
    try:
        time.sleep(1.0)  # several heartbeat intervals
        reader = StateStore(db_path)
        now = time.time()
        lease = reader.get_lease("scn")
        assert lease is not None and lease.expires_at > now
        lock = reader.held_resource_locks("d1")
        assert lock and lock[0].expires_at > now
        reader.close()
    finally:
        hb.stop()
        writer.close()


# ---------------------------------------------------------------------------
# Split-brain guard: Heartbeat.lost (code-review finding #2, 2026-07-07).
# `_loop` used to discard the return of `renew_lease`/`renew_resource_locks`
# entirely, so a stall past TTL let a second `axrange deploy` of the same
# scenario take over while this process kept writing. `lost` is the signal
# a caller (Executor(..., lost_event=heartbeat.lost)) polls to abort.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_heartbeat_sets_lost_when_lease_renewal_fails(db_path):
    """A lease renewal for a deploy_id that doesn't hold the lease (lost it,
    or never had it — same observable failure) must set `lost` promptly."""
    other = StateStore(db_path)
    other.acquire_lease("scn", "other-deploy", ttl_s=10_000.0)

    hb = Heartbeat(db_path, deploy_id="d1", scenario_id="scn",
                   interval_s=0.1, ttl_s=2.0)
    hb.start()
    try:
        assert hb.lost.wait(timeout=2.0), (
            "lost should fire once renew_lease returns False"
        )
    finally:
        hb.stop()
        other.close()


@pytest.mark.slow
def test_heartbeat_does_not_set_lost_while_lease_and_locks_are_healthy(db_path):
    """The common case: nothing reclaimed anything -> lost never fires."""
    writer = StateStore(db_path)
    writer.acquire_lease("scn", "d1", ttl_s=10_000.0)
    writer.acquire_resource_locks(["vm:1"], "d1", "op-a", ttl_s=10_000.0)

    hb = Heartbeat(db_path, deploy_id="d1", scenario_id="scn",
                   interval_s=0.1, ttl_s=10_000.0)
    hb.start()
    try:
        time.sleep(0.5)  # several ticks
        assert not hb.lost.is_set()
    finally:
        hb.stop()
        writer.close()


@pytest.mark.slow
def test_heartbeat_does_not_set_lost_when_a_resource_key_changes_holder(db_path):
    """A per-resource-key holder change must NOT set `lost` (2026-07-07
    follow-up review, fixing the racy first cut of this guard). Correctness
    resource keys (`ovs:<node>`, `vm:<vmid>`, `gretap:<sensor>`) are SHARED
    across scenarios by design — a concurrent, entirely legitimate deploy of
    a DIFFERENT scenario acquiring a key right after we released it (e.g. our
    executor moved past the op that held it) is normal, not split-brain. Only
    a lease-renewal failure (this test's sibling) is the race-free loss
    signal; a same-key different-holder transition observed here must be
    tolerated silently."""
    writer = StateStore(db_path)
    writer.acquire_lease("scn", "d1", ttl_s=10_000.0)
    writer.acquire_resource_locks(["vm:1"], "d1", "op-a", ttl_s=10_000.0)

    hb = Heartbeat(db_path, deploy_id="d1", scenario_id="scn",
                   interval_s=0.15, ttl_s=10_000.0)
    hb.start()
    try:
        time.sleep(0.25)  # let >=1 tick pass

        # Simulate a concurrent different-scenario deploy legitimately taking
        # the shared key after we released it (the DB row simply reassigns —
        # a real caller would `release_resource_locks_for_op` first, but the
        # observable state this test guards against is the same).
        reclaimer = StateStore(db_path)
        reclaimer._conn.execute(
            "UPDATE resource_locks SET deploy_id = ?, op_id = ? "
            "WHERE resource_key = ?",
            ("other-deploy", "other-op", "vm:1"),
        )
        reclaimer.close()

        time.sleep(0.4)  # let several more ticks pass
        assert not hb.lost.is_set(), (
            "a per-key holder change on a shared correctness lock must not "
            "trip the split-brain guard — only lease-renewal failure should"
        )
    finally:
        hb.stop()
        writer.close()
