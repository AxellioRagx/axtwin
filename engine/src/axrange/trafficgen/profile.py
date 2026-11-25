"""The tfc profile's trafficgen blocks: `budget`, `emulated`, `lines`
(`SPEC.md` "tfc profile additions", signed off as T2).

Shape only, fail loud: every unknown key inside these blocks is refused (the
tfc parser itself ignores unknown top-level keys). Whether a group resolves, a
mix exists, the rate fits the budget and the addresses fit the segment are
validator checks (`trafficgen.validate`, rules TGN-0xx).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Budget:
    cps: float
    mbps: float


@dataclass(frozen=True)
class EmulatedSpec:
    group: str
    count: int
    external: bool = False
    segment: Optional[str] = None      # required iff external
    role: Optional[str] = None         # required iff external
    start: Optional[str] = None


@dataclass(frozen=True)
class LineSpec:
    id: str
    from_group: str
    to_group: str
    mix: str
    rate_cps: float
    max_concurrent: int


_EMULATED_KEYS = {"group", "count", "external", "segment", "role", "start"}
_LINE_KEYS = {"id", "from", "to", "mix", "rate_cps", "max_concurrent"}
_BUDGET_KEYS = {"cps", "mbps"}


def _num(v: Any, what: str, err: Callable[[str], Exception], *, integer: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and not isinstance(v, int)):
        raise err(f"{what} must be {'an integer' if integer else 'a number'}, got {v!r}")
    if v <= 0:
        raise err(f"{what} must be > 0, got {v!r}")
    return v


def _name(v: Any, what: str, err: Callable[[str], Exception]) -> str:
    if not isinstance(v, str) or not v.strip():
        raise err(f"{what} is required (a non-empty string)")
    return v.strip()


def parse_trafficgen_blocks(
    body: dict[str, Any], err: Callable[[str], Exception],
) -> tuple[Optional[Budget], list[EmulatedSpec], list[LineSpec]]:
    """Parse `budget` / `emulated` / `lines` out of a `traffic-profile:` body.
    `err(message)` builds the exception the caller raises (the tfc parser's
    `TrafficPlanError`)."""
    budget: Optional[Budget] = None
    raw_b = body.get("budget")
    if raw_b is not None:
        if not isinstance(raw_b, dict):
            raise err("traffic-profile.budget must be a mapping {cps, mbps}")
        unknown = sorted(set(raw_b) - _BUDGET_KEYS)
        missing = sorted(_BUDGET_KEYS - set(raw_b))
        if unknown or missing:
            raise err(f"traffic-profile.budget: unknown {unknown}, missing {missing} (expected cps, mbps)")
        budget = Budget(cps=_num(raw_b["cps"], "budget.cps", err), mbps=_num(raw_b["mbps"], "budget.mbps", err))

    raw_e = body.get("emulated") or []
    if not isinstance(raw_e, list):
        raise err("traffic-profile.emulated must be a list")
    emulated: list[EmulatedSpec] = []
    seen: set[str] = set()
    for i, e in enumerate(raw_e):
        w = f"emulated[{i}]"
        if not isinstance(e, dict):
            raise err(f"{w} must be a mapping")
        unknown = sorted(set(e) - _EMULATED_KEYS)
        if unknown:
            raise err(f"{w}: unknown key(s) {unknown} (expected any of {sorted(_EMULATED_KEYS)})")
        group = _name(e.get("group"), f"{w}.group", err)
        if group in seen:
            raise err(f"{w}: group {group!r} appears twice in emulated[]")
        seen.add(group)
        count = int(_num(e.get("count"), f"{w}.count", err, integer=True))
        external = e.get("external", False)
        if not isinstance(external, bool):
            raise err(f"{w}.external must be true or false")
        segment, role = e.get("segment"), e.get("role")
        if external:
            segment = _name(segment, f"{w}.segment (required for an external group)", err)
            role = _name(role, f"{w}.role (required for an external group)", err)
        elif segment is not None or role is not None:
            raise err(f"{w}: segment/role are only for an external group (a te group brings its own)")
        start = e.get("start")
        if start is not None:
            try:
                start = str(ipaddress.IPv4Address(start))
            except (ValueError, TypeError) as ex:
                raise err(f"{w}.start {start!r} is not an IPv4 address") from ex
        emulated.append(EmulatedSpec(group=group, count=count, external=external,
                                     segment=segment, role=role, start=start))

    raw_l = body.get("lines") or []
    if not isinstance(raw_l, list):
        raise err("traffic-profile.lines must be a list")
    lines: list[LineSpec] = []
    ids: set[str] = set()
    for i, ln in enumerate(raw_l):
        w = f"lines[{i}]"
        if not isinstance(ln, dict):
            raise err(f"{w} must be a mapping")
        unknown = sorted(set(ln) - _LINE_KEYS)
        if unknown:
            raise err(f"{w}: unknown key(s) {unknown} (expected any of {sorted(_LINE_KEYS)})")
        frm = _name(ln.get("from"), f"{w}.from", err)
        to = _name(ln.get("to"), f"{w}.to", err)
        mix = _name(ln.get("mix"), f"{w}.mix", err)
        rate = float(_num(ln.get("rate_cps"), f"{w}.rate_cps", err))
        mc = ln.get("max_concurrent")
        max_conc = int(_num(mc, f"{w}.max_concurrent", err, integer=True)) if mc is not None else max(1, int(round(rate * 10)))
        lid = ln.get("id")
        lid = _name(lid, f"{w}.id", err) if lid is not None else f"{frm}--{to}--{mix}"
        if lid in ids:
            raise err(f"{w}: line id {lid!r} is not unique (give one an explicit id:)")
        ids.add(lid)
        lines.append(LineSpec(id=lid, from_group=frm, to_group=to, mix=mix, rate_cps=rate, max_concurrent=max_conc))

    if lines and budget is None:
        raise err("traffic-profile.budget {cps, mbps} is required when lines[] is present")
    return budget, emulated, lines
