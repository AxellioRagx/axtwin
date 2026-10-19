"""`cli._write_serve_scripts` — where the two populations meet, and must not blur.

The observed hosts' declared set comes from the listen census and cannot be widened here; the
internet sink's is built here because nothing ever observed a synthetic node. Everything in this
file is about keeping those two rules apart, because merging them is exactly how the echo
responder ended up binding what personas *dialled* rather than what hosts *answered*
(`axrange/docs/WORK_ORDER_protocol_faithful_services.md` §2, §7.4).
"""
from aximprint import cli
from aximprint.synth.services import COMPILER_SINK_PORTS


def _out(nodes, segments=(("s1", "10.0.0.0/24"),)):
    return {"twin_config": {"twin_config": {
                "nodes": list(nodes),
                "segments": [{"name": n, "cidr": c} for n, c in segments]}},
            "provenance": {"fields": {}}, "caveats": []}


def _persona(peer_role, ports, transport="tcp"):
    return {"persona": {"emits": [
        {"peer_role": peer_role, "transport": transport, "ports": list(ports)}]}}


_EMPTY_PIT = {"ir_entity": [], "ir_identity": []}


def _nodes(out):
    return {n["name"]: n for n in out["twin_config"]["twin_config"]["nodes"]}


def test_an_observed_host_gets_a_script_and_the_pkg_actions_its_recipes_need(tmp_path):
    out = _out([{"name": "web-a", "role": "web-tier", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 443, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    node = _nodes(out)["web-a"]
    assert node["install"] == [{"pkg": "nginx"}, {"pkg": "openssl"},
                               {"script": "scripts/serve-web-a.sh"}]
    assert (tmp_path / "scripts" / "serve-web-a.sh").is_file()


def test_a_persona_dial_can_never_widen_an_OBSERVED_hosts_declared_set(tmp_path):
    """The defect this whole build removes, asserted directly: personas dial 443 at a host
    observed answering only on 80, and the host still serves only 80."""
    out = _out([{"name": "web-a", "role": "web-tier", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 80, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path,
                             {"p": _persona("web-tier", [80, 443])})
    assert _nodes(out)["web-a"]["listen"] == [{"port": 80, "proto": "tcp"}]
    script = (tmp_path / "scripts" / "serve-web-a.sh").read_text()
    assert "listen 80 default_server;" in script
    assert "443 ssl" not in script


def test_an_unanswered_dial_is_REPORTED_rather_than_silently_served(tmp_path):
    """§2's cross-check. A fabricated listener in the artifact being measured is worse than a
    known gap in it."""
    out = _out([{"name": "web-a", "role": "web-tier", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 80, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path,
                             {"p": _persona("web-tier", [80, 443])})
    hits = [c for c in out["caveats"] if "personas dial" in c and "web-tier" in c]
    assert len(hits) == 1 and "443/tcp" in hits[0]


def test_a_fully_answered_dial_set_produces_no_cross_check_noise(tmp_path):
    out = _out([{"name": "web-a", "role": "web-tier", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 80, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {"p": _persona("web-tier", [80])})
    assert not [c for c in out["caveats"] if "personas dial" in c]


def test_the_sink_takes_its_set_from_the_dial_set_plus_its_real_services(tmp_path):
    """§7.4. The sink is the ONE population whose declared set is a compiler choice, because
    nothing observed it — and the choice is derived from what it stands in for.

    This fixture's PIT carries no external entities at all, so `sink_real_services` takes its
    documented FALLBACK (`COMPILER_SINK_PORTS`); the derivation itself is exercised in
    `tests/test_synth_services.py`. What is under test here is the WIRING — that the sink's set
    is the union, that it is published as `compiler`, and that a port with no recipe still falls
    to the echo."""
    out = _out([{"name": "internet-sink-1", "role": "internet-sink-1",
                 "image": "debian-12-cloud@v1", "net": "s1"}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path,
                             {"p": _persona("internet-sink-1", [8883])})
    listen = {(e["port"], e["proto"]) for e in _nodes(out)["internet-sink-1"]["listen"]}
    assert listen == {(8883, "tcp")} | set(COMPILER_SINK_PORTS)
    row = out["provenance"]["fields"]["twin_config.nodes[0].listen"]
    assert row["evidence_class"] == "compiler"
    script = (tmp_path / "scripts" / "serve-internet-sink-1.sh").read_text()
    # The two real services, and the echo for the one this project has no server for.
    assert "--- nginx" in script and "--- chrony" in script
    assert "responder.py 8883/tcp" in script


def test_the_sink_never_overrides_a_set_something_already_declared(tmp_path):
    out = _out([{"name": "internet-sink-1", "role": "internet-sink-1",
                 "image": "debian-12-cloud@v1", "net": "s1",
                 "listen": [{"port": 80, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path,
                             {"p": _persona("internet-sink-1", [8883])})
    assert _nodes(out)["internet-sink-1"]["listen"] == [{"port": 80, "proto": "tcp"}]


def test_the_echo_ports_are_published_as_a_compiler_choice(tmp_path):
    """D8(a): the echo is kept, and it is REPORTED as a compiler choice rather than passing for
    an observation."""
    out = _out([{"name": "h", "role": "log-shipper", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 57086, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    row = out["provenance"]["fields"]["twin_config.nodes[0].install.echo"]
    assert row["evidence_class"] == "compiler" and row["ports"] == ["57086/tcp"]


def test_a_windows_host_is_named_as_unserved_rather_than_silently_skipped(tmp_path):
    out = _out([{"name": "ws", "role": "corp-workstation", "image": "win11-finance@v4",
                 "net": "s1", "listen": [{"port": 3389, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    assert not (tmp_path / "scripts").exists()
    assert "install" not in _nodes(out)["ws"]
    hit = next(c for c in out["caveats"] if "'ws'" in c)
    assert "UNSERVED" in hit and "3389/tcp" in hit


def test_a_windows_host_that_PROMOTES_a_domain_is_not_reported_as_unserved(tmp_path):
    """A DC answers on twelve ports as a consequence of `role: adds`. Reporting it as unserved
    would be a false finding in the register the scorecard reads."""
    out = _out([{"name": "axdc01", "role": "domain-controller",
                 "image": "windows-server-2022@v1", "net": "s1",
                 "install": [{"role": "adds"}],
                 "listen": [{"port": 88, "proto": "tcp"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    hit = next(c for c in out["caveats"] if "'axdc01'" in c)
    assert "UNSERVED" not in hit and "AD promotion" in hit


def test_a_re_cast_replaces_the_install_actions_rather_than_stacking_them(tmp_path):
    """An installed folder carrying both spellings must never install both. The old
    `responder-*.sh` action is filtered alongside the new one."""
    out = _out([{"name": "web-a", "role": "web-tier", "image": "debian-12-cloud@v1",
                 "net": "s1", "listen": [{"port": 443, "proto": "tcp"}],
                 "install": [{"pkg": "nginx"},
                             {"script": "scripts/responder-web-tier.sh"},
                             {"script": "scripts/serve-web-a.sh"}]}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    install = _nodes(out)["web-a"]["install"]
    assert [a for a in install if a.get("script")] == [{"script": "scripts/serve-web-a.sh"}]
    assert install.count({"pkg": "nginx"}) == 1, "an existing pkg action must not be duplicated"


def test_a_host_declaring_nothing_gets_no_script_at_all(tmp_path):
    out = _out([{"name": "quiet", "role": "generic-endpoint", "image": "debian-12-cloud@v1",
                 "net": "s1"}])
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    assert not (tmp_path / "scripts").exists()
    assert "install" not in _nodes(out)["quiet"]


def test_a_dhcp_scope_that_cannot_be_derived_is_reported_and_falls_to_the_echo(tmp_path):
    out = _out([{"name": "h", "role": "dns-resolver", "image": "debian-12-cloud@v1",
                 "net": "tiny", "listen": [{"port": 67, "proto": "udp"}]}],
               segments=(("tiny", "10.0.0.0/30"),))
    cli._write_serve_scripts(out, _EMPTY_PIT, tmp_path, {})
    assert any("no DHCP server" in c for c in out["caveats"])
    assert "responder.py 67/udp" in (tmp_path / "scripts" / "serve-h.sh").read_text()
