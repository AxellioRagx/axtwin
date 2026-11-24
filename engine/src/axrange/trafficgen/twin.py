"""A read-only view of a twin for trafficgen: its segments, its node groups
and every address already in use (`SPEC.md`, T2 "Groups -> hosts").

Built from the twin config's te file by the same expansion a te deploy runs
(`twinconfig.expand.expand_twin_config_raw`), which assigns every host and
router leg its address deterministically, so the view needs no deploy. The te
file is only read.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class Segment:
    name: str
    cidr: ipaddress.IPv4Network
    gateway: Optional[str] = None
    vlan: Optional[int] = None


@dataclass(frozen=True)
class Group:
    """A te node group (`nodes[].name`, fanned out by `count:`)."""

    name: str
    role: Optional[str]
    kind: str                                # host | router | switch | ...
    instances: tuple[str, ...]               # host ids, e.g. rack1-web01, rack1-web02
    segments: tuple[str, ...]                # the segments its instances sit on
    ips: tuple[tuple[str, str], ...] = ()    # (segment, ip) of every instance leg


@dataclass
class TwinView:
    twin_id: str
    segments: dict[str, Segment] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    #: segment -> every address in use there (gateways, router legs, hosts)
    used: dict[str, set[str]] = field(default_factory=dict)

    def real_ips(self) -> set[str]:
        return {ip for ips in self.used.values() for ip in ips}

    @classmethod
    def from_te_path(cls, te_path: str | Path) -> "TwinView":
        from axrange.ir.yaml_util import load_yaml_raw
        from axrange.twinconfig.registry import _yaml_loader

        raw = load_yaml_raw(Path(te_path), loader=_yaml_loader()) or {}
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "TwinView":
        from axrange.twinconfig.expand import expand_twin_config_raw, instance_ids

        ir, _ = expand_twin_config_raw(raw)
        tc = raw.get("twin_config") or {}
        view = cls(twin_id=str((ir.get("scenario") or {}).get("id") or tc.get("id") or ""))
        infra = (ir.get("layers") or {}).get("infra") or {}
        for n in infra.get("networks") or []:
            seg = Segment(
                name=n["id"], cidr=ipaddress.ip_network(n["cidr"], strict=False),
                gateway=n.get("gateway"), vlan=n.get("vlan"),
            )
            view.segments[seg.name] = seg
            view.used[seg.name] = {seg.gateway} if seg.gateway else set()
        nodes = {n["id"]: n for n in infra.get("nodes") or []}
        for n in nodes.values():
            for leg in n.get("nets") or []:
                if leg.get("ip") and leg.get("net") in view.used:
                    view.used[leg["net"]].add(str(leg["ip"]))
        for spec in tc.get("nodes") or []:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            try:
                count = max(1, int(spec.get("count", 1)))
            except (TypeError, ValueError):
                count = 1
            ids = tuple(i for i in instance_ids(str(spec["name"]), count) if i in nodes)
            legs = [(leg["net"], str(leg["ip"])) for i in ids for leg in nodes[i].get("nets") or []
                    if leg.get("ip")]
            segs = tuple(dict.fromkeys(leg["net"] for i in ids for leg in nodes[i].get("nets") or []))
            kind = str(nodes[ids[0]].get("kind") or "host") if ids else str(spec.get("kind") or "host")
            view.groups[str(spec["name"])] = Group(
                name=str(spec["name"]), role=spec.get("role"), kind=kind,
                instances=ids, segments=segs, ips=tuple(legs),
            )
        return view
