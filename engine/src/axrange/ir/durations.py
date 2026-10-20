"""Duration / clock / weekday parsing shared by the activity `schedule:`
surface (docs/SLICE_ACTIVITY.md section 11) -- the seeded-jitter cadence
renderer (`config_exec/render.py`), its validator rules
(`validator/checks/activity.py` ACT-060/ACT-070), and the planner's shape
guard (`planner/pass1_emit.py`).

Pure and dependency-free, like `ir/capabilities.py` -- deliberately placed
here (not in `config_exec/` or `validator/`) so every layer that needs it can
import it without a layering violation: the planner must not import
`config_exec` (LES-025 reuse-downward), and the validator must not depend on
an execution backend either.
"""
from __future__ import annotations

import re
from typing import Any


class DurationParseError(Exception):
    """A duration / clock / weekday value the schedule surface can't parse."""


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}

# A jittered `schedule.interval`'s minimum floor, in seconds (docs/SLICE_
# ACTIVITY.md §11; code-review fix 2026-07-10). `min: "0s"`/near-zero
# intervals pass a naive `max >= min` check but peg the guest CPU (a
# `sleep(0)`-cadence busy loop firing the step chain continuously). Shared
# by the render-time guard (`config_exec/render.py`) and the validator
# (`ACT-060`) so the two enforcement points can't drift apart.
MIN_JITTER_INTERVAL_S = 1.0


def parse_duration(spec: Any) -> float:
    """Parse a duration like `"30s"` / `"5m"` / `"2h"` / `"1d"` (a bare
    number is seconds) to seconds. Raises DurationParseError on a bad shape.
    """
    if spec is None or str(spec).strip() == "":
        raise DurationParseError("empty duration")
    m = _DURATION_RE.match(str(spec))
    if not m:
        raise DurationParseError(
            f"bad duration {spec!r} (want e.g. '30s' / '5m' / '2h' / '1d')"
        )
    num, unit = m.group(1), m.group(2).lower()
    return float(num) * _DURATION_UNITS[unit]


_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_days(days: Any) -> list[int] | None:
    """`['Mon', 'Wed']` -> `[0, 2]` (sorted, deduped, Mon=0..Sun=6). Empty/None
    -> None (every day). Raises DurationParseError on an unknown day name."""
    if not days:
        return None
    out: set[int] = set()
    for d in days:
        key = str(d).strip().lower()[:3]
        if key not in _WEEKDAYS:
            raise DurationParseError(f"unknown schedule day {d!r}")
        out.add(_WEEKDAYS[key])
    return sorted(out)


def parse_clock(spec: Any) -> int:
    """`"HH:MM"` -> minutes since midnight. Raises DurationParseError on a
    bad shape or an out-of-range hour/minute."""
    if spec is None or str(spec).strip() == "":
        raise DurationParseError("empty time")
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(spec).strip())
    if not m:
        raise DurationParseError(f"bad time {spec!r} (want HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise DurationParseError(f"time out of range {spec!r}")
    return h * 60 + mi
