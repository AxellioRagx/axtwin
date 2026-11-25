"""The traffic-app registry (`engine/src/axrange/trafficgen/SPEC.md`, "Config").

Lists every `catalog/traffic/apps/<id>/app.yaml` that is installed
(`axrange.paths.TRAFFIC_APPS_DIR`, plus the colon-separated
`AXRANGE_TRAFFIC_APPS_DIR` extras on top). There is no hard-coded table: a
directory with an `app.yaml` is an app. Loading is lazy and never raises for a
bad file -- a file that does not parse, or breaks a shape rule, becomes a
warning (the API serves it, the validator turns it into a finding), and the
other apps still load.

Shape rules checked here (the loader's half; cross-references such as "a mix
names an installed app" are validator checks):

* `app.id` equals the directory name;
* `label`, `proto`, `transport` (`tcp` | `udp`), `port` (1-65535) present;
* exactly one of `source.program` / `source.pcap`; a pcap file exists beside
  `app.yaml` and stays inside the app's directory;
* every program step has exactly one action key (`send`, `reply`,
  `send_text`, `reply_text`, `recv`, `delay_ms`), hex is hex, `size` / `fill`
  only on send/reply steps, `fill` given whenever `size` is;
* a UDP program has no `recv` steps (UDP carries whole messages);
* tunables are `{default, min, max}` or `{default, choices}`; a numeric
  default lies inside its bounds; a default is one of its choices;
* every `{placeholder}` a step uses is a tunable or a peer placeholder.
"""
from __future__ import annotations

import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML

from axrange.paths import TRAFFIC_APPS_DIR

_EXTRA_DIRS_ENV = "AXRANGE_TRAFFIC_APPS_DIR"
APP_FILE = "app.yaml"
TRANSPORTS = ("tcp", "udp")
#: Placeholders the planner fills from the line's peer, not from tunables.
PEER_PLACEHOLDERS = frozenset({"server_name", "server_ip", "client_ip"})
_ACTIONS = ("send", "reply", "send_text", "reply_text", "recv", "delay_ms")
_PADDABLE = ("send", "reply", "send_text", "reply_text")
_APP_KEYS = {"id", "label", "blurb", "proto", "transport", "port", "real_server_ok",
             "source", "tunables", "provenance"}
_HEX = re.compile(r"^(?:[0-9a-fA-F]{2})*$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class AppSchemaError(ValueError):
    """One app file breaks a shape rule; the message names the file."""


@dataclass(frozen=True)
class Tunable:
    name: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    choices: tuple[Any, ...] = ()


@dataclass(frozen=True)
class TrafficApp:
    id: str
    label: str
    blurb: str
    proto: str
    transport: str
    port: int
    real_server_ok: bool
    source_kind: str                         # "program" | "pcap"
    program: tuple[dict[str, Any], ...] = ()
    pcap: Optional[Path] = None
    tunables: dict[str, Tunable] = field(default_factory=dict)
    provenance: str = ""
    path: Optional[Path] = None              # the app.yaml

    def placeholders(self) -> set[str]:
        """Every `{name}` the program's text steps and sizes use."""
        out: set[str] = set()
        for st in self.program:
            for k in ("send_text", "reply_text", "size", "delay_ms"):
                v = st.get(k)
                if isinstance(v, str):
                    out |= _fields(v)
        return out


def _fields(text: str) -> set[str]:
    try:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}
    except ValueError as e:
        raise AppSchemaError(f"malformed placeholder in {text[:40]!r}: {e}") from e


def _num_or_placeholder(v: Any, what: str) -> None:
    if isinstance(v, bool):
        raise AppSchemaError(f"{what} must be a number or a '{{tunable}}', got {v!r}")
    if isinstance(v, (int, float)):
        if v < 0:
            raise AppSchemaError(f"{what} must be >= 0, got {v!r}")
        return
    if isinstance(v, str) and re.fullmatch(r"\{[a-z_][a-z0-9_]*\}", v):
        return
    raise AppSchemaError(f"{what} must be a number or a '{{tunable}}', got {v!r}")


def _load_step(i: int, st: Any, transport: str) -> dict[str, Any]:
    if not isinstance(st, dict):
        raise AppSchemaError(f"source.program[{i}] is not a mapping: {st!r}")
    actions = [k for k in _ACTIONS if k in st]
    if len(actions) != 1:
        raise AppSchemaError(f"source.program[{i}] needs exactly one of {list(_ACTIONS)}, got {sorted(st)}")
    act = actions[0]
    unknown = sorted(set(st) - {act, "size", "fill"})
    if unknown:
        raise AppSchemaError(f"source.program[{i}] has unknown key(s) {unknown}")
    if ("size" in st or "fill" in st) and act not in _PADDABLE:
        raise AppSchemaError(f"source.program[{i}]: size/fill only apply to {list(_PADDABLE)}")
    if "size" in st:
        _num_or_placeholder(st["size"], f"source.program[{i}].size")
        if "fill" not in st:
            raise AppSchemaError(f"source.program[{i}]: size needs a fill (hex)")
    if "fill" in st and (not isinstance(st["fill"], str) or not st["fill"] or not _HEX.match(st["fill"])):
        raise AppSchemaError(f"source.program[{i}].fill must be non-empty hex")
    v = st[act]
    if act in ("send", "reply") and (not isinstance(v, str) or not v or not _HEX.match(v)):
        raise AppSchemaError(f"source.program[{i}].{act} must be non-empty hex")
    if act in ("send_text", "reply_text") and (not isinstance(v, str) or not v):
        raise AppSchemaError(f"source.program[{i}].{act} must be a non-empty string")
    if act == "recv":
        if transport == "udp":
            raise AppSchemaError(f"source.program[{i}]: a UDP program has no recv steps (whole messages only)")
        _num_or_placeholder(v, f"source.program[{i}].recv")
    if act == "delay_ms":
        _num_or_placeholder(v, f"source.program[{i}].delay_ms")
    return dict(st)


def _load_tunable(name: str, raw: Any) -> Tunable:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise AppSchemaError(f"tunables: {name!r} is not a valid name (lowercase, digits, _)")
    if not isinstance(raw, dict) or "default" not in raw:
        raise AppSchemaError(f"tunables.{name} must be a mapping with a default")
    unknown = sorted(set(raw) - {"default", "min", "max", "choices"})
    if unknown:
        raise AppSchemaError(f"tunables.{name} has unknown key(s) {unknown}")
    d = raw["default"]
    choices = raw.get("choices")
    if choices is not None:
        if "min" in raw or "max" in raw:
            raise AppSchemaError(f"tunables.{name}: choices excludes min/max")
        if not isinstance(choices, list) or not choices:
            raise AppSchemaError(f"tunables.{name}.choices must be a non-empty list")
        if d not in choices:
            raise AppSchemaError(f"tunables.{name}: default {d!r} is not one of {choices}")
        return Tunable(name=name, default=d, choices=tuple(choices))
    lo, hi = raw.get("min"), raw.get("max")
    for what, b in (("min", lo), ("max", hi)):
        if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float))):
            raise AppSchemaError(f"tunables.{name}.{what} must be a number")
    if lo is not None or hi is not None:
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            raise AppSchemaError(f"tunables.{name}: a bounded tunable needs a numeric default")
        if lo is not None and hi is not None and lo > hi:
            raise AppSchemaError(f"tunables.{name}: min {lo} > max {hi}")
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            raise AppSchemaError(f"tunables.{name}: default {d} outside [{lo}, {hi}]")
    return Tunable(name=name, default=d, min=lo, max=hi)


def load_app(path: str | Path) -> TrafficApp:
    """Parse one `<id>/app.yaml`. Raises `AppSchemaError` (message without the
    path; `list_apps` adds it)."""
    path = Path(path)
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 -- any YAML/IO error is this file's fault
        raise AppSchemaError(f"unreadable: {e}") from e
    body = raw.get("app") if isinstance(raw, dict) else None
    if not isinstance(body, dict):
        raise AppSchemaError("file must have a top-level 'app:' mapping")
    unknown = sorted(set(body) - _APP_KEYS)
    if unknown:
        raise AppSchemaError(f"unknown key(s) {unknown}")
    aid = body.get("id")
    if aid != path.parent.name:
        raise AppSchemaError(f"app.id {aid!r} must equal its directory name {path.parent.name!r}")
    if not isinstance(aid, str) or not _ID.match(aid):
        raise AppSchemaError(f"app.id {aid!r} must be lowercase letters, digits and '-'")
    for k in ("label", "proto"):
        if not isinstance(body.get(k), str) or not body[k].strip():
            raise AppSchemaError(f"app.{k} is required (a non-empty string)")
    transport = body.get("transport")
    if transport not in TRANSPORTS:
        raise AppSchemaError(f"app.transport must be one of {TRANSPORTS}, got {transport!r}")
    port = body.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise AppSchemaError(f"app.port must be an integer 1-65535, got {port!r}")
    rso = body.get("real_server_ok", False)
    if not isinstance(rso, bool):
        raise AppSchemaError(f"app.real_server_ok must be true or false, got {rso!r}")
    tun_raw = body.get("tunables") or {}
    if not isinstance(tun_raw, dict):
        raise AppSchemaError("app.tunables must be a mapping")
    tunables = {n: _load_tunable(str(n), v) for n, v in tun_raw.items()}
    clash = sorted(set(tunables) & PEER_PLACEHOLDERS)
    if clash:
        raise AppSchemaError(f"tunables {clash} shadow peer placeholders {sorted(PEER_PLACEHOLDERS)}")
    src = body.get("source")
    if not isinstance(src, dict):
        raise AppSchemaError("app.source is required (a mapping with program or pcap)")
    kinds = [k for k in ("program", "pcap") if k in src]
    if len(kinds) != 1 or set(src) - {"program", "pcap"}:
        raise AppSchemaError(f"app.source needs exactly one of program / pcap, got {sorted(src)}")
    program: tuple[dict[str, Any], ...] = ()
    pcap: Optional[Path] = None
    if kinds[0] == "program":
        steps = src["program"]
        if not isinstance(steps, list) or not steps:
            raise AppSchemaError("app.source.program must be a non-empty list of steps")
        program = tuple(_load_step(i, st, transport) for i, st in enumerate(steps))
    else:
        name = src["pcap"]
        if not isinstance(name, str) or not name:
            raise AppSchemaError("app.source.pcap must be a file name")
        pcap = (path.parent / name).resolve()
        if path.parent.resolve() not in pcap.parents:
            raise AppSchemaError(f"app.source.pcap {name!r} must stay inside the app's directory")
        if not pcap.is_file():
            raise AppSchemaError(f"app.source.pcap {name!r} does not exist beside app.yaml")
    app = TrafficApp(
        id=aid, label=body["label"].strip(), blurb=str(body.get("blurb") or "").strip(),
        proto=body["proto"].strip(), transport=transport, port=port, real_server_ok=rso,
        source_kind=kinds[0], program=program, pcap=pcap, tunables=tunables,
        provenance=str(body.get("provenance") or "").strip(), path=path,
    )
    missing = sorted(app.placeholders() - set(tunables) - PEER_PLACEHOLDERS)
    if missing:
        raise AppSchemaError(f"placeholder(s) {missing} are neither tunables nor peer placeholders")
    return app


def app_dirs() -> list[Path]:
    extra = [Path(p).expanduser() for p in os.environ.get(_EXTRA_DIRS_ENV, "").split(":") if p.strip()]
    return [TRAFFIC_APPS_DIR, *extra]


def list_apps(dirs: Optional[list[Path]] = None) -> tuple[dict[str, TrafficApp], list[str]]:
    """Every installed app, by id, and one warning per file that did not load.
    Later directories win on an id collision (the extras override the
    catalog), with a warning."""
    apps: dict[str, TrafficApp] = {}
    warnings: list[str] = []
    for d in (dirs if dirs is not None else app_dirs()):
        if not d.is_dir():
            continue
        for f in sorted(d.glob(f"*/{APP_FILE}")):
            try:
                app = load_app(f)
            except AppSchemaError as e:
                warnings.append(f"{f}: {e}")
                continue
            if app.id in apps:
                warnings.append(f"{f}: app {app.id!r} overrides {apps[app.id].path}")
            apps[app.id] = app
    return apps, warnings


def get_app(app_id: str, dirs: Optional[list[Path]] = None) -> Optional[TrafficApp]:
    return list_apps(dirs)[0].get(app_id)
