"""Tests for aximprint.acquire.extract.

No test here opens a network socket: same `FakeAdapter`-on-a-real-`Session`
pattern as `tests/acquire/test_client.py` (a `requests.adapters.BaseAdapter` mounted
onto a real `requests.Session`, so `params=` still goes through `requests`'
real URL-building/encoding machinery). `tmp_path` stands in for `--out`.
"""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from aximprint.acquire import extract
from aximprint.acquire.auth import Credential
from aximprint.acquire.client import DEFAULT_CHUNK_SIZE, Client
from aximprint.acquire.errors import UnauthorizedError
from aximprint.acquire.extract import Empty, Failed, Written, extract_window
from aximprint.acquire.timeutil import parse_instant
from aximprint.acquire.windows import Window
from tests.acquire.conftest import pcap_body

# --- Test doubles: a socket-free requests transport -------------------------


class FakeAdapter(requests.adapters.BaseAdapter):
    """`script` is consumed one item per `send()` call (the last item repeats
    once exhausted). Each item is either a `requests.Response` or an
    `Exception`/`BaseException` (instance or class) to raise.
    """

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.requests = []  # captured PreparedRequest objects, call order

    def send(self, request, **kwargs):
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self.script) - 1)
        item = self.script[idx]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("fake transport error")
        return item

    def close(self):
        pass


class _FakeRaw:
    """Just enough of a urllib3-response shape for `Response.iter_content`
    to pull chunks from lazily.
    """

    def __init__(self, data, chunk_size=16):
        self._data = data
        self._chunk_size = chunk_size

    def stream(self, chunk_size, decode_content=True):
        size = chunk_size or self._chunk_size
        for i in range(0, len(self._data), size):
            yield self._data[i : i + size]


class _RaisingRaw:
    """Yields a first chunk, then raises `exc` — simulates a connection drop
    or a `KeyboardInterrupt` partway through a transfer.
    """

    def __init__(self, first_chunk: bytes, exc: BaseException):
        self._first_chunk = first_chunk
        self._exc = exc

    def stream(self, chunk_size, decode_content=True):
        yield self._first_chunk
        raise self._exc


def make_response(status_code, json_body=None, content=b"", headers=None, reason="OK", raw=None):
    resp = requests.Response()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers.update(headers or {})
    if json_body is not None:
        content = json.dumps(json_body).encode()
        resp.headers.setdefault("Content-Type", "application/json")
    if raw is not None:
        resp.raw = raw
    else:
        resp._content = content
        resp._content_consumed = True
    resp.url = "https://fake-host/whatever"
    return resp


def make_session(script):
    adapter = FakeAdapter(script)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, adapter


def api_key_client(script, **kwargs):
    session, adapter = make_session(script)
    client = Client(
        "https://host", Credential.from_api_key("sk-test-key"), session=session, **kwargs
    )
    return client, adapter


def _window(start: str, end: str) -> Window:
    return Window(start=parse_instant(start), end=parse_instant(end), index=1, total=1)


# --- Phase 1: the request -----------------------------------------------------


def test_params_set_exactly_as_specified(tmp_path):
    client, adapter = api_key_client([make_response(200, content=pcap_body(b"pcap-bytes"))])
    window = _window("1000", "1010")

    extract_window(client, window, "2-4", "tcp and port 443", tmp_path, "ctid-1")

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["ctid"] == ["ctid-1"]
    assert query["start_time"] == ["1000"]
    # Q22: end_time is inclusive on the wire, so wire_params() sends end - 1ns
    # (see windows.py's Q22_END_TIME_INCLUSIVE), not the logical window end.
    assert query["end_time"] == ["1009.999999999"]
    assert query["stream_ids"] == ["2-4"]
    assert query["filter"] == ["tcp and port 443"]
    assert adapter.requests[0].method == "GET"
    assert urlsplit(adapter.requests[0].url).path == "/axapi/pcap"


def test_filter_with_spaces_parens_quotes_survives_encoding(tmp_path):
    tricky_filter = 'tcp and port 443 (test) "quoted value"'
    client, adapter = api_key_client([make_response(200, content=pcap_body(b"pcap-bytes"))])
    window = _window("1000", "1010")

    extract_window(client, window, None, tricky_filter, tmp_path, "ctid-1")

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["filter"][0] == tricky_filter


@pytest.mark.parametrize("streams, filter_", [(None, None), ("", "")])
def test_absent_filter_and_streams_omit_keys(tmp_path, streams, filter_):
    client, adapter = api_key_client([make_response(200, content=pcap_body(b"pcap-bytes"))])
    window = _window("1000", "1010")

    extract_window(client, window, streams, filter_, tmp_path, "ctid-1")

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert "stream_ids" not in query
    assert "filter" not in query
    assert set(query) == {"ctid", "start_time", "end_time"}


def test_x_task_guid_captured(tmp_path):
    client, _adapter = api_key_client(
        [make_response(200, content=pcap_body(b"pcap-bytes"), headers={"X-Task-GUID": "guid-1234"})]
    )
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Written)
    assert outcome.task_guid == "guid-1234"


def test_verbatim_stream_spec_passthrough_never_expanded(tmp_path):
    client, adapter = api_key_client([make_response(200, content=pcap_body(b"pcap-bytes"))])
    window = _window("1000", "1010")

    extract_window(client, window, "2-4", None, tmp_path, "ctid-1")

    query = parse_qs(urlsplit(adapter.requests[0].url).query)
    assert query["stream_ids"] == ["2-4"]
    assert query["stream_ids"] != ["2,3,4"]


# --- Phase 2: streaming to disk, atomically -----------------------------------


def test_completed_extract_lands_at_final_name_and_part_is_gone(tmp_path):
    body = pcap_body(b"pcap-bytes" * 1000)
    client, _adapter = api_key_client([make_response(200, raw=_FakeRaw(body, chunk_size=16))])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    final_path = tmp_path / "1000.pcap"
    part_path = tmp_path / "1000.pcap.part"
    assert isinstance(outcome, Written)
    assert outcome.path == final_path
    assert final_path.read_bytes() == body
    assert not part_path.exists()


def test_mid_stream_exception_leaves_no_file_at_final_name(tmp_path):
    client, _adapter = api_key_client(
        [make_response(200, raw=_RaisingRaw(b"partial-bytes", RuntimeError("connection dropped")))]
    )
    window = _window("1000", "1010")

    with pytest.raises(RuntimeError, match="connection dropped"):
        extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert not (tmp_path / "1000.pcap").exists()
    assert not (tmp_path / "1000.pcap.part").exists()


def test_written_bytes_equal_served_bytes(tmp_path):
    body = pcap_body(b"x" * 12345)
    client, _adapter = api_key_client([make_response(200, raw=_FakeRaw(body, chunk_size=4096))])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Written)
    assert outcome.bytes == len(body)
    assert (tmp_path / "1000.pcap").stat().st_size == len(body)


def test_preexisting_part_file_is_overwritten_not_resumed(tmp_path):
    (tmp_path / "1000.pcap.part").write_bytes(b"stale-garbage-from-a-prior-crash")
    body = pcap_body(b"the-real-served-bytes")
    client, _adapter = api_key_client([make_response(200, raw=_FakeRaw(body, chunk_size=8))])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Written)
    assert (tmp_path / "1000.pcap").read_bytes() == body


def test_filename_equals_start_time_string_exactly(tmp_path):
    start_str = "1728484411.293105240"
    client, _adapter = api_key_client([make_response(200, content=pcap_body(b"pcap-bytes"))])
    window = _window(start_str, "1728484412")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Written)
    assert outcome.path.name == f"{start_str}.pcap"
    assert (tmp_path / f"{start_str}.pcap").exists()


# --- Phase 3: outcomes ---------------------------------------------------------


def test_200_with_body_is_written(tmp_path):
    client, _adapter = api_key_client(
        [make_response(200, content=pcap_body(b"pcap-bytes"), headers={"X-Task-GUID": "guid-1"})]
    )
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert outcome == Written(
        path=tmp_path / "1000.pcap", bytes=len(pcap_body(b"pcap-bytes")), task_guid="guid-1"
    )


def test_418_returns_empty_and_writes_nothing(tmp_path):
    client, _adapter = api_key_client([make_response(418, {"message": "no data"})])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Empty)
    assert "no data" in outcome.reason
    assert list(tmp_path.iterdir()) == []


def test_zero_byte_200_is_empty_not_written(tmp_path):
    client, _adapter = api_key_client([make_response(200, content=b"")])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Empty)
    assert not (tmp_path / "1000.pcap").exists()
    assert not (tmp_path / "1000.pcap.part").exists()


# --- Phase 3b: the appliance's `200`-with-error-body defect (measured 2026-08-07) -------------
#
# `GET /axapi/pcap` can answer `200 OK` with the literal error text
# `b"Error starting listen thread, shutting down thread"` (repeated to fill the body) instead of a
# pcap — no other signal distinguishes it from a real capture. `PCAP_MAGICS`/`leading_magic` are
# the only defense; these tests are the regression coverage for that defense itself, distinct from
# every other test in this module (which now merely uses `pcap_body()` to get past the check).


def test_200_error_body_is_failed_not_written_or_empty(tmp_path):
    error_text = b"Error starting listen thread, shutting down thread" * 100
    client, _adapter = api_key_client([make_response(200, content=error_text)])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Failed)
    assert outcome.status == 200


def test_200_error_body_leaves_no_file_at_final_path(tmp_path):
    error_text = b"Error starting listen thread, shutting down thread" * 100
    client, _adapter = api_key_client([make_response(200, content=error_text)])
    window = _window("1000", "1010")

    extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert not (tmp_path / "1000.pcap").exists()


def test_200_error_body_cleans_up_the_part_file(tmp_path):
    error_text = b"Error starting listen thread, shutting down thread" * 100
    client, _adapter = api_key_client([make_response(200, content=error_text)])
    window = _window("1000", "1010")

    extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert not (tmp_path / "1000.pcap.part").exists()


def test_200_error_body_detail_names_leading_bytes_not_host_or_url(tmp_path):
    """LES-039: `Failed.detail` may name the leading bytes it rejected, but must never leak the
    appliance's hostname or the request URL — `make_response` sets `resp.url` to
    `https://fake-host/whatever`, which must not appear in the detail string.
    """
    error_text = b"Error starting listen thread, shutting down thread" * 100
    client, _adapter = api_key_client([make_response(200, content=error_text)])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Failed)
    assert error_text[:4].hex() in outcome.detail  # names the leading bytes...
    assert "fake-host" not in outcome.detail  # ...but never a URL or hostname
    assert "https://" not in outcome.detail


def test_200_pcapng_body_is_accepted_not_just_classic_pcap(tmp_path):
    """The whitelist must not be classic-pcap-only — pcapng is a legitimate capture container
    too (`PCAP_MAGICS`).
    """
    body = b"\x0a\x0d\x0d\x0a" + b"rest-of-a-pcapng-file"
    client, _adapter = api_key_client([make_response(200, content=body)])
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Written)
    assert (tmp_path / "1000.pcap").read_bytes() == body


def test_500_is_failed_with_diagnosable_detail(tmp_path):
    client, _adapter = api_key_client(
        [make_response(500, {"message": "an error occurred while extracting"})],
        max_retries=0,
    )
    window = _window("1000", "1010")

    outcome = extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert isinstance(outcome, Failed)
    assert outcome.status == 500
    assert "an error occurred while extracting" in outcome.detail


def test_401_propagates_rather_than_becoming_failed(tmp_path):
    client, _adapter = api_key_client([make_response(401, {"message": "bad key"})])
    window = _window("1000", "1010")

    with pytest.raises(UnauthorizedError):
        extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert list(tmp_path.iterdir()) == []


# --- Phase 4: interrupt handling ----------------------------------------------


def test_interrupt_mid_stream_aborts_task_and_cleans_up_part(tmp_path):
    client, adapter = api_key_client(
        [
            make_response(
                200,
                raw=_RaisingRaw(b"partial-bytes", KeyboardInterrupt()),
                headers={"X-Task-GUID": "guid-9"},
            ),
            make_response(200, {"guid": "ctid-1"}),  # DELETE /axapi/task/<ctid> response
        ]
    )
    window = _window("1000", "1010")

    with pytest.raises(KeyboardInterrupt):
        extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert len(adapter.requests) == 2
    assert adapter.requests[1].method == "DELETE"
    assert urlsplit(adapter.requests[1].url).path == "/axapi/task/ctid-1"
    assert not (tmp_path / "1000.pcap").exists()
    assert not (tmp_path / "1000.pcap.part").exists()


def test_interrupt_before_response_still_aborts_and_reraises(tmp_path):
    client, adapter = api_key_client(
        [KeyboardInterrupt(), make_response(200, {"guid": "ctid-1"})]
    )
    window = _window("1000", "1010")

    with pytest.raises(KeyboardInterrupt):
        extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert len(adapter.requests) == 2
    assert adapter.requests[1].method == "DELETE"
    assert urlsplit(adapter.requests[1].url).path == "/axapi/task/ctid-1"
    assert not (tmp_path / "1000.pcap.part").exists()


def test_extract_does_not_redeclare_the_chunk_size_constant():
    """`extract.py` used to carry its own private `_CHUNK_SIZE = 65536`
    constant, mirroring (and able to silently drift from)
    `client.iter_stream_body`'s default chunk size. Now there is one shared,
    exported constant.
    """
    assert not hasattr(extract, "_CHUNK_SIZE")
    assert extract.DEFAULT_CHUNK_SIZE is DEFAULT_CHUNK_SIZE


def test_abort_failure_does_not_mask_the_original_interrupt(tmp_path):
    client, adapter = api_key_client(
        [
            make_response(200, raw=_RaisingRaw(b"partial-bytes", KeyboardInterrupt())),
            make_response(404, {"message": "task already gone"}),  # abort call itself fails
        ]
    )
    window = _window("1000", "1010")

    with pytest.raises(KeyboardInterrupt):
        extract_window(client, window, None, None, tmp_path, "ctid-1")

    assert len(adapter.requests) == 2
    assert not (tmp_path / "1000.pcap.part").exists()
