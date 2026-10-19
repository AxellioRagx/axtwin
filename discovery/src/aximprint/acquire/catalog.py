"""catalog — assembling "what have we got?" from `axapi`'s filesystem endpoints.

**This delivers the user's first task** (`docs/WORK_ORDER_catalog_streams.md`
WO-3): `xtract streams` needs, per stream, the first and last timestamp of
stored capture data, how many files back that range, how many bytes, and
where the gaps are. No endpoint returns any of that directly — it is
assembled here, from `GET /axapi/fs/streams` (the stream list) and
`GET /axapi/fs/files?stream=<s>` (the file list per stream), and this is the
**only** place that assembly happens.

Wire traps this module exists to isolate, from `docs/AXAPI_CONTRACT.md` §5 and
the 2026-08-05 live probe against CorpAPCN (PacketXpress 5.1.0):

  * The live appliance returns a **bare JSON list** from both
    `GET /axapi/fs/streams` and `GET /axapi/fs/files` — not the
    `{"stream"/"files": [...]}` wrapper both vendor PDFs document. This module
    accepts either: a bare list first, and the documented wrapper as a
    fallback (a firmware update could restore it), type-checked
    (`isinstance(..., list)`) rather than branched on key presence either way.
  * `time_start`/`time_end` conversion goes through
    `timeutil.instant_from_fs_field` exclusively (Q19, resolved: whole-second
    epoch **strings**) — never inlined here.
  * A disk-usage footer line ("X TB of Y TB available, Z% utilized") is
    returned as a fake, field-shifted record mixed into both endpoints' real
    entries — filtered out here (`_is_footer_junk_stream_entry`,
    `_is_footer_junk_file_entry`) before parsing, never surfaced as a
    `StreamListing`/`FileEntry`, and never the cause of a raised
    `CatalogError` (a genuinely malformed *real* record still raises).

Q21 (does `fs/streams`' `name` map to the decimal `stream_ids` integer?) is
**resolved**: `name` is `streamNN` (e.g. `stream08`), not a bare integer — see
`stream_id_for_name` below, still the **only** function that knows this
mapping.

Scope, deliberately narrow:
  * No extraction, no `/axapi/pcap`, no caching to disk (work order §3).
  * `client.py` is the only module that imports `requests`; this module only
    calls `client.request(...)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aximprint.acquire.errors import XtractError
from aximprint.acquire.timeutil import (
    NS_PER_SECOND,
    Duration,
    Instant,
    instant_from_fs_field,
)

__all__ = [
    "CatalogError",
    "FileEntry",
    "GapReport",
    "StreamIdMappingError",
    "StreamListing",
    "StreamRange",
    "list_streams",
    "stream_id_for_name",
    "stream_range",
]


class CatalogError(XtractError, ValueError):
    """A response from `fs/streams`/`fs/files` didn't match the documented
    shape (`docs/AXAPI_CONTRACT.md` §5): wrong JSON type for a wrapper key, a
    missing required field, or an unparseable time field. Raised rather than
    silently skewing a range or dropping a file from the count.

    Also a `ValueError` (unchanged, for any existing `except ValueError:` call
    site) — which is a known, accepted risk: an ordinary `except ValueError:`
    elsewhere in the codebase could silently swallow a wire-contract
    violation raised here. `except XtractError` is the way to catch every
    `xtract`-raised error deliberately without also catching every unrelated
    `ValueError`.
    """


class StreamIdMappingError(CatalogError):
    """`stream_id_for_name` couldn't map a `fs/streams` `name` to a decimal
    stream id under the resolved (Q21) `streamNN` naming convention (or the
    tolerated bare-decimal fallback). See that function's docstring.
    """


@dataclass(frozen=True)
class StreamListing:
    """One entry from `GET /axapi/fs/streams` (§5.1) — a stream directory.

    `permissions` is typed loosely (`int | str`) rather than pinned to one:
    the documented wrapper response types it `integer`, but the live bare
    list returns it as a string (e.g. `"0444"`) — passed through unconverted
    either way, since `xtract` never does permission arithmetic on it.
    """

    name: str
    permissions: int | str
    user: str
    group: str
    size: int
    date: str
    type: str


@dataclass(frozen=True)
class FileEntry:
    """One entry from `GET /axapi/fs/files?stream=<s>` (§5.3) — a captured file."""

    name: str
    size: int
    time_start: Instant
    time_end: Instant
    epoch: int
    seqnum: int


@dataclass(frozen=True)
class GapReport:
    """Storage-gap summary over a stream's sorted file list.

    `count == 0` implies `total_duration == Duration(0)` and
    `largest is None` — there is no "zero-length gap"; the absence of a gap
    is the absence of an entry, not a degenerate one (mirrors the "empty
    stream yields no range, not a zero-length one" rule for `StreamRange`).
    """

    count: int
    total_duration: Duration
    largest: Duration | None


@dataclass(frozen=True)
class StreamRange:
    """The assembled range for one stream — the sole output of this module
    that `xtract streams` prints.

    `first`/`last` are `None` for a stream with zero files: an empty stream
    yields **no** range, not a zero-length one starting and ending at the
    same instant. `file_count`/`total_bytes` are always well-defined (`0`
    for an empty stream).
    """

    stream: str
    first: Instant | None
    last: Instant | None
    file_count: int
    total_bytes: int
    gaps: GapReport


# A gap is a positive discontinuity between one file's `time_end` and the next
# file's `time_start` in sorted order — zero-width abutment is not a gap.
# Gaps at or below this threshold are not reported. `fs/files` timestamps are epoch
# *seconds* (Q19, resolved by the 2026-08-05 live probe), so an hourly file
# ending at T and its successor starting at T+1 reads as a one-second gap
# purely from second-resolution rounding — not an outage. Measured on
# CorpAPCN stream00: 125 reported gaps totalling 4876s, of which the single
# largest was 4752s and the remaining 124 were exactly 1s each. A 2s floor
# suppresses the rounding artifacts and leaves the one real outage, which is
# what an operator needs to see. Callers can still pass `gap_threshold=0` for
# the raw view.
DEFAULT_GAP_THRESHOLD = Duration(2 * NS_PER_SECOND)


def _list_payload(body: object, *, wrapper_key: str, endpoint: str) -> list:
    """Extract the list of records from a `fs/streams`/`fs/files` response
    body, accepting either shape:

      * a **bare JSON list** — what the live appliance actually returns
        (measured 2026-08-05 against CorpAPCN, PacketXpress 5.1.0), or
      * the documented `{"<wrapper_key>": [...]}` wrapper — tolerated as a
        fallback in case a firmware update restores it; still type-checked
        (`isinstance(data, list)`), never branched on key presence alone, so
        this can't be fooled by §5.2's single-item response reusing the same
        wrapper key for a JSON *object*.
    """
    if isinstance(body, list):
        return body
    data = body.get(wrapper_key) if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise CatalogError(
            f"{endpoint}: expected a bare JSON list, or a {{{wrapper_key!r}: [...]}} wrapper, got "
            f"{type(body).__name__}: {body!r}"
        )
    return data


def _is_footer_junk_stream_entry(item: dict) -> bool:
    """`GET /axapi/fs/streams` mixes a disk-usage footer line ("X TB of Y TB
    available, Z% utilized") into the real directory list as a fake,
    field-shifted entry (measured 2026-08-05: `{"name": "TB", "is_directory":
    false, "type": "file", ...}`). Every real stream directory is
    `is_directory: true` / `type: "directory"`; this is the general test
    (not a hardcoded `name == "TB"` check), so any other junk row shaped the
    same way — not just this exact one — is caught too. A `dict` missing
    `is_directory` entirely (the documented, wrapper-shaped response, which
    never carried this field) is never treated as junk by this check alone.
    """
    return item.get("is_directory") is False or item.get("type") == "file"


def _is_all_digit_str(v: object) -> bool:
    return isinstance(v, str) and v.isdigit()


def _is_footer_junk_file_entry(item: dict) -> bool:
    """The same disk-usage footer line surfaces in `GET /axapi/fs/files` too,
    as a fake file record (measured 2026-08-05):
    `{"name": "TB", "time_start": "available,", "time_end": "98.00%", ...}`
    — no `seqnum` field at all, and non-numeric `time_start`/`time_end`.

    Both signals together, not either alone: every *real* file entry carries
    `seqnum`, so a record missing it is never a genuine capture file; and
    requiring the non-numeric time fields too keeps this from swallowing a
    genuinely malformed real record (which must still raise `CatalogError`
    via `instant_from_fs_field`, not be silently dropped — see
    `CatalogError`'s own docstring).
    """
    if "seqnum" in item:
        return False
    return not (_is_all_digit_str(item.get("time_start")) and _is_all_digit_str(item.get("time_end")))


def list_streams(client) -> list[StreamListing]:
    """`GET /axapi/fs/streams` (§5.1) — every stream directory on the appliance.

    Accepts a bare JSON list (the live shape) or the documented
    `{"stream": [...]}` wrapper (`_list_payload`). The disk-usage footer's
    fake `"TB"` entry is filtered out before parsing
    (`_is_footer_junk_stream_entry`) — it never becomes a `StreamListing` and
    never raises, since it isn't a wire-contract violation, just noise the
    appliance appends.
    """
    resp = client.request("GET", "/axapi/fs/streams", expected_status=200)
    body = resp.json()
    data = _list_payload(body, wrapper_key="stream", endpoint="GET /axapi/fs/streams")
    listings = []
    for item in data:
        if isinstance(item, dict) and _is_footer_junk_stream_entry(item):
            continue
        listings.append(_parse_stream_listing(item))
    return listings


def _parse_stream_listing(item: object) -> StreamListing:
    if not isinstance(item, dict):
        raise CatalogError(f"stream listing entry must be an object, got {type(item).__name__}: {item!r}")
    try:
        return StreamListing(
            name=item["name"],
            permissions=item["permissions"],
            user=item["user"],
            group=item["group"],
            size=item["size"],
            date=item["date"],
            type=item["type"],
        )
    except KeyError as exc:
        raise CatalogError(f"stream listing entry missing required field {exc}: {item!r}") from exc


def stream_range(client, stream: int | str, *, gap_threshold: Duration = DEFAULT_GAP_THRESHOLD) -> StreamRange:
    """Assemble one stream's data range from `GET /axapi/fs/files?stream=<s>`
    (§5.3) — the **only** place `first`/`last`/gap detection are computed.

    `first = min(time_start)`, `last = max(time_end)` over every file
    returned; both are `None` (not a zero-length range) when the stream has
    no files. Files are sorted by `time_start` before gap detection so an
    out-of-order response from the appliance doesn't produce spurious gaps.
    Accepts a bare JSON list (the live shape) or the documented
    `{"files": [...]}` wrapper (`_list_payload`). The disk-usage footer's
    fake entry is filtered out before parsing (`_is_footer_junk_file_entry`),
    the same crash that killed an earlier live run (`int(time_start)` on
    `"available,"`).
    """
    resp = client.request("GET", "/axapi/fs/files", params={"stream": str(stream)}, expected_status=200)
    body = resp.json()
    data = _list_payload(body, wrapper_key="files", endpoint=f"GET /axapi/fs/files?stream={stream}")
    files = [
        _parse_file_entry(item, stream=stream)
        for item in data
        if not (isinstance(item, dict) and _is_footer_junk_file_entry(item))
    ]

    if not files:
        return StreamRange(
            stream=str(stream),
            first=None,
            last=None,
            file_count=0,
            total_bytes=0,
            gaps=GapReport(count=0, total_duration=Duration(0), largest=None),
        )

    files_sorted = sorted(files, key=lambda f: f.time_start)
    first = min(f.time_start for f in files_sorted)
    last = max(f.time_end for f in files_sorted)
    total_bytes = sum(f.size for f in files_sorted)
    gaps = _detect_gaps(files_sorted, threshold=gap_threshold)
    return StreamRange(
        stream=str(stream),
        first=first,
        last=last,
        file_count=len(files_sorted),
        total_bytes=total_bytes,
        gaps=gaps,
    )


def _parse_file_entry(item: object, *, stream: int | str) -> FileEntry:
    if not isinstance(item, dict):
        raise CatalogError(
            f"file entry for stream {stream!r} must be an object, got {type(item).__name__}: {item!r}"
        )
    try:
        name = item["name"]
        size = item["size"]
        time_start_raw = item["time_start"]
        time_end_raw = item["time_end"]
        epoch = item["epoch"]
        seqnum = item["seqnum"]
    except KeyError as exc:
        raise CatalogError(
            f"file entry for stream {stream!r} missing required field {exc}: {item!r}"
        ) from exc
    # instant_from_fs_field (Q19) raises on a non-numeric-string or
    # implausible value rather than letting a malformed field silently skew
    # the range.
    time_start = instant_from_fs_field(time_start_raw)
    time_end = instant_from_fs_field(time_end_raw)
    return FileEntry(name=name, size=size, time_start=time_start, time_end=time_end, epoch=epoch, seqnum=seqnum)


def _detect_gaps(files_sorted: list[FileEntry], *, threshold: Duration) -> GapReport:
    """Gap detection over a **pre-sorted** (by `time_start`) file list.

    Consecutive-pair differencing (`curr.time_start - prev.time_end`) is only
    correct when no file nests inside or overlaps its predecessor — sorting
    by `time_start` alone does not guarantee that (a short file can start
    after, and end well before, a long file that started earlier). This
    tracks a running maximum of `time_end` seen so far instead, and measures
    each file's `time_start` against *that* — so a file nested inside an
    earlier, longer one contributes no phantom gap, and an overlap
    immediately followed by a real gap is still measured from the correct
    (running-max) end, not from whichever file happened to come immediately
    before it in sort order.
    """
    count = 0
    total_ns = 0
    largest_ns = 0
    max_end_ns: int | None = None
    for f in files_sorted:
        if max_end_ns is not None:
            gap_ns = f.time_start.ns - max_end_ns
            if gap_ns > threshold:
                count += 1
                total_ns += gap_ns
                largest_ns = max(largest_ns, gap_ns)
        max_end_ns = f.time_end.ns if max_end_ns is None else max(max_end_ns, f.time_end.ns)
    return GapReport(
        count=count,
        total_duration=Duration(total_ns),
        largest=Duration(largest_ns) if count else None,
    )


_STREAM_NAME_RE = re.compile(r"stream(\d+)", re.IGNORECASE)


def stream_id_for_name(name: str) -> int:
    """Map a `fs/streams` `name` (string) to the decimal integer `expand()`
    produces and `stream_ids` grammar takes.

    # Q21: RESOLVED 2026-08-05 by live probe, see docs/OPEN_QUESTIONS.md —
    against CorpAPCN (PacketXpress 5.1.0), `fs/streams`' `name` is
    `streamNN` (e.g. `stream08`), **not** a bare decimal integer as both
    vendor PDFs' silence had left ambiguous. `stream08` maps to `8`.
    Also tolerates a bare decimal name (`"2"` -> `2`) as a fallback, in case
    the documented wrapper response (§5.1) — which this module still
    accepts, see `_list_payload` — ever pairs with the originally-assumed
    bare-integer naming on some other firmware. A name matching neither form
    (e.g. `"TB"`, the disk-usage-footer junk entry's name, or an arbitrary
    label like `"primary-capture"`) raises `StreamIdMappingError` — a loud
    failure rather than a guess.
    """
    if isinstance(name, str):
        match = _STREAM_NAME_RE.fullmatch(name)
        if match is not None:
            return int(match.group(1))
    try:
        return int(name)
    except (TypeError, ValueError) as exc:
        raise StreamIdMappingError(
            f"stream name {name!r} does not match the resolved streamNN naming convention "
            "(Q21, docs/OPEN_QUESTIONS.md) or the tolerated bare-decimal fallback"
        ) from exc
