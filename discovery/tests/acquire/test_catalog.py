"""Tests for aximprint.acquire.catalog.

No test here opens a network socket — a `_FakeClient` stub stands in for
`aximprint.acquire.client.Client`, since this module only ever calls `client.request(
method, path, *, params=, expected_status=)` and never touches `requests`
itself. `tests/acquire/test_client.py` already proves that plumbing against a real
`requests.Session` with a fake transport adapter; this file only needs to
prove catalog.py's own parsing/assembly logic.

Raw `time_start`/`time_end` fixture values are built from `_BASE` (a
plausible unix timestamp) plus a small offset, both in the *raw fs-field
unit* — `instant_from_fs_field`'s plausibility guard (`ImplausibleInstantError`,
Q19) rejects anything that doesn't land in roughly 1990-2100 once converted,
so a bare small integer like `1000` isn't a valid fixture on its own.
Duration assertions go through `_dur()` (raw-unit delta ->
`Duration`) rather than a hardcoded nanosecond literal, so this file doesn't
duplicate `timeutil.FS_FIELD_NS_PER_UNIT`'s value and silently drift from it.
"""

import pytest

from aximprint.acquire.catalog import (
    CatalogError,
    FileEntry,
    GapReport,
    StreamIdMappingError,
    StreamListing,
    StreamRange,
    list_streams,
    stream_id_for_name,
    stream_range,
)
from aximprint.acquire.timeutil import (
    FS_FIELD_NS_PER_UNIT,
    Duration,
    InstantParseError,
    instant_from_fs_field,
)

# A plausible raw fs-field value (unix seconds, under the current Q19
# assumption) to build fixture file timestamps from.
_BASE = 1_700_000_000


def _dur(raw_delta: int) -> Duration:
    """The `Duration` a gap of `raw_delta` raw fs-field units converts to,
    computed via the same constant `catalog.py` uses — never a hardcoded
    nanosecond literal that would silently drift from `timeutil.py`.
    """
    return Duration(raw_delta * FS_FIELD_NS_PER_UNIT)


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """Records every call and returns canned bodies keyed by (method, path)."""

    def __init__(self, responses):
        # responses: list of (method, path, params) -> body, matched in order
        self._responses = list(responses)
        self.calls = []

    def request(self, method, path, *, params=None, expected_status=200):
        self.calls.append((method, path, params, expected_status))
        _, _, _, body = self._responses.pop(0)
        return _FakeResponse(body)


def _file(name, start_offset, end_offset, size=1000, epoch=1, seqnum=1):
    # time_start/time_end are strings on the wire (measured 2026-08-05 live
    # probe against CorpAPCN) — stringified here so every existing fixture
    # call site keeps passing a plain int offset for readability.
    return {
        "name": name,
        "permissions": 0o644,
        "user": "root",
        "group": "root",
        "size": size,
        "date": "2026-08-04",
        "time_start": str(_BASE + start_offset),
        "time_end": str(_BASE + end_offset),
        "epoch": epoch,
        "seqnum": seqnum,
        "type": "f",
    }


# --- list_streams: §5.1 list/single-item ambiguity --------------------------


def test_list_streams_parses_documented_shape():
    body = {
        "stream": [
            {
                "name": "0",
                "permissions": 0o755,
                "user": "root",
                "group": "root",
                "size": 4096,
                "date": "2026-08-01",
                "type": "d",
            },
            {
                "name": "1",
                "permissions": 0o755,
                "user": "root",
                "group": "root",
                "size": 4096,
                "date": "2026-08-01",
                "type": "d",
            },
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    streams = list_streams(client)

    assert streams == [
        StreamListing(name="0", permissions=0o755, user="root", group="root", size=4096, date="2026-08-01", type="d"),
        StreamListing(name="1", permissions=0o755, user="root", group="root", size=4096, date="2026-08-01", type="d"),
    ]
    assert client.calls == [("GET", "/axapi/fs/streams", None, 200)]


def test_list_streams_type_checks_not_key_checks():
    # The single-stream response (§5.2) uses the SAME key `stream` holding a
    # JSON object, not an array. list_streams must reject that by type, not
    # accidentally iterate a dict's keys as if they were stream records.
    body = {"stream": {"name": "0", "permissions": 0o755, "user": "root", "group": "root", "size": 4096, "date": "x", "type": "d"}}
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    with pytest.raises(CatalogError):
        list_streams(client)


def test_list_streams_missing_field_raises():
    body = {"stream": [{"name": "0", "permissions": 0o755}]}  # missing user/group/size/date/type
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    with pytest.raises(CatalogError):
        list_streams(client)


def test_list_streams_non_object_entry_raises():
    body = {"stream": ["not-an-object"]}
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    with pytest.raises(CatalogError):
        list_streams(client)


# --- list_streams: bare-list response (measured 2026-08-05 live probe) ------


def test_list_streams_parses_bare_list_shape():
    """`GET /axapi/fs/streams` returns a BARE JSON LIST on the live
    appliance, not the documented `{"stream": [...]}` wrapper — the exact
    shape measured against CorpAPCN (PacketXpress 5.1.0).
    """
    body = [
        {
            "date": "Oct 18 04:23",
            "group": "capture-grp",
            "is_directory": True,
            "name": "stream08",
            "permissions": "0444",
            "size": 1,
            "time_start": "4000000h",
            "type": "directory",
            "user": "capture-user",
        }
    ]
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    streams = list_streams(client)

    assert streams == [
        StreamListing(
            name="stream08", permissions="0444", user="capture-user", group="capture-grp", size=1, date="Oct 18 04:23", type="directory"
        )
    ]


def test_list_streams_filters_the_disk_usage_footer_entry():
    """The disk-usage footer line ("20.538 TB of 20.958 TB available, 98.00%
    utilized") is returned as a fake `"TB"`-named entry mixed into the real
    stream list — filtered out, never surfaced as a `StreamListing` and never
    raised as a `CatalogError` (it isn't a wire-contract violation, just
    noise the appliance appends).
    """
    real = {
        "date": "Oct 18 04:23",
        "group": "capture-grp",
        "is_directory": True,
        "name": "stream08",
        "permissions": "0444",
        "size": 1,
        "time_start": "4000000h",
        "type": "directory",
        "user": "capture-user",
    }
    junk = {
        "date": "TB of 20.958",
        "epoch": "utilized",
        "group": "using",
        "is_directory": False,
        "name": "TB",
        "permissions": "0000",
        "size": "20.538",
        "time_end": "98.00%",
        "time_start": "available,",
        "type": "file",
        "user": "is",
    }
    body = [real, junk]
    client = _FakeClient([("GET", "/axapi/fs/streams", None, body)])

    streams = list_streams(client)

    assert [s.name for s in streams] == ["stream08"]


# --- stream_range: assembly from fs/files ------------------------------------


def test_stream_range_computes_min_start_max_end():
    body = {
        "files": [
            _file("a.pcap", 0, 1000),
            _file("b.pcap", 1000, 2000),
            _file("c.pcap", 2000, 3500),
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "2"}, body)])

    result = stream_range(client, 2)

    assert result.stream == "2"
    assert result.first == instant_from_fs_field(str(_BASE + 0))
    assert result.last == instant_from_fs_field(str(_BASE + 3500))
    assert result.file_count == 3
    assert result.total_bytes == 3000
    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)
    assert client.calls == [("GET", "/axapi/fs/files", {"stream": "2"}, 200)]


def test_stream_range_out_of_order_listing_sorts_correctly():
    body = {
        "files": [
            _file("c.pcap", 2000, 3000),
            _file("a.pcap", 0, 1000),
            _file("b.pcap", 1000, 2000),
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.first == instant_from_fs_field(str(_BASE + 0))
    assert result.last == instant_from_fs_field(str(_BASE + 3000))
    assert result.gaps.count == 0


def test_stream_range_single_file():
    body = {"files": [_file("only.pcap", 0, 1000, size=42)]}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "3"}, body)])

    result = stream_range(client, 3)

    assert result.first == instant_from_fs_field(str(_BASE + 0))
    assert result.last == instant_from_fs_field(str(_BASE + 1000))
    assert result.file_count == 1
    assert result.total_bytes == 42
    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)


def test_stream_range_empty_stream_yields_no_range_not_zero_length():
    body = {"files": []}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "4"}, body)])

    result = stream_range(client, 4)

    assert result.first is None
    assert result.last is None
    assert result.file_count == 0
    assert result.total_bytes == 0
    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)


def test_stream_range_type_checks_not_key_checks():
    body = {"files": {"name": "only.pcap"}}  # object, not array — malformed for this endpoint
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    with pytest.raises(CatalogError):
        stream_range(client, 0)


def test_stream_range_missing_time_start_raises_not_skews():
    body = {"files": [{"name": "a.pcap", "size": 10, "time_end": _BASE + 1000, "epoch": 1, "seqnum": 1}]}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    with pytest.raises(CatalogError):
        stream_range(client, 0)


def test_stream_range_malformed_time_field_type_raises():
    body = {"files": [{**_file("a.pcap", 0, 1000), "time_start": "not-an-int"}]}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    with pytest.raises(InstantParseError):  # propagated from timeutil, not swallowed
        stream_range(client, 0)


def test_stream_range_passes_stream_param_as_string():
    body = {"files": []}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "7"}, body)])

    stream_range(client, 7)

    assert client.calls[0][2] == {"stream": "7"}


# --- stream_range: bare-list response (measured 2026-08-05 live probe) ------


def test_stream_range_parses_bare_list_shape():
    """`GET /axapi/fs/files` returns a BARE JSON LIST on the live appliance,
    not the documented `{"files": [...]}` wrapper — the exact shape measured
    against CorpAPCN (PacketXpress 5.1.0), with string `time_start`/
    `time_end`/`epoch`/`seqnum`.
    """
    body = [
        {
            "date": "Jun 18 15:17",
            "epoch": "1777907484",
            "group": "capture-ops",
            "is_directory": False,
            "name": "1781795842.pcap",
            "permissions": "444",
            "seqnum": "1083",
            "size": 671923900,
            "time_end": "1781799442",
            "time_start": "1781795842",
            "type": "file",
            "user": "capture-user",
        }
    ]
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "8"}, body)])

    result = stream_range(client, 8)

    assert result.file_count == 1
    assert result.total_bytes == 671923900
    assert result.first == instant_from_fs_field("1781795842")
    assert result.last == instant_from_fs_field("1781799442")


def test_stream_range_filters_the_disk_usage_footer_entry():
    """The disk-usage footer's fake file record — `int(time_start)` on it
    (`"available,"`) is what crashed an earlier live run — is filtered out
    before parsing, never raised, alongside a real file in the same list.
    """
    real = _file("a.pcap", 0, 1000)
    junk = {
        "date": "TB of 20.958",
        "epoch": "utilized",
        "group": "using",
        "is_directory": False,
        "name": "TB",
        "permissions": "0000",
        "size": "20.538",
        "time_end": "98.00%",
        "time_start": "available,",
        "type": "file",
        "user": "is",
    }
    body = {"files": [real, junk]}
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.file_count == 1
    assert result.total_bytes == real["size"]


# --- gap detection ------------------------------------------------------------


def test_gap_detection_finds_a_known_gap():
    body = {
        "files": [
            _file("a.pcap", 0, 1000),
            _file("b.pcap", 5000, 6000),  # 4000-unit gap after a
            _file("c.pcap", 6000, 7000),  # abuts b, no gap
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps.count == 1
    assert result.gaps.total_duration == _dur(4000)
    assert result.gaps.largest == _dur(4000)


def test_gap_detection_ignores_contiguous_files():
    body = {
        "files": [
            _file("a.pcap", 0, 1000),
            _file("b.pcap", 1000, 2000),
            _file("c.pcap", 2000, 3000),
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)


def test_gap_detection_multiple_gaps_reports_largest_and_total():
    body = {
        "files": [
            _file("a.pcap", 0, 1000),
            _file("b.pcap", 3000, 4000),  # 2000-unit gap
            _file("c.pcap", 4000, 5000),  # no gap
            _file("d.pcap", 9000, 10000),  # 4000-unit gap
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps.count == 2
    assert result.gaps.total_duration == _dur(6000)
    assert result.gaps.largest == _dur(4000)


def test_gap_detection_overlapping_files_are_not_a_negative_gap():
    body = {
        "files": [
            _file("a.pcap", 0, 2000),
            _file("b.pcap", 1000, 3000),  # overlaps a — not a gap
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)


def test_gap_detection_a_file_nested_inside_its_predecessor_is_not_a_phantom_gap():
    """Reproduces the reviewer's finding: naive consecutive-pair
    differencing (`curr.time_start - prev.time_end`) is only valid when no
    file nests inside or overlaps its predecessor — sorting by `time_start`
    alone does not guarantee that. `b` and `c` both nest entirely inside `a`
    ([0, 100] continuously covered); a consecutive-pair diff against `a`'s
    early `time_end` (10 or 60) would fabricate a gap where none exists.
    """
    body = {
        "files": [
            _file("a.pcap", 0, 100),
            _file("b.pcap", 10, 20),  # nested inside a
            _file("c.pcap", 50, 60),  # nested inside a
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps == GapReport(count=0, total_duration=Duration(0), largest=None)


def test_gap_detection_overlap_does_not_mask_a_real_gap_after_it():
    """A running-max-of-`time_end` bug that only tracks the *previous* file's
    end (rather than the max seen so far) would still be fooled by an overlap
    immediately followed by a real gap: `b` overlaps `a` but ends earlier than
    `a`, so the next gap must be measured from `max(a.end, b.end)`, not from
    `b.end` alone.
    """
    body = {
        "files": [
            _file("a.pcap", 0, 5000),
            _file("b.pcap", 1000, 2000),  # nested inside a, ends well before a
            _file("c.pcap", 9000, 10000),  # real gap, measured from a's end (5000), not b's (2000)
        ]
    }
    client = _FakeClient([("GET", "/axapi/fs/files", {"stream": "0"}, body)])

    result = stream_range(client, 0)

    assert result.gaps.count == 1
    assert result.gaps.total_duration == _dur(4000)
    assert result.gaps.largest == _dur(4000)


# --- shared error hierarchy ----------------------------------------------------


def test_catalog_error_is_also_an_xtract_error():
    """`CatalogError` is also a bare `ValueError`, which means an ordinary
    `except ValueError:` somewhere else in the codebase can silently swallow
    a wire-contract violation — a known, accepted risk (not fixed by this
    reparenting). Reparenting under `XtractError` gives a caller a way to
    catch every `xtract`-raised error deliberately, without also catching
    every unrelated `ValueError`.
    """
    from aximprint.acquire.errors import XtractError

    assert issubclass(CatalogError, XtractError)
    assert issubclass(CatalogError, ValueError)


# --- Q21 isolation -------------------------------------------------------------


def test_stream_id_for_name_assumes_decimal_identity():
    # A bare decimal name is still tolerated as a fallback (in case the
    # documented wrapper response ever pairs with the originally-assumed
    # bare-integer naming on some other firmware) — see
    # test_stream_id_for_name_maps_streamnn_pattern for the resolved (Q21),
    # measured live-probe naming convention.
    assert stream_id_for_name("2") == 2
    assert stream_id_for_name("0") == 0


def test_stream_id_for_name_maps_streamnn_pattern():
    """Q21 RESOLVED 2026-08-05 by live probe: `fs/streams`' `name` is
    `streamNN`, not a bare decimal integer — `stream08` -> `8`.
    """
    assert stream_id_for_name("stream08") == 8
    assert stream_id_for_name("stream00") == 0
    assert stream_id_for_name("stream11") == 11


def test_stream_id_for_name_rejects_non_decimal_name():
    with pytest.raises(StreamIdMappingError):
        stream_id_for_name("primary-capture")


def test_stream_id_for_name_rejects_the_disk_usage_footer_pseudo_stream():
    with pytest.raises(StreamIdMappingError):
        stream_id_for_name("TB")


def test_q21_isolated_behind_one_flagged_function():
    import inspect

    from aximprint.acquire import catalog

    source = inspect.getsource(catalog.stream_id_for_name)
    assert "# Q21:" in source
    assert "docs/OPEN_QUESTIONS.md" in source
    # No other function in the module references the Q21 marker — the mapping
    # lives in exactly one place.
    module_source = inspect.getsource(catalog)
    assert module_source.count("# Q21:") == 1


# --- FileEntry/StreamRange dataclasses stay simple value objects -----------


def test_file_entry_is_a_plain_value_object():
    fe = FileEntry(
        name="a.pcap",
        size=10,
        time_start=instant_from_fs_field(str(_BASE)),
        time_end=instant_from_fs_field(str(_BASE + 1)),
        epoch=1,
        seqnum=1,
    )
    assert fe.name == "a.pcap"


def test_stream_range_is_a_plain_value_object():
    sr = StreamRange(
        stream="0",
        first=None,
        last=None,
        file_count=0,
        total_bytes=0,
        gaps=GapReport(0, Duration(0), None),
    )
    assert sr.file_count == 0
