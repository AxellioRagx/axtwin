"""`AXIMPRINT_SOURCE_TLS_VERIFY=0`: an https source whose certificate is self-signed with no
SAN for the address it is reached by (an XPress sensor appliance, `CN=XPress-SW`) is refused
by default, and read once the control plane turns verification off for that source."""
from __future__ import annotations

import http.server
import shutil
import ssl
import subprocess
import threading

import pytest

from aximprint.ingest.axquery.sources import AxQueryError, AxQuerySource
from aximprint.ingest.siem.http import ENV_TLS_VERIFY, tls_context

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")


@pytest.fixture
def self_signed_server(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=XPress-SW", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )

    class _Info(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"first_ts": 1, "last_ts": 2}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Info)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"https://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_a_self_signed_sensor_is_refused_by_default_with_a_hint(self_signed_server, monkeypatch):
    monkeypatch.delenv(ENV_TLS_VERIFY, raising=False)
    with pytest.raises(AxQueryError) as exc:
        AxQuerySource(f"axquery:{self_signed_server}").info()
    assert "CERTIFICATE_VERIFY_FAILED" in str(exc.value) and f"{ENV_TLS_VERIFY}=0" in str(exc.value)


def test_verification_off_reads_it(self_signed_server, monkeypatch):
    monkeypatch.setenv(ENV_TLS_VERIFY, "0")
    assert AxQuerySource(f"axquery:{self_signed_server}").info()["last_ts"] == 2


def test_only_an_explicit_zero_turns_verification_off():
    assert tls_context({}) is None
    assert tls_context({ENV_TLS_VERIFY: "1"}) is None
    assert tls_context({ENV_TLS_VERIFY: "false"}) is None
    ctx = tls_context({ENV_TLS_VERIFY: "0"})
    assert ctx is not None and ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname
