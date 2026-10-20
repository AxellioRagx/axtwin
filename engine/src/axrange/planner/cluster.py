"""ClusterContext — the planner-side snapshot of cluster-side facts.

PLANNER.md §1 says the planner consumes "a snapshot of the target Proxmox
cluster's capabilities". The first slice (docs/ADAPTER.md §6) needs only:

  - one or more Proxmox node names (for trivial placement);
  - the capture sensor's id + its transport-vnet IP (for OP-MIR-010);
  - default capability advertisement per node (the slice does no real cap
    checking — placement is "the node").

This stays a planner-side type; the adapter has its own connection config
(adapter_proxmox.config). The CLI / executor wires both at deploy time.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NodeSnapshot:
    """One Proxmox node, plus what host-capabilities it advertises."""

    name: str
    advertised_caps: tuple[str, ...] = ()
    # vm_count is the load proxy `docs/PLANNER.md` §3.4 uses for the
    # least-loaded heuristic. Deferred in the slice; we keep it for parity.
    vm_count: int = 0


@dataclass
class ClusterContext:
    """Slice-minimum cluster snapshot.

    v1-slice fields:

      nodes:                 list[NodeSnapshot] — at least one
      sensor_id:               "default" — the capture sensor's stable id
      sensor_transport_ip:     the sensor's IP on vnet-transport
      default_node:          the node name placement uses for every host

    Fields beyond these are added as later slices need them
    (multi-node placement, host-cap snapshots, multi-sensor fanout).
    """

    nodes: list[NodeSnapshot] = field(default_factory=list)
    sensor_id: str = "default"
    sensor_transport_ip: str = ""
    # Per-tap sensor routing (2026-06-03): logical sensor id -> transport-vnet IP.
    # `sensor_id` above is the *primary* (default) sensor; `sensors` resolves any
    # logical id a tap routes to (IR `sensors:` + tap->sensor bindings). Empty map
    # means single-sensor: everything resolves to `sensor_transport_ip`.
    sensors: dict[str, str] = field(default_factory=dict)
    default_node: str = ""

    @classmethod
    def single_node(cls, node_name: str, *, sensor_transport_ip: str = "") -> "ClusterContext":
        """Convenience for the slice: one node, one (primary) sensor."""
        return cls(
            nodes=[NodeSnapshot(name=node_name)],
            default_node=node_name,
            sensor_transport_ip=sensor_transport_ip,
            sensors={"default": sensor_transport_ip} if sensor_transport_ip else {},
        )

    def node_names(self) -> list[str]:
        return [n.name for n in self.nodes]

    def sensor_ip(self, sensor_id: str) -> str:
        """Resolve a logical sensor id to its transport-vnet IP.

        Falls back to the primary `sensor_transport_ip` for the primary id (or an
        unmapped id in single-sensor setups), so existing single-sensor scenarios
        keep working unchanged.
        """
        if sensor_id in self.sensors:
            return self.sensors[sensor_id]
        if sensor_id == self.sensor_id:
            return self.sensor_transport_ip
        return self.sensor_transport_ip
