"""axrange/personas/ — schema, registry, PSN-0xx catalog checks, and the
three drivers (docs/WORK_ORDER_persona_library.md §4).
"""
from __future__ import annotations

import subprocess
import shutil

import pytest

from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.personas.drivers import (
    BrowserDriver,
    CommandDriver,
    PersonaDriverError,
    RealServiceDriver,
    ReplayDriver,
    ScriptedDriver,
    get_driver,
    instance_paths,
    write_binary_file,
)
from axrange.personas.drivers.browser import (
    _BROWSER_PROBE_MARKER,
    _FOUND_TAG,
    browser_profile_dir,
    render_browser_client,
    render_browser_launch,
    render_browser_probe,
)
from axrange.personas.drivers.browser import supported_protos as browser_protos
from axrange.personas.drivers.command import (
    _COMMAND_TEMPLATES,
    _DEFAULT_DNS_QNAME,
    _MISSING_TAG,
    _PING_MAX_PAYLOAD,
    _TOOL_PROBE_MARKER,
    render_command_client,
    render_command_launch,
    render_tool_probe,
    required_binaries,
    supported_protos,
)
from axrange.personas.drivers.scripted import (
    render_scripted_client,
    render_scripted_client_windows,
    render_scripted_launch,
)
from axrange.personas.drivers.replay import render_replay_launch
from axrange.personas.registry import (
    PERSONAS,
    PersonaRegistryError,
    all_ids,
    get,
    is_known_id,
    load_catalog_dir,
)
from axrange.personas.schema import Emit, Persona, PersonaSchemaError, load_persona
from axrange.validator.catalog import get_rule, list_rules
from axrange.validator.checks.personas import validate_catalog, validate_persona
from axrange.validator.finding import RuleCategory


# --- catalog / registry ---------------------------------------------------


def test_catalog_loads_starter_set():
    assert len(PERSONAS) >= 20
    assert is_known_id("corp-workstation")
    assert not is_known_id("no-such-persona")
    assert get("domain-controller") is not None
    assert all_ids() == sorted(PERSONAS.keys())


def test_every_catalog_persona_validates_clean():
    """No PSN-0xx finding for any curated starter-catalog persona."""
    findings = validate_catalog()
    bad = {pid: f for pid, f in findings.items() if f}
    assert not bad, bad


def test_every_real_service_persona_has_a_gap032_binding():
    """GAP-032: every shipped `drive: real-service` catalog persona must
    carry an os-appropriate `knobs.service_name`/`knobs.process_match`
    default (a Windows persona's binding must be a service name, checked via
    `Get-Service`; a Linux persona's may be either) -- otherwise driving it
    fails loud with no traffic at all the moment it's assigned."""
    unbound = []
    for pid, persona in PERSONAS.items():
        if persona.drive != "real-service":
            continue
        if not persona.knobs.get("service_name") and not persona.knobs.get("process_match"):
            unbound.append(pid)
    assert not unbound, f"real-service personas with no GAP-032 binding: {unbound}"


# --- PSN rule catalog registration -----------------------------------------


def test_psn_rules_registered_additively():
    psn_ids = [r.id for r in list_rules() if r.category == RuleCategory.PERSONA]
    assert psn_ids == [
        "PSN-010", "PSN-020", "PSN-030", "PSN-040",
        "PSN-050", "PSN-060", "PSN-070", "PSN-080", "PSN-090",
        # The high-volume persona wave's two new emit fields, 2026-07-28.
        "PSN-100", "PSN-110",
    ]
    for rid in psn_ids:
        get_rule(rid)  # doesn't raise


def test_personas_package_has_zero_validator_imports():
    """Boundary conformance (code-review): `axrange.personas` is a pure leaf
    the validator reads (mirrors `axrange.sensors`) -- it must import
    NOTHING from `axrange.validator`. `axrange.validator.checks.personas` is
    the one place persona content flows the other way. Parses actual
    `import`/`from ... import` statements (ast), not a raw substring search
    -- a docstring merely MENTIONING `axrange.validator` (as this module's
    own do, to document the boundary) must not fail this check."""
    import ast
    import pathlib

    import axrange.personas as personas_pkg

    pkg_dir = pathlib.Path(personas_pkg.__file__).parent
    for path in pkg_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("axrange.validator"), (
                        f"{path} imports {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("axrange.validator"), (
                    f"{path} imports from {mod}"
                )


# --- PSN-0xx individual checks (each fires on a deliberately-bad persona) --


def test_psn_010_empty_id():
    p = Persona(id="", os_family="linux", drive="scripted")
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-010" in ids


def test_psn_020_bad_os_family():
    p = Persona(id="x", os_family="plan9", drive="scripted")
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-020" in ids


def test_psn_030_bad_drive():
    p = Persona(id="x", os_family="linux", drive="teleport")
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-030" in ids


def test_psn_040_unknown_proto():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="carrier-pigeon", peer_role="dc"),),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-040" in ids


def test_psn_050_dangling_peer_role():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role=""),),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-050" in ids


def test_psn_090_peer_role_not_in_requires():
    """The gap the code review caught: a persona emitting toward a
    peer_role it never declares in requires[] must fail PSN-090, distinct
    from PSN-050 (presence-only)."""
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role="dns-resolver"),),
        requires=("domain-controller",),  # dns-resolver is NOT declared
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-090" in ids
    assert "PSN-050" not in ids  # peer_role IS set -- only the dangling check fires


def test_psn_090_satisfied_when_declared_in_requires():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role="dns-resolver"),),
        requires=("dns-resolver",),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-090" not in ids


def test_psn_090_optional_peer_role_exempt_from_requires():
    """2026-07-25 refinement: an `optional: true` emit-peer need NOT be
    declared in requires[] -- PSN-090 must not fire for it."""
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role="dns-resolver", optional=True),),
        requires=(),  # deliberately NOT declared
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-090" not in ids


def test_psn_090_still_fires_for_hard_peer_role_missing_from_requires():
    """The refinement narrows PSN-090, it doesn't gut it: a HARD
    (non-optional) emit-peer not in requires[] must still fire."""
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(
            Emit(proto="dns", peer_role="dns-resolver", optional=True),
            Emit(proto="kerberos", peer_role="domain-controller"),  # hard, missing
        ),
        requires=(),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-090" in ids


def test_psn_090_reserved_tokens_exempt():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(
            Emit(proto="https", peer_role="egress"),
            Emit(proto="https", peer_role="internet"),
        ),
        requires=(),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-090" not in ids


def test_psn_090_empty_peer_role_does_not_also_fire_090():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role=""),),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-050" in ids
    assert "PSN-090" not in ids


def test_all_catalog_personas_requires_cover_every_hard_peer_role():
    """The catalog-data fix: every non-reserved, HARD (non-optional)
    peer_role a persona emits toward is declared in that SAME persona's own
    requires[] (PSN-090 clean across the whole starter catalog, not just
    the aggregate PSN-check). An `optional: true` emit (2026-07-25
    refinement) is deliberately EXEMPT -- it's a peer the persona talks to
    only if present, not something requires[] needs to cover."""
    from axrange.personas.schema import RESERVED_PEER_ROLES

    for pid, p in PERSONAS.items():
        peer_roles = {
            e.peer_role for e in p.emits
            if e.peer_role and e.peer_role not in RESERVED_PEER_ROLES and not e.optional
        }
        missing = peer_roles - set(p.requires)
        assert not missing, f"{pid}: requires[] missing {missing}"


def test_corp_workstation_marks_dns_resolver_and_print_server_optional():
    """The exact live dev45 case (docs/WORK_ORDER_traffic_tier.md): DNS is
    commonly hosted on the DC and printing is commonly absent -- neither
    should be a hard requirement that blocks binding onto a realistic AD
    twin with no dedicated dns-resolver/print-server role."""
    p = PERSONAS["corp-workstation"]
    optional_peers = {e.peer_role for e in p.emits if e.optional}
    assert {"dns-resolver", "print-server"} <= optional_peers
    assert "dns-resolver" not in p.requires
    assert "print-server" not in p.requires
    # The hard peer set narrowed to ONE (aximprint docs/JOURNAL_corpapcn_fidelity.md GAP-C5):
    # a twin cast from a real network that runs no SMB server, no proxy and no on-prem mail is
    # a correct twin, and demanding them made the entire traffic profile unappliable to it.
    # `requires` means "structurally cannot function without", and only the domain controller
    # qualifies -- this persona models a DOMAIN-JOINED workstation. The other three are now
    # resolve-if-present, on the same argument this test already makes for dns-resolver.
    assert set(p.requires) == {"domain-controller"}
    assert {"egress-proxy", "file-server", "mail-server"} <= optional_peers


def test_psn_060_bad_cadence():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role="dc", cadence="whenever-i-feel-like-it"),),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-060" in ids


def test_psn_060_accepts_cyclic_and_burst():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(
            Emit(proto="dns", peer_role="dc", cadence="cyclic:30s"),
            Emit(proto="dns", peer_role="dc", cadence="burst:1h"),
        ),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-060" not in ids


def test_psn_070_bad_volume():
    p = Persona(
        id="x", os_family="linux", drive="scripted",
        emits=(Emit(proto="dns", peer_role="dc", volume="extreme"),),
    )
    ids = {f.rule_id for f in validate_persona(p)}
    assert "PSN-070" in ids


def test_psn_080_requires_not_satisfiable():
    p = Persona(id="x", os_family="linux", drive="scripted", requires=("domain-controller",))
    # No role set supplied -> no-op (twin-agnostic catalog validation).
    assert not any(f.rule_id == "PSN-080" for f in validate_persona(p))
    # A role set that's missing the requirement -> fires.
    findings = validate_persona(p, available_roles=frozenset({"file-server"}))
    ids = {f.rule_id for f in findings}
    assert "PSN-080" in ids


def test_psn_080_satisfied_role_set_is_clean():
    p = Persona(id="x", os_family="linux", drive="scripted", requires=("domain-controller",))
    findings = validate_persona(
        p, available_roles=frozenset({"domain-controller", "file-server"})
    )
    assert not any(f.rule_id == "PSN-080" for f in findings)


def test_psn_080_reserved_peer_role_tokens_never_missing():
    p = Persona(id="x", os_family="linux", drive="scripted", requires=("egress", "internet"))
    findings = validate_persona(p, available_roles=frozenset())
    assert not any(f.rule_id == "PSN-080" for f in findings)


# --- schema loader ----------------------------------------------------------


def test_emit_optional_defaults_false():
    """Additive, backward-compatible: an Emit built without `optional`
    behaves exactly like the pre-2026-07-25 shape (hard peer)."""
    e = Emit(proto="dns", peer_role="dns-resolver")
    assert e.optional is False


def test_load_persona_parses_optional_emit_flag():
    raw = {
        "id": "x", "os_family": "linux", "drive": "scripted",
        "emits": [
            {"proto": "dns", "peer_role": "dns-resolver", "optional": True},
            {"proto": "smb", "peer_role": "file-server"},
        ],
    }
    p = load_persona(raw)
    assert p.emits[0].optional is True
    assert p.emits[1].optional is False


def test_load_persona_wrapped_and_bare():
    wrapped = {"persona": {"id": "a", "os_family": "linux", "drive": "scripted"}}
    bare = {"id": "b", "os_family": "linux", "drive": "scripted"}
    assert load_persona(wrapped).id == "a"
    assert load_persona(bare).id == "b"


def test_load_persona_missing_id_raises():
    with pytest.raises(PersonaSchemaError):
        load_persona({"os_family": "linux", "drive": "scripted"})


def test_load_persona_bad_emits_shape_raises():
    with pytest.raises(PersonaSchemaError):
        load_persona({"id": "a", "emits": "not-a-list"})


def test_registry_duplicate_id_raises(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "persona:\n  id: dup\n  os_family: linux\n  drive: scripted\n"
    )
    (tmp_path / "b.yaml").write_text(
        "persona:\n  id: dup\n  os_family: linux\n  drive: scripted\n"
    )
    with pytest.raises(PersonaRegistryError):
        load_catalog_dir(tmp_path)


def test_registry_loads_bad_but_well_shaped_persona_for_validation(tmp_path):
    """A persona that LOADS (structurally fine) but fails PSN checks
    (semantic) — the loader/validator split the docstrings describe."""
    (tmp_path / "bad.yaml").write_text(
        "persona:\n"
        "  id: bad-one\n"
        "  os_family: haiku\n"
        "  drive: scripted\n"
        "  emits:\n"
        "    - proto: carrier-pigeon\n"
        "      peer_role: dc\n"
    )
    loaded = load_catalog_dir(tmp_path)
    assert "bad-one" in loaded
    findings = validate_persona(loaded["bad-one"])
    ids = {f.rule_id for f in findings}
    assert "PSN-020" in ids
    assert "PSN-040" in ids


# --- scripted driver: render determinism + systemd-run (not nohup) --------


def test_scripted_render_is_byte_stable_given_seed():
    p = PERSONAS["corp-workstation"]
    peers = {"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"}
    a = render_scripted_client(p, instance_id="ws01-corp-workstation", peers=peers, seed=7)
    b = render_scripted_client(p, instance_id="ws01-corp-workstation", peers=peers, seed=7)
    assert a == b


def test_scripted_render_differs_across_seed_and_instance():
    p = PERSONAS["corp-workstation"]
    a = render_scripted_client(p, instance_id="ws01-corp-workstation", peers={}, seed=1)
    b = render_scripted_client(p, instance_id="ws01-corp-workstation", peers={}, seed=2)
    c = render_scripted_client(p, instance_id="ws02-corp-workstation", peers={}, seed=1)
    assert a != b
    assert a != c


def test_scripted_render_compiles():
    p = PERSONAS["plc"]
    src = render_scripted_client(p, instance_id="cell1-plc", peers={"hmi": "10.1.0.2"}, seed=3)
    assert src.startswith("#!/usr/bin/env python3")
    compile(src, "<persona-client>", "exec")


def test_scripted_launch_uses_systemd_run_not_nohup():
    """Regression guard (LES-086/GAP-020/GAP-025 class): never nohup."""
    p = PERSONAS["corp-workstation"]
    paths = instance_paths("ws01-corp-workstation")
    launch = render_scripted_launch(
        "ws01-corp-workstation", p, script_path=paths["script"]
    )
    assert "nohup" not in launch
    assert f"systemd-run --unit={paths['unit']} --collect bash -c" in launch
    assert f"systemctl reset-failed {paths['unit']}" in launch
    assert f"systemctl stop {paths['unit']}" in launch
    assert f"systemctl is-active --quiet {paths['unit']} ||" in launch
    assert "exit 1" in launch
    if shutil.which("bash"):
        res = subprocess.run(["bash", "-n"], input=launch, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr


# --- scripted driver: schedule override (2026-07-25, WORK_ORDER_traffic_tier) --


def test_scripted_render_schedule_unset_is_byte_identical_and_cadence_derived():
    """BACKWARD-COMPAT golden pin: omitting `schedule=` and passing
    `schedule=None` explicitly must render byte-identically, and the
    cadence-derived gates (corp-workstation carries both a 24x7 emit and
    several diurnal ones) must be exactly what they were before this
    override existed -- 2 'always' (dns 24x7 + print burst) and 7
    'business_hours' (the diurnal emits)."""
    p = PERSONAS["corp-workstation"]
    peers = {
        "domain-controller": "10.0.0.5", "file-server": "10.0.0.6",
        "egress-proxy": "10.0.0.7", "mail-server": "10.0.0.8",
    }
    a = render_scripted_client(p, instance_id="ws01-corp-workstation", peers=peers, seed=7)
    b = render_scripted_client(
        p, instance_id="ws01-corp-workstation", peers=peers, seed=7, schedule=None,
    )
    assert a == b
    assert a.count("'gate': 'always'") == 2
    assert a.count("'gate': 'business_hours'") == 7


def test_scripted_render_schedule_24x7_forces_every_emit_gate_always():
    p = PERSONAS["corp-workstation"]
    peers = {
        "domain-controller": "10.0.0.5", "file-server": "10.0.0.6",
        "egress-proxy": "10.0.0.7", "mail-server": "10.0.0.8",
    }
    src = render_scripted_client(
        p, instance_id="ws01-corp-workstation", peers=peers, seed=7, schedule="24x7",
    )
    assert src.count("'gate': 'always'") == len(p.emits)
    assert "'gate': 'business_hours'" not in src


def test_scripted_render_schedule_business_hours_forces_every_emit_gate():
    p = PERSONAS["corp-workstation"]
    peers = {
        "domain-controller": "10.0.0.5", "file-server": "10.0.0.6",
        "egress-proxy": "10.0.0.7", "mail-server": "10.0.0.8",
    }
    src = render_scripted_client(
        p, instance_id="ws01-corp-workstation", peers=peers, seed=7,
        schedule="business-hours",
    )
    assert src.count("'gate': 'business_hours'") == len(p.emits)
    assert "'gate': 'always'" not in src


def test_instance_unit_name_is_injective_across_sanitize_collisions():
    """Code-review fix: character-sanitizing alone is not injective (`a/b`
    and `a b` both collapse to `a-b`) -- the hash suffix restores it."""
    from axrange.personas.drivers import instance_unit

    a = instance_unit("a/b")
    b = instance_unit("a b")
    assert a != b
    # Still deterministic given the SAME instance_id.
    assert instance_unit("a/b") == a


def test_safe_label_escapes_shell_metacharacters():
    from axrange.personas.drivers import safe_label

    raw = 'ws$(rm -rf /)"`\\'
    escaped = safe_label(raw)
    assert escaped == 'ws\\$(rm -rf /)\\"\\`\\\\'


def test_scripted_launch_escapes_hostile_instance_id():
    """A persona instance_id carrying shell metacharacters must not break
    (or inject into) the rendered launch script's echo lines."""
    p = PERSONAS["corp-workstation"]
    hostile = 'ws01$(touch /tmp/pwned)"'
    paths = instance_paths(hostile)
    launch = render_scripted_launch(hostile, p, script_path=paths["script"])
    # The `$` must be BACKSLASH-escaped (so bash treats it as a literal
    # dollar sign, not the start of a command substitution) and the
    # embedded `"` must be escaped too (so it doesn't close the echo's
    # double-quoted string early).
    assert "\\$(touch /tmp/pwned)\\\"" in launch
    assert '$(touch /tmp/pwned)"' not in launch  # the UNescaped form is absent
    if shutil.which("bash"):
        res = subprocess.run(["bash", "-n"], input=launch, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr


def test_replay_launch_uses_systemd_run_not_nohup():
    p = PERSONAS["historian"]
    paths = instance_paths("hist1-historian")
    launch = render_replay_launch(
        "hist1-historian", p,
        template_path=f"{paths['dir']}/template.pcap",
        replay_path=f"{paths['dir']}/replay.pcap",
        orig_ip="192.0.2.9", node_ip="10.5.0.20", iface="eth0",
    )
    assert "nohup" not in launch
    assert f"systemd-run --unit={paths['unit']} --collect bash -c" in launch
    assert "tcpreplay -i eth0 --loop=0" in launch
    assert "tcprewrite --infile=" in launch
    assert "srcipmap=192.0.2.9/32:10.5.0.20/32" in launch
    if shutil.which("bash"):
        res = subprocess.run(["bash", "-n"], input=launch, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr


# --- fake GuestAgent harness (mirrors tests/test_sensor_exec.py's _FakeGA) -


class _FakeGA:
    def __init__(self, *, run_exit=0, missing_tools=None, profile_banner=""):
        self.run_exit = run_exit
        # The `command` driver's start-time protocol-client probe prints one
        # `MISSING:`-tagged line per MISSING binary (nothing when every tool
        # is present) -- `missing_tools` is what this fake reports back for
        # it, tagged the same way `render_tool_probe`'s real output is.
        self.missing_tools = list(missing_tools or [])
        # A login shell (`sh -lc`, which the real probe runs under) can print
        # a `/etc/profile`-style banner to stdout BEFORE the probe body ever
        # runs -- `profile_banner`, when set, prepends that to the probe's
        # stdout so a test can prove `_assert_tools_present`'s `MISSING:`
        # filtering (code review item 6) isn't fooled by it.
        self.profile_banner = profile_banner
        self.execs: list[tuple] = []
        self.shell_execs: list[str] = []
        self.written: dict[str, str] = {}

    def exec(self, vmid, argv, *, timeout_s=300):
        self.execs.append(tuple(argv))
        joined = " ".join(str(a) for a in argv)
        if argv[:2] == ["mkdir", "-p"]:
            return GuestExecResult(0, "", "")
        if _TOOL_PROBE_MARKER in joined:
            # Always exit 0: the probe's `for`-loop reports absence on
            # STDOUT, never via exit status, so a `run_exit=1` fake (which
            # exists to make the LAUNCH fail) must not also make the probe
            # look broken.
            tagged = "\n".join(f"{_MISSING_TAG}{t}" for t in self.missing_tools)
            return GuestExecResult(0, self.profile_banner + tagged, "")
        if "command -v python3" in joined or "command -v tcprewrite" in joined:
            return GuestExecResult(0, "/usr/bin/python3", "")
        if argv[:1] == ["chmod"]:
            return GuestExecResult(0, "", "")
        if argv[:3] == ["systemctl", "is-active", "--quiet"]:
            return GuestExecResult(0 if self.run_exit == 0 else 1, "", "")
        if argv[:1] == ["pgrep"]:
            return GuestExecResult(0 if self.run_exit == 0 else 1, "", "")
        # bash <launch script> / sh -c <write/stop cmd> / powershell -File <...>
        return GuestExecResult(self.run_exit, "ok", "" if self.run_exit == 0 else "boom")

    def shell_exec(self, vmid, command, *, timeout_s=300):
        # Windows driver paths (Get-Service/Get-Process/Get-ScheduledTask/
        # New-Item/...) go over `ga.shell_exec` (PowerShell), never a raw
        # argv `ga.exec` -- mirror the real `GuestAgent.shell_exec` closely
        # enough for these tests (records the command, honors run_exit the
        # same way `exec`'s generic fallback does).
        self.shell_execs.append(command)
        return GuestExecResult(0 if self.run_exit == 0 else 1, "", "" if self.run_exit == 0 else "boom")

    def write_file(self, vmid, path, content, *, mode=None):
        self.written[path] = content


# --- driver factory ---------------------------------------------------------


def test_get_driver_dispatch():
    assert isinstance(get_driver("scripted"), ScriptedDriver)
    assert isinstance(get_driver("replay"), ReplayDriver)
    assert isinstance(get_driver("real-service"), RealServiceDriver)
    # 2026-09-02: adding a `_DRIVERS` entry is the ENTIRE execution-side
    # wiring change for a new drive kind (work order SS2.6).
    assert isinstance(get_driver("command"), CommandDriver)
    with pytest.raises(PersonaDriverError):
        get_driver("teleport")


# --- ScriptedDriver start/stop/heartbeat -----------------------------------


def test_scripted_driver_start_writes_and_launches():
    """`backup-server` is a LINUX scripted persona -- proves the Linux path
    (unchanged by the Windows port, LES-025-adjacent "don't touch the
    live-verified Linux behavior" constraint) is still byte-for-byte the
    same mechanism: systemd transient unit, never `nohup`."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    result = driver.start(
        ga, 101, persona, instance_id="bkp01-backup-server",
        peers={"file-server": "10.0.0.6"}, seed=5,
    )
    assert result["started"] is True
    paths = instance_paths("bkp01-backup-server")
    assert paths["script"] in ga.written
    assert paths["dir"] + "/launch.sh" in ga.written
    assert "nohup" not in ga.written[paths["dir"] + "/launch.sh"]


def test_scripted_driver_start_fails_loud_on_launch_error():
    ga = _FakeGA(run_exit=1)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(ga, 101, persona, instance_id="bkp01-backup-server", peers={})


def test_scripted_driver_stop_and_heartbeat():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["backup-server"]
    driver = ScriptedDriver()
    r = driver.stop(ga, 101, persona, instance_id="bkp01-backup-server")
    assert r["stopped"] is True
    hb = driver.heartbeat(ga, 101, persona, instance_id="bkp01-backup-server")
    assert hb["alive"] is True


# --- ScriptedDriver Windows port (2026-07-25) ------------------------------


def test_scripted_driver_windows_start_writes_and_launches():
    """`corp-workstation` is a WINDOWS scripted persona -- the headline
    Windows-port case. Must render a PowerShell client (real TCP connects via
    `[System.Net.Sockets.TcpClient]::new(...)`, LES-022) and launch it as a
    Scheduled Task (`Register-ScheduledTask`), never `Start-Process`/`nohup`
    (LES-086's Windows analog)."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["corp-workstation"]
    assert persona.os_family == "windows"
    driver = ScriptedDriver()
    result = driver.start(
        ga, 101, persona, instance_id="ws01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=5,
    )
    assert result["started"] is True
    paths = instance_paths("ws01-corp-workstation", os_family="windows")
    assert paths["script"] in ga.written
    launch_src = ga.written[paths["dir"] + "\\launch.ps1"]
    assert "Register-ScheduledTask" in launch_src
    assert "Start-ScheduledTask" in launch_src
    assert "Start-Process" not in launch_src
    assert "nohup" not in launch_src
    client_src = ga.written[paths["script"]]
    assert "[System.Net.Sockets.TcpClient]::new(" in client_src
    # LES-022: the TcpClient (a method-argument-shaped construction, the
    # class this lesson is about) must use `[Type]::new(...)`, never
    # `New-Object TcpClient ...`.
    assert "New-Object System.Net.Sockets.TcpClient" not in client_src
    assert "New-Object 'TcpClient'" not in client_src


def test_scripted_driver_windows_client_render_byte_stable_given_seed():
    """Same inputs -> byte-identical rendered PowerShell (mirrors the Linux
    client's own determinism guarantee)."""
    from axrange.personas.drivers.scripted import render_scripted_client_windows

    persona = PERSONAS["corp-workstation"]
    kwargs = dict(
        instance_id="ws01-corp-workstation",
        peers={"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"},
        seed=42,
    )
    first = render_scripted_client_windows(persona, **kwargs)
    second = render_scripted_client_windows(persona, **kwargs)
    assert first == second
    # every non-optional emit's resolved peer:port makes it into the client.
    assert "'10.0.0.5'" in first
    assert "'10.0.0.6'" in first
    assert "kerberos" in first and "88" in first


def test_scripted_driver_windows_client_escapes_single_quotes():
    """LES-036: a peer IP/instance id containing a literal `'` must not
    break the single-quoted PS literal it's embedded in."""
    from axrange.personas.drivers.scripted import render_scripted_client_windows

    persona = PERSONAS["corp-workstation"]
    src = render_scripted_client_windows(
        persona, instance_id="ws'01-corp-workstation",
        peers={"domain-controller": "10.0.0.5"}, seed=1,
    )
    assert "ws''01-corp-workstation" in src


def test_scripted_driver_windows_client_schedule_unset_is_byte_identical():
    """BACKWARD-COMPAT golden pin (Windows path)."""
    from axrange.personas.drivers.scripted import render_scripted_client_windows

    persona = PERSONAS["corp-workstation"]
    peers = {"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"}
    a = render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers=peers, seed=42,
    )
    b = render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers=peers, seed=42, schedule=None,
    )
    assert a == b
    assert a.count("Gate='always'") == 2
    assert a.count("Gate='business_hours'") == 7


def test_scripted_driver_windows_client_schedule_24x7_forces_gate_always():
    from axrange.personas.drivers.scripted import render_scripted_client_windows

    persona = PERSONAS["corp-workstation"]
    peers = {"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"}
    src = render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers=peers, seed=42,
        schedule="24x7",
    )
    assert src.count("Gate='always'") == len(persona.emits)
    assert "Gate='business_hours'" not in src


def test_scripted_driver_windows_client_schedule_business_hours_forces_gate():
    from axrange.personas.drivers.scripted import render_scripted_client_windows

    persona = PERSONAS["corp-workstation"]
    peers = {"domain-controller": "10.0.0.5", "file-server": "10.0.0.6"}
    src = render_scripted_client_windows(
        persona, instance_id="ws01-corp-workstation", peers=peers, seed=42,
        schedule="business-hours",
    )
    assert src.count("Gate='business_hours'") == len(persona.emits)
    assert "Gate='always'" not in src


def test_scripted_driver_windows_start_fails_loud_on_launch_error():
    ga = _FakeGA(run_exit=1)
    persona = PERSONAS["corp-workstation"]
    driver = ScriptedDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(ga, 101, persona, instance_id="ws01-corp-workstation", peers={})


def test_scripted_driver_windows_stop_and_heartbeat():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["corp-workstation"]
    driver = ScriptedDriver()
    r = driver.stop(ga, 101, persona, instance_id="ws01-corp-workstation")
    assert r["stopped"] is True
    assert any("Unregister-ScheduledTask" in cmd for cmd in ga.shell_execs)
    hb = driver.heartbeat(ga, 101, persona, instance_id="ws01-corp-workstation")
    assert hb["alive"] is True
    assert any("Get-ScheduledTask" in cmd for cmd in ga.shell_execs)


def test_scheduled_task_start_block_escapes_label_for_double_quote_context():
    """`config_exec.render.scheduled_task_start_block` -- the shared Windows
    daemon-launch primitive the Windows scripted driver reuses (LES-025) --
    must escape an embedded label for the PLAIN double-quoted `Write-Output
    "..."` lines (LES-036) without over-escaping (a literal `'` needs no
    doubling there, unlike the nested-single-quote `_ps_dq_escape` case)."""
    from axrange.config_exec.render import scheduled_task_start_block

    src = scheduled_task_start_block(
        "axr-persona-test", "powershell.exe", "-File test.ps1",
        label="persona ws'01 (corp-workstation) $HOME `backtick`",
    )
    # A literal `'` in the label must NOT be doubled inside the double-quoted
    # Write-Output lines (over-escaping would print `ws''01`, not `ws'01`).
    assert "ws'01" in src
    assert "ws''01" not in src
    # `$` and backtick must still be escaped so PowerShell doesn't try to
    # interpolate/expand them.
    assert "`$HOME" in src
    assert "``backtick``" in src


# --- ReplayDriver -----------------------------------------------------------


def test_replay_driver_requires_template_and_addressing():
    ga = _FakeGA()
    persona = PERSONAS["historian"]
    driver = ReplayDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(ga, 102, persona, instance_id="hist1-historian")


def test_replay_driver_stages_and_launches(tmp_path):
    pcap = tmp_path / "template.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1fake-pcap-bytes\x00\xff")
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["historian"]
    driver = ReplayDriver()
    result = driver.start(
        ga, 102, persona, instance_id="hist1-historian",
        template_pcap_path=str(pcap), orig_ip="192.0.2.9", node_ip="10.5.0.20",
    )
    assert result["started"] is True
    paths = instance_paths("hist1-historian")
    # the template is staged via write_binary_file (ga.exec + base64-in-
    # command), NOT ga.write_file (which is lossy for binary content) --
    # assert the staging command was issued, not that it landed in `written`.
    assert any(
        "template.pcap" in " ".join(str(a) for a in call) for call in ga.execs
    )
    assert paths["dir"] + "/launch.sh" in ga.written


def test_replay_driver_fails_loud_on_launch_error(tmp_path):
    """Parallel to test_scripted_driver_start_fails_loud_on_launch_error --
    the replay driver shares the SAME systemd_daemon_start_block fail-loud
    contract (LES-086), so a launch script that fails its is-active check
    (modeled here by the `bash <launch.sh>` invocation itself exiting
    non-zero, exactly as the real embedded `|| { ...; exit 1; }` would
    propagate) must raise PersonaDriverError, not swallow it."""
    pcap = tmp_path / "template.pcap"
    pcap.write_bytes(b"\xd4\xc3\xb2\xa1fake-pcap-bytes\x00\xff")

    class _FailingLaunchGA(_FakeGA):
        def exec(self, vmid, argv, *, timeout_s=300):
            if argv[:1] == ["bash"]:
                return GuestExecResult(1, "", "boom: unit did not start")
            return super().exec(vmid, argv, timeout_s=timeout_s)

    ga = _FailingLaunchGA(run_exit=0)
    persona = PERSONAS["historian"]
    driver = ReplayDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(
            ga, 102, persona, instance_id="hist1-historian",
            template_pcap_path=str(pcap), orig_ip="192.0.2.9", node_ip="10.5.0.20",
        )


def test_write_binary_file_true_byte_fidelity():
    """write_binary_file must not lossily UTF-8 round-trip arbitrary bytes
    (the trap GuestAgent.write_file's text-only path has)."""
    raw = bytes(range(256))  # every byte value, incl. invalid UTF-8 sequences
    seen = {}

    class _GA:
        def exec(self, vmid, argv, *, timeout_s=300):
            if argv[:1] == ["chmod"]:
                return GuestExecResult(0, "", "")
            # decode the delivered command to recover the written bytes and
            # prove round-trip fidelity.
            import base64
            import shlex

            cmd = argv[-1]
            tokens = shlex.split(cmd)
            b64 = tokens[tokens.index("printf") + 2]
            decoded = base64.b64decode(b64)
            seen["decoded"] = decoded
            return GuestExecResult(0, "", "")

        def write_file(self, *a, **k):
            raise AssertionError("write_binary_file must not call write_file")

    write_binary_file(_GA(), 1, "/tmp/x.pcap", raw, mode="0644")
    assert seen["decoded"] == raw


# --- RealServiceDriver -------------------------------------------------------
#
# `domain-controller` is the one WINDOWS real-service catalog persona --
# dispatch on `persona.os_family` routes it through `Get-Service`/
# `Get-Process` over `ga.shell_exec` (PowerShell), never a raw `ga.exec`
# `systemctl`/`pgrep` argv. `file-server`/`plc` are LINUX, unaffected by the
# Windows port (byte-identical dispatch: still `ga.exec` + systemctl/pgrep).


def test_real_service_driver_asserts_present():
    """LINUX path (`file-server`, catalog default `smbd`, GAP-032) is
    unchanged: `systemctl is-active` over a raw `ga.exec` argv, never
    `ga.shell_exec`."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["file-server"]
    assert persona.os_family == "linux"
    driver = RealServiceDriver()
    result = driver.start(
        ga, 103, persona, instance_id="fs01-file-server",
        service_name="smbd",
    )
    assert result["asserted"] is True
    assert ga.shell_execs == []


def test_real_service_driver_fails_loud_when_absent():
    ga = _FakeGA(run_exit=1)
    persona = PERSONAS["file-server"]
    driver = RealServiceDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(
            ga, 103, persona, instance_id="fs01-file-server",
            service_name="smbd",
        )


def test_real_service_driver_requires_a_check_target():
    """Every shipped real-service catalog persona now carries a GAP-032
    default binding, so a bare, non-catalog `Persona` (empty `knobs`) is
    what proves the genuinely-unbound case still fails loud."""
    ga = _FakeGA(run_exit=0)
    persona = Persona(id="unbound-rs", os_family="linux", drive="real-service")
    driver = RealServiceDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(ga, 103, persona, instance_id="x01-unbound-rs")


def test_real_service_driver_stop_is_a_pure_noop():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["domain-controller"]
    driver = RealServiceDriver()
    driver.stop(ga, 103, persona, instance_id="dc01-domain-controller")
    # No systemctl stop / Stop-Service / exec calls at all -- stop never
    # touches the daemon this driver didn't start, on either OS.
    assert ga.execs == []
    assert ga.shell_execs == []


def test_real_service_driver_process_match_fallback():
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["plc"]
    driver = RealServiceDriver()
    result = driver.start(
        ga, 104, persona, instance_id="cell1-plc", process_match="axr-plc-sim",
    )
    assert result["asserted"] is True
    assert ga.shell_execs == []


# --- RealServiceDriver Windows port (2026-07-25) ---------------------------


def test_real_service_driver_windows_asserts_via_get_service():
    """`domain-controller` is a WINDOWS real-service persona -- asserted via
    `Get-Service` over `ga.shell_exec`, never a raw `systemctl` argv."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["domain-controller"]
    assert persona.os_family == "windows"
    driver = RealServiceDriver()
    result = driver.start(
        ga, 103, persona, instance_id="dc01-domain-controller",
        service_name="samba-ad-dc",
    )
    assert result["asserted"] is True
    assert ga.execs == []
    assert len(ga.shell_execs) == 1
    assert "Get-Service" in ga.shell_execs[0]
    assert "samba-ad-dc" in ga.shell_execs[0]


def test_real_service_driver_windows_fails_loud_when_absent():
    ga = _FakeGA(run_exit=1)
    persona = PERSONAS["domain-controller"]
    driver = RealServiceDriver()
    with pytest.raises(PersonaDriverError):
        driver.start(
            ga, 103, persona, instance_id="dc01-domain-controller",
            service_name="samba-ad-dc",
        )


def test_real_service_driver_windows_catalog_default():
    """With no explicit override, `domain-controller`'s own catalog
    `knobs.service_name: NTDS` (GAP-032) is what gets asserted."""
    ga = _FakeGA(run_exit=0)
    persona = PERSONAS["domain-controller"]
    driver = RealServiceDriver()
    result = driver.start(ga, 103, persona, instance_id="dc01-domain-controller")
    assert result["asserted"] is True
    assert "NTDS" in ga.shell_execs[0]


def test_real_service_driver_windows_process_match():
    """A Windows real-service assertion via `process_match` uses
    `Get-Process -Name`, not `pgrep -f` -- a bare (non-catalog) Persona so no
    `knobs.service_name` default shadows the `process_match` branch."""
    ga = _FakeGA(run_exit=0)
    persona = Persona(id="unbound-rs", os_family="windows", drive="real-service")
    driver = RealServiceDriver()
    result = driver.start(
        ga, 103, persona, instance_id="dc02-unbound-rs", process_match="lsass",
    )
    assert result["asserted"] is True
    assert ga.execs == []
    assert len(ga.shell_execs) == 1
    assert "Get-Process" in ga.shell_execs[0]
    assert "lsass" in ga.shell_execs[0]


# ===========================================================================
# The high-volume persona wave (2026-07-28): UDP transport, the volume lever,
# `peer_scope: all` fan-out, segment-scoped peer roles, and the `burst:` fix.
# ===========================================================================


def _emit_cfg(src: str) -> list[dict]:
    """The `EMITS = [...]` literal out of a rendered Linux client."""
    ns: dict = {}
    for line in src.splitlines():
        if line.startswith("EMITS = "):
            exec(line, ns)  # noqa: S102 - our own rendered literal
            return ns["EMITS"]
    raise AssertionError("no EMITS line in rendered client")


def _mk(**emit_kw) -> Persona:
    base = dict(proto="https", ports=(443,), peer_role="web-tier")
    base.update(emit_kw)
    return Persona(
        id="t", os_family="linux", drive="scripted",
        emits=(Emit(**base),), requires=("web-tier",),
    )


# --- transport derivation (PSN-100) ---------------------------------------


def test_transport_derives_from_proto_and_can_be_pinned():
    assert Emit(proto="dns").effective_transport() == "udp"
    assert Emit(proto="https").effective_transport() == "tcp"
    # An explicit value always wins over the proto-derived default -- e.g. a
    # DNS zone transfer, which really is TCP.
    assert Emit(proto="dns", transport="tcp").effective_transport() == "tcp"


def test_udp_emit_renders_as_udp_and_tcp_emit_as_tcp():
    src = render_scripted_client(
        _mk(proto="syslog", ports=(514,), peer_role="web-tier"),
        instance_id="h1-t", peers={"web-tier": "10.0.0.9"}, seed=1,
    )
    assert _emit_cfg(src)[0]["transport"] == "udp"
    src = render_scripted_client(
        _mk(), instance_id="h1-t", peers={"web-tier": "10.0.0.9"}, seed=1,
    )
    assert _emit_cfg(src)[0]["transport"] == "tcp"


def test_psn_100_flags_only_an_explicit_bad_transport():
    findings = validate_persona(_mk(transport="sctp"))
    assert [f.rule_id for f in findings] == ["PSN-100"]
    # An UNSET transport is derived, never invalid.
    assert validate_persona(_mk()) == []


# --- the volume lever ------------------------------------------------------


def test_volume_scales_bytes_not_just_message_count():
    """The pre-2026-07-28 model moved message COUNT only; byte size was a
    fixed ~40-byte tag at every level, so no persona could express size."""
    sizes = {}
    for vol in ("low", "med", "high", "bulk"):
        src = render_scripted_client(
            _mk(volume=vol), instance_id="h1-t",
            peers={"web-tier": "10.0.0.9"}, seed=1,
        )
        cfg = _emit_cfg(src)[0]
        sizes[vol] = (cfg["messages"], cfg["bytes"])
    assert [s[0] for s in sizes.values()] == sorted(s[0] for s in sizes.values())
    assert [s[1] for s in sizes.values()] == sorted(s[1] for s in sizes.values())
    # `bulk` is an elephant flow, not a nudge above `high`.
    assert sizes["bulk"][1] >= 16 * sizes["high"][1]


def test_explicit_bytes_overrides_volume_without_changing_message_count():
    """The log-shipper shape: many messages, deliberately small ones."""
    src = render_scripted_client(
        _mk(volume="bulk", bytes_per_msg=512), instance_id="h1-t",
        peers={"web-tier": "10.0.0.9"}, seed=1,
    )
    cfg = _emit_cfg(src)[0]
    assert cfg["bytes"] == 512
    assert cfg["messages"] == 64  # still the bulk message count


def test_psn_070_accepts_bulk_and_loader_rejects_negative_bytes():
    assert validate_persona(_mk(volume="bulk")) == []
    assert [f.rule_id for f in validate_persona(_mk(volume="enormous"))] == ["PSN-070"]
    with pytest.raises(PersonaSchemaError):
        load_persona({"id": "x", "emits": [{"proto": "https", "bytes": -1}]})


# --- the burst: fix --------------------------------------------------------


def test_burst_cadence_fires_every_authored_duration_not_once_a_day():
    """Before 2026-07-28 `burst:` pinned interval_s to 86400 regardless of the
    authored duration, so corp-workstation's `burst:30m` print emit fired
    twice a DAY. Both kinds now mean 'every <duration>'; they differ in
    shape -- burst fires back-to-back (gap 0), cyclic paces at the volume
    gap."""
    def cfg(cadence):
        src = render_scripted_client(
            _mk(cadence=cadence, volume="med"), instance_id="h1-t",
            peers={"web-tier": "10.0.0.9"}, seed=1,
        )
        return _emit_cfg(src)[0]

    burst, cyclic = cfg("burst:30m"), cfg("cyclic:30m")
    assert burst["interval_s"] == 1800.0
    assert burst["interval_s"] == cyclic["interval_s"]
    assert burst["gap_s"] == 0.0
    assert cyclic["gap_s"] > 0.0


# --- segment-scoped peer roles --------------------------------------------


def test_broadcast_and_multicast_roles_resolve_to_wire_addresses():
    src = render_scripted_client(
        PERSONAS["broadcast-chatter"], instance_id="ws01-bc", peers={}, seed=1,
    )
    by_proto = {c["proto"]: c for c in _emit_cfg(src)}
    assert by_proto["llmnr"]["peer_ips"] == ["224.0.0.252"]
    assert by_proto["mdns"]["peer_ips"] == ["224.0.0.251"]
    assert by_proto["nbns"]["peer_ips"] == ["255.255.255.255"]
    # The socket options the guest client needs are flagged per emit.
    assert by_proto["nbns"]["broadcast"] is True
    assert by_proto["mdns"]["multicast"] is True
    # ...and it never needs a twin host, so it binds anywhere.
    assert PERSONAS["broadcast-chatter"].requires == ()


def test_multicast_role_on_a_proto_with_no_group_fails_loud():
    """LES-001: never silently substitute a plausible-looking wrong address."""
    with pytest.raises(PersonaDriverError, match="no well-known multicast group"):
        render_scripted_client(
            _mk(proto="https", peer_role="multicast"),
            instance_id="h1-t", peers={}, seed=1,
        )


# --- peer_scope fan-out (PSN-110) -----------------------------------------


def test_peer_scope_all_fans_out_and_one_does_not():
    peers = {"web-tier": "10.0.0.9"}
    peer_sets = {"web-tier": ["10.0.0.9", "10.0.0.10", "10.0.0.11"]}
    fan = render_scripted_client(
        _mk(peer_scope="all"), instance_id="h1-t", peers=peers,
        peer_sets=peer_sets, seed=1,
    )
    assert _emit_cfg(fan)[0]["peer_ips"] == peer_sets["web-tier"]
    one = render_scripted_client(
        _mk(), instance_id="h1-t", peers=peers, peer_sets=peer_sets, seed=1,
    )
    assert _emit_cfg(one)[0]["peer_ips"] == ["10.0.0.9"]


def test_peer_scope_all_falls_back_to_the_single_peer_without_a_set():
    """A driver called on the pre-2026-07-28 signature (no peer_sets) must
    still drive the emit, not silently emit nothing."""
    src = render_scripted_client(
        _mk(peer_scope="all"), instance_id="h1-t",
        peers={"web-tier": "10.0.0.9"}, seed=1,
    )
    assert _emit_cfg(src)[0]["peer_ips"] == ["10.0.0.9"]


def test_psn_110_flags_a_bad_peer_scope():
    assert [f.rule_id for f in validate_persona(_mk(peer_scope="some"))] == ["PSN-110"]


# --- the eight new catalog personas ---------------------------------------


def test_high_volume_wave_personas_are_registered_and_valid():
    added = [
        "broadcast-chatter", "ci-build-farm", "db-replica", "log-shipper",
        "p2p-update-peer", "scanner-benign", "streaming-media-client",
        "voip-softphone",
    ]
    for pid in added:
        assert pid in PERSONAS, pid
        assert validate_persona(PERSONAS[pid]) == [], pid


def test_every_new_scripted_persona_renders_a_compiling_client():
    peers = {
        "egress-proxy": "10.0.0.2", "file-server": "10.0.0.6",
        "monitoring-collector": "10.0.0.7", "corp-workstation": "10.0.0.8",
        "bastion-jump": "10.0.0.3", "db-tier": "10.0.0.4",
        "voip-softphone": "10.0.0.20", "p2p-update-peer": "10.0.0.21",
    }
    for pid, p in PERSONAS.items():
        if p.drive != "scripted":
            continue
        render = (
            render_scripted_client if p.os_family == "linux"
            else render_scripted_client_windows
        )
        src = render(p, instance_id=f"h1-{pid}", peers=peers, seed=5)
        if p.os_family == "linux":
            compile(src, f"<{pid}>", "exec")
        assert "axrange-persona" in src


# ===========================================================================
# The `command` driver (2026-09-02, docs/WORK_ORDER_protocol_faithful_traffic.md)
#
# A fourth drive kind that runs a REAL protocol client tool at each emit's
# firing instant instead of pushing filler bytes at the right port. The tests
# below cover the work order's §8 validation list: the PSN-030 widening,
# dispatch, render determinism, the CONTAINMENT safety property, the
# volume->repetition mapping, both fail-loud paths (unmapped proto, missing
# tool), the Linux-only refusal, the ignored-`bytes:` announcement, the
# `scripted` byte-identity regression pin, and one FUNCTIONAL wire test.
# ===========================================================================

_CMD_PEER = "203.0.113.7"


def _cmd_persona(*emits, persona_id="probe-client", os_family="linux", knobs=None):
    """A synthetic `drive: command` persona. No SHIPPED catalog persona uses
    this drive kind yet (deliberate -- work order §5.2: a shipped one would be
    assignable onto guests with no `dig`/`curl` baked in), so every test here
    builds its own, exactly as the PSN-0xx tests already build deliberately-bad
    personas."""
    return Persona(
        id=persona_id, os_family=os_family, drive="command", emits=tuple(emits),
        requires=tuple(sorted({e.peer_role for e in emits if e.peer_role})),
        knobs=dict(knobs or {}),
    )


def _emits_of(rendered_src):
    """The `EMITS = [...]` literal out of a rendered client -- the same
    'read the baked config back' technique the scripted-driver tests use."""
    import ast

    for line in rendered_src.splitlines():
        if line.startswith("EMITS = "):
            return ast.literal_eval(line[len("EMITS = "):])
    raise AssertionError("rendered client has no EMITS literal")


def _replaced_template(cmd_mod, proto, **changes):
    import dataclasses

    return dataclasses.replace(cmd_mod._COMMAND_TEMPLATES[proto], **changes)


# --- PSN-030: the enum widening (additive; rule COUNT unchanged) -----------


def test_psn_030_accepts_command_and_all_three_pre_existing_kinds():
    """The version-locked widening: `command` joins the enum, the original
    three still pass, and an unknown kind still fires."""
    for drive in ("scripted", "replay", "real-service", "command"):
        p = Persona(id="x", os_family="linux", drive=drive)
        assert "PSN-030" not in {f.rule_id for f in validate_persona(p)}, drive
    bad = Persona(id="x", os_family="linux", drive="teleport")
    assert "PSN-030" in {f.rule_id for f in validate_persona(bad)}


def test_psn_030_rule_text_echoes_every_drive_kind():
    """`catalog.py`'s PSN-030 title/message hardcode the kind names in prose
    while `checks/personas.py` reads `DRIVE_KINDS` -- they must stay in step,
    or the finding an operator reads names a stale enum."""
    from axrange.personas.schema import DRIVE_KINDS

    rule = get_rule("PSN-030")
    for kind in DRIVE_KINDS:
        assert kind in rule.message_template, kind


def test_psn_rule_count_is_unchanged_by_the_command_drive_kind():
    """Widening a closed enum inside a version-locked spec must NOT move the
    rule count (the PSN-100/PSN-110 precedent, CHANGELOG 2026-07-28): 11 PSN
    rules, 114 total. `catalog.py`'s own asserts pin this at import; this test
    makes the intent explicit and greppable."""
    psn = [r for r in list_rules() if r.category == RuleCategory.PERSONA]
    assert len(psn) == 11
    assert len(list_rules()) == 114


# --- render: determinism, compilability, one template per proto ------------


def test_command_render_is_byte_stable_given_seed():
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="dns-resolver"))
    peers = {"dns-resolver": _CMD_PEER}
    a = render_command_client(p, instance_id="ws01-probe", peers=peers, seed=7)
    b = render_command_client(p, instance_id="ws01-probe", peers=peers, seed=7)
    assert a == b


def test_command_render_differs_across_seed_and_instance():
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="dns-resolver"))
    peers = {"dns-resolver": _CMD_PEER}
    a = render_command_client(p, instance_id="ws01-probe", peers=peers, seed=1)
    b = render_command_client(p, instance_id="ws01-probe", peers=peers, seed=2)
    c = render_command_client(p, instance_id="ws02-probe", peers=peers, seed=1)
    assert a != b
    assert a != c


def test_command_render_compiles_and_never_goes_through_a_shell():
    """The client runs an argv LIST via `subprocess.run`, never a shell
    string -- so no in-guest quoting/injection surface exists at all."""
    p = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="dns-resolver"),
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
    )
    src = render_command_client(
        p, instance_id="ws01-probe",
        peers={"dns-resolver": _CMD_PEER, "web-tier": "203.0.113.8"}, seed=3,
    )
    assert src.startswith("#!/usr/bin/env python3")
    compile(src, "<command-persona-client>", "exec")
    assert "shell=True" not in src
    assert "os.system" not in src


def test_every_supported_proto_has_a_renderable_template():
    """One template per supported proto, each producing a real client argv
    whose FIRST token is the tool it declares."""
    # 2026-09-21 (E5): the original five plus the seven real clients.
    assert supported_protos() == (
        "dns", "http", "https", "icmp", "kerberos", "ldap", "ntp",
        "rdp", "rpc", "smb", "snmp", "ssh",
    )
    for proto in supported_protos():
        p = _cmd_persona(Emit(proto=proto, peer_role="sink"))
        src = render_command_client(
            p, instance_id=f"h1-{proto}", peers={"sink": _CMD_PEER}, seed=1,
        )
        (emit,) = _emits_of(src)
        assert emit["proto"] == proto
        argv = emit["argv"][0][0]
        # argv[0] is the declared tool -- EXCEPT on a row that declares a
        # credential helper wrapping it (`ssh` -> `sshpass -f <path> ssh …`,
        # 2026-09-21). Then the helper runs first and the tool follows it in
        # the same argv; both are still probed for at start
        # (`credential_binaries`).
        wrappers = [b for b, _ in _COMMAND_TEMPLATES[proto].credential_binaries]
        assert argv[0] == emit["binary"] or (
            argv[0] in wrappers and emit["binary"] in argv
        ), proto
        assert emit["package"], proto


# --- CONTAINMENT: the guest must never resolve a name ----------------------


def _dotted_non_ip_tokens(argv):
    """Every argv token that looks like a DNS name rather than an IP literal
    -- re-derived here independently of the driver's own check, so this test
    fails if `_check_containment` is ever weakened."""
    import ipaddress
    import re as _re
    from urllib.parse import urlsplit

    name_re = _re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+\.?$")
    out = set()
    for tok in argv:
        cand = tok
        if "://" in tok:
            cand = urlsplit(tok).hostname or ""
        elif tok.startswith("@"):
            cand = tok[1:]
        if not cand:
            continue
        try:
            ipaddress.ip_address(cand)
            continue
        except ValueError:
            pass
        if name_re.match(cand):
            out.add(cand)
    return out


def test_every_shipped_template_addresses_its_peer_by_baked_ip_by_default():
    """The orchestrator still resolves every peer to a twin host IP and still
    bakes it in -- what the 2026-09-21 reversal changed is that a NAME is no
    longer REFUSED, not that names became the default. So with no
    `knobs.target_name`, every template that CAN address an IP still does,
    and the only name-shaped token anywhere is dig's QUERY NAME -- payload
    sent TO a server at a baked address, not an address.

    `kerberos` is the one exception and is excluded deliberately: `kinit`
    addresses a REALM (a ticket is issued against an SPN, never an IP), which
    is exactly why the reversal was needed. `_COMMAND_TEMPLATES` declares that
    as `addresses_by_name=True`, so this test reads the declaration rather
    than hard-coding the exception list."""
    for proto in supported_protos():
        tpl = _COMMAND_TEMPLATES[proto]
        if tpl.addresses_by_name:
            continue
        p = _cmd_persona(Emit(proto=proto, peer_role="sink"))
        src = render_command_client(
            p, instance_id=f"h1-{proto}", peers={"sink": _CMD_PEER}, seed=1,
        )
        (emit,) = _emits_of(src)
        argv = emit["argv"][0][0]
        assert any(_CMD_PEER in tok for tok in argv), (proto, argv)
        names = _dotted_non_ip_tokens(argv)
        # PAYLOAD that merely LOOKS name-shaped is not an address and is
        # deliberately not treated as one: dig's query name, and snmpget's
        # numeric OID (dotted, but never resolved by anything).
        expected = {
            "dns": {_DEFAULT_DNS_QNAME},
            "snmp": {"1.3.6.1.2.1.1.1.0"},
        }.get(proto, set())
        assert names == expected, (proto, argv, names)
        if emit["binary"] == "curl":
            # MUST-FIX 1: `-q` disables `~/.curlrc` and must be FIRST (curl
            # only honours it there); `--noproxy *` blocks ambient
            # http_proxy/https_proxy/ALL_PROXY from sending the request to
            # an uncontained proxy host that never appears in argv at all
            # (so `_check_containment` cannot see it); `-L` (follow
            # redirects) is never passed -- a redirect target is exactly
            # the kind of guest/server-chosen, unbaked address containment
            # exists to prevent.
            assert argv[1] == "-q", (proto, argv)
            assert "--noproxy" in argv, (proto, argv)
            assert argv[argv.index("--noproxy") + 1] == "*", (proto, argv)
            assert "-L" not in argv, (proto, argv)


def test_a_name_addressed_template_renders_by_default_and_refuses_in_strict_mode(
    monkeypatch,
):
    """REVERSED 2026-09-21 (`docs/WORK_ORDER_protocol_engines.md` ruling 1,
    Authority: user). `curl https://fileserver/` used to fail loud at render;
    it now RENDERS, because containment became a network property (twin-only
    DNS + any-IP sink + router egress denial) rather than an argv check. The
    pre-reversal rule stays reachable and is pinned here as the strict-mode
    half -- this test is the reversal's regression pin in both directions."""
    from axrange.personas.drivers import command as cmd_mod

    def bad_build(peer, port, ctx):
        url = "https://fileserver/"
        return ["curl", "-sS", url], (url,)

    broken = _replaced_template(cmd_mod, "https", build=bad_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))

    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "https://fileserver/" in _emits_of(src)[0]["argv"][0][0]

    with pytest.raises(PersonaDriverError) as e:
        render_command_client(
            p, instance_id="h1", peers={"sink": _CMD_PEER},
            require_ip_literals=True,
        )
    assert "fileserver" in str(e.value)
    assert "STRICT containment" in str(e.value)


def test_containment_accepts_a_name_explicitly_bound_with_curl_resolve(monkeypatch):
    """The documented escape hatch: a hostname-bearing URL is allowed only
    when the SAME argv pins it to the baked IP with `--resolve`."""
    from axrange.personas.drivers import command as cmd_mod

    def bound_build(peer, port, ctx):
        url = "https://fileserver/"
        return (
            ["curl", "-sS", "--resolve", f"fileserver:{port}:{peer}", url],
            (url,),
        )

    ok = _replaced_template(cmd_mod, "https", build=bound_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", ok)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    argv = _emits_of(src)[0]["argv"][0][0]
    assert f"fileserver:443:{_CMD_PEER}" in argv


def test_containment_refuses_resolve_pinned_to_a_non_ip_address(monkeypatch):
    """SHOULD-FIX 2: `--resolve host:port:address` only counts as a binding
    when ADDRESS is itself an IP literal -- pinning a name to ANOTHER name
    just moves the resolution problem one hop. The old `_bound_hosts` only
    checked the flag was PRESENT and blessed this."""
    from axrange.personas.drivers import command as cmd_mod

    def bad_bound_build(peer, port, ctx):
        url = "https://fileserver/"
        return (
            ["curl", "-sS", "--resolve", "fileserver:443:not-an-ip", url],
            (url,),
        )

    broken = _replaced_template(cmd_mod, "https", build=bad_bound_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    # STRICT mode only (2026-09-21): under the default model a name needs no
    # binding at all, so a half-useless binding is not a refusal -- it is
    # simply not load-bearing any more.
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(
            p, instance_id="h1", peers={"sink": _CMD_PEER},
            require_ip_literals=True,
        )
    assert "fileserver" in str(e.value)


def test_containment_refuses_connect_to_pinned_to_a_non_ip_host(monkeypatch):
    """Same fix, the `--connect-to h1:p1:h2:p2` form: h2 must be a literal
    too, or the flag just renames which hostname the guest would resolve."""
    from axrange.personas.drivers import command as cmd_mod

    def bad_connect_build(peer, port, ctx):
        url = "https://fileserver/"
        return (
            ["curl", "-sS", "--connect-to", "fileserver:443:another-name:443", url],
            (url,),
        )

    broken = _replaced_template(cmd_mod, "https", build=bad_connect_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:  # strict mode only, see above
        render_command_client(
            p, instance_id="h1", peers={"sink": _CMD_PEER},
            require_ip_literals=True,
        )
    assert "fileserver" in str(e.value)


def test_containment_refuses_an_undeclared_second_endpoint(monkeypatch):
    """A template cannot smuggle a second destination past the address check
    by simply not declaring it."""
    from axrange.personas.drivers import command as cmd_mod

    def sneaky_build(peer, port, ctx):
        url = f"https://{peer}:{port}/"
        return ["curl", "-sS", url, "http://evil.example/"], (url,)

    broken = _replaced_template(cmd_mod, "https", build=sneaky_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "undeclared endpoint" in str(e.value)


def test_containment_refuses_a_template_that_declares_zero_addresses(monkeypatch):
    """MUST-FIX 3: a template that talks to a peer but declares NO addresses
    makes every containment check vacuously pass (nothing to iterate) --
    refuse it outright rather than let that be a silent bypass."""
    from axrange.personas.drivers import command as cmd_mod

    def no_addresses_build(peer, port, ctx):
        return ["curl", "-sS", f"https://{peer}:{port}/"], ()

    broken = _replaced_template(cmd_mod, "https", build=no_addresses_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "zero addresses" in str(e.value)


def test_build_emit_invocations_refuses_a_template_that_addresses_a_different_peer(
    monkeypatch,
):
    """MUST-FIX 3's second check: every address a template declares must
    individually be a well-formed IP literal (`_check_containment`'s job)
    AND resolve to the SAME peer this invocation was asked to dial. A
    template whose `build()` drifts from its own `peer` argument (a future
    copy-paste bug, hardcoded IP, ...) would sail through `_check_containment`
    unchanged -- every address it declares is still individually a real IP
    literal -- so this is a SEPARATE check, in `build_emit_invocations`
    itself."""
    from axrange.personas.drivers import command as cmd_mod

    other_ip = "203.0.113.99"
    assert other_ip != _CMD_PEER

    def wrong_peer_build(peer, port, ctx):
        url = f"https://{other_ip}:{port}/"
        return ["curl", "-sS", url], (url,)

    broken = _replaced_template(cmd_mod, "https", build=wrong_peer_build)
    monkeypatch.setitem(cmd_mod._COMMAND_TEMPLATES, "https", broken)
    p = _cmd_persona(Emit(proto="https", ports=(443,), peer_role="sink"))
    with pytest.raises(AssertionError):
        render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})


# --- volume -> repetition, and the ignored-`bytes:` announcement -----------


def test_command_volume_maps_to_invocation_count_not_byte_size():
    """Work order §3.3, pinned explicitly: `messages` becomes the invocation
    count and `gap_s` the inter-invocation sleep -- the SAME numbers the
    scripted driver's volume profile resolves, reused rather than
    re-tabulated."""
    seen = {}
    for volume in ("low", "med", "high", "bulk"):
        p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink", volume=volume))
        src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
        (emit,) = _emits_of(src)
        seen[volume] = emit["messages"]
        # `bytes` is deliberately NOT threaded into a command emit's config:
        # it is either already spent inside the argv or ignored-and-announced.
        assert "bytes" not in emit
    assert seen == {"low": 2, "med": 8, "high": 24, "bulk": 64}


def test_command_ignored_bytes_is_announced_once_not_silently_dropped():
    """An ignored knob must be VISIBLE (the `fails=` precedent) -- the
    rendered client logs it once at arm time."""
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink", volume="high"))
    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    (emit,) = _emits_of(src)
    assert "ignored" in emit["bytes_note"]
    assert "65536" in emit["bytes_note"]
    assert "dig" in emit["bytes_note"]
    # ...and the client actually emits it.
    assert "bytes_note" in src
    assert "log('note '" in src


def test_command_icmp_spends_bytes_and_announces_the_clamp():
    """`ping -s` is the one template that CAN spend a byte budget; a budget
    above ICMP's payload ceiling is clamped and the clamp is announced."""
    small = _cmd_persona(Emit(proto="icmp", peer_role="sink", volume="low"))
    src = render_command_client(small, instance_id="h1", peers={"sink": _CMD_PEER})
    (emit,) = _emits_of(src)
    argv = emit["argv"][0][0]
    assert argv[argv.index("-s") + 1] == "256"
    assert emit["bytes_note"] == ""  # spent in full, nothing to announce

    big = _cmd_persona(Emit(proto="icmp", peer_role="sink", volume="bulk"))
    src = render_command_client(big, instance_id="h1", peers={"sink": _CMD_PEER})
    (emit,) = _emits_of(src)
    argv = emit["argv"][0][0]
    assert argv[argv.index("-s") + 1] == str(_PING_MAX_PAYLOAD)
    assert "clamped" in emit["bytes_note"]


def test_command_explicit_bytes_override_reaches_the_ping_payload():
    p = _cmd_persona(
        Emit(proto="icmp", peer_role="sink", volume="low", bytes_per_msg=1400)
    )
    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    argv = _emits_of(src)[0]["argv"][0][0]
    assert argv[argv.index("-s") + 1] == "1400"


# --- fail-loud paths -------------------------------------------------------


def test_command_unmapped_proto_fails_loud_and_names_the_extension_point():
    """LES-001: never silently substitute a plausible-looking wrong client
    (the same posture an unmapped multicast proto already has)."""
    p = _cmd_persona(Emit(proto="modbus", ports=(502,), peer_role="plc"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(p, instance_id="h1", peers={"plc": _CMD_PEER})
    msg = str(e.value)
    assert "modbus" in msg
    assert "_COMMAND_TEMPLATES" in msg
    assert "drive: scripted" in msg


def test_command_segment_scoped_peer_roles_fail_loud():
    for role in ("broadcast", "multicast"):
        p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role=role))
        with pytest.raises(PersonaDriverError) as e:
            render_command_client(p, instance_id="h1", peers={})
        assert role in str(e.value)


def test_command_port_a_tool_cannot_dial_fails_loud():
    """`ntpdate`/`ping` have no port flag; an emit pinning a different port is
    refused rather than silently driving the well-known one."""
    p = _cmd_persona(Emit(proto="ntp", ports=(1123,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "1123" in str(e.value)
    assert "no port flag" in str(e.value)
    # The well-known port, and the schema's "no port authored" placeholder,
    # both render fine.
    for ports in ((), (123,)):
        ok = _cmd_persona(Emit(proto="ntp", ports=ports, peer_role="sink"))
        render_command_client(ok, instance_id="h1", peers={"sink": _CMD_PEER})


def test_command_port_zero_placeholder_falls_back_to_the_well_known_port():
    p = _cmd_persona(Emit(proto="https", peer_role="sink"))  # no ports authored
    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    argv = _emits_of(src)[0]["argv"][0][0]
    assert f"https://{_CMD_PEER}:443/" in argv


def test_command_explicit_transport_tcp_forces_dig_plus_tcp():
    """Code review item 5: an EXPLICITLY-authored `transport: tcp` on a
    `dns` emit adds `+tcp` to the dig invocation (dig's own unforced
    default is already udp, matching `default_transport("dns")`, so there
    is nothing to force for the common case)."""
    tcp = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink", transport="tcp")
    )
    src = render_command_client(tcp, instance_id="h1", peers={"sink": _CMD_PEER})
    argv = _emits_of(src)[0]["argv"][0][0]
    assert "+tcp" in argv

    # An EXPLICIT `transport: udp` is also honourable (it's in dig's
    # `explicit_transports`) but is a no-op on the wire -- udp is already
    # dig's unforced default.
    udp = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink", transport="udp")
    )
    src = render_command_client(udp, instance_id="h1", peers={"sink": _CMD_PEER})
    argv = _emits_of(src)[0]["argv"][0][0]
    assert "+tcp" not in argv


def test_command_derived_transport_default_is_never_forced_onto_dig():
    """"Derived defaults stay ignored" (code review item 5): an emit that
    leaves `transport:` UNSET derives udp for `dns`
    (`default_transport("dns")`) via `Emit.effective_transport()`, but
    `build_emit_invocations` must never consult that DERIVED value -- only
    an author who TYPED `transport:` can reach the explicit-transport check
    at all. Pinned by asserting "unset" and "explicitly udp" render
    byte-identically."""
    unset = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    explicit_udp = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink", transport="udp")
    )
    a = render_command_client(unset, instance_id="h1", peers={"sink": _CMD_PEER})
    b = render_command_client(
        explicit_udp, instance_id="h1", peers={"sink": _CMD_PEER}
    )
    assert a == b
    assert "+tcp" not in _emits_of(a)[0]["argv"][0][0]


def test_command_explicit_transport_a_template_cannot_honour_fails_loud():
    """The other half of item 5: an EXPLICIT transport a template's tool
    genuinely cannot drive is a loud authoring error, not a silently
    ignored knob -- `ntpdate` is udp-only, so `transport: tcp` on an `ntp`
    emit must refuse (the work order's own worked example)."""
    p = _cmd_persona(Emit(proto="ntp", peer_role="sink", transport="tcp"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    msg = str(e.value)
    assert "ntp" in msg
    assert "tcp" in msg
    assert "ntpdate" in msg

    # `ping` can honour NEITHER explicit transport -- ICMP is its own IP
    # protocol, not tcp or udp.
    for transport in ("tcp", "udp"):
        bad = _cmd_persona(Emit(proto="icmp", peer_role="sink", transport=transport))
        with pytest.raises(PersonaDriverError):
            render_command_client(bad, instance_id="h1", peers={"sink": _CMD_PEER})


def test_command_dns_qname_knob_overrides_the_default_and_rejects_garbage():
    p = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink"),
        knobs={"dns_qname": "intranet.corp.example."},
    )
    src = render_command_client(p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "intranet.corp.example." in _emits_of(src)[0]["argv"][0][0]

    bad = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink"),
        knobs={"dns_qname": "not a; name"},
    )
    with pytest.raises(PersonaDriverError) as e:
        render_command_client(bad, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "dns_qname" in str(e.value)


def test_command_unresolved_peer_role_is_a_skip_not_a_failure():
    """Matches the scripted driver's graceful behavior: an unresolved
    peer_role renders an empty invocation set and the guest logs a skip."""
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="dns-resolver"))
    src = render_command_client(p, instance_id="h1", peers={})
    assert _emits_of(src)[0]["argv"] == []
    assert "not resolved" in src


def test_command_peer_scope_all_fans_out_one_invocation_set_per_peer():
    p = _cmd_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier", peer_scope="all")
    )
    src = render_command_client(
        p, instance_id="h1", peers={"web-tier": "10.0.0.9"},
        peer_sets={"web-tier": ["10.0.0.9", "10.0.0.10"]},
    )
    (emit,) = _emits_of(src)
    assert len(emit["argv"]) == 2
    assert "https://10.0.0.9:443/" in emit["argv"][0][0]
    assert "https://10.0.0.10:443/" in emit["argv"][1][0]


# --- launch + driver lifecycle ---------------------------------------------


def test_command_launch_uses_systemd_run_not_nohup():
    """Regression guard (LES-086/GAP-020/GAP-025 class), same as scripted's."""
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    paths = instance_paths("h1-probe-client")
    launch = render_command_launch("h1-probe-client", p, script_path=paths["script"])
    assert "nohup" not in launch
    assert f"systemd-run --unit={paths['unit']} --collect bash -c" in launch
    assert f"systemctl is-active --quiet {paths['unit']} ||" in launch
    if shutil.which("bash"):
        res = subprocess.run(["bash", "-n"], input=launch, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr


def test_required_binaries_dedupes_and_names_debian_packages():
    p = _cmd_persona(
        Emit(proto="http", ports=(80,), peer_role="a"),
        Emit(proto="https", ports=(443,), peer_role="b"),
        Emit(proto="dns", ports=(53,), peer_role="c"),
    )
    assert required_binaries(p) == (("curl", "curl"), ("dig", "dnsutils"))


def test_tool_probe_prints_only_missing_binaries():
    """The probe's shape is pinned: it reports absence on STDOUT (one
    `MISSING:`-tagged line per missing binary, code review item 6) and
    always exits 0, so a real shell run of it is unambiguous. Run against a
    REAL shell, not just asserted as a string."""
    probe = render_tool_probe(["bash", "definitely-not-a-real-binary-xyz"])
    assert _TOOL_PROBE_MARKER in probe
    if shutil.which("sh"):
        res = subprocess.run(["sh", "-lc", probe], capture_output=True, text=True)
        assert res.returncode == 0
        assert res.stdout.split() == [f"{_MISSING_TAG}definitely-not-a-real-binary-xyz"]


def test_tool_probe_ignores_a_login_shells_profile_banner():
    """Code review item 6: `_assert_tools_present` runs its probe under
    `sh -lc` -- a LOGIN shell, which sources `/etc/profile` and friends
    before the probe body executes. A real guest image's login banner lands
    on the SAME stdout stream; un-tagged, the old behaviour (every non-empty
    line is a missing tool) would misread it as one. `MISSING:`-prefix
    filtering must ignore it and still find the real missing tool."""
    ga = _FakeGA(
        run_exit=0, missing_tools=["dig"],
        profile_banner="Welcome to the axRange lab image\n",
    )
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(ga, 101, p, instance_id="h1", peers={"sink": _CMD_PEER})
    msg = str(e.value)
    assert "dig" in msg
    assert "Welcome to the axRange lab image" not in msg


def test_command_driver_start_writes_probes_and_launches():
    ga = _FakeGA(run_exit=0)
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    result = CommandDriver().start(
        ga, 101, p, instance_id="h1-probe-client",
        peers={"sink": _CMD_PEER}, seed=5,
    )
    assert result["started"] is True
    paths = instance_paths("h1-probe-client")
    assert paths["script"] in ga.written
    assert "dig" in ga.written[paths["script"]]
    assert paths["dir"] + "/launch.sh" in ga.written
    assert "nohup" not in ga.written[paths["dir"] + "/launch.sh"]
    # The protocol-client probe really ran, before the launch.
    joined = [" ".join(str(a) for a in argv) for argv in ga.execs]
    probe_idx = next(i for i, j in enumerate(joined) if _TOOL_PROBE_MARKER in j)
    launch_idx = next(i for i, j in enumerate(joined) if "launch.sh" in j)
    assert probe_idx < launch_idx


def test_command_driver_start_fails_loud_on_a_missing_protocol_client():
    """§3.4: never start a client that would log `command not found` on every
    fire for the life of the twin."""
    ga = _FakeGA(run_exit=0, missing_tools=["dig"])
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(ga, 101, p, instance_id="h1", peers={"sink": _CMD_PEER})
    msg = str(e.value)
    assert "dig" in msg
    assert "dnsutils" in msg
    assert "IMAGE_BUILD" in msg
    # ...and it stopped BEFORE launching anything.
    assert not any("launch.sh" in " ".join(str(a) for a in argv) for argv in ga.execs)


def test_command_driver_start_fails_loud_on_launch_error():
    ga = _FakeGA(run_exit=1)
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(ga, 101, p, instance_id="h1", peers={"sink": _CMD_PEER})
    assert "launch failed" in str(e.value)


def test_command_driver_stop_and_heartbeat_reuse_the_shared_helpers():
    ga = _FakeGA(run_exit=0)
    p = _cmd_persona(Emit(proto="dns", ports=(53,), peer_role="sink"))
    r = CommandDriver().stop(ga, 101, p, instance_id="h1")
    assert r["stopped"] is True
    assert r["unit"] == instance_paths("h1")["unit"]
    hb = CommandDriver().heartbeat(ga, 101, p, instance_id="h1")
    assert hb["alive"] is True


def test_command_driver_dispatches_windows_to_its_own_table_not_the_linux_one():
    """**Superseded, deliberately** (2026-09-21,
    docs/WORK_ORDER_protocol_engines.md §5 E6). This used to assert that a
    Windows `drive: command` persona was a LOUD refusal, because the Windows
    per-proto table did not exist. It does now
    (`personas.drivers.command_windows`), so the property worth pinning moved:
    a Windows persona must reach the WINDOWS table — never the Linux one, and
    never a silent no-op. The Linux table's own protos are not assumed to
    exist there: `dns` does, but as `Resolve-DnsName`, not `dig`.

    (The full Windows-engine suite is at the end of this file; this test
    stands guard on the DISPATCH, which lives in `command.py`.)"""
    ga = _FakeGA(run_exit=0)
    win = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink"), os_family="windows",
        knobs={"ad_domain": "corp.example", "dc_name": "dc01.corp.example"},
    )
    result = CommandDriver().start(
        ga, 101, win, instance_id="h1", peers={"sink": _CMD_PEER}
    )
    assert result["started"] is True
    written = ga.written[instance_paths("h1", os_family="windows")["script"]]
    assert "Resolve-DnsName" in written and "dig" not in written
    # The Linux client was not rendered for it.
    assert instance_paths("h1")["script"] not in ga.written


def test_command_driver_unknown_os_family_still_raises_the_shared_error():
    ga = _FakeGA(run_exit=0)
    weird = _cmd_persona(
        Emit(proto="dns", ports=(53,), peer_role="sink"), os_family="plan9"
    )
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(ga, 101, weird, instance_id="h1", peers={})
    assert "unsupported os_family" in str(e.value)


def test_no_shipped_persona_uses_the_command_drive_on_either_os():
    """Content guard, widened with E6 (2026-09-21): this used to forbid only
    `command` + `windows`, because Windows had no table. Now that BOTH OSes
    have one, the guard that still earns its keep is the `browser` driver's —
    no shipped catalog persona uses `drive: command` at all yet, because it
    would be assignable onto guests with neither the Linux tools baked in nor
    a domain join (GAP-051: joins are slow and effectively serialised)."""
    assert [pid for pid, p in PERSONAS.items() if p.drive == "command"] == []


# --- regression pin: the `scripted` driver is byte-identical ---------------

# Recomputed deliberately ONLY when the scripted render is intentionally
# changed (see the test below).
_SCRIPTED_RENDER_SHA256 = "926f4dfeaccf47dce678aed1327306a97ae3ebc848caa474578d78145ede063c"


def test_scripted_render_is_byte_identical_after_the_command_driver_landed():
    """Work order §8.6: this change ADDS a branch, it must not touch the
    first. Pinned as a content hash of a canonical render so a future edit to
    the scripted template is a deliberate, visible act rather than collateral
    damage from work on its sibling."""
    import hashlib

    p = PERSONAS["corp-workstation"]
    peers = {
        "domain-controller": "10.0.0.5", "file-server": "10.0.0.6",
        "dns-resolver": "10.0.0.7", "print-server": "10.0.0.8",
        "mail-server": "10.0.0.9", "egress-proxy": "10.0.0.2",
    }
    src = render_scripted_client(
        p, instance_id="ws01-corp-workstation", peers=peers, seed=11
    )
    digest = hashlib.sha256(src.encode()).hexdigest()
    assert digest == _SCRIPTED_RENDER_SHA256, (
        "the scripted driver's render changed -- the `command` driver must add "
        "a branch, never touch this one (work order §8.6). If the change is "
        "deliberate, update the pin and say so in the commit."
    )


# --- FUNCTIONAL wire test ---------------------------------------------------


class _StubDNS:
    """A ~30-line UDP DNS responder that answers any A query with a fixed
    address. Enough for `dig` to exit 0 with a real ANSWER section, which is
    the whole point: it proves the rendered argv puts a genuine DNS query on
    the wire, not filler bytes at port 53."""

    def __init__(self):
        import socket
        import threading

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.queries: list[bytes] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)

    @staticmethod
    def _qname_end(msg, off=12):
        i = off
        while msg[i]:
            i += 1 + msg[i]
        return i + 1

    @classmethod
    def qname_of(cls, msg):
        labels = []
        i = 12
        while msg[i]:
            n = msg[i]
            labels.append(msg[i + 1: i + 1 + n].decode())
            i += 1 + n
        return ".".join(labels) + "."

    def _answer(self, q):
        qend = self._qname_end(q) + 4
        header = (
            q[0:2] + b"\x81\x80" + b"\x00\x01" + b"\x00\x01"
            + b"\x00\x00" + b"\x00\x00"
        )
        rr = (
            b"\xc0\x0c" + b"\x00\x01" + b"\x00\x01" + b"\x00\x00\x00\x3c"
            + b"\x00\x04" + bytes([192, 0, 2, 10])
        )
        return header + q[12:qend] + rr

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                continue
            self.queries.append(data)
            try:
                self.sock.sendto(self._answer(data), addr)
            except OSError:
                pass

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=3)
        self.sock.close()


def _http_sink():
    """A localhost HTTP sink recording every request line."""
    import http.server
    import threading

    seen: list[str] = []

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):  # noqa: N802
            seen.append(self.requestline)
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # silence
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1], seen


def _ping_works() -> bool:
    """`ping` binary PRESENCE is not enough (code review nit): in a
    sandboxed/containerized test host `ping` can be installed but lack
    CAP_NET_RAW, so `-c 1 127.0.0.1` fails with a permission error even
    though `shutil.which("ping")` finds it -- which would make the icmp leg
    of the functional wire test spuriously report `fails=1`/`ok=1` instead
    of being cleanly skipped. Actually RUN it once against loopback rather
    than trusting presence alone."""
    if not shutil.which("ping"):
        return False
    try:
        res = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "2", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


@pytest.mark.skipif(
    not (shutil.which("dig") and shutil.which("curl")),
    reason="the functional wire test needs real dig + curl on the test host",
)
def test_command_client_functionally_speaks_real_protocols_on_the_wire(
    tmp_path, monkeypatch
):
    """FUNCTIONAL (not a render assertion): render a `command` client, RUN it
    against local listeners, and assert BOTH the guest-log counters AND what
    actually arrived at the far side.

    This is the test that would catch the very defect the work order exists to
    fix -- a client that opens the right port but speaks the wrong protocol
    passes every render assertion and fails here, because the DNS sink parses
    the bytes as a query and the HTTP sink records a request line."""
    import sys
    import time

    log_path = tmp_path / "persona.log"
    script_path = tmp_path / "client.py"

    def _paths(instance_id, *, os_family="linux"):
        return {
            "dir": str(tmp_path), "script": str(script_path),
            "log": str(log_path), "unit": "axr-persona-test",
        }

    monkeypatch.setattr("axrange.personas.drivers.command.instance_paths", _paths)

    srv, http_port, http_seen = _http_sink()
    try:
        with _StubDNS() as dns:
            emits = [
                Emit(proto="dns", ports=(dns.port,), peer_role="sink", volume="low"),
                Emit(proto="http", ports=(http_port,), peer_role="sink", volume="low"),
            ]
            expect = {"dns", "http"}
            if _ping_works():
                emits.append(Emit(proto="icmp", peer_role="sink", volume="low"))
                expect.add("icmp")
            p = _cmd_persona(*emits)
            src = render_command_client(
                p, instance_id="h1-probe-client",
                peers={"sink": "127.0.0.1"}, seed=17,
            )
            script_path.write_text(src)

            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.time() + 60
                summaries: dict[str, str] = {}
                while time.time() < deadline:
                    if log_path.exists():
                        for line in log_path.read_text().splitlines():
                            if " ran " in line:
                                proto = line.split(" ran ", 1)[1].split()[0]
                                summaries[proto] = line
                    if set(summaries) == expect:
                        break
                    if proc.poll() is not None:
                        raise AssertionError(
                            "client exited early: "
                            + (proc.stderr.read().decode() if proc.stderr else "")
                        )
                    time.sleep(0.25)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()

            assert set(summaries) == expect, (summaries, log_path.read_text())
            # --- guest-log counters: every invocation succeeded ---
            for proto, line in summaries.items():
                assert "ok=2" in line, (proto, line)
                assert "fails=0" in line, (proto, line)
                assert "missing_tool=0" in line, (proto, line)

            # --- and the far sides saw REAL protocol traffic ---
            assert len(http_seen) >= 2, http_seen
            assert all(r.startswith("GET / ") for r in http_seen), http_seen

            assert len(dns.queries) >= 2, dns.queries
            for q in dns.queries:
                # A well-formed DNS query header: QR=0, opcode 0, QDCOUNT=1.
                assert q[2] & 0x80 == 0
                assert q[4:6] == b"\x00\x01"
                assert _StubDNS.qname_of(q) == _DEFAULT_DNS_QNAME

            # The ignored-`bytes:` announcement really lands in the guest log.
            assert "note dns bytes=256 ignored" in log_path.read_text()
    finally:
        srv.shutdown()
        srv.server_close()


# ===========================================================================
# `drive: browser` -- the browser engine (docs/WORK_ORDER_protocol_engines.md
# §5 E4). Real headless Chromium at each firing instant, so ONE driver
# produces DNS + real TLS (real SNI/JA3/JA4) + HTTP/1.1 + HTTP/2 + QUIC.
# The tests below cover: the PSN-030 widening (again additive, count
# unchanged), dispatch, render determinism (including the seeded browse-order
# shuffle), BOTH containment modes and the structural check that keeps them
# apart, the volume->pages mapping and the ignored-`bytes:` announcement, the
# fail-loud paths (un-drivable proto, malformed browse name, no browser
# binary, Windows), and lifecycle/unit-naming parity with the siblings.
# ===========================================================================

_BR_PEER = "198.51.100.23"


def _browser_persona(*emits, persona_id="web-user", os_family="linux", knobs=None):
    """A synthetic `drive: browser` persona. No SHIPPED catalog persona uses
    this drive kind yet (deliberate, on the `command` precedent: a shipped one
    would be assignable onto guests with no Chromium baked in)."""
    return Persona(
        id=persona_id, os_family=os_family, drive="browser", emits=tuple(emits),
        requires=tuple(sorted({e.peer_role for e in emits if e.peer_role})),
        knobs=dict(knobs or {}),
    )


class _FakeBrowserGA(_FakeGA):
    """`_FakeGA` plus an answer for the browser driver's ANY-OF probe (which
    reports PRESENCE with `FOUND:` lines, the inverse of the `command`
    driver's missing-report)."""

    def __init__(self, *, browsers=("chromium",), **kw):
        super().__init__(**kw)
        self.browsers = list(browsers)

    def exec(self, vmid, argv, *, timeout_s=300):
        joined = " ".join(str(a) for a in argv)
        if _BROWSER_PROBE_MARKER in joined:
            self.execs.append(tuple(argv))
            tagged = "\n".join(f"{_FOUND_TAG}{b}" for b in self.browsers)
            return GuestExecResult(0, self.profile_banner + tagged, "")
        return super().exec(vmid, argv, timeout_s=timeout_s)


def _args_of(emit, target=0, index=0):
    return emit["args"][target][index]


def _url_of(args):
    return args[-1]


# --- vocabulary: PSN-030 widened again, additively -------------------------


def test_psn_030_accepts_browser_and_the_rule_count_is_unchanged():
    """The version-locked widening (VALIDATOR): `browser` joins the enum, no
    previously valid persona becomes invalid, and the rule COUNT stays 114 --
    the PSN-100/PSN-110/`command` precedent."""
    from axrange.personas.schema import DRIVE_KINDS

    for drive in DRIVE_KINDS:
        p = Persona(id="x", os_family="linux", drive=drive)
        assert "PSN-030" not in {f.rule_id for f in validate_persona(p)}, drive
    assert "browser" in DRIVE_KINDS
    bad = Persona(id="x", os_family="linux", drive="teleport")
    assert "PSN-030" in {f.rule_id for f in validate_persona(bad)}
    assert len(list_rules()) == 114
    assert len([r for r in list_rules() if r.category == RuleCategory.PERSONA]) == 11


def test_quic_is_a_known_proto_carried_over_udp():
    """PSN-040's open-but-curated vocabulary gains the one token the browser
    engine needs for HTTP/3, and the schema's own transport derivation puts
    it on UDP (there is no TCP form of QUIC -- that form is `https`)."""
    from axrange.personas.schema import KNOWN_PROTOS, default_transport

    assert "quic" in KNOWN_PROTOS
    assert "http3" not in KNOWN_PROTOS  # deliberately ONE spelling
    assert default_transport("quic") == "udp"
    assert default_transport("https") == "tcp"
    p = _browser_persona(Emit(proto="quic", peer_role="web-tier"))
    assert "PSN-040" not in {f.rule_id for f in validate_persona(p)}


def test_get_driver_dispatches_browser():
    assert isinstance(get_driver("browser"), BrowserDriver)


# --- render: determinism + the seeded browse-order shuffle -----------------


def test_browser_render_is_byte_stable_given_seed():
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["intranet.corp.example", "wiki.corp.example"]},
    )
    peers = {"web-tier": _BR_PEER}
    a = render_browser_client(p, instance_id="ws01-web-user", peers=peers, seed=7)
    b = render_browser_client(p, instance_id="ws01-web-user", peers=peers, seed=7)
    assert a == b


def test_browser_render_differs_across_seed_and_instance():
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["a.corp.example", "b.corp.example"]},
    )
    peers = {"web-tier": _BR_PEER}
    a = render_browser_client(p, instance_id="ws01", peers=peers, seed=1)
    b = render_browser_client(p, instance_id="ws01", peers=peers, seed=2)
    c = render_browser_client(p, instance_id="ws02", peers=peers, seed=1)
    assert a != b
    assert a != c


def test_browser_browse_order_is_shuffled_per_instance_not_lock_step():
    """Two hosts running ONE persona must not walk the same sites in the same
    order; the same host must walk the same order every render (determinism).
    An UNSEEDED render keeps the authored order."""
    names = [f"n{i}.corp.example" for i in range(8)]
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": names},
    )
    peers = {"web-tier": _BR_PEER}

    def order(instance_id, seed):
        src = render_browser_client(
            p, instance_id=instance_id, peers=peers, seed=seed
        )
        return _emits_of(src)[0]["names"]

    a1, a2 = order("ws01", 5), order("ws01", 5)
    b1 = order("ws02", 5)
    assert a1 == a2                     # deterministic per (instance, seed)
    assert sorted(a1) == sorted(names)  # a permutation, nothing dropped
    assert a1 != b1                     # ...and the two hosts differ
    assert order("ws01", None) == names  # unseeded -> authored order


def test_browser_client_compiles_and_never_goes_through_a_shell():
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        Emit(proto="quic", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["intranet.corp.example"]},
    )
    src = render_browser_client(
        p, instance_id="ws01", peers={"web-tier": _BR_PEER}, seed=3
    )
    assert src.startswith("#!/usr/bin/env python3")
    compile(src, "<browser-persona-client>", "exec")
    assert "shell=True" not in src
    assert "os.system" not in src


def test_every_supported_proto_renders():
    """Derived from the driver's OWN table (LES-223), not a hardcoded list:
    a sixth proto row cannot land untested."""
    assert browser_protos() == ("http", "https", "quic")
    for proto in browser_protos():
        p = _browser_persona(
            Emit(proto=proto, peer_role="web-tier"),
            knobs={"browse_names": ["shop.example"]},
        )
        src = render_browser_client(
            p, instance_id=f"h1-{proto}", peers={"web-tier": _BR_PEER}, seed=1
        )
        (emit,) = _emits_of(src)
        assert emit["proto"] == proto
        args = _args_of(emit)
        assert args[0] == "--headless=new"
        assert _url_of(args).endswith("://shop.example/")


# --- CONTAINMENT: the real name in SNI, the baked IP on the wire -----------


def test_browser_default_mode_maps_every_name_to_the_resolved_peer():
    """Containment mode 1: the URL carries the REAL name (so SNI/Host carry
    it) while `--host-resolver-rules=MAP * <ip>` pins the connection to the
    orchestrator-resolved twin address -- no guest-side resolution at all."""
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["intranet.corp.example"]},
    )
    src = render_browser_client(
        p, instance_id="ws01", peers={"web-tier": _BR_PEER}, seed=1
    )
    (emit,) = _emits_of(src)
    args = _args_of(emit)
    assert f"--host-resolver-rules=MAP * {_BR_PEER}" in args
    # The name is what goes on the wire as SNI/Host...
    assert _url_of(args) == "https://intranet.corp.example/"
    # ...and the peer IP appears ONLY in the resolver rule, never as the URL
    # host (an IP URL would mean a handshake with no SNI at all).
    assert _BR_PEER not in _url_of(args)


def test_browser_resolve_via_dns_omits_the_map_rule_and_needs_no_peer():
    """Containment mode 2 (opt-in, user ruling 2026-09-21): the guest resolves
    normally, so there is no MAP rule -- and the emit no longer depends on a
    resolved peer at all (a `peer_role: internet` browser persona is the
    intended shape)."""
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="internet"),
        knobs={"browse_names": ["shop.example"], "resolve_via_dns": True},
    )
    src = render_browser_client(p, instance_id="ws01", peers={}, seed=1)
    (emit,) = _emits_of(src)
    assert emit["peer_ips"] == []
    assert len(emit["args"]) == 1          # one target, no peer needed
    args = _args_of(emit)
    assert not [a for a in args if a.startswith("--host-resolver-rules")]
    assert _url_of(args) == "https://shop.example/"
    assert any("resolve_via_dns" in n for n in emit["notes"])


def test_browser_check_refuses_a_map_rule_that_is_not_the_peers_ip_literal():
    """The structural check that keeps the two modes from blurring: a MAP rule
    pinned to a NAME (or to a different address than the peer the invocation
    was built for) is refused, exactly as `command._check_containment` refuses
    a `--resolve` bound to a non-literal."""
    from axrange.personas.drivers import browser as br

    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    spec = br._BROWSER_PROTOS["https"]
    bad = br._build_args(
        name="shop.example", spec=spec, port=443, map_ip="sink.example",
        profile_dir="/tmp/p", ignore_cert_errors=False,
    )
    with pytest.raises(PersonaDriverError) as e:
        br._check_browser_args(
            p, "ws01", p.emits[0], bad, name="shop.example",
            map_ip="sink.example", ignore_cert_errors=False,
        )
    assert "IP literal" in str(e.value)

    # ...and a MAP rule present in the DNS mode is refused too.
    mapped = br._build_args(
        name="shop.example", spec=spec, port=443, map_ip=_BR_PEER,
        profile_dir="/tmp/p", ignore_cert_errors=False,
    )
    with pytest.raises(PersonaDriverError) as e:
        br._check_browser_args(
            p, "ws01", p.emits[0], mapped, name="shop.example", map_ip=None,
            ignore_cert_errors=False,
        )
    assert "resolve_via_dns" in str(e.value)


def test_browser_check_refuses_a_second_endpoint_and_a_wrong_url_host():
    from axrange.personas.drivers import browser as br

    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    spec = br._BROWSER_PROTOS["https"]
    args = br._build_args(
        name="shop.example", spec=spec, port=443, map_ip=_BR_PEER,
        profile_dir="/tmp/p", ignore_cert_errors=False,
    )
    with pytest.raises(PersonaDriverError) as e:
        br._check_browser_args(
            p, "ws01", p.emits[0], args + ["http://evil.example/"],
            name="shop.example", map_ip=_BR_PEER, ignore_cert_errors=False,
        )
    assert "URL tokens" in str(e.value)

    with pytest.raises(PersonaDriverError) as e:
        br._check_browser_args(
            p, "ws01", p.emits[0], args, name="other.example",
            map_ip=_BR_PEER, ignore_cert_errors=False,
        )
    assert "SNI" in str(e.value)


def test_browser_cert_errors_are_only_ignored_when_the_knob_asks():
    """Default OFF: the lab CA belongs in the image trust store, and a silent
    cert bypass would hide the one thing the sink must get right."""
    emit = Emit(proto="https", peer_role="web-tier")
    default = _browser_persona(emit, knobs={"browse_names": ["shop.example"]})
    src = render_browser_client(
        default, instance_id="ws01", peers={"web-tier": _BR_PEER}
    )
    assert "--ignore-certificate-errors" not in _args_of(_emits_of(src)[0])

    opted = _browser_persona(
        emit,
        knobs={"browse_names": ["shop.example"], "browser_ignore_cert_errors": True},
    )
    src = render_browser_client(
        opted, instance_id="ws01", peers={"web-tier": _BR_PEER}
    )
    assert "--ignore-certificate-errors" in _args_of(_emits_of(src)[0])


# --- browse names: the field that becomes SNI ------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "https://shop.example/",   # a URL, not a name
        "shop.example:443",        # a host:port
        "shop.example/news",       # a path
        "198.51.100.10",           # an IP literal -> a handshake with no SNI
        "shop.example.",           # a trailing dot
        "-shop.example",           # a malformed label
        "shop..example",           # an empty label
        "shop example",            # whitespace
        "",                        # empty
    ],
)
def test_browser_refuses_a_browse_name_that_is_not_a_hostname(bad):
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"), knobs={"browse_names": [bad]}
    )
    with pytest.raises(PersonaDriverError) as e:
        render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    assert "browse_names" in str(e.value)


def test_browser_refuses_malformed_browse_names_and_knob_types():
    for knobs in (
        {"browse_names": "shop.example"},          # a bare string, not a list
        {"browse_names": []},                      # present but empty
        {"browse_names": ["a.example"], "resolve_via_dns": "yes"},
        {"browse_names": ["a.example"], "browser_ignore_cert_errors": 1},
        {"browse_names": ["a.example"], "browse_timeout_s": "soon"},
        {"browse_names": ["a.example"], "browse_timeout_s": 9999},
    ):
        p = _browser_persona(Emit(proto="https", peer_role="web-tier"), knobs=knobs)
        with pytest.raises(PersonaDriverError):
            render_browser_client(
                p, instance_id="ws01", peers={"web-tier": _BR_PEER}
            )


def test_browser_without_browse_names_synthesizes_one_and_says_so():
    """The fallback is a real name derived from `peer_role`, and the
    substitution is ANNOUNCED once at arm time -- never silently applied."""
    p = _browser_persona(Emit(proto="https", ports=(443,), peer_role="web-tier"))
    src = render_browser_client(
        p, instance_id="ws01", peers={"web-tier": _BR_PEER}, seed=1
    )
    (emit,) = _emits_of(src)
    assert emit["names"] == ["web-tier.twin.invalid"]
    assert _url_of(_args_of(emit)) == "https://web-tier.twin.invalid/"
    assert any("no knobs.browse_names authored" in n for n in emit["notes"])
    assert "log('note '" in src


def test_browser_timeout_knob_reaches_the_client():
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"], "browse_timeout_s": 12},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    assert _emits_of(src)[0]["timeout_s"] == 12
    assert "subprocess.TimeoutExpired" in src


# --- protos, ports, transports ---------------------------------------------


def test_browser_quic_forces_the_origin_to_http3():
    """Chromium only speaks QUIC to an origin it has seen advertise Alt-Svc,
    so a `quic` emit that did not force it would be a TCP HTTP/2 flow wearing
    a QUIC label (LES-001)."""
    p = _browser_persona(
        Emit(proto="quic", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    args = _args_of(_emits_of(src)[0])
    assert "--enable-quic" in args
    assert "--origin-to-force-quic-on=shop.example:443" in args
    assert _url_of(args) == "https://shop.example/"   # h3 rides the https scheme

    # ...and plain https never forces it.
    https = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(
        https, instance_id="ws01", peers={"web-tier": _BR_PEER}
    )
    assert "--enable-quic" not in _args_of(_emits_of(src)[0])


def test_browser_unsupported_proto_fails_loud_and_names_the_alternatives():
    """LES-001: never a silent skip, never a plausible-looking wrong client."""
    p = _browser_persona(
        Emit(proto="smb", ports=(445,), peer_role="file-server"),
        knobs={"browse_names": ["shop.example"]},
    )
    with pytest.raises(PersonaDriverError) as e:
        render_browser_client(
            p, instance_id="ws01", peers={"file-server": _BR_PEER}
        )
    msg = str(e.value)
    assert "smb" in msg
    assert "'quic'" in msg and "'https'" in msg
    assert "drive: command" in msg


def test_browser_segment_scoped_peer_roles_fail_loud():
    for role in ("broadcast", "multicast"):
        p = _browser_persona(
            Emit(proto="https", peer_role=role),
            knobs={"browse_names": ["shop.example"]},
        )
        with pytest.raises(PersonaDriverError) as e:
            render_browser_client(p, instance_id="ws01", peers={})
        assert role in str(e.value)


def test_browser_explicit_transport_a_browser_cannot_drive_fails_loud():
    """An EXPLICIT `transport:` a browser cannot honour for that proto is a
    loud authoring error; a DERIVED default is never checked (so `unset` and
    `explicitly correct` render byte-identically)."""
    bad = _browser_persona(
        Emit(proto="https", peer_role="web-tier", transport="udp"),
        knobs={"browse_names": ["shop.example"]},
    )
    with pytest.raises(PersonaDriverError) as e:
        render_browser_client(bad, instance_id="ws01", peers={"web-tier": _BR_PEER})
    assert "quic" in str(e.value)

    unset = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    explicit = _browser_persona(
        Emit(proto="https", peer_role="web-tier", transport="tcp"),
        knobs={"browse_names": ["shop.example"]},
    )
    kw = dict(instance_id="ws01", peers={"web-tier": _BR_PEER}, seed=4)
    assert render_browser_client(unset, **kw) == render_browser_client(explicit, **kw)


def test_browser_non_default_port_lands_in_the_url_and_the_default_does_not():
    """A real browser omits the default port from the Host header; anything
    else is in the URL."""
    p = _browser_persona(
        Emit(proto="http", ports=(8080,), peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    assert _url_of(_args_of(_emits_of(src)[0])) == "http://shop.example:8080/"

    default = _browser_persona(
        Emit(proto="http", peer_role="web-tier"),   # no ports authored -> 80
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(
        default, instance_id="ws01", peers={"web-tier": _BR_PEER}
    )
    assert _url_of(_args_of(_emits_of(src)[0])) == "http://shop.example/"


def test_browser_fans_out_per_peer_and_round_robins_names():
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier", peer_scope="all"),
        knobs={"browse_names": ["a.example", "b.example"]},
    )
    src = render_browser_client(
        p, instance_id="ws01", peers={"web-tier": "10.0.0.9"},
        peer_sets={"web-tier": ["10.0.0.9", "10.0.0.10"]}, seed=2,
    )
    (emit,) = _emits_of(src)
    assert len(emit["args"]) == 2                     # one target per peer
    assert len(emit["args"][0]) == 2                  # both names per target
    assert "--host-resolver-rules=MAP * 10.0.0.9" in _args_of(emit, 0, 0)
    assert "--host-resolver-rules=MAP * 10.0.0.10" in _args_of(emit, 1, 0)
    hosts = {_url_of(a).split("//")[1].rstrip("/") for a in emit["args"][0]}
    assert hosts == {"a.example", "b.example"}


def test_browser_unresolved_peer_role_is_a_skip_not_a_failure():
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={})
    assert _emits_of(src)[0]["args"] == []
    assert "not resolved" in src


# --- volume -> pages, and the un-spendable byte budget ---------------------


def test_browser_volume_maps_to_pages_per_firing():
    """`volume:` is PAGE LOADS per firing -- the shared scripted volume
    profile's message counts, reused rather than re-tabulated."""
    seen = {}
    for volume in ("low", "med", "high", "bulk"):
        p = _browser_persona(
            Emit(proto="https", peer_role="web-tier", volume=volume),
            knobs={"browse_names": ["shop.example"]},
        )
        src = render_browser_client(
            p, instance_id="ws01", peers={"web-tier": _BR_PEER}
        )
        (emit,) = _emits_of(src)
        seen[volume] = emit["pages"]
        # `bytes` is deliberately NOT threaded into a browser emit's config.
        assert "bytes" not in emit
    assert seen == {"low": 2, "med": 8, "high": 24, "bulk": 64}


def test_browser_ignored_byte_budget_is_announced_once_not_silently_dropped():
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier", volume="high"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    (emit,) = _emits_of(src)
    note = next(n for n in emit["notes"] if "bytes=" in n)
    assert "65536" in note
    assert "ignored" in note
    assert "server's choice" in note


def test_browser_schedule_override_and_cadence_gate():
    """The traffic-profile assignment's `schedule:` override forces the gate,
    exactly as it does for the scripted/command siblings."""
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier", cadence="diurnal"),
        knobs={"browse_names": ["shop.example"]},
    )
    peers = {"web-tier": _BR_PEER}
    src = render_browser_client(p, instance_id="ws01", peers=peers)
    assert _emits_of(src)[0]["gate"] == "business_hours"
    src = render_browser_client(p, instance_id="ws01", peers=peers, schedule="24x7")
    assert _emits_of(src)[0]["gate"] == "always"

    cyc = _browser_persona(
        Emit(proto="https", peer_role="web-tier", cadence="cyclic:5m"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(cyc, instance_id="ws01", peers=peers)
    (emit,) = _emits_of(src)
    assert emit["interval_s"] == 300.0
    src = render_browser_client(
        cyc, instance_id="ws01", peers=peers, schedule="business-hours"
    )
    assert _emits_of(src)[0]["gate"] == "business_hours"


# --- probe, launch, lifecycle ----------------------------------------------


def test_browser_probe_reports_presence_any_of_and_always_exits_zero():
    """ANY-OF semantics (the inverse of `command`'s missing-report): a guest
    needs `chromium` OR `chromium-browser`, so absence is 'no tagged line at
    all'. Run against a REAL shell, not just asserted as a string."""
    probe = render_browser_probe(["sh", "definitely-not-a-real-binary-xyz"])
    assert _BROWSER_PROBE_MARKER in probe
    if shutil.which("sh"):
        res = subprocess.run(["sh", "-lc", probe], capture_output=True, text=True)
        assert res.returncode == 0
        assert [
            ln for ln in res.stdout.splitlines() if ln.startswith(_FOUND_TAG)
        ] == [f"{_FOUND_TAG}sh"]

        none = render_browser_probe(["definitely-not-a-real-binary-xyz"])
        res = subprocess.run(["sh", "-lc", none], capture_output=True, text=True)
        assert res.returncode == 0
        assert not [
            ln for ln in res.stdout.splitlines() if ln.startswith(_FOUND_TAG)
        ]


def test_browser_launch_uses_systemd_run_not_nohup():
    """Regression guard (LES-086/GAP-020/GAP-025 class), same as its
    siblings' -- and the same deterministic unit/paths."""
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    paths = instance_paths("ws01-web-user")
    launch = render_browser_launch("ws01-web-user", p, script_path=paths["script"])
    assert "nohup" not in launch
    assert f"systemd-run --unit={paths['unit']} --collect bash -c" in launch
    assert f"systemctl is-active --quiet {paths['unit']} ||" in launch
    if shutil.which("bash"):
        res = subprocess.run(["bash", "-n"], input=launch, text=True, capture_output=True)
        assert res.returncode == 0, res.stderr


def test_browser_profile_dir_lives_under_the_instance_path():
    paths = instance_paths("ws01-web-user")
    assert browser_profile_dir("ws01-web-user") == paths["dir"] + "/chrome-profile"
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(
        p, instance_id="ws01-web-user", peers={"web-tier": _BR_PEER}
    )
    assert f"--user-data-dir={paths['dir']}/chrome-profile" in _args_of(
        _emits_of(src)[0]
    )
    # The stale-singleton guard: a timed-out fetch must not wedge every later
    # one for the life of the twin.
    assert "SingletonLock" in src


def test_browser_driver_start_writes_probes_and_launches():
    ga = _FakeBrowserGA(run_exit=0)
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    result = BrowserDriver().start(
        ga, 101, p, instance_id="ws01-web-user",
        peers={"web-tier": _BR_PEER}, seed=5,
    )
    assert result["started"] is True
    paths = instance_paths("ws01-web-user")
    assert result["unit"] == paths["unit"]
    assert "--headless=new" in ga.written[paths["script"]]
    assert "nohup" not in ga.written[paths["dir"] + "/launch.sh"]
    # The browser probe really ran, before the launch.
    joined = [" ".join(str(a) for a in argv) for argv in ga.execs]
    probe_idx = next(i for i, j in enumerate(joined) if _BROWSER_PROBE_MARKER in j)
    launch_idx = next(i for i, j in enumerate(joined) if "launch.sh" in j)
    assert probe_idx < launch_idx


def test_browser_driver_start_fails_loud_when_no_browser_is_installed():
    """Never start a client that would log a skip on every fire for the life
    of the twin -- and name BOTH accepted binaries plus the Debian package."""
    ga = _FakeBrowserGA(run_exit=0, browsers=())
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    with pytest.raises(PersonaDriverError) as e:
        BrowserDriver().start(
            ga, 101, p, instance_id="ws01", peers={"web-tier": _BR_PEER}
        )
    msg = str(e.value)
    assert "chromium" in msg and "chromium-browser" in msg
    assert "apt: chromium" in msg
    assert "IMAGE_BUILD" in msg
    # ...and it stopped BEFORE launching anything.
    assert not any("launch.sh" in " ".join(str(a) for a in argv) for argv in ga.execs)


def test_browser_probe_accepts_the_ubuntu_binary_and_ignores_a_login_banner():
    ga = _FakeBrowserGA(
        run_exit=0, browsers=("chromium-browser",),
        profile_banner="Welcome to the axRange lab image\n",
    )
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    assert BrowserDriver().start(
        ga, 101, p, instance_id="ws01", peers={"web-tier": _BR_PEER}
    )["started"] is True


def test_browser_driver_start_fails_loud_on_launch_error():
    ga = _FakeBrowserGA(run_exit=1)
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    with pytest.raises(PersonaDriverError) as e:
        BrowserDriver().start(
            ga, 101, p, instance_id="ws01", peers={"web-tier": _BR_PEER}
        )
    assert "launch failed" in str(e.value)


def test_browser_driver_stop_and_heartbeat_reuse_the_shared_helpers():
    ga = _FakeBrowserGA(run_exit=0)
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    r = BrowserDriver().stop(ga, 101, p, instance_id="ws01")
    assert r["stopped"] is True
    assert r["unit"] == instance_paths("ws01")["unit"]
    assert BrowserDriver().heartbeat(ga, 101, p, instance_id="ws01")["alive"] is True


def test_browser_driver_is_linux_only_and_says_so_loudly():
    """The Windows web engine is its own slice (work order §5 E6); a Windows
    browser persona must be a LOUD error, never a silent no-op."""
    ga = _FakeBrowserGA(run_exit=0)
    win = _browser_persona(
        Emit(proto="https", peer_role="web-tier"), os_family="windows",
        knobs={"browse_names": ["shop.example"]},
    )
    driver = BrowserDriver()
    for call in (
        lambda: driver.start(ga, 101, win, instance_id="ws01", peers={"web-tier": _BR_PEER}),
        lambda: driver.stop(ga, 101, win, instance_id="ws01"),
        lambda: driver.heartbeat(ga, 101, win, instance_id="ws01"),
    ):
        with pytest.raises(PersonaDriverError) as e:
            call()
        assert "Linux-only" in str(e.value)
    assert ga.written == {}


def test_browser_driver_unknown_os_family_still_raises_the_shared_error():
    ga = _FakeBrowserGA(run_exit=0)
    weird = _browser_persona(
        Emit(proto="https", peer_role="web-tier"), os_family="plan9",
        knobs={"browse_names": ["shop.example"]},
    )
    with pytest.raises(PersonaDriverError) as e:
        BrowserDriver().start(ga, 101, weird, instance_id="ws01", peers={})
    assert "unsupported os_family" in str(e.value)


def test_no_shipped_persona_uses_the_browser_drive_yet():
    """Content guard, on the `command` precedent: shipping one would make it
    assignable onto guests with no Chromium baked in."""
    assert [pid for pid, p in PERSONAS.items() if p.drive == "browser"] == []


# --- ok= must MEAN something, and the census must stay honest ---------------
# Both fixes from the dev45 live run, 2026-09-21.


def _browser_namespace(src, log_path):
    """Exec the rendered browser client's DEFINITIONS (everything above the
    arming line, so the `while True:` loop never runs) with a recording stub
    for `subprocess` -- the `command` client's `_client_namespace` posture:
    run the rendered artifact, don't just assert over its text."""
    head = src.split("log('armed")[0]
    ns: dict = {}
    exec(compile(head, "<browser-client>", "exec"), ns)
    ns["LOG"] = str(log_path)
    ns["BROWSER"] = "/usr/bin/chromium"
    # the real one is under /var/lib/axrange, which this process cannot create
    ns["PROFILE_DIR"] = str(log_path.parent / "chrome-profile")
    calls: list[list[str]] = []
    plan: list = []

    class _Stub:
        PIPE = -1
        DEVNULL = -3

        class SubprocessError(Exception):
            pass

        class TimeoutExpired(Exception):
            pass

        @staticmethod
        def run(argv, **kw):
            calls.append(list(argv))
            rc, out = plan.pop(0) if plan else (0, b"<html><body>" + b"x" * 200
                                               + b"</body></html>")
            return type("R", (), {"returncode": rc, "stdout": out})()

    ns["subprocess"] = _Stub
    return ns, calls, plan


_GOOD_DOM = b"<html><head><title>t</title></head><body>" + b"hello " * 40 + b"</body></html>"
_NETERROR_DOM = (
    b'<html><body><div id="main-frame-error" class="interstitial-wrapper">'
    b"<span>ERR_NAME_NOT_RESOLVED</span></div></body></html>"
)


def test_browser_counts_a_chromium_error_page_as_a_failure_not_an_ok(tmp_path):
    """dev45 2026-09-21: `pages=8 ok=8 fails=0` while the sensor saw ONE tls
    flow. Chromium exits 0 having rendered its own error page, so an exit-code
    success criterion counts a page that never loaded as a success -- LES-001
    in our own tooling. The DOM is the evidence now."""
    p = _browser_persona(
        Emit(proto="https", ports=(443,), peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(
        p, instance_id="ws01", peers={"web-tier": _BR_PEER}, seed=1
    )
    log_path = tmp_path / "persona.log"
    ns, calls, plan = _browser_namespace(src, log_path)
    plan.extend([(0, _GOOD_DOM), (0, _NETERROR_DOM), (0, b"   ")])
    emit = dict(_emits_of(src)[0], pages=3, gap_s=0)
    ns["fire"](emit)
    log = log_path.read_text()
    assert len(calls) == 3
    # one real page, one error page, one empty shell
    assert "pages=3 ok=1 fails=2 errpage=1 empty=1 timeouts=0" in log
    # ...and the reason is the network error, by name
    assert "ERR_NAME_NOT_RESOLVED" in log


def test_browser_judge_is_derived_from_the_dom_not_the_exit_code(tmp_path):
    """The verdict function itself, over the four cases it separates."""
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    ns, _, _ = _browser_namespace(src, tmp_path / "persona.log")
    judge = ns["judge"]
    assert judge(0, _GOOD_DOM.decode())[0] == "ok"
    assert judge(0, _NETERROR_DOM.decode()) == ("errpage", "ERR_NAME_NOT_RESOLVED")
    assert judge(0, "")[0] == "empty"
    assert judge(21, _GOOD_DOM.decode()) == ("proc", "exit=21")


def test_browser_turns_off_chromiums_own_background_networking():
    """dev45 2026-09-21: 976 queries for `dl.google.com` plus
    `edgedl.me.gvt1.com` in ONE cycle -- Chromium's component updater and
    telemetry inventing names the cast never observed, against a resolver with
    no upstream: 6,250 NXDOMAINs polluting the census the fidelity read
    consumes. These flags exist for the census, not for speed."""
    p = _browser_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs={"browse_names": ["shop.example"]},
    )
    src = render_browser_client(p, instance_id="ws01", peers={"web-tier": _BR_PEER})
    args = _args_of(_emits_of(src)[0])
    for flag in (
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-sync",
        "--metrics-recording-only",
        "--disable-client-side-phishing-detection",
        "--no-first-run",
        "--no-default-browser-check",
    ):
        assert flag in args, flag
    # `--safebrowsing-disable-auto-update` is deliberately absent: it cannot
    # be verified against a current Chromium from this repo and its job is
    # already covered by --disable-background-networking. A flag we cannot
    # confirm exists is a claim this render should not make.
    assert "--safebrowsing-disable-auto-update" not in args


# ===========================================================================
# The WINDOWS half of `drive: command` -- the domain-member engine
# (2026-09-21, docs/WORK_ORDER_protocol_engines.md §5 E6)
#
# NOT a new drive kind: `drive: command` already means "run a real protocol
# client", and the only reason it refused Windows was that nobody had written
# the PowerShell table. `DRIVE_KINDS`, PSN-030 and VALIDATOR.md are untouched
# -- the tests below pin that, because "we didn't have to change a
# version-locked contract" is a property worth keeping.
# ===========================================================================

import re  # noqa: E402

from axrange.personas.drivers import command_windows as cw  # noqa: E402
from axrange.personas.drivers.scripted import (  # noqa: E402
    render_scripted_launch_windows,
)
from axrange.personas.drivers.command_windows import (  # noqa: E402
    S3_LISTEN_PROOF_PROTOS,
    dials_at_target,
    ps_literal,
    render_command_client_windows,
    render_command_launch_windows,
    render_windows_command_probe,
    required_commands,
    windows_payload_path,
)
from axrange.personas.drivers.command_windows import (  # noqa: E402
    supported_protos as win_protos,
)

# Every knob the eight rows between them address the twin by. A test that
# drops one is testing a different persona than the twin will run.
_WIN_KNOBS = {
    "ad_domain": "corp.example",
    "dc_name": "dc01.corp.example",
    "fileserver": "fs01.corp.example",
    "smb_share": "Sales",
    "web_names": ["intranet.corp.example", "portal.corp.example"],
}
_WIN_PEERS = {"domain-controller": "10.10.0.5", "file-server": "10.10.0.6"}


def _win_persona(*emits, persona_id="win-member", os_family="windows", knobs=None):
    """A synthetic WINDOWS `drive: command` persona. No SHIPPED catalog
    persona uses `drive: command` on either OS yet (see the content guard at
    the end of this section)."""
    return Persona(
        id=persona_id, os_family=os_family, drive="command", emits=tuple(emits),
        knobs=dict(_WIN_KNOBS if knobs is None else knobs),
    )


class _FakeWinGA(_FakeGA):
    """`_FakeGA` plus an answer for the Windows built-in probe, which goes
    over `shell_exec` (PowerShell) rather than `exec`. Reports absence the
    same way the real probe does -- one `MISSING:`-tagged stdout line per
    absent command, ALWAYS exiting 0 (a `run_exit=1` fake exists to make the
    LAUNCH fail and must not also make the probe look broken)."""

    def __init__(self, *, missing_commands=(), banner="", **kw):
        super().__init__(**kw)
        self.missing_commands = list(missing_commands)
        self.banner = banner

    def shell_exec(self, vmid, command, *, timeout_s=300):
        self.shell_execs.append(command)
        if cw._WIN_PROBE_MARKER in command:
            tagged = "\n".join(f"{cw._MISSING_TAG}{c}" for c in self.missing_commands)
            return GuestExecResult(0, self.banner + tagged, "")
        return GuestExecResult(
            0 if self.run_exit == 0 else 1, "", "" if self.run_exit == 0 else "boom"
        )


def _all_protos_persona():
    return _win_persona(*[Emit(proto=p, peer_role="domain-controller")
                          for p in win_protos()])


# --- no contract moved ------------------------------------------------------


def test_windows_command_engine_adds_no_drive_kind_and_no_psn_rule():
    """The design choice, pinned: E6 extends `drive: command` rather than
    minting `windows-domain`, so `DRIVE_KINDS`, PSN-030's enum and the
    114-rule catalog are all untouched."""
    from axrange.personas.schema import DRIVE_KINDS

    assert DRIVE_KINDS == ("scripted", "replay", "real-service", "command", "browser")
    assert len(list_rules()) == 114
    # A windows `drive: command` persona is VALID against the catalog rules --
    # nothing in the validator had to learn about this engine.
    p = _win_persona(Emit(proto="kerberos", peer_role="domain-controller"))
    findings = validate_persona(p)
    assert [f.rule_id for f in findings if f.rule_id in ("PSN-020", "PSN-030")] == []


def test_get_driver_still_returns_the_one_command_driver_for_both_oses():
    assert isinstance(get_driver("command"), CommandDriver)


# --- determinism ------------------------------------------------------------


def test_windows_render_is_byte_stable_given_seed():
    p = _all_protos_persona()
    a = render_command_client_windows(p, instance_id="ws01-win", peers=_WIN_PEERS, seed=7)
    b = render_command_client_windows(p, instance_id="ws01-win", peers=_WIN_PEERS, seed=7)
    assert a == b


def test_windows_render_differs_across_seed_and_instance():
    p = _win_persona(Emit(proto="https", peer_role="web-tier"))
    a = render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    b = render_command_client_windows(p, instance_id="ws01", peers={}, seed=2)
    c = render_command_client_windows(p, instance_id="ws02", peers={}, seed=1)
    assert a != b and a != c


def test_windows_web_name_order_is_shuffled_per_instance_not_lock_step():
    """Same reasoning as the browser engine: two members running one persona
    must not walk the same sites in the same order."""
    p = _win_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs=dict(_WIN_KNOBS, web_names=[f"site{i}.corp.example" for i in range(8)]),
    )
    orders = set()
    for inst in ("ws01", "ws02", "ws03", "ws04"):
        src = render_command_client_windows(p, instance_id=inst, peers={}, seed=11)
        orders.add(tuple(re.findall(r"Uri='https://([a-z0-9.]+)/'", src)))
    assert len(orders) > 1


def test_windows_render_is_ascii_only_because_ps51_reads_bomless_as_ansi():
    src = render_command_client_windows(
        _all_protos_persona(), instance_id="ws01", peers=_WIN_PEERS, seed=3
    )
    assert all(ord(ch) < 128 for ch in src)
    with pytest.raises(PersonaDriverError) as e:
        cw._assert_ascii(_win_persona(), "ws01", "Write-PersonaLog 'caf\u00e9'")
    assert "non-ASCII" in str(e.value)


# --- the template table: what each row actually runs -------------------------


def test_windows_supported_protos_are_the_eight_built_in_rows():
    assert win_protos() == (
        "dns", "http", "https", "kerberos", "ldap", "ntp", "rpc", "smb",
    )


def test_kerberos_row_purges_then_requests_a_real_spn_not_a_cache_read():
    """LES-001 per-row: a bare `klist` prints the cached tickets and emits
    NOTHING on the wire. The purge is what forces the AS-REQ/TGS-REQ, and
    `get` needs a real SPN -- an address would not be a ticket target at
    all."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="kerberos", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "File='klist.exe'; Args=@('purge')" in src
    assert "File='klist.exe'; Args=@('get','HOST/dc01.corp.example')" in src
    # The purge must come FIRST; a `get` against a warm cache is a local read.
    assert src.index("Args=@('purge')") < src.index("Args=@('get',")


def test_kerberos_row_forces_the_kdc_exchange_onto_tcp_for_the_s3_census():
    """Services order S3: the DC is only re-inferred as a DC when something
    dials 88/tcp AND 389/tcp at it. Windows picks UDP or TCP by MaxPacketSize,
    so the member must be pinned to TCP or the census silently misses it."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="kerberos", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "Lsa\\Kerberos\\Parameters" in src
    assert "Name='MaxPacketSize'; Value=1" in src
    assert "Setup=@(@{Kind='cmdlet'" in src
    off = render_command_client_windows(
        _win_persona(
            Emit(proto="kerberos", peer_role="domain-controller"),
            knobs=dict(_WIN_KNOBS, force_kerberos_tcp=False),
        ),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "MaxPacketSize" not in off
    assert "Setup=@()" in off


def test_s3_listen_proof_needs_both_88_tcp_and_389_tcp():
    """Neither row alone closes S3's AND -- a default member persona carries
    both, and `dials_at_target` is what a twin config can check."""
    assert S3_LISTEN_PROOF_PROTOS == ("kerberos", "ldap")
    dialled = {d for proto in S3_LISTEN_PROOF_PROTOS for d in dials_at_target(proto)}
    assert (88, "tcp") in dialled and (389, "tcp") in dialled
    assert (88, "tcp") in dials_at_target("kerberos")
    assert (389, "tcp") in dials_at_target("ldap")


def test_ldap_row_is_a_forced_group_policy_cycle_not_a_version_check():
    """`gpupdate` without `/force` compares GPO version stamps and does
    nothing on an already-applied member -- exit 0, no traffic."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="ldap", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "File='gpupdate.exe'" in src
    assert "'/force'" in src
    assert "/wait:" in src
    # One GP cycle is four protocols at once -- that is why this row exists.
    assert set(dials_at_target("ldap")) >= {
        (389, "tcp"), (445, "tcp"), (135, "tcp"), (88, "tcp")
    }


def test_dns_row_pins_a_server_to_bypass_the_client_cache_and_forces_the_locator():
    src = render_command_client_windows(
        _win_persona(Emit(proto="dns", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    # -Server is the cache bypass; without it an identical query inside the
    # TTL is answered locally and emits nothing.
    assert "Cmdlet='Resolve-DnsName'" in src
    assert "Server='10.10.0.5'" in src
    assert "DnsOnly=$true" in src and "NoHostsFile=$true" in src
    # The DC locator: /force ignores the cached DC (DNS SRV + CLDAP ping).
    assert "File='nltest.exe'; Args=@('/dsgetdc:corp.example','/force')" in src
    assert (389, "udp") in dials_at_target("dns")


def test_dns_row_refuses_to_render_with_no_server_at_all():
    p = _win_persona(
        Emit(proto="dns", peer_role="domain-controller"),
        knobs={"ad_domain": "corp.example"},
    )
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "client cache" in str(e.value)


def test_smb_row_opens_a_session_copies_real_bytes_and_tears_the_session_down():
    p = _win_persona(
        Emit(proto="smb", peer_role="file-server", volume="med"),
    )
    src = render_command_client_windows(p, instance_id="ws01", peers=_WIN_PEERS, seed=1)
    assert "File='net.exe'; Args=@('use','\\\\fs01.corp.example\\Sales')" in src
    assert "Cmdlet='Copy-Item'" in src
    assert windows_payload_path("ws01") in src
    # A write is never cached, and the trailing /delete is what makes the NEXT
    # fire re-negotiate the session (Kerberos AP-REQ included).
    assert "'/delete','/y'); Always=$true" in src
    assert "Cmdlet='Remove-Item'" in src and "Always=$true" in src
    # The one Windows row that can genuinely spend a byte budget.
    assert "PayloadBytes=4096" in src


def test_smb_payload_is_clamped_and_the_clamping_is_announced():
    p = _win_persona(Emit(proto="smb", peer_role="file-server", bytes_per_msg=10**10))
    src = render_command_client_windows(p, instance_id="ws01", peers=_WIN_PEERS, seed=1)
    assert f"PayloadBytes={cw._SMB_MAX_BYTES}" in src
    assert "clamped to" in src


def test_rpc_row_is_dcom_with_a_remote_computername_not_wsman_and_not_local():
    """The observed 49667/49669/49677 are DCOM on dynamic high ports.
    `Get-CimInstance -ComputerName` would be WSMan on 5985/tcp -- a different
    protocol wearing the same label -- and dropping `-ComputerName` makes the
    query local and silent."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="rpc", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "Cmdlet='Get-WmiObject'" in src
    assert "ComputerName='dc01.corp.example'" in src
    assert "Get-CimInstance" not in src
    assert cw._WINDOWS_TEMPLATES["rpc"].dials_dynamic_high_ports is True
    assert (135, "tcp") in dials_at_target("rpc")


def test_ntp_row_rediscovers_rather_than_resyncing_from_service_state():
    src = render_command_client_windows(
        _win_persona(Emit(proto="ntp", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "File='w32tm.exe'; Args=@('/resync','/rediscover')" in src
    assert dials_at_target("ntp") == ((123, "udp"),)


def test_web_rows_carry_the_real_name_and_force_a_fresh_handshake():
    """.NET sends the SNI from the URL host, so the NAME is the fidelity
    point; `-DisableKeepAlive` is what makes each fire a fresh connection (and
    therefore a fresh, SNI-bearing handshake) rather than a pooled reuse."""
    src = render_command_client_windows(
        _win_persona(
            Emit(proto="https", peer_role="web-tier"),
            Emit(proto="http", peer_role="web-tier"),
        ),
        instance_id="ws01", peers={}, seed=1,
    )
    assert "Uri='https://intranet.corp.example/'" in src
    assert "Uri='http://portal.corp.example/'" in src or \
           "Uri='http://intranet.corp.example/'" in src
    assert src.count("DisableKeepAlive=$true") >= 2
    # UseBasicParsing: PS 5.1's IE-based parser errors on a never-configured
    # IE and does not exist on Server Core at all.
    assert "UseBasicParsing=$true" in src


def test_web_row_honours_a_non_default_port_in_the_url():
    src = render_command_client_windows(
        _win_persona(Emit(proto="https", ports=(8443,), peer_role="web-tier")),
        instance_id="ws01", peers={}, seed=1,
    )
    assert "Uri='https://intranet.corp.example:8443/'" in src or \
           "Uri='https://portal.corp.example:8443/'" in src


def test_every_row_declares_an_anti_cache_lever():
    """LES-001, structurally: a row that cannot say WHY it puts bytes on the
    wire is a row nobody checked."""
    for proto, tpl in cw._WINDOWS_TEMPLATES.items():
        assert tpl.forces_wire.strip(), proto
        assert tpl.commands, proto
        assert tpl.dials or tpl.dials_dynamic_high_ports, proto


# --- containment: names, not addresses (the INVERSE of the Linux check) ------


def test_windows_targets_must_be_names_never_ip_literals():
    for knob in ("dc_name", "fileserver"):
        p = _win_persona(
            Emit(proto="rpc", peer_role="domain-controller"),
            knobs=dict(_WIN_KNOBS, **{knob: "10.10.0.5"}),
        )
        with pytest.raises(PersonaDriverError) as e:
            render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
        assert "IP literal" in str(e.value)
        assert "SPN" in str(e.value)


def test_web_name_that_is_an_ip_is_refused_because_it_drops_the_sni():
    p = _win_persona(
        Emit(proto="https", peer_role="web-tier"),
        knobs=dict(_WIN_KNOBS, web_names=["10.10.0.9"]),
    )
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "IP literal" in str(e.value)


def test_containment_check_refuses_an_ip_target_and_an_undeclared_endpoint():
    p = _win_persona(Emit(proto="rpc", peer_role="domain-controller"))
    tpl = cw._WINDOWS_TEMPLATES["rpc"]
    good = [cw._cmdlet("Get-WmiObject", {"ComputerName": "dc01.corp.example"})]
    cw._check_windows_targets(
        p, "ws01", "rpc", tpl, good, ("dc01.corp.example",), ()
    )
    with pytest.raises(PersonaDriverError) as e:
        cw._check_windows_targets(
            p, "ws01", "rpc", tpl,
            [cw._cmdlet("Get-WmiObject", {"ComputerName": "10.10.0.5"})],
            ("10.10.0.5",), (),
        )
    assert "by ADDRESS" in str(e.value)
    with pytest.raises(PersonaDriverError) as e:
        cw._check_windows_targets(
            p, "ws01", "rpc", tpl,
            good + [cw._exe("net.exe", ["use", "\\\\other.corp.example\\x"])],
            ("dc01.corp.example",), (),
        )
    assert "undeclared UNC endpoint" in str(e.value)
    with pytest.raises(PersonaDriverError) as e:
        cw._check_windows_targets(
            p, "ws01", "rpc", tpl, good, ("absent.corp.example",), ()
        )
    assert "appears in none of its steps" in str(e.value)


def test_a_targetless_row_is_allowed_to_declare_no_target_but_others_are_not():
    """`gpupdate` and `w32tm` address NOTHING on purpose -- a real member
    discovers its DC and its time source. Every other row must declare one."""
    p = _win_persona(Emit(proto="ntp", peer_role="domain-controller"))
    cw._check_windows_targets(
        p, "ws01", "ntp", cw._WINDOWS_TEMPLATES["ntp"],
        [cw._exe("w32tm.exe", ["/resync"])], (), (),
    )
    with pytest.raises(PersonaDriverError) as e:
        cw._check_windows_targets(
            p, "ws01", "rpc", cw._WINDOWS_TEMPLATES["rpc"],
            [cw._cmdlet("Get-WmiObject", {"Class": "Win32_OperatingSystem"})], (), (),
        )
    assert "zero targets" in str(e.value)


def test_the_client_never_evaluates_a_rendered_string():
    """A cmdlet step is a hashtable of DATA the client splats -- no
    Invoke-Expression, no [scriptblock]::Create, so there is no eval surface
    for a knob to ride into."""
    src = render_command_client_windows(
        _all_protos_persona(), instance_id="ws01", peers=_WIN_PEERS, seed=1
    )
    assert "Invoke-Expression" not in src
    assert "scriptblock]::Create" not in src
    assert "iex " not in src


def test_invocations_are_hashtables_so_powershell_cannot_flatten_them():
    """`@(@(...))` collapses back to the inner array in PowerShell -- a single
    -target emit would silently lose a level. A hashtable wrapper cannot."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="rpc", peer_role="domain-controller")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    assert "Invocations=@(@{Target=" in src
    assert "Invocations=@(@(" not in src


# --- the table is OS-scoped, in both directions ------------------------------


def test_a_linux_persona_is_refused_by_the_windows_table():
    p = _win_persona(Emit(proto="kerberos", peer_role="domain-controller"),
                     os_family="linux")
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="h1", peers={}, seed=1)
    assert "os_family 'windows'" in str(e.value)


def test_a_windows_persona_is_refused_by_a_linux_only_proto():
    """`icmp` has a Linux template (`ping`) and no Windows one; the refusal
    must name the WINDOWS-supported set, not silently fall through."""
    p = _win_persona(Emit(proto="icmp", peer_role="domain-controller"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    msg = str(e.value)
    assert "no WINDOWS" in msg and "kerberos" in msg


def test_the_windows_table_is_a_SUBSET_of_the_linux_one_so_no_proto_is_windows_only():
    """Both tables speak one protocol vocabulary (PSN-040's), and every proto
    Windows can drive, Linux can drive too.

    2026-09-21: this test replaced a "`rpc` is Windows-only" mirror test. That
    was never a real asymmetry -- it was a NAMING mismatch. The Linux template
    was keyed `dcerpc` while the Windows table and `KNOWN_PROTOS` both said
    `rpc`, so the Linux renderer refused `rpc` for want of a spelling, and an
    authored `proto: rpc` emit failed PSN-040 on the Linux side. One spelling
    now, asserted here: a future table that drifts apart again fails this.
    """
    assert set(win_protos()) <= set(supported_protos())
    assert "rpc" in supported_protos() and "rpc" in win_protos()


def test_a_linux_only_proto_is_refused_on_windows():
    """The real asymmetry, in the direction it actually runs: Linux drives
    icmp/ssh/snmp/rdp, the Windows table does not, and an emit naming one must
    fail loud rather than render a client that emits nothing (LES-001)."""
    for proto in sorted(set(supported_protos()) - set(win_protos())):
        p = _win_persona(Emit(proto=proto, peer_role="domain-controller"))
        with pytest.raises(PersonaDriverError) as e:
            render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
        assert proto in str(e.value)


def test_segment_scoped_peer_roles_are_refused_on_windows_too():
    p = _win_persona(Emit(proto="dns", peer_role="multicast"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "peer_role" in str(e.value)


# --- knobs: malformed input is refused, never defaulted away ----------------


@pytest.mark.parametrize(
    "knobs,needle",
    [
        ({"web_names": "intranet.corp.example"}, "must be a LIST"),
        ({"web_names": []}, "present but empty"),
        ({"web_names": ["https://intranet.corp.example/"]}, "bare hostname"),
        ({"dc_name": "dc01.corp.example:88"}, "bare hostname"),
        ({"smb_share": "Sales/../etc"}, "bare SMB share name"),
        ({"kerberos_spn": "HOST"}, "well-formed SPN"),
        ({"dns_qname": "bad qname!"}, "well-formed DNS name"),
        ({"force_kerberos_tcp": "yes"}, "must be a boolean"),
        ({"win_step_timeout_s": "soon"}, "integer number of seconds"),
        ({"win_step_timeout_s": 99999}, "outside"),
    ],
)
def test_malformed_windows_knobs_are_refused(knobs, needle):
    p = _win_persona(Emit(proto="https", peer_role="web-tier"),
                     knobs=dict(_WIN_KNOBS, **knobs))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert needle in str(e.value)


def test_a_row_missing_the_knob_it_addresses_by_fails_loud_naming_it():
    p = _win_persona(Emit(proto="smb", peer_role="file-server"),
                     knobs={"ad_domain": "corp.example"})
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    msg = str(e.value)
    assert "fileserver" in msg and "smb_share" in msg


def test_win_step_timeout_knob_overrides_every_rows_own_timeout():
    p = _win_persona(Emit(proto="ldap", peer_role="domain-controller"),
                     knobs=dict(_WIN_KNOBS, win_step_timeout_s=45))
    src = render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "TimeoutS=45" in src


def test_a_derived_spn_is_announced_and_an_authored_one_is_honoured():
    derived = render_command_client_windows(
        _win_persona(Emit(proto="kerberos", peer_role="domain-controller")),
        instance_id="ws01", peers={}, seed=1,
    )
    assert "no knobs.kerberos_spn authored" in derived
    authored = render_command_client_windows(
        _win_persona(
            Emit(proto="kerberos", peer_role="domain-controller"),
            knobs=dict(_WIN_KNOBS, kerberos_spn="MSSQLSvc/db01.corp.example:1433"),
        ),
        instance_id="ws01", peers={}, seed=1,
    )
    assert "'get','MSSQLSvc/db01.corp.example:1433'" in authored
    assert "no knobs.kerberos_spn authored" not in authored


# --- ports / transports ------------------------------------------------------


def test_a_row_with_no_port_argument_refuses_a_pinned_port():
    p = _win_persona(Emit(proto="kerberos", ports=(1234,), peer_role="domain-controller"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "no port argument" in str(e.value)
    # Its own well-known port is fine, as is the schema's unset placeholder.
    for ports in ((), (88,)):
        render_command_client_windows(
            _win_persona(Emit(proto="kerberos", ports=ports, peer_role="domain-controller")),
            instance_id="ws01", peers={}, seed=1,
        )


def test_an_explicit_transport_a_row_cannot_drive_is_refused():
    p = _win_persona(Emit(proto="smb", transport="udp", peer_role="file-server"))
    with pytest.raises(PersonaDriverError) as e:
        render_command_client_windows(p, instance_id="ws01", peers=_WIN_PEERS, seed=1)
    assert "pins transport" in str(e.value)
    # A DERIVED default is never checked against the row -- only an author who
    # typed `transport:` can hit that branch.
    render_command_client_windows(
        _win_persona(Emit(proto="smb", peer_role="file-server")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )


# --- schedule / gate override ------------------------------------------------


def test_windows_schedule_override_forces_the_gate_both_ways():
    p = _win_persona(Emit(proto="ntp", cadence="diurnal", peer_role="domain-controller"))
    base = render_command_client_windows(p, instance_id="ws01", peers={}, seed=1)
    assert "Gate='business_hours'" in base
    always = render_command_client_windows(
        p, instance_id="ws01", peers={}, seed=1, schedule="24x7"
    )
    assert "Gate='always'" in always
    biz = render_command_client_windows(
        _win_persona(Emit(proto="ntp", cadence="24x7", peer_role="domain-controller")),
        instance_id="ws01", peers={}, seed=1, schedule="business-hours",
    )
    assert "Gate='business_hours'" in biz
    # Unset is byte-identical to no override at all (the backward-compat pin
    # every sibling renderer carries).
    assert render_command_client_windows(
        p, instance_id="ws01", peers={}, seed=1, schedule=None
    ) == base


def test_volume_drives_invocation_count_and_gap_on_windows():
    for volume, runs in (("low", 2), ("med", 8), ("high", 24), ("bulk", 64)):
        src = render_command_client_windows(
            _win_persona(Emit(proto="ntp", volume=volume, peer_role="domain-controller")),
            instance_id="ws01", peers={}, seed=1,
        )
        assert f"Runs={runs}" in src


# --- the probe ---------------------------------------------------------------


def test_required_commands_covers_every_emit_and_names_a_provider():
    needed = dict(required_commands(_all_protos_persona()))
    assert {"klist.exe", "gpupdate.exe", "nltest.exe", "net.exe", "w32tm.exe",
            "Resolve-DnsName", "Invoke-WebRequest", "Get-WmiObject",
            "Copy-Item", "Remove-Item"} <= set(needed)
    assert all(v.strip() for v in needed.values())


def test_the_windows_probe_reports_only_tagged_lines_and_always_exits_zero():
    probe = render_windows_command_probe(["klist.exe", "nltest.exe"])
    assert cw._WIN_PROBE_MARKER in probe
    assert "Get-Command" in probe and "exit 0" in probe
    assert probe.count(cw._MISSING_TAG) == 1


def test_a_missing_built_in_fails_loud_and_names_what_provides_it():
    """`nltest.exe` is the one that really does go missing: a Windows Server
    built-in, an RSAT/optional-feature component on some client SKUs."""
    ga = _FakeWinGA(missing_commands=["nltest.exe"], banner="MOTD line\n")
    p = _win_persona(Emit(proto="dns", peer_role="domain-controller"))
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(
            ga, 101, p, instance_id="ws01", peers=_WIN_PEERS, seed=1
        )
    msg = str(e.value)
    assert "nltest.exe" in msg and "RSAT" in msg
    # A banner line on the probe's stdout is not a missing command.
    assert "MOTD" not in msg
    # The probe runs BEFORE the launch script is written, so nothing was
    # launched against a guest that cannot run the client.
    assert not any(k.endswith("launch.ps1") for k in ga.written)


def test_a_probe_that_cannot_run_at_all_is_a_loud_failure_not_a_pass():
    class _BrokenProbeGA(_FakeWinGA):
        def shell_exec(self, vmid, command, *, timeout_s=300):
            self.shell_execs.append(command)
            if cw._WIN_PROBE_MARKER in command:
                return GuestExecResult(127, "", "powershell is unwell")
            return GuestExecResult(0, "", "")

    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(
            _BrokenProbeGA(), 101,
            _win_persona(Emit(proto="ntp", peer_role="domain-controller")),
            instance_id="ws01", peers={}, seed=1,
        )
    assert "could not probe" in str(e.value)


# --- driver lifecycle: launch / stop / heartbeat parity ----------------------


def test_windows_command_driver_starts_as_a_scheduled_task_never_start_process():
    ga = _FakeWinGA(run_exit=0)
    p = _all_protos_persona()
    result = CommandDriver().start(
        ga, 101, p, instance_id="ws01-win-member", peers=_WIN_PEERS, seed=5
    )
    paths = instance_paths("ws01-win-member", os_family="windows")
    assert result["started"] is True
    assert result["unit"] == paths["unit"]
    assert paths["script"] in ga.written
    launch_src = ga.written[paths["dir"] + "\\launch.ps1"]
    assert "Register-ScheduledTask" in launch_src
    assert "Start-ScheduledTask" in launch_src
    assert "Start-Process" not in launch_src and "nohup" not in launch_src
    # LES-174: the instance dir (which holds the log and the redirected
    # stdout/stderr the client writes) is created before the task starts.
    assert any("New-Item -ItemType Directory" in c for c in ga.shell_execs)
    # The probe ran BEFORE the launch script was written.
    assert any(cw._WIN_PROBE_MARKER in c for c in ga.shell_execs)


def test_windows_command_launch_delegates_to_the_scripted_windows_bootstrap():
    p = _win_persona(Emit(proto="ntp", peer_role="domain-controller"))
    mine = render_command_launch_windows(
        "ws01", p, script_path="C:\\axrange\\personas\\x\\client.ps1"
    )
    theirs = render_scripted_launch_windows(
        "ws01", p, script_path="C:\\axrange\\personas\\x\\client.ps1"
    )
    assert mine == theirs


def test_windows_command_start_fails_loud_when_the_task_will_not_start():
    ga = _FakeWinGA(run_exit=1)
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(
            ga, 101, _win_persona(Emit(proto="ntp", peer_role="domain-controller")),
            instance_id="ws01", peers={}, seed=1,
        )
    assert "launch failed" in str(e.value)


def test_windows_command_stop_and_heartbeat_use_the_scheduled_task_helpers():
    ga = _FakeWinGA(run_exit=0)
    p = _win_persona(Emit(proto="ntp", peer_role="domain-controller"))
    r = CommandDriver().stop(ga, 101, p, instance_id="ws01")
    assert r["stopped"] is True
    assert r["unit"] == instance_paths("ws01", os_family="windows")["unit"]
    assert any("Unregister-ScheduledTask" in c for c in ga.shell_execs)
    hb = CommandDriver().heartbeat(ga, 101, p, instance_id="ws01")
    assert hb["alive"] is True
    assert any("Get-ScheduledTask" in c for c in ga.shell_execs)


def test_windows_and_linux_command_instances_share_one_unit_naming_scheme():
    """Both OSes key every path and the task/unit name off `instance_id`, so a
    host carrying one persona per OS can never collide."""
    win = instance_paths("ws01-win-member", os_family="windows")
    lin = instance_paths("ws01-win-member")
    assert win["unit"] == lin["unit"]
    assert win["script"].endswith("client.ps1") and lin["script"].endswith("client.py")


def test_command_driver_still_raises_the_shared_error_for_an_unknown_os():
    with pytest.raises(PersonaDriverError) as e:
        CommandDriver().start(
            _FakeWinGA(), 101,
            _win_persona(Emit(proto="ntp", peer_role="domain-controller"),
                         os_family="plan9"),
            instance_id="ws01", peers={},
        )
    assert "unsupported os_family" in str(e.value)


# --- the guest client's own contract -----------------------------------------


def test_the_windows_client_logs_one_summary_line_with_the_positive_control():
    """LES-245: `ok=` is what separates "the member did 32 things" from "the
    member did nothing and nothing complained"."""
    src = render_command_client_windows(
        _all_protos_persona(), instance_id="ws01", peers=_WIN_PEERS, seed=1
    )
    for token in ("' ok=' + $ok", "' fails=' + $fails", "' timeouts=' + $timeouts",
                  "' missing_tool=' + $missing", "' runs=' + $runs"):
        assert token in src
    # The second, runtime guard for a tool that disappears after start.
    assert "Get-Command -Name $name" in src
    # A hard per-step bound: `net use` prompts and `gpupdate` can ask to log
    # off; either would park a SYSTEM task forever.
    assert "WaitForExit($TimeoutS * 1000)" in src
    assert "$proc.Kill()" in src


def test_cleanup_steps_run_even_when_the_invocation_failed():
    """A failed `net use /delete` is not a failed emit, but a LEAKED session
    would poison every later fire -- so cleanup runs on both paths and is
    never counted."""
    src = render_command_client_windows(
        _win_persona(Emit(proto="smb", peer_role="file-server")),
        instance_id="ws01", peers=_WIN_PEERS, seed=1,
    )
    # The main pass skips Always steps; the cleanup pass runs only those.
    assert "if ($step.Always) { continue }" in src
    assert "if (-not $step.Always) { continue }" in src


def test_ps_literal_refuses_a_key_or_type_it_cannot_safely_render():
    assert ps_literal({"A": "it's"}) == "@{A='it''s'}"
    assert ps_literal([1, True, None]) == "@(1,$true,$null)"
    with pytest.raises(PersonaDriverError):
        ps_literal({"bad key": 1})
    with pytest.raises(PersonaDriverError):
        ps_literal(object())


# (The "no shipped persona uses `drive: command`" content guard lives with the
# Linux table's tests above -- widened there rather than duplicated here.)
