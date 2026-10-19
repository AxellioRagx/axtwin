"""Op registry tests: catalog totals, idempotency-class constraints,
cleanup-strategy expectations."""
from __future__ import annotations

from axrange.ops import (
    CleanupStrategy,
    IdempotencyClass,
    OpDefinition,
    OpKind,
    list_actions_and_waits,
    list_barriers,
)


def test_action_wait_count() -> None:
    # +OP-MIR-050 deploy-sensor (sensors Phase B); +OP-HOST-100 configure-routing (fabric slice);
    # +OP-OVL-070 install-agent (A3 Stage A -- exporter/adversary default agents, 2026-07-11);
    # +OP-TFC-010 drive-persona (Traffic tier, 2026-07-24);
    # -OP-MIR-005 record-tap-intent (RETIRED, docs/WORK_ORDER_tap_eligibility.md,
    # Authority: user, 2026-07-28 -- the twin substrate is tap-agnostic; a te
    # deploy emits no OP-MIR op at all any more)
    # +OP-TFC-020 assign-role (role/persona decoupling,
    # docs/WORK_ORDER_role_persona_tier.md, Authority: user, 2026-08-04)
    assert len(list_actions_and_waits()) == 34


def test_barrier_count() -> None:
    assert len(list_barriers()) == 6  # +OP-BAR-060 fabric-ready (fabric slice)


def test_only_run_script_is_escalate() -> None:
    escalators = [
        op for op in list_actions_and_waits()
        if op.cleanup_strategy == CleanupStrategy.ESCALATE
    ]
    assert len(escalators) == 1
    assert escalators[0].name == "run-script"


def test_action_ops_have_idempotency_class() -> None:
    for op in list_actions_and_waits():
        if op.kind == OpKind.ACTION:
            assert op.idempotency_class is not None, (
                f"{op.id} ({op.name}) is an action but has no idempotency_class"
            )


def test_wait_ops_have_no_cleanup_and_route_infra_or_config() -> None:
    """Waits never declare a cleanup_strategy (there's nothing to undo). They
    route to the infra adapter (idempotency_class=None) by default; the one
    exception is a wait that polls *in-guest* state over the QGA config channel
    (wait-actor-ready), which is config-class so the executor dispatches it to
    the ConfigBackend (docs/SLICE_ACTIVITY.md)."""
    for op in list_actions_and_waits():
        if op.kind == OpKind.WAIT:
            assert op.cleanup_strategy is None, op.id
            assert op.idempotency_class in (None, IdempotencyClass.CONFIG), op.id


def test_infra_ops_are_proxmox_or_ovs() -> None:
    """Sanity: every infra-class op touches proxmox-cluster, an OVS bridge, a
    VM, or the kernel gretap. No infra op should run through the ansible-pool.
    """
    for op in list_actions_and_waits():
        if op.idempotency_class != IdempotencyClass.INFRA:
            continue
        assert "ansible-pool" not in op.resource_categories, (
            f"{op.id} ({op.name}) is infra-class but uses ansible-pool"
        )


def test_config_ops_use_ansible_pool() -> None:
    """config-class ACTION ops run through the QGA/ansible throughput pool. A
    config-class WAIT (wait-actor-ready) only polls a completion marker, so it
    must NOT hold the ansible-pool throughput slot for its whole timeout —
    otherwise a long poll would serialize against real config work.

    Two config-class ACTION ops are exceptions, for DIFFERENT reasons:

      * verify-capture (OP-MIR-040) is a deploy-time capture heartbeat (inject
        a sentinel in-guest over QGA + confirm at the sensor over mgmt-SSH,
        docs/design/06-taps-sensors.md), not config convergence — so like the
        config-class waits it holds only vm:$vmid, never the ansible-pool slot.
      * assign-role (OP-TFC-020) delivers NOTHING: no adapter call, no guest
        exec, nothing on the wire. It records the tfc-owned semantic role of a
        host as pure metadata (docs/WORK_ORDER_role_persona_tier.md). It is
        config-CLASS only because that is how the executor routes it to the
        Traffic tier's own backend; it holds no resource_categories at all, and
        giving it the ansible-pool slot to satisfy this invariant would burn a
        real throughput slot on a no-op. The invariant this test encodes is
        therefore "config-class ops that DELIVER INTO A GUEST hold the pool",
        not "every config-class action does"."""
    pool_exempt = {"verify-capture", "assign-role"}
    for op in list_actions_and_waits():
        if op.idempotency_class != IdempotencyClass.CONFIG:
            continue
        if op.kind == OpKind.WAIT or op.name in pool_exempt:
            assert "ansible-pool" not in op.resource_categories, (
                f"{op.id} ({op.name}) is a config-class wait/probe but holds ansible-pool"
            )
            continue
        assert "ansible-pool" in op.resource_categories, (
            f"{op.id} ({op.name}) is a config-class action but does not declare ansible-pool"
        )


def test_unique_op_ids() -> None:
    ids = [op.id for op in list_actions_and_waits()]
    assert len(ids) == len(set(ids))


def test_unique_op_names() -> None:
    names = [op.name for op in list_actions_and_waits()]
    assert len(names) == len(set(names))
