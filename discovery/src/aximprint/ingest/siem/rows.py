"""What a SIEM row MEANS — the semantics every vendor module shares.

`http.py` moves rows; this module reads them. Three source families reach a SIEM, and each
yields `state_class` differently (`ingest/siem/SPEC.md`, "state_class"):

* **Zeek-fed rows** (Corelight add-on, Filebeat's `zeek.*` datasets, a plain JSON shipper)
  keep `conn_state` and go through the Zeek reader's own table (`ingest/zeek/plugin.py`
  `_CONN_STATE_CLASS`, via `_conn`), so a Zeek-fed SIEM yields the Zeek reader's fidelity.
  Every other Zeek stem (`dns`, `ssl`, `http`, `dhcp`, `kerberos`, `ntlm`, `smb_mapping`, …)
  goes through that reader's parser for the stem — ONE table, not a SIEM-side copy of it.
* **Firewall rows** carry an action. Allowed with return bytes -> `established`; allowed with
  none -> `attempted`; blocked -> `unknown`, because a drop proves nothing about the host
  behind it (the packet never reached it), so neither `refused` nor `attempted` is honest.
* **NetFlow/IPFIX rows** are unpaired: one direction, no handshake outcome. `unknown`, with
  `counters_imputed` set when the per-direction counters are missing (a single `bytes` is
  the exporter's one direction, not the responder's reply).

Alert and incident tables are never mapped here: `AlertEvent` has no second producer yet,
and a SIEM's alert table is named into `unparsed_stems` by the vendor module instead.
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import json
from typing import Any, Callable, Iterator, Optional

from aximprint.events import Event, FlowEvent
from aximprint.ingest.zeek.plugin import _CONN_STATE_CLASS, _PARSERS

__all__ = [
    "ALLOWED_ACTIONS", "BLOCKED_ACTIONS", "ZEEK_ALERT_STEMS", "ZEEK_CONN_STATE_CLASS",
    "ZEEK_STEMS", "as_float", "as_int", "as_str", "epoch", "first_of", "firewall_state",
    "flatten", "flow_from", "is_alert_stem", "is_ip", "netflow_counters", "parse_raw_json",
    "zeek_events",
]

#: The Zeek reader's table, re-exported so a test can assert a Zeek-fed row's class came
#: from it rather than from a second copy.
ZEEK_CONN_STATE_CLASS = _CONN_STATE_CLASS

#: Zeek stems that are ALERT channels. Declined BY NAME for a SIEM pull: the Zeek reader maps
#: them to `AlertEvent` from a log directory it owns, but a SIEM's notice/weird tables are
#: alert tables and the SPEC keeps alert tables out of every plugin in this family until
#: `AlertEvent` has a measured second producer.
ZEEK_ALERT_STEMS = frozenset({"notice", "weird"})

#: Every Zeek stem a SIEM row may be routed to: the reader's own parser table minus the alert
#: stems. Closed — a Zeek-named row whose stem is not here is COUNTED (`unrecognized_stems`),
#: never guessed at.
ZEEK_STEMS = frozenset(_PARSERS) - ZEEK_ALERT_STEMS

#: A firewall's "the session was allowed" vocabulary across the CIM (`action=allowed`,
#: `teardown` is an allowed session's end) and ECS (`event.type: allowed`, `event.action`
#: free text from the common firewall modules). Lower-cased membership; anything else is
#: neither allowed nor blocked and falls to the NetFlow rule.
ALLOWED_ACTIONS = frozenset({"allowed", "allow", "accept", "accepted", "permit", "permitted",
                             "teardown", "pass", "success"})
BLOCKED_ACTIONS = frozenset({"blocked", "block", "deny", "denied", "drop", "dropped", "reject",
                             "rejected", "failure"})


def epoch(value: object, field: str, error: type[Exception]) -> float:
    """One timestamp normalizer for the shapes a SIEM returns: epoch seconds or milliseconds
    as a number or numeric string, or ISO-8601 (`2026-10-28T09:00:00.000Z`, `+00:00`). Fails
    loud on anything else — a wrong `ts` silently reassigns an event to another window."""
    if isinstance(value, bool) or value is None or value == "":
        raise error(f"{field}: unrecognized timestamp shape {value!r}")
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            v = float(text)
        except ValueError:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = _dt.datetime.fromisoformat(text)
            except ValueError as exc:
                raise error(f"{field}: unparseable timestamp {value!r}") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp()
    else:
        raise error(f"{field}: unrecognized timestamp shape {value!r}")
    # Epoch milliseconds (ES `@timestamp` as `epoch_millis`, ~1.8e12 today) vs seconds
    # (~1.8e9): three orders of magnitude apart, no threshold anyone has to tune.
    return v / 1000.0 if v > 1e11 else v


def flatten(doc: dict, prefix: str = "") -> dict[str, Any]:
    """A nested document as dotted keys (`{"source": {"ip": x}}` -> `{"source.ip": x}`).
    Lists are leaves (a list of answers stays a list). A key that is ALREADY dotted keeps its
    dots, so a flattened Zeek row (`id.orig_h`) and a nested ECS one read the same way."""
    out: dict[str, Any] = {}
    for k, v in doc.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def is_alert_stem(stem: str) -> bool:
    return stem in ZEEK_ALERT_STEMS


def zeek_events(stem: str, row: dict) -> Iterator[Event]:
    """Route a Zeek-shaped row (Zeek's own field names) through the Zeek reader's parser for
    `stem`. The caller has already decided the stem is in `ZEEK_STEMS`; this is the one place
    the SIEM family touches the Zeek table, so a `conn_state` reaches `_CONN_STATE_CLASS`
    exactly as it would from a log directory."""
    yield from _PARSERS[stem](row)


def parse_raw_json(raw: object) -> Optional[dict]:
    """A SIEM's verbatim event (`_raw`, `event.original`, `message`) when it is a JSON object
    — the Zeek row as the sensor wrote it, which beats any flattened re-extraction of it."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return None
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return blob if isinstance(blob, dict) else None


def firewall_state(action: str, *, bytes_in: Optional[int],
                   pkts_in: Optional[int]) -> tuple[str, str]:
    """(state, state_class) for a row that carries a firewall action (see module docstring).

    `state` keeps the action verbatim (lower-cased) so the derivation stays auditable. An
    action outside both vocabularies is `unknown` and counted as such downstream — never
    bucketed with a neighbour.
    """
    verb = action.strip().lower()
    if verb in BLOCKED_ACTIONS:
        return verb, "unknown"
    if verb in ALLOWED_ACTIONS:
        replied = (bytes_in or 0) > 0 or (pkts_in or 0) > 0
        return verb, "established" if replied else "attempted"
    return verb, "unknown"


def netflow_counters(bytes_in: Optional[int], bytes_out: Optional[int], pkts_in: Optional[int],
                     pkts_out: Optional[int], total_bytes: Optional[int],
                     total_pkts: Optional[int]) -> tuple[int, int, int, int, bool]:
    """(bytes_out, bytes_in, pkts_out, pkts_in, counters_imputed) for an unpaired row.

    Per-direction counters present -> taken as observed. Only a total -> it is the exporter's
    ONE direction (originator -> responder), the reply side is unknown and reads as 0 WITH
    `counters_imputed` set so no window publishes that 0 as observed (CONVENTIONS §4,
    "imputed never masquerades as observed").
    """
    paired = bytes_in is not None and bytes_out is not None
    if paired:
        return (int(bytes_out or 0), int(bytes_in or 0), int(pkts_out or 0), int(pkts_in or 0),
                False)
    return (int(bytes_out if bytes_out is not None else (total_bytes or 0)),
            int(bytes_in or 0),
            int(pkts_out if pkts_out is not None else (total_pkts or 0)),
            int(pkts_in or 0),
            True)


def flow_from(ts: float, src_ip: str, dst_ip: str, src_port: int, dst_port: int, proto: str,
              service: str, duration: float, state: str, state_class: str, bytes_out: int,
              bytes_in: int, pkts_out: int, pkts_in: int, counters_imputed: bool) -> FlowEvent:
    """One constructor for every non-Zeek flow in the family, so the shared `None`s are spelled
    once: `local_*` and `vlan_*` stay `None` — the sensor said nothing, and `segments.py`
    imputes locality for this family exactly as it does for axquery (never a guess here)."""
    return FlowEvent(
        ts=ts, src_ip=src_ip, dst_ip=dst_ip, src_port=src_port, dst_port=dst_port,
        proto=proto.lower(), service=service.lower(), duration=max(0.0, duration),
        bytes_out=bytes_out, bytes_in=bytes_in, pkts_out=pkts_out, pkts_in=pkts_in,
        state=state, state_class=state_class, counters_imputed=counters_imputed,
        originator_is_src=True, local_src=None, local_dst=None, vlan_src=None, vlan_dst=None,
    )


def as_int(value: object) -> Optional[int]:
    """`int`, or `None` when the field is absent/unset — never 0 for "not there"."""
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
        if value is None:
            return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: object) -> Optional[float]:
    """`float`, or `None` when absent/unset."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_ip(value: str) -> bool:
    """True only for a real IPv4/IPv6 literal — a DNS answer that is a NAME binds nothing the
    resolver keys on, so it is dropped rather than coerced (`ingest/zeek/plugin.py::_is_ip`)."""
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def as_str(value: object) -> str:
    """A stripped string, or `''`; a one-element list is its element (Splunk and ES both hand
    back multivalue fields as lists)."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return "" if value is None else str(value).strip()


def first_of(row: dict, *keys: str) -> Any:
    """The first key present AND non-empty, in order — a synonym chain for fields that differ
    between a source's CIM/ECS spelling and its native one."""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "" and v != "-" and v != []:
            return v
    return None


Parser = Callable[[dict], Iterator[Event]]
