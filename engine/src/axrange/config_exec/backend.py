"""ConfigBackend — runs `config`-class ops in the guest via Ansible over QGA.

docs/SLICE_CONFIG.md (ratified 2026-06-02): config ops execute as Ansible
playbooks delivered into the guest over the qemu-guest-agent exec channel (the
orchestrator can't reach isolated scenario SDN networks; the same channel the
infra slice's `wait-os-ready` uses). The executor routes `config`-class ops here
by `idempotency_class`; this backend implements the same `run()` shape as the
Proxmox (infra) adapter.

The Ansible *render* (`render.py`) is platform-agnostic; only the *delivery*
(GuestAgent → Proxmox QGA) is Proxmox-specific. Ansible must be present in the
guest — baked into the template via the imagebuild `provision:` path
(`docs/IMAGE_BUILD.md`); a clear error fires if it's missing (it can't be
installed on demand on an isolated host).
"""
from __future__ import annotations

import re
import shlex
import time
from typing import Any

from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.guest_agent import GuestAgent
from axrange.adapter_proxmox.rest import RestClient
from axrange.config_exec.render import (
    ActivityRenderError,
    ConfigRenderError,
    DatasetRenderError,
    activity_paths,
    agent_script_path,
    capture_confirm_command,
    capture_sentinel_runner_path,
    capture_sentinel_runner_path_windows,
    capture_sentinel_token,
    defense_agent_script_path,
    network_sensor_script_path,
    overlay_seeder_path,
    render_activity_launch_script,
    render_activity_runner,
    render_activity_scheduler,
    render_activity_truth_shipper,
    render_capture_sentinel,
    render_capture_sentinel_windows,
    render_dataset_seeder,
    render_defense_agent_config,
    render_exporter_agent,
    render_sandcat_agent,
    render_windows_defense_agent,
    render_windows_exporter_agent,
    render_windows_sandcat_agent,
    render_create_domain_user,
    render_create_local_user,
    render_join_to_domain,
    render_network_sensor_setup,
    render_playbook,
    render_windows_adds_promotion,
    render_windows_provision,
)
from axrange.planner.dag import OpInstance
from axrange.planner.executor import AdapterError, AdapterTimeout, ExecutionContext

_PROVISION_DIR = "/var/lib/axrange/provision"
_WIN_PROVISION_DIR = r"C:\axrange\provision"  # W1.4: Windows guest provision dir
_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")


def _failure_detail(res: Any, *, keep: int = 1200) -> str:
    """The part of a failed guest command's output worth recording: the TAIL
    of stderr (where a recipe's `FAIL ...` / apt's `E: ...` lands) and the
    tail of stdout, both labelled. The head of stdout alone -- the old
    `(stdout or stderr)[:300]` -- dropped stderr whenever stdout was non-empty
    and cut the progress log before the failing step: the corp-twin live run 1
    (2026-09-26) recorded two failures whose cause was in neither message
    (docs/WORK_ORDER_corp_twin.md §11.D)."""
    def tail(s: Any, n: int) -> str:
        s = str(s or "").strip()
        return s if len(s) <= n else "..." + s[-n:]

    err, out = tail(res.stderr, keep), tail(res.stdout, keep // 2)
    parts = [f"stderr: {err}"] if err else []
    if out:
        parts.append(f"stdout (tail): {out}")
    return " | ".join(parts) or "(no output)"

# W2a (docs/SLICE_WINDOWS_AD.md §6.1): the DSRM / SafeMode admin password used for
# Install-ADDSForest when the IR doesn't supply one. Falls back to
# `self.config.default_password` (docs/WORK_ORDER_access_facts.md Part A) --
# threading a PER-binding override from the IR remains a follow-on.

# Install-ADDSForest (forest create + DNS + SYSVOL) is slow and must not be cut
# short by provision-app's default op timeout (900s) — a premature timeout would
# trip the reboot mid-promotion. Give it its own generous floor.
_ADDS_PROMOTE_TIMEOUT_S = 1800
# Post-promotion the DC reboots and initializes AD; allow ample time to return.
_ADDS_REBOOT_TIMEOUT_S = 900

# wait-domain-ready stability gate: how many CONSECUTIVE successful probes before
# declaring the domain serving. ADWS (the service the ActiveDirectory PS module —
# and create-domain-user's New-ADUser — talks to) flaps during post-DCPROMO
# settling: a single Get-ADDomain success can be followed by an "ADWS not running"
# failure moments later. Requiring N-in-a-row (at the 3s poll interval) rides that
# out (CHANGELOG 2026-06-24, observed in the AD-defense verify).
_DOMAIN_READY_STABLE = 3

# W2b (docs/SLICE_WINDOWS_AD.md §6.2): default password for a created AD domain
# / local user when `IdentityEntry.password` is unset -- falls back to
# `self.config.default_password` (docs/WORK_ORDER_access_facts.md Part A);
# an explicit per-identity `password:` still wins (`inp.get("password") or
# self.config.default_password`, unchanged precedence).

# The domain Administrator password the DC promotion sets (so it is deterministic),
# and the credential join-host-to-domain (§6.3) authenticates with as
# <NETBIOS>\Administrator. Falls back to `self.config.default_password`
# (docs/WORK_ORDER_access_facts.md Part A); no per-binding override channel
# exists for this account today.


class ConfigBackend:
    """Implements axrange.planner.Adapter for `config`-class ops.

    `guest_agent_factory` is injectable so tests drive the full flow without a
    cluster (mirrors the adapter slice's client-stub approach)."""

    def __init__(
        self,
        config: AdapterConfig,
        *,
        rest_client: RestClient | None = None,
        guest_agent_factory=GuestAgent,
        sleep_fn=time.sleep,
        actor_poll_interval_s: float = 3.0,
    ) -> None:
        self.config = config
        self._client = rest_client or RestClient(config)
        self._ga_factory = guest_agent_factory
        self._sleep = sleep_fn
        self._actor_poll_interval_s = actor_poll_interval_s
        # verify-capture's sensor read (OP-MIR-040) is ALWAYS over QGA via the
        # op's `sensor_vmid` input (2026-07-13,
        # docs/WORK_ORDER_sensor_agnostic_te.md — no SENSOR_SSH_* mgmt-SSH
        # fallback, so no injectable confirm fn is needed; tests drive it via
        # `guest_agent_factory`'s fake, like every other QGA-mediated op).

    def run(
        self,
        op: OpInstance,
        resolved_inputs: dict[str, Any],
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        if op.op_name == "provision-app":
            return self._provision_app(op, resolved_inputs, ctx)
        if op.op_name == "run-script":
            return self._run_script(op, resolved_inputs, ctx)
        if op.op_name == "deploy-behavior-agent":
            return self._deploy_behavior_agent(op, resolved_inputs, ctx)
        if op.op_name == "start-activity":
            return self._start_activity(op, resolved_inputs, ctx)
        if op.op_name == "wait-actor-ready":
            return self._wait_actor_ready(op, resolved_inputs, ctx)
        if op.op_name == "wait-domain-ready":
            return self._wait_domain_ready(op, resolved_inputs, ctx)
        if op.op_name == "create-domain-user":
            return self._create_domain_user(op, resolved_inputs, ctx)
        if op.op_name == "join-host-to-domain":
            return self._join_host_to_domain(op, resolved_inputs, ctx)
        if op.op_name == "seed-dataset":
            return self._seed_dataset(op, resolved_inputs, ctx)
        if op.op_name == "install-defense-agent":
            return self._install_defense_agent(op, resolved_inputs, ctx)
        if op.op_name == "install-agent":
            return self._install_agent(op, resolved_inputs, ctx)
        if op.op_name == "deploy-network-sensor":
            return self._deploy_network_sensor(op, resolved_inputs, ctx)
        if op.op_name == "deploy-sensor":
            return self._deploy_sensor(op, resolved_inputs, ctx)
        if op.op_name == "verify-capture":
            return self._verify_capture(op, resolved_inputs, ctx)
        raise AdapterError(
            f"config backend has no implementation for {op.op_name!r}",
            error_class="config-unsupported",
        )

    def _provision_app(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        vmid = int(inp["vmid"])
        actions = inp.get("install_actions") or []
        app_id = str(inp.get("app_id") or op.scope.get("app") or "app")
        os_family = (inp.get("os_family") or "linux").lower()

        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )

        if not actions:
            # An app with no install actions is a no-op success (still valid IR).
            return {"provisioned": True, "tasks": 0}

        if os_family == "windows":
            return self._provision_app_windows(vmid, app_id, actions, ga, op, inp)

        playbook = render_playbook(
            actions,
            package_manager=inp.get("package_manager") or "apt",
            env=inp.get("env"),
        )

        # Ansible must be baked into the template (it can't be apt-installed on
        # an isolated host with no internet). Fail loudly if it's absent.
        check = ga.exec(
            vmid, ["sh", "-lc", "command -v ansible-playbook"], timeout_s=30
        )
        if not check.ok:
            raise AdapterError(
                f"provision-app[{app_id}]: ansible-playbook not found in vmid="
                f"{vmid}; bake ansible into the template via the imagebuild "
                "provision: path (docs/SLICE_CONFIG.md §2)",
                error_class="config-no-ansible",
            )

        pb_path = f"{_PROVISION_DIR}/axr-{_SAFE.sub('-', app_id)}.yml"
        ga.exec(vmid, ["mkdir", "-p", _PROVISION_DIR], timeout_s=30)
        ga.write_file(vmid, pb_path, playbook, mode="0644")

        res = ga.exec(
            vmid, ["ansible-playbook", pb_path], timeout_s=op.timeout_s or 900
        )
        if not res.ok:
            raise AdapterError(
                f"provision-app[{app_id}]: ansible-playbook exited "
                f"{res.exit_code} in vmid={vmid}: {_failure_detail(res)}",
                error_class="ansible-failed",
            )
        return {"provisioned": True, "tasks": len(actions)}

    def _run_script(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Write a `{script:}` action's file into the guest and execute it
        directly over QGA (honoring its shebang). Unlike provision-app this is
        not wrapped in Ansible — a `script:` is opaque shell. `run-script` is
        `cleanup_strategy: escalate` in the catalog (non-idempotent); the slice
        executor treats escalate as redo, so a failed script retries naively
        (escalate-walk-back is deferred planner machinery — docs/SLICE_CONFIG.md).
        QGA exec is root, so the script runs as root in-guest.
        """
        vmid = int(inp["vmid"])
        content = inp.get("script_content")
        name = str(inp.get("script_name") or "script")
        os_family = (inp.get("os_family") or "linux").lower()
        if not content:
            return {"script_ran": True}

        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        if os_family == "windows":
            return self._run_script_windows(vmid, name, content, ga, op)
        scripts_dir = f"{_PROVISION_DIR}/scripts"
        guest_path = f"{scripts_dir}/{_SAFE.sub('-', name)}"
        ga.exec(vmid, ["mkdir", "-p", scripts_dir], timeout_s=30)
        ga.write_file(vmid, guest_path, content, mode="0755")

        res = ga.exec(vmid, [guest_path], timeout_s=op.timeout_s or 600)
        if not res.ok:
            raise AdapterError(
                f"run-script[{name}]: exited {res.exit_code} in vmid={vmid}: "
                f"{_failure_detail(res)}"
                + (" (scripts must have a shebang)" if res.exit_code in (126, 127) else ""),
                error_class="script-failed",
            )
        return {"script_ran": True}

    # --- W1.4: Windows config (docs/SLICE_WINDOWS_AD.md §5) -----------------

    def _provision_app_windows(
        self, vmid: int, app_id: str, actions: list, ga, op: OpInstance,
        inp: dict[str, Any],
    ) -> dict[str, Any]:
        """Provision a Windows host over the QGA-PowerShell channel (no Ansible on
        Windows). Two action kinds: `pkg`/`pkg@version` install via chocolatey (the
        bootstrap needs guest egress, like apt on Linux); `role: adds` promotes the
        host to an AD domain controller (W2a, docs/SLICE_WINDOWS_AD.md §6.1) — slow
        and reboot-tolerant. Packages run first, then the (rebooting) promotion."""
        pkg_actions = [
            a for a in actions if isinstance(a, dict) and not a.get("role")
        ]
        role_actions = [
            a for a in actions if isinstance(a, dict) and a.get("role")
        ]
        ga.shell_exec(
            vmid,
            f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
            timeout_s=30,
        )

        if pkg_actions:
            try:
                script = render_windows_provision(pkg_actions)
            except ConfigRenderError as e:
                raise AdapterError(
                    f"provision-app[{app_id}]: {e}",
                    error_class="config-render-failed",
                )
            path = f"{_WIN_PROVISION_DIR}\\axr-{_SAFE.sub('-', app_id)}.ps1"
            ga.write_file(vmid, path, script)
            res = ga.exec(
                vmid,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                timeout_s=op.timeout_s or 900,
            )
            if not res.ok:
                raise AdapterError(
                    f"provision-app[{app_id}]: PowerShell exited {res.exit_code} in "
                    f"vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                    error_class="windows-provision-failed",
                )

        for a in role_actions:
            role = a.get("role")
            if role != "adds":
                raise AdapterError(
                    f"provision-app[{app_id}]: role={role!r} not supported on Windows "
                    "(only 'adds' — AD DC promotion — is implemented)",
                    error_class="windows-role-unsupported",
                )
            self._promote_dc_windows(vmid, app_id, ga, op, inp)

        return {"provisioned": True, "tasks": len(actions)}

    def _promote_dc_windows(
        self, vmid: int, app_id: str, ga, op: OpInstance, inp: dict[str, Any]
    ) -> None:
        """Promote a Windows Server to the first DC of a new AD forest (W2a
        `role: adds`). Reboot-tolerant: Install-ADDSForest runs with
        -NoRebootOnCompletion, then we drive the reboot and wait for the box to
        return (boot-id change). wait-domain-ready (ordered after this op) then
        confirms AD is actually serving."""
        domain_fqdn = inp.get("domain_fqdn")
        if not domain_fqdn:
            raise AdapterError(
                f"provision-app[{app_id}]: role: adds but no domain_fqdn in inputs "
                "(the planner resolves it from the DC app's provides:[{domain}])",
                error_class="windows-adds-no-domain",
            )
        safe_pw = str(inp.get("safe_mode_password") or self.config.default_password)
        try:
            script = render_windows_adds_promotion(
                str(domain_fqdn), safe_mode_password=safe_pw,
                admin_password=self.config.default_password,
            )
        except ConfigRenderError as e:
            raise AdapterError(
                f"provision-app[{app_id}]: {e}", error_class="config-render-failed"
            )
        path = f"{_WIN_PROVISION_DIR}\\axr-{_SAFE.sub('-', app_id)}-adds.ps1"
        ga.write_file(vmid, path, script)

        prev_boot = self._windows_boot_id(ga, vmid)

        # Install-ADDSForest is slow (~5-15 min). -NoRebootOnCompletion leaves the
        # box up, but AD may bounce services mid-call — tolerate a QGA drop (the
        # boot-id wait below is the real completion gate, mirroring W0 sysprep_seal).
        try:
            res = ga.exec(
                vmid,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                timeout_s=max(op.timeout_s or 0, _ADDS_PROMOTE_TIMEOUT_S),
            )
            if not res.ok:
                raise AdapterError(
                    f"provision-app[{app_id}]: Install-ADDSForest exited "
                    f"{res.exit_code} in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                    error_class="windows-adds-failed",
                )
        except AdapterTimeout:
            pass  # promotion didn't return cleanly; the reboot wait confirms it

        # AD DS needs a reboot to fully come up. Drive it, tolerate the QGA drop,
        # then wait for the guest to return.
        try:
            ga.shell_exec(vmid, "Restart-Computer -Force", timeout_s=30)
        except (AdapterError, AdapterTimeout):
            pass  # the response races the reboot; benign
        self._wait_windows_reboot(ga, vmid, prev_boot, timeout_s=_ADDS_REBOOT_TIMEOUT_S)

    def _windows_boot_id(self, ga, vmid: int) -> str:
        """The guest's LastBootUpTime ticks as a string — a monotonic reboot
        fingerprint. Empty string if the guest isn't answering (mid-reboot)."""
        try:
            res = ga.shell_exec(
                vmid,
                "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.Ticks",
                timeout_s=60,
            )
            return res.stdout.strip() if res.ok else ""
        except (AdapterError, AdapterTimeout):
            return ""

    def _wait_windows_reboot(
        self, ga, vmid: int, prev_boot_id: str, *, timeout_s: int = 600,
        poll_s: float = 10.0,
    ) -> None:
        """Block until the guest reboots and QGA answers again. With a baseline
        boot id, returns when it advances; without one, returns after observing a
        down→up transition. Raises AdapterTimeout if it never returns."""
        deadline = time.time() + timeout_s
        saw_down = False
        last = "no response"
        while time.time() < deadline:
            cur = self._windows_boot_id(ga, vmid)
            if not cur:
                saw_down = True
                last = "guest unreachable (rebooting)"
            elif prev_boot_id and cur != prev_boot_id:
                return
            elif not prev_boot_id and saw_down:
                return
            else:
                last = f"boot id unchanged ({cur})"
            self._sleep(poll_s)
        raise AdapterTimeout(
            f"vmid={vmid} did not reboot back after DC promotion in {timeout_s}s "
            f"({last})"
        )

    def _wait_domain_ready(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-010 (wait, config-class): poll the DC over QGA until its AD
        directory services answer. Gates domain-scoped identity ops via domain-ready
        (docs/SLICE_WINDOWS_AD.md §6.1).

        Hardened (CHANGELOG 2026-06-24): the probe requires the core DC services —
        **ADWS** (the endpoint the ActiveDirectory PS module / create-domain-user's
        New-ADUser depends on), NTDS, and Netlogon — to be `Running`, *and*
        Get-ADDomain to answer the forest root. We then require `_DOMAIN_READY_STABLE`
        CONSECUTIVE successes (a failure resets the streak) so a transient ADWS flap
        during post-DCPROMO settling can't declare the domain ready prematurely (the
        'Unable to find a default server with Active Directory Web Services running'
        create-domain-user failure)."""
        vmid = int(inp["dc_vmid"])
        domain_fqdn = str(inp.get("domain_fqdn") or "")
        # AD is Windows; default the channel accordingly even if os_family is absent.
        os_family = (inp.get("os_family") or "windows").lower()
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        # Services must be Running (ADWS first — that's the create-domain-user dep).
        svc_check = (
            "foreach ($n in 'ADWS','NTDS','Netlogon') { "
            "$s = Get-Service -Name $n -ErrorAction SilentlyContinue; "
            "if (-not $s -or $s.Status -ne 'Running') { exit 1 } }\n"
        )
        if domain_fqdn:
            fq = domain_fqdn.replace("'", "''")
            ad_check = (
                "try { $r = (Get-ADDomain -ErrorAction Stop).DNSRoot } catch { exit 1 }\n"
                f"if ($r -eq '{fq}') {{ exit 0 }} else {{ exit 1 }}"
            )
        else:
            ad_check = (
                "try { $r = (Get-ADDomain -ErrorAction Stop).DNSRoot } catch { exit 1 }\n"
                "if ($r) { exit 0 } else { exit 1 }"
            )
        probe = svc_check + ad_check
        budget = float(op.timeout_s or 600)
        interval = self._actor_poll_interval_s
        waited = 0.0
        last = "no answer"
        stable = 0
        while True:
            try:
                res = ga.shell_exec(vmid, probe, timeout_s=60)
                if res.ok:
                    stable += 1
                    if stable >= _DOMAIN_READY_STABLE:
                        return {"domain_serving": True}
                else:
                    stable = 0
                    last = (res.stderr or res.stdout or "non-zero exit")[:200]
            except (AdapterError, AdapterTimeout) as e:
                stable = 0
                last = str(e)
            if waited >= budget:
                break
            self._sleep(interval)
            waited += interval
        raise AdapterTimeout(
            f"wait-domain-ready: vmid={vmid} domain {domain_fqdn or '?'} not "
            f"stably serving (ADWS/NTDS/Netlogon + Get-ADDomain, "
            f"{_DOMAIN_READY_STABLE}x) after {budget:.0f}s ({last})"
        )

    def _create_domain_user(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-020 (W2b): create one AD domain user on the DC over QGA-PowerShell
        (New-ADUser, idempotent), after domain-ready. A no-`domain_fqdn` input is a
        *local* user (New-LocalUser / useradd on the target host) — the local branch
        (docs/SLICE_WINDOWS_AD.md §6.2/§6.3)."""
        if not inp.get("domain_fqdn"):
            return self._create_local_user(op, inp, ctx)
        vmid = int(inp["target_vmid"])
        identity_id = str(inp.get("identity_id") or op.scope.get("identity") or "user")
        groups = list(inp.get("groups") or [])
        os_family = (inp.get("os_family") or "windows").lower()
        password = str(inp.get("password") or self.config.default_password)
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        try:
            script = render_create_domain_user(
                identity_id, password=password, groups=groups,
                spn=inp.get("spn"), ou=inp.get("ou"),
                asrep_roastable=bool(inp.get("asrep_roastable")),
                domain_fqdn=str(inp.get("domain_fqdn") or ""),
                acl=inp.get("acl"), delegation=inp.get("delegation"),
                delegation_to=inp.get("delegation_to"),
            )
        except ConfigRenderError as e:
            raise AdapterError(
                f"create-domain-user[{identity_id}]: {e}",
                error_class="config-render-failed",
            )
        path = f"{_WIN_PROVISION_DIR}\\axr-user-{_SAFE.sub('-', identity_id)}.ps1"
        ga.shell_exec(
            vmid,
            f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
            timeout_s=30,
        )
        ga.write_file(vmid, path, script)
        res = ga.exec(
            vmid,
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            timeout_s=op.timeout_s or 300,
        )
        if not res.ok:
            raise AdapterError(
                f"create-domain-user[{identity_id}]: New-ADUser exited {res.exit_code} "
                f"in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="windows-user-create-failed",
            )
        return {"user_created": True}

    def _create_local_user(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-020 local branch (W2, §6.2/§6.3): create one *local* user on the
        target host over QGA — New-LocalUser on Windows, useradd on Linux. Runs after
        host-ready[$host]. Idempotent (skip-if-exists)."""
        vmid = int(inp["target_vmid"])
        identity_id = str(inp.get("identity_id") or op.scope.get("identity") or "user")
        groups = list(inp.get("groups") or [])
        os_family = (inp.get("os_family") or "windows").lower()
        password = str(inp.get("password") or self.config.default_password)
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        try:
            script = render_create_local_user(
                identity_id, password=password, groups=groups, os_family=os_family
            )
        except ConfigRenderError as e:
            raise AdapterError(
                f"create-local-user[{identity_id}]: {e}",
                error_class="config-render-failed",
            )
        if os_family == "windows":
            path = f"{_WIN_PROVISION_DIR}\\axr-localuser-{_SAFE.sub('-', identity_id)}.ps1"
            ga.shell_exec(
                vmid,
                f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
                timeout_s=30,
            )
            ga.write_file(vmid, path, script)
            res = ga.exec(
                vmid,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                timeout_s=op.timeout_s or 300,
            )
        else:
            path = f"/tmp/axr-localuser-{_SAFE.sub('-', identity_id)}.sh"
            ga.write_file(vmid, path, script)
            res = ga.exec(vmid, ["sh", path], timeout_s=op.timeout_s or 300)
        if not res.ok:
            raise AdapterError(
                f"create-local-user[{identity_id}]: exited {res.exit_code} "
                f"in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="local-user-create-failed",
            )
        return {"user_created": True}

    def _join_host_to_domain(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-030 (W2, docs/SLICE_WINDOWS_AD.md §6.3): join a Windows host to an
        AD domain. Points the host's DNS at the DC, runs Add-Computer with the
        domain admin credential, then drives the reboot the join needs (reboot-
        tolerant, like the DC promotion). Idempotent: skips reboot if already joined."""
        vmid = int(inp["vmid"])
        host_id = str(inp.get("host_id") or op.scope.get("host") or "host")
        domain_fqdn = inp.get("domain_fqdn")
        dc_ip = inp.get("dc_ip")
        if not domain_fqdn or not dc_ip:
            raise AdapterError(
                f"join-host-to-domain[{host_id}]: missing domain_fqdn/dc_ip (the "
                "planner resolves these from node.joins + the domain's DC node)",
                error_class="windows-join-no-domain",
            )
        os_family = (inp.get("os_family") or "windows").lower()
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        try:
            script = render_join_to_domain(
                str(domain_fqdn), dc_ip=str(dc_ip),
                admin_password=self.config.default_password,
            )
        except ConfigRenderError as e:
            raise AdapterError(
                f"join-host-to-domain[{host_id}]: {e}",
                error_class="config-render-failed",
            )
        path = f"{_WIN_PROVISION_DIR}\\axr-join-{_SAFE.sub('-', host_id)}.ps1"
        ga.shell_exec(
            vmid,
            f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
            timeout_s=30,
        )
        ga.write_file(vmid, path, script)

        prev_boot = self._windows_boot_id(ga, vmid)
        res = ga.exec(
            vmid,
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            timeout_s=op.timeout_s or 600,
        )
        if not res.ok:
            raise AdapterError(
                f"join-host-to-domain[{host_id}]: Add-Computer exited {res.exit_code} "
                f"in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="windows-join-failed",
            )
        if "already joined" in (res.stdout or ""):
            return {"host_joined": True}  # idempotent — no reboot needed
        # A fresh join needs a reboot to take effect; drive it + wait for return.
        try:
            ga.shell_exec(vmid, "Restart-Computer -Force", timeout_s=30)
        except (AdapterError, AdapterTimeout):
            pass
        self._wait_windows_reboot(ga, vmid, prev_boot, timeout_s=_ADDS_REBOOT_TIMEOUT_S)
        return {"host_joined": True}

    def _run_script_windows(
        self, vmid: int, name: str, content: str, ga, op: OpInstance
    ) -> dict[str, Any]:
        """Run an opaque PowerShell script in a Windows guest (`powershell -File`)."""
        scripts_dir = f"{_WIN_PROVISION_DIR}\\scripts"
        path = f"{scripts_dir}\\{_SAFE.sub('-', name)}.ps1"
        ga.shell_exec(
            vmid,
            f"New-Item -ItemType Directory -Force -Path '{scripts_dir}' | Out-Null",
            timeout_s=30,
        )
        ga.write_file(vmid, path, content)
        res = ga.exec(
            vmid,
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            timeout_s=op.timeout_s or 600,
        )
        if not res.ok:
            raise AdapterError(
                f"run-script[{name}]: PowerShell exited {res.exit_code} in "
                f"vmid={vmid}: {_failure_detail(res)}",
                error_class="script-failed",
            )
        return {"script_ran": True}

    # --- OP-ACT activity (inline-steps engine + schedule executor,
    # docs/SLICE_ACTIVITY.md sections 2-3, 11) ---------------------------

    @staticmethod
    def _activity_interval(inp: dict[str, Any]) -> dict[str, Any] | None:
        """The `schedule.interval` sub-dict if this activity is jittered, else
        None (one-shot / windowed-`once` -- unchanged current behavior).
        `schedule` is threaded as plain resolved-input data (no op-catalog
        change: `deploy-behavior-agent`'s catalog `inputs` already reserves a
        `schedule:obj` slot; `start-activity`/`wait-actor-ready` accept it the
        same way other ops accept resolved-input fields the catalog doc dict
        doesn't literally enumerate -- see the pass1_emit threading note)."""
        schedule = inp.get("schedule")
        if not isinstance(schedule, dict):
            return None
        interval = schedule.get("interval")
        return interval if isinstance(interval, dict) else None

    def _deploy_behavior_agent(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-ACT-010: write the rendered step-runner into the guest. Idempotent
        (overwrites). Does not run it -- `start-activity` does, gated on
        mirror-ready so capture is live first.

        When the activity's `schedule.interval` is set, ALSO renders + writes
        the seeded-jitter scheduler loop (section 11.3) -- `start-activity`
        launches it instead of firing the runner directly."""
        vmid = int(inp["vmid"])
        activity_id = str(inp["activity_id"])
        steps = inp.get("inline_steps") or []
        schedule = inp.get("schedule") if isinstance(inp.get("schedule"), dict) else None
        seed = inp.get("seed")
        try:
            runner = render_activity_runner(steps, activity_id=activity_id)
        except ActivityRenderError as e:
            raise AdapterError(
                f"deploy-behavior-agent[{activity_id}]: {e}",
                error_class="activity-render-failed",
            )
        paths = activity_paths(activity_id)
        # The runner lives under the activity dir, NOT _PROVISION_DIR -- create its
        # parent before writing, or write_file fails with ENOENT (the guest is
        # fresh; the dir doesn't exist until the runner itself first runs).
        activity_dir = paths["runner"].rsplit("/", 1)[0]
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        ga.exec(vmid, ["mkdir", "-p", activity_dir], timeout_s=30)
        ga.write_file(vmid, paths["runner"], runner, mode="0755")

        if self._activity_interval(inp) is not None:
            try:
                scheduler = render_activity_scheduler(
                    steps, activity_id=activity_id, schedule=schedule or {}, seed=seed,
                )
            except ActivityRenderError as e:
                raise AdapterError(
                    f"deploy-behavior-agent[{activity_id}]: {e}",
                    error_class="activity-render-failed",
                )
            ga.write_file(vmid, paths["scheduler"], scheduler, mode="0755")
            # The kill-then-start LAUNCHER also lives in its own written file
            # (code-review fix): `start-activity` execs it as
            # `["bash", <launch path>]`, whose argv never contains the
            # scheduler's path substring -- see render_activity_launch_script's
            # docstring for why inlining `pkill -f <scheduler>` into the
            # invoking `sh -lc` string self-kills the launcher instead.
            ga.write_file(
                vmid, paths["launch"], render_activity_launch_script(activity_id),
                mode="0755",
            )

        # A4 ground-truth marker shipper (docs/design/08-exercise-measurement.md):
        # if the operator has wired LOKI_URL (`AdapterConfig.loki_url`
        # -- the SAME config `deploy-sensor`'s `ship: loki` path already reads,
        # no new config plumbing), start the truth->Loki shipper unconditionally
        # -- unlike the sensors' per-sensor `export.ship:` opt-in, ground truth
        # should ship whenever the O2 telemetry plane is wired, one-shot or
        # jittered alike. Best-effort (mirrors `_deploy_sensor`'s ship-loki
        # block): a shipper hiccup must never fail the activity deploy.
        loki_url = getattr(self.config, "loki_url", "") or ""
        if loki_url:
            # Code-review fix (SHOULD-FIX #1): `touch` the truth log BEFORE
            # starting the shipper. The shipper waits for the file to exist
            # then `seek(0, 2)`s to its END -- a sub-second first step (e.g.
            # `{run: "true"}`, or the fast early steps of the AD kill chain
            # this feeds) can otherwise write marker #1 into the log before
            # the shipper's existence-poll notices the file, and seek-to-end
            # then skips straight past it: the LOCAL log still has it (ground
            # truth is never lost), but it never reaches Loki -- the coverage
            # dashboard would silently under-count. Touching first guarantees
            # an EMPTY file exists when the shipper opens it on a fresh
            # deploy (seek-to-end lands at offset 0, so it catches every
            # marker); on a re-deploy `touch` doesn't truncate, so the
            # shipper still correctly resumes past prior content.
            ga.exec(vmid, ["touch", paths["truth"]], timeout_s=30)
            bootstrap = render_activity_truth_shipper(activity_id, loki_url=loki_url)
            ga.write_file(vmid, paths["truth_ship_bootstrap"], bootstrap, mode="0755")
            ga.exec(vmid, [paths["truth_ship_bootstrap"]], timeout_s=60)
        return {"agent_deployed": True}

    def _start_activity(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-ACT-020: launch the deployed runner. Two modes:

        - one-shot / windowed-`once` (no `schedule.interval`): detached
          one-time fire, unchanged from the MVP. `wait-actor-ready` polls the
          completion marker.
        - jittered (`schedule.interval` set): execs the written LAUNCH script
          (section 11.3), which kills any prior scheduler instance and starts
          a fresh one, detached (a redo must not stack a second loop, the same
          restart-before-start pattern `render_sensor_setup`'s suricata block
          uses, LES-035). The kill+restart command MUST run from a FILE
          (`ga.exec(vmid, ["bash", <launch path>])`), never inlined into a
          `sh -lc "pkill -f <scheduler> ..."` string -- the invoking shell's
          own argv would then contain the scheduler path substring and
          self-SIGTERM before `setsid python3` ever ran (code-review fix:
          this was the original bug -- the launcher self-killed itself, the
          scheduler never armed, and `wait-actor-ready` timed out on EVERY
          jittered deploy). The loop itself runs indefinitely inside the
          window; `wait-actor-ready` confirms it is ARMED/running, not
          "completed".

        Returns immediately either way (setsid + background detaches so QGA
        guest-exec returns at once). Re-running re-fires/re-arms -- traffic
        generation is not idempotent by design (the point of attack replay;
        the catalog marks this op `redo`)."""
        vmid = int(inp["vmid"])
        activity_id = str(inp["activity_id"])
        paths = activity_paths(activity_id)
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        if self._activity_interval(inp) is not None:
            argv = ["bash", paths["launch"]]
        else:
            launch = f'setsid bash {paths["runner"]} </dev/null >/dev/null 2>&1 &'
            argv = ["sh", "-lc", launch]
        res = ga.exec(vmid, argv, timeout_s=op.timeout_s or 120)
        if not res.ok:
            raise AdapterError(
                f"start-activity[{activity_id}]: launch failed (exit "
                f"{res.exit_code}) in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="activity-launch-failed",
            )
        return {"activity_started": True}

    def _wait_actor_ready(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-ACT-030 (wait): two modes, mirroring `_start_activity`:

        - one-shot / windowed-`once`: poll the runner's completion marker
          until present (unchanged) -- "actor ready" degrades to "the
          one-shot chain finished" (docs/SLICE_ACTIVITY.md section 6).
        - jittered: poll for the scheduler PROCESS being alive (`pgrep -f` on
          its own script path -- an argv-present match, not a bare comm-name
          match, LES-035) -- "actor ready" means "armed/running", since a
          jittered activity runs indefinitely across the window and never
          reaches a one-shot "completed" state."""
        vmid = int(inp["vmid"])
        activity_id = str(inp["activity_id"])
        paths = activity_paths(activity_id)
        jittered = self._activity_interval(inp) is not None
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        budget = float(op.timeout_s or 180)
        interval = self._actor_poll_interval_s
        if jittered:
            check = ["pgrep", "-f", paths["scheduler"]]
            what = f'scheduler process {paths["scheduler"]!r}'
        else:
            check = ["test", "-f", paths["marker"]]
            what = f'completion marker {paths["marker"]}'
        # check-then-sleep: already-satisfied returns without sleeping.
        waited = 0.0
        while True:
            res = ga.exec(vmid, check, timeout_s=30)
            if res.ok:
                return {"actor_ready": True}
            if waited >= budget:
                break
            self._sleep(interval)
            waited += interval
        raise AdapterError(
            f"wait-actor-ready[{activity_id}]: {what} not present after "
            f"{budget:.0f}s in vmid={vmid}",
            error_class="activity-timeout",
        )

    # --- OP-OVL data overlay (seed-dataset MVP, docs/SLICE_OVERLAY.md) ------

    def _seed_dataset(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-040: materialize a synthetic dataset at the entry's path on the
        host. Renders a deterministic python3 seeder (seed folded with the data
        id), writes it into the guest, and runs it. Idempotent: re-running
        regenerates identical bytes."""
        vmid = int(inp["vmid"])
        data_id = str(inp["data_id"])
        entry = inp.get("data_entry") or {}
        try:
            seeder = render_dataset_seeder(entry, seed=inp.get("seed"))
        except DatasetRenderError as e:
            raise AdapterError(
                f"seed-dataset[{data_id}]: {e}", error_class="dataset-render-failed"
            )
        seeder_path = overlay_seeder_path(data_id)
        seeder_dir = seeder_path.rsplit("/", 1)[0]
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        ga.exec(vmid, ["mkdir", "-p", seeder_dir], timeout_s=30)
        ga.write_file(vmid, seeder_path, seeder, mode="0755")
        # python3 is baked into the template by imagebuild; fail loudly if absent
        # (it can't be apt-installed on an isolated host), same as OP-APP's ansible.
        check = ga.exec(vmid, ["sh", "-lc", "command -v python3"], timeout_s=30)
        if not check.ok:
            raise AdapterError(
                f"seed-dataset[{data_id}]: python3 not found in vmid={vmid}; bake "
                "it into the template (docs/IMAGE_BUILD.md)",
                error_class="dataset-no-python",
            )
        res = ga.exec(vmid, ["python3", seeder_path], timeout_s=op.timeout_s or 600)
        if not res.ok:
            raise AdapterError(
                f"seed-dataset[{data_id}]: seeder exited {res.exit_code} in "
                f"vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="dataset-seed-failed",
            )
        return {"dataset_seeded": True}

    def _install_defense_agent(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-050: configure + enable a host defense agent (the binary comes
        from an app/template; the overlay drops the config + enables the
        service). Renders a config/enable script and runs it in-guest."""
        vmid = int(inp["vmid"])
        defense_id = str(inp["defense_id"])
        entry = inp.get("defense_entry") or {}
        os_family = (inp.get("os_family") or "linux").lower()
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        if os_family == "windows":
            return self._install_defense_agent_windows(
                vmid, defense_id, entry, inp, ga, op
            )
        script = render_defense_agent_config(entry, config_content=inp.get("config_content"))
        path = defense_agent_script_path(defense_id)
        seeder_dir = path.rsplit("/", 1)[0]
        ga.exec(vmid, ["mkdir", "-p", seeder_dir], timeout_s=30)
        ga.write_file(vmid, path, script, mode="0755")
        res = ga.exec(vmid, [path], timeout_s=op.timeout_s or 600)
        if not res.ok:
            raise AdapterError(
                f"install-defense-agent[{defense_id}]: exited {res.exit_code} in "
                f"vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="defense-agent-failed",
            )
        return {"agent_installed": True}

    def _install_defense_agent_windows(
        self, vmid, defense_id, entry, inp, ga, op
    ) -> dict[str, Any]:
        """Host-side AD detection on a Windows DC over QGA-PowerShell: enable the
        AD audit subcategories (Security event log substrate Sysmon/Wazuh forward) +
        a best-effort DCSync SACL + a baked EDR service start. Runs post-promotion
        (gated on domain-ready)."""
        script = render_windows_defense_agent(
            entry, config_content=inp.get("config_content")
        )
        path = f"{_WIN_PROVISION_DIR}\\axr-defense-{_SAFE.sub('-', defense_id)}.ps1"
        ga.shell_exec(
            vmid,
            f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
            timeout_s=30,
        )
        ga.write_file(vmid, path, script)
        res = ga.exec(
            vmid,
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            timeout_s=op.timeout_s or 600,
        )
        if not res.ok:
            raise AdapterError(
                f"install-defense-agent[{defense_id}] (windows): exited "
                f"{res.exit_code} in vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="defense-agent-failed",
            )
        return {"agent_installed": True}

    def _install_agent(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-070 (A3 Stage A, docs/design/08-exercise-measurement.md):
        install a non-defense agent (Prometheus node-exporter, or the
        Sandcat/Caldera adversary agent) on one host. Mirrors
        `_install_defense_agent`'s structure with an `agent_kind` branch in
        place of the defense tool registry, so `install-defense-agent` stays
        defense-only. `agent_kind=adversary` REQUIRES `server` (the Caldera
        server this agent beacons to -- resolved by the planner from the
        overlay entry's `server:` field at emit time; the O2 Caldera server
        itself is Stage B, not yet stood up)."""
        vmid = int(inp["vmid"])
        agent_id = str(inp["agent_id"])
        kind = str(inp.get("agent_kind") or "")
        entry = inp.get("agent_entry") or {}
        os_family = (inp.get("os_family") or "linux").lower()
        ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )

        if kind == "exporter":
            script = (
                render_windows_exporter_agent(entry) if os_family == "windows"
                else render_exporter_agent(entry)
            )
        elif kind == "adversary":
            server = str(inp.get("server") or "")
            if not server:
                raise AdapterError(
                    f"install-agent[{agent_id}] (adversary): no Caldera server "
                    "configured -- set overlays.adversary[].server: (the O2 "
                    "Caldera server is Stage B, not yet stood up; "
                    "docs/design/08-exercise-measurement.md)",
                    error_class="agent-no-server",
                )
            script = (
                render_windows_sandcat_agent(entry, server=server) if os_family == "windows"
                else render_sandcat_agent(entry, server=server)
            )
        else:
            raise AdapterError(
                f"install-agent[{agent_id}]: unknown agent_kind {kind!r} "
                "(expected 'exporter' or 'adversary')",
                error_class="agent-unknown-kind",
            )

        if os_family == "windows":
            path = f"{_WIN_PROVISION_DIR}\\axr-agent-{_SAFE.sub('-', agent_id)}.ps1"
            ga.shell_exec(
                vmid,
                f"New-Item -ItemType Directory -Force -Path '{_WIN_PROVISION_DIR}' | Out-Null",
                timeout_s=30,
            )
            ga.write_file(vmid, path, script)
            res = ga.exec(
                vmid,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                timeout_s=op.timeout_s or 600,
            )
        else:
            path = agent_script_path(agent_id)
            agent_dir = path.rsplit("/", 1)[0]
            ga.exec(vmid, ["mkdir", "-p", agent_dir], timeout_s=30)
            ga.write_file(vmid, path, script, mode="0755")
            res = ga.exec(vmid, [path], timeout_s=op.timeout_s or 600)

        if not res.ok:
            raise AdapterError(
                f"install-agent[{agent_id}] ({kind}): exited {res.exit_code} in "
                f"vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="agent-install-failed",
            )
        return {"agent_installed": True}

    def _deploy_network_sensor(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-OVL-060 (Option C, docs/SLICE_OVERLAY.md §4b): configure Suricata on
        the standing network-sensor — sensor-tier aggregation infra fed a mirror of the
        sensor's capture iface. The network sensor vmid/iface come from adapter config
        (resolved here at deploy, not at plan), so a scenario with a sensor still
        plans without a network sensor; it fails loudly here if none is configured."""
        defense_id = str(inp["defense_id"])
        entry = inp.get("defense_entry") or {}
        vmid = int(self.config.network_sensor_vmid or 0)
        if not vmid:
            raise AdapterError(
                f"deploy-network-sensor[{defense_id}]: no network sensor configured "
                "(set NETWORK_SENSOR_VMID in adapter.conf). The network sensor is "
                "sensor-tier standing infra (docs/SLICE_OVERLAY.md §4b).",
                error_class="sensor-no-network-sensor",
            )
        iface = self.config.network_sensor_iface or "cap-mirror"
        script = render_network_sensor_setup(
            entry, iface=iface, rules_content=inp.get("config_content")
        )
        path = network_sensor_script_path(defense_id)
        sensor_dir = path.rsplit("/", 1)[0]
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        ga.exec(vmid, ["mkdir", "-p", sensor_dir], timeout_s=30)
        ga.write_file(vmid, path, script, mode="0755")
        res = ga.exec(vmid, [path], timeout_s=op.timeout_s or 600)
        if not res.ok:
            raise AdapterError(
                f"deploy-network-sensor[{defense_id}]: exited {res.exit_code} on "
                f"network sensor vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="sensor-setup-failed",
            )
        return {"sensor_live": True}

    # --- OP-MIR-050 deploy-sensor (in-scenario sensor VM, sensors §5) --------

    def _deploy_sensor(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """OP-MIR-050: provision a typed capture sensor inside its in-scenario VM
        over QGA (sensors design Phase B, docs/design/06-taps-sensors.md). Renders
        the per-type setup (flow-based gretap decap iface + the tool the `type`
        selects), delivers it over the guest-agent exec channel, and runs it.
        Idempotent (the script re-applies config + restarts)."""
        from axrange.config_exec.render import render_sensor_setup, sensor_script_path

        vmid = int(inp["vmid"])
        sensor_id = str(inp["sensor_id"])
        sensor_type = str(inp.get("sensor_type") or "tcpdump-sensor")
        iface = str(inp.get("capture_iface") or "gre-cap")
        # Disk-%/file-count pcap-retention watchdog thresholds (tcpdump-sensor
        # only; a no-op input for other types) -- operator-tunable via
        # adapter.conf CAPTURE_HIGH_WATER_PCT/CAPTURE_LOW_WATER_PCT/
        # CAPTURE_MAX_FILES, defaulting to 85/70/2000 (user decision +
        # review follow-up 2026-07-17) when unset/absent on `self.config`.
        high_pct = int(getattr(self.config, "capture_high_water_pct", 85) or 85)
        low_pct = int(getattr(self.config, "capture_low_water_pct", 70) or 70)
        max_files = int(getattr(self.config, "capture_max_files", 2000) or 2000)
        script = render_sensor_setup(
            sensor_id, sensor_type, iface=iface,
            config_content=inp.get("config_content"),
            capture_high_water_pct=high_pct, capture_low_water_pct=low_pct,
            capture_max_files=max_files,
        )
        path = sensor_script_path(sensor_id)
        sdir = path.rsplit("/", 1)[0]
        ga = self._ga_factory(self._client, ctx.cluster.default_node)
        ga.exec(vmid, ["mkdir", "-p", sdir], timeout_s=30)
        ga.write_file(vmid, path, script, mode="0755")
        res = ga.exec(vmid, [path], timeout_s=op.timeout_s or 900)
        if not res.ok:
            raise AdapterError(
                f"deploy-sensor[{sensor_id}] (type={sensor_type}): exited "
                f"{res.exit_code} on vmid={vmid}: {(res.stdout or res.stderr)[:300]}",
                error_class="sensor-setup-failed",
            )

        # export: { ship: loki } — tail the sensor's structured artifact to Loki
        # (sensors design Phase C). Resolved here from adapter config (plan stays
        # Loki-agnostic). A no-op (with a note) when the type has no Loki artifact
        # or LOKI_URL is unset — never a hard failure.
        if str(inp.get("ship") or "").lower() == "loki":
            from axrange.config_exec.render import (
                render_sensor_loki_shipper,
                sensor_loki_artifact,
                sensor_shipper_path,
            )
            artifact = sensor_loki_artifact(sensor_type)
            loki_url = self.config.loki_url
            if artifact and loki_url:
                ship = render_sensor_loki_shipper(
                    sensor_id, sensor_type, loki_url=loki_url, artifact=artifact
                )
                spath = sensor_shipper_path(sensor_id) + ".sh"
                ga.write_file(vmid, spath, ship, mode="0755")
                ga.exec(vmid, [spath], timeout_s=120)
            # else: silently skip — tcpdump pcaps don't ship to Loki, and an
            # unset LOKI_URL means the operator hasn't wired the telemetry plane.

        return {"sensor_ready": True}

    # --- OP-MIR-040 verify-capture (capture heartbeat, dynamic-tap §6) -------

    def _verify_capture(
        self, op: OpInstance, inp: dict[str, Any], ctx: ExecutionContext
    ) -> dict[str, Any]:
        """Inject a uniquely-keyed sentinel from the injector guest (over QGA),
        then confirm it at the sensor by reading the sensor's capture iface over
        QGA — within the op timeout. Fails LOUDLY when no sentinel is seen,
        so the silent-zero-capture case (CHANGELOG 2026-06-03) surfaces as a real
        deploy failure (docs/design/06-taps-sensors.md). Feeds mirror-ready.

        The sensor is ALWAYS read over QGA via `sensor_vmid` (2026-07-13,
        docs/WORK_ORDER_sensor_agnostic_te.md) — every sensor this op can ever be
        emitted for is either an in-scenario sensor VM (sensors design Phase B) or
        an A2-bound standing O2 sensor (also a VM, `axrange.o2control.taps`); the
        legacy SENSOR_SSH_* mgmt-SSH read path is retired, no fallback.

        **Windows injector (2026-07-24):** the INJECTOR (not the sensor —
        sensors are always Linux) may be a Windows host. `os_family` (from
        `inp`, `taps.py._compose_ops` threads the injector's real OS family)
        selects between `render_capture_sentinel` (POSIX sh, `ping -p`,
        broadcast unless a unicast target is given) and
        `render_capture_sentinel_windows` (PowerShell, .NET `Ping.Send`,
        ALWAYS unicast) — the sensor-side confirm command is unchanged
        either way, since both variants carry the identical ASCII token
        bytes in the ICMP payload.

        **Unicast target (fix round F1, `docs/WORK_ORDER_tap_scopes.md`):**
        `sentinel_target_ip` (the injector's own segment role forced it to
        BE the multi-homed router under an rx-only mirror,
        `taps.py._select_verify_injector`) takes priority over `gateway_ip`
        (the pre-existing "bystander leaf targets the router" case) when
        both are present — they are never the SAME address (a forced-router
        injector can't usefully target its own leg), and only one of the
        two is ever actually set on a given op in practice. Threaded into
        the LINUX branch too now, but NARROWLY: only the NEW
        `sentinel_target_ip` forces a unicast target there (the
        forced-router-injects-itself case, where broadcast resolves via the
        DEFAULT ROUTE and may miss the mirrored NIC entirely) -- `gateway_ip`
        ALONE still does NOT change Linux's broadcast default (unchanged
        invariant: the pre-existing "safe"/bystander-leaf cases already
        capture correctly via broadcast, and must keep doing so unchanged).
        Windows has no such distinction (it never had a broadcast option at
        all), so it falls back to `gateway_ip` when `sentinel_target_ip` is
        absent, exactly as before F1.
        """
        vmid = int(inp["vmid"])
        net_id = str(inp.get("net_id") or op.scope.get("net") or "")
        sensor_id = str(inp.get("sensor_id") or op.scope.get("sensor") or "")
        bridge = str(inp.get("bridge_name") or net_id)
        token = capture_sentinel_token(ctx.deploy_id, bridge)
        os_family = str(inp.get("os_family") or "linux").lower()
        gateway_ip = inp.get("gateway_ip")
        # NARROW on purpose (see docstring): Linux uses ONLY this new key;
        # Windows (below) additionally falls back to `gateway_ip`.
        sentinel_target_ip = inp.get("sentinel_target_ip")
        windows_target_ip = sentinel_target_ip or gateway_ip
        sensor_vmid = inp.get("sensor_vmid")
        if not sensor_vmid:
            raise AdapterError(
                f"verify-capture[{net_id}/{sensor_id}]: no sensor_vmid — the sensor "
                "must be read over QGA (the legacy SENSOR_SSH_* mgmt-SSH read path "
                "is retired, docs/WORK_ORDER_sensor_agnostic_te.md). This op must be "
                "emitted with the sensor's vmid threaded via inputs_from_outputs.",
                error_class="verify-capture-no-sensor-vmid",
            )
        # `ga` below is bound to ctx.cluster.default_node (the only node the
        # v1 planner ever places anything on — pass4_placement assigns every
        # host to default_node, docs/PLANNER.md §3.4 multi-node placement isn't
        # implemented). If a future planner ever places a sensor VM elsewhere,
        # reading it through a GuestAgent bound to the WRONG node would be a
        # silent/ambiguous PVE API failure rather than a clear error — guard it
        # now so that day fails loud instead of quietly misreading vmid state.
        sensor_node = inp.get("sensor_node")
        if sensor_node and sensor_node != ctx.cluster.default_node:
            raise AdapterError(
                f"verify-capture[{net_id}/{sensor_id}]: sensor vmid={sensor_vmid} "
                f"is placed on node {sensor_node!r} but this backend only reads "
                f"sensor VMs on ctx.cluster.default_node ({ctx.cluster.default_node!r}) "
                "— config_exec assumes single-node placement; add per-node "
                "GuestAgent routing here before enabling multi-node sensor "
                "placement.",
                error_class="sensor-cross-node-unsupported",
            )
        iface = str(inp.get("sensor_iface") or "gre-cap")
        is_windows_injector = os_family == "windows"

        # 1) Render + deliver the sentinel injector into the injector guest.
        # `injector_ga` is bound to the INJECTOR's real os_family (Windows or
        # Linux); `sensor_ga` stays the default (Linux — sensors are always
        # Linux VMs, sensors design Phase B / A2 standing inventory).
        injector_ga = self._ga_factory(
            self._client, ctx.cluster.default_node, os_family=os_family
        )
        sensor_ga = self._ga_factory(self._client, ctx.cluster.default_node)
        if is_windows_injector:
            if not windows_target_ip:
                raise AdapterError(
                    f"verify-capture[{net_id}/{sensor_id}]: injector vmid={vmid} "
                    "is Windows but no unicast target IP was resolved for this "
                    "net -- a Windows sentinel cannot fall back to broadcast "
                    "(unlike the Linux sentinel, docs/design/06-taps-sensors.md); "
                    "`taps.py._compose_ops`/`_select_verify_injector` "
                    "resolves this (a gateway IP, or -- fix round F1 -- a "
                    "unicast target for a forced-router injector).",
                    error_class="verify-capture-no-gateway",
                )
            runner = render_capture_sentinel_windows(
                token=token, target=str(windows_target_ip)
            )
            path = capture_sentinel_runner_path_windows(net_id, sensor_id)
            runner_dir = path.rsplit("\\", 1)[0]
            esc_dir = runner_dir.replace("'", "''")  # PS single-quote escape
            injector_ga.shell_exec(
                vmid,
                f"New-Item -ItemType Directory -Force -Path '{esc_dir}' | Out-Null",
                timeout_s=30,
            )
        else:
            # Fix round F1: an explicit unicast target (when resolved) beats
            # the broadcast default -- REQUIRED when the injector is itself
            # the multi-homed router under an rx-only mirror (broadcast on a
            # multi-homed host resolves via the DEFAULT ROUTE, not
            # necessarily the mirrored NIC); harmless/optional otherwise.
            runner = render_capture_sentinel(
                token=token,
                target=(str(sentinel_target_ip) if sentinel_target_ip else None),
            )
            path = capture_sentinel_runner_path(net_id, sensor_id)
            runner_dir = path.rsplit("/", 1)[0]
            injector_ga.exec(vmid, ["mkdir", "-p", runner_dir], timeout_s=30)
        injector_ga.write_file(vmid, path, runner, mode="0755")

        # 2) Inject + confirm within the op budget. Each round: start a bounded
        # sensor read window, fire the sentinel into it, check the result. Retrying
        # the round (not just one shot) absorbs a slow first-boot tap bring-up.
        # The budget is bounded by REAL elapsed time (monotonic deadline), not a
        # synthetic per-round counter, so a blocking sensor read can't run the op
        # far past op.timeout_s.
        budget = float(op.timeout_s or 120)
        # Split the budget into a per-round sensor-read window; >=1 round.
        per_round = max(8, int(budget / 3))
        confirm_cmd = capture_confirm_command(
            iface=iface, token=token, timeout_s=per_round
        )
        start = time.monotonic()
        last = "no sentinel observed at sensor"
        first_round = True
        while first_round or (time.monotonic() - start) < budget:
            first_round = False
            # Fire the sentinel in the BACKGROUND so it keeps emitting WHILE the
            # sensor read window is open — capture is real-time, so a blocking
            # inject-then-read races the frames past the reader before it starts
            # (the silent failure on dev45's first clean run). Linux: nohup+&
            # returns immediately. Windows: `Start-Process -WindowStyle Hidden`
            # is a genuinely DETACHED process (not a QGA-exec-session child,
            # unlike a bare `nohup … &` — LES-086 does not apply here), matching
            # the same detached-launch pattern already used for the Sandcat
            # agent planter (`render_windows_sandcat_agent`). Either way the
            # injector then pings for ~COUNT*interval into the read window below.
            try:
                if is_windows_injector:
                    esc_path = path.replace("'", "''")
                    inject_cmd = (
                        "Start-Process -FilePath 'powershell' -ArgumentList "
                        "@('-NoProfile','-ExecutionPolicy','Bypass','-File',"
                        f"'{esc_path}') -WindowStyle Hidden"
                    )
                    injector_ga.shell_exec(vmid, inject_cmd, timeout_s=20)
                else:
                    injector_ga.exec(
                        vmid,
                        ["sh", "-c", f"nohup {shlex.quote(path)} >/dev/null 2>&1 &"],
                        timeout_s=20,
                    )
            except (AdapterError, AdapterTimeout) as e:
                last = f"sentinel injection failed: {e}"
            try:
                # Read the sensor VM over QGA (same node as the injector, so the
                # same-node GuestAgent handles its vmid — sensor_vmid is required,
                # see the check above; no SENSOR_SSH_* fallback). Always the
                # LINUX-default `sensor_ga`, regardless of the injector's OS.
                r = sensor_ga.exec(
                    int(sensor_vmid), ["sh", "-c", confirm_cmd],
                    timeout_s=per_round + 10,
                )
                code = 0 if r.ok else 1
                out = (r.stdout or r.stderr or "")
                if code == 0:
                    return {"capture_verified": True, "token": token}
                last = (out or "exit %d" % code).strip()[:200]
            except AdapterTimeout as e:
                last = f"sensor read timed out: {e}"
            if (time.monotonic() - start) >= budget:
                break
            self._sleep(self._actor_poll_interval_s)
        raise AdapterError(
            f"verify-capture[{net_id}@{sensor_id}]: sentinel {token} from "
            f"injector vmid={vmid} did NOT reach the sensor (iface {iface}) within "
            f"{budget:.0f}s — capture is not proven live (silent-zero-capture). "
            f"Last: {last}",
            error_class="capture-not-verified",
        )
