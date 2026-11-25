"""The trafficgen planner (`SPEC.md`; T3 signed off): a tfc profile's emulated
instances and traffic lines -> addresses, generator VMs, their legs, and one
profile document per generator. Pure and deterministic: the same profile and
twin give byte-identical output (`TrafficGenPlan.to_json`).

Grouping (T3 §3): one generator per distinct (client segment, server segment)
of the lines; lines sharing that pair share the generator. Each generator runs
one TRex process with one port pair: port 0 = the client leg on the `from`
segment, port 1 = the server leg on the `to` segment. In a single-pair process
every pool size works (spikes A1, A5); a multi-pair process needs every pool
larger than its pair count (A3).

Addresses (T6 proposal, A2): in-segment. Emulated groups take contiguous
blocks (`addresses.allocate`), then each generator leg takes one address. A
same-segment generator's client leg routes to its server leg directly
(`default_gw` = the server leg's IP), so the flow stays on L2.

No router routes: in-segment addresses are answered on L2 by the generator's
port namespace (`routes` is always empty, kept so a caller need not guess).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from axrange.trafficgen.addresses import Allocator, Instance, allocate, group_segment
from axrange.trafficgen.twin import TwinView
from axrange.trafficgen.validate import mix_shares

PLAN_VERSION = 1
#: TRex leg name in the generated documents; the in-VM wrapper maps it to the
#: interface carrying that leg's MAC (spike A3: names are not predictable).
CLIENT_PORT, SERVER_PORT = 0, 1


class TrafficGenPlanError(ValueError):
    """The profile cannot be planned (a reason the validator does not cover)."""


@dataclass(frozen=True)
class Leg:
    index: int                 # NIC index on the generator VM == TRex port
    role: str                  # client | server
    segment: str
    ip: str
    prefix: int
    default_gw: str
    addresses: tuple[str, ...]  # emulated addresses this leg answers ARP for


@dataclass(frozen=True)
class Generator:
    host_id: str               # the planned host id (VM name: tfc-<6hex>-<host_id>)
    client_segment: str
    server_segment: str
    cores: int
    memory_mib: int
    legs: tuple[Leg, ...]
    lines: tuple[str, ...]
    document: dict[str, Any]   # pushed to the VM; the generic ASTF profile reads it

    def document_json(self) -> str:
        return json.dumps(self.document, sort_keys=True, indent=1)

    def document_sha256(self) -> str:
        return hashlib.sha256(self.document_json().encode()).hexdigest()


@dataclass
class TrafficGenPlan:
    instances: dict[str, list[Instance]] = field(default_factory=dict)
    generators: list[Generator] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "version": PLAN_VERSION,
            "instances": {g: [asdict(i) for i in v] for g, v in self.instances.items()},
            "generators": [{**{k: v for k, v in asdict(g).items() if k != "document"},
                            "document": g.document} for g in self.generators],
            "routes": self.routes,
        }, sort_keys=True, indent=1)


def generator_host_id(client_segment: str, server_segment: str) -> str:
    return f"gen-{client_segment}--{server_segment}"


def sizing(pairs: int, legs: int) -> tuple[int, int]:
    """T5: `cores = 1 + ceil(pairs / 4)`, `memory = 2 GiB + 0.1 GiB x legs`."""
    return 1 + math.ceil(pairs / 4), math.ceil((2 + 0.1 * legs) * 1024)


def _render_steps(app: Any, values: dict[str, Any]) -> list[dict[str, Any]]:
    """An app's program with every placeholder filled: text steps become hex,
    sizes and delays become integers. The generic profile sees only `send` /
    `reply` (hex, optional `size` + `fill`), `recv` and `delay_ms`."""
    out = []
    for st in app.program:
        st = dict(st)
        r: dict[str, Any] = {}
        for k in ("send_text", "reply_text"):
            if k in st:
                r[k.split("_")[0]] = st.pop(k).format(**values).encode().hex()
        for k in ("send", "reply"):
            if k in st:
                r[k] = st.pop(k)
        for k in ("size", "recv", "delay_ms"):
            if k in st:
                r[k] = int(str(st.pop(k)).format(**values))
        if "fill" in st:
            r["fill"] = st.pop("fill")
        out.append(r)
    return out


def _template(app: Any, share: float, line: Any, values: dict[str, Any]) -> dict[str, Any]:
    t: dict[str, Any] = {
        "app": app.id, "proto": app.proto, "transport": app.transport, "port": app.port,
        "share": round(share, 6), "cps": round(line.rate_cps * share, 6),
    }
    if app.source_kind == "pcap":
        data = app.pcap.read_bytes()
        t["pcap"] = {"name": f"{app.id}--{app.pcap.name}", "sha256": hashlib.sha256(data).hexdigest()}
    else:
        t["program"] = _render_steps(app, values)
    return t


def _ip_range(insts: list[Instance]) -> list[str]:
    return [insts[0].ip, insts[-1].ip]


def plan_lines(spec: Any, twin: TwinView, personas: dict[str, Any], apps: dict[str, Any]) -> TrafficGenPlan:
    """Plan `spec.emulated` / `spec.lines` against `twin`. Assumes the profile
    passed `trafficgen.validate.check_lines` (no TGN error); raises
    `TrafficGenPlanError` for what only planning can see."""
    plan = TrafficGenPlan()
    if not spec.lines:
        return plan
    alloc = Allocator(twin)
    plan.instances = allocate(twin, spec.emulated, alloc)
    emulated = {e.group: e for e in spec.emulated}

    # Group lines by (client segment, server segment), in first-seen order.
    combos: dict[tuple[str, str], list[Any]] = {}
    for ln in spec.lines:
        cs = group_segment(twin, emulated[ln.from_group])
        ss = group_segment(twin, emulated[ln.to_group])
        combos.setdefault((cs, ss), []).append(ln)

    # An emulated group's addresses are answered by exactly one leg (one MAC
    # per address), so a group may sit on one generator only.
    owner: dict[str, tuple[str, str]] = {}
    for (cs, ss), lines in combos.items():
        gid = generator_host_id(cs, ss)
        for ln in lines:
            for g in (ln.from_group, ln.to_group):
                first_gid, first_line = owner.setdefault(g, (gid, ln.id))
                if first_gid != gid:
                    raise TrafficGenPlanError(
                        f"emulated group {g!r} would need two generators: line {first_line!r} puts it on "
                        f"{first_gid}, line {ln.id!r} on {gid}; its lines must share one "
                        "(from segment, to segment) pair in this phase")

    for (cs, ss), lines in combos.items():
        gid = generator_host_id(cs, ss)
        same = cs == ss
        cseg, sseg = twin.segments[cs], twin.segments[ss]
        c_ip = alloc.take(cs, who=f"{gid} client leg")
        s_ip = alloc.take(ss, who=f"{gid} server leg")
        if not same and (cseg.gateway is None or sseg.gateway is None):
            raise TrafficGenPlanError(f"{gid}: segment {cs if cseg.gateway is None else ss} has no gateway to route through")
        clients = sorted({i.ip for ln in lines for i in plan.instances[ln.from_group]},
                         key=lambda x: tuple(int(o) for o in x.split(".")))
        servers = sorted({i.ip for ln in lines for i in plan.instances[ln.to_group]},
                         key=lambda x: tuple(int(o) for o in x.split(".")))
        legs = (
            Leg(index=CLIENT_PORT, role="client", segment=cs, ip=c_ip, prefix=cseg.cidr.prefixlen,
                default_gw=s_ip if same else cseg.gateway, addresses=tuple(clients)),
            Leg(index=SERVER_PORT, role="server", segment=ss, ip=s_ip, prefix=sseg.cidr.prefixlen,
                default_gw=c_ip if same else sseg.gateway, addresses=tuple(servers)),
        )
        cores, mem = sizing(pairs=1, legs=len(legs))
        doc_lines = []
        for ln in lines:
            p = personas[ln.mix]
            cl, sv = plan.instances[ln.from_group], plan.instances[ln.to_group]
            values_base = {"server_name": ln.to_group, "server_ip": sv[0].ip, "client_ip": cl[0].ip}
            templates = []
            for app_id, share in mix_shares(p):
                app = apps[app_id]
                vals = {k: t.default for k, t in app.tunables.items()}
                vals.update(values_base)
                templates.append(_template(app, share, ln, vals))
            doc_lines.append({
                "id": ln.id, "from": ln.from_group, "to": ln.to_group, "mix": ln.mix,
                "rate_cps": ln.rate_cps, "max_concurrent": ln.max_concurrent,
                "client_port": CLIENT_PORT, "clients": _ip_range(cl), "servers": _ip_range(sv),
                "templates": templates,
            })
        document = {
            "version": PLAN_VERSION,
            "generator": gid,
            "trex": {
                "low_end": True, "stack": "linux_based",
                "ports": [{"leg": leg.index, "ip": leg.ip, "default_gw": leg.default_gw} for leg in legs],
                "hugepages": 20 + 40 * len(legs) + 64,
            },
            "namespaces": {str(leg.index): list(leg.addresses) for leg in legs},
            "budget": {"cps": spec.budget.cps, "mbps": spec.budget.mbps} if spec.budget else None,
            "lines": doc_lines,
        }
        plan.generators.append(Generator(
            host_id=gid, client_segment=cs, server_segment=ss, cores=cores, memory_mib=mem,
            legs=legs, lines=tuple(ln.id for ln in lines), document=document,
        ))
    return plan


def plan_profile(spec: Any, twin: TwinView, personas: Optional[dict[str, Any]] = None,
                 apps: Optional[dict[str, Any]] = None) -> TrafficGenPlan:
    """`plan_lines` with the installed personas and apps as defaults."""
    if personas is None:
        from axrange.personas.registry import PERSONAS as personas  # noqa: N811
    if apps is None:
        from axrange.trafficgen.apps import list_apps
        apps = list_apps()[0]
    return plan_lines(spec, twin, personas, apps)


def plan_for_file(spec: Any, spec_path: Any) -> TrafficGenPlan:
    """`plan_profile` for a profile file inside a twin folder: the twin's te
    file (read-only) and the catalog personas plus the twin's own."""
    from axrange.personas.registry import PERSONAS, twin_overlay
    from axrange.twinconfig.manifest import load_twin_manifest, twin_dir_for_file

    twin_dir = twin_dir_for_file(spec_path)
    if twin_dir is None:
        raise TrafficGenPlanError(f"{spec_path}: lines need the profile inside a twin folder (twins/<id>/tfc/)")
    personas = {**PERSONAS, **twin_overlay(twin_dir / "personas")}
    return plan_profile(spec, TwinView.from_te_path(load_twin_manifest(twin_dir).te_path), personas)
