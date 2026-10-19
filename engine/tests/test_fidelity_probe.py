"""The in-guest probe harness (S3): `probe.py`'s pure render/parse and
`runner.py`'s QGA execution against a fake guest agent.

Nothing here reaches a cluster or a config file: every backend seam the
runner offers is injected (LES-208). The rendered script IS executed, but
only in-process against local sockets, to prove the protocol handshakes
actually work rather than only that the render contains the right words.
"""
from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import threading

import pytest

from axrange.fidelity.probe import (
    BEGIN_MARKER,
    EXCLUDED_NOT_A_SERVICE,
    EXCLUDED_NO_PROBE,
    END_MARKER,
    FAIL,
    INCONCLUSIVE,
    PASS,
    ProbeRenderError,
    ProbeTarget,
    check_containment,
    method_for_port,
    parse_listen_output,
    parse_listen_port,
    classify_listen_port,
    parse_probe_output,
    render_probe_script,
    target_key,
)
from axrange.fidelity import probe as probe_mod
from axrange.fidelity.runner import (
    QgaProbeRunner,
    compose_probe_targets,
    select_probe_peer,
)
from tests.fidelity_support import FakeGuestAgent


def _t(key="h:80/tcp", ip="10.0.0.5", port=80, transport="tcp", method="http",
       peer_vmid=42, peer="peer-a"):
    return ProbeTarget(
        key=key, host_id=key.split(":")[0], ip=ip, segment="seg", port=port,
        transport=transport, method=method, peer_host_id=peer,
        peer_vmid=peer_vmid,
    )


# ---------------------------------------------------------------------------
# Method selection
# ---------------------------------------------------------------------------
def test_method_follows_the_observed_ad_profile():
    """§3.3: without the AD four, row 5 could never pass on the one host
    carrying 12 of the observed ports, and the gate would be hollow."""
    assert method_for_port(53, "udp") == "dns"
    assert method_for_port(53, "tcp") == "dns"
    assert method_for_port(88, "tcp") == "krb"
    assert method_for_port(389, "tcp") == "ldap"
    assert method_for_port(3268, "tcp") == "ldap"
    assert method_for_port(445, "tcp") == "smb"
    assert method_for_port(3389, "tcp") == "rdp"
    assert method_for_port(123, "udp") == "ntp"
    assert method_for_port(80, "tcp") == "http"
    assert method_for_port(443, "tcp") == "https"


def test_the_ad_profile_is_covered_end_to_end():
    """RULED 2026-09-05: after the widened method set, EVERY port in the
    standing twin's observed listen set that survives the denominator's own
    exclusions has a protocol-aware method. A `connect` left in there would
    make row 5 permanently `not-computed` for a reason that has nothing to
    do with the twin."""
    assert method_for_port(135, "tcp") == "dcerpc"
    assert method_for_port(137, "udp") == "nbns"
    assert method_for_port(389, "udp") == "cldap"
    assert method_for_port(67, "udp") == "dhcp"
    corpapcn_ports = [
        (53, "udp"), (53, "tcp"), (135, "tcp"), (137, "udp"), (123, "udp"),
        (389, "tcp"), (389, "udp"), (445, "tcp"), (88, "tcp"), (88, "udp"),
        (3268, "tcp"), (3389, "tcp"), (443, "tcp"), (80, "tcp"), (8006, "tcp"),
        (67, "udp"),
    ]
    for port, transport in corpapcn_ports:
        method, excluded, _reason = classify_listen_port(port, transport)
        assert not excluded, (port, transport, excluded)
        assert _t(method=method).protocol_aware, (port, transport, method)


def test_unmapped_ports_get_a_never_passing_method():
    assert method_for_port(5432, "tcp") == "connect"
    assert method_for_port(1812, "udp") == "udp-connect"
    assert not _t(method="connect").protocol_aware
    assert not _t(method="udp-connect").protocol_aware


def test_ruled_exclusions_are_classified_with_a_reason():
    """Every exclusion is a decision, and each one is NAMED. A silent
    filter is as dishonest as a vacuous pass (LES-101 cuts both ways)."""
    for port in (49152, 49667, 57086, 63153):
        method, cat, reason = classify_listen_port(port, "tcp")
        assert (method, cat) == ("", EXCLUDED_NOT_A_SERVICE)
        assert "ephemeral" in reason
    assert classify_listen_port(7680, "tcp")[1] == EXCLUDED_NOT_A_SERVICE
    assert classify_listen_port(9997, "tcp")[1] == EXCLUDED_NO_PROBE
    assert classify_listen_port(3389, "udp")[1] == EXCLUDED_NO_PROBE
    # ...and 3389/TCP is NOT excluded: it carries the RDP fact.
    assert classify_listen_port(3389, "tcp") == ("rdp", "", "")
    # QUIC (GAP-061, D7 (a)): no real HTTP/3 server on the twin and no
    # stdlib-expressible probe, so it is EXCLUDED rather than left to sit
    # permanently `inconclusive` and block row 5 from ever passing.
    assert classify_listen_port(443, "udp")[1] == EXCLUDED_NO_PROBE
    # ...while 443/TCP is real (nginx + a self-signed cert) and stays in.
    assert classify_listen_port(443, "tcp") == ("https", "", "")
    # One below the floor stays in.
    assert classify_listen_port(49151, "tcp")[1] == ""


def test_the_published_denominator_note_tracks_the_exclusion_table():
    """review S2: the note row 5 publishes is DERIVED from `_EXCLUSIONS` +
    `EPHEMERAL_PORT_FLOOR`, never restated. Adding QUIC to the table left two
    hand-written prose lists claiming a smaller set -- in a document whose
    whole point is that a reader can trust what it says was taken out
    (LES-101). Derived from the table here too, so a new exclusion cannot
    pass this test without appearing in the note (LES-223)."""
    from axrange.fidelity.probe import _EXCLUSIONS, EPHEMERAL_PORT_FLOOR
    from axrange.fidelity.rows import _denominator_note

    note = _denominator_note()
    for (port, transport), (category, _reason) in _EXCLUSIONS.items():
        assert f"{port}/{transport}" in note, (port, transport)
        assert category in note
    assert str(EPHEMERAL_PORT_FLOOR) in note
    # Each category is named exactly once -- the floor is folded into its
    # category rather than listed as a separate clause.
    for category in {c for c, _ in _EXCLUSIONS.values()}:
        assert note.count(f"'{category}'") == 1, category


def test_every_exclusion_has_an_entry_in_the_honest_gap_register():
    """S5 (`docs/WORK_ORDER_protocol_faithful_services.md`): an observed port
    the twin cannot reproduce as a real service is recorded in
    `docs/PRODUCT_GAPS.md` with its reason -- otherwise a reader learns the
    twin's limits from a code comment. Derived from `_EXCLUSIONS` itself, not
    a mirrored list, so a new exclusion fails here until it is written down
    (LES-223)."""
    import re
    from pathlib import Path

    from axrange.fidelity.probe import _EXCLUSIONS, EPHEMERAL_PORT_FLOOR

    register = (
        Path(__file__).resolve().parent.parent / "docs" / "PRODUCT_GAPS.md"
    ).read_text()
    # Scoped to the GAP SECTIONS, not the whole file: a bare substring search
    # passes on a port number mentioned once in unrelated prose, which is the
    # failure mode this test exists to catch -- "written down somewhere" is
    # not the bar, "has an entry" is.
    parts = re.split(r"^## (GAP-\d+)", register, flags=re.M)
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    assert sections, "no GAP sections parsed out of docs/PRODUCT_GAPS.md"
    for port, transport in _EXCLUSIONS:
        token = f"{port}/{transport}"
        assert any(token in body for body in sections.values()), (
            f"{token} is excluded from row 5's denominator but has no entry "
            "in docs/PRODUCT_GAPS.md"
        )
    assert any(str(EPHEMERAL_PORT_FLOOR) in body for body in sections.values())
    # ...and the converse: every GAP id the exclusion table's own comments
    # name must actually exist, so a renumbered/removed gap fails here rather
    # than leaving a dangling pointer in a code comment.
    probe_src = (
        Path(__file__).resolve().parent.parent
        / "axrange" / "fidelity" / "probe.py"
    ).read_text()
    named = set(re.findall(r"GAP-\d+", probe_src))
    assert named, "probe.py names no GAP ids at all"
    assert named <= set(sections), f"dangling gap ids: {sorted(named - set(sections))}"


def test_target_key_is_the_one_join_between_runner_and_rows():
    """LES-037: two independent f-strings that must agree character for
    character is the join silently returning nothing."""
    assert target_key("web01", 80, "tcp") == "web01:80/tcp"
    assert target_key("web01", "80", "tcp") == "web01:80/tcp"


def test_parse_listen_port():
    assert parse_listen_port("53/udp") == (53, "udp")
    assert parse_listen_port("3389/tcp") == (3389, "tcp")
    assert parse_listen_port("garbage") is None
    assert parse_listen_port("53/sctp") is None
    assert parse_listen_port("70000/tcp") is None


# ---------------------------------------------------------------------------
# Render: deterministic, contained, never vacuous
# ---------------------------------------------------------------------------
def test_render_is_byte_deterministic():
    targets = [_t("b:443/tcp", port=443, method="https"), _t()]
    first = render_probe_script(targets)
    second = render_probe_script(list(reversed(targets)))
    assert first == second
    compile(first, "<probe>", "exec")


def test_render_bakes_every_address_as_a_literal():
    script = render_probe_script([_t(ip="10.9.9.9")])
    assert '"ip": "10.9.9.9"' in script
    # A containment assertion, mirroring the `drive: command` pin: the guest
    # is never handed a name to resolve.
    assert "gethostbyname" not in script
    assert "getaddrinfo" not in script


def test_render_refuses_a_non_literal_address():
    with pytest.raises(ProbeRenderError, match="not an IP literal"):
        render_probe_script([_t(ip="fileserver.corp.lab")])
    with pytest.raises(ProbeRenderError, match="not an IP literal"):
        check_containment([_t(ip="")])


def test_render_refuses_an_empty_target_set():
    """LES-101: an empty probe must never render, because its result would
    be a vacuous 100%."""
    with pytest.raises(ProbeRenderError, match="empty target set"):
        render_probe_script([])


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def _framed(results):
    return f"MOTD noise\n{BEGIN_MARKER}\n{json.dumps({'results': results})}\n{END_MARKER}\n"


def test_parse_ignores_surrounding_noise():
    t = _t()
    out = parse_probe_output(
        _framed([{"key": t.key, "status": "pass", "detail": "HTTP/1.0 200 OK"}]), [t],
    )
    assert out[t.key].status == PASS
    assert out[t.key].detail == "HTTP/1.0 200 OK"


def test_parse_downgrades_a_pass_from_a_non_protocol_aware_method():
    """Enforced at BOTH ends, so a future template edit cannot quietly
    promote a bare connect into a gate pass."""
    t = _t(method="connect")
    out = parse_probe_output(_framed([{"key": t.key, "status": "pass"}]), [t])
    assert out[t.key].status == INCONCLUSIVE


def test_parse_fails_loud_on_an_unframed_result():
    with pytest.raises(ProbeRenderError, match="no framed result"):
        parse_probe_output("python3: command not found", [_t()])


def test_parse_fails_loud_on_non_json():
    with pytest.raises(ProbeRenderError, match="not JSON"):
        parse_probe_output(f"{BEGIN_MARKER}\nnope\n{END_MARKER}", [_t()])


def test_parse_listen_output_reads_only_the_local_address_column():
    out = parse_listen_output(
        "udp   UNCONN 0 0 0.0.0.0:53   0.0.0.0:*\n"
        "tcp   LISTEN 0 128 [::]:22    [::]:*\n"
        "tcp   LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
        "garbage line\n"
    )
    assert out == ["22/tcp", "53/udp"]


# ---------------------------------------------------------------------------
# The rendered client, run for real against local sockets
# ---------------------------------------------------------------------------
def _canned_tcp_server(reply: bytes) -> int:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)

    def run():
        try:
            conn, _ = s.accept()
            conn.recv(8192)
            conn.sendall(reply)
            conn.close()
        finally:
            s.close()

    threading.Thread(target=run, daemon=True).start()
    return s.getsockname()[1]


def test_rendered_probe_speaks_the_ad_protocols_on_the_wire(tmp_path):
    """A functional wire test, not a render assertion: each handshake is run
    against a canned responder and must be classified correctly."""
    krb = _canned_tcp_server(struct.pack("!I", 5) + b"\x7e\x03\x02\x01\x05")
    ldap = _canned_tcp_server(bytes.fromhex("300c02010165070a010004000400"))
    smb = _canned_tcp_server(struct.pack("!I", 68) + b"\xfeSMB" + 64 * b"\0")
    rdp = _canned_tcp_server(b"\x03\x00\x00\x0b\x06\xd0\x00\x00\x12\x34\x00")
    junk = _canned_tcp_server(b"hello there")

    targets = [
        _t("k:88/tcp", ip="127.0.0.1", port=krb, method="krb"),
        _t("l:389/tcp", ip="127.0.0.1", port=ldap, method="ldap"),
        _t("s:445/tcp", ip="127.0.0.1", port=smb, method="smb"),
        _t("r:3389/tcp", ip="127.0.0.1", port=rdp, method="rdp"),
        _t("j:445/tcp", ip="127.0.0.1", port=junk, method="smb"),
    ]
    script = tmp_path / "probe.py"
    script.write_text(render_probe_script(targets))
    res = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=90,
    )
    assert res.returncode == 0, res.stderr
    out = parse_probe_output(res.stdout, targets)
    assert out["k:88/tcp"].status == PASS      # KRB-ERROR is protocol-valid
    assert out["l:389/tcp"].status == PASS
    assert out["s:445/tcp"].status == PASS
    assert out["r:3389/tcp"].status == PASS
    assert out["j:445/tcp"].status == FAIL     # not SMB2 -- never a pass


def test_rendered_probe_reports_a_closed_port_as_fail(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    targets = [_t("h:80/tcp", ip="127.0.0.1", port=port, method="http")]
    script = tmp_path / "probe.py"
    script.write_text(render_probe_script(targets))
    res = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60,
    )
    out = parse_probe_output(res.stdout, targets)
    assert out["h:80/tcp"].status == FAIL
    assert "refused" in out["h:80/tcp"].detail.lower()


# ---------------------------------------------------------------------------
# Peer selection (the `_select_verify_injector` shape, LES-121/LES-146)
# ---------------------------------------------------------------------------
class _Net:
    def __init__(self, net_id, ip=None, external=False):
        self.net_id, self.ip, self.external = net_id, ip, external


class _Host:
    def __init__(self, host_id, nets, vmid=1, status="completed", kind="host"):
        self.host_id, self.nets, self.vmid = host_id, nets, vmid
        self.vm_status, self.kind = status, kind
        self.role = None


def test_peer_selection_prefers_a_leaf_over_the_router():
    hosts = [
        _Host("router", [_Net("a", "10.0.0.1"), _Net("b", "10.0.1.1"),
                         _Net("__wan__", "192.0.2.1", external=True)],
              vmid=1, kind="router"),
        _Host("leaf", [_Net("a", "10.0.0.5")], vmid=2),
        _Host("target", [_Net("a", "10.0.0.9")], vmid=3),
    ]
    peer, router_only = select_probe_peer(hosts, "a", exclude={"target"})
    assert peer.host_id == "leaf" and router_only is False


def test_peer_selection_says_so_when_the_router_is_the_only_option():
    hosts = [
        _Host("router", [_Net("a", "10.0.0.1"), _Net("b", "10.0.1.1")],
              vmid=1, kind="router"),
        _Host("target", [_Net("a", "10.0.0.9")], vmid=3),
    ]
    peer, router_only = select_probe_peer(hosts, "a", exclude={"target"})
    assert peer.host_id == "router" and router_only is True


def test_peer_selection_skips_hosts_that_never_finished_cloning():
    hosts = [
        _Host("half-built", [_Net("a", "10.0.0.5")], vmid=None, status="pending"),
        _Host("target", [_Net("a", "10.0.0.9")], vmid=3),
    ]
    peer, _ = select_probe_peer(hosts, "a", exclude={"target"})
    assert peer is None


class _Fact:
    def __init__(self, node_id, segment, port, transport, method, candidates):
        self.observed_node_id, self.segment = node_id, segment
        self.port, self.transport, self.method = port, transport, method
        self.candidates = candidates


def test_compose_notes_a_segment_with_no_peer_instead_of_probing_blind():
    hosts = [_Host("lonely", [_Net("a", "10.0.0.9")], vmid=3)]
    facts = [_Fact("address:1.1.1.1", "a", 80, "tcp", "http", ("lonely",))]
    targets, notes = compose_probe_targets(facts, hosts)
    assert targets == []
    assert any("no deployed LINUX host other than itself" in n for n in notes)


def test_peer_selection_excludes_a_windows_guest(tmp_path):
    """M1: the probe is a stdlib python3 script and the Windows images carry
    no python3. Measured on the standing twin before this existed: 28 of 45
    targets were peered to Windows guests, and the first refusal aborted the
    ENTIRE scorecard."""
    hosts = [
        _Host("win01", [_Net("a", "10.0.0.5")], vmid=2),
        _Host("lin01", [_Net("a", "10.0.0.6")], vmid=3),
        _Host("target", [_Net("a", "10.0.0.9")], vmid=4),
    ]
    os_family = {"win01": "windows", "lin01": "linux", "target": "linux"}
    peer, _ = select_probe_peer(
        hosts, "a", exclude={"target"},
        is_linux=lambda h: os_family.get(h) == "linux",
    )
    assert peer.host_id == "lin01"


def test_peer_selection_treats_an_unknown_os_as_unusable():
    """"Probably Linux" is a guess, and this is the exact place guessing
    cost the artifact -- an unrecorded OS family is excluded."""
    hosts = [
        _Host("mystery", [_Net("a", "10.0.0.5")], vmid=2),
        _Host("target", [_Net("a", "10.0.0.9")], vmid=4),
    ]
    peer, _ = select_probe_peer(
        hosts, "a", exclude={"target"}, is_linux=lambda h: False,
    )
    assert peer is None


def test_compose_on_a_mixed_os_segment_peers_only_to_linux():
    hosts = [
        _Host("win01", [_Net("a", "10.0.0.5")], vmid=2),
        _Host("win02", [_Net("a", "10.0.0.7")], vmid=5),
        _Host("lin01", [_Net("a", "10.0.0.6")], vmid=3),
        _Host("dc01", [_Net("a", "10.0.0.9")], vmid=4),
    ]
    os_family = {"win01": "windows", "win02": "windows",
                 "lin01": "linux", "dc01": "windows"}
    facts = [
        _Fact("address:1.1.1.1", "a", 389, "tcp", "ldap", ("dc01",)),
        _Fact("address:1.1.1.2", "a", 445, "tcp", "smb", ("dc01",)),
    ]
    targets, notes = compose_probe_targets(
        facts, hosts, is_linux=lambda h: os_family.get(h) == "linux",
    )
    assert {t.key for t in targets} == {"dc01:389/tcp", "dc01:445/tcp"}
    # A Windows TARGET is fine -- it is being probed, not probing.
    assert {t.peer_host_id for t in targets} == {"lin01"}
    assert notes == []


def test_compose_notes_a_missing_address():
    hosts = [
        _Host("t", [_Net("a", None)], vmid=3),
        _Host("peer", [_Net("a", "10.0.0.5")], vmid=4),
    ]
    facts = [_Fact("address:1.1.1.1", "a", 80, "tcp", "http", ("t",))]
    targets, notes = compose_probe_targets(facts, hosts)
    assert targets == []
    assert any("no address recorded" in n for n in notes)


# ---------------------------------------------------------------------------
# The runner (fake guest agent -- no cluster, no adapter.conf)
# ---------------------------------------------------------------------------
def test_runner_delivers_executes_and_removes_the_script():
    """Read-only posture (§9.6): the temporary script is removed after the
    run, and nothing is installed."""
    ga = FakeGuestAgent(results={"h:80/tcp": ("pass", "HTTP/1.0 200 OK")})
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    report = runner.run([_t()])
    assert report.ran and report.results["h:80/tcp"].status == PASS
    assert len(ga.written) == 1 and ga.written[0][1].endswith(".py")
    assert ga.removed == [42]
    assert not any("apt" in " ".join(a) for _v, a in ga.execs)


def test_missing_python3_costs_that_peer_s_facts_not_the_whole_artifact():
    """M1: still LOUD (LES-089 -- the refusal names the host, the vmid and
    the remedy, and its facts become row 5's `unmeasured`), but no longer
    fatal. A peer that cannot run the probe must not cost 137 other hosts
    their rows."""
    ga = FakeGuestAgent(python3=False)
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    report = runner.run([_t(peer="p1", peer_vmid=11), _t("b:80/tcp", peer="p2", peer_vmid=12)])
    assert report.results == {}
    assert len(report.errors) == 2
    assert all("python3 not found" in e for e in report.errors)
    assert all("ProbeRunError" in e for e in report.errors)
    assert ga.written == []


def test_one_bad_peer_does_not_cost_a_good_peer_its_results():
    class _Selective(FakeGuestAgent):
        def exec(self, vmid, argv, timeout_s=None):
            if "command -v python3" in " ".join(argv) and int(vmid) == 11:
                return _Res(1, "", "")
            return super().exec(vmid, argv, timeout_s=timeout_s)

    from tests.fidelity_support import _Res  # noqa: F401  (used above)

    ga = _Selective(results={"b:80/tcp": ("pass", "HTTP/1.0 200 OK")})
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    report = runner.run([
        _t("a:80/tcp", peer="p1", peer_vmid=11),
        _t("b:80/tcp", peer="p2", peer_vmid=12),
    ])
    assert report.results["b:80/tcp"].status == PASS
    assert len(report.errors) == 1 and "python3 not found" in report.errors[0]


def test_runner_batches_targets_per_peer():
    ga = FakeGuestAgent(results={
        "a:80/tcp": ("pass", ""), "b:80/tcp": ("pass", ""),
    })
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    runner.run([
        _t("a:80/tcp", peer="p1", peer_vmid=11),
        _t("b:80/tcp", peer="p1", peer_vmid=11),
    ])
    assert len(ga.written) == 1     # one script, one exec, two targets


def test_runner_records_a_target_with_no_peer_as_an_error():
    ga = FakeGuestAgent()
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    report = runner.run([_t(peer=None, peer_vmid=None)])
    assert report.errors and "no peer resolved" in report.errors[0]


def test_runner_listen_probe_reads_only_local_ports():
    ga = FakeGuestAgent(listens="tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n")
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    from axrange.fidelity.probe import ProbeReport

    report = ProbeReport(ran=True)
    runner.run_listens([_Host("h", [_Net("a", "10.0.0.2")], vmid=7)], report)
    assert report.listens_ran and report.listens["h"] == ["22/tcp"]


def test_listen_probe_is_scoped_to_row_5_targets_and_skips_windows():
    """S6: row 4's reference side is `nodes[].listen_ports`, so a host that
    carries no observed listen has nothing to compare against; and `ss` is
    iproute2, which the Windows images do not have."""
    ga = FakeGuestAgent(listens="tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n")
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    from axrange.fidelity.probe import ProbeReport

    hosts = [
        _Host("in-scope", [_Net("a", "10.0.0.2")], vmid=7),
        _Host("win-target", [_Net("a", "10.0.0.3")], vmid=8),
        _Host("bystander", [_Net("a", "10.0.0.4")], vmid=9),
    ]
    report = ProbeReport(ran=True, targets=[
        _t("in-scope:80/tcp"), _t("win-target:445/tcp"),
    ])
    runner.run_listens(
        hosts, report, is_linux=lambda h: h != "win-target",
    )
    assert set(report.listens) == {"in-scope"}
    assert any("not a Linux guest" in n for n in report.notes)

def test_runner_records_an_unreachable_guest_instead_of_sinking_the_run():
    """One flaky QGA session becomes row 5's `unmeasured`, not a crash and
    not a manufactured failure."""
    class _Boom(FakeGuestAgent):
        def write_file(self, vmid, path, content, mode=None):
            raise RuntimeError("qga session died")

    ga = _Boom()
    runner = QgaProbeRunner(node="px1", ga_factory=lambda **_: ga)
    report = runner.run([_t()])
    assert report.results == {}
    assert any("qga session died" in e for e in report.errors)
    # Cleanup still ran on the failure path (read-only posture).
    assert ga.removed == [42]


# ---------------------------------------------------------------------------
# What the probe puts ON THE WIRE (the REQUEST half)
#
# `test_rendered_probe_speaks_the_ad_protocols_on_the_wire` above proves the
# probe classifies a canned RESPONSE correctly; it never looks at the bytes
# the probe SENT. That is the half that can fail silently: a malformed
# request is dropped by a real server, the probe reads "nothing answered",
# and row 5 records a `fail` that is a measurement artefact rather than a
# fidelity gap -- the exact false negative `probe.py`'s own SMB comment says
# must not be manufactured. These tests capture the request and validate it
# structurally, RECOMPUTING every length from the content rather than
# trusting the literal in the template.
# ---------------------------------------------------------------------------
def _recording_tcp_server(reply: bytes) -> tuple[int, dict]:
    """A one-shot server that records the request before replying."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    box: dict = {}

    def run():
        try:
            conn, _ = s.accept()
            conn.settimeout(10)
            box["request"] = conn.recv(8192)
            conn.sendall(reply)
            conn.close()
        finally:
            s.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    box["thread"] = t
    return s.getsockname()[1], box


def _ber_walk(buf: bytes, off: int = 0, end=None, depth: int = 0, out=None):
    """Walk a DER/BER buffer, RECOMPUTING each length from the content.

    Raises `AssertionError` on any length that does not agree with what it
    contains, so "it parses" is a real structural claim and not a restatement
    of the literal the template happens to hold.
    """
    if out is None:
        out = []
    if end is None:
        end = len(buf)
    while off < end:
        tag = buf[off]
        off += 1
        # Bounds-check before reading the length header, so a truncated or
        # corrupt encoding fails as a legible assertion rather than an
        # IndexError from the walker itself.
        assert off < end, f"tag 0x{tag:02x} has no length byte"
        first = buf[off]
        off += 1
        if first & 0x80:
            n = first & 0x7F
            assert n > 0, "indefinite length is not valid DER"
            assert off + n <= end, f"tag 0x{tag:02x} has a truncated long-form length"
            length = int.from_bytes(buf[off:off + n], "big")
            off += n
        else:
            length = first
        assert off + length <= end, (
            f"tag 0x{tag:02x} declares length {length} but only "
            f"{end - off} bytes remain in its container"
        )
        out.append((depth, tag, length))
        if tag & 0x20:                      # constructed -- recurse
            _ber_walk(buf, off, off + length, depth + 1, out)
        off += length
    assert off == end, f"{end - off} trailing byte(s) inside a container"
    return out


def _run_probe(tmp_path, targets):
    script = tmp_path / "probe.py"
    script.write_text(render_probe_script(targets))
    res = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=90,
    )
    assert res.returncode == 0, res.stderr
    return res


def test_rendered_probe_sends_a_64_byte_smb2_negotiate_header(tmp_path):
    """`probe.py`: "A header that is not 64 bytes is dropped by a real
    server, which would read as 'nothing answers'." Nothing pinned that, so
    the header size is recomputed here from the body's own offset rather
    than read out of the StructureSize field alone."""
    port, box = _recording_tcp_server(struct.pack("!I", 68) + b"\xfeSMB" + 64 * b"\0")
    _run_probe(tmp_path, [_t("s:445/tcp", ip="127.0.0.1", port=port, method="smb")])
    box["thread"].join(10)
    req = box["request"]

    # NetBIOS session length prefix must describe the rest exactly.
    assert struct.unpack("!I", req[:4])[0] == len(req) - 4
    msg = req[4:]
    assert msg[:4] == b"\xfeSMB", "SMB2 ProtocolId"
    # The header's self-declared size...
    assert struct.unpack("<H", msg[4:6])[0] == 64
    # ...and, independently, the NEGOTIATE body really does begin at 64:
    # StructureSize 36 is the fixed size of an SMB2 NEGOTIATE request.
    assert struct.unpack("<H", msg[64:66])[0] == 36
    assert struct.unpack("<H", msg[12:14])[0] == 0, "Command 0 == NEGOTIATE"
    # DialectCount 2, and both dialects present after the fixed body.
    assert struct.unpack("<H", msg[66:68])[0] == 2
    assert struct.unpack("<HH", msg[64 + 36:64 + 40]) == (0x0202, 0x0210)


def test_rendered_probe_sends_a_parseable_ldap_rootdse_search(tmp_path):
    port, box = _recording_tcp_server(bytes.fromhex("300c02010165070a010004000400"))
    _run_probe(tmp_path, [_t("l:389/tcp", ip="127.0.0.1", port=port, method="ldap")])
    box["thread"].join(10)
    req = box["request"]

    nodes = _ber_walk(req)          # every length recomputed; consumes exactly
    depth0 = [n for n in nodes if n[0] == 0]
    assert len(depth0) == 1 and depth0[0][1] == 0x30, "one outer LDAPMessage SEQUENCE"
    top = [n for n in nodes if n[0] == 1]
    assert top[0][1] == 0x02, "messageID INTEGER first"
    assert top[1][1] == 0x63, "protocolOp [APPLICATION 3] SearchRequest"
    # baseObject is the empty string -- that is what makes it a rootDSE read.
    base = [n for n in nodes if n[0] == 2][0]
    assert base == (2, 0x04, 0), "baseObject OCTET STRING of length 0"


def test_rendered_probe_sends_a_parseable_kerberos_as_req(tmp_path):
    port, box = _recording_tcp_server(struct.pack("!I", 5) + b"\x7e\x03\x02\x01\x05")
    _run_probe(tmp_path, [_t("k:88/tcp", ip="127.0.0.1", port=port, method="krb")])
    box["thread"].join(10)
    req = box["request"]

    # Kerberos over TCP is a 4-byte big-endian length prefix, computed here.
    assert struct.unpack("!I", req[:4])[0] == len(req) - 4
    nodes = _ber_walk(req[4:])
    depth0 = [n for n in nodes if n[0] == 0]
    assert len(depth0) == 1 and depth0[0][1] == 0x6A, "[APPLICATION 10] AS-REQ"
    assert [n[1] for n in nodes if n[0] == 1] == [0x30], "one KDC-REQ SEQUENCE"
    # pvno 5, msg-type 10 -- the two fields a KDC reads before anything else.
    ctx = [n for n in nodes if n[0] == 2]
    assert [n[1] for n in ctx[:3]] == [0xA1, 0xA2, 0xA4]


def test_rendered_probe_imports_only_stdlib():
    """The module docstring makes this load-bearing: stdlib-only is what
    lets the probe run against a STANDING twin with no package install."""
    import ast

    script = render_probe_script([
        _t("a:80/tcp", ip="10.0.0.5", port=80, method="http"),
        _t("b:88/udp", ip="10.0.0.6", port=88, transport="udp", method="krb"),
    ])
    modules: set[str] = set()
    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative import can resolve in a guest"
            if node.module:
                modules.add(node.module.split(".")[0])
    assert modules == {"json", "socket", "ssl", "struct", "sys"}
    assert modules <= set(sys.stdlib_module_names)


def _all_methods() -> list[str]:
    """Every method the RENDERED template dispatches on, read out of the
    template itself rather than hardcoded.

    A hardcoded list is a drift trap: when the fix round added cldap / nbns /
    dhcp / dcerpc, a literal list would have kept passing while silently
    covering none of them.
    """
    import re

    body = re.search(r"METHODS = \{(.*?)\n\}", probe_mod._SCRIPT_TEMPLATE, re.S)
    assert body, "the rendered template no longer defines a METHODS dispatch table"
    return sorted(re.findall(r'"([a-z-]+)":', body.group(1)))


def test_the_method_table_covers_every_port_mapping():
    """Every method a port can map to must exist in the rendered dispatch
    table, and vice versa -- otherwise a mapped port renders a script that
    reports `no probe method`."""
    dispatched = set(_all_methods())
    mapped = set(probe_mod._TCP_METHODS.values()) | set(probe_mod._UDP_METHODS.values())
    assert mapped <= dispatched, f"mapped but never dispatched: {mapped - dispatched}"
    assert dispatched - mapped == {"connect", "udp-connect"}, (
        "the only undispatched-by-port methods are the two fallbacks"
    )
    # And every protocol-aware name really is implemented in the template.
    for method in probe_mod.PROTOCOL_AWARE_METHODS:
        fn = "probe_" + method.replace("-", "_")
        assert f"def {fn}(" in probe_mod._SCRIPT_TEMPLATE, f"{fn} is not rendered"


@pytest.mark.parametrize("method", _all_methods())
def test_render_is_byte_deterministic_for_every_method(method):
    transport = "udp" if method in probe_mod._UDP_METHODS.values() or method == "udp-connect" else "tcp"
    targets = [
        _t(f"a:1/{transport}", ip="10.0.0.5", port=1, transport=transport, method=method),
        _t(f"b:2/{transport}", ip="10.0.0.6", port=2, transport=transport, method=method),
    ]
    first = render_probe_script(targets)
    assert first == render_probe_script(list(reversed(targets)))
    assert first == render_probe_script(targets)
    compile(first, "<probe>", "exec")


def test_render_refuses_a_non_literal_address_at_any_position():
    """`check_containment` must scan the whole target list, not just its
    head -- a name in position 4 of a 41-fact probe is the realistic shape."""
    ok = _t("ok:80/tcp", ip="10.0.0.5")
    for position in range(4):
        targets = [ok, ok, ok, ok]
        targets[position] = _t("bad:80/tcp", ip="dc01.corp.lab")
        with pytest.raises(ProbeRenderError, match="not an IP literal"):
            render_probe_script(targets)
    for bad in ("localhost", "10.0.0.5 ", "10.0.0.999", "10.0.0.5/24", ""):
        with pytest.raises(ProbeRenderError, match="not an IP literal"):
            check_containment([_t("bad:80/tcp", ip=bad)])


# ---------------------------------------------------------------------------
# The four methods the fix round added (cldap / nbns / dhcp / dcerpc)
#
# They arrived with a method-TABLE assertion only. Nothing ran them, and
# nothing looked at what they put on the wire -- and they are precisely the
# methods that make `golden` reachable at all on the standing twin (they
# cover 135/tcp, 137/udp, 389/udp and 67/udp, which were `connect`/
# `udp-connect` before and could therefore never pass). A malformed request
# here is dropped by a real server and reads as "the service does not
# answer": row 5 would record a `fail` that is a measurement artefact.
# ---------------------------------------------------------------------------
def _recording_udp_server(reply_for):
    """One-shot UDP responder that records the datagram it received."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(20)
    box: dict = {}

    def run():
        try:
            data, addr = s.recvfrom(4096)
            box["request"] = data
            s.sendto(reply_for(data), addr)
        except Exception as e:  # noqa: BLE001 -- surfaced through box
            box["error"] = e
        finally:
            s.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    box["thread"] = t
    return s.getsockname()[1], box


def _nbns_reply(query: bytes) -> bytes:
    # Echo the transaction id, set the response bit, one answer RR.
    return query[0:2] + b"\x84\x00" + b"\x00\x00\x00\x01\x00\x00\x00\x00" + b"\x00" * 40


def _dhcp_reply(query: bytes) -> bytes:
    reply = bytearray(b"\x02" + query[1:4] + query[4:8] + b"\x00" * 228)
    reply[236:240] = b"\x63\x82\x53\x63"          # magic cookie
    return bytes(reply) + b"\x35\x01\x05\xff"     # DHCPACK


def test_the_four_new_methods_answer_against_canned_responders(tmp_path):
    cldap_port, cldap = _recording_udp_server(
        lambda _q: bytes.fromhex("300c02010165070a010004000400")
    )
    nbns_port, nbns = _recording_udp_server(_nbns_reply)
    dhcp_port, dhcp = _recording_udp_server(_dhcp_reply)
    # bind_ack (PDU type 0x0c) and a non-DCE-RPC negative control.
    rpc_port, rpc = _recording_tcp_server(b"\x05\x00\x0c\x03" + b"\x10\x00\x00\x00" + b"\x00" * 40)
    junk_port, _junk = _recording_tcp_server(b"not rpc at all")

    targets = [
        _t("c:389/udp", ip="127.0.0.1", port=cldap_port, transport="udp", method="cldap"),
        _t("n:137/udp", ip="127.0.0.1", port=nbns_port, transport="udp", method="nbns"),
        _t("d:67/udp", ip="127.0.0.1", port=dhcp_port, transport="udp", method="dhcp"),
        _t("r:135/tcp", ip="127.0.0.1", port=rpc_port, method="dcerpc"),
        _t("j:135/tcp", ip="127.0.0.1", port=junk_port, method="dcerpc"),
    ]
    res = _run_probe(tmp_path, targets)
    out = parse_probe_output(res.stdout, targets)
    assert out["c:389/udp"].status == PASS
    assert out["n:137/udp"].status == PASS
    assert out["d:67/udp"].status == PASS
    assert out["r:135/tcp"].status == PASS
    assert out["j:135/tcp"].status == FAIL       # not DCE-RPC -- never a pass

    for box in (cldap, nbns, dhcp, rpc):
        box["thread"].join(10)

    # -- and what each one actually SENT ---------------------------------
    # CLDAP: the same rootDSE SearchRequest as LDAP, in one datagram.
    q = cldap["request"]
    nodes = _ber_walk(q)                          # lengths recomputed
    assert nodes[0][1] == 0x30 and nodes[0][2] == len(q) - 2
    assert any(tag == 0x63 for _d, tag, _l in nodes), "SearchRequest protocolOp"

    # NBNS: an NBSTAT (0x0021) node-status query for the wildcard name.
    q = nbns["request"]
    assert struct.unpack("!H", q[4:6])[0] == 1, "qdcount 1"
    assert struct.unpack("!H", q[-4:-2])[0] == 0x0021, "qtype NBSTAT"
    assert struct.unpack("!H", q[-2:])[0] == 0x0001, "qclass IN"
    assert q[12] == 32, "second-level-encoded NetBIOS name is 32 bytes"

    # DHCP: an INFORM, chosen so the probe cannot consume a lease.
    q = dhcp["request"]
    assert q[0] == 1, "BOOTREQUEST"
    assert q[236:240] == b"\x63\x82\x53\x63", "magic cookie at 236"
    assert q[240:243] == b"\x35\x01\x08", "option 53 len 1 value 8 == DHCPINFORM"
    assert q.endswith(b"\xff"), "option list terminated"

    # DCE-RPC: a bind whose frag_length describes the whole PDU.
    q = rpc["request"]
    assert (q[0], q[1]) == (5, 0), "DCE-RPC 5.0"
    assert q[2] == 0x0b, "PDU type 0x0b == bind"
    assert struct.unpack("<H", q[8:10])[0] == len(q), "frag_length == actual length"


def test_the_four_new_methods_refuse_a_hostname(tmp_path):
    for method, transport, port in (
        ("cldap", "udp", 389), ("nbns", "udp", 137),
        ("dhcp", "udp", 67), ("dcerpc", "tcp", 135),
    ):
        ok = _t(f"ok:{port}/{transport}", ip="10.0.0.5", port=port,
                transport=transport, method=method)
        bad = _t(f"bad:{port}/{transport}", ip="dc01.corp.lab", port=port,
                 transport=transport, method=method)
        with pytest.raises(ProbeRenderError, match="not an IP literal"):
            render_probe_script([ok, bad])
