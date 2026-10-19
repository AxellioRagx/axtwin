"""Cluster-free tests for `scripts/verify_wire_protocols.py`.

The tool decides whether a round's traffic produced REAL protocol records in a
Suricata `eve.json` or only bare `tcp`/`udp` with `app_proto: failed|unknown`
(the filler-bytes failure mode). Its whole value is that it must NOT produce a
plausible-looking wrong answer (LES-001), so these tests pin the two directions
that matter most:

  * a **shape-only** window (everything `app_proto: failed`, zero protocol
    records) must NOT produce a PASS anywhere, and must name the reset/
    unclassified flows on each protocol's ports;
  * a **real** window (tls with SNI, krb5 with a reply msgtype, smb with a
    dialect + successful tree connect, ldap with a bind result, ssh with both
    banners) must produce PASS with `completion: completed`;
  * an **empty** window must report "no records in window" / NO-DATA, never a
    row of zeros that reads like a clean pass.

Plus the robustness the 720 MB live file demands: truncated final line,
non-JSON lines, byte-offset and timestamp windowing, and a stable `--json`
shape.

The script lives in `scripts/` (not a package) and is loaded via
`importlib.util.spec_from_file_location` per LES-029. The module is registered
in `sys.modules` BEFORE `exec_module` — module-level constructs that resolve
`sys.modules[__module__]` (dataclasses among them) fail with an opaque
AttributeError otherwise.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _load(mod_name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register BEFORE exec_module (see docstring)
    spec.loader.exec_module(mod)
    return mod


vwp = _load("verify_wire_protocols", "verify_wire_protocols.py")


# ---------------------------------------------------------------------------
# fixtures — hand-written eve records
# ---------------------------------------------------------------------------
TS = "2026-09-21T13:00:%02d.000000+0000"


def _flow(port, app_proto="failed", rst=False, second=1, proto="TCP", src=None, dst=None):
    rec = {
        "timestamp": TS % second,
        "event_type": "flow",
        "src_ip": src or "10.66.10.31",
        "dest_ip": dst or "10.66.10.21",
        "src_port": 50000 + port,
        "dest_port": port,
        "proto": proto,
        "app_proto": app_proto,
        "flow": {"pkts_toserver": 5, "bytes_toserver": 300},
    }
    if rst:
        rec["tcp"] = {"rst": True, "tcp_flags": "1e"}
    else:
        rec["tcp"] = {"rst": False, "tcp_flags": "1b"}
    return rec


SHAPE_ONLY = [
    _flow(88, rst=True, second=1),
    _flow(445, rst=True, second=2),
    _flow(389, second=3),
    _flow(443, second=4, dst="10.66.10.99"),
    _flow(22, second=5, dst="10.66.10.40"),
]

REAL = [
    {
        "timestamp": TS % 1,
        "event_type": "tls",
        "src_ip": "10.66.10.31",
        "dest_ip": "93.184.216.34",
        "dest_port": 443,
        "proto": "TCP",
        "app_proto": "tls",
        "tls": {
            "sni": "www.example.com",
            "version": "TLS 1.3",
            "subject": "CN=example.com",
            "ja3": {"hash": "aabb"},
            "ja3s": {"hash": "ccdd"},
        },
    },
    {
        "timestamp": TS % 2,
        "event_type": "tls",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.99",
        "dest_port": 443,
        "proto": "TCP",
        "tls": {"sni": "update.googleapis.com", "version": "UNDETERMINED"},
    },
    {
        "timestamp": TS % 3,
        "event_type": "krb5",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dest_port": 88,
        "proto": "TCP",
        "krb5": {
            "msgtype": "KRB_AS_REP",
            "cname": "alice",
            "realm": "CORP.AXR.LAB",
            "sname": "krbtgt/CORP.AXR.LAB",
        },
    },
    {
        "timestamp": TS % 4,
        "event_type": "krb5",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dest_port": 88,
        "proto": "TCP",
        "krb5": {"msgtype": "KRB_ERROR", "error_code": "KDC_ERR_PREAUTH_REQUIRED"},
    },
    {
        "timestamp": TS % 5,
        "event_type": "smb",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dest_port": 445,
        "proto": "TCP",
        "smb": {
            "dialect": "3.1.1",
            "command": "SMB2_COMMAND_TREE_CONNECT",
            "status": "STATUS_SUCCESS",
            "share": "\\\\dc\\share",
        },
    },
    {
        "timestamp": TS % 6,
        "event_type": "ldap",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dest_port": 389,
        "proto": "TCP",
        "ldap": {
            "request": {"operation": "bind_request"},
            "responses": [
                {"operation": "bind_response", "bind_response": {"result_code": "success"}}
            ],
        },
    },
    {
        "timestamp": TS % 7,
        "event_type": "ssh",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.40",
        "dest_port": 22,
        "proto": "TCP",
        "ssh": {
            "client": {"software_version": "OpenSSH_9.2p1"},
            "server": {"software_version": "OpenSSH_9.2p1 Debian"},
        },
    },
    {
        "timestamp": TS % 8,
        "event_type": "dns",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dns": {
            "type": "answer",
            "rrname": "dc.corp.axr.lab",
            "rcode": "NOERROR",
            "answers": [{"rrname": "dc.corp.axr.lab", "rrtype": "A"}],
        },
    },
    {
        "timestamp": TS % 9,
        "event_type": "http",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.99",
        "dest_port": 80,
        "proto": "TCP",
        "http": {"hostname": "neverssl.com", "url": "/", "status": 200},
    },
    {
        "timestamp": TS % 10,
        "event_type": "dcerpc",
        "src_ip": "10.66.10.31",
        "dest_ip": "10.66.10.21",
        "dest_port": 445,
        "proto": "TCP",
        "dcerpc": {"request": "BIND", "response": "BINDACK"},
    },
    {
        "timestamp": TS % 11,
        "event_type": "rdp",
        "src_ip": "10.66.10.32",
        "dest_ip": "10.66.10.50",
        "dest_port": 3389,
        "proto": "TCP",
        "rdp": {"event_type": "initial_request", "cookie": "mstshash=alice"},
    },
    {
        "timestamp": TS % 12,
        "event_type": "snmp",
        "src_ip": "10.66.10.32",
        "dest_ip": "10.66.10.1",
        "dest_port": 161,
        "proto": "UDP",
        "snmp": {"version": 2, "pdu_type": "get_response"},
    },
]


def write_eve(path: Path, records, *, prefix: str = "", suffix: str = "") -> Path:
    """Write `records` as eve.json lines, with optional raw pre/suffix text."""
    body = "".join(json.dumps(r) + "\n" for r in records)
    path.write_text(prefix + body + suffix, encoding="utf-8")
    return path


def run(argv):
    """Run the tool's `main()`, capturing nothing (callers use --out/--json)."""
    return vwp.main(argv)


def report_for(path: Path, extra=None):
    """Build the report dict directly (no stdout parsing)."""
    argv = ["--eve", str(path), "--json"] + list(extra or [])
    args = vwp.build_parser().parse_args(argv)
    since_ts = vwp.parse_ts(args.since_ts) if args.since_ts else None
    analysis = vwp.Analysis(
        hosts=vwp.parse_hosts(args.hosts),
        top=args.top,
        expect=tuple(p.strip() for p in args.expect.split(",")) if args.expect else vwp.DEFAULT_EXPECT,
    )
    vwp.scan(str(path), analysis, offset=args.since_offset, since_ts=since_ts)
    return vwp.build_report(analysis, args)


def verdict(report, proto):
    for v in report["verdicts"]:
        if v["protocol"] == proto:
            return v
    raise AssertionError("no verdict for %s" % proto)


# ---------------------------------------------------------------------------
# the shape-only window — the failure the engines are supposed to have fixed
# ---------------------------------------------------------------------------
def test_shape_only_window_produces_no_pass(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", SHAPE_ONLY))

    assert report["empty_window"] is False
    assert report["summary"]["pass"] == 0, "shape-only traffic must never PASS"
    for proto in ("tls", "krb5", "smb", "ldap", "ssh"):
        assert verdict(report, proto)["status"] == vwp.STATUS_ABSENT


def test_shape_only_window_counts_unclassified_and_resets(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", SHAPE_ONLY))
    census = report["census"]

    assert census["flows"] == len(SHAPE_ONLY)
    assert census["unclassified_flows"] == len(SHAPE_ONLY)
    assert census["reset_flows"] == 2
    assert census["unclassified_reset_flows"] == 2
    by_port = dict(census["unclassified_by_port_top"])
    assert by_port["tcp/88"] == 1
    assert by_port["tcp/445"] == 1
    reset_by_port = dict(census["unclassified_reset_by_port_top"])
    assert reset_by_port == {"tcp/445": 1, "tcp/88": 1}


def test_shape_only_absent_verdict_names_the_reset_flows(tmp_path):
    """ABSENT must explain "the port looked alive but nothing was parsed"."""
    report = report_for(write_eve(tmp_path / "eve.json", SHAPE_ONLY))

    notes = " ".join(verdict(report, "krb5")["notes"])
    assert "unclassified flow" in notes
    assert "RST" in notes
    assert "shape-only failure mode" in notes


def test_ldap_absent_flags_the_suricata_version_caveat(tmp_path):
    """`ldap` is a Suricata 8.0 event type — ABSENT may be a sensor limit."""
    report = report_for(write_eve(tmp_path / "eve.json", SHAPE_ONLY))

    notes = " ".join(verdict(report, "ldap")["notes"])
    assert "7.x" in notes and "SENSOR-VERSION" in notes


# ---------------------------------------------------------------------------
# the real window
# ---------------------------------------------------------------------------
def test_real_window_passes_every_expected_protocol(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", REAL))

    assert report["summary"]["absent"] == 0
    assert report["summary"]["partial"] == 0
    for proto in vwp.DEFAULT_EXPECT:
        assert verdict(report, proto)["status"] == vwp.STATUS_PASS, proto


def test_real_window_completion_flags(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", REAL))

    for proto in ("tls", "krb5", "smb", "ldap", "ssh", "dns", "http", "snmp", "dcerpc"):
        assert verdict(report, proto)["completion"] == "completed", proto
    # eve's rdp schema carries no session-established marker: say so, don't guess.
    rdp = verdict(report, "rdp")
    assert rdp["completion"] == "undetermined"
    assert any("UNDETERMINED" in n for n in rdp["notes"])


def test_tls_sni_census_and_handshake_only_labelling(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", REAL))
    tls = report["tls"]

    assert tls["records"] == 2
    assert tls["with_sni"] == 2
    assert tls["distinct_sni"] == 2
    assert tls["sni_top"] == [
        ["update.googleapis.com", 1],
        ["www.example.com", 1],
    ], "top-N ties break on the key, deterministically"
    assert tls["ja3_present"] == 1 and tls["ja3s_present"] == 1
    # The UNDETERMINED-version handshake is labelled, never counted as success.
    assert tls["handshake_only"] == 1
    assert "handshake-only" in " ".join(verdict(report, "tls")["notes"])


def test_krb5_completion_needs_a_reply_not_just_a_request(tmp_path):
    request_only = [
        {
            "timestamp": TS % 1,
            "event_type": "krb5",
            "src_ip": "10.66.10.31",
            "dest_ip": "10.66.10.21",
            "dest_port": 88,
            "proto": "TCP",
            "krb5": {"msgtype": "KRB_AS_REQ", "cname": "alice"},
        },
        {
            "timestamp": TS % 2,
            "event_type": "krb5",
            "src_ip": "10.66.10.31",
            "dest_ip": "10.66.10.21",
            "dest_port": 88,
            "proto": "TCP",
            "krb5": {"msgtype": "KRB_ERROR", "error_code": "KDC_ERR_C_PRINCIPAL_UNKNOWN"},
        },
    ]
    report = report_for(write_eve(tmp_path / "eve.json", request_only))
    v = verdict(report, "krb5")

    assert v["status"] == vwp.STATUS_PARTIAL
    assert v["completion"] == "not-completed"
    assert report["krb5"]["replies"] == 0
    assert report["krb5"]["errors"] == 1
    assert report["krb5"]["error_codes"] == {"KDC_ERR_C_PRINCIPAL_UNKNOWN": 1}


def test_smb_without_tree_connect_is_partial(tmp_path):
    negotiate_only = [
        {
            "timestamp": TS % 1,
            "event_type": "smb",
            "src_ip": "10.66.10.31",
            "dest_ip": "10.66.10.21",
            "dest_port": 445,
            "proto": "TCP",
            "smb": {"dialect": "3.1.1", "command": "SMB2_COMMAND_NEGOTIATE", "status": "STATUS_SUCCESS"},
        }
    ]
    report = report_for(write_eve(tmp_path / "eve.json", negotiate_only))
    v = verdict(report, "smb")

    assert v["status"] == vwp.STATUS_PARTIAL
    assert v["completion"] == "not-completed"
    assert "no TREE_CONNECT" in v["evidence"]
    assert report["smb"]["with_dialect"] == 1


def test_ldap_record_without_a_result_code_is_undetermined_not_pass(tmp_path):
    """Honest bucket: the parser saw LDAP, eve did not say how it ended."""
    no_result = [
        {
            "timestamp": TS % 1,
            "event_type": "ldap",
            "src_ip": "10.66.10.31",
            "dest_ip": "10.66.10.21",
            "dest_port": 389,
            "proto": "TCP",
            "ldap": {"request": {"operation": "search_request"}},
        }
    ]
    report = report_for(write_eve(tmp_path / "eve.json", no_result))
    v = verdict(report, "ldap")

    assert v["status"] == vwp.STATUS_PARTIAL
    assert v["completion"] == "undetermined"
    assert report["ldap"]["undetermined"] == 1
    assert any("UNDETERMINED" in n for n in v["notes"])


def test_ldap_bind_failure_is_partial_and_names_the_result_code(tmp_path):
    refused = [
        {
            "timestamp": TS % 1,
            "event_type": "ldap",
            "src_ip": "10.66.10.31",
            "dest_ip": "10.66.10.21",
            "dest_port": 389,
            "proto": "TCP",
            "ldap": {
                "request": {"operation": "bind_request"},
                "responses": [
                    {"operation": "bind_response", "result_code": "invalid_credentials"}
                ],
            },
        }
    ]
    report = report_for(write_eve(tmp_path / "eve.json", refused))
    v = verdict(report, "ldap")

    assert v["status"] == vwp.STATUS_PARTIAL
    assert v["completion"] == "not-completed"
    assert report["ldap"]["bind_success"] == 0
    assert "invalid_credentials" in v["evidence"]


def test_flow_classified_only_is_partial_never_pass(tmp_path):
    """Suricata 7.x logs tls flows but (GAP-076) may log no tls records."""
    flow_only = [
        _flow(443, app_proto="tls", second=1),
        _flow(389, app_proto="ldap", second=2),
    ]
    report = report_for(write_eve(tmp_path / "eve.json", flow_only))

    for proto in ("tls", "ldap"):
        v = verdict(report, proto)
        assert v["status"] == vwp.STATUS_PARTIAL, proto
        assert v["completion"] == "undetermined", proto
    assert "GAP-076" in " ".join(verdict(report, "tls")["notes"])
    assert report["tls"]["flow_app_proto_tls"] == 1
    assert report["ldap"]["flow_app_proto_ldap"] == 1


def test_mixed_window_separates_real_from_shape_only(tmp_path):
    report = report_for(write_eve(tmp_path / "eve.json", REAL[:6] + SHAPE_ONLY))

    assert verdict(report, "tls")["status"] == vwp.STATUS_PASS
    assert verdict(report, "krb5")["status"] == vwp.STATUS_PASS
    assert verdict(report, "smb")["status"] == vwp.STATUS_PASS
    assert verdict(report, "ldap")["status"] == vwp.STATUS_PASS
    # ssh has only a shape-only flow in this window.
    assert verdict(report, "ssh")["status"] == vwp.STATUS_ABSENT
    assert report["census"]["unclassified_flows"] == len(SHAPE_ONLY)
    assert report["census"]["event_type"]["flow"] == len(SHAPE_ONLY)


# ---------------------------------------------------------------------------
# robustness: truncated tail, junk lines, empty file
# ---------------------------------------------------------------------------
def test_truncated_final_line_is_tolerated_and_counted(tmp_path):
    path = write_eve(
        tmp_path / "eve.json", REAL[:3], suffix='{"timestamp":"2026-09-21T13:00:99","event_ty'
    )
    report = report_for(path)

    assert report["window"]["truncated_final_line"] == 1
    assert report["window"]["json_errors"] == 0
    assert report["window"]["records_in_window"] == 3


def test_non_json_lines_are_counted_not_fatal(tmp_path):
    path = write_eve(
        tmp_path / "eve.json",
        REAL[:3],
        prefix="this is not json\n\n",
        suffix="[not, an, object]\n",
    )
    report = report_for(path)

    assert report["window"]["records_in_window"] == 3
    assert report["window"]["json_errors"] == 2  # the text line + the JSON array
    assert report["window"]["json_error_samples"][0] == "this is not json"


def test_empty_file_reports_no_records_never_a_clean_pass(tmp_path, capsys):
    path = tmp_path / "eve.json"
    path.write_text("", encoding="utf-8")

    rc = run(["--eve", str(path)])
    out = capsys.readouterr().out

    assert rc == 2, "an empty window is its own exit code, not success"
    assert "NO RECORDS IN WINDOW" in out
    assert "NOT a pass" in out
    assert vwp.STATUS_PASS not in out.split("VERDICT")[-1]
    assert out.count(vwp.STATUS_NO_DATA) >= len(vwp.DEFAULT_EXPECT)


def test_empty_window_json_marks_every_protocol_no_data(tmp_path):
    path = tmp_path / "eve.json"
    path.write_text("", encoding="utf-8")
    report = report_for(path)

    assert report["empty_window"] is True
    assert report["summary"]["no_data"] == len(vwp.DEFAULT_EXPECT)
    assert report["summary"]["pass"] == 0
    assert all(v["status"] == vwp.STATUS_NO_DATA for v in report["verdicts"])


def test_window_with_only_stale_records_is_an_empty_window(tmp_path):
    """The live file is 720 MB of stale data: filtering it all out is NO-DATA."""
    path = write_eve(tmp_path / "eve.json", REAL)
    report = report_for(path, ["--since-ts", "2026-09-22T00:00:00+0000"])

    assert report["empty_window"] is True
    assert report["window"]["records_before_since_ts"] == len(REAL)
    assert report["summary"]["pass"] == 0


# ---------------------------------------------------------------------------
# windowing: offset, timestamp, and the two together
# ---------------------------------------------------------------------------
def test_since_offset_skips_everything_before_it(tmp_path):
    path = tmp_path / "eve.json"
    stale = "".join(json.dumps(r) + "\n" for r in SHAPE_ONLY)
    fresh = "".join(json.dumps(r) + "\n" for r in REAL)
    path.write_text(stale + fresh, encoding="utf-8")

    report = report_for(path, ["--since-offset", str(len(stale.encode("utf-8")))])

    assert report["window"]["records_in_window"] == len(REAL)
    assert report["census"]["unclassified_flows"] == 0
    assert verdict(report, "krb5")["status"] == vwp.STATUS_PASS


def test_since_ts_filters_by_record_timestamp(tmp_path):
    path = write_eve(tmp_path / "eve.json", REAL)

    report = report_for(path, ["--since-ts", TS % 7])

    assert report["window"]["records_in_window"] == 6  # seconds 07..12
    assert report["window"]["records_before_since_ts"] == 6
    assert verdict(report, "ssh")["status"] == vwp.STATUS_PASS
    assert verdict(report, "krb5")["status"] == vwp.STATUS_ABSENT


def test_offset_and_ts_compose_as_and(tmp_path):
    """Documented rule: offset seeks, timestamp filters; both apply."""
    path = tmp_path / "eve.json"
    stale = "".join(json.dumps(r) + "\n" for r in SHAPE_ONLY)
    fresh = "".join(json.dumps(r) + "\n" for r in REAL)
    path.write_text(stale + fresh, encoding="utf-8")

    report = report_for(
        path,
        ["--since-offset", str(len(stale.encode("utf-8"))), "--since-ts", TS % 11],
    )

    assert report["window"]["records_in_window"] == 2  # rdp + snmp
    assert report["window"]["filter_rule"].startswith("offset seeks, timestamp filters")
    assert verdict(report, "rdp")["status"] == vwp.STATUS_PASS
    assert verdict(report, "tls")["status"] == vwp.STATUS_ABSENT


def test_undated_records_are_excluded_and_reported_when_ts_filtering(tmp_path):
    undated = [{"event_type": "krb5", "krb5": {"msgtype": "KRB_AS_REP"}}]
    path = write_eve(tmp_path / "eve.json", REAL[:2] + undated)

    with_ts = report_for(path, ["--since-ts", TS % 1])
    assert with_ts["window"]["undated_skipped"] == 1
    assert with_ts["window"]["records_in_window"] == 2

    # Without a time filter, an undated record still counts.
    without_ts = report_for(path)
    assert without_ts["window"]["undated_skipped"] == 0
    assert without_ts["window"]["records_in_window"] == 3


def test_offset_past_eof_reads_nothing_and_warns(tmp_path):
    path = write_eve(tmp_path / "eve.json", REAL)

    report = report_for(path, ["--since-offset", "999999999"])

    assert report["empty_window"] is True
    assert report["window"]["warnings"]
    assert "beyond the file's current size" in report["window"]["warnings"][0]


# ---------------------------------------------------------------------------
# hosts labelling
# ---------------------------------------------------------------------------
def test_hosts_inline_map_labels_the_per_host_rows(tmp_path):
    path = write_eve(tmp_path / "eve.json", REAL)

    report = report_for(path, ["--hosts", "linux-client=10.66.10.31,dc=10.66.10.21"])
    labels = {h["ip"]: h["label"] for h in report["hosts"]}

    assert labels["10.66.10.31"] == "linux-client"
    assert labels["10.66.10.21"] == "dc"
    assert labels["10.66.10.40"] is None
    top = report["hosts"][0]
    assert top["ip"] == "10.66.10.31" and top["as_src"] >= top["as_dest"]


def test_hosts_json_file_accepts_both_directions(tmp_path):
    name_to_ip = tmp_path / "a.json"
    name_to_ip.write_text(json.dumps({"dc": "10.66.10.21"}), encoding="utf-8")
    ip_to_name = tmp_path / "b.json"
    ip_to_name.write_text(json.dumps({"10.66.10.21": "dc"}), encoding="utf-8")

    assert vwp.parse_hosts(str(name_to_ip)) == {"10.66.10.21": "dc"}
    assert vwp.parse_hosts(str(ip_to_name)) == {"10.66.10.21": "dc"}


def test_hosts_rejects_a_malformed_inline_entry():
    with pytest.raises(ValueError):
        vwp.parse_hosts("linux-client")


# ---------------------------------------------------------------------------
# CLI surface: --json shape, --out, exit codes, determinism
# ---------------------------------------------------------------------------
def test_json_output_shape_is_stable(tmp_path, capsys):
    path = write_eve(tmp_path / "eve.json", REAL)

    rc = run(["--eve", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["tool"] == "verify_wire_protocols"
    assert payload["schema_version"] == vwp.SCHEMA_VERSION
    assert set(payload) == {
        "census",
        "dcerpc",
        "dns",
        "empty_window",
        "hosts",
        "http",
        "krb5",
        "ldap",
        "rdp",
        "schema_version",
        "smb",
        "snmp",
        "ssh",
        "summary",
        "tls",
        "tool",
        "verdicts",
        "window",
    }
    assert set(payload["summary"]) == {"pass", "partial", "absent", "no_data"}
    assert [v["protocol"] for v in payload["verdicts"]] == sorted(vwp.DEFAULT_EXPECT)
    for v in payload["verdicts"]:
        assert set(v) == {"protocol", "status", "completion", "evidence", "notes"}
        assert v["status"] in (
            vwp.STATUS_PASS,
            vwp.STATUS_PARTIAL,
            vwp.STATUS_ABSENT,
            vwp.STATUS_NO_DATA,
        )


def test_json_output_is_byte_identical_across_runs(tmp_path, capsys):
    path = write_eve(tmp_path / "eve.json", REAL + SHAPE_ONLY)

    run(["--eve", str(path), "--json"])
    first = capsys.readouterr().out
    run(["--eve", str(path), "--json"])
    second = capsys.readouterr().out

    assert first == second


def test_out_writes_the_same_report_it_prints(tmp_path, capsys):
    path = write_eve(tmp_path / "eve.json", REAL)
    out = tmp_path / "report.json"

    rc = run(["--eve", str(path), "--json", "--out", str(out)])
    printed = capsys.readouterr().out

    assert rc == 0
    assert out.read_text(encoding="utf-8") == printed
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["pass"] == 10


def test_human_table_names_the_unclassified_flows(tmp_path, capsys):
    path = write_eve(tmp_path / "eve.json", SHAPE_ONLY)

    rc = run(["--eve", str(path), "--hosts", "dc=10.66.10.21"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "shape-only failure mode" in out
    assert "UNCLASSIFIED (app_proto failed/unknown/none): 5" in out
    assert "dc" in out  # the host label reached the per-host table
    assert "summary: PASS=0" in out


def test_expect_narrows_the_verdict_set(tmp_path):
    path = write_eve(tmp_path / "eve.json", REAL)

    report = report_for(path, ["--expect", "krb5,smb"])

    assert [v["protocol"] for v in report["verdicts"]] == ["krb5", "smb"]


def test_missing_file_and_bad_args_exit_one(tmp_path, capsys):
    assert run(["--eve", str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err

    path = write_eve(tmp_path / "eve.json", REAL)
    assert run(["--eve", str(path), "--since-ts", "not-a-timestamp"]) == 1
    assert "ISO8601" in capsys.readouterr().err
    assert run(["--eve", str(path), "--top", "0"]) == 1
    assert run(["--eve", str(path), "--since-offset", "-1"]) == 1


# ---------------------------------------------------------------------------
# unit-level helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expect_utc_hour",
    [
        ("2026-09-21T13:00:00.000000+0000", 13),
        ("2026-09-21T13:00:00Z", 13),
        ("2026-09-21T13:00:00", 13),
        ("2026-09-21T15:00:00+02:00", 13),
    ],
)
def test_parse_ts_accepts_the_shapes_suricata_emits(value, expect_utc_hour):
    from datetime import timezone

    dt = vwp.parse_ts(value)
    assert dt is not None
    assert dt.astimezone(timezone.utc).hour == expect_utc_hour


@pytest.mark.parametrize("value", ["", None, "yesterday", 17])
def test_parse_ts_rejects_junk(value):
    assert vwp.parse_ts(value) is None


@pytest.mark.parametrize(
    "rec,expect",
    [
        ({"tcp": {"rst": True}}, True),
        ({"tcp": {"rst": False}}, False),
        ({"tcp": {"tcp_flags": "1e"}}, True),  # 0x04 set
        ({"tcp": {"tcp_flags": "1b"}}, False),  # 0x04 clear
        ({"tcp": {"tcp_flags": "zz"}}, False),  # unparseable, not a crash
        ({"tcp": {"state": "reset"}}, True),
        ({"tcp": {"state": "closed"}}, False),  # orderly FIN is not a reset
        ({"flow": {"reason": "reset"}}, True),
        ({}, False),
    ],
)
def test_flow_was_reset(rec, expect):
    assert vwp.flow_was_reset(rec) is expect


def test_top_n_tie_breaks_on_the_key():
    from collections import Counter

    counter = Counter({"b": 2, "a": 2, "c": 5})
    assert vwp.top_n(counter, 3) == [["c", 5], ["a", 2], ["b", 2]]


def test_iter_lines_handles_an_oversized_line(tmp_path):
    path = tmp_path / "eve.json"
    huge = "x" * (vwp.MAX_LINE_BYTES + 10)
    path.write_text(huge + "\n" + json.dumps(REAL[0]) + "\n", encoding="utf-8")

    analysis = vwp.Analysis()
    vwp.scan(str(path), analysis)

    assert analysis.oversized_lines == 1
    assert analysis.records == 1  # the good record after it still parsed
