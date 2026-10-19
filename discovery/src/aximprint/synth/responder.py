"""The ECHO — a labelled fallback for ports this project has no real server for.

**Scope narrowed on 2026-09-05, and the narrowing is the point** (`synth/services.py`; axRange
`docs/WORK_ORDER_protocol_faithful_services.md` D8(a), Authority: user). This module used to be a
twin's answer to EVERY dialled role. It is now the fallback for the ports no recipe in
`services.SERVER_RECIPES` covers -- QUIC, Splunk's proprietary 9997, NetBIOS, the ephemeral
sockets -- and `services.plan_node_services` refuses at RENDER time to hand it a port a real
daemon on that host owns. The mechanism below (`_RESPONDER_PY`) is unchanged and is imported by
`services._render_echo`; what changed is what reaches it.

Kept rather than removed, because silence is not free either: an unserved port draws an ICMP
unreachable that Zeek scores as a *fabricated reverse local edge*, and every echoed port is
reported as a `compiler`-class choice in the provenance sidecar so it can never pass for an
observation.

The original note, still the reason the mechanism exists:

Measured on the deployed corpapcn twin (iteration 11), the defect this exists to fix:

    twin edge protos:   icmp 225, udp 148, tcp 145, ntp 39, ssl 3, dns 1
    source edge protos: ssl 243, tcp 179, ntp 150, icmp 39, udp 36, dns 17

The twin is ICMP-dominated and the source is SSL-dominated, because NOTHING IN THE TWIN
LISTENS. A `dns-resolver` node is a bare VM with no DNS server, so every client emit at it
draws an ICMP port-unreachable from the kernel -- 103 manufactured `dns-resolver ->
generic-endpoint` edges against a source that has 3 -- while the forward conversation never
completes and Zeek cannot classify a protocol whose response never comes (exactly ONE `dns`
edge in the whole twin).

It also silently caps byte volume: axRange's own note, "TCP byte volume only materializes
against a real listener -- a refused connect is 2 packets, not 64 MiB."

Delivered as an `install: [{script: ...}]` action, so it runs through `run-script`
(OP-APP-030) IN-GUEST with no package manager and no egress -- a twin segment often has
neither. Started as a named transient systemd unit rather than `nohup`, because the
qemu-guest-agent session that launches a background process is reaped when it ends and takes
the process with it (axRange LES/GAP-020, the same failure that left standing sensors dead).
"""

from __future__ import annotations

from typing import Any

# A responder is only useful for ports a persona actually dials. Reserved pseudo-peers
# resolve to wire addresses, not to a host that could listen.
_NOT_A_HOST = {"multicast", "broadcast", "internet", "egress"}

_RESPONDER_PY = r'''
import errno, socket, sys, threading

def _serve_tcp(s):
    while True:
        try:
            conn, _ = s.accept()
        except OSError:
            continue
        threading.Thread(target=_echo, args=(conn,), daemon=True).start()

def _echo(conn):
    # Echo rather than drain: a persona measuring bytes needs the far side to actually
    # move them back, and an echo keeps the conversation shaped like a real exchange.
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            conn.sendall(data)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass

def _serve_udp(s):
    while True:
        try:
            data, addr = s.recvfrom(65536)
            s.sendto(data, addr)
        except OSError:
            continue

# Bind EVERY socket up front, in the main thread, and die loudly if any one fails.
# Binding inside the per-port thread hides a partial failure completely: the thread
# raises, the process keeps running because its siblings are healthy, and
# `systemctl is-active` still says "active" -- measured on dns-resolver-10-80-64,
# where the port-53 thread crashed on EADDRINUSE (systemd-resolved owns 53) while the
# unit reported healthy and the op passed. A responder serving SOME of its ports is
# the silent-partial-success case, which is worse than a hard failure because the
# fingerprint then blames the personas.
_socks = []
for spec in sys.argv[1:]:
    p, _, proto = spec.partition("/")
    port = int(p)
    try:
        if proto == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port)); s.listen(128)
    except OSError as exc:
        # Already served by a REAL daemon is a success, not a failure: a dns-resolver node
        # installs bind9 and `named` owns :53 (measured on dev45 -- the responder was
        # competing with the very service that makes the role real). The point is that
        # SOMETHING answers, so a port another process holds needs no responder. Anything
        # else is a genuine failure and still exits non-zero.
        if exc.errno == errno.EADDRINUSE:
            sys.stderr.write("axr-responder: %s already served by another process, "
                             "skipping\n" % spec)
            continue
        sys.stderr.write("axr-responder: cannot bind %s: %s\n" % (spec, exc))
        raise SystemExit(1)
    _socks.append((s, proto))

for s, proto in _socks:
    target = _serve_udp if proto == "udp" else _serve_tcp
    threading.Thread(target=target, args=(s,), daemon=True).start()

threading.Event().wait()
'''


def listener_ports(personas: dict[str, Any]) -> dict[str, set[tuple[int, str]]]:
    """`{peer_role: {(port, transport)}}` — every (port, transport) some persona DIALS at a role.

    **This is the dial set, and it is the WRONG input for an observed host's declared listen
    set** (axRange `docs/WORK_ORDER_protocol_faithful_services.md` §2, which measured what taking
    it cost: `internet-sink-1` bound 88/tcp while the two `web-tier` nodes bound nothing at all).
    Two callers survive, both legitimate:

      * **the internet sink**, whose declared set this genuinely is — nothing observed a
        synthetic node, and the ports it is aimed at are the ports it exists to answer (§7.4);
      * **the cross-check** — a port dialled at a role no node was observed answering is
        REPORTED in `caveats`, never added, because a fabricated listener in the artifact being
        measured is worse than a known gap in it.
    """
    out: dict[str, set[tuple[int, str]]] = {}
    for doc in personas.values():
        persona = doc.get("persona") or {}
        for emit in persona.get("emits") or []:
            peer = emit.get("peer_role")
            if not peer or peer in _NOT_A_HOST:
                continue
            transport = emit.get("transport") or "tcp"
            for port in emit.get("ports") or []:
                out.setdefault(peer, set()).add((int(port), transport))
    return out


# `_needs_port_53()` REMOVED 2026-09-05 -- dead at HEAD, not made dead by this change: it had no
# caller anywhere in `src/`, `tests/` or `scripts/` before the narrowing above either. Its job
# (does this port list contain 53, so the systemd-resolved stub has to be displaced first) is now
# a positive branch inside `services._render_bind9`, keyed on the bind9 recipe having fired
# rather than on a port scan of the list.


def is_windows_image(image: str) -> bool:
    """Windows nodes are skipped by `listener_ports`' consumers for now.

    The responder is a POSIX shell action delivered through `run-script`, which on a Windows
    guest runs the file as PowerShell -- measured on dev45: `cat > ... <<'EOF'` fails with
    "Missing file specification after redirection operator". A PowerShell responder needs a
    different shape entirely (a .NET TcpListener under a scheduled task, since a background
    job dies with its session), and the roles that actually drive this twin's dominant edges
    -- web-tier and dns-resolver -- are Linux. Skipping Windows is a scoped, recorded gap
    rather than a half-working script that fails at deploy time.
    """
    return "win" in (image or "").lower()


# `responder_script()` REMOVED 2026-09-05 with the scope narrowing at the top of this module.
# Its whole body is now `services._render_echo`, emitted as one section of a `serve-<node>.sh`
# beside the real daemons -- and a second renderer of the same shell block, called by nothing,
# is the drift hazard `LES-101` names ("a declared element no code reads is documentation
# wearing a data costume"). `_RESPONDER_PY` above is the shared payload and did not move; the
# four properties the removed function's tests asserted -- detached start, fail-loud,
# bind-before-thread, EADDRINUSE-is-a-skip -- are asserted against the shipped path in
# `tests/test_synth_services.py`.
