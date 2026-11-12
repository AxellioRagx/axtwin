"""SPAN sessions on native switches (`docs/design/native-switches.md` §6;
Authority: user, D7/D8, 2026-11-12). Kept beside `taps.py` rather than in it
(`Claude.md`: that module carries hard-won cluster fixes and is changed only
where a seam needs it): `taps.py` parses the `span:` binding and calls the
three functions here at its existing resolve and compose points.

  * `parse_span` -- the binding's shape;
  * `resolve_sessions` -- span bindings, PLUS every already-resolved interface
    that sits on a switch-hosted network (D8: the existing scopes compile to
    an OVS mirror on the carrier switch instead of a `tc` mirror), become
    `SpanSession`s, one per (switch, sensor, VLAN set);
  * `compose_span_ops` -- one keyed gretap (`attach-gre-tunnel`, unchanged op)
    and one `install-span` per session, plus a removing `install-span` for
    every session a prior apply made that this one no longer wants.

Directions are switch-port directions (`tapscopes.DIRECTIONS`): `rx` is what
the switch receives on a port (the far end's transmit), `tx` what it sends
out. For a VM port that is exactly the `tc` reading (`rx` = ingress on the
VM's tap), so an interface resolved by a scope keeps its direction when it
is compiled onto a switch: `rx` -> `select_src_port`, `tx` ->
`select_dst_port`, `both` -> both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from axrange.tapscopes import DIRECTIONS

SPAN_KEYS = {"switch", "sources"}
SOURCE_KEYS = {"port", "dir", "vlans"}


class SpanError(ValueError):
    """A span binding that cannot be parsed or resolved (taps.py maps it to
    its own `TapPlanError`, status 400)."""


def parse_span(raw: Any, where: str) -> dict[str, Any]:
    """Validate a binding's `span:` mapping and return it normalized:
    `{switch, sources: [{port, dir, vlans?}]}`."""
    if not isinstance(raw, dict) or not raw:
        raise SpanError(f"{where}: 'span' must be a non-empty mapping")
    unknown = set(raw) - SPAN_KEYS
    if unknown:
        raise SpanError(f"{where}: span has unknown key(s) {sorted(unknown)} "
                        f"(allowed: {sorted(SPAN_KEYS)})")
    switch = raw.get("switch")
    if not isinstance(switch, str) or not switch:
        raise SpanError(f"{where}: span.switch must be a non-empty string")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SpanError(f"{where}: span.sources must be a non-empty list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for j, s in enumerate(sources):
        w = f"{where}: span.sources[{j}]"
        if not isinstance(s, dict):
            raise SpanError(f"{w} must be a mapping")
        bad = set(s) - SOURCE_KEYS
        if bad:
            raise SpanError(f"{w} has unknown key(s) {sorted(bad)} (allowed: {sorted(SOURCE_KEYS)})")
        port = s.get("port")
        if not isinstance(port, str) or not port:
            raise SpanError(f"{w}.port must be a non-empty string")
        if port in seen:
            raise SpanError(f"{w}: port {port!r} is listed twice")
        seen.add(port)
        direction = s.get("dir", "both")
        if direction not in DIRECTIONS:
            raise SpanError(f"{w}.dir {direction!r} is not one of {', '.join(DIRECTIONS)}")
        entry: dict[str, Any] = {"port": port, "dir": direction}
        vlans = s.get("vlans")
        if vlans is not None:
            if not isinstance(vlans, list) or not vlans or not all(isinstance(v, str) and v for v in vlans):
                raise SpanError(f"{w}.vlans must be a non-empty list of segment names")
            entry["vlans"] = sorted(set(vlans))
        out.append(entry)
    return {"switch": switch, "sources": out}


def span_label(span: dict[str, Any]) -> str:
    srcs = ",".join(f"{s['port']}:{s['dir']}" for s in span.get("sources", []))
    return f"span:{span.get('switch')}[{srcs}]"


@dataclass
class SpanSession:
    """One OVS mirror: a switch's selection into one sensor's gretap."""

    switch: str
    sensor: str
    bridge: str
    vlans: tuple[str, ...] = ()           # narrowing segment names; () = all
    vlan_tags: tuple[int, ...] = ()
    src: dict[str, str] = field(default_factory=dict)   # ovs port -> switch port label
    dst: dict[str, str] = field(default_factory=dict)
    # VM port -> its VM's platform handle (vmid / domain UUID): where the
    # adapter keeps the fragment that re-adds the port after a restart (R5).
    owners: dict[str, Any] = field(default_factory=dict)
    # A VM on a source port, for the capture heartbeat: (host, net).
    injectors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.switch}|{self.sensor}|{','.join(self.vlans)}"


def _switch_index(twin: Any) -> dict[str, Any]:
    return {s.name: s for s in getattr(twin, "switches", []) or []}


def _add(sessions: dict[tuple[str, str, tuple[str, ...]], SpanSession], sw: Any, sensor: str,
         vlans: tuple[str, ...], ovs_port: str, label: str, direction: str,
         injector: Optional[tuple[str, str]], owner: Any = None) -> None:
    k = (sw.name, sensor, vlans)
    sess = sessions.get(k)
    if sess is None:
        sess = SpanSession(switch=sw.name, sensor=sensor, bridge=sw.bridge, vlans=vlans,
                           vlan_tags=tuple(sorted(sw.vlans[v] for v in vlans)))
        sessions[k] = sess
    if direction in ("rx", "both"):
        sess.src[ovs_port] = label
        if injector is not None and injector not in sess.injectors:
            sess.injectors.append(injector)
    if direction in ("tx", "both"):
        sess.dst[ovs_port] = label
    if owner is not None:
        sess.owners[ovs_port] = owner


def resolve_sessions(
    span_bindings: list[tuple[int, Any]],
    iface_new_sensors: dict[tuple[str, str, int], dict[str, str]],
    twin: Any,
    *,
    validate_sensor: Callable[[Any, str], None],
    binding_directions: Optional[dict[int, str]] = None,
) -> tuple[dict[tuple[str, str, int], dict[str, str]], list[SpanSession]]:
    """`(iface_new_sensors for SDN-hosted legs only, span sessions)`.

    `span_bindings` is `[(binding index, TapBindingSpec)]` for every binding
    carrying `span:`; `iface_new_sensors` is `taps._resolve_bindings`' output
    for the others. An interface whose leg is on a native switch
    (`HostNet.switch`) leaves the `tc` path and joins its switch's session
    (D8); every other interface is returned untouched, so a twin with no
    native switch resolves exactly as before."""
    switches = _switch_index(twin)
    sessions: dict[tuple[str, str, tuple[str, ...]], SpanSession] = {}
    leg_switch: dict[tuple[str, str], tuple[str, str]] = {}
    vm_of = {h.host_id: h.vmid for h in getattr(twin, "hosts", []) or [] if h.vmid is not None}
    for h in getattr(twin, "hosts", []) or []:
        for n in h.nets:
            if n.switch and n.port:
                leg_switch[(h.host_id, n.net_id)] = (n.switch, n.port)

    sdn: dict[tuple[str, str, int], dict[str, str]] = {}
    for iface, sids in iface_new_sensors.items():
        host, net, _nic = iface
        hit = leg_switch.get((host, net))
        if hit is None:
            sdn[iface] = sids
            continue
        sw = switches.get(hit[0])
        port = next((p for p in sw.ports if p.port == hit[1]), None) if sw else None
        if sw is None or port is None or not port.ovs_port or not sw.bridge:
            raise SpanError(
                f"interface {host}:{net} is on native switch {hit[0]!r} port {hit[1]!r}, "
                "but the twin's inventory has no OVS port for it (is the twin fully deployed?)")
        for sid, direction in sids.items():
            _add(sessions, sw, sid, (), port.ovs_port, port.port, direction, (host, net),
                 vm_of.get(host))

    for i, b in span_bindings:
        span = b.span
        sw = switches.get(span["switch"])
        if sw is None:
            names = ", ".join(sorted(switches)) or "none"
            raise SpanError(
                f"binding {i}: {span_label(span)}: {span['switch']!r} is not a native switch "
                f"of the target twin (native switches: {names}); a logical switch has no "
                "ports to mirror")
        if not sw.bridge:
            raise SpanError(f"binding {i}: switch {sw.name!r} has no bridge recorded "
                            "(is the twin fully deployed?)")
        ports = {p.port: p for p in sw.ports}
        for sid in b.sensors:
            validate_sensor(b, sid)
        dirs: set[str] = set()
        for src in span["sources"]:
            p = ports.get(src["port"])
            if p is None:
                raise SpanError(
                    f"binding {i}: {span_label(span)}: port {src['port']!r} is not on "
                    f"{sw.name!r} (ports: {', '.join(sorted(ports))})")
            if not p.ovs_port:
                raise SpanError(
                    f"binding {i}: {span_label(span)}: port {p.port!r} on {sw.name!r} has "
                    "nothing plugged in")
            vlans: tuple[str, ...] = ()
            if src.get("vlans"):
                missing = [v for v in src["vlans"] if v not in p.vlans]
                if missing:
                    raise SpanError(
                        f"binding {i}: {span_label(span)}: port {p.port!r} does not carry "
                        f"{missing} (it carries {p.vlans})")
                vlans = tuple(src["vlans"])
            far = p.far or {}
            injector = None
            if far.get("kind") in ("host", "router") and p.vlans:
                injector = (str(far.get("device")), p.vlans[0])
            owner = vm_of.get(str(far.get("device"))) if "leg" in far else None
            for sid in b.sensors:
                _add(sessions, sw, sid, vlans, p.ovs_port, p.port, src["dir"], injector, owner)
            dirs.add(src["dir"])
        if binding_directions is not None:
            binding_directions[i] = "+".join(sorted(dirs))
    return sdn, [sessions[k] for k in sorted(sessions)]


def compose_span_ops(
    plan: Any,
    plan_id: str,
    sessions: list[SpanSession],
    sensor_by_id: dict[str, Any],
    target_node: str,
    *,
    make_op: Callable[..., Any],
    gre_port_for: Callable[[str, str], str],
    prior_spans: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    verify_inputs: Optional[Callable[["SpanSession", str], Optional[dict[str, Any]]]] = None,
) -> None:
    """Add the span ops to `plan` (mutated in place). `verify_inputs(session,
    gre_port)` returns a `verify-capture` input dict for a session bound to a
    standing (non-external) sensor, or None when no injector can run it."""
    gre_ops: dict[tuple[str, str], Any] = {}
    wanted_keys = {s.key for s in sessions}
    for sess in sessions:
        if not sess.src and not sess.dst:
            continue
        pair = (sess.switch, sess.sensor)
        gre = gre_port_for(sess.bridge, sess.sensor)
        if pair not in gre_ops:
            sensor = sensor_by_id[sess.sensor]
            gop = make_op(
                plan_id, "attach-gre-tunnel", {"switch": sess.switch, "sensor": sess.sensor},
                {
                    "bridge_name": sess.bridge,
                    "net_id": f"switch:{sess.switch}",
                    "sensor_id": sess.sensor,
                    "sensor_transport_ip": sensor.transport_ip,
                    "target_node": target_node,
                },
            )
            plan.add_op(gop)
            gre_ops[pair] = gop
        sop = make_op(
            plan_id, "install-span",
            {"switch": sess.switch, "sensor": sess.sensor, "mirror": sess.key},
            {
                "bridge_name": sess.bridge,
                "mirror_key": sess.key,
                "src_ports": sorted(sess.src),
                "dst_ports": sorted(sess.dst),
                "vlan_tags": list(sess.vlan_tags),
                "gre_port": gre,
                "port_owners": {p: sess.owners[p] for p in sorted(sess.owners)},
                "remove": False,
                "target_node": target_node,
                "sensor_id": sess.sensor,
                # labels for `tap info` / the console, not read by the adapter
                "src_labels": sorted(set(sess.src.values())),
                "dst_labels": sorted(set(sess.dst.values())),
            },
        )
        plan.add_op(sop)
        plan.add_edge(gre_ops[pair].op_id, sop.op_id, "ordering")
        if not sensor_by_id[sess.sensor].deploy_id:
            if warnings is not None:
                warnings.append(
                    f"capture UNVERIFIED for external sensor {sess.sensor!r} on switch "
                    f"{sess.switch!r} (span) — operator-managed sink, no verify-capture "
                    "heartbeat; confirm capture out-of-band (packetXpress / tcpdump on gre-cap)")
            continue
        vin = verify_inputs(sess, gre) if verify_inputs is not None else None
        if vin is None:
            if warnings is not None:
                warnings.append(
                    f"verify-capture heartbeat for span on {sess.switch!r} / sensor "
                    f"{sess.sensor!r} SKIPPED -- no VM on a source port can inject the "
                    "sentinel; confirm capture out-of-band")
            continue
        vop = make_op(
            plan_id, "verify-capture",
            {"switch": sess.switch, "sensor": sess.sensor, "host": vin["injector_host_id"],
             "mirror": sess.key},
            vin,
        )
        plan.add_op(vop)
        plan.add_edge(sop.op_id, vop.op_id, "ordering")

    # De-scope: a session a prior apply installed that this one does not want.
    live_pairs = {(s.switch, s.sensor) for s in sessions if s.src or s.dst}
    for prior in prior_spans or []:
        key = prior.get("mirror_key")
        if not key or key in wanted_keys:
            continue
        pair = (prior.get("switch"), prior.get("sensor"))
        rop = make_op(
            plan_id, "install-span",
            {"switch": str(pair[0]), "sensor": str(pair[1]), "mirror": key, "op": "remove"},
            {
                "bridge_name": prior.get("bridge_name"),
                "mirror_key": key,
                "src_ports": [], "dst_ports": [], "vlan_tags": [],
                "gre_port": prior.get("gre_port"),
                "remove": True,
                # the gretap goes too when no session of this (switch, sensor) remains
                "delete_gre": pair not in live_pairs,
                "target_node": target_node,
                "sensor_id": str(pair[1]),
            },
        )
        plan.add_op(rop)
