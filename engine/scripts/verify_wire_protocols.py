#!/usr/bin/env python3
"""Judge whether the protocol engines put REAL protocol bytes on the wire.

Reads a Suricata **eve.json** from the standing O2 sensor and answers one
question with numbers: did this round's traffic get *classified* by a protocol
parser (`dns`/`tls`/`krb5`/`ldap`/`smb`/`dcerpc`/`rdp`/`ssh`/`snmp`/`http`),
or did it land as bare `tcp`/`udp` with `app_proto: failed|unknown` — the old
filler-bytes failure mode (right peer/port/timing, wrong content).

The tool is deliberately conservative: it never reports PASS without a record
that proves protocol bytes were parsed, it labels handshake-only/failed
exchanges as PARTIAL (the sink cannot serve pinned endpoints — that is
expected, and is never counted as success), and where eve's schema simply does
not carry enough to decide completion it says `undetermined` instead of
guessing. An empty window reports "NO RECORDS IN WINDOW", never a row of zeros
that reads like a clean pass.

Windowing (the sensor's eve.json is ~720 MB of stale history — never truncate
it, never read it whole):

  --since-offset N   seek to byte N before reading (take it with
                     `stat -c %s /var/log/suricata/eve.json` BEFORE the run)
  --since-ts TS      drop records whose `timestamp` is older than TS

  **When both are given they COMPOSE (AND).** `--since-offset` is authoritative
  for where reading BEGINS (the file seek); `--since-ts` is then authoritative
  for which of the read records are INCLUDED. A record before TS is dropped
  even if it sits after the offset. Records with no parseable timestamp are
  excluded while `--since-ts` is in force, and counted in the header as
  `undated-skipped` so the exclusion is visible.

Usage:
  python3 verify_wire_protocols.py --eve /var/log/suricata/eve.json \
      --since-offset 757118976 \
      --hosts dc=10.66.10.21,linux-client=10.66.10.31 --top 10
  python3 verify_wire_protocols.py --eve eve.json --since-ts 2026-09-21T12:00:00+0000 --json

Exit codes: 0 = report produced from >=1 record; 2 = no records in window;
1 = usage/IO error. stdlib only, python3, no repo imports (runnable in-guest
over the QGA channel).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

TOOL = "verify_wire_protocols"
SCHEMA_VERSION = 1

# Lines longer than this are almost certainly not an eve record; they are
# dropped (and counted) rather than buffered, so a pathological file cannot
# blow memory up.
MAX_LINE_BYTES = 1 << 20
READ_CHUNK = 1 << 20

# app_proto values that mean "Suricata did NOT classify this flow".
UNCLASSIFIED = {"failed", "unknown", "", "none", "template"}

# Canonical ports per protocol. Used ONLY to report the shape-only failure
# mode: "N flows on this protocol's ports, unclassified, M of them RST".
PROTO_PORTS = {
    "dns": (53, 5353),
    "tls": (443, 8443, 636, 993, 995),
    "krb5": (88, 464, 749),
    "ldap": (389, 636, 3268, 3269),
    "smb": (445, 139),
    "dcerpc": (135,),
    "rdp": (3389,),
    "ssh": (22,),
    "snmp": (161, 162),
    "http": (80, 8080, 3128),
    "ntp": (123,),
    "quic": (443,),
}

# The protocols this round claims to produce (the svc-engines README §5 table).
DEFAULT_EXPECT = (
    "dns",
    "tls",
    "krb5",
    "ldap",
    "smb",
    "dcerpc",
    "rdp",
    "ssh",
    "snmp",
    "http",
)

# Event types that are themselves protocol evidence (a parser decoded bytes).
PROTOCOL_EVENT_TYPES = {
    "dns",
    "tls",
    "krb5",
    "ldap",
    "smb",
    "dcerpc",
    "rdp",
    "ssh",
    "snmp",
    "http",
    "http2",
    "ntp",
    "quic",
    "dhcp",
    "ftp",
    "smtp",
    "sip",
    "tftp",
    "ike",
    "mqtt",
    "rfb",
    "nfs",
    "snmp_trap",
    "modbus",
}

STATUS_PASS = "PASS"
STATUS_PARTIAL = "PARTIAL"
STATUS_ABSENT = "ABSENT"
STATUS_NO_DATA = "NO-DATA"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def parse_ts(value):
    """Parse a Suricata `timestamp` (`2026-09-21T12:00:00.123456+0000`).

    Returns a tz-aware datetime, or None if unparseable. Naive timestamps are
    assumed UTC.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    m = re.search(r"([+-]\d{2})(\d{2})$", text)
    if m:
        text = text[: m.start()] + m.group(1) + ":" + m.group(2)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def top_n(counter, n):
    """Top `n` (key, count) pairs, sorted by count desc then key asc.

    The key tie-break makes the output deterministic for equal counts.
    """
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [[k, c] for k, c in items[:n]]


def as_sorted_dict(counter):
    """A plain dict with keys in sorted order (deterministic JSON/text)."""
    return {str(k): counter[k] for k in sorted(counter, key=str)}


def _looks_like_ip(text):
    if not isinstance(text, str):
        return False
    if ":" in text and re.fullmatch(r"[0-9A-Fa-f:.]+", text):
        return True
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text))


def parse_hosts(spec):
    """Parse `--hosts`: inline `name=ip,...` or a path to a JSON mapping.

    The JSON mapping may be name->ip or ip->name; both are accepted. Returns
    {ip: label}.
    """
    out = {}
    if not spec:
        return out
    if "=" in spec and not os.path.exists(spec):
        for chunk in spec.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError("--hosts entry %r is not name=ip" % chunk)
            name, _, ip = chunk.partition("=")
            out[ip.strip()] = name.strip()
        return out
    if not os.path.exists(spec):
        raise ValueError(
            "%r is neither a `name=ip,...` list nor an existing JSON file" % spec
        )
    with open(spec, "r", encoding="utf-8", errors="replace") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("--hosts JSON file must contain an object")
    for key, value in raw.items():
        if _looks_like_ip(value):
            out[str(value)] = str(key)
        elif _looks_like_ip(key):
            out[str(key)] = str(value)
        else:
            raise ValueError("--hosts JSON entry %r: neither side is an IP" % key)
    return out


def dig(record, *path):
    """Nested `get` that tolerates missing/non-dict levels."""
    cur = record
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def first_present(mapping, *keys):
    """First non-None value among `keys` (tolerates schema spelling drift)."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


# ---------------------------------------------------------------------------
# streaming reader
# ---------------------------------------------------------------------------
def iter_lines(path, offset=0):
    """Yield `(line_bytes, kind)` from `path` starting at byte `offset`.

    `kind` is one of "line" (newline-terminated), "tail" (final line with no
    trailing newline — a candidate truncated record) or "oversized" (a line
    longer than MAX_LINE_BYTES, dropped). Never holds more than one line plus
    one chunk in memory.
    """
    with open(path, "rb") as fh:
        if offset:
            fh.seek(offset)
        buf = b""
        skipping = False
        while True:
            chunk = fh.read(READ_CHUNK)
            if not chunk:
                break
            buf += chunk
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                line, buf = buf[:idx], buf[idx + 1 :]
                if skipping:
                    skipping = False
                    continue
                if len(line) > MAX_LINE_BYTES:
                    yield b"", "oversized"
                    continue
                yield line, "line"
            if len(buf) > MAX_LINE_BYTES:
                buf = b""
                skipping = True
                yield b"", "oversized"
        if buf and not skipping:
            yield buf, "tail"


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
class Analysis(object):
    """Accumulates every counter the report needs, one record at a time."""

    def __init__(self, hosts=None, top=10, expect=DEFAULT_EXPECT):
        self.hosts = dict(hosts or {})
        self.top = top
        self.expect = list(expect)

        # window bookkeeping
        self.bytes_read = 0
        self.lines_total = 0
        self.records = 0
        self.json_errors = 0
        self.json_error_samples = []
        self.oversized_lines = 0
        self.truncated_final_line = 0
        self.undated_skipped = 0
        self.ts_filtered = 0
        self.first_ts = None
        self.last_ts = None
        self.warnings = []

        # census
        self.event_types = Counter()
        self.flow_app_proto = Counter()
        self.any_app_proto = Counter()
        self.flows = 0
        self.unclassified_flows = 0
        self.unclassified_reset = 0
        self.unclassified_by_port = Counter()
        self.unclassified_reset_by_port = Counter()
        self.reset_flows = 0

        # per-host
        self.host_records = Counter()
        self.host_src = Counter()
        self.host_dst = Counter()
        self.host_protos = defaultdict(Counter)

        # per-protocol detail
        self.tls = {
            "records": 0,
            "with_sni": 0,
            "sni": Counter(),
            "versions": Counter(),
            "ja3": 0,
            "ja3s": 0,
            "with_subject": 0,
            "handshake_only": 0,
            "session_resumed": 0,
        }
        self.krb5 = {
            "records": 0,
            "msgtypes": Counter(),
            "error_codes": Counter(),
            "cnames": Counter(),
            "snames": Counter(),
            "realms": Counter(),
            "failed_request": 0,
            "replies": 0,
            "requests": 0,
            "errors": 0,
            "undetermined": 0,
        }
        self.smb = {
            "records": 0,
            "dialects": Counter(),
            "commands": Counter(),
            "statuses": Counter(),
            "with_dialect": 0,
            "tree_connect": 0,
            "tree_connect_ok": 0,
            "session_setup_ok": 0,
            "shares": Counter(),
            "named_pipes": Counter(),
        }
        self.ldap = {
            "records": 0,
            "operations": Counter(),
            "result_codes": Counter(),
            "bind_requests": 0,
            "bind_responses": 0,
            "bind_success": 0,
            "undetermined": 0,
        }
        self.ssh = {
            "records": 0,
            "client_software": Counter(),
            "server_software": Counter(),
            "both_banners": 0,
        }
        self.dns = {
            "records": 0,
            "queries": 0,
            "answers": 0,
            "rcodes": Counter(),
            "rrnames": Counter(),
        }
        self.http = {
            "records": 0,
            "with_status": 0,
            "hostnames": Counter(),
            "status_codes": Counter(),
        }
        self.dcerpc = {"records": 0, "with_response": 0, "interfaces": Counter()}
        self.rdp = {"records": 0, "event_types": Counter(), "with_tls": 0}
        self.snmp = {
            "records": 0,
            "pdu_types": Counter(),
            "versions": Counter(),
            "responses": 0,
        }
        self.alerts = Counter()

    # -- ingest ------------------------------------------------------------
    def add(self, rec):
        self.records += 1
        etype = rec.get("event_type")
        etype = str(etype) if etype is not None else "(missing)"
        self.event_types[etype] += 1

        app_proto = rec.get("app_proto")
        app_key = str(app_proto).lower() if app_proto is not None else "(none)"
        if app_proto is not None:
            self.any_app_proto[app_key] += 1

        if etype == "flow":
            self._add_flow(rec, app_key)
        elif etype == "tls":
            self._add_tls(rec)
        elif etype == "krb5":
            self._add_krb5(rec)
        elif etype == "smb":
            self._add_smb(rec)
        elif etype == "ldap":
            self._add_ldap(rec)
        elif etype == "ssh":
            self._add_ssh(rec)
        elif etype == "dns":
            self._add_dns(rec)
        elif etype == "http":
            self._add_http(rec)
        elif etype == "dcerpc":
            self._add_dcerpc(rec)
        elif etype == "rdp":
            self._add_rdp(rec)
        elif etype == "snmp":
            self._add_snmp(rec)
        elif etype == "alert":
            sig = dig(rec, "alert", "signature")
            self.alerts[str(sig) if sig else "(unnamed)"] += 1
        self._add_hosts(rec, etype, app_key)

    def _add_hosts(self, rec, etype, app_key):
        src = rec.get("src_ip")
        dst = rec.get("dest_ip")
        label = None
        if etype in PROTOCOL_EVENT_TYPES:
            label = etype
        elif etype == "flow":
            label = app_key if app_key not in ("(none)",) else "(none)"
        if isinstance(src, str) and src:
            self.host_records[src] += 1
            self.host_src[src] += 1
            if label:
                self.host_protos[src][label] += 1
        if isinstance(dst, str) and dst:
            self.host_records[dst] += 1
            self.host_dst[dst] += 1
            if label:
                self.host_protos[dst][label] += 1

    def _add_flow(self, rec, app_key):
        self.flows += 1
        self.flow_app_proto[app_key] += 1
        reset = flow_was_reset(rec)
        if reset:
            self.reset_flows += 1
        if app_key in UNCLASSIFIED or app_key == "(none)":
            self.unclassified_flows += 1
            port = port_key(rec)
            self.unclassified_by_port[port] += 1
            if reset:
                self.unclassified_reset += 1
                self.unclassified_reset_by_port[port] += 1

    def _add_tls(self, rec):
        tls = rec.get("tls") if isinstance(rec.get("tls"), dict) else {}
        self.tls["records"] += 1
        sni = tls.get("sni")
        if sni:
            self.tls["with_sni"] += 1
            self.tls["sni"][str(sni)] += 1
        version = tls.get("version")
        self.tls["versions"][str(version) if version else "(none)"] += 1
        if dig(tls, "ja3", "hash") or tls.get("ja3"):
            self.tls["ja3"] += 1
        if dig(tls, "ja3s", "hash") or tls.get("ja3s"):
            self.tls["ja3s"] += 1
        if tls.get("subject"):
            self.tls["with_subject"] += 1
        if tls.get("session_resumed"):
            self.tls["session_resumed"] += 1
        # A handshake that never produced a server certificate/version is
        # handshake-only or failed. The sink cannot serve pinned endpoints, so
        # this is EXPECTED — but it is not evidence of a completed session.
        bad_version = (not version) or str(version).upper() in (
            "UNDETERMINED",
            "UNKNOWN",
        )
        if bad_version and not tls.get("subject"):
            self.tls["handshake_only"] += 1

    def _add_krb5(self, rec):
        krb = rec.get("krb5") if isinstance(rec.get("krb5"), dict) else {}
        self.krb5["records"] += 1
        msgtype = first_present(krb, "msgtype", "msg_type", "message_type")
        msg = str(msgtype).upper() if msgtype else "(none)"
        self.krb5["msgtypes"][msg] += 1
        err = first_present(krb, "error_code", "errorcode")
        if err is not None:
            self.krb5["error_codes"][str(err)] += 1
        for field, bucket in (("cname", "cnames"), ("sname", "snames"), ("realm", "realms")):
            value = krb.get(field)
            if isinstance(value, dict):
                value = first_present(value, "name_string", "name", "value")
                if isinstance(value, list):
                    value = "/".join(str(v) for v in value)
            if value:
                self.krb5[bucket][str(value)] += 1
        if krb.get("failed_request"):
            self.krb5["failed_request"] += 1
        if "REP" in msg:
            self.krb5["replies"] += 1
        elif "ERROR" in msg or err is not None:
            self.krb5["errors"] += 1
        elif "REQ" in msg:
            self.krb5["requests"] += 1
        else:
            self.krb5["undetermined"] += 1

    def _add_smb(self, rec):
        smb = rec.get("smb") if isinstance(rec.get("smb"), dict) else {}
        self.smb["records"] += 1
        dialect = smb.get("dialect")
        if dialect:
            self.smb["with_dialect"] += 1
            self.smb["dialects"][str(dialect)] += 1
        command = smb.get("command")
        status = smb.get("status")
        if command:
            self.smb["commands"][str(command)] += 1
        if status:
            self.smb["statuses"][str(status)] += 1
        ok = str(status).upper() == "STATUS_SUCCESS" if status else False
        cmd = str(command).upper() if command else ""
        if "TREE_CONNECT" in cmd:
            self.smb["tree_connect"] += 1
            if ok:
                self.smb["tree_connect_ok"] += 1
        if "SESSION_SETUP" in cmd and ok:
            self.smb["session_setup_ok"] += 1
        share = smb.get("share")
        if share:
            self.smb["shares"][str(share)] += 1
        pipe = smb.get("named_pipe")
        if pipe:
            self.smb["named_pipes"][str(pipe)] += 1

    def _add_ldap(self, rec):
        ldap = rec.get("ldap") if isinstance(rec.get("ldap"), dict) else {}
        self.ldap["records"] += 1
        decided = False
        request = ldap.get("request")
        if isinstance(request, dict):
            op = first_present(request, "operation", "operation_name")
            if op:
                self.ldap["operations"][str(op)] += 1
                if "bind" in str(op).lower():
                    self.ldap["bind_requests"] += 1
        responses = ldap.get("responses")
        if isinstance(responses, dict):
            responses = [responses]
        if isinstance(responses, list):
            for resp in responses:
                if not isinstance(resp, dict):
                    continue
                op = first_present(resp, "operation", "operation_name")
                if op:
                    self.ldap["operations"][str(op)] += 1
                code = _ldap_result_code(resp)
                if code is not None:
                    decided = True
                    self.ldap["result_codes"][str(code)] += 1
                    if "bind" in str(op or "").lower():
                        self.ldap["bind_responses"] += 1
                        if _is_ldap_success(code):
                            self.ldap["bind_success"] += 1
        if not decided:
            self.ldap["undetermined"] += 1

    def _add_ssh(self, rec):
        ssh = rec.get("ssh") if isinstance(rec.get("ssh"), dict) else {}
        self.ssh["records"] += 1
        client = dig(ssh, "client", "software_version")
        server = dig(ssh, "server", "software_version")
        if client:
            self.ssh["client_software"][str(client)] += 1
        if server:
            self.ssh["server_software"][str(server)] += 1
        if client and server:
            self.ssh["both_banners"] += 1

    def _add_dns(self, rec):
        dns = rec.get("dns") if isinstance(rec.get("dns"), dict) else {}
        self.dns["records"] += 1
        dtype = str(dns.get("type") or "").lower()
        queries = dns.get("queries")
        answers = dns.get("answers")
        if dtype == "answer" or isinstance(answers, list):
            self.dns["answers"] += 1
        elif dtype == "query" or isinstance(queries, list):
            self.dns["queries"] += 1
        rcode = dns.get("rcode")
        if rcode:
            self.dns["rcodes"][str(rcode)] += 1
        for name in _dns_names(dns):
            self.dns["rrnames"][name] += 1

    def _add_http(self, rec):
        http = rec.get("http") if isinstance(rec.get("http"), dict) else {}
        self.http["records"] += 1
        status = http.get("status")
        if status is not None:
            self.http["with_status"] += 1
            self.http["status_codes"][str(status)] += 1
        host = http.get("hostname")
        if host:
            self.http["hostnames"][str(host)] += 1

    def _add_dcerpc(self, rec):
        dce = rec.get("dcerpc") if isinstance(rec.get("dcerpc"), dict) else {}
        self.dcerpc["records"] += 1
        if dce.get("response") not in (None, "", "UNREPLIED"):
            self.dcerpc["with_response"] += 1
        interfaces = dce.get("interfaces")
        if isinstance(interfaces, list):
            for iface in interfaces:
                uuid = dig(iface, "uuid") if isinstance(iface, dict) else None
                if uuid:
                    self.dcerpc["interfaces"][str(uuid)] += 1

    def _add_rdp(self, rec):
        rdp = rec.get("rdp") if isinstance(rec.get("rdp"), dict) else {}
        self.rdp["records"] += 1
        sub = rdp.get("event_type")
        self.rdp["event_types"][str(sub) if sub else "(none)"] += 1
        if rdp.get("x509_serials") or str(sub or "").lower() == "tls_handshake":
            self.rdp["with_tls"] += 1

    def _add_snmp(self, rec):
        snmp = rec.get("snmp") if isinstance(rec.get("snmp"), dict) else {}
        self.snmp["records"] += 1
        pdu = snmp.get("pdu_type")
        self.snmp["pdu_types"][str(pdu) if pdu else "(none)"] += 1
        version = snmp.get("version")
        if version is not None:
            self.snmp["versions"][str(version)] += 1
        if pdu and "response" in str(pdu).lower():
            self.snmp["responses"] += 1

    # -- derived -----------------------------------------------------------
    def flow_fallback(self, proto):
        """Flows Suricata classified as `proto` (the <8.0 / GAP-076 fallback)."""
        return self.flow_app_proto.get(proto, 0)

    def port_noise(self, proto):
        """(unclassified_flows, reset_of_those) on this protocol's ports."""
        total = 0
        reset = 0
        for port in PROTO_PORTS.get(proto, ()):  # canonical ports only
            for key, count in self.unclassified_by_port.items():
                if key.endswith("/%d" % port):
                    total += count
            for key, count in self.unclassified_reset_by_port.items():
                if key.endswith("/%d" % port):
                    reset += count
        return total, reset


def _dns_names(dns):
    names = []
    rrname = dns.get("rrname")
    if rrname:
        names.append(str(rrname))
    for key in ("queries", "answers"):
        seq = dns.get(key)
        if isinstance(seq, list):
            for item in seq:
                if isinstance(item, dict) and item.get("rrname"):
                    names.append(str(item["rrname"]))
    return names


def _ldap_result_code(resp):
    """Pull an LDAP result code out of a response object, schema-tolerantly."""
    direct = first_present(resp, "result_code", "resultCode", "result")
    if isinstance(direct, (str, int)):
        return direct
    for value in resp.values():
        if isinstance(value, dict):
            nested = first_present(value, "result_code", "resultCode", "result")
            if isinstance(nested, (str, int)):
                return nested
    return None


def _is_ldap_success(code):
    if isinstance(code, int):
        return code == 0
    text = str(code).strip().lower()
    return text in ("0", "success")


def flow_was_reset(rec):
    """True if this flow record shows a TCP RST (either direction)."""
    tcp = rec.get("tcp")
    if isinstance(tcp, dict):
        if tcp.get("rst") is True:
            return True
        for key in ("tcp_flags", "tcp_flags_ts", "tcp_flags_tc"):
            flags = tcp.get(key)
            if isinstance(flags, str) and flags:
                try:
                    if int(flags, 16) & 0x04:
                        return True
                except ValueError:
                    pass
        state = tcp.get("state")
        # `closed` is an orderly FIN teardown — only an explicit reset counts.
        if isinstance(state, str) and state.lower() == "reset":
            return True
    reason = dig(rec, "flow", "reason")
    if isinstance(reason, str) and reason.lower() == "reset":
        return True
    return False


def port_key(rec):
    """`<proto>/<dest_port>` for a flow record, deterministic and printable."""
    proto = rec.get("proto") or "?"
    port = rec.get("dest_port")
    if port is None:
        return "%s/-" % str(proto).lower()
    return "%s/%s" % (str(proto).lower(), port)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def scan(path, analysis, offset=0, since_ts=None):
    """Stream `path` from `offset`, feeding each in-window record to `analysis`."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if size is not None and offset > size:
        analysis.warnings.append(
            "--since-offset %d is beyond the file's current size %d "
            "(rotated or truncated?) — NOTHING was read; re-take the offset."
            % (offset, size)
        )
        return
    if size is not None:
        analysis.bytes_read = max(0, size - offset)

    for raw, kind in iter_lines(path, offset):
        if kind == "oversized":
            analysis.oversized_lines += 1
            continue
        analysis.lines_total += 1
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            rec = json.loads(text)
        except ValueError:
            if kind == "tail":
                # A final line with no newline that does not parse is the
                # file's in-flight record, not corruption.
                analysis.truncated_final_line += 1
            else:
                analysis.json_errors += 1
                if len(analysis.json_error_samples) < 3:
                    analysis.json_error_samples.append(text[:120])
            continue
        if not isinstance(rec, dict):
            analysis.json_errors += 1
            continue
        ts = parse_ts(rec.get("timestamp"))
        if since_ts is not None:
            if ts is None:
                analysis.undated_skipped += 1
                continue
            if ts < since_ts:
                analysis.ts_filtered += 1
                continue
        if ts is not None:
            if analysis.first_ts is None or ts < analysis.first_ts:
                analysis.first_ts = ts
            if analysis.last_ts is None or ts > analysis.last_ts:
                analysis.last_ts = ts
        analysis.add(rec)


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def _verdict(proto, status, completion, evidence, notes=None):
    return {
        "protocol": proto,
        "status": status,
        "completion": completion,
        "evidence": evidence,
        "notes": list(notes or []),
    }


def _absent(analysis, proto, extra_notes=None):
    """ABSENT, with the shape-only failure mode spelled out when present."""
    notes = list(extra_notes or [])
    noise, reset = analysis.port_noise(proto)
    if noise:
        notes.append(
            "%d unclassified flow(s) on %s port(s) %s, %d of them RST — "
            "connections happened but no protocol bytes were parsed "
            "(the shape-only failure mode)"
            % (
                noise,
                proto,
                ",".join(str(p) for p in PROTO_PORTS.get(proto, ())),
                reset,
            )
        )
    return _verdict(proto, STATUS_ABSENT, "none", "no records, no classified flows", notes)


def _flow_only(analysis, proto, why):
    count = analysis.flow_fallback(proto)
    return _verdict(
        proto,
        STATUS_PARTIAL,
        "undetermined",
        "0 %s records; %d flow(s) classified app_proto=%s" % (proto, count, proto),
        [why, "completion cannot be decided from a flow record alone"],
    )


def build_verdicts(analysis):
    """One verdict per expected protocol. Never PASS without a parsed record."""
    verdicts = []
    for proto in analysis.expect:
        fn = _VERDICT_FNS.get(proto, _verdict_generic)
        verdicts.append(fn(analysis, proto))
    verdicts.sort(key=lambda v: v["protocol"])
    return verdicts


def _verdict_generic(analysis, proto):
    """Records of this event type exist => real protocol bytes were parsed."""
    count = analysis.event_types.get(proto, 0)
    if count:
        return _verdict(
            proto,
            STATUS_PASS,
            "undetermined",
            "%d %s record(s)" % (count, proto),
            ["eve's %s schema carries no completion marker this tool trusts" % proto],
        )
    if analysis.flow_fallback(proto):
        return _flow_only(analysis, proto, "no %s event records in this window" % proto)
    return _absent(analysis, proto)


def _verdict_dns(analysis, proto="dns"):
    d = analysis.dns
    if d["answers"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d dns records (%d queries, %d answers)"
            % (d["records"], d["queries"], d["answers"]),
        )
    if d["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d dns records, %d queries, ZERO answers" % (d["records"], d["queries"]),
            ["queries went out but no response was logged"],
        )
    if analysis.flow_fallback("dns"):
        return _flow_only(analysis, proto, "no dns event records in this window")
    return _absent(analysis, proto)


def _verdict_tls(analysis, proto="tls"):
    t = analysis.tls
    real = t["records"] - t["handshake_only"]
    if t["records"] and real > 0:
        notes = []
        if t["handshake_only"]:
            notes.append(
                "%d of %d are handshake-only/failed — EXPECTED against the sink "
                "(it cannot serve pinned endpoints); not counted as success"
                % (t["handshake_only"], t["records"])
            )
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d tls records, %d with SNI, %d completed handshakes"
            % (t["records"], t["with_sni"], real),
            notes,
        )
    if t["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d tls records, ALL handshake-only/failed (%d with SNI)"
            % (t["records"], t["with_sni"]),
            [
                "expected against the sink (no pinned endpoint is served); "
                "real TLS bytes were on the wire, but no session completed"
            ],
        )
    if analysis.flow_fallback("tls"):
        return _flow_only(
            analysis,
            proto,
            "0 tls records despite tls flows — known sensor gap GAP-076; "
            "SNI/JA3/subject live in the tls record, so they are UNDETERMINED here",
        )
    return _absent(analysis, proto)


def _verdict_krb5(analysis, proto="krb5"):
    k = analysis.krb5
    if k["replies"]:
        notes = []
        if k["errors"]:
            notes.append(
                "%d KRB_ERROR record(s): %s (KDC_ERR_PREAUTH_REQUIRED is a "
                "NORMAL first leg of an AS exchange)"
                % (k["errors"], _fmt_counter(k["error_codes"], 3))
            )
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d krb5 records, %d reply message(s) (AS-REP/TGS-REP)"
            % (k["records"], k["replies"]),
            notes,
        )
    if k["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d krb5 records, msgtypes %s, ZERO AS-REP/TGS-REP"
            % (k["records"], _fmt_counter(k["msgtypes"], 4)),
            [
                "real Kerberos bytes were parsed, but no ticket-granting reply "
                "was logged in this window"
            ],
        )
    if analysis.flow_fallback("krb5"):
        return _flow_only(analysis, proto, "no krb5 event records in this window")
    return _absent(analysis, proto)


def _verdict_smb(analysis, proto="smb"):
    s = analysis.smb
    if s["with_dialect"] and s["tree_connect_ok"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d smb records, dialect(s) %s, %d successful TREE_CONNECT"
            % (s["records"], _fmt_counter(s["dialects"], 3), s["tree_connect_ok"]),
        )
    if s["records"]:
        missing = []
        if not s["with_dialect"]:
            missing.append("no negotiated dialect")
        if not s["tree_connect"]:
            missing.append("no TREE_CONNECT")
        elif not s["tree_connect_ok"]:
            missing.append(
                "TREE_CONNECT seen but never STATUS_SUCCESS (%s)"
                % _fmt_counter(s["statuses"], 3)
            )
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d smb records; %s" % (s["records"], "; ".join(missing) or "incomplete"),
            ["real SMB bytes were parsed, but the session did not reach a share"],
        )
    if analysis.flow_fallback("smb"):
        return _flow_only(analysis, proto, "no smb event records in this window")
    return _absent(analysis, proto)


def _verdict_ldap(analysis, proto="ldap"):
    l = analysis.ldap
    if l["bind_success"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d ldap records, %d successful bind response(s)"
            % (l["records"], l["bind_success"]),
        )
    if l["records"] and l["result_codes"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d ldap records, result codes %s, no successful bind"
            % (l["records"], _fmt_counter(l["result_codes"], 3)),
            ["the server answered — the bind was refused, not the bytes"],
        )
    if l["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "undetermined",
            "%d ldap records, no result code in any response" % l["records"],
            [
                "real LDAP bytes were parsed; eve carried no result code, so "
                "completion is UNDETERMINED (not a failure, not a success)"
            ],
        )
    if analysis.flow_fallback("ldap"):
        return _flow_only(
            analysis,
            proto,
            "0 ldap records — the `ldap` event type is Suricata 8.0; on 7.x the "
            "flow classification is the only available evidence",
        )
    return _absent(
        analysis,
        proto,
        [
            "on Suricata 7.x there is no ldap parser at all, so ABSENT here may "
            "be a SENSOR-VERSION limit rather than an engine failure — check the "
            "sensor's suricata version before scoring this row"
        ],
    )


def _verdict_ssh(analysis, proto="ssh"):
    s = analysis.ssh
    if s["both_banners"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d ssh records, %d with BOTH client and server banners (a real KEX)"
            % (s["records"], s["both_banners"]),
            ["auth may still fail by design (no key distributed) — that is after the KEX"],
        )
    if s["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d ssh records, but never both banners" % s["records"],
            ["one side's version string was never observed"],
        )
    if analysis.flow_fallback("ssh"):
        return _flow_only(analysis, proto, "no ssh event records in this window")
    return _absent(analysis, proto)


def _verdict_http(analysis, proto="http"):
    h = analysis.http
    if h["with_status"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d http records, %d with a response status (%s)"
            % (h["records"], h["with_status"], _fmt_counter(h["status_codes"], 3)),
        )
    if h["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d http records, none carrying a response status" % h["records"],
            ["requests were parsed; no response was logged"],
        )
    if analysis.flow_fallback("http"):
        return _flow_only(analysis, proto, "no http event records in this window")
    return _absent(analysis, proto)


def _verdict_dcerpc(analysis, proto="dcerpc"):
    d = analysis.dcerpc
    if d["with_response"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d dcerpc records, %d with a response" % (d["records"], d["with_response"]),
        )
    if d["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "undetermined",
            "%d dcerpc records, none carrying a response field" % d["records"],
            ["real DCE/RPC bytes were parsed; eve did not record a reply"],
        )
    if analysis.flow_fallback("dcerpc"):
        return _flow_only(analysis, proto, "no dcerpc event records in this window")
    return _absent(analysis, proto)


def _verdict_rdp(analysis, proto="rdp"):
    r = analysis.rdp
    if r["records"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "undetermined",
            "%d rdp records (%s)" % (r["records"], _fmt_counter(r["event_types"], 3)),
            [
                "eve's rdp schema has no session-established marker, so "
                "completion is UNDETERMINED; the records themselves prove real "
                "RDP/X.224 bytes were parsed"
            ],
        )
    if analysis.flow_fallback("rdp"):
        return _flow_only(analysis, proto, "no rdp event records in this window")
    return _absent(analysis, proto)


def _verdict_snmp(analysis, proto="snmp"):
    s = analysis.snmp
    if s["responses"]:
        return _verdict(
            proto,
            STATUS_PASS,
            "completed",
            "%d snmp records, %d response PDU(s)" % (s["records"], s["responses"]),
        )
    if s["records"]:
        return _verdict(
            proto,
            STATUS_PARTIAL,
            "not-completed",
            "%d snmp records, PDUs %s, no response"
            % (s["records"], _fmt_counter(s["pdu_types"], 3)),
            ["the get went out; nothing answered"],
        )
    if analysis.flow_fallback("snmp"):
        return _flow_only(analysis, proto, "no snmp event records in this window")
    return _absent(analysis, proto)


_VERDICT_FNS = {
    "dns": _verdict_dns,
    "tls": _verdict_tls,
    "krb5": _verdict_krb5,
    "smb": _verdict_smb,
    "ldap": _verdict_ldap,
    "ssh": _verdict_ssh,
    "http": _verdict_http,
    "dcerpc": _verdict_dcerpc,
    "rdp": _verdict_rdp,
    "snmp": _verdict_snmp,
}


def _fmt_counter(counter, n):
    pairs = top_n(counter, n)
    if not pairs:
        return "-"
    return ", ".join("%s=%d" % (k, c) for k, c in pairs)


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------
def build_report(analysis, args):
    window = {
        "eve": args.eve,
        "since_offset": args.since_offset,
        "since_ts": args.since_ts,
        "filter_rule": (
            "offset seeks, timestamp filters; both compose (AND)"
            if (args.since_offset and args.since_ts)
            else ("offset seek" if args.since_offset else ("timestamp filter" if args.since_ts else "whole file"))
        ),
        "bytes_read": analysis.bytes_read,
        "lines_read": analysis.lines_total,
        "records_in_window": analysis.records,
        "records_before_since_ts": analysis.ts_filtered,
        "undated_skipped": analysis.undated_skipped,
        "json_errors": analysis.json_errors,
        "json_error_samples": list(analysis.json_error_samples),
        "truncated_final_line": analysis.truncated_final_line,
        "oversized_lines": analysis.oversized_lines,
        "first_timestamp": analysis.first_ts.isoformat() if analysis.first_ts else None,
        "last_timestamp": analysis.last_ts.isoformat() if analysis.last_ts else None,
        "warnings": list(analysis.warnings),
    }

    empty = analysis.records == 0
    if empty:
        verdicts = [
            _verdict(
                proto,
                STATUS_NO_DATA,
                "undetermined",
                "no records in window — nothing was measured",
                ["a NO-DATA row is not a pass and not a failure"],
            )
            for proto in sorted(analysis.expect)
        ]
    else:
        verdicts = build_verdicts(analysis)

    summary = Counter(v["status"] for v in verdicts)
    report = {
        "tool": TOOL,
        "schema_version": SCHEMA_VERSION,
        "empty_window": empty,
        "window": window,
        "census": {
            "event_type": as_sorted_dict(analysis.event_types),
            "event_type_top": top_n(analysis.event_types, args.top),
            "flow_app_proto": as_sorted_dict(analysis.flow_app_proto),
            "flows": analysis.flows,
            "reset_flows": analysis.reset_flows,
            "unclassified_flows": analysis.unclassified_flows,
            "unclassified_reset_flows": analysis.unclassified_reset,
            "unclassified_by_port_top": top_n(analysis.unclassified_by_port, args.top),
            "unclassified_reset_by_port_top": top_n(
                analysis.unclassified_reset_by_port, args.top
            ),
            "alerts_top": top_n(analysis.alerts, args.top),
        },
        "tls": {
            "records": analysis.tls["records"],
            "with_sni": analysis.tls["with_sni"],
            "distinct_sni": len(analysis.tls["sni"]),
            "sni_top": top_n(analysis.tls["sni"], args.top),
            "versions": as_sorted_dict(analysis.tls["versions"]),
            "handshake_only": analysis.tls["handshake_only"],
            "with_subject": analysis.tls["with_subject"],
            "ja3_present": analysis.tls["ja3"],
            "ja3s_present": analysis.tls["ja3s"],
            "session_resumed": analysis.tls["session_resumed"],
            "flow_app_proto_tls": analysis.flow_fallback("tls"),
        },
        "krb5": {
            "records": analysis.krb5["records"],
            "msgtypes": as_sorted_dict(analysis.krb5["msgtypes"]),
            "error_codes": as_sorted_dict(analysis.krb5["error_codes"]),
            "replies": analysis.krb5["replies"],
            "requests": analysis.krb5["requests"],
            "errors": analysis.krb5["errors"],
            "undetermined": analysis.krb5["undetermined"],
            "failed_request": analysis.krb5["failed_request"],
            "cnames_top": top_n(analysis.krb5["cnames"], args.top),
            "snames_top": top_n(analysis.krb5["snames"], args.top),
            "flow_app_proto_krb5": analysis.flow_fallback("krb5"),
        },
        "smb": {
            "records": analysis.smb["records"],
            "with_dialect": analysis.smb["with_dialect"],
            "dialects": as_sorted_dict(analysis.smb["dialects"]),
            "commands_top": top_n(analysis.smb["commands"], args.top),
            "statuses": as_sorted_dict(analysis.smb["statuses"]),
            "tree_connect": analysis.smb["tree_connect"],
            "tree_connect_ok": analysis.smb["tree_connect_ok"],
            "session_setup_ok": analysis.smb["session_setup_ok"],
            "shares_top": top_n(analysis.smb["shares"], args.top),
            "named_pipes_top": top_n(analysis.smb["named_pipes"], args.top),
            "flow_app_proto_smb": analysis.flow_fallback("smb"),
        },
        "ldap": {
            "records": analysis.ldap["records"],
            "operations": as_sorted_dict(analysis.ldap["operations"]),
            "result_codes": as_sorted_dict(analysis.ldap["result_codes"]),
            "bind_requests": analysis.ldap["bind_requests"],
            "bind_responses": analysis.ldap["bind_responses"],
            "bind_success": analysis.ldap["bind_success"],
            "undetermined": analysis.ldap["undetermined"],
            "flow_app_proto_ldap": analysis.flow_fallback("ldap"),
        },
        "ssh": {
            "records": analysis.ssh["records"],
            "both_banners": analysis.ssh["both_banners"],
            "client_software_top": top_n(analysis.ssh["client_software"], args.top),
            "server_software_top": top_n(analysis.ssh["server_software"], args.top),
            "flow_app_proto_ssh": analysis.flow_fallback("ssh"),
        },
        "dns": {
            "records": analysis.dns["records"],
            "queries": analysis.dns["queries"],
            "answers": analysis.dns["answers"],
            "rcodes": as_sorted_dict(analysis.dns["rcodes"]),
            "rrnames_top": top_n(analysis.dns["rrnames"], args.top),
            "flow_app_proto_dns": analysis.flow_fallback("dns"),
        },
        "http": {
            "records": analysis.http["records"],
            "with_status": analysis.http["with_status"],
            "status_codes": as_sorted_dict(analysis.http["status_codes"]),
            "hostnames_top": top_n(analysis.http["hostnames"], args.top),
            "flow_app_proto_http": analysis.flow_fallback("http"),
        },
        "dcerpc": {
            "records": analysis.dcerpc["records"],
            "with_response": analysis.dcerpc["with_response"],
            "interfaces_top": top_n(analysis.dcerpc["interfaces"], args.top),
            "flow_app_proto_dcerpc": analysis.flow_fallback("dcerpc"),
        },
        "rdp": {
            "records": analysis.rdp["records"],
            "event_types": as_sorted_dict(analysis.rdp["event_types"]),
            "with_tls": analysis.rdp["with_tls"],
            "flow_app_proto_rdp": analysis.flow_fallback("rdp"),
        },
        "snmp": {
            "records": analysis.snmp["records"],
            "pdu_types": as_sorted_dict(analysis.snmp["pdu_types"]),
            "versions": as_sorted_dict(analysis.snmp["versions"]),
            "responses": analysis.snmp["responses"],
            "flow_app_proto_snmp": analysis.flow_fallback("snmp"),
        },
        "hosts": build_hosts(analysis, args.top),
        "verdicts": verdicts,
        "summary": {
            "pass": summary.get(STATUS_PASS, 0),
            "partial": summary.get(STATUS_PARTIAL, 0),
            "absent": summary.get(STATUS_ABSENT, 0),
            "no_data": summary.get(STATUS_NO_DATA, 0),
        },
    }
    return report


def build_hosts(analysis, top):
    rows = []
    for ip, count in sorted(
        analysis.host_records.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        rows.append(
            {
                "ip": ip,
                "label": analysis.hosts.get(ip),
                "records": count,
                "as_src": analysis.host_src.get(ip, 0),
                "as_dest": analysis.host_dst.get(ip, 0),
                "protocols_top": top_n(analysis.host_protos.get(ip, Counter()), top),
            }
        )
    return rows[: max(top * 4, top)]


# ---------------------------------------------------------------------------
# human rendering
# ---------------------------------------------------------------------------
def _rule(char="-", width=78):
    return char * width


def _section(title):
    return "\n%s\n%s" % (title, _rule("=", max(len(title), 20)))


def _table(headers, rows):
    """Fixed-width table; column widths from the content (deterministic)."""
    if not rows:
        return "  (none)"
    widths = [len(h) for h in headers]
    cells = []
    for row in rows:
        row = [("" if c is None else str(c)) for c in row]
        cells.append(row)
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  " + "  ".join("-" * widths[i] for i in range(len(headers))))
    for row in cells:
        lines.append("  " + "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))).rstrip())
    return "\n".join(lines)


def render_text(report):
    out = []
    w = report["window"]
    out.append("%s — wire-protocol verdict" % TOOL)
    out.append(_rule("="))
    out.append("eve            : %s" % w["eve"])
    out.append(
        "window         : since-offset=%s  since-ts=%s  (%s)"
        % (w["since_offset"], w["since_ts"] or "-", w["filter_rule"])
    )
    out.append(
        "read           : %d bytes, %d lines, %d records in window"
        % (w["bytes_read"], w["lines_read"], w["records_in_window"])
    )
    out.append(
        "timespan       : %s .. %s" % (w["first_timestamp"] or "-", w["last_timestamp"] or "-")
    )
    out.append(
        "skipped        : %d before --since-ts, %d undated, %d bad-json, "
        "%d truncated-tail, %d oversized"
        % (
            w["records_before_since_ts"],
            w["undated_skipped"],
            w["json_errors"],
            w["truncated_final_line"],
            w["oversized_lines"],
        )
    )
    for sample in w["json_error_samples"]:
        out.append("  bad-json sample: %s" % sample)
    for warn in w["warnings"]:
        out.append("!! %s" % warn)

    if report["empty_window"]:
        out.append("")
        out.append(_rule("!"))
        out.append("NO RECORDS IN WINDOW — nothing was measured.")
        out.append(
            "This is NOT a pass and NOT a failure: no traffic was read from the "
            "window you asked for."
        )
        out.append(
            "Check: the byte offset (was it taken before the run?), the sensor's "
            "capture iface, and that suricata is actually writing to this file."
        )
        out.append(_rule("!"))
        out.append(_section("VERDICT"))
        out.append(
            _table(
                ["protocol", "status", "completion", "evidence"],
                [
                    [v["protocol"], v["status"], v["completion"], v["evidence"]]
                    for v in report["verdicts"]
                ],
            )
        )
        return "\n".join(out) + "\n"

    census = report["census"]
    out.append(_section("event_type census"))
    out.append(
        _table(
            ["event_type", "count"],
            [[k, v] for k, v in sorted(census["event_type"].items(), key=lambda kv: (-kv[1], kv[0]))],
        )
    )

    out.append(_section("flow classification (the shape-only failure mode)"))
    out.append(
        "  flows: %d   classified: %d   UNCLASSIFIED (app_proto failed/unknown/none): %d"
        % (
            census["flows"],
            census["flows"] - census["unclassified_flows"],
            census["unclassified_flows"],
        )
    )
    out.append(
        "  RST flows: %d   of which unclassified: %d"
        % (census["reset_flows"], census["unclassified_reset_flows"])
    )
    out.append("")
    out.append(
        _table(
            ["app_proto (flows)", "count"],
            [
                [k, v]
                for k, v in sorted(
                    census["flow_app_proto"].items(), key=lambda kv: (-kv[1], kv[0])
                )
            ],
        )
    )
    out.append("")
    out.append("  unclassified flows by port (proto/dport), with RSTs:")
    reset_map = dict(census["unclassified_reset_by_port_top"])
    out.append(
        _table(
            ["port", "unclassified", "rst"],
            [[k, v, reset_map.get(k, 0)] for k, v in census["unclassified_by_port_top"]],
        )
    )

    tls = report["tls"]
    out.append(_section("TLS"))
    out.append(
        "  records=%d  with_sni=%d  distinct_sni=%d  handshake_only/failed=%d  "
        "with_subject=%d  ja3=%d  ja3s=%d  resumed=%d  flow_app_proto_tls=%d"
        % (
            tls["records"],
            tls["with_sni"],
            tls["distinct_sni"],
            tls["handshake_only"],
            tls["with_subject"],
            tls["ja3_present"],
            tls["ja3s_present"],
            tls["session_resumed"],
            tls["flow_app_proto_tls"],
        )
    )
    if tls["records"] == 0 and tls["flow_app_proto_tls"]:
        out.append(
            "  NOTE: zero tls records with tls-classified flows present is the known"
        )
        out.append(
            "        sensor gap GAP-076 — SNI/JA3/subject live in the tls record, so"
        )
        out.append("        they are UNDETERMINED from this file.")
    out.append("")
    out.append(_table(["SNI", "count"], tls["sni_top"]))
    out.append("")
    out.append(
        _table(
            ["tls version", "count"],
            [
                [k, v]
                for k, v in sorted(tls["versions"].items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        )
    )

    out.append(_section("Kerberos / LDAP / SMB — the completion question"))
    krb = report["krb5"]
    out.append(
        "  krb5 : records=%d  replies(AS-REP/TGS-REP)=%d  requests=%d  errors=%d  "
        "undetermined=%d  flow_app_proto_krb5=%d"
        % (
            krb["records"],
            krb["replies"],
            krb["requests"],
            krb["errors"],
            krb["undetermined"],
            krb["flow_app_proto_krb5"],
        )
    )
    if krb["msgtypes"]:
        out.append("         msgtypes: %s" % _fmt_dict(krb["msgtypes"]))
    if krb["error_codes"]:
        out.append("         error_codes: %s" % _fmt_dict(krb["error_codes"]))
    if krb["cnames_top"]:
        out.append("         cnames: %s" % _fmt_pairs(krb["cnames_top"]))
    if krb["snames_top"]:
        out.append("         snames: %s" % _fmt_pairs(krb["snames_top"]))

    ldap = report["ldap"]
    out.append(
        "  ldap : records=%d  bind_req=%d  bind_resp=%d  bind_success=%d  "
        "no-result-code=%d  flow_app_proto_ldap=%d"
        % (
            ldap["records"],
            ldap["bind_requests"],
            ldap["bind_responses"],
            ldap["bind_success"],
            ldap["undetermined"],
            ldap["flow_app_proto_ldap"],
        )
    )
    if ldap["result_codes"]:
        out.append("         result_codes: %s" % _fmt_dict(ldap["result_codes"]))

    smb = report["smb"]
    out.append(
        "  smb  : records=%d  with_dialect=%d  tree_connect=%d (ok=%d)  "
        "session_setup_ok=%d  flow_app_proto_smb=%d"
        % (
            smb["records"],
            smb["with_dialect"],
            smb["tree_connect"],
            smb["tree_connect_ok"],
            smb["session_setup_ok"],
            smb["flow_app_proto_smb"],
        )
    )
    if smb["dialects"]:
        out.append("         dialects: %s" % _fmt_dict(smb["dialects"]))
    if smb["commands_top"]:
        out.append("         commands: %s" % _fmt_pairs(smb["commands_top"]))
    if smb["statuses"]:
        out.append("         statuses: %s" % _fmt_dict(smb["statuses"]))

    out.append(_section("other protocols"))
    ssh = report["ssh"]
    out.append(
        "  ssh    : records=%d  both_banners=%d  client=%s  server=%s"
        % (
            ssh["records"],
            ssh["both_banners"],
            _fmt_pairs(ssh["client_software_top"][:2]),
            _fmt_pairs(ssh["server_software_top"][:2]),
        )
    )
    dns = report["dns"]
    out.append(
        "  dns    : records=%d  queries=%d  answers=%d  rcodes=%s"
        % (dns["records"], dns["queries"], dns["answers"], _fmt_dict(dns["rcodes"]))
    )
    if dns["rrnames_top"]:
        out.append("           qnames: %s" % _fmt_pairs(dns["rrnames_top"]))
    http = report["http"]
    out.append(
        "  http   : records=%d  with_status=%d  hosts=%s"
        % (http["records"], http["with_status"], _fmt_pairs(http["hostnames_top"][:3]))
    )
    dce = report["dcerpc"]
    out.append(
        "  dcerpc : records=%d  with_response=%d" % (dce["records"], dce["with_response"])
    )
    rdp = report["rdp"]
    out.append(
        "  rdp    : records=%d  event_types=%s" % (rdp["records"], _fmt_dict(rdp["event_types"]))
    )
    snmp = report["snmp"]
    out.append(
        "  snmp   : records=%d  responses=%d  pdu_types=%s"
        % (snmp["records"], snmp["responses"], _fmt_dict(snmp["pdu_types"]))
    )

    out.append(_section("per-host breakdown"))
    out.append(
        _table(
            ["host", "label", "records", "as_src", "as_dest", "protocols"],
            [
                [
                    h["ip"],
                    h["label"] or "-",
                    h["records"],
                    h["as_src"],
                    h["as_dest"],
                    _fmt_pairs(h["protocols_top"][:5]),
                ]
                for h in report["hosts"]
            ],
        )
    )

    out.append(_section("VERDICT"))
    out.append(
        _table(
            ["protocol", "status", "completion", "evidence"],
            [
                [v["protocol"], v["status"], v["completion"], v["evidence"]]
                for v in report["verdicts"]
            ],
        )
    )
    notes = [(v["protocol"], n) for v in report["verdicts"] for n in v["notes"]]
    if notes:
        out.append("")
        out.append("  notes:")
        for proto, note in notes:
            out.append("    [%s] %s" % (proto, note))
    s = report["summary"]
    out.append("")
    out.append(
        "  summary: PASS=%d  PARTIAL=%d  ABSENT=%d  NO-DATA=%d"
        % (s["pass"], s["partial"], s["absent"], s["no_data"])
    )
    return "\n".join(out) + "\n"


def _fmt_dict(mapping):
    if not mapping:
        return "-"
    pairs = sorted(mapping.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join("%s=%d" % (k, v) for k, v in pairs)


def _fmt_pairs(pairs):
    if not pairs:
        return "-"
    return ", ".join("%s=%d" % (k, v) for k, v in pairs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="verify_wire_protocols.py",
        description=(
            "Judge whether a round's traffic produced REAL protocol records in a "
            "Suricata eve.json, or only bare tcp/udp with app_proto failed/unknown."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--since-offset and --since-ts COMPOSE (AND): the offset is "
            "authoritative for where reading begins (file seek), the timestamp is "
            "authoritative for which read records are included. Records with no "
            "parseable timestamp are excluded while --since-ts is in force and "
            "reported as 'undated'."
        ),
    )
    p.add_argument("--eve", required=True, help="path to eve.json")
    p.add_argument(
        "--since-offset",
        type=int,
        default=0,
        metavar="N",
        help="seek to byte N before reading (take it with `stat -c %%s` BEFORE the run)",
    )
    p.add_argument(
        "--since-ts",
        default=None,
        metavar="TS",
        help="drop records older than this ISO8601 timestamp",
    )
    p.add_argument(
        "--hosts",
        default=None,
        metavar="MAP",
        help="`name=ip,name=ip` or a path to a JSON mapping (name->ip or ip->name)",
    )
    p.add_argument("--top", type=int, default=10, metavar="N", help="top-N rows (default 10)")
    p.add_argument(
        "--expect",
        default=None,
        metavar="P,P",
        help="comma-separated protocols to judge (default: %s)" % ",".join(DEFAULT_EXPECT),
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--out", default=None, metavar="PATH", help="also write the report here")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.top < 1:
        sys.stderr.write("error: --top must be >= 1\n")
        return 1
    if args.since_offset < 0:
        sys.stderr.write("error: --since-offset must be >= 0\n")
        return 1
    if not os.path.exists(args.eve):
        sys.stderr.write("error: eve file not found: %s\n" % args.eve)
        return 1

    since_ts = None
    if args.since_ts:
        since_ts = parse_ts(args.since_ts)
        if since_ts is None:
            sys.stderr.write("error: --since-ts is not a parseable ISO8601 timestamp: %s\n" % args.since_ts)
            return 1

    try:
        hosts = parse_hosts(args.hosts)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("error: --hosts: %s\n" % exc)
        return 1

    expect = DEFAULT_EXPECT
    if args.expect:
        expect = tuple(p.strip() for p in args.expect.split(",") if p.strip())
        if not expect:
            sys.stderr.write("error: --expect listed no protocols\n")
            return 1

    analysis = Analysis(hosts=hosts, top=args.top, expect=expect)
    try:
        scan(args.eve, analysis, offset=args.since_offset, since_ts=since_ts)
    except OSError as exc:
        sys.stderr.write("error: reading %s: %s\n" % (args.eve, exc))
        return 1

    report = build_report(analysis, args)
    text = json.dumps(report, indent=2, sort_keys=True) if args.json else render_text(report)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")
        except OSError as exc:
            sys.stderr.write("error: writing %s: %s\n" % (args.out, exc))
            return 1
    return 2 if report["empty_window"] else 0


if __name__ == "__main__":
    sys.exit(main())
