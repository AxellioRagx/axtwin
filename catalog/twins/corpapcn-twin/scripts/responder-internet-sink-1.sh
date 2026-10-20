set -eu
mkdir -p /usr/local/lib/axr
cat > /usr/local/lib/axr/responder.py <<'AXR_RESPONDER_EOF'
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
AXR_RESPONDER_EOF
systemctl stop axr-responder.service 2>/dev/null || true
systemd-run --unit=axr-responder --collect /usr/bin/python3 /usr/local/lib/axr/responder.py 53/udp 88/tcp 123/udp 135/tcp 138/udp 389/tcp 443/tcp 443/udp
sleep 1
systemctl is-active --quiet axr-responder.service
