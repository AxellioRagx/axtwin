"""Native-switch resolution (`docs/design/native-switches.md` §2, §3, §9;
Authority: user, 2026-11-12) -- a LEAF module over the typed IR, with no
planner / validator / adapter imports, so the validator (the NSW rules), the
planner (op emission, placement), the inventory and the tap playout all read
ONE model of the same twin instead of four re-derivations that drift.

`resolve_switches(ir, platform_caps)` answers:

  * what each `kind: switch` resolves to: `vm` (it has an image), `native`
    (imageless, and the platform declares `native-switch`), or `logical`
    (imageless, deploys as nothing -- today's behaviour);
  * which networks are **switch-hosted** (a native switch carries them) and
    the VLAN tag each gets on the switches' bridges;
  * every port on every native switch: the explicit `links[]` ports and the
    automatic access ports (D5) for VM legs no link attaches, on each
    network's deepest carrier;
  * the switch<->switch links (OVS patch pairs);
  * the rule problems (NSW-0x0), as data the validator and planner report.

A twin with no native switch gets an empty model (`has_native` False); every
consumer must then behave exactly as before (D1, the golden plans).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: The platform capability that makes an imageless switch native (D4).
NATIVE_SWITCH_CAP = "native-switch"

LINK_STATES = ("up", "down", "blocked")


def _kind(node: Any) -> str:
    k = getattr(node, "kind", None)
    k = k.value if hasattr(k, "value") else k
    return str(k or "host").lower().split(".")[-1]


def _leg_nets(node: Any) -> list[str]:
    """A node's networks in NIC order (`nets[]`, else the singular `net`)."""
    out: list[str] = []
    for ref in getattr(node, "nets", None) or []:
        net = ref if isinstance(ref, str) else getattr(ref, "net", None)
        if net and net not in out:
            out.append(net)
    if not out and getattr(node, "net", None):
        out.append(node.net)
    return out


def _addressed_nets(node: Any) -> list[str]:
    out: list[str] = []
    for ref in getattr(node, "nets", None) or []:
        if not isinstance(ref, str) and getattr(ref, "ip", None):
            out.append(ref.net)
    return out


def auto_port_name(node_id: str, leg_index: int) -> str:
    """The automatic access port's name: `<instance>.<leg index>` (D5;
    the leg index is the network's position in the node's `nets[]`)."""
    return f"{node_id}.{leg_index}"


@dataclass(frozen=True)
class SwitchPort:
    """One port on a native switch."""

    switch: str
    port: str
    mode: str                       # access | trunk
    vlans: tuple[str, ...]          # networks carried, native first
    native: Optional[str]
    state: str                      # up | down | blocked
    source: str                     # link | auto
    far_kind: str                   # host | router | switch (an imaged switch is a VM: "switch")
    far_device: str
    far_port: Optional[str] = None  # the far end's port name (links only)
    far_net: Optional[str] = None   # VM ends: the network of the NIC plugged in
    far_leg: Optional[int] = None   # VM ends: that NIC's leg index (None: no NIC, a non-up link to a net the VM lacks)
    link_index: Optional[int] = None
    peer_switch: Optional[str] = None  # set when the far end is another native switch

    @property
    def is_patch(self) -> bool:
        return self.peer_switch is not None

    @property
    def is_vm_port(self) -> bool:
        return self.peer_switch is None


@dataclass(frozen=True)
class SwitchLink:
    """A link between two native switches: one OVS patch pair."""

    link_index: int
    a: SwitchPort
    b: SwitchPort

    @property
    def key(self) -> str:
        ends = sorted([f"{self.a.switch}#{self.a.port}", f"{self.b.switch}#{self.b.port}"])
        return "|".join(ends)


@dataclass(frozen=True)
class Problem:
    rule_id: str
    severity: str        # error | warning
    message: str
    location: str


@dataclass
class SwitchModel:
    resolution: dict[str, str] = field(default_factory=dict)       # switch id -> native|logical|vm
    native: list[str] = field(default_factory=list)                # native switch ids, IR order
    stp: dict[str, str] = field(default_factory=dict)              # native switch -> off|rstp
    carriers: dict[str, list[str]] = field(default_factory=dict)   # switch-hosted net -> its native carriers
    vlan_tag: dict[str, int] = field(default_factory=dict)         # switch-hosted net -> VLAN id
    ports: dict[str, list[SwitchPort]] = field(default_factory=dict)  # native switch -> ports
    leg_port: dict[tuple[str, str], SwitchPort] = field(default_factory=dict)  # (vm, net) -> its port
    switch_links: list[SwitchLink] = field(default_factory=list)
    depth: dict[str, Optional[int]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)

    @property
    def has_native(self) -> bool:
        return bool(self.native)

    def is_switch_hosted(self, net: str) -> bool:
        return net in self.carriers

    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    def port(self, switch: str, name: str) -> Optional[SwitchPort]:
        for p in self.ports.get(switch, []):
            if p.port == name:
                return p
        return None

    def vlan_ids(self, nets: Iterable[str]) -> list[int]:
        return [self.vlan_tag[n] for n in nets if n in self.vlan_tag]


def _link_nets(link: Any) -> list[str]:
    out: list[str] = []
    for n in ([link.native] if getattr(link, "native", None) else []) + list(link.vlans or []):
        if n and n not in out:
            out.append(n)
    return out


def resolve_switches(ir: Any, platform_caps: Optional[Iterable[str]] = None) -> SwitchModel:
    """The native-switch model of `ir` on a platform declaring
    `platform_caps` (None / empty: no capabilities, every imageless switch is
    logical)."""
    caps = frozenset(platform_caps or ())
    model = SwitchModel()
    infra = getattr(getattr(ir, "layers", None), "infra", None)
    if infra is None:
        return model
    nodes = [n for n in infra.nodes if getattr(n, "id", None)]
    by_id = {n.id: n for n in nodes}
    networks = [n for n in infra.networks if getattr(n, "id", None)]
    net_by_id = {n.id: n for n in networks}
    links = list(getattr(infra, "links", None) or [])

    def problem(rule: str, sev: str, msg: str, loc: str) -> None:
        model.problems.append(Problem(rule, sev, msg, loc))

    # ---- Resolution (D4) --------------------------------------------------
    for i, n in enumerate(infra.nodes):
        if _kind(n) != "switch" or not getattr(n, "id", None):
            continue
        loc = f"layers.infra.nodes[{i}]"
        stp = getattr(n, "stp", None)
        if n.image:
            model.resolution[n.id] = "vm"
        elif NATIVE_SWITCH_CAP in caps:
            model.resolution[n.id] = "native"
            model.native.append(n.id)
            model.stp[n.id] = stp or "off"
        else:
            model.resolution[n.id] = "logical"
            if platform_caps is not None:
                problem("NSW-020", "warning",
                        f"Switch '{n.id}' has no image and the platform does not declare "
                        "'native-switch': it is logical -- it deploys as nothing, and its "
                        "link states and taps have no effect", loc)
        if stp == "rstp":
            # Native: OVS RSTP (phase 3, 2026-11-12). Elsewhere it does nothing.
            if model.resolution[n.id] != "native":
                problem("NSW-070", "warning",
                        f"Switch '{n.id}' sets stp: rstp but is a {model.resolution[n.id]} "
                        "switch: it has no effect", loc)
        if model.resolution[n.id] == "native":
            for net in _addressed_nets(n):
                problem("NSW-080", "warning",
                        f"Native switch '{n.id}' has an address on '{net}': a native switch "
                        "has no SVI in v1, the address is not deployed", loc)

    if not model.native:
        return model
    native = set(model.native)

    def is_vm(node_id: str) -> bool:
        node = by_id.get(node_id)
        return node is not None and (_kind(node) != "switch" or bool(node.image))

    # ---- Carriers (§2.3): a native switch's own legs (the expander adds
    # one per network its up links carry) -------------------------------------
    for sw in model.native:
        for net in _leg_nets(by_id[sw]):
            model.carriers.setdefault(net, [])
            if sw not in model.carriers[net]:
                model.carriers[net].append(sw)

    # ---- Depth: BFS from the routers over up links (the canvas rule) -------
    adj: dict[str, set[str]] = {}
    for link in links:
        if (link.state or "up") != "up" or link.a is None or link.b is None:
            continue
        a, b = link.a.device, link.b.device
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    routers = [n.id for n in nodes if _kind(n) == "router"]
    dist: dict[str, int] = {r: 0 for r in routers}
    q = deque(routers)
    while q:
        cur = q.popleft()
        for nxt in sorted(adj.get(cur, ())):
            if nxt in dist:
                continue
            # walk only through switches (a host does not extend the tree)
            if nxt in by_id and _kind(by_id[nxt]) == "switch" and nxt in native:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    for sw in model.native:
        model.depth[sw] = dist.get(sw)

    # ---- Explicit link ports ----------------------------------------------
    taken: dict[tuple[str, str], int] = {}  # (vm, net) -> link index that plugged it
    for li, link in enumerate(links):
        if link.a is None or link.b is None:
            continue
        loc = f"layers.infra.links[{li}]"
        ends = [(link.a, link.b), (link.b, link.a)]
        if not any(e.device in native for e, _ in ends):
            continue
        state = link.state or "up"
        nets = _link_nets(link)
        mode = link.mode or "access"
        sw_ends = [(e, f) for e, f in ends if e.device in native]
        if len(sw_ends) == 2:
            pa = SwitchPort(switch=link.a.device, port=link.a.port, mode=mode, vlans=tuple(nets),
                            native=link.native, state=state, source="link", far_kind="switch",
                            far_device=link.b.device, far_port=link.b.port, link_index=li,
                            peer_switch=link.b.device)
            pb = SwitchPort(switch=link.b.device, port=link.b.port, mode=mode, vlans=tuple(nets),
                            native=link.native, state=state, source="link", far_kind="switch",
                            far_device=link.a.device, far_port=link.a.port, link_index=li,
                            peer_switch=link.a.device)
            model.ports.setdefault(pa.switch, []).append(pa)
            model.ports.setdefault(pb.switch, []).append(pb)
            model.switch_links.append(SwitchLink(link_index=li, a=pa, b=pb))
            continue
        sw_end, far = sw_ends[0]
        far_node = by_id.get(far.device)
        if far_node is None:
            continue  # REF-270 reports it
        if not is_vm(far.device):
            problem("NSW-050", "error",
                    f"Native switch '{sw_end.device}' port '{sw_end.port}' links to "
                    f"'{far.device}', a {model.resolution.get(far.device, 'logical')} switch: "
                    "a native switch links only to VMs and native switches", loc)
            continue
        far_nets = _leg_nets(far_node)
        single = (mode == "access" and len(nets) == 1) or (
            mode == "trunk" and link.native and nets == [link.native])
        if not single:
            problem("NSW-060", "error",
                    f"Link {sw_end.device}#{sw_end.port} <-> {far.device}#{far.port} is a trunk "
                    f"to a VM carrying {nets}: v1 does no in-guest VLAN tagging -- carry one "
                    "network (access, or a trunk with only its native)", loc)
            continue
        net = nets[0]
        leg = far_nets.index(net) if net in far_nets else None
        if leg is None and state == "up":
            problem("NSW-050", "error",
                    f"Link {sw_end.device}#{sw_end.port} <-> {far.device} carries '{net}', "
                    f"on which '{far.device}' has no leg", loc)
            continue
        if leg is not None:
            prev = taken.get((far.device, net))
            if prev is not None:
                problem("NSW-050", "error",
                        f"'{far.device}' on '{net}' is plugged by links[{prev}] and "
                        f"links[{li}]: one NIC, one switch port", loc)
                continue
            taken[(far.device, net)] = li
        p = SwitchPort(switch=sw_end.device, port=sw_end.port, mode=mode, vlans=tuple(nets),
                       native=link.native, state=state, source="link",
                       far_kind=_kind(far_node), far_device=far.device, far_port=far.port,
                       far_net=net, far_leg=leg, link_index=li)
        model.ports.setdefault(p.switch, []).append(p)
        if leg is not None:
            model.leg_port[(far.device, net)] = p

    # ---- Automatic access ports (D5) --------------------------------------
    for i, n in enumerate(infra.nodes):
        if not getattr(n, "id", None) or not is_vm(n.id):
            continue
        for leg, net in enumerate(_leg_nets(n)):
            if net not in model.carriers or (n.id, net) in model.leg_port:
                continue
            cands = model.carriers[net]
            depths = {sw: (model.depth.get(sw) if model.depth.get(sw) is not None else -1)
                      for sw in cands}
            deepest = max(depths.values())
            best = [sw for sw in cands if depths[sw] == deepest]
            if len(best) != 1:
                problem("NSW-040", "error",
                        f"Ambiguous carrier for '{n.id}' on '{net}': {', '.join(best)} -- "
                        "add an explicit link", f"layers.infra.nodes[{i}]")
                continue
            p = SwitchPort(switch=best[0], port=auto_port_name(n.id, leg), mode="access",
                           vlans=(net,), native=None, state="up", source="auto",
                           far_kind=_kind(n), far_device=n.id, far_net=net, far_leg=leg)
            if model.port(p.switch, p.port) is not None:
                problem("NSW-050", "error",
                        f"Automatic port '{p.port}' collides with a linked port on '{p.switch}'",
                        f"layers.infra.nodes[{i}]")
                continue
            model.ports.setdefault(p.switch, []).append(p)
            model.leg_port[(n.id, net)] = p

    # ---- Port VLANs are networks the switch carries (NSW-050) --------------
    for sw, plist in model.ports.items():
        own = set(_leg_nets(by_id[sw]))
        for p in plist:
            if p.state != "up":
                continue
            missing = [v for v in p.vlans if v not in own]
            if missing:
                problem("NSW-050", "error",
                        f"Port '{p.port}' on '{sw}' names {missing}, which '{sw}' does not carry",
                        f"layers.infra.links[{p.link_index}]")

    # ---- A loop of native switches needs RSTP (NSW-090) -------------------
    # Without it the loop is a broadcast storm on the node the moment the
    # second link comes up. Parallel links count (two links between the same
    # pair are a loop).
    parent = {sw: sw for sw in model.native}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    closing: list[SwitchLink] = []
    for sl in model.switch_links:
        if sl.a.state != "up":
            continue
        ra, rb = find(sl.a.switch), find(sl.b.switch)
        if ra == rb:
            closing.append(sl)
        else:
            parent[ra] = rb
    for sl in closing:
        comp = sorted(sw for sw in model.native if find(sw) == find(sl.a.switch))
        without = [sw for sw in comp if model.stp.get(sw) != "rstp"]
        if without:
            problem("NSW-090", "error",
                    f"Native switches {', '.join(comp)} form a loop (closed by link "
                    f"{sl.key}) but {', '.join(without)} do not set stp: rstp -- the loop "
                    "would be a broadcast storm; set stp: rstp on every switch in it, or "
                    "mark a link down", f"layers.infra.links[{sl.link_index}]")

    # ---- SDN-or-switch, never both (D1, NSW-030) -------------------------
    for net in model.carriers:
        nw = net_by_id.get(net)
        if nw is None:
            continue
        if getattr(nw, "egress", None) or getattr(nw, "transport", False):
            what = "egress: true" if getattr(nw, "egress", None) else "transport: true"
            problem("NSW-030", "error",
                    f"Network '{net}' is carried by native switch(es) "
                    f"{', '.join(model.carriers[net])} but sets {what}, which needs an SDN "
                    "vnet: a network is SDN-hosted or switch-hosted, never both",
                    f"layers.infra.networks[{networks.index(nw)}]")

    # ---- VLAN tags (§2.3) --------------------------------------------------
    used = {n.vlan for n in networks if getattr(n, "vlan", None)}
    nxt = 2
    for nw in networks:
        if nw.id not in model.carriers:
            continue
        if getattr(nw, "vlan", None):
            model.vlan_tag[nw.id] = int(nw.vlan)
            continue
        while nxt in used:
            nxt += 1
        model.vlan_tag[nw.id] = nxt
        used.add(nxt)
    return model
