"""Tests for the protocol-engine round (`docs/WORK_ORDER_protocol_engines.md`).

Two slices live here:

* **E0 — multiple persona instances per host.** A twin host may now carry
  several DIFFERENT personas at once (the round's whole premise: one engine
  per protocol family, stacked on one client). What stays refused is the
  same persona bound onto one host twice, and a `replay[]` binding sharing a
  host with anything.
* **E5 — Linux real clients + the containment reversal.** Seven new
  `_COMMAND_TEMPLATES` rows (SMB/LDAP/Kerberos/DCE-RPC/RDP/SSH/SNMP), and
  `_check_containment`'s IP-literal requirement reversed behind an explicit
  switch (ruling 1, 2026-09-21 — containment is now a NETWORK property).

Cluster-free, like `tests/test_traffic_tier.py` and `tests/test_personas.py`:
a real `StateStore` over a tmp SQLite file with hand-seeded op rows plus a
fake persona backend / fake guest agent.
"""
from __future__ import annotations

import pytest

from axrange.o2control import (
    TrafficPlanError,
    destroy_traffic_profile,
    traffic_status,
)
from axrange.o2control.traffic import (
    AssignmentSpec,
    ReplayBindingSpec,
    RoleAssignmentSpec,
    TrafficProfileSpec,
    resolve_traffic_bindings,
    traffic_info_from_deploy,
)
from axrange.personas.drivers import PersonaDriverError
from axrange.personas.drivers.command import (
    CommandDriver,
    _COMMAND_TEMPLATES,
    render_command_client,
    required_binaries,
    required_packages,
    supported_protos,
)
from axrange.personas.schema import Emit, Persona
from axrange.planner import StateStore
from tests.test_traffic_tier import (  # reuse, don't re-seed (LES-025)
    TWIN_DEPLOY,
    _FakePersonaBackend,
    _apply,
    _seeded_db,
)

# Two personas that bind onto ANY twin without dragging a four-role fixture
# along (`requires: []`, reserved/segment-scoped peer roles only) -- the same
# reason `test_traffic_tier._SIMPLE_PERSONA` picks `dmz-web`.
_PERSONA_A = "dmz-web"
_PERSONA_B = "broadcast-chatter"


# ===========================================================================
# E0 -- a host may carry MORE THAN ONE persona instance
# ===========================================================================
def test_two_distinct_personas_on_one_host_resolve_to_two_instances(tmp_path):
    """The E0 lift: `browser` + `command` + `scripted` on one client is the
    point of the round, so two DISTINCT personas on one host must resolve --
    into two instances with distinct ids (the identity model already keys
    per-(host, persona), §3.1)."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[
                AssignmentSpec(node="ws01", persona=_PERSONA_A),
                AssignmentSpec(node="ws01", persona=_PERSONA_B),
            ],
        )
        res = resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    on_ws01 = [ra for ra in res.resolved if ra.host_id == "ws01"]
    assert len(on_ws01) == 2
    assert {ra.instance_id for ra in on_ws01} == {
        f"ws01-{_PERSONA_A}", f"ws01-{_PERSONA_B}",
    }
    assert {ra.persona.id for ra in on_ws01} == {_PERSONA_A, _PERSONA_B}


def test_two_distinct_personas_on_one_host_apply_as_two_units(tmp_path):
    """...and both actually get driven: two `drive-persona` ops, two running
    instances, two distinct in-guest identities."""
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="ws01", persona=_PERSONA_A),
            AssignmentSpec(node="ws01", persona=_PERSONA_B),
        ],
    )
    _, backend, status = _apply(tmp_path, spec, db=db)
    assert status.status == "completed"
    assert backend.running == {f"ws01-{_PERSONA_A}", f"ws01-{_PERSONA_B}"}
    vmids = {c["vmid"] for c in backend.calls if c.get("instance_id")}
    assert vmids == {100001}  # same host, two instances


def test_same_persona_twice_on_one_host_is_still_refused_by_name(tmp_path):
    """The refusal E0 KEEPS: an overlapping `node:`/`role:` pair binding the
    SAME persona twice collides on instance_id (one systemd unit, one
    in-guest dir, one op scope) -- and the message has to say so."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[
                AssignmentSpec(node="ws01", persona=_PERSONA_A),
                AssignmentSpec(role="corp-workstation", persona=_PERSONA_A),
            ],
        )
        with pytest.raises(TrafficPlanError) as e:
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    msg = str(e.value)
    assert "ws01" in msg
    assert _PERSONA_A in msg
    assert f"ws01-{_PERSONA_A}" in msg          # the colliding instance id
    assert "systemd unit" in msg
    assert "SAME persona" in msg


def test_role_disagreement_on_a_host_is_refused_not_silently_picked(tmp_path):
    """Role resolution is unchanged by persona count (W1: tfc `roles:` ->
    clone-vm baseline -> none). Two `roles:` entries that DISAGREE about one
    host are the only way a disagreement can arise, and that is a loud
    refusal -- never an entry-order-dependent pick -- even when the host also
    carries two personas."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            roles=[
                RoleAssignmentSpec(node="ws01", role="web-tier"),
                RoleAssignmentSpec(node="ws01", role="file-server"),
            ],
            assignments=[
                AssignmentSpec(node="ws01", persona=_PERSONA_A),
                AssignmentSpec(node="ws01", persona=_PERSONA_B),
            ],
        )
        with pytest.raises(TrafficPlanError, match="assigned a role twice"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


def test_role_baseline_is_unchanged_by_carrying_two_personas(tmp_path):
    """The positive half: stacking two personas onto a host does not change
    (or ambiguate) its role -- the twin's own `clone-vm` baseline still
    names it, and both instances resolve peers against that same map."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[
                AssignmentSpec(node="ws01", persona=_PERSONA_A),
                AssignmentSpec(node="ws01", persona=_PERSONA_B),
            ],
        )
        res = resolve_traffic_bindings(spec, store)
        host = {h.host_id: h for h in res.twin.hosts}["ws01"]
    finally:
        store.close()
    assert host.role == "corp-workstation"
    assert host.role_baseline == "corp-workstation"
    assert res.roles == []  # no tfc override authored


def test_status_and_info_report_both_instances_on_one_host(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="ws01", persona=_PERSONA_A),
            AssignmentSpec(node="ws01", persona=_PERSONA_B),
        ],
    )
    _, _backend, status = _apply(tmp_path, spec, db=db)

    st = traffic_status(db_path=db, deploy_id=status.deploy_id)
    assert {a.persona for a in st.assignments} == {_PERSONA_A, _PERSONA_B}
    assert all(a.hosts == ["ws01"] for a in st.assignments)

    store = StateStore(db)
    try:
        info = traffic_info_from_deploy(
            store, status.deploy_id, target=TWIN_DEPLOY, target_status="completed",
        )
    finally:
        store.close()
    assert {a.instance_id for a in info.assignments} == {
        f"ws01-{_PERSONA_A}", f"ws01-{_PERSONA_B}",
    }
    assert {a.host_id for a in info.assignments} == {"ws01"}


def test_destroy_stops_both_instances_on_one_host(tmp_path):
    db = _seeded_db(tmp_path)
    spec = TrafficProfileSpec(
        target=TWIN_DEPLOY,
        assignments=[
            AssignmentSpec(node="ws01", persona=_PERSONA_A),
            AssignmentSpec(node="ws01", persona=_PERSONA_B),
        ],
    )
    _, backend, _status = _apply(tmp_path, spec, db=db)
    result = destroy_traffic_profile(
        db_path=db, target=TWIN_DEPLOY, persona_backend=backend,
    )
    assert result.destroyed is True
    assert result.errors == []
    stopped = {inst for _vmid, _drive, inst in backend.stopped}
    assert stopped == {f"ws01-{_PERSONA_A}", f"ws01-{_PERSONA_B}"}
    assert backend.running == set()


def test_replay_binding_still_refuses_to_share_a_host(tmp_path):
    """E0 deliberately KEEPS `replay[]`'s whole-host exclusivity: a pcap
    replay re-emits a captured device's entire NIC, so nothing may emit
    alongside it."""
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            assignments=[AssignmentSpec(node="ws01", persona=_PERSONA_A)],
            replay=[ReplayBindingSpec(
                template="corp.pcap", onto_role="corp-workstation",
                orig_ip="192.168.1.10",
            )],
        )
        with pytest.raises(TrafficPlanError) as e:
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()
    assert "WHOLE" in str(e.value)
    assert "ws01" in str(e.value)


def test_two_replay_bindings_cannot_share_a_host_either(tmp_path):
    db = _seeded_db(tmp_path)
    store = StateStore(db)
    try:
        spec = TrafficProfileSpec(
            target=TWIN_DEPLOY,
            replay=[
                ReplayBindingSpec(
                    template="a.pcap", onto_role="domain-controller",
                    orig_ip="192.168.1.10",
                ),
                ReplayBindingSpec(
                    template="b.pcap", onto_role="domain-controller",
                    orig_ip="192.168.1.11",
                ),
            ],
        )
        with pytest.raises(TrafficPlanError, match="claimed by both"):
            resolve_traffic_bindings(spec, store)
    finally:
        store.close()


# ===========================================================================
# E5 -- Linux real clients + the containment reversal
# ===========================================================================
_PEER = "203.0.113.7"
_NEW_PROTOS = ("rpc", "kerberos", "ldap", "rdp", "smb", "snmp", "ssh")


def _persona(*emits, persona_id="real-client", knobs=None, os_family="linux"):
    return Persona(
        id=persona_id, os_family=os_family, drive="command", emits=tuple(emits),
        requires=tuple(sorted({e.peer_role for e in emits if e.peer_role})),
        knobs=dict(knobs or {}),
    )


def _emits_of(src):
    import ast

    for line in src.splitlines():
        if line.startswith("EMITS = "):
            return ast.literal_eval(line[len("EMITS = "):])
    raise AssertionError("rendered client has no EMITS literal")


def _argv(src, idx=0):
    return _emits_of(src)[idx]["argv"][0][0]


def _render(proto, *, knobs=None, peer=_PEER, **emit_kw):
    p = _persona(Emit(proto=proto, peer_role="sink", **emit_kw), knobs=knobs)
    return render_command_client(p, instance_id=f"h1-{proto}", peers={"sink": peer}, seed=1)


# --- the seven new rows ----------------------------------------------------


def test_the_seven_new_protos_are_supported_and_declare_their_tooling():
    assert set(_NEW_PROTOS) <= set(supported_protos())
    expected = {
        "smb": ("smbclient", "smbclient"),
        "ldap": ("ldapsearch", "ldap-utils"),
        "kerberos": ("kinit", "krb5-user"),
        "rpc": ("rpcclient", "smbclient"),
        "rdp": ("xfreerdp", "freerdp2-x11"),
        "ssh": ("ssh", "openssh-client"),
        "snmp": ("snmpget", "snmp"),
    }
    for proto, (binary, package) in expected.items():
        tpl = _COMMAND_TEMPLATES[proto]
        assert (tpl.binary, tpl.package) == (binary, package), proto


def test_every_new_template_renders_a_real_non_interactive_argv():
    """Each row produces an argv that runs its declared binary
    non-interactively -- never a tool that would block on a prompt for the
    life of the twin.

    `argv[0]` IS that binary, except where the row declares a credential
    helper that wraps it (`ssh` -> `sshpass -f <path> ssh …`, 2026-09-21):
    then the helper is first, the tool follows, and the prompt sshpass
    answers is the whole point."""
    for proto in _NEW_PROTOS:
        src = _render(proto)
        emit = _emits_of(src)[0]
        argv = emit["argv"][0][0]
        wrappers = [b for b, _ in _COMMAND_TEMPLATES[proto].credential_binaries]
        assert argv[0] == emit["binary"] or (
            argv[0] in wrappers and emit["binary"] in argv
        ), proto
        assert emit["package"], proto
        assert emit["timeout_s"] > 0, proto


def test_smb_fetches_a_file_so_the_byte_volume_is_real():
    argv = _argv(_render("smb"))
    assert argv[1] == f"//{_PEER}/share"
    assert "-c" in argv
    assert argv[argv.index("-c") + 1].startswith("get ")


def test_ldap_simple_bind_by_default_and_gssapi_when_asked():
    simple = _argv(_render("ldap"))
    assert "-x" in simple                      # simple bind
    assert f"ldap://{_PEER}:389" in simple
    assert "-Y" not in simple

    gssapi = _argv(_render(
        "ldap", knobs={"ldap_sasl": "gssapi", "target_name": "dc01.twin.invalid"},
    ))
    assert gssapi[gssapi.index("-Y") + 1] == "GSSAPI"
    assert "-x" not in gssapi
    assert "ldap://dc01.twin.invalid:389" in gssapi


def test_ldap_gssapi_does_not_canonicalize_the_sasl_host_via_reverse_dns():
    """dev45 2026-09-21: without `-N`, OpenLDAP reverse-resolves the host
    before building the SPN. A twin serves no PTR records, so it asked for a
    principal the KDC has never heard of -- `Local error (-2) … Server not
    found in Kerberos database`, exit 250, from a ccache in which `kvno
    ldap/dc.corp.axr.lab` succeeded. Deleting `-N` re-breaks GSSAPI binds on
    every twin."""
    gssapi = _argv(_render("ldap", knobs={"ldap_sasl": "gssapi"}))
    assert "-N" in gssapi
    # ...and the simple-bind shape has no business carrying it.
    assert "-N" not in _argv(_render("ldap"))


def test_ldap_declares_the_sasl_mech_plugin_no_probe_could_catch():
    """`ldap-utils` does NOT depend on the GSSAPI mech plugin, and the plugin
    ships no binary -- so the start-time `command -v` probe is structurally
    blind to it and the failure reads as `Unknown authentication method (-6)
    … no mechanism available`, i.e. as the server's fault. `required_packages`
    is where a recipe/image learns about it."""
    pkgs = required_packages(_persona(Emit(proto="ldap", peer_role="sink")))
    assert "ldap-utils" in pkgs                      # the binary's package...
    assert "libsasl2-modules-gssapi-mit" in pkgs     # ...and the mechanism's
    assert "libsasl2-modules" in pkgs
    # required_packages is a superset of required_binaries' package column.
    assert {p for _, p in required_binaries(
        _persona(Emit(proto="ldap", peer_role="sink"))
    )} <= set(pkgs)


def test_kerberos_runs_kinit_then_kvno_in_one_firing():
    src = _render("kerberos")
    emit = _emits_of(src)[0]
    assert emit["argv"][0][0][0] == "kinit"
    follow = emit["follow_up"][0][0]
    assert follow[0][0] == "kvno"
    # ...and the client really runs the follow-up, not just records it.
    assert "follow_up" in src


def test_rpc_runs_an_allowlisted_rpcclient_command():
    argv = _argv(_render("rpc"))
    assert argv[argv.index("-c") + 1] == "lsaquery"
    argv = _argv(_render("rpc", knobs={"rpc_command": "enumdomusers"}))
    assert argv[argv.index("-c") + 1] == "enumdomusers"
    with pytest.raises(PersonaDriverError, match="rpc_command"):
        _render("rpc", knobs={"rpc_command": "deldriver ; rm -rf /"})


def test_rdp_is_auth_only_and_headless():
    argv = _argv(_render("rdp"))
    assert "+auth-only" in argv
    assert f"/v:{_PEER}:3389" in argv
    assert any(t.startswith("/sec:nla") for t in argv)


def test_ssh_authenticates_so_the_session_completes():
    """dev45 2026-09-21: the key-auth-only shape produced `Permission denied
    (publickey,password)` / exit 255 on EVERY firing -- a real KEX and a
    client that never did what it claims to. The default shape now carries
    the lab credential through `sshpass -f <0600 path>`, so the remote
    command actually runs."""
    from axrange.personas.drivers.base import instance_paths

    argv = _argv(_render("ssh"))
    assert argv[0] == "sshpass"
    assert argv[1] == "-f"
    assert argv[2] == f"{instance_paths('h1-ssh')['dir']}/password"
    assert argv[3] == "ssh"
    # BatchMode and a password are mutually exclusive -- BatchMode refuses
    # the very prompt sshpass exists to answer.
    assert "BatchMode=yes" not in argv
    assert "PreferredAuthentications=password" in argv
    assert "NumberOfPasswordPrompts=1" in argv
    assert "StrictHostKeyChecking=no" in argv
    # the PATH is rendered, never the secret
    assert "sshpass" in dict(required_binaries(
        _persona(Emit(proto="ssh", peer_role="sink"))
    ))


def test_ssh_without_a_credential_is_the_key_auth_batchmode_shape():
    """`auth_credential: none` keeps the pre-2026-09-21 argv verbatim: no
    sshpass, no credential file, BatchMode on. "No credential" must never
    silently become "some credential"."""
    argv = _argv(_render("ssh", knobs={"auth_credential": "none"}))
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert not [t for t in argv if "password" in t]
    assert "sshpass" not in dict(required_binaries(
        _persona(Emit(proto="ssh", peer_role="sink"),
                 knobs={"auth_credential": "none"})
    ))


def test_snmpget_reads_an_oid_over_udp():
    argv = _argv(_render("snmp"))
    assert argv[-1] == "1.3.6.1.2.1.1.1.0"
    assert f"{_PEER}:161" in argv
    assert _COMMAND_TEMPLATES["snmp"].explicit_transports == frozenset({"udp"})


def test_unsupported_transport_is_pinned_per_tool():
    """The `ntpdate is UDP-only` pattern, kept for every new row: an
    EXPLICIT transport a tool cannot drive is a loud authoring error."""
    for proto, bad in (("smb", "udp"), ("ssh", "udp"), ("snmp", "tcp"),
                       ("rdp", "udp"), ("rpc", "udp")):
        with pytest.raises(PersonaDriverError) as e:
            _render(proto, transport=bad)
        assert bad in str(e.value), proto
    # LDAP genuinely speaks both (tcp = LDAP, udp = CLDAP).
    for transport in ("tcp", "udp"):
        _render("ldap", transport=transport)
    # `kinit` is the `ping` case: it uses BOTH on the wire but has no argv
    # slot to force either, so it can honour NEITHER explicit choice.
    for transport in ("tcp", "udp"):
        with pytest.raises(PersonaDriverError):
            _render("kerberos", transport=transport)


def test_required_binaries_names_the_debian_package_for_a_new_proto():
    p = _persona(
        Emit(proto="smb", peer_role="a"), Emit(proto="kerberos", peer_role="b"),
    )
    # `kvno` comes along: a follow-up step's tool is as load-bearing as the
    # primary's, so the start-time probe has to cover it too.
    assert required_binaries(p) == (
        ("kinit", "krb5-user"), ("kvno", "krb5-user"), ("smbclient", "smbclient"),
    )


def test_missing_binary_probe_names_the_package(tmp_path):
    from tests.test_personas import _FakeGA

    ga = _FakeGA(run_exit=0, missing_tools=["smbclient"])
    p = _persona(Emit(proto="smb", peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(
            ga, 101, p, instance_id="h1", peers={"sink": _PEER},
            credentials={"default_password": "s3cret-lab-pw"},
        )
    msg = str(e.value)
    assert "smbclient" in msg
    assert "apt: smbclient" in msg


# --- (a) the containment reversal -----------------------------------------


def test_name_targets_are_allowed_by_default(tmp_path):
    """Ruling 1 (2026-09-21): a guest resolving a name is the NEW model --
    the twin's own DNS answers, a sink impersonates the world, the router
    denies egress. A name-bearing target must render."""
    src = _render("smb", knobs={"target_name": "files.twin.invalid"})
    argv = _argv(src)
    assert "//files.twin.invalid/share" in argv
    assert _PEER not in " ".join(argv)


def test_strict_mode_still_refuses_a_name_target():
    """The old rule stays REACHABLE (and tested): an operator who wants the
    pre-reversal structural guarantee sets it and gets the same refusal."""
    p = _persona(
        Emit(proto="smb", peer_role="sink"), knobs={"target_name": "files.twin.invalid"},
    )
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(
            p, instance_id="h1", peers={"sink": _PEER}, require_ip_literals=True,
        )
    assert "files.twin.invalid" in str(e.value)
    assert "require_ip_literals" in str(e.value)


def test_strict_mode_refuses_kerberos_outright():
    """`kinit` is issued against a REALM, never an IP -- so strict mode
    cannot contain it, and says so rather than rendering something that
    would quietly never work."""
    p = _persona(Emit(proto="kerberos", peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(
            p, instance_id="h1", peers={"sink": _PEER}, require_ip_literals=True,
        )
    assert "kerberos" in str(e.value)


def test_the_old_ip_literal_templates_are_unchanged_by_the_reversal():
    """Regression pin: reversing check 2 must not change what the five
    pre-existing templates render -- they still address the baked peer IP."""
    for proto in ("dns", "http", "https", "ntp", "icmp"):
        argv = _argv(_render(proto))
        assert any(_PEER in tok for tok in argv), proto


def test_checks_one_and_three_survive_the_reversal(monkeypatch):
    """Only check 2 is reversed. A template that declares an address it does
    not use (1), or smuggles an UNDECLARED endpoint (3), still fails loud --
    in BOTH modes."""
    import dataclasses

    from axrange.personas.drivers import command as cmd_mod

    def undeclared(peer, port, ctx):
        url = f"https://{peer}:{port}/"
        return ["curl", "-q", "-sS", url, "https://evil.invalid/"], (url,)

    def absent(peer, port, ctx):
        return ["curl", "-q", "-sS", "-o", "/dev/null"], (f"https://{peer}/",)

    for build, needle in ((undeclared, "undeclared"), (absent, "not in its own argv")):
        broken = dataclasses.replace(_COMMAND_TEMPLATES["https"], build=build)
        monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
        p = _persona(Emit(proto="https", ports=(443,), peer_role="sink"))
        for strict in (False, True):
            with pytest.raises(PersonaDriverError) as e:
                render_command_client(
                    p, instance_id="h1", peers={"sink": _PEER},
                    require_ip_literals=strict,
                )
            assert needle in str(e.value)


def test_resolve_pin_still_works_as_a_non_load_bearing_hint(monkeypatch):
    """`_bound_hosts` keeps working: a `--resolve`-pinned name renders in
    BOTH modes (it is a legitimate pin, just no longer load-bearing)."""
    import dataclasses

    from axrange.personas.drivers import command as cmd_mod

    def bound(peer, port, ctx):
        url = "https://fileserver/"
        return ["curl", "-q", "-sS", "--resolve", f"fileserver:{port}:{peer}", url], (url,)

    broken = dataclasses.replace(_COMMAND_TEMPLATES["https"], build=bound)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    for strict in (False, True):
        src = render_command_client(
            p, instance_id="h1", peers={"sink": _PEER}, require_ip_literals=strict,
        )
        assert "--resolve" in _argv(src)


# --- credentials: references, never literals -------------------------------


def test_a_literal_secret_knob_is_refused_and_names_the_reference():
    for knob in ("password", "auth_password", "smb_password", "snmp_community"):
        p = _persona(Emit(proto="smb", peer_role="sink"), knobs={knob: "hunter2"})
        with pytest.raises(PersonaDriverError) as e:
            render_command_client(p, instance_id="h1", peers={"sink": _PEER})
        msg = str(e.value)
        assert knob in msg
        assert "auth_credential" in msg


def test_the_rendered_client_carries_a_credential_PATH_never_the_secret():
    from axrange.personas.drivers.base import instance_paths

    p = _persona(
        Emit(proto="smb", peer_role="sink"), Emit(proto="ldap", peer_role="sink"),
    )
    src = render_command_client(p, instance_id="h1-real", peers={"sink": _PEER})
    assert "s3cret-lab-pw" not in src
    assert f"{instance_paths('h1-real')['dir']}/smb-auth" in src
    assert f"{instance_paths('h1-real')['dir']}/password" in src


def test_credential_files_are_written_0600_at_start_not_into_the_script():
    from tests.test_personas import _FakeGA
    from axrange.personas.drivers.base import instance_paths

    ga = _FakeGA(run_exit=0)
    p = _persona(Emit(proto="smb", peer_role="sink"), knobs={"auth_user": "svc-backup"})
    CommandDriver().start(
        ga, 101, p, instance_id="h1-real", peers={"sink": _PEER},
        credentials={"default_password": "s3cret-lab-pw"},
    )
    paths = instance_paths("h1-real")
    authfile = f"{paths['dir']}/smb-auth"
    assert "s3cret-lab-pw" in ga.written[authfile]
    assert "username = svc-backup" in ga.written[authfile]
    assert "s3cret-lab-pw" not in ga.written[paths["script"]]
    chmods = [a for a in ga.execs if a[:2] == ("chmod", "0600")]
    assert authfile in {a[2] for a in chmods}


def test_a_credential_needing_persona_without_a_source_fails_loud():
    from tests.test_personas import _FakeGA

    ga = _FakeGA(run_exit=0)
    p = _persona(Emit(proto="smb", peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(ga, 101, p, instance_id="h1", peers={"sink": _PEER})
    assert "DEFAULT_PASSWORD" in str(e.value)


def test_auth_credential_none_renders_an_anonymous_client():
    src = _render("smb", knobs={"auth_credential": "none"})
    argv = _argv(src)
    assert "-N" in argv
    assert "-A" not in argv
    # ...and nothing downstream expects a credential to exist: no stdin file,
    # no secret reference anywhere in the rendered client.
    assert "stdin_file" not in _emits_of(src)[0]
    assert not any("axr-secret-file:" in tok for tok in argv)

    # The three tools with no anonymous mode refuse it instead, by name.
    for proto in ("kerberos", "rdp", "snmp"):
        with pytest.raises(PersonaDriverError, match="auth_credential"):
            _render(proto, knobs={"auth_credential": "none"})


def test_snmp_community_never_renders_as_a_literal():
    from axrange.personas.drivers.base import instance_paths
    from axrange.personas.drivers.command import _SECRET_FILE_PREFIX

    src = _render("snmp")
    argv = _argv(src)
    community = argv[argv.index("-c") + 1]
    assert community == (
        f"{_SECRET_FILE_PREFIX}{instance_paths('h1-snmp')['dir']}/snmp-community"
    )
    # ...and the client expands it at fire time from that 0600 file.
    assert "_SECRET_FILE_PREFIX" in src or _SECRET_FILE_PREFIX in src


def test_kerberos_ccache_is_instance_scoped_not_uid_scoped():
    """E0 fallout: two persona instances on ONE host authenticating as
    different principals must not share `/tmp/krb5cc_<uid>`."""
    from axrange.personas.drivers.base import instance_paths

    a = _emits_of(_render("kerberos"))[0]["env"]["KRB5CCNAME"]
    p = _persona(Emit(proto="kerberos", peer_role="sink"), persona_id="other")
    b = _emits_of(render_command_client(
        p, instance_id="h1-other", peers={"sink": _PEER},
    ))[0]["env"]["KRB5CCNAME"]
    assert a == f"FILE:{instance_paths('h1-kerberos')['dir']}/krb5cc"
    assert a != b


def _client_namespace(src, log_path):
    """Exec the rendered client's DEFINITIONS (everything above the arming
    line, so the `while True:` loop never runs) into a namespace, with a
    recording stub in place of `subprocess` -- the same "run the rendered
    artifact, don't just assert over its text" posture the scripted/command
    functional tests take."""
    head = src.split("log('armed")[0]
    ns: dict = {}
    exec(compile(head, "<client>", "exec"), ns)
    ns["LOG"] = str(log_path)
    calls: list[dict] = []

    class _Stub:
        DEVNULL = -3

        class SubprocessError(Exception):
            pass

        @staticmethod
        def run(argv, **kw):
            calls.append({"argv": list(argv), "input": kw.get("input"),
                          "env": kw.get("env")})
            return type("R", (), {"returncode": 0})()

    ns["subprocess"] = _Stub
    return ns, calls


def test_the_rendered_client_expands_a_secret_reference_at_fire_time(tmp_path):
    src = _render("snmp")
    from axrange.personas.drivers.command import _SECRET_FILE_PREFIX

    ns, calls = _client_namespace(src, tmp_path / "persona.log")
    community_path = tmp_path / "snmp-community"
    community_path.write_text("s3cret-lab-pw\n")
    argv = [
        "snmpget", "-v", "2c", "-c", _SECRET_FILE_PREFIX + str(community_path),
        f"{_PEER}:161", "1.3.6.1.2.1.1.1.0",
    ]
    emit = dict(_emits_of(src)[0], argv=[[argv]], gap_s=0, messages=1)
    ns["fire"](emit)
    assert calls[0]["argv"][4] == "s3cret-lab-pw"   # expanded, not the path
    assert "axr-secret-file:" not in " ".join(calls[0]["argv"])
    assert "MISSING CREDENTIAL" not in (tmp_path / "persona.log").read_text()


def test_the_rendered_client_counts_a_missing_credential_instead_of_skipping(tmp_path):
    """LES-245/LES-001: an absent credential file is a FAILED fire, announced
    once and counted -- never a silent no-op that looks like success."""
    src = _render("snmp")
    ns, calls = _client_namespace(src, tmp_path / "persona.log")
    emit = dict(_emits_of(src)[0], gap_s=0, messages=1)
    ns["fire"](emit)
    log = (tmp_path / "persona.log").read_text()
    assert calls == []
    assert "MISSING CREDENTIAL" in log
    assert "missing_cred=1" in log
    assert "ok=0 fails=1" in log


def test_the_rendered_client_runs_the_kerberos_follow_up_with_the_password(tmp_path):
    src = _render("kerberos")
    ns, calls = _client_namespace(src, tmp_path / "persona.log")
    pw = tmp_path / "password"
    pw.write_text("s3cret-lab-pw")
    emit = dict(
        _emits_of(src)[0], gap_s=0, messages=1, stdin_file=str(pw),
    )
    ns["fire"](emit)
    assert [c["argv"][0] for c in calls] == ["kinit", "kvno"]
    assert calls[0]["input"] == "s3cret-lab-pw\n"
    assert calls[0]["env"]["KRB5CCNAME"].endswith("/krb5cc")
    assert "runs=2 ok=2 fails=0" in (tmp_path / "persona.log").read_text()


def test_determinism_holds_for_every_new_template():
    for proto in _NEW_PROTOS:
        p = _persona(Emit(proto=proto, peer_role="sink"))
        a = render_command_client(
            p, instance_id="h1", peers={"sink": _PEER}, seed=11,
        )
        b = render_command_client(
            p, instance_id="h1", peers={"sink": _PEER}, seed=11,
        )
        assert a == b, proto


def test_new_clients_still_compile_and_never_go_through_a_shell():
    p = _persona(*(Emit(proto=proto, peer_role="sink") for proto in _NEW_PROTOS))
    src = render_command_client(p, instance_id="h1", peers={"sink": _PEER}, seed=3)
    compile(src, "<command-persona-client>", "exec")
    assert "shell=True" not in src
    assert "os.system" not in src
