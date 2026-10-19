"""Tests for aximprint.acquire.manifest.

No test here opens a network socket or a real appliance — this module is
pure file I/O against `tmp_path`.
"""

import json

from aximprint.acquire.manifest import (
    ManifestError,
    ManifestHeader,
    WindowRecord,
    append_window_record,
    manifest_path,
    read_manifest,
    write_header,
)


def _header(**overrides):
    defaults = {"streams": "2-4", "filter": None, "interval_ns": 60_000_000_000, "tool_version": "0.0.1"}
    defaults.update(overrides)
    return ManifestHeader(**defaults)


def _record(**overrides):
    defaults = {
        "start": "1000",
        "end": "1060",
        "outcome": "written",
        "bytes": 4096,
        "task_guid": "guid-1",
        "duration_s": 0.5,
        "attempt": 1,
        "timestamp": "2026-08-04T00:00:00+00:00",
    }
    defaults.update(overrides)
    return WindowRecord(**defaults)


# --- round trip -----------------------------------------------------------------


def test_missing_manifest_reads_as_empty(tmp_path):
    result = read_manifest(tmp_path)
    assert result.header is None
    assert result.records == []


def test_header_round_trips(tmp_path):
    header = _header()
    write_header(tmp_path, header)

    result = read_manifest(tmp_path)

    assert result.header == header
    assert result.records == []


def test_window_records_round_trip_in_order(tmp_path):
    write_header(tmp_path, _header())
    r1 = _record(start="1000", end="1060", outcome="written")
    r2 = _record(start="1060", end="1120", outcome="empty", bytes=0, task_guid=None, detail="418")
    append_window_record(tmp_path, r1)
    append_window_record(tmp_path, r2)

    result = read_manifest(tmp_path)

    assert result.records == [r1, r2]


def test_detail_defaults_to_none_and_round_trips(tmp_path):
    record = _record(outcome="failed", bytes=0, task_guid=None, detail="500: boom")
    append_window_record(tmp_path, record)

    result = read_manifest(tmp_path)

    assert result.records[0].detail == "500: boom"


def test_manifest_is_one_json_object_per_line(tmp_path):
    write_header(tmp_path, _header())
    append_window_record(tmp_path, _record())

    lines = manifest_path(tmp_path).read_text().splitlines()

    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)  # each line stands alone as valid JSON
        assert isinstance(obj, dict)
        assert "kind" in obj


# --- truncated final line (simulated hard kill) tolerated on read -------------


def test_truncated_final_line_is_tolerated_on_read(tmp_path):
    write_header(tmp_path, _header())
    append_window_record(tmp_path, _record(start="1000"))
    path = manifest_path(tmp_path)
    with open(path, "a") as fh:
        fh.write('{"kind": "window", "start": "1060", "end": "112')  # truncated, no trailing newline

    result = read_manifest(tmp_path)

    assert result.header is not None
    assert [r.start for r in result.records] == ["1000"]  # the truncated record is dropped, not raised


def test_truncated_header_line_is_tolerated_and_leaves_header_none(tmp_path):
    path = manifest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "header", "streams": "0-1", "filte')  # truncated, no newline

    result = read_manifest(tmp_path)

    assert result.header is None
    assert result.records == []


def test_only_the_first_header_line_is_kept(tmp_path):
    # write_header itself is a caller-disciplined single-call primitive, but
    # read_manifest must still be defensive about a manifest that somehow
    # ended up with two header-shaped lines.
    write_header(tmp_path, _header(streams="0-1"))
    write_header(tmp_path, _header(streams="9-9"))

    result = read_manifest(tmp_path)

    assert result.header.streams == "0-1"


# --- blank lines tolerated ------------------------------------------------------


def test_blank_lines_between_records_are_skipped(tmp_path):
    write_header(tmp_path, _header())
    path = manifest_path(tmp_path)
    with open(path, "a") as fh:
        fh.write("\n\n")
    append_window_record(tmp_path, _record())

    result = read_manifest(tmp_path)

    assert len(result.records) == 1


# --- no credential/hostname/capture-byte material ------------------------------


def test_manifest_never_contains_a_host_or_credential_field(tmp_path):
    write_header(tmp_path, _header(streams="2-4", filter="tcp and port 443 secret-looking-value"))
    append_window_record(tmp_path, _record(task_guid="guid-abc-123"))

    raw = manifest_path(tmp_path).read_text()

    for forbidden in ("host", "hostname", "password", "api_key", "token", "Authorization", "bearer"):
        assert forbidden not in raw


def test_window_record_has_no_capture_bytes_field(tmp_path):
    append_window_record(tmp_path, _record(bytes=123456))

    obj = json.loads(manifest_path(tmp_path).read_text().splitlines()[0])

    # "bytes" is a count, never the capture payload itself.
    assert obj["bytes"] == 123456
    assert isinstance(obj["bytes"], int)


def test_manifest_never_contains_a_host_or_credential_field_on_a_failed_record(tmp_path):
    """Extends the success-path-only coverage above: a `"failed"` outcome's
    `detail` is the field most at risk of carrying transport-layer leakage
    (`client.py`'s `ConnectionFailedError` message, `runner.py`'s
    `Failed.detail`) — this exercises that shape too, not just a
    `"written"` record with no `detail` at all.
    """
    write_header(tmp_path, _header(streams="2-4", filter="tcp and port 443 secret-looking-value"))
    append_window_record(
        tmp_path,
        _record(
            outcome="failed",
            bytes=0,
            task_guid=None,
            detail="GET /axapi/pcap failed after 4 attempt(s): ConnectionError",
        ),
    )

    raw = manifest_path(tmp_path).read_text()

    for forbidden in (
        "host",
        "hostname",
        "password",
        "api_key",
        "token",
        "Authorization",
        "bearer",
        "corpapcn",
        "HTTPSConnectionPool",
    ):
        assert forbidden not in raw


# --- shared error hierarchy ----------------------------------------------------


def test_manifest_error_is_also_an_xtract_error():
    from aximprint.acquire.errors import XtractError

    assert issubclass(ManifestError, XtractError)
    assert issubclass(ManifestError, ValueError)
