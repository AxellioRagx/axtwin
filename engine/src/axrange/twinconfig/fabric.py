"""Whole-fabric conversion of bridge VMs to native switches
(`docs/design/native-switches.md` §12 S5, D11; Authority: user, 2026-11-12:
"whole-fabric conversion").

A **bridge VM** is a `kind: host` node with `role: l2-switch` and an image
that builds a VLAN-aware Linux bridge from a per-device port table
(corp-twin's `scripts/serve-l2switch.sh`). The table is the only place the
trunks' VLAN sets live, so this module reads it, and turns the fabric into
native switches and `links[]`:

  * **one segment per VLAN.** Every access port names the segment its VLAN
    is on. Among a VLAN's segments exactly one is not `isolated` (the
    anchor, `mx-vlanN`); it survives. The isolated partners (`vlanN`, the
    point-to-point access wires) go, and every leg on them moves to the
    survivor with its pinned address kept.
  * **trunk segments become links.** A segment that only trunk ports use is
    a wire between two bridge VMs: it becomes a `trunk` link with the
    table's native and tagged VLANs (as survivor segment names). An access
    wire between two bridge VMs becomes an `access` link. The table's `down`
    flag becomes `state: down`.
  * **parallel links.** Of two `up` links between the same pair of switches
    the table's higher-`cost` one is the one STP blocked: it becomes
    `state: blocked` (deterministic, as the capture saw it). A loop that is
    still left gets `stp: rstp` on every converted switch (NSW-090).
  * **every VM keeps its switch.** A VM leg that was on a bridge VM's port
    group must land on that switch. The automatic port (D5) lands it on the
    segment's deepest carrier; where that is a different switch, or a tie
    (NSW-040), an explicit access link wires it (a group member by its
    instance id, S1). Computed with the deploy gate off, so every gated
    member is covered.
  * **taps.** A `segment` binding on a removed segment becomes part of one
    `span` binding on the switch common to every removed segment its group
    (same sensors and mode) names; the sources are that switch's ports on
    those segments, `both` directions.

What does not convert (S2, decided 2026-11-12): an addressed bridge leg (the
MS120's management address) stays as an addressed leg of the native switch
-- NSW-080 warns and the address is not deployed; L3 devices (`l3-switch`
hosts, the 9K pair) are not bridge VMs and stay VMs on access ports.

`plan_fabric_conversion` is pure (texts in, a plan out). The canvas applies
the plan as one undoable edit; nothing is written here. The plan's
`problems` are the native-switch model's rule problems on the converted twin
(the NSW rules need the platform capability, which `POST /api/validate`
does not pass).
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ruamel.yaml import YAML

from axrange.twinconfig.errors import TwinConfigParamError, TwinConfigShapeError

#: The deploy gate off: plan for every member a lower gate could emit.
_GATE_OFF = {"DEPLOY_MIN_OCCUPANCY": "0", "DEPLOY_MIN_ROLE_CONFIDENCE": "0"}

BRIDGE_ROLE = "l2-switch"


class FabricConversionError(ValueError):
    """The twin cannot be converted as a whole; the message says why."""


# ---------------------------------------------------------------------------
# The port table (`serve-l2switch.sh`'s `axr_table`)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TablePort:
    leg: str                       # the leg's segment (the table's first column)
    mode: str                      # access | trunk
    vid: Optional[int] = None      # access
    native: Optional[int] = None   # trunk: the untagged VLAN (None: `-`)
    vids: tuple[int, ...] = ()     # trunk: the tagged VLANs
    down: bool = False
    mgmt: bool = False
    cost: Optional[int] = None


@dataclass(frozen=True)
class PortTable:
    device: str
    prio: Optional[int]
    ports: tuple[TablePort, ...]


_BLOCK = re.compile(r"^[ \t]*([A-Za-z0-9._-]+)\)[ \t]*cat <<'EOT'[ \t]*\n(.*?)^EOT[ \t]*$",
                    re.MULTILINE | re.DOTALL)


def _vid(text: str, where: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 4094:
        raise FabricConversionError(f"{where}: {text!r} is not a VLAN ID")
    return int(text)


def read_port_tables(script: str) -> dict[str, PortTable]:
    """Every device's port table in a bridge recipe, keyed by device (node)
    name. Grammar (the recipe's header): `bridge prio=<n>`, then one port per
    line in leg order -- `<leg> access:<vid>` or `<leg> trunk:<native|->:<vid,...>`,
    flags `cost=<n>`, `down`, `mgmt`."""
    out: dict[str, PortTable] = {}
    for m in _BLOCK.finditer(script):
        device, body = m.group(1), m.group(2)
        prio: Optional[int] = None
        ports: list[TablePort] = []
        for raw in body.splitlines():
            words = raw.split()
            if not words:
                continue
            where = f"port table '{device}', line {raw.strip()!r}"
            if words[0] == "bridge":
                for w in words[1:]:
                    if w.startswith("prio="):
                        prio = int(w[5:])
                continue
            if len(words) < 2:
                raise FabricConversionError(f"{where}: no port spec")
            leg, spec, flags = words[0], words[1], words[2:]
            down = "down" in flags
            mgmt = "mgmt" in flags
            cost = next((int(f[5:]) for f in flags if f.startswith("cost=")), None)
            unknown = [f for f in flags if f not in ("down", "mgmt") and not f.startswith("cost=")]
            if unknown:
                raise FabricConversionError(f"{where}: unknown flag(s) {unknown}")
            kind, _, rest = spec.partition(":")
            if kind == "access":
                ports.append(TablePort(leg, "access", vid=_vid(rest, where),
                                       down=down, mgmt=mgmt, cost=cost))
            elif kind == "trunk":
                nat, _, vids = rest.partition(":")
                ports.append(TablePort(
                    leg, "trunk", native=None if nat == "-" else _vid(nat, where),
                    vids=tuple(_vid(v, where) for v in vids.split(",") if v),
                    down=down, mgmt=mgmt, cost=cost))
            else:
                raise FabricConversionError(f"{where}: {spec!r} is neither access: nor trunk:")
        out[device] = PortTable(device, prio, tuple(ports))
    return out


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

@dataclass
class TapRewrite:
    path: str
    remove: list[int] = field(default_factory=list)        # binding indices, ascending
    retarget: list[dict[str, Any]] = field(default_factory=list)  # {index, target}: a segment selector's new target
    add_spans: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FabricPlan:
    bridges: list[str] = field(default_factory=list)        # nodes replaced, te order
    switches: list[dict[str, Any]] = field(default_factory=list)  # {name, role, stp, nets}
    remove_segments: list[str] = field(default_factory=list)
    node_nets: list[dict[str, Any]] = field(default_factory=list)  # {node, nets: [{segment, ip}]}
    links: list[dict[str, Any]] = field(default_factory=list)
    taps: list[TapRewrite] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problems: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bridges": self.bridges,
            "switches": self.switches,
            "remove_segments": self.remove_segments,
            "node_nets": self.node_nets,
            "links": self.links,
            "taps": [{"path": t.path, "remove": t.remove, "retarget": t.retarget,
                      "add_spans": t.add_spans}
                     for t in self.taps],
            "notes": self.notes,
            "problems": self.problems,
        }


def _legs(node: dict[str, Any]) -> list[dict[str, Any]]:
    """A raw node's legs as `{segment, ip?, ...}` maps, NIC order."""
    if node.get("nets"):
        return [dict(leg) for leg in node["nets"]]
    if node.get("net"):
        return [{"segment": node["net"]}]
    return []


def _is_bridge(node: dict[str, Any]) -> bool:
    return (str(node.get("kind") or "host") == "host" and node.get("role") == BRIDGE_ROLE
            and bool(node.get("image")))


def _bridge_scripts(node: dict[str, Any]) -> list[str]:
    return [i["script"] for i in (node.get("install") or [])
            if isinstance(i, dict) and isinstance(i.get("script"), str)]


def bridge_recipe(te_raw: dict[str, Any]) -> Optional[str]:
    """The one recipe script every bridge VM installs (None: no bridge VM).
    Two different recipes are refused: one table grammar is read."""
    scripts = {s for n in (te_raw.get("twin_config") or {}).get("nodes") or []
               if isinstance(n, dict) and _is_bridge(n) for s in _bridge_scripts(n)}
    if not scripts:
        return None
    if len(scripts) > 1:
        raise FabricConversionError(
            f"the bridge VMs install different recipes ({', '.join(sorted(scripts))}): "
            "one port-table grammar is read")
    return scripts.pop()


def _expand_model(raw: dict[str, Any]):
    """Expand `raw` with the gate off and resolve it on a native-switch
    platform: `(ir, model)`."""
    from axrange.ir.parser import parse_ir
    from axrange.switchmodel import NATIVE_SWITCH_CAP, resolve_switches
    from axrange.twinconfig.expand import expand_twin_config_raw

    try:
        ir_dict, _ = expand_twin_config_raw(raw, param_overrides=_GATE_OFF)
    except (TwinConfigShapeError, TwinConfigParamError) as e:
        raise FabricConversionError(f"the converted twin does not expand: {e}") from e
    buf = io.StringIO()
    YAML(typ="safe").dump(ir_dict, buf)
    parsed = parse_ir(text=buf.getvalue())
    if parsed.ir is None:
        raise FabricConversionError(
            "the converted twin does not parse as IR: "
            + "; ".join(str(e) for e in getattr(parsed, "errors", []))[:400])
    return parsed.ir, resolve_switches(parsed.ir, frozenset({NATIVE_SWITCH_CAP}))


def _member_of(ir: Any, spec_names: set[str]) -> dict[str, str]:
    """IR node id -> the te node (group) it was emitted from
    (`expand.instance_ids`: `name`, or `name01`, `name02`, ...)."""
    out: dict[str, str] = {}
    ids = {n.id for n in ir.layers.infra.nodes}
    for name in spec_names:
        if name in ids:
            out[name] = name
        i = 1
        while f"{name}{i:02d}" in ids:
            out.setdefault(f"{name}{i:02d}", name)
            i += 1
    return out


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def apply_plan(te_raw: dict[str, Any], plan: FabricPlan) -> dict[str, Any]:
    """`te_raw` (a loaded twin config) with `plan` applied, as a new dict:
    the engine's reference for what the canvas's text edit produces."""
    raw = copy.deepcopy(te_raw)
    t = raw["twin_config"]
    removed = set(plan.remove_segments)
    bridges = set(plan.bridges)
    t["segments"] = [s for s in t["segments"] if s.get("name") not in removed]
    moved = {x["node"]: x["nets"] for x in plan.node_nets}
    by_switch = {x["name"]: x for x in plan.switches}
    nodes = []
    for n in t["nodes"]:
        if n["name"] in bridges:
            sw = by_switch[n["name"]]
            out = {"name": sw["name"], "role": sw["role"], "kind": "switch"}
            if sw.get("stp"):
                out["stp"] = sw["stp"]
            if sw["nets"]:
                out["nets"] = [_clean(leg) for leg in sw["nets"]]
            nodes.append(out)
            continue
        if n["name"] in moved:
            n = dict(n)
            n.pop("net", None)
            n["nets"] = [_clean(leg) for leg in moved[n["name"]]]
        nodes.append(n)
    t["nodes"] = nodes
    t["links"] = list(t.get("links") or []) + [_clean(link) for link in plan.links]
    return raw


def plan_fabric_conversion(te_text: str, script_text: str,
                           taps: Optional[dict[str, str]] = None) -> FabricPlan:
    """The whole-fabric conversion of `te_text`'s bridge VMs, read against
    the port tables in `script_text`; `taps` maps a tap file's path to its
    text. Raises `FabricConversionError` when the fabric cannot convert."""
    yaml = YAML(typ="safe")
    te_raw = yaml.load(te_text)
    if not isinstance(te_raw, dict) or not isinstance(te_raw.get("twin_config"), dict):
        raise FabricConversionError("not a twin config (no top-level 'twin_config:')")
    tc = te_raw["twin_config"]
    tables = read_port_tables(script_text)
    plan = FabricPlan()

    segs = {s["name"]: s for s in tc.get("segments") or [] if isinstance(s, dict)}
    nodes = [n for n in tc.get("nodes") or [] if isinstance(n, dict)]
    by_name = {n["name"]: n for n in nodes}
    bridges = [n for n in nodes if _is_bridge(n)]
    if not bridges:
        raise FabricConversionError("the twin has no bridge VM (a `kind: host`, "
                                    f"`role: {BRIDGE_ROLE}` node with an image)")
    plan.bridges = [b["name"] for b in bridges]
    bridge_names = set(plan.bridges)

    # ---- every bridge VM has a table that matches its legs -------------
    port_of: dict[tuple[str, str], TablePort] = {}
    for b in bridges:
        t = tables.get(b["name"])
        if t is None:
            raise FabricConversionError(f"bridge VM '{b['name']}' has no port table in the recipe")
        legs = [leg["segment"] for leg in _legs(b)]
        if legs != [p.leg for p in t.ports]:
            raise FabricConversionError(
                f"bridge VM '{b['name']}': its legs {legs} do not match its port table "
                f"{[p.leg for p in t.ports]} (order is load-bearing)")
        for p in t.ports:
            port_of[(b["name"], p.leg)] = p

    # ---- one segment per VLAN ------------------------------------------
    vid_segs: dict[int, list[str]] = {}
    trunk_segs: list[str] = []
    for (dev, leg), p in port_of.items():
        if leg not in segs:
            raise FabricConversionError(f"bridge VM '{dev}' leg '{leg}' names no segment")
        if p.mode == "access":
            vid_segs.setdefault(p.vid, [])
            if leg not in vid_segs[p.vid]:
                vid_segs[p.vid].append(leg)
        elif leg not in trunk_segs:
            trunk_segs.append(leg)
    seg_vid: dict[str, int] = {}
    for vid, names in vid_segs.items():
        for s in names:
            if s in seg_vid and seg_vid[s] != vid:
                raise FabricConversionError(
                    f"segment '{s}' is an access port on VLAN {seg_vid[s]} and on VLAN {vid}")
            seg_vid[s] = vid
    for s in trunk_segs:
        if s in seg_vid:
            raise FabricConversionError(f"segment '{s}' is both a trunk and an access port")
    survivor: dict[int, str] = {}
    for vid, names in sorted(vid_segs.items()):
        anchors = [s for s in names if not segs[s].get("isolated")]
        if len(anchors) != 1:
            raise FabricConversionError(
                f"VLAN {vid} is on {names}: exactly one of them must be a non-isolated anchor "
                f"(found {len(anchors)})")
        survivor[vid] = anchors[0]
        for s in names:
            if s != anchors[0] and segs[s].get("cidr") != segs[anchors[0]].get("cidr"):
                raise FabricConversionError(
                    f"'{s}' (VLAN {vid}) has CIDR {segs[s].get('cidr')} but its anchor "
                    f"'{anchors[0]}' has {segs[anchors[0]].get('cidr')}")
    for (dev, leg), p in port_of.items():
        for v in ([p.native] if p.native else []) + list(p.vids):
            if v not in survivor:
                raise FabricConversionError(
                    f"'{dev}' trunk '{leg}' carries VLAN {v}, which no access port names a "
                    "segment for")
    to_survivor = {s: survivor[v] for s, v in seg_vid.items()}
    removed = [s for s in segs if (s in to_survivor and to_survivor[s] != s) or s in trunk_segs]
    plan.remove_segments = removed
    removed_set = set(removed)

    # ---- nothing else may name a removed segment ------------------------
    for i, c in enumerate(tc.get("conduits") or []):
        for end in ("from", "to"):
            if isinstance(c, dict) and c.get(end) in removed_set:
                raise FabricConversionError(
                    f"conduit {i} names '{c[end]}', which the conversion removes")
    for i, link in enumerate(tc.get("links") or []):
        named = ([link.get("native")] if link.get("native") else []) + list(link.get("vlans") or [])
        if set(named) & removed_set:
            raise FabricConversionError(f"links[{i}] names a segment the conversion removes")

    # ---- ends of every bridge leg segment -------------------------------
    ends: dict[str, list[tuple[str, int]]] = {}   # segment -> [(node, leg index)]
    for n in nodes:
        for li, leg in enumerate(_legs(n)):
            ends.setdefault(leg["segment"], []).append((n["name"], li))

    links: list[dict[str, Any]] = []
    link_cost: list[Optional[int]] = []
    intended: dict[tuple[str, str], tuple[str, str]] = {}  # (te node, survivor) -> (switch, original segment)
    single_vm: set[str] = set()   # a bridge port with exactly one VM behind it: its link port keeps the name
    for s in sorted({leg for _, leg in port_of}, key=list(segs).index):
        b_ends = [(d, li) for d, li in ends.get(s, []) if d in bridge_names]
        o_ends = [(d, li) for d, li in ends.get(s, []) if d not in bridge_names]
        if s in trunk_segs:
            if o_ends or len(b_ends) != 2:
                raise FabricConversionError(
                    f"trunk segment '{s}' must join exactly two bridge VMs and nothing else "
                    f"(found {[d for d, _ in ends.get(s, [])]})")
            (da, _), (db, _) = b_ends
            pa, pb = port_of[(da, s)], port_of[(db, s)]
            if (pa.mode, pa.native, pa.vids) != (pb.mode, pb.native, pb.vids):
                raise FabricConversionError(
                    f"trunk '{s}': '{da}' and '{db}' disagree on its VLANs")
            link = {"a": {"device": da, "port": s}, "b": {"device": db, "port": s},
                    "mode": "trunk", "native": survivor[pa.native] if pa.native else None,
                    "vlans": [survivor[v] for v in pa.vids],
                    "state": "down" if pa.down or pb.down else None}
            links.append(link)
            link_cost.append(max(c for c in (pa.cost, pb.cost, 0) if c is not None))
            continue
        vid = seg_vid[s]
        surv = survivor[vid]
        if len(b_ends) == 2 and not o_ends:
            (da, _), (db, _) = b_ends
            pa, pb = port_of[(da, s)], port_of[(db, s)]
            links.append({"a": {"device": da, "port": s}, "b": {"device": db, "port": s},
                          "mode": "access", "native": None, "vlans": [surv],
                          "state": "down" if pa.down or pb.down else None})
            link_cost.append(max(c for c in (pa.cost, pb.cost, 0) if c is not None))
        elif len(b_ends) == 1:
            dev = b_ends[0][0]
            p = port_of[(dev, s)]
            for d, _li in o_ends:
                intended[(d, surv)] = (dev, s)
            if len(o_ends) == 1:
                single_vm.add(s)
            if p.down and o_ends:
                plan.notes.append(
                    f"'{dev}' port '{s}' is admin-down in the table and VMs sit behind it; "
                    "their links are written `up` -- set `state: down` on them to cut them off")
        elif b_ends:
            raise FabricConversionError(
                f"segment '{s}' is a port on {len(b_ends)} bridge VMs"
                + (" and has other nodes on it" if o_ends else "") + ": no single switch owns it")

    # ---- the switches ---------------------------------------------------
    carried: dict[str, set[str]] = {b: set() for b in bridge_names}
    for link in links:
        nets = set(link["vlans"]) | ({link["native"]} if link["native"] else set())
        if link["state"] is None:
            carried[link["a"]["device"]] |= nets
            carried[link["b"]["device"]] |= nets
    switches: list[dict[str, Any]] = []
    for b in bridges:
        nets: list[dict[str, Any]] = []
        for leg in _legs(b):
            p = port_of[(b["name"], leg["segment"])]
            if p.mode != "access":
                continue
            surv = to_survivor[leg["segment"]]
            if leg.get("ip"):
                nets.append({"segment": surv, "ip": leg["ip"]})
                plan.notes.append(
                    f"'{b['name']}' keeps its address {leg['ip']} on '{surv}' as an addressed "
                    "leg: a native switch has no SVI in v1, so it is not deployed (NSW-080, S2)")
            elif (surv not in carried[b["name"]] and not p.down
                  and (b["name"], leg["segment"]) in set(intended.values())):
                nets.append({"segment": surv, "ip": None})
        switches.append({"name": b["name"], "role": BRIDGE_ROLE, "stp": None, "nets": nets})
    if any(tables[b].prio is not None for b in bridge_names):
        plan.notes.append("the tables' STP bridge priorities and port costs have no field on a "
                          "native switch and are not carried")

    # ---- the other nodes' legs move to the survivors --------------------
    node_nets: list[dict[str, Any]] = []
    for n in nodes:
        if n["name"] in bridge_names:
            continue
        legs = _legs(n)
        if not any(leg["segment"] in to_survivor and to_survivor[leg["segment"]] != leg["segment"]
                   for leg in legs):
            continue
        out: list[dict[str, Any]] = []
        for leg in legs:
            seg = to_survivor.get(leg["segment"], leg["segment"])
            if any(x["segment"] == seg for x in out):
                raise FabricConversionError(
                    f"'{n['name']}' would have two legs on '{seg}' after the conversion")
            out.append({"segment": seg, "ip": leg.get("ip")})
        node_nets.append({"node": n["name"], "nets": out})
    plan.node_nets = node_nets

    # ---- apply what we have, then place every VM ------------------------
    def build(links_now: list[dict[str, Any]], stp: Optional[str]) -> dict[str, Any]:
        for sw in switches:
            sw["stp"] = stp
        plan.switches, plan.node_nets, plan.links = switches, node_nets, links_now
        return apply_plan(te_raw, plan)

    # Parallel up links between one pair: the higher-cost ones block.
    pairs: dict[frozenset, list[int]] = {}
    for i, link in enumerate(links):
        if link["state"] is None:
            pairs.setdefault(frozenset((link["a"]["device"], link["b"]["device"])), []).append(i)
    for idx in pairs.values():
        if len(idx) < 2:
            continue
        keep = min(idx, key=lambda i: (link_cost[i] or 0, i))
        for i in idx:
            if i != keep:
                links[i]["state"] = "blocked"
                a, b = links[i]["a"]["device"], links[i]["b"]["device"]
                plan.notes.append(
                    f"'{a}' <-> '{b}' port '{links[i]['a']['port']}' is the redundant link STP "
                    "blocked (higher cost): written `state: blocked`; with `stp: rstp` on both "
                    "switches it would fail over instead (but RSTP, not the table's cost, then "
                    "picks the blocked port)")

    from axrange.switchmodel import _kind, _leg_nets, auto_port_name

    def misplaced(ir: Any, model: Any, member_of: dict[str, str], *, routers: bool):
        """VM legs the table puts on a switch the model does not: routers
        first (they root the depth every host placement reads), then the rest."""
        out: list[dict[str, Any]] = []
        for node in ir.layers.infra.nodes:
            te_name = member_of.get(node.id)
            if te_name is None or te_name in bridge_names or (_kind(node) == "router") != routers:
                continue
            for leg, net in enumerate(_leg_nets(node)):
                want = intended.get((te_name, net))
                if want is None:
                    continue
                port = model.leg_port.get((node.id, net))
                if port is not None and port.switch == want[0]:
                    continue
                name = want[1] if want[1] in single_vm else auto_port_name(node.id, leg)
                out.append({"a": {"device": want[0], "port": name},
                            "b": {"device": node.id, "port": f"eth{leg}"},
                            "mode": "access", "native": None, "vlans": [net], "state": None})
        return out

    ir, model = _expand_model(build(links, None))
    member_of = _member_of(ir, set(by_name))
    links.extend(misplaced(ir, model, member_of, routers=True))
    ir, model = _expand_model(build(links, None))
    explicit = misplaced(ir, model, member_of, routers=False)
    links.extend(explicit)
    if explicit:
        plan.notes.append(
            f"{len(explicit)} VM leg(s) would auto-attach to another switch (or tie between "
            "carriers, NSW-040): wired by explicit access links to the switch the table puts "
            "them on")

    ir, model = _expand_model(build(links, None))
    left = misplaced(ir, model, member_of, routers=True) + misplaced(ir, model, member_of, routers=False)
    if left:
        raise FabricConversionError(
            f"{len(left)} VM leg(s) still land on the wrong switch after wiring, e.g. "
            f"{left[0]['b']['device']} on {left[0]['vlans'][0]}")
    stp = None
    if any(p.rule_id == "NSW-090" for p in model.problems):
        stp = "rstp"
        ir, model = _expand_model(build(links, stp))
        plan.notes.append("the converted fabric still has a loop: every switch runs `stp: rstp`")
    for sw in switches:
        sw["stp"] = stp
    plan.switches = switches
    plan.links = links
    plan.problems = [{"rule_id": p.rule_id, "severity": p.severity, "message": p.message,
                      "location": p.location} for p in model.problems]

    # ---- taps -------------------------------------------------------------
    # A binding on a switch<->switch wire becomes a source of one SPAN session
    # (those ports always exist); a binding on a host-facing partner segment
    # is retargeted to its survivor, which D8 compiles to the VLAN's VM ports
    # -- naming VM ports would break whenever the deploy gate drops a VM.
    wire_segs = {link["a"]["port"] for link in links
                 if link["a"]["device"] in bridge_names and link["b"]["device"] in bridge_names}
    depth = model.depth
    for path in sorted(taps or {}):
        traw = yaml.load(taps[path]) or {}
        bindings = ((traw.get("tap-plan") or {}).get("bindings") or [])
        rw = TapRewrite(path=path)
        groups: dict[tuple, list[tuple[int, str]]] = {}
        seen: set[tuple] = set()
        for i, b in enumerate(bindings):
            sel = (b or {}).get("selector") or {}
            key = (tuple(b.get("sensors") or []), b.get("mode"))
            if sel.get("scope") == "segment" and sel.get("target") not in removed_set:
                seen.add((sel.get("target"),) + key)
        for i, b in enumerate(bindings):
            sel = (b or {}).get("selector") or {}
            target = sel.get("target")
            if sel.get("scope") != "segment" or target not in removed_set:
                continue
            key = (tuple(b.get("sensors") or []), b.get("mode"))
            if target in wire_segs:
                groups.setdefault(key, []).append((i, target))
                continue
            surv = to_survivor[target]
            if (surv,) + key in seen:
                rw.remove.append(i)
            else:
                seen.add((surv,) + key)
                rw.retarget.append({"index": i, "target": surv})
                plan.notes.append(
                    f"{path}: the binding on '{target}' now names '{surv}': on a native switch "
                    "a segment binding mirrors every VM port on the VLAN, across its switches "
                    "(D8)")
        for (sensors, mode), members in groups.items():
            common = set(bridge_names)
            for _, seg in members:
                common &= {d for d in bridge_names if (d, seg) in port_of}
            if not common:
                raise FabricConversionError(
                    f"{path}: the bindings on {[s for _, s in members]} share no switch -- "
                    "one SPAN session mirrors one switch")
            sw = min(common, key=lambda d: (-(depth.get(d) if depth.get(d) is not None else -1), d))
            if len(common) > 1:
                plan.notes.append(
                    f"{path}: {[s for _, s in members]} joins {sorted(common)}; the SPAN session "
                    f"is put on '{sw}' (the deeper one -- a veth link mirrors the same frames "
                    "at either end)")
            rw.remove.extend(i for i, _ in members)
            rw.add_spans.append({"switch": sw,
                                 "sources": [{"port": seg, "dir": "both"} for _, seg in members],
                                 "sensors": list(sensors), "mode": mode})
        if rw.remove or rw.retarget or rw.add_spans:
            rw.remove.sort()
            plan.taps.append(rw)

    plan.notes.append(
        "the planner assigns the VLAN tags (from 2 upward): a twin config has no field for the "
        "real VLAN IDs, so mirrored frames carry the planner's tags")
    plan.notes.append(
        "comments in the files still describe the bridge VMs, and the recipe script is no "
        "longer installed by any node -- edit or delete them by hand")
    return plan
