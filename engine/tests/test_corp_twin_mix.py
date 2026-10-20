"""corp-twin's traffic mix, modelled statically from its personas x a tfc
variant, against the measured profile it is judged by (docs/
WORK_ORDER_corp_twin.md §7).

Live run 3 (2026-09-28, §11) measured the gate-0.9 deploy's mix and found it
nothing like §7: NTP + NetBIOS-DGM were 48-72 % of the bytes (the cast's
`cast-file-server` persona was two `volume: bulk` UDP emits, ~37 Mbit/s of
"NTP"/udp-138 to the sink), SMB was absent, TLS:HTTP was ~1:1 instead of
~30:1, QUIC was 15 % instead of < 1 %, and every "NTP"/"DNS" message was the
volume tier's 256 B-64 KiB payload chunked into 1400 B datagrams.

A 60 s capture is the only real proof, but the mix is decidable offline to
first order: the scripted driver offers `messages x bytes x peers` per firing
every `interval_s` (`drivers.scripted._resolve_emit_schedule` -- reused here,
not re-derived, so the model moves when the engine does). This test pins that
offered mix inside a tolerance of §7, so a persona edit that re-creates an
elephant flow in the wrong protocol fails here instead of in the next live
window.

What the model does NOT know (each is why the tolerances are wide): the
server's reply bytes (the sink's Caddy/bind9 answer little to a tagged
payload; `smbd` on the file-server drains without answering), TCP
retransmission, and a fired emit whose peer refuses the connection (bytes
never reach the wire). It models client-offered bytes plus header overhead.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.personas.drivers.scripted import _UDP_MTU, _resolve_emit_schedule
from axrange.personas.schema import RESERVED_PEER_ROLES, load_persona

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO.parent / "catalog"  # twin content lives at the project root
CORP = CATALOG / "twins" / "corp-twin"
_GATES = {"default": None, "gate90": "0.9"}

# Per-packet header bytes on the wire (Ethernet 14 + IPv4 20 + L4).
_UDP_HDR = 14 + 20 + 8
_TCP_HDR = 14 + 20 + 32
_TCP_MSS = 1448
# SYN, SYN/ACK, ACK, FIN/ACK x2, final ACK x2: one connection's fixed cost.
_TCP_HANDSHAKE_PKTS = 7


def _service(proto: str, transport: str) -> str:
    """The §7 service class an emit lands in on a capture (mix.py's naming)."""
    if proto == "quic" or (proto in ("https", "tls") and transport == "udp"):
        return "quic"
    return {
        "https": "tls", "tls": "tls", "http": "http", "smb": "smb", "dns": "dns",
        "ntp": "ntp", "ssh": "ssh", "ldap": "ldap", "kerberos": "krb", "rdp": "rdp",
        "rpc": "rpc", "ssdp": "ssdp",
    }.get(proto, "other")


def _wire(nbytes: int, transport: str) -> float:
    """One message's bytes on the wire, headers included."""
    if transport == "udp":
        return nbytes + _UDP_HDR * max(1, math.ceil(nbytes / _UDP_MTU))
    segs = max(1, math.ceil(nbytes / _TCP_MSS))
    # data segments + roughly one ACK per two of them + the connection's fixed packets
    return nbytes + _TCP_HDR * (segs + segs / 2 + _TCP_HANDSHAKE_PKTS)


def model_mix(stem: str) -> dict:
    """Offered bytes/s and flows/s per service class for tfc/`stem`.yaml over
    the deploy its gate expands to. Every message is a fresh socket (the
    scripted client's shape), so one message = one flow."""
    from axrange.twinconfig.expand import expand_twin_config

    gate = _GATES[stem]
    ir, _ = expand_twin_config(
        "corp-twin", param_overrides={"DEPLOY_MIN_OCCUPANCY": gate} if gate else None,
    )
    nodes = ir["layers"]["infra"]["nodes"]
    role_of = {n["id"]: n.get("role") for n in nodes}
    by_role: dict[str, list[str]] = {}
    for hid, role in role_of.items():
        by_role.setdefault(role, []).append(hid)

    y = YAML(typ="safe")
    spec = y.load((CORP / "tfc" / f"{stem}.yaml").read_text())["traffic-profile"]
    personas = {}
    rate, flows, scope, per_persona = Counter(), Counter(), Counter(), Counter()
    for a in spec["assignments"]:
        pid = a["persona"]
        if pid not in personas:
            personas[pid] = load_persona(
                y.load((CORP / "personas" / f"{pid}.yaml").read_text()))
        persona = personas[pid]
        assert persona.drive == "scripted", (pid, persona.drive)
        hosts = [a["node"]] if "node" in a else by_role.get(a["role"], [])
        for host in hosts:
            for emit in persona.emits:
                role = emit.peer_role
                if role in RESERVED_PEER_ROLES:
                    peers = 1 if role in ("broadcast", "multicast") else 0
                else:
                    others = [h for h in by_role.get(role, []) if h != host]
                    peers = len(others) if emit.peer_scope == "all" else min(1, len(others))
                if not peers:
                    continue
                sched = _resolve_emit_schedule(emit)
                transport = emit.effective_transport()
                msgs = sched["messages"] * peers / sched["interval_s"]
                b = msgs * _wire(sched["bytes"], transport)
                svc = _service(emit.proto, transport)
                rate[svc] += b
                flows[svc] += msgs
                scope["ext" if role == "sink" else "int"] += b
                per_persona[pid] += b
    return {"bytes": rate, "flows": flows, "scope": scope, "by_persona": per_persona}


# §7's measured shares (disco topology.json, 9 windows, 8.95 GB / 147k flows):
# tls 70.5 %, smb 25.0 %, http 2.5 %, ssh 0.55 %, dns 0.31 % (70 % of flows),
# quic 0.15 %, ntp 0.01 %.
@pytest.mark.parametrize("stem", sorted(_GATES))
def test_modelled_mix_matches_the_measured_profile(stem):
    m = model_mix(stem)
    total = sum(m["bytes"].values())
    share = {k: v / total for k, v in m["bytes"].items()}
    fshare = {k: v / sum(m["flows"].values()) for k, v in m["flows"].items()}

    # TLS dominates, and it is the EXTERNAL web (to the sink) that carries it.
    assert 0.55 <= share["tls"] <= 0.80, share
    assert share["tls"] == max(share.values()), share
    assert m["scope"]["ext"] / total >= 0.6, m["scope"]
    # SMB is the one major internal protocol.
    assert 0.15 <= share["smb"] <= 0.35, share
    assert sorted(share, key=share.get, reverse=True)[:2] == ["tls", "smb"], share
    # TLS:HTTP is ~28:1 in §7 (6.3 GB : 227 MB); anything past 10:1 is the shape.
    assert share["tls"] / share["http"] >= 10, share
    assert share["http"] <= 0.06, share
    # The live-run-3 regressions: NTP / NetBIOS / QUIC were each double digits.
    assert share["ntp"] < 0.02, share
    assert share.get("quic", 0) < 0.02, share
    assert share.get("other", 0) < 0.03, share
    assert share["dns"] < 0.03, share
    # DNS is the flow majority in §7 (70 %); a plurality is what a scripted twin can hold.
    assert fshare["dns"] == max(fshare.values()), fshare


def _all_emits():
    """Every emit of every persona the default/gate90 profiles drive (the
    discovery loop's `disco-exercise*` ride `tfc/exercise.yaml`, not judged
    against §7)."""
    y = YAML(typ="safe")
    pids = set()
    for stem in _GATES:
        spec = y.load((CORP / "tfc" / f"{stem}.yaml").read_text())["traffic-profile"]
        pids |= {a["persona"] for a in spec["assignments"]}
    for pid in sorted(pids):
        p = load_persona(y.load((CORP / "personas" / f"{pid}.yaml").read_text()))
        for e in p.emits:
            yield p.id, e


def test_datagram_protocols_carry_realistic_message_sizes():
    """A real NTP request is 48 B; a DNS query is tens of bytes. Live run 3 put
    1400 B frames under both labels (the volume tier's payload, chunked)."""
    for pid, e in _all_emits():
        size = _resolve_emit_schedule(e)["bytes"]
        if e.proto == "ntp":
            assert size == 48, (pid, e)
        elif e.proto == "dns":
            assert size <= 512, (pid, e)   # the classic UDP DNS ceiling


def test_no_bulk_emit_outside_smb():
    """`bulk` is the elephant-flow tier; §7's only elephants are TLS to the
    internet and one SMB client to the file server."""
    for pid, e in _all_emits():
        if e.volume == "bulk":
            assert e.proto in ("smb", "https"), (pid, e)


def test_the_file_server_is_dialled_on_smb_and_speaks_only_ntp():
    """The file server SERVES SMB (its clients dial it); its own persona is a
    host's background, not a traffic source (live run 3: two bulk UDP emits)."""
    y = YAML(typ="safe")
    fs = load_persona(y.load((CORP / "personas" / "cast-file-server.yaml").read_text()))
    assert {e.proto for e in fs.emits} == {"ntp"}
    dialers = [(pid, e) for pid, e in _all_emits()
               if e.proto == "smb" and e.peer_role == "file-server"]
    assert dialers and all(e.ports == (445,) for _pid, e in dialers), dialers
