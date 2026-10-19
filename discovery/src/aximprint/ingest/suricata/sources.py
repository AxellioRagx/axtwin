"""Suricata eve.json transport — where records come from, not what they mean.

Mirrors `ingest/zeek/sources.py`'s split (that module's own docstring states the rule
this package follows too): a **source** locates a sensor's records and decodes them
into dict rows; the **plugin** (`plugin.py`) decides what those rows *mean*. Nothing
here knows about `event_type`, `_PARSERS`, or the event model.

Suricata puts every record type in ONE stream (`eve.json`) and discriminates on the
`event_type` field **inside** each JSON line — unlike Zeek, which splits by FILE NAME
(one stem per log, so a directory *listing* is already a census). There is therefore no
per-stem partitioning to do here at all: this module's whole job is finding the file(s)
that carry that one stream and handing back already-decoded JSON dict rows, in a
deterministic discovery order. `plugin.py`'s own docstring explains what that
file-vs-field difference costs the run-level inventory (`unparsed_stems`/
`unrecognized_stems`) — read it before assuming this module could supply a Zeek-shaped
`inventory`/`inventory_is_complete` pair; it structurally cannot, because listing files
here never reads a single `event_type`.

Two locator shapes, and nothing else — an unrecognized shape is refused, not guessed at
(CONVENTIONS §3, "fail loud, fail closed"):

  a bare FILE       read directly, whatever its name — the caller already knows what it
                    is pointing at, exactly like `ingest/zeek/sources.py::LogDirSource`'s
                    own single-log-file branch.
  a DIRECTORY       searched, non-recursively, for `eve.json` and Suricata's own rotation
                    siblings (`eve.json.<suffix>`, optionally `.gz` on either — see
                    `_EVE_NAME` below for the exact shape accepted). A directory holding
                    no such file is refused: guessing at some OTHER JSON file's shape is
                    exactly the mistake CONVENTIONS §3 exists to prevent (an unrelated
                    JSON log, or eve.json sitting one level deeper than this module looks,
                    must never be silently read as if it were the right file).

**Suricata's own rotation is a real disorder source, the identical shape Zeek's own
rotated logs already are** (`ingest/zeek/sources.py`'s module docstring, `LES-004`):
`rotate-interval` freezes the current file under a new name and starts a fresh one, but
a flow that was still open at the rotation instant is logged — at CLOSE, keyed on its
OWN start time — into whichever file happens to be current at that later moment, which
can be the file *after* the one its `timestamp` would sort into. So every discovered
file is its own independently reorder-buffered stream (`plugin.py` does the actual
buffering, on the parsed EVENT stream — see that module's docstring for why post-parse,
not pre-parse, matches `ingest/zeek/plugin.py`'s own choice), merged on each event's own
`ts` — never concatenated in file-name order (`LES-013`).

**The reorder machinery is NOT imported from `ingest/zeek/sources.py` any more.** It
was, and that import is now gone: `reorder_by_key` (with its stats protocol, the
`Event`-level `in_order` wrapper, and `is_ip`) was HOISTED to `ingest/reorder.py`,
below every plugin, in the same round this module's own defects were fixed (code review
`S1`/`S2`). The original judgment call — import a public, deliberately generalized
helper from a sibling plugin rather than write a second, silently-divergent bounded
reorder buffer (`Q2`) — was right about the alternative and wrong about the home: it
left `ingest.suricata` depending on `ingest.zeek` staying importable for a helper that
is about TIME, not about Zeek, and a third plugin with the same problem shape would
have inherited the same edge again. `ingest/reorder.py`'s own docstring records the
move; `ingest/zeek/sources.py` re-exports the function so nothing that already read it
from there had to change.

**What did not move, deliberately: the horizon and row-cap constants** (`LES-119`).
Zeek's 600 s horizon is defensible because a real Zeek `conn.log` was MEASURED to
displace rows by up to 169.6 s. That is a fact about one sensor's write behavior, and
this plugin inheriting it as an unmarked shared constant would be exactly the
"one plugin's evidence becomes everyone's assumption" failure — so this module declares
its own pair, with its own honest note about where the number does and does not come
from (`REORDER_HORIZON_SECONDS`, below).
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "REORDER_HORIZON_SECONDS", "REORDER_ROWS",
    "EveStream", "SuricataSource", "SuricataSourceError", "parse_suricata_timestamp",
]

#: How far back in time this plugin's reorder buffer can repair disorder, and the
#: row-count memory backstop for that buffer. Suricata's OWN pair, not Zeek's — the
#: value happens to match, the evidence behind it does not, and conflating the two is
#: what `LES-119` is about.
#:
#: **This number is NOT measured.** No real Suricata capture was available to this build
#: (`plugin.py`'s module docstring), so unlike Zeek's identical figure — backed by a
#: measured 169.6 s worst-case `conn.log` displacement — this is a declared default with
#: a named path back: measure the real displacement between `flow.start` (what
#: `plugin.py::_flow` keys a `FlowEvent` on, code review `M4`) and the position the
#: record actually appears at in `eve.json`, then set this from that measurement.
#:
#: The pathology's SHAPE is known even without the measurement, and it is worse here
#: than for Zeek in one specific way: `M4` keys a flow event on `flow.start` while
#: Suricata writes the record at flow CLOSE or TIMEOUT, so the displacement of a
#: long-lived flow is bounded by Suricata's own configurable `flow-timeouts` (its
#: documented default for an established TCP flow is on the order of an hour), not by
#: anything this buffer can promise. Displacement past this horizon is not silently
#: absorbed: it is counted and announced as `SensorHealthEvent(kind="ingest_unordered")`
#: (`ingest/reorder.py::in_order`), which is what makes an under-set horizon a visible
#: health fact rather than a corrupted window.
REORDER_HORIZON_SECONDS = 600.0
REORDER_ROWS = 8192

#: Suricata's own eve.json rotation shape (`rotate-interval` in `suricata.yaml`): the
#: LIVE file is always exactly `eve.json`; a frozen rotated sibling is
#: `eve.json.<suffix>` (Suricata itself appends a Unix-epoch timestamp on its own
#: internal rotation; an external `logrotate` more commonly appends a small integer) —
#: either way, `eve.json` followed by a `.`-separated suffix, optionally gzipped on top
#: by a `logrotate compress` stanza. A per-thread-named file (`eve-1.json`,
#: `eve-worker2.json` — the pre-5.0 shape from before eve output consolidated onto one
#: shared, lock-protected file) is a DIFFERENT, legacy layout this module does not claim
#: to read: matching it here would silently read one thread's traffic as if it were the
#: whole capture, exactly the "unrecognized shape passed through" CONVENTIONS §3
#: forbids. A directory holding only that shape is refused (`_discover`, below) rather
#: than guessed at.
_EVE_NAME = re.compile(r"eve\.json(\.[^/]+)?(\.gz)?$")

#: Suricata's `timestamp` field, e.g. `"2026-06-21T10:00:00.123456+0000"` — an offset
#: with NO colon. `strptime` + `%z` accepts that spelling (and the `+00:00` one) on
#: every Python version this project's floor covers, and is the FALLBACK; the fast path
#: is `datetime.fromisoformat`, ~5x cheaper on the same string and reached first (code
#: review `S4`) because this function runs once per parsed field on every row of a
#: multi-million-row stream. `fromisoformat` was avoided entirely in this module's first
#: landing on the belief that it refuses the colon-less spelling; it does not on the
#: >=3.11 floor `pyproject.toml` pins (3.11 relaxed it), and the fallback below means a
#: build that somehow disagreed still parses correctly rather than dropping every row.
_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
)


def parse_suricata_timestamp(value: object) -> float | None:
    """Suricata's `timestamp` field (ISO 8601) -> epoch seconds, or `None` when it does
    not parse at all **or carries no UTC offset** — a naive timestamp is refused rather
    than resolved in host-local time (`LES-002`; see the guard's own comment below).

    Lives here, not in `plugin.py`, and is IMPORTED by it — a deliberate departure from
    `ingest/zeek/sources.py`'s own split, where ordering (`_row_ts`) and semantic parsing
    (`plugin.py::_f`) are two separate, independently-trivial functions. Zeek's `ts` is
    one `float()` call, cheap enough to duplicate safely with no drift risk; an ISO-8601
    parser is not — two independently-written copies (one here for the "is this shape
    parseable" question this transport never actually needs to ask, and one in `plugin.py`
    for constructing `Event.ts`) would be exactly the kind of pair `Q2`'s reorder-helper
    refactor exists to prevent one layer over. One function, shared within this package
    (not exported across the `zeek` boundary the way `reorder_by_key` is imported FROM),
    used by every parser in `plugin.py` that needs `Event.ts`.

    Confidence note (module docstring's own discipline, applied here): this format was
    NOT verified against a live Suricata capture — no real capture was available to this
    build (CLAUDE.md's gate on reading one). It matches every publicly documented eve.json
    sample this build's training knowledge recalls, but a real sample should confirm it
    before this parser is trusted at scale (flagged again in `plugin.py`'s module
    docstring, alongside every other confidence caveat this build is disclosing rather
    than silently assuming past).
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _TS_FORMATS:
            try:
                # DTZ007 (naive `strptime`) is suppressed, not dodged: both `_TS_FORMATS`
                # entries end in `%z`, which ruff cannot see through the tuple, and the
                # `tzinfo is None` guard below refuses a naive result from EITHER path
                # anyway — the enforcement is there, in code, not in this format string.
                parsed = _dt.datetime.strptime(text, fmt)  # noqa: DTZ007
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    # A NAIVE datetime is refused, not resolved: `.timestamp()` on one interprets it in
    # the HOST's local timezone, so the same capture would ingest to different epoch
    # seconds on two machines — a determinism break wearing a parsed value's face
    # (`LES-002`, CONVENTIONS §3). The `strptime` path could never produce one (`%z` is
    # mandatory in both `_TS_FORMATS` entries); `fromisoformat` happily can, which is
    # the one behavioural difference the fast path introduces and the reason this check
    # is here rather than assumed away.
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


class SuricataSourceError(ValueError):
    """The locator does not name a shape this module can read.

    Its own class, mirroring `ingest/zeek/sources.py::ArchiveContractError`: a distinct
    type names the failure as "this input's own shape is not one we read", not a bug in
    this reader.
    """


@dataclass
class EveStream:
    """One independently-ordered record stream: one discovered eve.json(-rotated) file.

    Unlike `ingest/zeek/sources.py::ZeekStream`, this carries no `stem` — Suricata's
    `event_type` lives INSIDE each row, not in the file name, so dispatch is `plugin.py`'s
    job once rows are already decoded (module docstring). `rows` is a factory rather than
    an iterator, matching `ZeekStream`'s own reasoning: discovery stays cheap and nothing
    is read until the merge actually pulls from this stream.
    """
    label: str
    rows: Callable[[object], Iterator[dict]]


def _is_eve_file(p: Path) -> bool:
    return p.is_file() and _EVE_NAME.fullmatch(p.name) is not None


def _discover(locator: str) -> tuple[list[Path], list[str]]:
    """`locator` -> (files to read, in a deterministic order; discovery notes).

    Fails loud (`SuricataSourceError`/`FileNotFoundError`) on anything this module does
    not recognize as an eve.json shape, rather than silently reading whatever it finds
    (CONVENTIONS §3). Non-recursive on the directory branch, deliberately: Suricata's
    default deployment writes `eve.json` directly into its configured log directory, with
    no documented nested-tree shape the way `xtract harvest`'s cold tier gives Zeek's own
    `LogDirSource` a verified window-tree layout to detect. Inventing a nested-search
    convention with no real shape to anchor it to would be exactly the "unrecognized
    shape passed through" CONVENTIONS §3 forbids — a caller with eve.json one level
    deeper than expected should point `--source` at the right file or directory, not
    have this module guess which of possibly many nested JSON files is the right one.
    """
    root = Path(locator)
    if not root.exists():
        raise FileNotFoundError(f"suricata source not found: {root}")  # fail loud
    if root.is_file():
        return [root], [
            (f"{root}: a single file, read directly (not a directory) — the name is not "
             f"checked against eve.json's own shape, matching "
             f"`ingest/zeek/sources.py::LogDirSource`'s identical single-log-file branch")
        ]
    files = sorted(p for p in root.iterdir() if _is_eve_file(p))
    if not files:
        raise SuricataSourceError(
            f"{root}: no eve.json (or a rotated/gzipped sibling matching "
            f"{_EVE_NAME.pattern!r}) found directly inside this directory. aximprint "
            f"refuses to guess which file in an unrecognized directory shape is the eve "
            f"stream (CONVENTIONS §3) — point --source at the exact file, or at "
            f"eve.json's own directory")
    return files, [
        (f"{root}: {len(files)} eve.json file(s) found (non-recursive; rotation "
         f"siblings merged on each event's own ts, never on file name — `LES-013`)")
    ]


def _rows(path: Path, stats) -> Iterator[dict]:
    """Yield dict rows from one eve.json(-rotated) file: JSON-lines, optionally gzipped.

    A non-JSON or non-object line is skipped and counted (`stats.malformed`), never
    allowed to crash the whole read — `ingest/zeek/sources.py::_rows`'s identical
    discipline for a TSV row whose column count disagrees with `#fields` (`LES-007`'s "a
    short row is not a row"), applied here to a line-oriented JSON transport instead:
    Suricata itself can write a genuinely truncated final line if the process is killed
    mid-write, the same "routine on the last line of a killed writer" case that module
    names for Zeek's own TSV.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats.malformed += 1
                continue
            if not isinstance(row, dict):
                stats.malformed += 1
                continue
            yield row


class SuricataSource:
    """One eve.json stream — a bare file, or a directory containing one (plus rotated/
    gzipped siblings). See the module docstring for the two shapes accepted and why a
    third (a per-thread-named file, or a nested subdirectory) is refused rather than
    guessed at.

    Deliberately carries no `inventory`/`inventory_is_complete` pair the way
    `ingest/zeek/sources.py::ZeekSource` does — not an oversight, a structural fact:
    those exist so `plugin.py` can trust a DISCOVERY-time (file-listing) census, and
    Suricata's `event_type` census cannot be taken at discovery time at all (module
    docstring's central point — it lives inside a row, not a file name).
    `ingest/suricata/plugin.py` computes its own, better answer once every discovered
    file has actually been read to exhaustion; see that module's docstring for the
    consequence (`unparsed_stems`/`unrecognized_stems` stay `None` until then, a
    disclosed departure from `LES-106`'s pre-first-yield guarantee).
    """

    name = "suricata"
    unit = "file"

    def __init__(self, locator: str, *,
                 reorder_horizon_seconds: float = REORDER_HORIZON_SECONDS,
                 reorder_rows: int = REORDER_ROWS) -> None:
        self.locator = locator
        self._notes: list[str] = []
        #: Not read by anything in this module — carried only so `plugin.py` can
        #: construct this source once and have both its own top-level merge and any
        #: future per-file smoothing agree on one configured bound (mirrors
        #: `ingest/zeek/sources.py::LogDirSource`'s identical two fields for the
        #: identical reason: tests override `SuricataPlugin.reorder_rows` etc., and
        #: this source is constructed fresh per run).
        self.reorder_horizon_seconds = reorder_horizon_seconds
        self.reorder_rows = reorder_rows

    def streams(self) -> list[EveStream]:
        # Reset rather than accumulate — `notes()` describes the MOST RECENT call, not
        # a running total (mirrors `LogDirSource.streams`'s identical reset, and the
        # same nit its own review caught: a stale note from an earlier call must not
        # survive into this one's answer).
        self._notes = []
        files, notes = _discover(self.locator)
        self._notes.extend(notes)
        return [EveStream(label=str(path), rows=lambda stats, _p=path: _rows(_p, stats))
                for path in files]

    def notes(self) -> list[str]:
        return list(self._notes)
