"""The tap and traffic tiers that FOLLOW a twin's te deploy (2026-11-09).

The console's Deploy stands the whole twin up as one action, the CLI's
sequence `te deploy` -> `tap deploy` -> `tfc deploy`: once the te deploy has
completed, the twin's own tap plan and traffic profile (its manifest
defaults) are applied in turn. Each tier's outcome is recorded here, per te
deploy, so the twin page and the canvas pill can show it -- including the
outcomes that leave no deploy row behind: a tier SKIPPED because the twin
has no plan for it (no taps, no traffic profile), or one that FAILED before
its playout wrote anything (a tap with no sensor).

A tier's record is one of `pending` (queued behind te), `running`,
`completed` (`deploy_id` names its playout row), `failed` (`reason` says
why) or `skipped` (`reason` says why). Each tier fails on its own: a tap
failure does not stop the traffic tier.

Storage: one small JSON file BESIDE the state DB (`<db>.follow-on.json`),
the way the working copies live beside it -- live-deploy state, never in the
checkout. Writes take an exclusive `flock` and replace the file atomically.
Pure state; the sequencing itself is `service.run_follow_on`.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

#: The tiers that follow a te deploy, in the order they run.
FOLLOW_ON_TIERS: tuple[str, ...] = ("tap", "tfc")
STATUSES = ("pending", "running", "completed", "failed", "skipped")


def follow_on_path(state_db_path: str | Path) -> Path:
    p = Path(state_db_path)
    return p.with_name(p.name + ".follow-on.json")


class FollowOnStore:
    """`{te deploy id: {tier: {status, reason, deploy_id, at}}}`."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, state_db_path: str | Path) -> "FollowOnStore":
        return cls(follow_on_path(state_db_path))

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = self._read()
            yield data
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, te_deploy_id: str) -> dict[str, dict[str, Any]]:
        rec = self._read().get(te_deploy_id)
        return rec if isinstance(rec, dict) else {}

    def all(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {k: v for k, v in self._read().items() if isinstance(v, dict)}

    def mark(self, te_deploy_id: str, tier: str, status: str, *,
             reason: Optional[str] = None, deploy_id: Optional[str] = None) -> None:
        if tier not in FOLLOW_ON_TIERS or status not in STATUSES:
            raise ValueError(f"follow-on {tier!r}/{status!r} is not a tier/status")
        with self._locked() as data:
            rec = data.setdefault(te_deploy_id, {})
            rec[tier] = {"status": status, "reason": reason, "deploy_id": deploy_id, "at": time.time()}

    def forget(self, te_deploy_id: str) -> None:
        with self._locked() as data:
            data.pop(te_deploy_id, None)
