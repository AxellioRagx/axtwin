"""extract — one window, one file, exactly once.

`extract_window()` issues `GET /axapi/pcap` (`docs/AXAPI_CONTRACT.md` §6.1)
for a single `windows.Window`, streams the response body to
`<out>/<startTs>.pcap`, and returns a typed `Outcome`. It never loops, never
resumes, and never touches a manifest — that is the batch runner's job
(`docs/WORK_ORDER_extract_window.md` §1, "Not a goal"; WO-6 builds the loop
on top of this function once it is correct).

Scope, deliberately narrow:
  * No `requests` import. `client.py` is the only module that talks to the
    network; this module drives it through `Client.request()` and
    `iter_stream_body()`, same as every other caller.
  * No inspection of the pcap bytes beyond a count. `xtract` does not parse
    capture contents.
  * `418` (no data for this window) is not an error — see `LES-035`. A
    zero-byte `200` is likewise not a successful capture: both surface as
    `Empty`, never `Written`, so neither can satisfy a later resume check
    that only looks for the final filename.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from aximprint.acquire.client import DEFAULT_CHUNK_SIZE, Client, iter_stream_body
from aximprint.acquire.errors import (
    AuthenticationError,
    NoDataError,
    UnauthorizedError,
    XtractAPIError,
)
from aximprint.acquire.windows import Window

__all__ = ["PCAP_MAGICS", "Empty", "Failed", "Outcome", "Written", "extract_window", "leading_magic"]

# The leading 4 bytes of every capture container this appliance can legitimately serve:
# classic pcap in both endiannesses, each in microsecond and nanosecond resolution, plus pcapng.
#
# Checked because a `200 OK` status is NOT sufficient evidence that the body is a capture — see
# the call site in `extract_window`. This is deliberately a whitelist: an unrecognized magic is
# treated as a failure rather than passed through, since the alternative is writing an error
# message to disk under a `.pcap` name and discovering it only when a downstream parser chokes.
PCAP_MAGICS: frozenset[bytes] = frozenset(
    {
        b"\xd4\xc3\xb2\xa1",  # classic, little-endian, microsecond
        b"\xa1\xb2\xc3\xd4",  # classic, big-endian, microsecond
        b"\x4d\x3c\xb2\xa1",  # classic, little-endian, nanosecond
        b"\xa1\xb2\x3c\x4d",  # classic, big-endian, nanosecond
        b"\x0a\x0d\x0d\x0a",  # pcapng (any endianness; byte-order magic follows)
    }
)


def leading_magic(path: Path) -> bytes:
    """The file's first 4 bytes, or `b""` if it is shorter than that/unreadable.

    Public (not `_`-prefixed): `docs/WORK_ORDER_harvest_from_pcaps.md` build step 4 reuses this,
    together with `PCAP_MAGICS`, to validate `--from-pcaps`-supplied files before handing them to
    Zeek — the scope boundary's "the one permitted inspection" (a 4-byte magic check, never full
    pcap parsing) applies there exactly as it does here.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(4)
    except OSError:
        return b""


@dataclass(frozen=True)
class Written:
    """`GET /axapi/pcap` returned `200` with a non-empty body, now safely
    renamed into place at `path`.

    `bytes` is the exact byte count served (not the appliance's own
    `bytes_sent` accounting, which this module never reads — see §3 "no
    inspection beyond a byte count"). `task_guid` is the `X-Task-GUID`
    response header, or `None` if the appliance omitted it.
    """

    path: Path
    bytes: int
    task_guid: str | None


@dataclass(frozen=True)
class Empty:
    """No pcap file was written for this window — either the appliance
    answered `418` ("no data for this window") or answered `200` with a
    zero-byte body, which is not a valid capture file either. `reason`
    describes which.
    """

    reason: str


@dataclass(frozen=True)
class Failed:
    """Every other outcome: a non-`200`/`418` status, or a transport failure
    that persisted past `client.py`'s own retry budget. `status` is the HTTP
    status code when one was received, `None` for a locally-synthesized
    failure (e.g. `ConnectionFailedError`). `detail` carries enough of the
    appliance's error message to diagnose without re-running the extract.
    """

    status: int | None
    detail: str


Outcome = Written | Empty | Failed


def extract_window(
    client: Client,
    window: Window,
    streams: str | None,
    filter: str | None,  # matches the `axapi` wire param name (`docs/AXAPI_CONTRACT.md` §6.1)
    out_dir: str | os.PathLike,
    ctid: str,
) -> Outcome:
    """Perform exactly one extraction for `window` and return its `Outcome`.

    `streams` is the caller's verbatim `stream_ids` spec string (`LES-036`
    — never re-spelled, never expanded); `filter` is a BPF/community-ID/tuple
    string per `docs/AXAPI_CONTRACT.md` §9.2. Both are omitted from the
    request entirely when falsy (`None` or `""`) rather than sent as empty
    strings — the appliance's own `stream_ids` default (`"0-11"`) only
    applies when the parameter is absent.

    On `KeyboardInterrupt` during the request or the transfer: best-effort
    `DELETE /axapi/task/<ctid>` to abort the extract server-side, clean up
    any `.part` file, then re-raise. `401`, after `client.py` has already
    tried refresh and re-login, also re-raises — a run that reaches here
    cannot continue.
    """
    wire = window.wire_params()
    start_ts = wire["start_time"]

    params: dict[str, str] = {
        "ctid": ctid,
        "start_time": start_ts,
        "end_time": wire["end_time"],
    }
    if streams:
        params["stream_ids"] = streams
    if filter:
        params["filter"] = filter

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    final_path = out_path / f"{start_ts}.pcap"
    part_path = out_path / f"{start_ts}.pcap.part"

    try:
        resp = client.request(
            "GET", "/axapi/pcap", params=params, stream=True, expected_status=200
        )
    except NoDataError as exc:
        return Empty(reason=str(exc))
    except (UnauthorizedError, AuthenticationError):
        raise
    except XtractAPIError as exc:
        return Failed(status=exc.status_code, detail=str(exc))
    except KeyboardInterrupt:
        _abort_task(client, ctid)
        raise

    task_guid = resp.headers.get("X-Task-GUID")

    try:
        written = _stream_to_part(resp, part_path)
    except KeyboardInterrupt:
        _abort_task(client, ctid)
        _cleanup_part(part_path)
        raise
    except BaseException:
        _cleanup_part(part_path)
        raise

    if written == 0:
        # Checked *before* the magic check below: a genuinely empty body has no 4-byte magic to
        # find either, and this is the pre-existing "no capture, no error" outcome (LES-035's
        # sibling) — not the appliance's error-body case, which is never zero-length (measured:
        # the error text repeats to fill the response). Magic-checking an empty body would
        # misclassify every zero-byte response as Failed.
        _cleanup_part(part_path)
        return Empty(reason="200 response with a zero-byte body")

    magic = leading_magic(part_path)
    if magic not in PCAP_MAGICS:
        # The appliance answers `200 OK` with an error *body* instead of a pcap when it cannot
        # serve the extract — measured 2026-08-07: a concurrent second request returns
        # `b"Error starting listen thread, shutting down thread"` repeated to fill a 57 MB
        # response, with a 200 status and no other signal. Nothing at the HTTP layer can detect
        # that, so the only defense is checking that the bytes are what they claim to be.
        #
        # This is `Failed`, not `Empty`: the appliance had an error, it did not report "no data
        # for this window". Conflating them would let a broken extract be recorded as a quiet
        # window and never retried.
        _cleanup_part(part_path)
        return Failed(
            status=200,
            detail=(
                f"200 response body is not a pcap (leading bytes {magic.hex()}, "
                f"{written} bytes) — the appliance returned an error body with a success status"
            ),
        )

    os.rename(part_path, final_path)
    return Written(path=final_path, bytes=written, task_guid=task_guid)


def _stream_to_part(resp, part_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
    """Stream `resp`'s body to `part_path` in chunks, `fsync` before
    returning so the caller's `os.rename()` is never ordered ahead of the
    data actually hitting disk. Opens in `"wb"` — a stale `.part` from a
    prior crash is truncated and overwritten, never resumed (not a
    documented capability of this endpoint).
    """
    written = 0
    with open(part_path, "wb") as fh:
        for chunk in iter_stream_body(resp, chunk_size=chunk_size):
            if not chunk:
                continue
            fh.write(chunk)
            written += len(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    return written


def _cleanup_part(part_path: Path) -> None:
    part_path.unlink(missing_ok=True)


def _abort_task(client: Client, ctid: str) -> None:
    """Best-effort `DELETE /axapi/task/<ctid>`. Fire-and-forget, same
    discipline as `Client.logout()`: an abort failure must never mask the
    `KeyboardInterrupt` that triggered it.
    """
    try:
        client.request("DELETE", f"/axapi/task/{ctid}", expected_status=200)
    except XtractAPIError:
        pass
