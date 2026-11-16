"""Live progress of the destroys this API process is running.

`DELETE /api/deploys/{id}` is synchronous and a big twin's teardown runs for
minutes (corp-twin-native: 96 VMs, ~7 min, 2026-11-16); without this the
console's destroy dialog could only say "destroying… te" until the DELETE
returned, which reads as stuck. `service.destroy_deploy` records the phase it
is in (the sibling cascade, then the te sweep's per-VM count from
`planner.teardown`'s `progress` hook) and `GET /api/deploys/{id}/destroy-
progress` reads it back.

In-memory and per process, on purpose: it is a view of work THIS process is
doing right now, not state -- the state store stays the record of what was
destroyed. A destroy run by another process (the CLI) shows as not running.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class DestroyProgress:
    deploy_id: str
    #: `cascade` (the tfc/tap siblings), then the te sweep's steps:
    #: `vms`, `snippets`, `network`.
    step: str
    done: int = 0
    total: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0


_lock = threading.Lock()
_running: dict[str, DestroyProgress] = {}


def begin(deploy_id: str) -> None:
    now = time.time()
    with _lock:
        _running[deploy_id] = DestroyProgress(deploy_id, "cascade", started_at=now, updated_at=now)


def update(deploy_id: str, step: str, done: int = 0, total: int = 0) -> None:
    with _lock:
        cur = _running.get(deploy_id)
        if cur is not None:
            _running[deploy_id] = replace(cur, step=step, done=done, total=total, updated_at=time.time())


def end(deploy_id: str) -> None:
    with _lock:
        _running.pop(deploy_id, None)


def get(deploy_id: str) -> Optional[DestroyProgress]:
    with _lock:
        return _running.get(deploy_id)
