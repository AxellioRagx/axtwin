"""Tests for the config-class execution slice (axrange/config_exec/).

The QGA channel is mocked (a fake GuestAgent); a live cluster run verifies the
real ansible-in-guest path (docs/SLICE_CONFIG.md §8). Mirrors the adapter
slice's client-stub approach.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestExecResult
from axrange.config_exec import ConfigBackend, ConfigRenderError, render_playbook
from axrange.config_exec.render import render_create_domain_user, render_create_local_user
from axrange.ir.parser import parse_ir
from axrange.ops.registry import CleanupStrategy, IdempotencyClass, OpKind
from axrange.planner import Executor, StateStore, build_plan
from axrange.planner.cluster import ClusterContext
from axrange.planner.dag import OpInstance, Plan
from axrange.planner.executor import AdapterError, ExecutionContext

FIXTURES = Path(__file__).parent / "fixtures"
APP_SCN = FIXTURES / "ir" / "valid_app" / "scenario.yaml"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _parse_yaml(text: str):
    return YAML(typ="safe").load(io.StringIO(text))


def _install_tasks(play):
    """The install tasks only -- the apt play also opens with a cache refresh."""
    return [t for t in play["tasks"] if str(t.get("name", "")).startswith("install ")]


def test_render_pkg_produces_apt_task():
    pb = render_playbook([{"pkg": "nginx"}])
    doc = _parse_yaml(pb)
    assert isinstance(doc, list) and len(doc) == 1
    play = doc[0]
    assert play["hosts"] == "localhost" and play["connection"] == "local"
    assert play["become"] is False  # QGA exec is already root
    task = _install_tasks(play)[0]
    assert task["ansible.builtin.apt"] == {"name": "nginx", "state": "present"}


def test_render_pkg_version_pins():
    pb = render_playbook([{"pkg": "nginx", "version": "1.22.1-9"}])
    doc = _parse_yaml(pb)
    task = _install_tasks(doc[0])[0]
    assert task["ansible.builtin.apt"]["name"] == "nginx=1.22.1-9"


def test_render_multiple_and_env():
    pb = render_playbook(
        [{"pkg": "nginx"}, {"pkg": "curl"}], env={"http_proxy": "http://p:8080"}
    )
    doc = _parse_yaml(pb)
    assert len(_install_tasks(doc[0])) == 2
    assert doc[0]["environment"] == {"http_proxy": "http://p:8080"}


def test_render_non_apt_uses_generic_package_module():
    pb = render_playbook([{"pkg": "nginx"}], package_manager="yum")
    doc = _parse_yaml(pb)
    assert "ansible.builtin.package" in doc[0]["tasks"][0]


def test_render_apt_refreshes_cache_before_installing():
    """A cloned guest carries its TEMPLATE's apt index. Once the distro rotates
    its indices every URL in that cache 404s and `state: present` fails on a
    package that is perfectly installable -- the error names the package, not
    the stale cache. Hit live on dev45 2026-08-13: four Debian hosts failed
    provision-app this way; an in-guest `apt-get update` fixed all of them.

    The refresh must come FIRST (an install against the stale index has already
    failed by the time a later refresh runs) and must be NON-FATAL, mirroring
    the shell installers' `apt-get update ... || true` so the loud, specific
    failure stays on the install task."""
    pb = render_playbook([{"pkg": "nginx"}, {"pkg": "curl"}])
    doc = _parse_yaml(pb)
    tasks = doc[0]["tasks"]
    first = tasks[0]
    assert first["ansible.builtin.apt"]["update_cache"] is True
    assert first["failed_when"] is False, "a failed refresh must not kill the play"
    # No `cache_valid_time`: it trusts apt's update-success TIMESTAMP rather
    # than whether the index actually exists. Caught live on dev45 2026-08-14 --
    # with /var/lib/apt/lists emptied but a recent stamp, Ansible reported the
    # refresh `ok` without doing anything and the install failed on the empty
    # index exactly as before, i.e. this whole fix silently did not apply.
    assert "cache_valid_time" not in first["ansible.builtin.apt"]
    # ...and it is genuinely first, ahead of every install.
    assert [t.get("name") for t in tasks[1:]] == ["install nginx", "install curl"]


def test_render_non_apt_gets_no_cache_refresh():
    """`ansible.builtin.package` has no `update_cache` parameter -- emitting one
    would make the play error out on a non-apt guest."""
    pb = render_playbook([{"pkg": "nginx"}], package_manager="yum")
    doc = _parse_yaml(pb)
    assert len(doc[0]["tasks"]) == 1
    assert "update_cache" not in doc[0]["tasks"][0]["ansible.builtin.package"]


def test_render_empty_actions_emits_no_refresh():
    """No installs -> nothing to refresh for; don't emit a lone apt task."""
    doc = _parse_yaml(render_playbook([]))
    assert doc[0]["tasks"] == []


def test_render_rejects_unsupported_kind():
    with pytest.raises(ConfigRenderError):
        render_playbook([{"script": "/x.sh"}])
    with pytest.raises(ConfigRenderError):
        render_playbook([{"role": "web"}])


# ---------------------------------------------------------------------------
# Fake GuestAgent + ConfigBackend
# ---------------------------------------------------------------------------


class _FakeGA:
    def __init__(self, *, ansible_present=True, ansible_exit=0, script_exit=0):
        self.ansible_present = ansible_present
        self.ansible_exit = ansible_exit
        self.script_exit = script_exit
        self.execs: list[tuple] = []
        self.shells: list[tuple] = []
        self.written: dict[str, str] = {}

    def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
        self.execs.append((vmid, tuple(argv)))
        cmd = " ".join(argv)
        if "command -v ansible-playbook" in cmd:
            return GuestExecResult(0 if self.ansible_present else 1, "", "")
        if argv[:1] == ["ansible-playbook"]:
            return GuestExecResult(
                self.ansible_exit, "PLAY RECAP ok=1 changed=1", ""
                if self.ansible_exit == 0 else "fatal: task failed"
            )
        if argv[:1] == ["powershell"]:  # windows -File run (W1.4)
            return GuestExecResult(
                self.script_exit, "ps ran", "" if self.script_exit == 0 else "boom"
            )
        if len(argv) == 1 and "/provision/scripts/" in argv[0]:  # run-script exec
            return GuestExecResult(
                self.script_exit, "script ran", "" if self.script_exit == 0 else "boom"
            )
        return GuestExecResult(0, "", "")  # mkdir etc.

    def shell_exec(self, vmid, command, *, input_data=None, timeout_s=300):
        self.shells.append((vmid, command))
        return GuestExecResult(0, "", "")

    def write_file(self, vmid, path, content, *, mode=None):
        self.written[path] = content


def _provision_op():
    return OpInstance(
        op_id="p:provision-app[app=web,host=h-a]",
        op_name="provision-app",
        op_catalog_id="OP-APP-010",
        kind=OpKind.ACTION,
        scope={"app": "web", "host": "h-a"},
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
    )


def _run_script_op():
    return OpInstance(
        op_id="p:run-script[app=web,host=h-a]",
        op_name="run-script",
        op_catalog_id="OP-APP-030",
        kind=OpKind.ACTION,
        scope={"app": "web", "host": "h-a"},
        cleanup_strategy=CleanupStrategy.ESCALATE,
        idempotency_class=IdempotencyClass.CONFIG,
    )


def _ctx():
    return ExecutionContext(
        plan_id="p", deploy_id="d",
        cluster=ClusterContext(default_node="px1"), placement={},
    )


def _backend(fake: _FakeGA) -> ConfigBackend:
    return ConfigBackend(
        AdapterConfig(), rest_client=object(),
        guest_agent_factory=lambda client, node, **kw: fake,
    )


def test_provision_app_windows_uses_choco_powershell():
    fake = _FakeGA()
    out = _backend(fake).run(_provision_op(), {
        "vmid": 7, "app_id": "web", "os_family": "windows",
        "install_actions": [{"pkg": "git"}, {"pkg": "nginx", "version": "1.2"}],
    }, _ctx())
    assert out == {"provisioned": True, "tasks": 2}
    ps = next(c for p, c in fake.written.items() if p.endswith(".ps1"))
    assert "chocolatey.org/install.ps1" in ps  # choco bootstrap
    assert "choco install -y --no-progress 'git'" in ps
    assert "--version='1.2' 'nginx'" in ps
    # run via powershell -File, never ansible.
    assert any(a[:1] == ("powershell",) for _v, a in fake.execs)
    assert not any("ansible" in " ".join(a) for _v, a in fake.execs)


def test_provision_app_windows_rejects_invalid_choco_pkg():
    """code-review finding 4: an unvalidated pkg/version interpolates straight
    into a PowerShell command line (the Linux path is safe via Ansible YAML;
    this path isn't) — a name outside the chocolatey id charset must be
    rejected, not silently shipped unquoted."""
    fake = _FakeGA()
    with pytest.raises(AdapterError):
        _backend(fake).run(_provision_op(), {
            "vmid": 7, "app_id": "web", "os_family": "windows",
            "install_actions": [{"pkg": "git; whoami"}],
        }, _ctx())


def test_render_domain_user_acl_target_with_dollar_escaped_in_filter():
    """code-review finding 3: an AD target containing `$` (e.g. a computer
    account like WS01$) must be escaped for the DOUBLE-quoted -Filter context —
    PowerShell still expands `$var`/`$(...)` inside double quotes even when the
    substring is ALSO wrapped in nested single quotes, so an unescaped $ would
    silently mis-evaluate (unset-variable empty string, or code injection via
    `$(...)`) and the takeover ACE would silently be skipped/misapplied."""
    ps = render_create_domain_user(
        "intern", password="P",
        acl=[{"right": "GenericAll", "target": "WS01$"}])
    filter_line = next(ln for ln in ps.splitlines() if "-or Name -eq" in ln)
    # escaped ($ -> `$) inside the double-quoted -Filter string
    assert "Name -eq 'WS01`$'" in filter_line
    # the raw, unescaped form must NOT appear (it would be $-expanded by PS)
    assert "Name -eq 'WS01$'" not in filter_line


def test_render_domain_user_acl_target_with_apostrophe_escaped_in_filter():
    """2026-07-07 review fix: an AD target containing an apostrophe (e.g. the
    display name `O'Brien`) sits inside a nested SINGLE-quoted literal
    (`-eq '{value}'`) within the DOUBLE-quoted -Filter string. An unescaped
    `'` terminates that literal early and breaks the filter — the value must
    have its single quotes doubled (`'` -> `''`), same as any PowerShell
    single-quoted-string escaping, in addition to the backtick/$/" escapes
    for the outer double-quoted context."""
    ps = render_create_domain_user(
        "intern", password="P",
        acl=[{"right": "GenericAll", "target": "O'Brien"}])
    filter_line = next(ln for ln in ps.splitlines() if "-or Name -eq" in ln)
    # doubled ('  -> '') inside the nested single-quoted literal
    assert "Name -eq 'O''Brien'" in filter_line
    # the raw, unescaped form must NOT appear (it would truncate the literal)
    assert "Name -eq 'O'Brien'" not in filter_line


def test_domain_user_sam_truncation_collision_guard():
    """code-review finding 5: two identity_ids sharing a >20-char common
    prefix must NOT collapse to the same SamAccountName — a bare [:20] cut
    would collide them, and the idempotent skip-if-exists guard would then
    never materialize the second account."""
    a = "a-very-long-identity-name-alpha"
    b = "a-very-long-identity-name-beta"
    ps_a = render_create_domain_user(a, password="P")
    ps_b = render_create_domain_user(b, password="P")
    sam_a = next(ln for ln in ps_a.splitlines() if ln.startswith("$sam="))
    sam_b = next(ln for ln in ps_b.splitlines() if ln.startswith("$sam="))
    assert sam_a != sam_b


def test_local_user_truncation_collision_guard():
    prefix = "long-local-identity-prefix-common-"  # 34 chars, > the 32-char limit
    a = prefix + "alpha"
    b = prefix + "beta"
    assert a[:32] == b[:32]  # would collide under a bare truncation
    sh_a = render_create_local_user(a, password="P", os_family="linux")
    sh_b = render_create_local_user(b, password="P", os_family="linux")
    user_a = next(ln for ln in sh_a.splitlines() if ln.startswith("id "))
    user_b = next(ln for ln in sh_b.splitlines() if ln.startswith("id "))
    assert user_a != user_b


def test_run_script_windows_runs_powershell_file():
    fake = _FakeGA()
    out = _backend(fake).run(_run_script_op(), {
        "vmid": 7, "script_name": "web-setup", "os_family": "windows",
        "script_content": "Write-Host hi",
    }, _ctx())
    assert out == {"script_ran": True}
    assert any(p.endswith(".ps1") and "Write-Host hi" in c for p, c in fake.written.items())
    assert any(a[:1] == ("powershell",) for _v, a in fake.execs)


def test_provision_app_writes_and_runs_playbook():
    fake = _FakeGA()
    backend = _backend(fake)
    out = backend.run(
        _provision_op(),
        {"vmid": 100, "app_id": "web", "install_actions": [{"pkg": "nginx"}],
         "package_manager": "apt", "env": {}},
        _ctx(),
    )
    assert out == {"provisioned": True, "tasks": 1}
    # A playbook was written and then run.
    assert any("axr-web.yml" in p for p in fake.written)
    pb = next(c for c in fake.written.values())
    assert "nginx" in pb
    assert any(a[1][:1] == ("ansible-playbook",) for a in fake.execs)


def test_provision_app_no_ansible_in_guest_errors():
    fake = _FakeGA(ansible_present=False)
    with pytest.raises(AdapterError) as exc:
        _backend(fake).run(
            _provision_op(),
            {"vmid": 100, "app_id": "web", "install_actions": [{"pkg": "nginx"}]},
            _ctx(),
        )
    assert exc.value.error_class == "config-no-ansible"
    # Never tried to run the playbook.
    assert not any(a[1][:1] == ("ansible-playbook",) for a in fake.execs)


def test_provision_app_ansible_failure_errors():
    fake = _FakeGA(ansible_exit=2)
    with pytest.raises(AdapterError) as exc:
        _backend(fake).run(
            _provision_op(),
            {"vmid": 100, "app_id": "web", "install_actions": [{"pkg": "nginx"}]},
            _ctx(),
        )
    assert exc.value.error_class == "ansible-failed"


def test_provision_app_no_actions_is_noop():
    fake = _FakeGA(ansible_present=False)  # never checked — no actions
    out = _backend(fake).run(
        _provision_op(),
        {"vmid": 100, "app_id": "web", "install_actions": []},
        _ctx(),
    )
    assert out == {"provisioned": True, "tasks": 0}
    assert fake.execs == []


def test_config_backend_rejects_unknown_op():
    op = _provision_op()
    op = OpInstance(
        op_id="p:run-container[x]", op_name="run-container",
        op_catalog_id="OP-APP-020", kind=OpKind.ACTION, scope={},
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.CONFIG,
    )
    with pytest.raises(AdapterError) as exc:
        _backend(_FakeGA()).run(op, {"vmid": 1}, _ctx())
    assert "no implementation" in str(exc.value).lower()


def test_run_script_writes_and_execs():
    fake = _FakeGA()
    out = _backend(fake).run(
        _run_script_op(),
        {"vmid": 100, "script_name": "web-setup.sh",
         "script_content": "#!/bin/sh\necho hi\n"},
        _ctx(),
    )
    assert out == {"script_ran": True}
    # Script written under the provision scripts dir, then executed directly.
    written_path = next(iter(fake.written))
    assert "/provision/scripts/web-setup.sh" in written_path
    assert fake.written[written_path].startswith("#!/bin/sh")
    assert any(a[1] == (written_path,) for a in fake.execs)  # exec'd by path (shebang)


def test_run_script_failure_errors():
    fake = _FakeGA(script_exit=1)
    with pytest.raises(AdapterError) as exc:
        _backend(fake).run(
            _run_script_op(),
            {"vmid": 100, "script_name": "x.sh", "script_content": "#!/bin/sh\nfalse\n"},
            _ctx(),
        )
    assert exc.value.error_class == "script-failed"
    assert "stderr: boom" in str(exc.value)          # stderr is kept even with stdout
    assert "stdout (tail): script ran" in str(exc.value)
    assert "shebang" not in str(exc.value)           # exit 1 is not an exec failure


def test_run_script_failure_keeps_the_stderr_line_after_a_long_stdout():
    """Live run 1 (2026-09-26): a recipe's progress log filled the old
    300-char stdout head, so its `FAIL ...` stderr line was never recorded."""
    class _Chatty(_FakeGA):
        def exec(self, vmid, argv, *, input_data=None, timeout_s=300):
            if len(argv) == 1 and "/provision/scripts/" in argv[0]:
                self.execs.append((vmid, tuple(argv)))
                log = "".join(f"axr-l2switch[ms120]: net{i} port line\n" for i in range(40))
                return GuestExecResult(
                    1, log, "axr-l2switch[ms120]: FAIL bridge port eth0 still has an address")
            return super().exec(vmid, argv, input_data=input_data, timeout_s=timeout_s)

    with pytest.raises(AdapterError) as exc:
        _backend(_Chatty()).run(
            _run_script_op(),
            {"vmid": 100, "script_name": "x.sh", "script_content": "#!/bin/sh\nfalse\n"},
            _ctx(),
        )
    msg = str(exc.value)
    assert "FAIL bridge port eth0 still has an address" in msg
    assert "net39 port line" in msg                  # the TAIL of stdout, not the head


# ---------------------------------------------------------------------------
# Executor routing
# ---------------------------------------------------------------------------


def _infra_op():
    return OpInstance(
        op_id="p:start-vm[host=h-a]", op_name="start-vm",
        op_catalog_id="OP-HOST-060", kind=OpKind.ACTION, scope={"host": "h-a"},
        cleanup_strategy=CleanupStrategy.REDO,
        idempotency_class=IdempotencyClass.INFRA,
    )


def test_executor_routes_by_idempotency_class(tmp_path):
    plan = Plan(plan_id="p", scenario_id="s", scenario_version=1, ir_hash="h")
    store = StateStore(str(tmp_path / "s.db"))
    fake_infra, fake_config = object(), object()
    ex = Executor(
        plan, store, fake_infra, ClusterContext(default_node="n"),
        deploy_id="d", config_backend=fake_config,
    )
    assert ex._backend_for(_provision_op()) is fake_config
    assert ex._backend_for(_infra_op()) is fake_infra
    store.close()


def test_executor_config_op_without_backend_raises(tmp_path):
    plan = Plan(plan_id="p", scenario_id="s", scenario_version=1, ir_hash="h")
    store = StateStore(str(tmp_path / "s.db"))
    ex = Executor(
        plan, store, object(), ClusterContext(default_node="n"), deploy_id="d",
    )  # no config_backend
    with pytest.raises(AdapterError) as exc:
        ex._backend_for(_provision_op())
    assert exc.value.error_class == "executor-no-config-backend"
    store.close()


# ---------------------------------------------------------------------------
# End-to-end plan from the fixture
# ---------------------------------------------------------------------------


def test_app_fixture_validates_and_plans(manifests):
    from axrange.validator.engine import validate_full

    result = validate_full(str(APP_SCN), manifests=manifests)
    assert result.exit_code == 0, [f.message for f in result.errors]

    parsed = parse_ir(path=str(APP_SCN))
    ctx = ClusterContext.single_node("px1")
    plan = build_plan(
        parsed.ir, manifests, ctx, raw_ir=parsed.raw,
        scenario_dir=str(APP_SCN.parent),
    )
    prov = [op for op in plan.ops.values() if op.op_name == "provision-app"]
    run = [op for op in plan.ops.values() if op.op_name == "run-script"]
    assert len(prov) == 1 and len(run) == 1  # pkg -> provision-app, script -> run-script
    assert prov[0].idempotency_class == IdempotencyClass.CONFIG
    # script content was read at emit time; run-script gated after provision-app
    assert "web app provisioned" in run[0].inputs_resolved["script_content"]
    assert prov[0].op_id in plan.predecessors(run[0].op_id)
