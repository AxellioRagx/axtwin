"""Per-tap sensor routing — planner emission (te-side non-emission) + naming.

Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md): the
per-(bridge, sensor) tunnel FAN-OUT this file originally tested (pass1_emit
emitting attach-gre-tunnel/install-ovs-mirror per tap->sensor binding) was
already RETIRED from the te planner before this trim — a te records no
mirror-related state of its own at all. Full-topology tap eligibility
(2026-07-28, docs/WORK_ORDER_tap_eligibility.md, Authority: user) went one
step further and retired the intermediate `record-tap-intent` op
(OP-MIR-005) this file used to also cover: there is no more te-side tap
intent, hint, or declaration of ANY kind — `tap:`/`sensors:` bindings and
the `tap_host`/`tap_net` binding kind are all gone from the IR (D2/D6).

The actual multi-sensor fan-out mechanism (naming.py's per-(bridge,sensor)
key derivation + the attach-gre-tunnel/install-ovs-mirror composition)
lives on, but is now exercised exclusively by the A2 tap playout
(`axrange.o2control.taps`, covered by test_o2_taps.py's fan-out/exclusive-
routing tests) against an already-deployed te's published topology, not by
the te's own emission. This file now covers only:
  - a te plan NEVER carries attach-gre-tunnel/install-ovs-mirror/
    verify-capture/record-tap-intent, regardless of `sensors:`/bindings
    content (a leftover `tap_host`/`tap_net` binding is REJECTED by the
    existing STR-100 closed binding-kind set -- `_binding_kind` no longer
    classifies it, `axrange/ir/schema.py`'s `Binding` docstring -- and
    `tap:` is REJECTED by STR-200; both are validator-pass concerns this
    plan-only, `build_plan`-direct test doesn't invoke, so this file just
    never constructs either shape to begin with);
  - naming derives distinct per-(bridge, sensor) ports/keys (unchanged — A2
    still uses this).
"""
from __future__ import annotations

import textwrap

from axrange.adapter_proxmox.naming import (
    gre_key_for_bridge_sensor,
    gre_port_for_bridge_sensor,
)
from axrange.ir.parser import parse_ir
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext


def _renders(plan, op_name):
    return sorted(o.render() for o in plan.ops.values() if o.op_name == op_name)


def test_te_emits_no_mirror_ops_at_all(manifests):
    """A te plan carries NONE of attach-gre-tunnel / install-ovs-mirror /
    verify-capture / record-tap-intent — mirror wiring is entirely the A2
    tap playout's job against an already-deployed twin."""
    src = textwrap.dedent("""
        scenario_schema_version: 1
        scenario: { id: notaps, version: 1 }
        layers:
          infra:
            networks: [{ id: corp, cidr: 10.0.0.0/24, vlan: 10 }]
            nodes:
              - { id: h-a, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
              - { id: h-b, kind: host, net: corp, image: ubuntu-server-22@v5, requires: [linux] }
          apps: []
          activity: []
        bindings: []
        overlays: {}
    """).strip()
    parsed = parse_ir(text=src)
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw)

    assert _renders(plan, "attach-gre-tunnel") == []
    assert _renders(plan, "install-ovs-mirror") == []
    assert _renders(plan, "verify-capture") == []
    assert _renders(plan, "record-tap-intent") == []


def test_naming_per_bridge_sensor_is_distinct():
    """Unchanged — A2's tap playout still uses this to derive distinct
    per-(bridge, sensor) gretap device/key names."""
    a = gre_port_for_bridge_sensor("vbr_corp", "s1")
    b = gre_port_for_bridge_sensor("vbr_corp", "s2")
    assert a != b and a.startswith("tapg_") and len(a) <= 15
    ka = gre_key_for_bridge_sensor("vbr_corp", "s1")
    kb = gre_key_for_bridge_sensor("vbr_corp", "s2")
    assert ka != kb and ka != 0 and kb != 0
