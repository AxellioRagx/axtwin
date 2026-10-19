"""`synth/services.py` — real protocol servers, driven by the observed listen census.

The properties under test are not "it emits a shell script" but the three the work order's ruling
turns on (`axrange/docs/WORK_ORDER_protocol_faithful_services.md`):

  * a host serves what it was seen ANSWERING on, never what a persona dials at it (§2);
  * a real daemon and the echo can never contend for one port, and that is refused at RENDER
    time rather than skipped at runtime (D8, §9.1 test 4);
  * the render is a pure function — byte-identical for the same input (CONVENTIONS §3).
"""
import json

import pytest

from aximprint.synth.services import (
    COMPILER_SINK_PORTS,
    LAB_REALM,
    SERVER_RECIPES,
    ServiceRenderError,
    app_token,
    dhcp_pool,
    edge_tokens,
    external_listen_census,
    install_actions,
    listen_specs,
    parse_port,
    plan_node_services,
    render_serve_script,
    services_for,
    sink_real_services,
    zone_names_from_pit,
    zone_serial,
)

# ---------------------------------------------------------------------------------------------
# the protocol join
# ---------------------------------------------------------------------------------------------

def test_app_token_answers_in_the_SENSOR_vocabulary_not_the_persona_one():
    """`services[].app` must join against `edges[].proto`, so it is spelled the way the sensor
    spells it. `_PORT_PROTO` says `https`/`kerberos`/`ldap`; Zeek says `ssl`/`krb`/`ldap_tcp`,
    and two spellings of one fact join to nothing (`LES-143`)."""
    assert app_token(443, "tcp") == "ssl"
    assert app_token(80, "tcp") == "http"
    assert app_token(53, "udp") == "dns"


def test_transport_split_analyzers_resolve_by_transport():
    """Zeek splits LDAP by transport and Kerberos by TCP-vs-not; a single token for both would
    make one of the two never match an observed edge."""
    assert app_token(389, "tcp") == "ldap_tcp"
    assert app_token(389, "udp") == "ldap_udp"
    assert app_token(88, "tcp") == "krb_tcp"
    assert app_token(88, "udp") == "krb"


def test_a_port_no_table_names_yields_no_app_rather_than_a_guess():
    """The work order's §4 last row: ephemeral sockets and Delivery Optimization are
    **observed but not reproduced**, and 9997/tcp has no free real server. Reporting them in
    that state is the point; inventing an app for them is the defect (`LES-006`)."""
    assert app_token(49669, "tcp") is None
    assert app_token(7680, "tcp") is None
    # 9997 maps to the sensor's *unidentified* label, which names no service.
    assert app_token(9997, "tcp") is None


def test_composite_service_strings_are_token_matched():
    """Zeek joins every analyzer that fired on one connection, so `gssapi,smb,krb` carries
    THREE facts and a string comparison would see none of them."""
    assert edge_tokens("gssapi,smb,krb") == {"gssapi", "smb", "krb"}
    assert edge_tokens("quic,ssl") == {"quic", "ssl"}
    assert edge_tokens(None) == set()


def test_services_report_the_listen_and_the_app_under_SEPARATE_evidence_classes():
    """The listen is observed; whether the wire named its protocol is a different claim. Folding
    them into one class lets an inference ride on an observation's evidence."""
    rows = services_for(["53/udp", "3389/tcp"], ["dns", "tcp"])
    by_port = {r["port"]: r for r in rows}
    assert by_port[53]["evidence_class"] == "observed"
    assert (by_port[53]["app"], by_port[53]["app_evidence_class"]) == ("dns", "observed")
    # 3389 was accepted (so the listen is observed) but no inbound edge was labelled `rdp` —
    # only bare `tcp`. The app is therefore the port convention's, and says so.
    assert by_port[3389]["evidence_class"] == "observed"
    assert (by_port[3389]["app"], by_port[3389]["app_evidence_class"]) == ("rdp", "imputed")


def test_an_unnamed_port_carries_neither_app_key_rather_than_a_null_one():
    row = services_for(["49669/tcp"], [])[0]
    assert row == {"port": 49669, "proto": "tcp", "evidence_class": "observed"}


def test_services_are_ordered_by_port_so_the_export_is_byte_stable():
    rows = services_for(["445/tcp", "53/udp", "88/tcp"], [])
    assert [r["port"] for r in rows] == [53, 88, 445]


def test_a_malformed_port_spec_is_dropped_rather_than_becoming_port_zero():
    assert parse_port("not-a-port") is None
    assert parse_port("53/sctp") is None
    assert parse_port("70000/tcp") is None
    assert services_for(["not-a-port", "53/udp"], []) == [
        {"port": 53, "proto": "udp", "evidence_class": "observed",
         "app": "dns", "app_evidence_class": "imputed"}]


# ---------------------------------------------------------------------------------------------
# the plan, and the render-time guard
# ---------------------------------------------------------------------------------------------

def test_the_declared_set_is_partitioned_between_real_servers_and_the_echo():
    plan = plan_node_services("n", [(53, "udp"), (80, "tcp"), (123, "udp"), (49669, "tcp")])
    assert {r.name for r, _p in plan.recipes} == {"bind9", "nginx", "chrony"}
    assert plan.echo == ((49669, "tcp"),)
    assert sorted(set(plan.covered) | set(plan.echo)) == sorted(plan.declared)


def test_the_echo_can_never_bind_a_port_a_real_daemon_owns():
    """D8's guard, and the reason it is a render-time assertion: the runtime symptom is
    invisible. A responder that loses one bind keeps running, `systemctl is-active` still says
    `active`, and the fingerprint blames the personas."""
    plan = plan_node_services("n", [(53, "udp"), (53, "tcp")])
    assert plan.echo == ()
    assert set(plan.covered) == {(53, "udp"), (53, "tcp")}


def test_two_recipes_claiming_one_port_is_a_loud_refusal(monkeypatch):
    """Mutation-proven: break the table and the guard must fire. A guard nothing can trip is not
    a guard (`LES-014`)."""
    import aximprint.synth.services as svc
    clash = svc.Recipe(name="clash", packages=(), unit="x", unit_alternatives=("x",),
                       ports=frozenset({(53, "udp")}))
    monkeypatch.setattr(svc, "SERVER_RECIPES", SERVER_RECIPES + (clash,))
    with pytest.raises(ServiceRenderError) as exc:
        svc.plan_node_services("n", [(53, "udp")])
    assert "53/udp" in str(exc.value) and "clash" in str(exc.value)


def test_every_recipe_port_set_is_disjoint_from_every_other():
    """The condition that makes the guard above unreachable in production. Asserted over the
    shipped table so a future recipe cannot introduce the contention silently."""
    seen: dict = {}
    for recipe in SERVER_RECIPES:
        for port in recipe.ports:
            assert port not in seen, f"{port} claimed by {seen.get(port)} and {recipe.name}"
            seen[port] = recipe.name


def test_dhcp_is_declined_rather_than_rendered_when_the_segment_has_no_room():
    """A DHCP server with no derivable scope is worse than none: the port falls to the echo and
    the caller reports why."""
    assert dhcp_pool("10.0.0.0/24") == ("10.0.0.0", "255.255.255.0", "10.0.0.243", "10.0.0.254")
    assert dhcp_pool("10.0.0.0/30") is None
    assert dhcp_pool("2001:db8::/64") is None
    assert dhcp_pool(None) is None
    plan = plan_node_services("n", [(67, "udp")], segment_cidr="10.0.0.0/30")
    assert plan.recipes == () and plan.echo == ((67, "udp"),)


def test_packages_go_in_pkg_actions_and_configuration_in_the_script():
    """Work order §7.2, and the reason is `--update`'s mutation rule: a `run-script`'s CONTENT
    can be re-applied to a standing twin, while adding a package to an existing node is
    refused."""
    plan = plan_node_services("n", [(53, "udp"), (443, "tcp")])
    actions = install_actions(plan, "scripts/serve-n.sh")
    assert actions[-1] == {"script": "scripts/serve-n.sh"}
    assert {a["pkg"] for a in actions[:-1]} == {"bind9", "bind9-utils", "nginx", "openssl"}


def test_sshd_is_asserted_and_installs_nothing():
    plan = plan_node_services("n", [(22, "tcp")])
    assert [r.name for r, _p in plan.recipes] == ["sshd"]
    assert plan.packages == ()
    assert "axr_assert_unit ssh sshd" in render_serve_script(plan)


# ---------------------------------------------------------------------------------------------
# the render
# ---------------------------------------------------------------------------------------------

_ALL = [(22, "tcp"), (53, "tcp"), (53, "udp"), (67, "udp"), (80, "tcp"), (123, "udp"),
        (443, "tcp"), (8006, "tcp"), (49669, "tcp")]


def _full_script():
    plan = plan_node_services("host-a", _ALL, segment_cidr="10.9.0.0/24")
    return render_serve_script(plan, zone_names=["axdc01", "axdc04"],
                               segment_cidr="10.9.0.0/24")


def test_every_recipe_renders_byte_identically_for_the_same_input():
    """Determinism is the house rule and the middle tier's whole job. A wall-clock SOA serial
    would make every re-cast of one PIT a different artifact."""
    assert _full_script() == _full_script()


def test_the_zone_serial_is_derived_from_content_not_from_a_clock():
    assert zone_serial("a", "b") == zone_serial("a", "b")
    assert zone_serial("a", "b") != zone_serial("a", "c")


def test_the_zone_name_order_does_not_change_the_bytes():
    a = plan_node_services("h", [(53, "udp")])
    assert (render_serve_script(a, zone_names=["b", "a"])
            == render_serve_script(a, zone_names=["a", "b"]))


def test_the_script_declares_a_shebang_and_fails_loud_on_every_daemon():
    script = _full_script()
    assert script.startswith("#!/bin/sh\n")
    assert "set -eu" in script
    # Never `nohup ... &` — the guest-agent session that launches a background process reaps it
    # (axRange LES-079/GAP-020). Real daemons take their own systemd unit; only the echo, which
    # has no package and no unit, uses `systemd-run`. Checked over CODE lines only: the preamble
    # names the hazard in a comment, and a substring test over the whole file would either fail
    # on the explanation or force the explanation out of the file.
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("nohup" in ln for ln in code)
    assert "systemctl is-active" in script
    assert "named-checkconf" in script and "nginx -t" in script and "dhcpd -t" in script


def test_the_zone_carries_the_lab_realm_and_the_observed_short_names():
    script = _full_script()
    assert f'zone "{LAB_REALM}"' in script
    assert "axdc01                   IN  A     @@IP@@" in script
    assert "axdc04                   IN  A     @@IP@@" in script


def test_the_echo_appears_only_for_ports_no_recipe_covers_and_says_it_is_a_compiler_choice():
    script = _full_script()
    assert "--- echo responder (evidence class: compiler) ---" in script
    assert "responder.py 49669/tcp" in script
    # The ports a real daemon owns must not reach the responder's argv.
    echo_line = next(ln for ln in script.splitlines() if "responder.py " in ln)
    for owned in ("53/udp", "53/tcp", "80/tcp", "443/tcp", "123/udp", "67/udp"):
        assert owned not in echo_line


def test_a_plan_with_no_echo_renders_no_responder_at_all():
    plan = plan_node_services("h", [(53, "udp"), (443, "tcp")])
    script = render_serve_script(plan)
    assert "responder.py" not in script and "axr-responder" not in script


def test_packages_are_installed_once_before_any_daemon_is_reconfigured():
    """Ordering hazard made structurally absent: the bind9 section repoints `/etc/resolv.conf`
    at the loopback, so a later section's own `apt-get` would have to resolve its mirror through
    a `named` that has not started."""
    script = _full_script()
    pkg_at = script.index("# --- packages")
    assert pkg_at < script.index("# --- bind9")
    assert script.count("axr_pkg bind9") == 1
    assert "axr_pkg bind9 bind9-utils nginx openssl chrony isc-dhcp-server" in script


def test_the_tls_certificate_is_always_self_signed_and_generated_in_guest():
    """Work order §11, absolute: a real certificate, chain or key is never reproduced."""
    script = _full_script()
    assert "openssl req -x509" in script and "-nodes" in script
    assert "-subj \"/CN=host-a.corp.axr.lab\"" in script


def test_an_observed_vhost_becomes_the_certificate_CN_and_the_default_server_name():
    plan = plan_node_services("h", [(443, "tcp"), (80, "tcp")])
    # SORTED, so which vhost is "first" is a property of the content rather than of the
    # caller's iteration order (`LES-009`) — `b.example` sorts before `test-api...`.
    script = render_serve_script(plan, vhosts=["test-api.example.internal", "b.example"])
    assert "/CN=b.example" in script
    assert "server_name test-api.example.internal;" in script


# ---------------------------------------------------------------------------------------------
# the inputs
# ---------------------------------------------------------------------------------------------

def test_listen_specs_union_a_groups_members_and_reject_junk():
    members = [{"listen_ports": '["53/udp", "123/udp"]'},
               {"listen_ports": '["53/udp", "nonsense"]'},
               {"listen_ports": None}]
    assert listen_specs(members) == ["53/udp", "123/udp"]


def test_a_malformed_listen_blob_degrades_to_empty_rather_than_raising():
    assert listen_specs([{"listen_ports": "not-json"}]) == []


def test_zone_names_take_only_names_bound_to_a_LOCAL_PLACED_entity():
    """*"Never invent corporate-looking names"* made mechanical: a twin resolver claiming
    authority for a name the capture bound to an external host is asserting something no
    observation supports."""
    pit = {
        "ir_entity": [
            {"entity_key": "hostname:axdc01", "locality": "local", "segment": "10.0.0.0/24"},
            {"entity_key": "address:10.0.0.5", "locality": "local", "segment": "10.0.0.0/24"},
            {"entity_key": "address:9.9.9.9", "locality": "external", "segment": None},
            {"entity_key": "hostname:nowhere", "locality": "local", "segment": None},
        ],
        "ir_identity": [
            {"kind": "dns", "identifier": "axdc04.axellio.dom", "entity_key": "address:10.0.0.5"},
            # external: the capture's 670-row majority, and none of it belongs in the twin's zone
            {"kind": "dns", "identifier": "www.sharepoint.us", "entity_key": "address:9.9.9.9"},
            # mDNS/SRV service names are not hosts
            {"kind": "dns", "identifier": "_pxstats._tcp.local", "entity_key": "address:10.0.0.5"},
            # reserved suffix
            {"kind": "dns", "identifier": "dev-r1e2.local", "entity_key": "address:10.0.0.5"},
        ],
    }
    assert zone_names_from_pit(pit) == ["axdc01", "axdc04"]


def _external(*port_lists):
    return {"ir_entity": [{"locality": "external", "listen_ports": json.dumps(list(p))}
                          for p in port_lists]}


def test_the_external_census_counts_HOSTS_and_ignores_the_local_population():
    """§7.4: the sink cannot observe itself, so the honest input is a census of the population it
    REPLACES — and that population is external by definition."""
    pit = _external(["443/tcp"], ["443/tcp", "123/udp"], ["123/udp"], ["25/tcp"])
    pit["ir_entity"].append({"locality": "local", "listen_ports": '["9999/tcp"]'})
    census = dict(external_listen_census(pit))
    assert census[(443, "tcp")] == 2 and census[(123, "udp")] == 2
    assert (9999, "tcp") not in census


def test_the_sinks_real_services_are_DERIVED_from_that_census_not_hardcoded():
    """§8.2's ruling is *based on the observed data*, and the sink's own set is the one place a
    twin could most easily fall back on an invented default."""
    pit = _external(*([["443/tcp"]] * 150), *([["123/udp"]] * 50), ["53/udp"], ["25/tcp"])
    # 443 and 123 clear the share bar; the two singletons (1 of 202, under 1%) do not — and
    # `53/udp` is the one that matters, because it IS recipe-covered, so only the SHARE excludes
    # it. One external host answering DNS is not "the internet answers DNS".
    assert sink_real_services(pit) == ((123, "udp"), (443, "tcp"))


def test_a_port_no_recipe_covers_is_never_promoted_to_a_real_service():
    """Admitting it would only move it from the echo to the echo, and the sidecar would then
    claim a real server this project does not have."""
    pit = _external(*([["25/tcp"]] * 50), *([["443/tcp"]] * 50))
    assert sink_real_services(pit) == ((443, "tcp"),)


def test_a_pit_with_no_external_census_falls_back_to_the_two_headline_services():
    assert COMPILER_SINK_PORTS == ((443, "tcp"), (123, "udp"))
    assert sink_real_services({"ir_entity": []}) == COMPILER_SINK_PORTS
    # ... and so does one whose whole external population is unservable.
    assert sink_real_services(_external(["25/tcp"])) == COMPILER_SINK_PORTS


# ---------------------------------------------------------------------------------------------
# The echo, asserted against the SHIPPED path
#
# These four moved here from `tests/test_persona_synth.py` on 2026-09-05, with
# `responder.responder_script()`, which they used to call and which had no production caller
# left once `services._render_echo` took over. Every property is unchanged and every reason is
# still the measured one; only the renderer under test moved.
# ---------------------------------------------------------------------------------------------

def test_the_echo_starts_detached_and_fails_loud():
    """Two failure modes this exists downstream of, both measured on dev45: a background process
    launched from a guest-agent session is reaped when that session ends (GAP-020), and a
    responder that silently failed to start is indistinguishable from the no-listener defect it
    fixes (LES-001)."""
    script = render_serve_script(plan_node_services("h", [(443, "udp"), (9997, "tcp")]))
    assert "systemd-run --unit=axr-responder" in script
    assert "systemctl is-active --quiet axr-responder.service" in script
    assert "443/udp" in script and "9997/tcp" in script
    # Replaced cleanly on re-apply: a stale unit still holding the ports would make the new one
    # fail to bind, which reads as "the responder does not work".
    assert "systemctl stop axr-responder.service" in script


def test_an_echo_only_node_needs_no_package_manager():
    """A twin segment often has no egress; putting the fix behind apt would make it unavailable
    exactly where it is needed. The helper is DEFINED in every script's preamble, so the
    property is that it is never CALLED when no recipe fired."""
    plan = plan_node_services("h", [(443, "udp"), (9997, "tcp")])
    assert plan.packages == () and plan.recipes == ()
    script = render_serve_script(plan)
    called = [ln for ln in script.splitlines()
              if ln.startswith("axr_pkg ") and not ln.lstrip().startswith("#")]
    assert called == []
    assert "/usr/bin/python3" in script


def test_every_socket_binds_before_any_thread_starts():
    """Binding inside a per-port thread hides a PARTIAL failure completely: the thread raises,
    the process survives on its healthy siblings, and `systemctl is-active` still reports active.
    Measured on dns-resolver-10-80-64 -- the port-53 thread died on EADDRINUSE, the unit reported
    healthy, and the deploy op passed while the twin's dns-resolver answered nothing. A responder
    serving SOME of its ports is worse than one that fails outright, because the fingerprint then
    blames the personas."""
    body = render_serve_script(
        plan_node_services("h", [(443, "udp")])).split("AXR_RESPONDER_EOF")[1]
    assert body.index("s.bind(") < body.index("threading.Thread(target=target")
    assert "raise SystemExit(1)" in body


def test_a_port_a_real_service_already_serves_is_a_success_at_runtime_too():
    """Belt and braces behind the render-time guard. That guard makes contention unreachable
    from THIS renderer; the runtime skip still covers a port some process outside this script
    owns. EADDRINUSE is a skip; every other bind error is still fatal."""
    body = render_serve_script(
        plan_node_services("h", [(443, "udp")])).split("AXR_RESPONDER_EOF")[1]
    assert "errno.EADDRINUSE" in body
    assert "already served by another process" in body
    assert "raise SystemExit(1)" in body
