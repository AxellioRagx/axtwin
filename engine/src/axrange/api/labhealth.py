"""`GET /api/targets/{name}/health` (`docs/design/targets-and-lab.md` §2.2).

One status per lab component -- `up | down | unknown`, `checked_at`, a
one-line `detail` -- cached 15 s per target so the Targets page can poll.

- **url components** (telemetry / attack / defence): HTTP GET on
  `reach.url + health_http`, 2 s timeout; 2xx is `up`, anything else or no
  answer is `down`. Wazuh's manager API answers 401 to an unauthenticated
  GET and has no open health path, so for `type: wazuh` a 401 is `up` (the
  API is answering). TLS is not verified: the probe sends no credential and
  only asks whether the service answers. A component with no probe path is
  `unknown`.
- **sensors**: the engine has no tap-metrics read to lean on yet, and a
  sensor's `transport_ip` lives on the transport wire, usually not routable
  from the API host. So: resolve the sensor's VM on the platform
  (`Inventory.external_sensor_facts`, its management IP) and TCP-connect to
  :22 there; else try :22 on `transport_ip`; `up` when either answers. When
  the platform resolved an IP but nothing answers, `down`; when nothing
  resolved and the transport is not reachable from here, `unknown`, saying
  so.

Probes run in parallel (one thread per component), so one hung service
costs the poll 2 s, not 2 s per component.
"""
from __future__ import annotations

import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from axrange.api import dtos

TIMEOUT_S = 2.0
CACHE_TTL_S = 15.0

_cache: dict[str, tuple[float, dtos.TargetHealthDTO]] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def clear_cache() -> None:
    _cache.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def http_status(url: str, timeout: float = TIMEOUT_S) -> tuple[Optional[int], str]:
    """`(status, detail)`; status None when nothing answered."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "axrange-health"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310 -- operator-declared URL
            return int(resp.status), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return int(e.code), f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        return None, f"no answer ({reason})"


def tcp_open(host: str, port: int, timeout: float = TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _url_component(c: Any) -> dtos.ComponentHealthDTO:
    path = c.health_http
    if not c.url or not path:
        return dtos.ComponentHealthDTO(
            id=c.id, role=c.role, status="unknown", checked_at=_now_iso(),
            detail="no health probe declared for this component",
        )
    url = c.url.rstrip("/") + path
    code, detail = http_status(url)
    if code is not None and 200 <= code < 300:
        status = "up"
    elif code == 401 and c.type == "wazuh":
        status, detail = "up", "HTTP 401 (the API answers; it needs credentials)"
    else:
        status = "down"
    return dtos.ComponentHealthDTO(
        id=c.id, role=c.role, status=status, checked_at=_now_iso(),
        detail=f"GET {url}: {detail}",
    )


def _sensor_component(c: Any, fact: Optional[dict[str, Any]]) -> dtos.ComponentHealthDTO:
    reach = c.reach
    resolved_ip = (fact or {}).get("resolved_ip")
    resolved_name = (fact or {}).get("resolved_name")
    vmid = reach.get("vmid")
    if resolved_ip:
        ok = tcp_open(resolved_ip, 22)
        who = f"VM {resolved_name or vmid} at {resolved_ip}"
        return dtos.ComponentHealthDTO(
            id=c.id, role=c.role, status="up" if ok else "down", checked_at=_now_iso(),
            detail=f"{who}: ssh {'answers' if ok else 'does not answer'}",
        )
    tip = reach.get("transport_ip")
    if tip and tcp_open(str(tip), 22):
        return dtos.ComponentHealthDTO(
            id=c.id, role=c.role, status="up", checked_at=_now_iso(),
            detail=f"transport {tip}: ssh answers",
        )
    return dtos.ComponentHealthDTO(
        id=c.id, role=c.role, status="unknown", checked_at=_now_iso(),
        detail=(
            f"VM {vmid} not resolved on the platform, and transport {tip} is not "
            "reachable from the API host"
        ),
    )


def _sensor_facts(cfg: Any, ids: set[str]) -> dict[str, dict[str, Any]]:
    if cfg is None or not ids:
        return {}
    try:
        from axrange.platform import adapter_for_config

        facts = adapter_for_config(cfg).inventory.external_sensor_facts(only_ids=ids)
    except Exception:  # noqa: BLE001 -- best-effort: an unreachable platform resolves nothing
        return {}
    return {str(f.get("id")): f for f in facts}


def compute(target: Any, facts: Any) -> dtos.TargetHealthDTO:
    comps = list(target.lab.components)
    if not comps:
        return dtos.TargetHealthDTO(target=target.name, components=[])
    sensor_ids = {c.id for c in comps if c.role == "sensor"}
    with ThreadPoolExecutor(max_workers=min(16, len(comps) + 1)) as pool:
        sf = pool.submit(_sensor_facts, getattr(facts, "cfg", None), sensor_ids)
        url_futs = {c.id: pool.submit(_url_component, c) for c in comps if c.role != "sensor"}
        sensor_fact = sf.result()
        sensor_futs = {
            c.id: pool.submit(_sensor_component, c, sensor_fact.get(c.id))
            for c in comps if c.role == "sensor"
        }
        by_id = {**{k: f.result() for k, f in url_futs.items()},
                 **{k: f.result() for k, f in sensor_futs.items()}}
    return dtos.TargetHealthDTO(target=target.name, components=[by_id[c.id] for c in comps])


def target_health(target: Any, facts: Any) -> dtos.TargetHealthDTO:
    """The cached health of `target`'s lab (recomputed after `CACHE_TTL_S`).
    Concurrent pollers of one target share one probe run."""
    hit = _cache.get(target.name)
    if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    with _locks_guard:
        lock = _locks.setdefault(target.name, threading.Lock())
    with lock:
        hit = _cache.get(target.name)
        if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
        out = compute(target, facts)
        _cache[target.name] = (time.monotonic(), out)
        return out
