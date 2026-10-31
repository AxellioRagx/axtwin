"""The ONE place in `axrange.fidelity` that holds a `GuestAgent`
(`docs/WORK_ORDER_fidelity_scorecard.md` §5/§6 S3), plus the ONE helper the
CLI verb and the API endpoint both call (LES-037 -- `te inventory`-vs-
endpoint divergence is a past review finding this must not repeat).

**Read-only, as §9.6 defines it** (Authority: implementation (Claude Code),
open to veto): no package install, no op row, no state-store write, and the
temporary script removed after the run -- materially the action
`verify-capture` already performs on every tapped deploy. The probe runs
against the STANDING `corpapcn-twin`, which must not be destroyed,
`--update`d, `--reapply`ed or grown a node (adding so much as a package
trips `--update`'s mutation refusal).

**Delivery mirrors `config_exec/backend.py::_verify_capture`**: pick a
guest, `GuestAgent.write_file` a rendered script (base64-in-command,
LES-002), `GuestAgent.exec` it, read structured output back, fail loud on no
result (LES-001). `python3` is probed for FIRST, once per peer, and a
missing interpreter is a loud refusal, never a silent per-target skip
(LES-089) -- the same posture `personas/drivers/base.py` already takes.

**Injector selection reuses the SHAPE of
`o2control/taps.py::_select_verify_injector`, not the function**: the engine
side may not import `o2control` (LES-025, §5), and the question is a
different one anyway -- that helper picks a sentinel injector against a
MIRROR's direction, this one picks "a host on the same segment that is not
the target." What is reused is the discipline: prefer a single-homed leaf,
know when the router is the only option, and SAY SO rather than quietly
accept a structurally weaker answer (LES-121, LES-146).
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Callable, Optional

from axrange.fidelity import probe as probe_mod
from axrange.fidelity.probe import ProbeReport, ProbeTarget
from axrange.planner.executor import AdapterError, AdapterTimeout

# Where the probe lands in the guest, and the exec budget around it. `/tmp`
# is deliberate: the script is removed after the run, and nothing about it
# should survive a reboot of a twin host we are only reading.
GUEST_SCRIPT_PATH = "/tmp/axr-fidelity-probe.py"
DEFAULT_EXEC_TIMEOUT_S = 180


class ProbeRunError(Exception):
    """The probe could not be run (no peer, no interpreter, no readable
    result). Typed so the CLI/API render it as a refusal (LES-110)."""


# ---------------------------------------------------------------------------
# Target composition (pure -- no cluster call)
# ---------------------------------------------------------------------------
def _is_leaf(host: Any) -> bool:
    """Single-homed on twin segments -- the structural question, not the
    semantic one (`_select_verify_injector`'s own G3 note: a dual-homed
    `kind: host` is just as route-ambiguous as a router)."""
    return len([n for n in host.nets if not n.external]) <= 1


def select_probe_peer(
    hosts: list[Any], segment: str, *, exclude: set[str],
    is_linux: Optional[Callable[[str], bool]] = None,
) -> tuple[Optional[Any], bool]:
    """`(peer, router_only)` for `segment`: a deployed **Linux** host on that
    segment, not in `exclude`, preferring a single-homed leaf.

    **`is_linux` is the load-bearing argument.** The probe is a stdlib
    python3 script and the Windows base images carry no python3 at all
    (`personas/drivers/scripted.py`'s own reason for a PowerShell client),
    so a Windows peer cannot run it. Measured on the standing CorpAPCN twin
    before this existed: 34 of 65 targets were peered to Windows guests, and
    the first `command -v python3` refusal aborted the ENTIRE scorecard --
    a whole artifact lost to a host that was never a valid vantage. An
    unknown OS is excluded too: `Reproduced.is_linux` returns False for a
    host with no recorded `wait-os-ready` family, because "probably Linux"
    is a guess and this is the exact place guessing cost the artifact.

    `router_only=True` means the only candidate is multi-homed -- returned
    rather than hidden, so the caller can note it on the row instead of
    presenting a structurally weaker probe as an equal one.
    """
    candidates = [
        h for h in hosts
        if h.host_id not in exclude
        and h.vmid is not None
        and (h.vm_status or "") == "completed"
        and (is_linux is None or is_linux(h.host_id))
        and any(n.net_id == segment and not n.external for n in h.nets)
    ]
    if not candidates:
        return None, False
    leaves = sorted(
        (h for h in candidates if _is_leaf(h)), key=lambda h: h.host_id
    )
    if leaves:
        return leaves[0], False
    return sorted(candidates, key=lambda h: h.host_id)[0], True


def compose_probe_targets(
    facts: list[Any], hosts: list[Any],
    is_linux: Optional[Callable[[str], bool]] = None,
) -> tuple[list[ProbeTarget], list[str]]:
    """Turn row 5's observed listen facts into concrete probe targets.

    Every address is looked up HERE, in the orchestrator, and baked into the
    render as a literal -- the guest never resolves a name (§3.3;
    `probe.check_containment` enforces it at render time).

    `is_linux` is threaded through to `select_probe_peer` (see its docstring
    for why a Windows peer is not a valid vantage) and is what turns "no
    peer" from a silent skip into a NOTE naming the OS reason.
    """
    by_id = {h.host_id: h for h in hosts}
    targets: dict[str, ProbeTarget] = {}
    notes: list[str] = []
    # S11: `select_probe_peer` scans the whole roster; on a 138-host twin
    # with 65 targets that is 65 full scans of an answer that only ever
    # depends on `(segment, excluded host)`.
    peer_cache: dict[tuple[str, str], tuple[Optional[Any], bool]] = {}

    def _peer(segment: str, host_id: str) -> tuple[Optional[Any], bool]:
        key = (segment, host_id)
        if key not in peer_cache:
            peer_cache[key] = select_probe_peer(
                hosts, segment, exclude={host_id}, is_linux=is_linux,
            )
        return peer_cache[key]

    for fact in facts:
        for host_id in fact.candidates:
            host = by_id.get(host_id)
            if host is None:
                continue
            ip = next(
                (n.ip for n in host.nets
                 if n.net_id == fact.segment and not n.external and n.ip),
                None,
            )
            if not ip:
                notes.append(
                    f"{host_id}: no address recorded on segment "
                    f"{fact.segment!r} -- not probed"
                )
                continue
            peer, router_only = _peer(str(fact.segment), host_id)
            if peer is None:
                notes.append(
                    f"{host_id}: no deployed LINUX host other than itself on "
                    f"segment {fact.segment!r} to probe FROM -- 'does this "
                    "service answer' is a question only another host can ask, "
                    "and the probe is a stdlib python3 script a Windows guest "
                    "cannot run"
                )
                continue
            if router_only:
                notes.append(
                    f"{host_id}: the only available peer on {fact.segment!r} "
                    f"is multi-homed ({peer.host_id}) -- probing from it "
                    "anyway, recorded because it is a weaker vantage"
                )
            key = probe_mod.target_key(host_id, fact.port, fact.transport)
            targets[key] = ProbeTarget(
                key=key, host_id=host_id, ip=str(ip), segment=str(fact.segment),
                port=fact.port, transport=fact.transport, method=fact.method,
                observed_node_id=fact.observed_node_id,
                peer_host_id=peer.host_id, peer_vmid=peer.vmid,
                peer_is_router=router_only,
            )
    return [targets[k] for k in sorted(targets)], notes


# ---------------------------------------------------------------------------
# Execution (the GuestAgent lives here and nowhere else)
# ---------------------------------------------------------------------------
class QgaProbeRunner:
    """Runs the rendered probe in twin guests over QGA.

    Every backend seam is injectable (`ga_factory`), so a unit test fakes the
    guest agent instead of reaching a cluster and never resolves a gitignored
    `adapter.conf` through the process cwd (LES-208).
    """

    def __init__(
        self, *, node: str, ga_factory: Callable[..., Any],
        exec_timeout_s: int = DEFAULT_EXEC_TIMEOUT_S,
        target_timeout_s: float = probe_mod.DEFAULT_TARGET_TIMEOUT_S,
        script_path: str = GUEST_SCRIPT_PATH,
    ) -> None:
        self.node = node
        self._ga_factory = ga_factory
        self.exec_timeout_s = exec_timeout_s
        self.target_timeout_s = target_timeout_s
        self.script_path = script_path

    # -- helpers ---------------------------------------------------------
    def _ga(self) -> Any:
        return self._ga_factory(node=self.node)

    def _require_python3(self, ga: Any, vmid: int, host_id: str) -> None:
        res = ga.exec(vmid, ["sh", "-lc", "command -v python3"], timeout_s=30)
        if not getattr(res, "ok", False):
            raise ProbeRunError(
                f"probe peer {host_id} (vmid={vmid}): python3 not found in the "
                "guest -- the fidelity probe is stdlib-only python3 by design "
                "and installs NOTHING (the run is read-only against a standing "
                "twin). Bake python3 into the image "
                "(docs/IMAGE_BUILD.md) or exclude this host. Refusing rather "
                "than skipping silently (LES-089)."
            )

    # -- row 5 -----------------------------------------------------------
    def run(self, targets: list[ProbeTarget]) -> ProbeReport:
        report = ProbeReport(ran=True, targets=list(targets))
        if not targets:
            return report
        ga = self._ga()
        # One exec per PEER: a peer probes every target it can reach, so a
        # 41-fact probe is a handful of guest execs, not 41.
        by_peer: dict[tuple[str, int], list[ProbeTarget]] = {}
        for t in targets:
            if t.peer_vmid is None or not t.peer_host_id:
                report.errors.append(
                    f"probe target {t.key}: no peer resolved -- not probed"
                )
                continue
            by_peer.setdefault((t.peer_host_id, int(t.peer_vmid)), []).append(t)

        for (peer_host, peer_vmid), peer_targets in sorted(by_peer.items()):
            # BOTH the interpreter check and the render live INSIDE the
            # per-peer try (M1): a peer that cannot run the probe must cost
            # its own facts (they become row 5's `unmeasured`), never the
            # whole artifact. `select_probe_peer` already excludes non-Linux
            # peers, so reaching this refusal now means a Linux guest that
            # genuinely lacks python3 -- still loud (LES-089), still
            # recorded, but no longer fatal to 137 other hosts' rows.
            try:
                self._require_python3(ga, peer_vmid, peer_host)
                script = probe_mod.render_probe_script(
                    peer_targets, timeout_s=self.target_timeout_s,
                )
                ga.write_file(peer_vmid, self.script_path, script, mode="0700")
                res = ga.exec(
                    peer_vmid, ["python3", self.script_path],
                    timeout_s=self.exec_timeout_s,
                )
                stdout = getattr(res, "stdout", "") or ""
                stderr = getattr(res, "stderr", "") or ""
                try:
                    report.results.update(
                        probe_mod.parse_probe_output(stdout, peer_targets)
                    )
                except probe_mod.ProbeRenderError as e:
                    report.errors.append(
                        f"probe from {peer_host} (vmid={peer_vmid}): {e}"
                        + (f" stderr={stderr[:200]!r}" if stderr else "")
                    )
            except Exception as e:  # noqa: BLE001 -- see below
                # One unreachable guest must not sink the whole scorecard:
                # its facts become row 5's `unmeasured` (a MEASUREMENT gap,
                # not a claim that the service is absent) and the reason is
                # recorded here. A refusal that stops everything is right for
                # what changed is the blast radius: a refusal that stops
                # everything is wrong for one flaky QGA session.
                report.errors.append(
                    f"probe from {peer_host} (vmid={peer_vmid}) failed: "
                    f"{type(e).__name__}: {e}"
                )
            finally:
                # Read-only posture (§9.6): the temporary script is removed
                # after the run, on the failure path too.
                try:
                    ga.exec(
                        peer_vmid,
                        ["sh", "-lc", f"rm -f {shlex.quote(self.script_path)}"],
                        timeout_s=30,
                    )
                except Exception as e:  # noqa: BLE001 -- best-effort cleanup
                    report.errors.append(
                        f"probe cleanup on {peer_host} (vmid={peer_vmid}) "
                        f"failed, {self.script_path} may remain: {e}"
                    )
        return report

    # -- row 4 (deferred; --include-deferred) ----------------------------
    def run_listens(
        self, hosts: list[Any], report: ProbeReport, *,
        is_linux: Optional[Callable[[str], bool]] = None,
    ) -> None:
        """`ss -lnH -tu` on each row-5 TARGET host (row 4 probes the target,
        row 5 probes from a peer). Only the local-address column is parsed --
        no process, user or credential column is even requested (§6 S4).

        **Scoped, not swept (S6):** the host set is the row-5 targets, not
        the whole roster. Row 4's reference side is `nodes[].listen_ports`,
        so a host that carries no observed listen has nothing to compare
        against and running `ss` there is a guest exec that buys nothing.
        Non-Linux hosts are skipped for the same reason `select_probe_peer`
        excludes them: `ss` is `iproute2`, which the Windows images do not
        have -- and the skip is NOTED, never silent (LES-089).
        """
        ga = self._ga()
        report.listens_ran = True
        wanted = {t.host_id for t in report.targets}
        for host in sorted(hosts, key=lambda h: h.host_id):
            if wanted and host.host_id not in wanted:
                continue
            if is_linux is not None and not is_linux(host.host_id):
                report.notes.append(
                    f"{host.host_id}: row 4's `ss -lnH -tu` skipped -- not a "
                    "Linux guest (iproute2 is not on the Windows images)"
                )
                continue
            if host.vmid is None or (host.vm_status or "") != "completed":
                continue
            try:
                res = ga.exec(
                    int(host.vmid), ["sh", "-lc", probe_mod.LISTEN_COMMAND],
                    timeout_s=60,
                )
            except Exception as e:  # noqa: BLE001 -- one host must not sink the row
                report.errors.append(f"listen probe on {host.host_id}: {e}")
                continue
            if not getattr(res, "ok", False):
                report.errors.append(
                    f"listen probe on {host.host_id}: "
                    f"{(getattr(res, 'stderr', '') or '').strip()[:160]}"
                )
                continue
            report.listens[host.host_id] = probe_mod.parse_listen_output(
                getattr(res, "stdout", "") or ""
            )


def default_ga_factory(adapter_config: Optional[str] = None) -> tuple[Callable[..., Any], str]:
    """`(ga_factory, node)` bound to the configured cluster.

    Built here rather than in `__init__.py` so the pure half of the package
    stays importable with no adapter config on disk. The client is the
    ordinary fail-loud one, NOT `adapter_proxmox.resolve`'s best-effort
    short-timeout client (LES-128): a probe that cannot reach the cluster
    must say so, not degrade to "nothing answered."
    """
    from axrange.platform import adapter_for_config, load_config

    cfg = load_config(adapter_config)
    adapters = adapter_for_config(cfg)
    if not cfg.default_node or not adapters.inventory.is_configured():
        raise ProbeRunError(
            "adapter.conf names no PROXMOX_API_URL / default node -- the "
            "in-guest probe needs a cluster to reach. Run with --no-probe to "
            "produce the artifact-only rows (row 5 then reads not-computed, "
            "and the verdict is `incomplete`, never `not-golden`)."
        )
    def factory(*, node: str, os_family: str = "linux") -> Any:
        return adapters.guest_exec(node, os_family=os_family)

    return factory, cfg.default_node


# ---------------------------------------------------------------------------
# The ONE shared CLI/API helper (LES-037)
# ---------------------------------------------------------------------------
def scorecard_for_deploy(
    store: Any, deploy_id: str, *,
    reference_dir: Optional[str | Path] = None,
    reference_root: Optional[str | Path] = None,
    twin_configs_dir: Any = None,
    manifests: Any = None,
    role_overrides: Optional[dict[str, str]] = None,
    probe: bool = True,
    include_deferred: bool = False,
    attestation: Optional[dict[str, Any]] = None,
    adapter_config: Optional[str] = None,
    probe_runner: Any = None,
    generated_at: Optional[str] = None,
) -> Any:
    """Build one deploy's scorecard. Called by BOTH `axrange te scorecard`
    and `GET /api/deploys/{id}/scorecard`, so the two can never produce
    different row data for the same deploy.

    `probe_runner` is an injectable seam: tests pass a fake and never touch a
    cluster (LES-208); production leaves it `None` and gets `QgaProbeRunner`
    bound to `adapter.conf`. `role_overrides` is passed IN, exactly as
    `build_twin_inventory` takes it -- resolving "which tfc deploy belongs to
    this twin" is `o2control`'s job and this package must not import it
    (LES-025).
    """
    from axrange.fidelity import (
        build_reproduced, build_scorecard, load_reference_bundle,
        resolve_bundle_dir, resolve_gate_info,
    )
    from axrange.fidelity.reference import ReferenceError
    from axrange.fidelity.rows import observed_listen_facts

    reproduced = build_reproduced(
        store, deploy_id, manifests=manifests, role_overrides=role_overrides,
    )
    bundle_dir = resolve_bundle_dir(
        reference_dir=reference_dir, reference_root=reference_root,
        bundle_id=reproduced.scenario_id,
    )
    if bundle_dir is None:
        raise ReferenceError(
            f"no cast bundle found for {reproduced.scenario_id!r} -- pass "
            "--reference <bundle-dir> to pin one. A twin deployed from a "
            "hand-authored config has no cast bundle, and every reference-side "
            "row would be not-computed without it."
        )
    reference = load_reference_bundle(bundle_dir)

    # Confirm the deploy threshold against the SAME catalog the reference
    # bundle came from -- pinned to one directory, never a first-hit chain
    # across catalogs (LES-198).
    gate = resolve_gate_info(
        config_id=reproduced.scenario_id, ir_hash=reproduced.ir_hash,
        catalog_dir=twin_configs_dir or [reference.bundle_dir.parent],
    )

    extra_warnings: list[str] = []
    if reproduced.scenario_id and reference.bundle_id != reproduced.scenario_id:
        extra_warnings.append(
            f"the deploy's scenario_id {reproduced.scenario_id!r} differs from "
            f"the cast bundle id {reference.bundle_id!r} -- this scorecard "
            "certifies a twin against a reference it may not have been cast "
            "from (§7 preflight)"
        )

    report: Optional[ProbeReport] = None
    if probe:
        # S2: every adapter/render failure below is re-raised as the ONE
        # typed refusal the CLI and API already handle, so a bad address, an
        # unreachable cluster or a QGA timeout surfaces as a legible message
        # and an exit code -- never a CLI traceback or an HTTP 500.
        try:
            facts = observed_listen_facts(reference, reproduced, gate)
            targets, notes = compose_probe_targets(
                facts, reproduced.hosts, is_linux=reproduced.is_linux,
            )
            runner = probe_runner
            if runner is None and (targets or include_deferred):
                # Built ONLY when there is something to run: a twin whose
                # observed listen set resolves to no target should still
                # produce a document on a host with no `adapter.conf`.
                factory, node = default_ga_factory(adapter_config)
                runner = QgaProbeRunner(node=node, ga_factory=factory)
            report = ProbeReport(ran=True, targets=list(targets))
            if targets and runner is not None:
                report = runner.run(targets)
            report.notes.extend(notes)
            if include_deferred and runner is not None:
                runner.run_listens(
                    reproduced.hosts, report, is_linux=reproduced.is_linux,
                )
        except ProbeRunError:
            raise
        except (probe_mod.ProbeRenderError, AdapterError, AdapterTimeout) as e:
            raise ProbeRunError(
                f"the in-guest probe could not run: {type(e).__name__}: {e}. "
                "Re-run with --no-probe to produce the artifact-only rows "
                "(row 5 then reads not-computed, and the verdict is "
                "`incomplete`, never `not-golden`)."
            ) from e
    elif include_deferred:
        extra_warnings.append(
            "--include-deferred was requested with --no-probe: row 4's "
            "in-guest listen check needs a guest, so it stays not-computed"
        )

    return build_scorecard(
        reference=reference, reproduced=reproduced, gate=gate,
        probe_report=report, attestation=attestation,
        generated_at=generated_at, extra_warnings=extra_warnings,
    )
