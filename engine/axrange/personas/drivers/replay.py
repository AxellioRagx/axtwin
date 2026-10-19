"""The `replay` driver (docs/design/07-traffic-personas.md,
docs/WORK_ORDER_persona_library.md §2): stage a captured template pcap,
remap its addressing onto the assigned node's IP via `tcprewrite`, and
replay it with `tcpreplay` — launched as a detached transient systemd unit
(LES-086 — never `nohup ... &`).

**Content deferred, not built here (scope note):** the actual per-persona
captured pcap templates (a real historian/IoT/OT trace, binary content, not
schema/registry data) are curated CONTENT — the same way an image's disk
bytes live outside `axrange.manifest`. This driver's job is the staging +
remap + replay MECHANISM; `template_pcap_path` (a host-side path to that
content) is an explicit `start()` argument the future Traffic tier (or a
content-curation follow-on) supplies, not something a `Persona` schema
entry carries. `docs/WORK_ORDER_persona_library.md`'s gated live-verify
("one replay persona") needs a template pcap supplied at that time.
"""
from __future__ import annotations

import shlex
from typing import Any

from axrange.config_exec.render import systemd_daemon_start_block
from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    heartbeat_unit,
    instance_paths,
    safe_label,
    stop_unit,
    write_binary_file,
)
from axrange.personas.schema import Persona


def render_replay_launch(
    instance_id: str,
    persona: Persona,
    *,
    template_path: str,
    replay_path: str,
    orig_ip: str,
    node_ip: str,
    iface: str = "eth0",
) -> str:
    """The bash bootstrap: fail loud if `tcprewrite`/`tcpreplay` aren't
    installed, remap `orig_ip` (the template's original device address) to
    `node_ip` in both directions (the capture may show the device as either
    src or dst), then (re)launch the replay as a detached transient systemd
    unit. Idempotent: re-running re-remaps + restarts."""
    paths = instance_paths(instance_id)
    unit = paths["unit"]
    # safe_label: both interpolations below land inside a bash DOUBLE-quoted
    # `echo "..."` string -- escape instance_id/persona.id there (code-review
    # fix), not just at systemd_daemon_start_block's own label= argument.
    safe_instance = safe_label(instance_id)
    check = (
        "if ! command -v tcprewrite >/dev/null 2>&1 || "
        "! command -v tcpreplay >/dev/null 2>&1; then\n"
        '  echo "[axrange] FATAL: tcpreplay/tcprewrite not installed for '
        f'persona {safe_instance}" >&2\n'
        "  exit 1\n"
        "fi\n"
    )
    remap = (
        f"tcprewrite --infile={shlex.quote(template_path)} "
        f"--outfile={shlex.quote(replay_path)} "
        f"--srcipmap={shlex.quote(orig_ip)}/32:{shlex.quote(node_ip)}/32 "
        f"--dstipmap={shlex.quote(orig_ip)}/32:{shlex.quote(node_ip)}/32\n"
    )
    # tcpreplay --loop=0 repeats the capture indefinitely -- the replay
    # persona's traffic is meant to be ongoing background chatter, not a
    # one-shot playback.
    cmd = f"exec tcpreplay -i {shlex.quote(iface)} --loop=0 {shlex.quote(replay_path)}"
    return (
        "#!/bin/bash\nset -u\n"
        + check
        + remap
        + systemd_daemon_start_block(
            unit, cmd, log_path=paths["log"],
            label=safe_label(f"persona {instance_id} ({persona.id}) replay"),
        )
    )


class ReplayDriver(PersonaDriver):
    """Stages the template pcap (true byte fidelity — `write_binary_file`,
    not the text-only `GuestAgent.write_file`), remaps addressing, and
    launches `tcpreplay` as a transient unit."""

    def start(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None = None,
        seed: Any = None,
        template_pcap_path: str | None = None,
        orig_ip: str | None = None,
        node_ip: str | None = None,
        iface: str = "eth0",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not template_pcap_path:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): replay driver needs "
                "template_pcap_path (a host-side captured pcap to stage + remap "
                "— content curation, see this module's docstring)"
            )
        if not orig_ip or not node_ip:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): replay driver needs both "
                "orig_ip (the template's original device address) and node_ip "
                "(the twin node's assigned IP) to remap addressing"
            )
        paths = instance_paths(instance_id)
        ga.exec(vmid, ["mkdir", "-p", paths["dir"]], timeout_s=30)

        with open(template_pcap_path, "rb") as f:
            raw = f.read()
        template_path = f"{paths['dir']}/template.pcap"
        replay_path = f"{paths['dir']}/replay.pcap"
        write_binary_file(ga, vmid, template_path, raw, mode="0644")

        launch_path = f"{paths['dir']}/launch.sh"
        ga.write_file(
            vmid, launch_path,
            render_replay_launch(
                instance_id, persona, template_path=template_path,
                replay_path=replay_path, orig_ip=orig_ip, node_ip=node_ip,
                iface=iface,
            ),
            mode="0755",
        )
        res = ga.exec(vmid, ["bash", launch_path], timeout_s=60)
        if not res.ok:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): replay launch failed "
                f"(exit {res.exit_code}) in vmid={vmid}: "
                f"{(res.stdout or res.stderr)[:300]}"
            )
        return {"started": True, "unit": paths["unit"]}

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        return stop_unit(ga, vmid, instance_id)

    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str
    ) -> dict[str, Any]:
        return heartbeat_unit(ga, vmid, instance_id)
