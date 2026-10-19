"""W2a — Windows AD identity: `role: adds` DC promotion + wait-domain-ready
(docs/SLICE_WINDOWS_AD.md §6.1).

Three layers, all with the QGA channel mocked (a fake GuestAgent), mirroring the
config-slice tests; a live dev45 run verifies the real Install-ADDSForest path:

  - render   : render_windows_adds_promotion produces the promotion PowerShell.
  - backend  : ConfigBackend promotes (reboot-tolerant) + polls wait-domain-ready.
  - emission : pass-1 emits provision-app(role:adds) -> wait-domain-ready ->
               domain-ready, with the star-tail `after`/predecessor edges resolved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend
from axrange.config_exec.render import (
    ConfigRenderError,
    netbios_from_fqdn,
    render_create_domain_user,
    render_create_local_user,
    render_join_to_domain,
    render_windows_defense_agent,
    render_windows_adds_promotion,
    render_windows_set_hostname,
    render_windows_static_ip,
    windows_computer_name,
)
from axrange.ir.parser import parse_ir
from axrange.ir.schema import (
    App,
    Binding,
    DefenseEntry,
    IdentityEntry,
    InstallAction,
    NetRef,
    Node,
    Overlays,
)
from axrange.ops.registry import IdempotencyClass, OpKind, get_op
from axrange.planner import build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, AdapterTimeout, ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"
APP_SCN = FIXTURES / "ir" / "valid_app" / "scenario.yaml"


# ---------------------------------------------------------------------------
# render — render_windows_adds_promotion
# ---------------------------------------------------------------------------


def test_netbios_from_fqdn():
    assert netbios_from_fqdn("corp.local") == "CORP"
    assert netbios_from_fqdn("a-very-long-domain-label.example.com") == "A-VERY-LONG-DOM"  # ≤15, hyphens ok
    assert netbios_from_fqdn("") == "AXRANGE"


def test_promotion_script_has_feature_install_and_forest():
    ps = render_windows_adds_promotion(
        "corp.local", safe_mode_password="Pw1!", admin_password="Adm1!"
    )
    assert "Install-WindowsFeature AD-Domain-Services" in ps
    assert "Install-ADDSForest" in ps
    assert "-DomainName 'corp.local'" in ps
    assert "-DomainNetbiosName 'CORP'" in ps
    assert "-InstallDns" in ps
    # backend drives the reboot deterministically -> no auto-reboot in the script.
    assert "-NoRebootOnCompletion" in ps
    # idempotent guard: an already-promoted DC exits 0 without re-creating.
    assert "Get-ADDomain" in ps and "already" in ps.lower()
    # sets a deterministic Administrator pw -> reliable domain-admin cred for join.
    assert "net user Administrator 'Adm1!'" in ps


def test_promotion_script_escapes_single_quotes():
    ps = render_windows_adds_promotion(
        "corp.local", safe_mode_password="a'b", admin_password="c'd", netbios="X'Y"
    )
    assert "'a''b'" in ps  # safe-mode password single-quote doubled
    assert "'c''d'" in ps  # admin password single-quote doubled
    assert "'X''Y'" in ps


def test_promotion_script_rejects_non_fqdn():
    with pytest.raises(ConfigRenderError):
        render_windows_adds_promotion(
            "notdotted", safe_mode_password="Pw1!", admin_password="Adm1!"
        )


# ---------------------------------------------------------------------------
# backend — promotion + wait-domain-ready
# ---------------------------------------------------------------------------


class _FakeADGA:
    """Guest-agent fake for the AD path: a boot id that advances on
    Restart-Computer (so the reboot wait completes), and Get-ADDomain answers
    governed by `domain_serving` / `serving_after`."""

    def __init__(self, *, promote_exit=0, domain_serving=True, serving_after=0):
        self.promote_exit = promote_exit
        self.domain_serving = domain_serving
        self.serving_after = serving_after
        self.boot_id = 100
        self.execs: list[tuple] = []
        self.shells: list[tuple] = []
        self.written: dict[str, str] = {}
        self._addomain_calls = 0

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append((vmid, tuple(argv)))
        if argv[:1] == ["powershell"]:  # the -File promotion run
            return GuestExecResult(
                self.promote_exit, "promoted",
                "" if self.promote_exit == 0 else "ADDS boom",
            )
        return GuestExecResult(0, "", "")

    def shell_exec(self, vmid, command, *, input_data=None, timeout_s=300):
        self.shells.append((vmid, command))
        if "Restart-Computer" in command:
            self.boot_id += 1  # the reboot
            return GuestExecResult(0, "", "")
        if "LastBootUpTime" in command:
            return GuestExecResult(0, str(self.boot_id), "")
        if "Get-ADDomain" in command:
            self._addomain_calls += 1
            ok = self.domain_serving and self._addomain_calls > self.serving_after
            return GuestExecResult(0 if ok else 1, "", "" if ok else "not serving")
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        self.written[path] = content


def _ctx():
    return ExecutionContext(
        plan_id="p", deploy_id="d",
        cluster=ClusterContext(default_node="px1"), placement={},
    )


def _backend(fake) -> ConfigBackend:
    return ConfigBackend(
        AdapterConfig(), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )


def _provision_op() -> OpInstance:
    return OpInstance(
        op_id="p:provision-app[app=dc-ad,host=dc01]",
        op_name="provision-app", op_catalog_id="OP-APP-010",
        kind=OpKind.ACTION, scope={"app": "dc-ad", "host": "dc01"},
        idempotency_class=IdempotencyClass.CONFIG,
    )


def _wait_domain_op(timeout_s: float = 600) -> OpInstance:
    return OpInstance(
        op_id="p:wait-domain-ready[dc_app=dc-ad,domain=corp.local]",
        op_name="wait-domain-ready", op_catalog_id="OP-OVL-010",
        kind=OpKind.WAIT, scope={"domain": "corp.local", "dc_app": "dc-ad"},
        idempotency_class=IdempotencyClass.CONFIG, timeout_s=timeout_s,
    )


def test_role_adds_promotes_dc_and_reboots():
    fake = _FakeADGA()
    out = _backend(fake).run(_provision_op(), {
        "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
        "install_actions": [{"role": "adds"}], "domain_fqdn": "corp.local",
    }, _ctx())
    assert out == {"provisioned": True, "tasks": 1}
    ps = next(c for p, c in fake.written.items() if p.endswith("-adds.ps1"))
    assert "Install-ADDSForest" in ps and "corp.local" in ps
    # reboot-tolerant: the box was rebooted and the wait saw it come back.
    assert any("Restart-Computer" in c for _v, c in fake.shells)


def test_role_adds_after_packages():
    """An app mixing pkg + role: adds installs the package, then promotes."""
    fake = _FakeADGA()
    out = _backend(fake).run(_provision_op(), {
        "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
        "install_actions": [{"pkg": "git"}, {"role": "adds"}],
        "domain_fqdn": "corp.local",
    }, _ctx())
    assert out == {"provisioned": True, "tasks": 2}
    assert any(p.endswith(".ps1") and "choco install" in c for p, c in fake.written.items())
    assert any(p.endswith("-adds.ps1") for p in fake.written)


def test_role_adds_uses_cfg_default_password_for_safe_mode_and_admin(monkeypatch):
    """docs/WORK_ORDER_access_facts.md Part A: no `safe_mode_password` input
    -> both the DSRM safe-mode password AND the deterministic domain
    Administrator password come from `cfg.default_password`, not a
    hardcoded module constant."""
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="CfgDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_provision_op(), {
        "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
        "install_actions": [{"role": "adds"}], "domain_fqdn": "corp.local",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith("-adds.ps1"))
    assert "CfgDefaultPw!" in ps


def test_role_adds_explicit_safe_mode_password_wins(monkeypatch):
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="CfgDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_provision_op(), {
        "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
        "install_actions": [{"role": "adds"}], "domain_fqdn": "corp.local",
        "safe_mode_password": "ExplicitSafePw!",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith("-adds.ps1"))
    # The explicit safe_mode_password wins for DSRM; admin_password has no
    # per-binding override channel today, so it still uses cfg.default_password
    # -- both values legitimately appear in the same rendered script.
    assert "ExplicitSafePw!" in ps
    assert "CfgDefaultPw!" in ps


def test_role_adds_requires_domain():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA()).run(_provision_op(), {
            "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
            "install_actions": [{"role": "adds"}],  # no domain_fqdn
        }, _ctx())
    assert exc.value.error_class == "windows-adds-no-domain"


def test_role_adds_promotion_failure_errors():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA(promote_exit=1)).run(_provision_op(), {
            "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
            "install_actions": [{"role": "adds"}], "domain_fqdn": "corp.local",
        }, _ctx())
    assert exc.value.error_class == "windows-adds-failed"


def test_backend_rejects_non_adds_role():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA()).run(_provision_op(), {
            "vmid": 9, "app_id": "dc-ad", "os_family": "windows",
            "install_actions": [{"role": "dns"}], "domain_fqdn": "corp.local",
        }, _ctx())
    assert exc.value.error_class == "windows-role-unsupported"


def test_wait_domain_ready_polls_until_serving():
    fake = _FakeADGA(serving_after=2)  # AD not up until the 3rd probe
    out = _backend(fake).run(_wait_domain_op(), {
        "dc_vmid": 9, "domain_fqdn": "corp.local", "os_family": "windows",
    }, _ctx())
    assert out == {"domain_serving": True}
    assert sum("Get-ADDomain" in c for _v, c in fake.shells) >= 3


def test_wait_domain_ready_times_out():
    with pytest.raises(AdapterTimeout):
        _backend(_FakeADGA(domain_serving=False)).run(_wait_domain_op(timeout_s=0.02), {
            "dc_vmid": 9, "domain_fqdn": "corp.local", "os_family": "windows",
        }, _ctx())


def test_wait_domain_ready_checks_adws_and_rides_out_flap():
    """The probe checks the ADWS service, and an ADWS flap (success then failure)
    resets the consecutive-success streak so the domain isn't declared ready
    prematurely (CHANGELOG 2026-06-24)."""

    class _FlapGA:
        # 2 successes, then a flap, then a stable run — ready only after N-in-a-row.
        _seq = [True, True, False, True, True, True, True, True]

        def __init__(self):
            self.i = 0
            self.last_probe = None

        def shell_exec(self, vmid, command, *, input_data=None, timeout_s=300):
            self.last_probe = command
            ok = self._seq[min(self.i, len(self._seq) - 1)]
            self.i += 1
            return GuestExecResult(0 if ok else 1, "", "" if ok else "ADWS not running")

    fake = _FlapGA()
    out = _backend(fake).run(_wait_domain_op(), {
        "dc_vmid": 9, "domain_fqdn": "corp.local", "os_family": "windows",
    }, _ctx())
    assert out == {"domain_serving": True}
    # the probe gates on the ADWS service, not just Get-ADDomain
    assert "Get-Service" in fake.last_probe and "ADWS" in fake.last_probe
    # the flap (index 2) reset the streak: ready only after the trailing 3-in-a-row
    # -> at least 6 probes (2 ok + 1 flap + 3 ok).
    assert fake.i >= 6


def test_wait_domain_ready_is_config_class_no_ansible_pool():
    """Routes to ConfigBackend (config-class) but, being a poll, must not hold the
    ansible-pool throughput slot — only vm:$dc_vmid (registry-enforced too)."""
    d = get_op("wait-domain-ready")
    assert d.idempotency_class == IdempotencyClass.CONFIG
    assert "ansible-pool" not in d.resource_categories


# ---------------------------------------------------------------------------
# emission — pass-1 emits the trio with resolved edges
# ---------------------------------------------------------------------------


def _dc_plan(manifests, *, apps, bindings, identities=None, extra_nodes=None,
             defense=None):
    """Plan the valid_app scenario with its apps/bindings swapped for a DC app
    (and optional overlays.identity / overlays.defense entries + extra nodes)."""
    parsed = parse_ir(path=str(APP_SCN))
    parsed.ir.layers.apps = apps
    parsed.ir.bindings = bindings
    if extra_nodes:
        parsed.ir.layers.infra.nodes.extend(extra_nodes)
    if identities is not None or defense is not None:
        parsed.ir.overlays = Overlays(identity=identities, defense=defense)
    ctx = ClusterContext.single_node("pxtest1", sensor_transport_ip="10.255.255.10")
    plan = build_plan(parsed.ir, manifests, ctx, raw_ir=parsed.raw,
                      scenario_dir=str(APP_SCN.parent))
    plan.freeze()
    return plan


def test_role_adds_emits_provision_wait_and_barrier(manifests):
    plan = _dc_plan(
        manifests,
        apps=[App(id="dc-ad", install=[InstallAction(role="adds")],
                  provides=[{"domain": "corp.local"}])],
        bindings=[Binding(app="dc-ad", host="h-a")],
    )
    prov = [o for o in plan.ops.values() if o.op_name == "provision-app"]
    assert len(prov) == 1
    assert prov[0].inputs_resolved["install_actions"] == [{"role": "adds"}]
    assert prov[0].inputs_resolved["domain_fqdn"] == "corp.local"

    wdr = [o for o in plan.ops.values() if o.op_name == "wait-domain-ready"]
    assert len(wdr) == 1
    assert wdr[0].scope == {"domain": "corp.local", "dc_app": "dc-ad"}
    assert wdr[0].idempotency_class == IdempotencyClass.CONFIG

    barr = [b for b in plan.barriers.values() if b.barrier_name == "domain-ready"]
    assert len(barr) == 1
    assert barr[0].scope == {"domain": "corp.local"}

    # The star-tail fix: provision-app -> wait-domain-ready -> domain-ready resolve.
    assert prov[0].op_id in set(plan.predecessors(wdr[0].op_id))
    assert wdr[0].op_id in set(plan.predecessors(barr[0].op_id))


def test_role_adds_without_provides_domain_rejected(manifests):
    with pytest.raises(Exception) as e:
        _dc_plan(
            manifests,
            apps=[App(id="dc-ad", install=[InstallAction(role="adds")])],  # no provides
            bindings=[Binding(app="dc-ad", host="h-a")],
        )
    msg = str(e.value).lower()
    assert "provides" in msg or "domain" in msg


def test_non_adds_role_rejected_at_emit(manifests):
    with pytest.raises(Exception) as e:
        _dc_plan(
            manifests,
            apps=[App(id="r", install=[InstallAction(role="iis")])],
            bindings=[Binding(app="r", host="h-a")],
        )
    assert "adds" in str(e.value).lower()


def test_plain_pkg_app_unchanged_no_identity_ops(manifests):
    """A normal pkg app still emits exactly one provision-app and no AD ops."""
    plan = _dc_plan(
        manifests,
        apps=[App(id="web", install=[InstallAction(pkg="nginx")])],
        bindings=[Binding(app="web", host="h-a")],
    )
    assert len([o for o in plan.ops.values() if o.op_name == "provision-app"]) == 1
    assert not [o for o in plan.ops.values() if o.op_name == "wait-domain-ready"]
    assert not [b for b in plan.barriers.values() if b.barrier_name == "domain-ready"]


# ---------------------------------------------------------------------------
# W2b — create-domain-user (domain branch): render / backend / emission
# ---------------------------------------------------------------------------


def test_render_create_domain_user():
    ps = render_create_domain_user("alice", password="Pw1!", groups=["Domain Admins", "IT"])
    assert "New-ADUser" in ps and "-Enabled $true" in ps
    assert "Get-ADUser -Filter" in ps  # idempotent guard — safe redo
    assert "Add-ADGroupMember -Identity 'Domain Admins'" in ps
    assert "Add-ADGroupMember -Identity 'IT'" in ps
    assert "ConvertTo-SecureString 'Pw1!'" in ps


def test_render_create_domain_user_sanitizes_sam():
    import re
    ps = render_create_domain_user("alice@corp/very-long-name-exceeding-twenty", password="P")
    sam = re.search(r"\$sam='([^']*)'", ps).group(1)
    assert len(sam) <= 20 and "/" not in sam and "@" not in sam  # valid SamAccountName


def test_render_create_domain_user_escapes_quotes():
    ps = render_create_domain_user("o'brien", password="a'b")
    assert "'o''brien'" in ps and "'a''b'" in ps  # name + password single-quote escaped


def test_render_create_domain_user_empty_id_errors():
    with pytest.raises(ConfigRenderError):
        render_create_domain_user("", password="P")


# --- Vulnerable-AD / SPN-OU modeling (§9) ---

def test_render_domain_user_spn_is_kerberoastable():
    """An SPN account: the SPN is set + the account forced to RC4 so its Kerberoast
    TGS is crackable (and trips the RC4 IDS rule)."""
    ps = render_create_domain_user(
        "svc-sql", password="Summer2024", spn="MSSQLSvc/db.corp.lab:1433")
    assert "-ServicePrincipalNames 'MSSQLSvc/db.corp.lab:1433'" in ps
    assert "msDS-SupportedEncryptionTypes'=4" in ps  # RC4 only


def test_render_domain_user_ou_created_and_placed():
    ps = render_create_domain_user(
        "svc-sql", password="P", ou="Servers", domain_fqdn="corp.lab")
    assert "New-ADOrganizationalUnit -Name 'Servers' -Path 'DC=corp,DC=lab'" in ps
    assert "-Path 'OU=Servers,DC=corp,DC=lab'" in ps


def test_render_domain_user_asrep_roastable():
    ps = render_create_domain_user("jdoe", password="P", asrep_roastable=True)
    assert "Set-ADAccountControl" in ps and "-DoesNotRequirePreAuth $true" in ps


def test_domain_identity_weakening_threaded(manifests):
    """Vulnerable-AD fields on an identity flow into create-domain-user inputs."""
    apps, bindings = _dc_apps_bindings()
    plan = _dc_plan(
        manifests, apps=apps, bindings=bindings,
        identities=[IdentityEntry(id="svc-sql", domain="corp.axr.lab",
                                  groups=["Domain Users"], password="Summer2024",
                                  spn="MSSQLSvc/db:1433", ou="Servers")],
    )
    op = next(o for o in plan.ops.values()
              if o.op_name == "create-domain-user" and o.scope.get("identity") == "svc-sql")
    assert op.inputs_resolved["spn"] == "MSSQLSvc/db:1433"
    assert op.inputs_resolved["ou"] == "Servers"
    assert op.inputs_resolved["password"] == "Summer2024"


# --- dangerous-ACL / delegation weakening (BloodHound edges) ---

def test_render_domain_user_acl_genericall():
    ps = render_create_domain_user(
        "intern", password="P", acl=[{"right": "GenericAll", "target": "helpdesk"}])
    assert "ActiveDirectoryRights]::GenericAll" in ps
    assert "AddAccessRule" in ps and 'Set-Acl' in ps
    assert "Name -eq 'helpdesk'" in ps  # waits for / resolves the target object


def test_render_domain_user_acl_forcechangepassword_uses_guid():
    ps = render_create_domain_user(
        "intern", password="P", acl=[{"right": "ForceChangePassword", "target": "ceo"}])
    assert "ExtendedRight" in ps
    assert "00299570-246d-11d0-a768-00aa006e0529" in ps  # the control-access GUID


def test_render_domain_user_unconstrained_delegation():
    ps = render_create_domain_user("websrv", password="P", delegation="unconstrained")
    assert "-TrustedForDelegation $true" in ps


def test_render_domain_user_constrained_delegation():
    ps = render_create_domain_user(
        "websrv", password="P", delegation_to=["CIFS/fileserver.corp.lab"])
    assert "msDS-AllowedToDelegateTo" in ps and "CIFS/fileserver.corp.lab" in ps


def test_render_domain_user_unsupported_acl_right_skipped():
    ps = render_create_domain_user(
        "intern", password="P", acl=[{"right": "Bogus", "target": "x"}])
    assert "unsupported (skipped)" in ps


def test_domain_identity_acl_delegation_threaded(manifests):
    apps, bindings = _dc_apps_bindings()
    plan = _dc_plan(
        manifests, apps=apps, bindings=bindings,
        identities=[IdentityEntry(id="intern", domain="corp.axr.lab",
                                  acl=[{"right": "GenericAll", "target": "helpdesk"}],
                                  delegation="unconstrained")],
    )
    op = next(o for o in plan.ops.values()
              if o.op_name == "create-domain-user" and o.scope.get("identity") == "intern")
    assert op.inputs_resolved["acl"] == [{"right": "GenericAll", "target": "helpdesk"}]
    assert op.inputs_resolved["delegation"] == "unconstrained"


def _create_user_op() -> OpInstance:
    return OpInstance(
        op_id="p:create-domain-user[domain=corp.axr.lab,host=dc01,identity=alice]",
        op_name="create-domain-user", op_catalog_id="OP-OVL-020",
        kind=OpKind.ACTION,
        scope={"identity": "alice", "host": "dc01", "domain": "corp.axr.lab"},
        idempotency_class=IdempotencyClass.CONFIG,
    )


def test_create_domain_user_runs_new_aduser():
    fake = _FakeADGA()
    out = _backend(fake).run(_create_user_op(), {
        "target_vmid": 9, "identity_id": "alice", "domain_fqdn": "corp.axr.lab",
        "groups": ["Domain Admins"], "os_family": "windows",
    }, _ctx())
    assert out == {"user_created": True}
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "New-ADUser" in ps and "alice" in ps and "Domain Admins" in ps
    assert any(a[:1] == ("powershell",) for _v, a in fake.execs)


def test_create_domain_user_uses_cfg_default_password():
    """docs/WORK_ORDER_access_facts.md Part A: no `password` input -> falls
    back to `cfg.default_password` (was the `_WINDOWS_DEFAULT_USER_PW`
    module constant)."""
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="UserDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_create_user_op(), {
        "target_vmid": 9, "identity_id": "alice", "domain_fqdn": "corp.axr.lab",
        "groups": ["Domain Admins"], "os_family": "windows",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "UserDefaultPw!" in ps


def test_create_domain_user_explicit_password_wins():
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="UserDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_create_user_op(), {
        "target_vmid": 9, "identity_id": "alice", "domain_fqdn": "corp.axr.lab",
        "groups": ["Domain Admins"], "os_family": "windows",
        "password": "AliceExplicitPw!",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "AliceExplicitPw!" in ps
    assert "UserDefaultPw!" not in ps


def test_create_local_user_uses_cfg_default_password_windows():
    """OP-OVL-020's LOCAL branch (no `domain_fqdn` -> New-LocalUser, W2/§6.2)
    falls back to `cfg.default_password` too -- same fallback the domain
    branch uses, just a different render/backend path."""
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="LocalDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_create_user_op(), {
        "target_vmid": 9, "identity_id": "bob", "os_family": "windows",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "LocalDefaultPw!" in ps


def test_create_domain_user_failure_errors():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA(promote_exit=1)).run(_create_user_op(), {
            "target_vmid": 9, "identity_id": "alice", "domain_fqdn": "corp.axr.lab",
        }, _ctx())
    assert exc.value.error_class == "windows-user-create-failed"


def _dc_apps_bindings():
    return (
        [App(id="dc-ad", install=[InstallAction(role="adds")],
             provides=[{"domain": "corp.axr.lab"}])],
        [Binding(app="dc-ad", host="h-a")],
    )


def test_domain_identity_emits_create_domain_user(manifests):
    apps, bindings = _dc_apps_bindings()
    plan = _dc_plan(
        manifests, apps=apps, bindings=bindings,
        identities=[IdentityEntry(id="alice", domain="corp.axr.lab", groups=["Domain Admins"])],
    )
    cdu = [o for o in plan.ops.values() if o.op_name == "create-domain-user"]
    assert len(cdu) == 1
    op = cdu[0]
    assert op.scope == {"identity": "alice", "host": "h-a", "domain": "corp.axr.lab"}
    assert op.inputs_resolved["domain_fqdn"] == "corp.axr.lab"
    assert op.inputs_resolved["target_host_id"] == "h-a"  # the role:adds host (DC)
    assert op.inputs_resolved["groups"] == ["Domain Admins"]
    assert op.idempotency_class == IdempotencyClass.CONFIG
    # gated on domain-ready[corp.axr.lab] (the W2a barrier resolves as a predecessor)
    barr = [b for b in plan.barriers.values() if b.barrier_name == "domain-ready"]
    assert len(barr) == 1
    assert barr[0].op_id in set(plan.predecessors(op.op_id))


def test_external_identity_emits_no_op(manifests):
    apps, bindings = _dc_apps_bindings()
    plan = _dc_plan(
        manifests, apps=apps, bindings=bindings,
        identities=[IdentityEntry(id="attacker", external=True)],
    )
    assert not [o for o in plan.ops.values() if o.op_name == "create-domain-user"]


def test_local_identity_no_host_rejected(manifests):
    """A no-`domain:` identity with no `host:` cannot be placed — clear error."""
    apps, bindings = _dc_apps_bindings()
    with pytest.raises(Exception) as e:
        _dc_plan(manifests, apps=apps, bindings=bindings,
                 identities=[IdentityEntry(id="localguy")])  # no domain, no host
    assert "host:" in str(e.value).lower() or "local user" in str(e.value).lower()


def test_local_identity_unknown_host_rejected(manifests):
    apps, bindings = _dc_apps_bindings()
    with pytest.raises(Exception) as e:
        _dc_plan(manifests, apps=apps, bindings=bindings,
                 identities=[IdentityEntry(id="bob", host="nope")])
    assert "not a node" in str(e.value).lower()


def test_local_identity_windows_emits_create_user(manifests):
    """A local user on a Windows host: create-domain-user with no domain_fqdn,
    os_family windows, gated on host-ready[$host] (not domain-ready)."""
    apps, bindings = _dc_apps_bindings()
    win = Node(id="ws", kind="host", nets=[NetRef(net="corp", ip="10.10.0.20")],
               image="windows-server-2022@v1", requires=["windows"])
    plan = _dc_plan(manifests, apps=apps, bindings=bindings, extra_nodes=[win],
                    identities=[IdentityEntry(id="wsadmin", host="ws",
                                              groups=["Administrators"])])
    cdu = [o for o in plan.ops.values()
           if o.op_name == "create-domain-user" and o.scope.get("identity") == "wsadmin"]
    assert len(cdu) == 1
    op = cdu[0]
    assert op.scope == {"identity": "wsadmin", "host": "ws"}
    assert "domain_fqdn" not in op.inputs_resolved      # local-user marker
    assert op.inputs_resolved["target_host_id"] == "ws"
    assert op.inputs_resolved["os_family"] == "windows"
    assert op.inputs_resolved["groups"] == ["Administrators"]
    # gated on host-ready[ws], NOT domain-ready
    hr = next(b for b in plan.barriers.values()
              if b.barrier_name == "host-ready" and b.scope.get("host") == "ws")
    assert hr.op_id in set(plan.predecessors(op.op_id))


def test_local_identity_linux_emits_create_user(manifests):
    """A local user on a Linux host resolves os_family linux (useradd path)."""
    apps, bindings = _dc_apps_bindings()  # h-a is the linux base node (debian)
    plan = _dc_plan(manifests, apps=apps, bindings=bindings,
                    identities=[IdentityEntry(id="opsuser", host="h-a",
                                              groups=["sudo"])])
    cdu = [o for o in plan.ops.values()
           if o.op_name == "create-domain-user" and o.scope.get("identity") == "opsuser"]
    assert len(cdu) == 1
    assert cdu[0].inputs_resolved["os_family"] == "linux"
    assert "domain_fqdn" not in cdu[0].inputs_resolved


# --- local-user render (New-LocalUser / useradd) ---

def test_render_windows_local_user():
    ps = render_create_local_user("wsadmin", password="Pw1!",
                                  groups=["Administrators"], os_family="windows")
    assert "New-LocalUser" in ps
    assert "Get-LocalUser" in ps          # idempotent guard
    assert "Add-LocalGroupMember -Group 'Administrators'" in ps


def test_render_linux_local_user():
    sh = render_create_local_user("opsuser", password="Pw1!",
                                  groups=["sudo"], os_family="linux")
    assert "useradd" in sh
    assert "id 'opsuser'" in sh            # idempotent guard
    assert "chpasswd" in sh
    assert "usermod -aG 'sudo' 'opsuser'" in sh


def test_render_local_user_empty_id_errors():
    with pytest.raises(ConfigRenderError):
        render_create_local_user("", password="x", os_family="windows")


def test_render_windows_local_user_sanitizes_name():
    # reserved chars stripped, <=20
    ps = render_create_local_user("bad\\user@dom", password="x", os_family="windows")
    assert "baduserdom" in ps and "\\" not in ps.split("\n")[1]


# ---------------------------------------------------------------------------
# Host-side AD detection — install-defense-agent Windows branch (Sysmon/Wazuh
# substrate: AD audit subcategories + DCSync SACL), gated on domain-ready.
# ---------------------------------------------------------------------------

def test_render_windows_defense_agent_enables_ad_auditing():
    ps = render_windows_defense_agent({"id": "dc-audit", "tool": "sysmon"},
                                      config_content=None)
    # Kerberos Service Ticket Operations (4769 — Kerberoast) + Auth Service (4768)
    assert "{0CCE9240-69AE-11D9-BED3-505054503030}" in ps
    assert "{0CCE9242-69AE-11D9-BED3-505054503030}" in ps
    assert "auditpol /set /subcategory" in ps
    # DCSync: the replication extended-right GUIDs + a domain-root SACL
    assert "1131f6aa-9c07-11d1-f79f-00c04fc2dcd2" in ps
    assert "AddAuditRule" in ps and "AD:$dn" in ps
    # best-effort baked-EDR service start
    assert "Get-Service" in ps and ("Sysmon" in ps or "WazuhSvc" in ps)


def test_render_windows_defense_agent_drops_config_when_present():
    ps = render_windows_defense_agent({"id": "dc-sysmon", "tool": "sysmon"},
                                      config_content="<Sysmon><EventFiltering/></Sysmon>")
    assert "FromBase64String" in ps and "dc-sysmon.xml" in ps


def test_defense_agent_on_dc_is_windows_and_gates_on_domain_ready(manifests):
    """A defense host agent on the DC → install-defense-agent scoped with `domain`
    (so domain-ready resolves) + os_family windows (post-promotion auditing)."""
    win_dc = Node(id="dc01", kind="host", nets=[NetRef(net="corp", ip="10.10.0.10")],
                  image="windows-server-2022@v1", requires=["windows"])
    plan = _dc_plan(
        manifests,
        apps=[App(id="dc-ad", install=[InstallAction(role="adds")],
                  provides=[{"domain": "corp.axr.lab"}])],
        bindings=[Binding(app="dc-ad", host="dc01")],
        extra_nodes=[win_dc],
        defense=[DefenseEntry(id="dc-audit", tool="sysmon", installed_on=["dc01"])],
    )
    # Default-on (2026-07-11): h-a (no explicit wazuh entry) and dc01 (explicit
    # tool=sysmon, not wazuh-agent -- no dedup collision) BOTH also get a
    # baked-in default Wazuh agent now, alongside the explicit dc-audit entry
    # this test cares about -- filter to it specifically.
    ida = [o for o in plan.ops.values() if o.op_name == "install-defense-agent"]
    assert len(ida) == 3
    op = next(o for o in ida if o.inputs_resolved["defense_id"] == "dc-audit")
    assert op.scope == {"defense": "dc-audit", "host": "dc01", "domain": "corp.axr.lab"}
    assert op.inputs_resolved["os_family"] == "windows"
    dr = next(b for b in plan.barriers.values() if b.barrier_name == "domain-ready")
    assert dr.op_id in set(plan.predecessors(op.op_id))
    # The DC's own DEFAULT Wazuh entry is also domain-scoped (Fix #1, this
    # session) -- not just the hand-authored one.
    default_dc = next(o for o in ida if o.inputs_resolved["defense_id"] == "default-defense-dc01")
    assert default_dc.scope.get("domain") == "corp.axr.lab"


def test_defense_agent_on_non_dc_carries_no_domain(manifests):
    """A defense agent on a non-DC host → no `domain` in scope (domain-ready
    pattern drops), os_family from the host image."""
    apps, bindings = _dc_apps_bindings()  # dc-ad on h-a
    lx = Node(id="soc", kind="host", nets=[NetRef(net="corp", ip="10.10.0.50")],
              image="debian-12-cloud@v1", requires=["linux"])
    plan = _dc_plan(
        manifests, apps=apps, bindings=bindings, extra_nodes=[lx],
        defense=[DefenseEntry(id="soc-edr", tool="wazuh-agent", installed_on=["soc"])],
    )
    op = next(o for o in plan.ops.values()
              if o.op_name == "install-defense-agent" and o.scope.get("host") == "soc")
    assert "domain" not in op.scope
    assert op.inputs_resolved["os_family"] == "linux"


def test_identity_without_dc_rejected(manifests):
    with pytest.raises(Exception) as e:
        _dc_plan(
            manifests,
            apps=[App(id="web", install=[InstallAction(pkg="nginx")])],  # no DC
            bindings=[Binding(app="web", host="h-a")],
            identities=[IdentityEntry(id="alice", domain="corp.axr.lab")],
        )
    assert "domain controller" in str(e.value).lower()


# ---------------------------------------------------------------------------
# join-host-to-domain (explicit node.joins:): render / backend / emission
# ---------------------------------------------------------------------------


def test_render_join_to_domain():
    ps = render_join_to_domain("corp.lab", dc_ip="10.0.0.10", admin_password="Pw1!")
    assert "Add-Computer -DomainName 'corp.lab'" in ps
    assert "Set-DnsClientServerAddress" in ps and "10.0.0.10" in ps
    assert "CORP\\Administrator" in ps  # NetBIOS\Administrator domain-admin cred
    assert "already joined" in ps  # idempotent guard
    assert "ConvertTo-SecureString 'Pw1!'" in ps


def test_render_join_rejects_non_fqdn():
    with pytest.raises(ConfigRenderError):
        render_join_to_domain("notdotted", dc_ip="10.0.0.10", admin_password="P")


def test_render_join_escapes_quotes():
    ps = render_join_to_domain("corp.lab", dc_ip="10.0.0.10", admin_password="a'b")
    assert "'a''b'" in ps


def _join_op() -> OpInstance:
    return OpInstance(
        op_id="p:join-host-to-domain[domain=corp.lab,host=ws01]",
        op_name="join-host-to-domain", op_catalog_id="OP-OVL-030",
        kind=OpKind.ACTION, scope={"host": "ws01", "domain": "corp.lab"},
        idempotency_class=IdempotencyClass.CONFIG,
    )


def test_join_runs_and_reboots():
    fake = _FakeADGA()
    out = _backend(fake).run(_join_op(), {
        "vmid": 9, "host_id": "ws01", "domain_fqdn": "corp.lab",
        "dc_ip": "10.0.0.10", "os_family": "windows",
    }, _ctx())
    assert out == {"host_joined": True}
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "Add-Computer" in ps and "corp.lab" in ps
    assert any("Restart-Computer" in c for _v, c in fake.shells)  # join reboot driven


def test_join_uses_cfg_default_password_for_domain_admin_credential():
    """docs/WORK_ORDER_access_facts.md Part A: no per-binding override channel
    exists for the domain Administrator credential join-host-to-domain
    authenticates with -- always `cfg.default_password`."""
    fake = _FakeADGA()
    backend = ConfigBackend(
        AdapterConfig(default_password="JoinDefaultPw!"), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
        sleep_fn=lambda *_a: None, actor_poll_interval_s=0.001,
    )
    backend.run(_join_op(), {
        "vmid": 9, "host_id": "ws01", "domain_fqdn": "corp.lab",
        "dc_ip": "10.0.0.10", "os_family": "windows",
    }, _ctx())
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "JoinDefaultPw!" in ps


def test_join_requires_domain_and_dc_ip():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA()).run(_join_op(), {
            "vmid": 9, "host_id": "ws01", "os_family": "windows",  # no domain/dc_ip
        }, _ctx())
    assert exc.value.error_class == "windows-join-no-domain"


def test_join_failure_errors():
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeADGA(promote_exit=1)).run(_join_op(), {
            "vmid": 9, "host_id": "ws01", "domain_fqdn": "corp.lab", "dc_ip": "10.0.0.10",
        }, _ctx())
    assert exc.value.error_class == "windows-join-failed"


def _joining_ws(domain="corp.axr.lab"):
    return Node(id="ws", kind="host", nets=[NetRef(net="corp", ip="10.10.0.20")],
                image="windows-server-2022@v1", requires=["windows"], joins=domain)


def test_node_joins_emits_join_op(manifests):
    apps, bindings = _dc_apps_bindings()  # dc-ad role:adds provides corp.axr.lab on h-a
    plan = _dc_plan(manifests, apps=apps, bindings=bindings, extra_nodes=[_joining_ws()])
    j = [o for o in plan.ops.values() if o.op_name == "join-host-to-domain"]
    assert len(j) == 1
    op = j[0]
    assert op.scope == {"host": "ws", "domain": "corp.axr.lab"}
    assert op.inputs_resolved["domain_fqdn"] == "corp.axr.lab"
    assert op.inputs_resolved["dc_ip"] == "10.10.0.10"  # h-a's IP (the DC) for DNS
    assert op.idempotency_class == IdempotencyClass.CONFIG
    # gated on domain-ready[corp.axr.lab] + host-ready[ws]
    preds = set(plan.predecessors(op.op_id))
    dr = next(b for b in plan.barriers.values() if b.barrier_name == "domain-ready")
    hr = next(b for b in plan.barriers.values()
              if b.barrier_name == "host-ready" and b.scope.get("host") == "ws")
    assert dr.op_id in preds and hr.op_id in preds


def test_node_joins_non_windows_rejected(manifests):
    apps, bindings = _dc_apps_bindings()
    lx = Node(id="lx", kind="host", nets=[NetRef(net="corp", ip="10.10.0.20")],
              image="debian-12-cloud@v1", requires=["linux"], joins="corp.axr.lab")
    with pytest.raises(Exception) as e:
        _dc_plan(manifests, apps=apps, bindings=bindings, extra_nodes=[lx])
    assert "windows-only" in str(e.value).lower()


def test_node_joins_no_dc_rejected(manifests):
    with pytest.raises(Exception) as e:
        _dc_plan(manifests,
                 apps=[App(id="web", install=[InstallAction(pkg="nginx")])],  # no DC
                 bindings=[Binding(app="web", host="h-a")],
                 extra_nodes=[_joining_ws()])
    assert "domain controller" in str(e.value).lower()


# ---------------------------------------------------------------------------
# W1 substrate — Windows static-IP-over-QGA (§5; live dev45 2026-06-18)
# A Windows clone's first-boot path leaves the NIC on DHCP and ships no
# cloudbase-init, so the adapter pins the IR static IP over QGA in wait-os-ready.
# ---------------------------------------------------------------------------


def test_render_windows_static_ip_idempotent_and_sets_gw():
    s = render_windows_static_ip("10.20.0.5", prefix=24, gateway="10.20.0.1")
    # Idempotent early-exit on an already-present address.
    assert "ip already set" in s
    assert "-IPAddress '10.20.0.5'" in s
    assert "-PrefixLength 24" in s
    assert "-DefaultGateway '10.20.0.1'" in s
    # Clears DHCP + stale addr/route before setting.
    assert "-Dhcp Disabled" in s
    assert "Remove-NetIPAddress" in s


def test_render_windows_static_ip_no_gateway_omits_gw():
    s = render_windows_static_ip("10.20.0.5", prefix=25)
    assert "-PrefixLength 25" in s
    assert "DefaultGateway" not in s


def test_render_windows_static_ip_rejects_bad_prefix():
    with pytest.raises(ConfigRenderError):
        render_windows_static_ip("10.20.0.5", prefix=99)
    with pytest.raises(ConfigRenderError):
        render_windows_static_ip("", prefix=24)


def test_wait_os_ready_threads_static_ip_for_windows(manifests):
    """pass-1 threads the mgmt net's ip/prefix/gw into a Windows host's
    wait-os-ready so the adapter can pin it over QGA (isolated nets have no
    DHCP; the template ships no cloudbase-init to consume ipconfigN)."""
    apps, bindings = _dc_apps_bindings()  # DC on h-a @ 10.10.0.10
    plan = _dc_plan(manifests, apps=apps, bindings=bindings,
                    extra_nodes=[_joining_ws()])  # ws @ 10.10.0.20
    wor = {tuple(sorted(o.scope.items())): o
           for o in plan.ops.values() if o.op_name == "wait-os-ready"}
    ws = wor[(("host", "ws"),)]  # the Windows joining host
    assert ws.inputs_resolved["os_family"] == "windows"
    assert ws.inputs_resolved["hostname"] == "ws"  # renamed to node id over QGA
    assert ws.inputs_resolved["static_ip"] == "10.10.0.20"
    assert ws.inputs_resolved["static_prefix"] == 24
    assert ws.inputs_resolved["static_gateway"] == "10.10.0.1"
    # A Linux host (h-a, the debian-imaged DC app host in this fixture) gets no
    # static-IP/hostname threading — Linux cloud-init applies ipconfigN +
    # hostname itself.
    dc = wor[(("host", "h-a"),)]
    assert dc.inputs_resolved["os_family"] == "linux"
    assert "static_ip" not in dc.inputs_resolved
    assert "hostname" not in dc.inputs_resolved


def test_render_windows_set_hostname_idempotent_and_renames():
    s = render_windows_set_hostname("member01")
    assert "-ieq 'member01'" in s            # idempotent guard
    assert "AXR_RENAMED no" in s
    assert "Rename-Computer -NewName 'member01'" in s
    assert "AXR_RENAMED yes" in s


def test_windows_computer_name_sanitizes():
    assert windows_computer_name("member01") == "member01"
    assert windows_computer_name("db-srv.corp.lab") == "db-srv"   # first label
    assert windows_computer_name("a_very_long_hostname_indeed") == "a-very-long-hos"  # <=15
    assert windows_computer_name("web_01") == "web-01"            # underscore -> hyphen


def test_render_windows_set_hostname_rejects_empty():
    with pytest.raises(ConfigRenderError):
        render_windows_set_hostname("___")  # sanitizes to empty
