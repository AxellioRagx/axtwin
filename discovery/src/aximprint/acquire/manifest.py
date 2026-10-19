"""manifest — the load-bearing record of what happened to every extraction
window (`docs/WORK_ORDER_runner_pcap.md` WO-6 Phase 2).

Because a `<startTs>.pcap` filename carries only the window's start time, the
manifest is the **only** record that a `418` ("no data") window was ever
attempted. Without it, every empty window on a sparse capture would be
re-requested on every resume and the run would never converge. This module
owns only the file format — `runner.py` decides what a record *means* for
resume/collision purposes.

`<out>/manifest.jsonl`: one JSON object per line.

  * A **header** record (`"kind": "header"`), written once, the first time a
    fresh manifest is created for a given `--out`. Carries the run
    parameters (`streams`, `filter`, `interval`, `tool_version`) that
    `runner.py`'s collision guard compares future invocations against. Never
    rewritten once written — the header is the identity of what is already
    on disk, not a running log of every invocation that has touched it.
  * One **window** record (`"kind": "window"`) per attempted window:
    start/end wire strings, outcome, bytes, `X-Task-GUID`, duration, attempt
    count, and a timestamp. Appended (never rewritten) and `fsync`ed before
    the write returns, so a `kill -9` loses at most the one record in
    flight.

Never contains credentials, hostnames, or capture bytes — nothing accepted
here has a slot for any of them.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from aximprint.acquire.errors import XtractError

__all__ = [
    "MANIFEST_FILENAME",
    "Manifest",
    "ManifestError",
    "ManifestHeader",
    "WindowRecord",
    "append_window_record",
    "manifest_path",
    "read_manifest",
    "write_header",
]


class ManifestError(XtractError, ValueError):
    """A manifest record could not be parsed into `ManifestHeader`/`WindowRecord`."""


MANIFEST_FILENAME = "manifest.jsonl"

_KIND_HEADER = "header"
_KIND_WINDOW = "window"


@dataclass(frozen=True)
class ManifestHeader:
    """The run parameters a manifest was created under (work order §2 Phase 2).

    `filter` is `None` when the run had none — never the empty string, so a
    later comparison never has to treat `None` and `""` as different things.
    `interval_ns` is a plain `int`, not a `Duration`, so this dataclass has
    no dependency on `timeutil`/`windows` and round-trips through `json`
    without a custom encoder.
    """

    streams: str
    filter: str | None
    interval_ns: int
    tool_version: str


@dataclass(frozen=True)
class WindowRecord:
    """One attempted window (work order §2 Phase 2: "start/end strings,
    outcome, bytes, X-Task-GUID, duration, attempt count, timestamp").

    `start` is the exact wire string from `Window.wire_params()['start_time']`
    (it doubles as the resume key and the output filename — never reformatted
    here). `end` is the window's **logical** end (`format_instant(window.end)`),
    deliberately *not* `Window.wire_params()['end_time']` — the wire encoding
    applies the Q22 inclusive-end transport adjustment (end - 1ns), and
    recording that instead would couple the resume key to a transport quirk:
    flipping `Q22_END_TIME_INCLUSIVE` would then make every pre-existing
    manifest record look "clipped" against a freshly-planned window and force
    a full re-extract for a reason that has nothing to do with the requested
    range actually changing (`runner.py`'s `plan_resume`/`execute`).
    `outcome` is one of `"written"`/`"empty"`/`"failed"`.
    `detail` carries a short diagnosable reason for `"empty"`/`"failed"`
    outcomes (an `extract.Empty.reason` or `extract.Failed.detail`); `None`
    for `"written"`. `timestamp` is an ISO-8601 UTC string recorded when the
    record was written, not when the window's time range occurred.
    """

    start: str
    end: str
    outcome: str
    bytes: int
    task_guid: str | None
    duration_s: float
    attempt: int
    timestamp: str
    detail: str | None = None


@dataclass(frozen=True)
class Manifest:
    """The result of reading a manifest file: the header (`None` if the
    manifest doesn't exist yet or its first line hasn't landed) and every
    window record parsed from it, in file order (oldest first).
    """

    header: ManifestHeader | None
    records: list[WindowRecord]


def manifest_path(out_dir: os.PathLike | str) -> Path:
    return Path(out_dir) / MANIFEST_FILENAME


def _append_line(path: Path, obj: dict) -> None:
    """Append one JSON line and `fsync` before returning — the "loses at
    most one record" guarantee depends on this actually hitting disk before
    the next window starts, not just landing in a buffered file object.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True)
    with open(path, "a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_header(out_dir: os.PathLike | str, header: ManifestHeader) -> None:
    """Append `header` as the manifest's header record.

    Callers (`runner.py`'s collision guard) are responsible for only calling
    this once, for a manifest that doesn't already have a header — this
    function itself doesn't check, so it stays a pure "append one record"
    primitive like `append_window_record`.
    """
    _append_line(manifest_path(out_dir), {"kind": _KIND_HEADER, **asdict(header)})


def append_window_record(out_dir: os.PathLike | str, record: WindowRecord) -> None:
    """Append one window outcome record, flushed to disk before returning."""
    _append_line(manifest_path(out_dir), {"kind": _KIND_WINDOW, **asdict(record)})


def read_manifest(out_dir: os.PathLike | str) -> Manifest:
    """Read every record from `<out_dir>/manifest.jsonl`.

    A missing file reads as `Manifest(header=None, records=[])` — a fresh
    `--out` directory is not an error. A line that fails to parse as JSON
    (a truncated final line from a hard `kill -9` mid-write, per the work
    order's resume acceptance test) is silently skipped rather than raising
    — every *complete* line before it is still trustworthy, and the
    incomplete line's window (if any) simply gets re-attempted on resume,
    which is always safe (`extract.py` never resumes a partial `.part`
    either). Only the *first* `"kind": "header"` line found becomes
    `Manifest.header` — later header-shaped lines (there should never be
    one; `write_header` is called at most once per manifest) do not silently
    reassign the run's recorded identity.
    """
    path = manifest_path(out_dir)
    if not path.exists():
        return Manifest(header=None, records=[])

    header: ManifestHeader | None = None
    records: list[WindowRecord] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.pop("kind", None)
            try:
                if kind == _KIND_HEADER:
                    if header is None:
                        header = ManifestHeader(**obj)
                elif kind == _KIND_WINDOW:
                    records.append(WindowRecord(**obj))
            except TypeError:
                # A record shaped for a different manifest schema version —
                # tolerated the same as a truncated line, never fatal to a
                # resume.
                continue
    return Manifest(header=header, records=records)
