"""The `trex-astf` generator driver (`engine/src/axrange/trafficgen/SPEC.md`,
B5): drives a planned generator VM over the guest agent through the in-VM
wrapper `trafficgen/guest_tg.py`, pushed to `/opt/axrange-tg/tg.py`.

Beside `TrexAstfDriver` (the per-VM `knobs.side` form): an app mix is played
by a generator for traffic lines, so this driver is addressed per generator,
not per persona instance, and is called by the tfc playout's generator ops
(`provision-generator`, `load-traffic-profile`, `start-traffic`,
`update-traffic`, `stop-traffic`) rather than through `get_driver(drive)`.
Every call converges: the wrapper writes a desired state that the generator's
one controller reconciles (spike A6), so a retried op re-asserts, it does not
duplicate.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Optional

from axrange.personas.drivers.base import PersonaDriverError, write_binary_file

BASE = "/opt/axrange-tg"
TG = f"{BASE}/tg.py"
_TRAFFICGEN = Path(__file__).resolve().parents[2] / "trafficgen"


def _source(name: str) -> str:
    return (_TRAFFICGEN / name).read_text(encoding="utf-8")


def _last_json(text: str) -> dict[str, Any]:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {}


class TrexGeneratorDriver:
    """Stateless; every method takes the guest agent `ga` and the VM id."""

    def _run(self, ga: Any, vmid: int, args: list[str], *, timeout_s: int, what: str) -> dict[str, Any]:
        cmd = " ".join(shlex.quote(a) for a in ["python3", TG, *args])
        res = ga.exec(vmid, ["sh", "-c", cmd], timeout_s=timeout_s)
        if not res.ok:
            raise PersonaDriverError(
                f"generator vmid={vmid} {what} failed (exit {res.exit_code}): "
                f"{(res.stderr or res.stdout or '').strip()[-400:]}")
        return _last_json(res.stdout)

    def _push_text(self, ga: Any, vmid: int, path: str, text: str, mode: str = "0644") -> None:
        ga.write_file(vmid, path, text, mode=mode)

    def _push_file_if_changed(self, ga: Any, vmid: int, path: str, data: bytes) -> bool:
        want = hashlib.sha256(data).hexdigest()
        res = ga.exec(vmid, ["sh", "-c", f"sha256sum {shlex.quote(path)} 2>/dev/null | cut -d' ' -f1"], timeout_s=30)
        if res.ok and (res.stdout or "").strip() == want:
            return False
        write_binary_file(ga, vmid, path, data)
        return True

    def provision(self, ga: Any, vmid: int, *, document_json: str, legs: dict[str, str],
                  files: Optional[dict[str, bytes]] = None, timeout_s: int = 240) -> dict[str, Any]:
        """Push everything and run `tg.py provision`. `legs` maps the NIC index
        (= TRex port) to its MAC; `files` maps a replay file's published name
        to its bytes."""
        check = ga.exec(vmid, ["sh", "-c", "test -x /opt/trex/current/t-rex-64 && command -v python3 ethtool"],
                        timeout_s=30)
        if not check.ok:
            raise PersonaDriverError(
                f"generator vmid={vmid}: no baked TRex / python3 / ethtool; clone the `generator` image (trex-astf@v1)")
        ga.exec(vmid, ["mkdir", "-p", f"{BASE}/files", "/var/lib/axrange-tg"], timeout_s=30)
        self._push_text(ga, vmid, TG, _source("guest_tg.py"), mode="0755")
        self._push_text(ga, vmid, f"{BASE}/astf_profile.py", _source("astf_profile.py"))
        self._push_text(ga, vmid, f"{BASE}/generator.json", document_json)
        for name, data in sorted((files or {}).items()):
            self._push_file_if_changed(ga, vmid, f"{BASE}/files/{name}", data)
        return self._run(ga, vmid, ["provision", "--legs", json.dumps(legs, sort_keys=True),
                                    "--timeout", str(timeout_s - 30)],
                         timeout_s=timeout_s, what="provision")

    def load(self, ga: Any, vmid: int) -> dict[str, Any]:
        return self._run(ga, vmid, ["desire", "--state", "loaded"], timeout_s=120, what="load-traffic-profile")

    def start(self, ga: Any, vmid: int, mults: Optional[dict[str, float]] = None) -> dict[str, Any]:
        out = self._run(ga, vmid, ["desire", "--state", "running"], timeout_s=120, what="start-traffic")
        for line, mult in sorted((mults or {}).items()):
            if mult != 1.0:
                out = self.update(ga, vmid, line, mult)
        return out

    def update(self, ga: Any, vmid: int, line: str, mult: float) -> dict[str, Any]:
        if mult <= 0:
            raise PersonaDriverError(f"update-traffic {line}: multiplier must be > 0, got {mult}")
        return self._run(ga, vmid, ["desire", "--line", line, "--mult", repr(float(mult))],
                         timeout_s=60, what=f"update-traffic {line}")

    def stop(self, ga: Any, vmid: int, line: str) -> dict[str, Any]:
        return self._run(ga, vmid, ["desire", "--remove", line], timeout_s=60, what=f"stop-traffic {line}")

    def status(self, ga: Any, vmid: int) -> dict[str, Any]:
        return self._run(ga, vmid, ["status"], timeout_s=30, what="status")
