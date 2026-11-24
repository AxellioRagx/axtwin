"""Emulated-instance addresses (`SPEC.md`, T2 "Addresses"; policy T6:
in-segment, spike A2).

Deterministic: groups in profile order, instances in index order, each taking
the next free host address in its segment, ascending from the group's
`start` (or the segment's first host address), skipping the gateway, every
router leg, every te host address and every address already handed out.
Extra addresses (generator legs) are taken the same way, after the instances.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable, Optional

from axrange.trafficgen.profile import EmulatedSpec
from axrange.trafficgen.twin import TwinView


class AllocationError(ValueError):
    """A segment has no free address left for an emulated instance."""

    def __init__(self, message: str, *, group: str, segment: str) -> None:
        super().__init__(message)
        self.group, self.segment = group, segment


@dataclass(frozen=True)
class Instance:
    name: str          # <group>-e01, <group>-e02, ...
    group: str
    role: str
    segment: str
    ip: str


def instance_name(group: str, index: int, count: int) -> str:
    return f"{group}-e{index:0{max(2, len(str(count)))}d}"


def group_segment(twin: TwinView, e: EmulatedSpec) -> Optional[str]:
    """The segment an emulated group's instances live on: an external group's
    own `segment`, else the te group's (its first segment; a multi-homed group
    is not a valid emulated group -- TGN-030 says so)."""
    if e.external:
        return e.segment
    g = twin.groups.get(e.group)
    return g.segments[0] if g and g.segments else None


class Allocator:
    """Hands out free addresses per segment, remembering what it gave."""

    def __init__(self, twin: TwinView) -> None:
        self.twin = twin
        self.taken: dict[str, set[str]] = {k: set(v) for k, v in twin.used.items()}

    def take(self, segment: str, *, start: Optional[str] = None, who: str = "") -> str:
        seg = self.twin.segments[segment]
        taken = self.taken.setdefault(segment, set())
        lo = ipaddress.IPv4Address(start) if start else None
        for host in seg.cidr.hosts():
            if lo is not None and host < lo:
                continue
            ip = str(host)
            if ip not in taken:
                taken.add(ip)
                return ip
        raise AllocationError(
            f"{who or 'emulated instance'}: segment {segment} ({seg.cidr}) has no free address"
            + (f" at or above {start}" if start else ""),
            group=who, segment=segment,
        )


def allocate(twin: TwinView, emulated: Iterable[EmulatedSpec],
             allocator: Optional[Allocator] = None) -> dict[str, list[Instance]]:
    """`{group: [Instance, ...]}` for every emulated group whose segment is
    known. Raises `AllocationError` when a segment runs out."""
    alloc = allocator or Allocator(twin)
    out: dict[str, list[Instance]] = {}
    for e in emulated:
        seg = group_segment(twin, e)
        if seg is None or seg not in twin.segments:
            continue
        role = e.role if e.external else (twin.groups[e.group].role or "")
        out[e.group] = [
            Instance(name=instance_name(e.group, i, e.count), group=e.group, role=role or "",
                     segment=seg, ip=alloc.take(seg, start=e.start, who=e.group))
            for i in range(1, e.count + 1)
        ]
    return out
