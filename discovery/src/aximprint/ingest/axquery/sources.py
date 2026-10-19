"""axquery HTTP transport — where rows come from, not what they mean.

Mirrors `ingest/zeek/sources.py`'s split: this module locates, fetches, pages and decodes;
`plugin.py` owns the endpoint table and the event semantics.

**stdlib `urllib` on purpose, not `requests`.** `requests` sits in the optional `[acquire]`
extra precisely so a bare `pip install -e .` still runs the whole discovery pipeline; importing
it here would quietly make an optional extra mandatory for a discovery-side plugin. A GET with
query parameters returning JSON does not need a dependency to reach that far.

Two hard-won constraints from `docs/WORK_ORDER_axquery_ingest.md`, both measured:

* **Paging by `limit` can kill this sensor.** Walking `limit` up against a `--lazy-cold` server
  OOM-killed it at 25 GB anon-rss under its cgroup cap (2026-08-19). Cold chunks fault in
  without bound. So: page by TIME first, `offset` second, and keep `limit` small.
* **`totalMatches` is not a total.** In one fixed window it grew 67 -> 68 -> 115 -> 185 as
  `limit` went 2 -> 3 -> 50 -> 120. It is a discovered-so-far count. Page until `nextOffset`
  is absent; a reader that pages to that number silently under-reads.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator, Optional

#: Rows per request. **Sized against CONCURRENCY, not against rate**, and the difference is what
#: a first value of 500 got wrong.
#:
#: `after`/`before` select conns that OVERLAP the interval, so as an interval shrinks the row
#: count does NOT converge to zero — it converges to the number of connections ALIVE at that
#: instant. That is a floor bisection cannot get under, so `PAGE_ROWS` must exceed peak
#: concurrency or a dense second becomes unreadable however finely it is split. Measured
#: 2026-08-20 at the second that first hit the floor (`[1780876260, 1780876261)`): **525
#: overlapping conns, of which only 101 actually start there** — 500 sat just under it and a
#: four-hour read failed.
#:
#: 5,000 is ~10x that observed peak. **This is not the request shape that OOM-killed this server
#: on 2026-08-19**: those were `/api/connections?limit=2000` with NO time bound (26 days) and
#: `/api/entities?limit=5000` against a 1.42 M-row table. Every request here is time-bounded, and
#: measurement showed `limit=2000` and `limit=8000` return the identical 525 rows on a bounded
#: window — a larger cap costs nothing when the window is small, and buys far fewer splits.
#: **`PAGE_ROWS` is SEMANTIC, not just a performance knob** — measured 2026-08-20. axquery
#: consolidates a connection's cross-chunk fragments per response, and its own docs say a
#: *"truncated (limit-hit) response is page semantics (merge-so-far)"*: a heavily truncated page
#: merges only what it has seen, so the same hour yields **61,066** flows at 500 and **61,089** at
#: 5,000. Determinism still holds as this project defines it — window close is a pure function of
#: (events, resolver state, **code build**), and this constant is part of the build; two runs at
#: one value are byte-identical (verified). But changing it CHANGES THE DATA, so treat it like a
#: parser change and re-measure, never as a tuning dial.
PAGE_ROWS = 5000

#: Wall-clock per request. A cold-chunk fault can be slow; a hang must not be forever.
TIMEOUT_SECONDS = 120

#: Floor on time-bisection. An interval this small that STILL fills a page is reported, never
#: silently truncated — one second holding more than `PAGE_ROWS` connections is a real
#: possibility on a 12-stream tap and the operator needs to know, not get a prefix.
MIN_BISECT_SECONDS = 1.0

#: Hard stop on pages per (endpoint, window). At PAGE_ROWS this is 5 M rows in one window,
#: far past anything real — a runaway-cursor backstop, not a coverage limit. Hitting it is
#: reported, never silent (`LES-006`: a truncation nobody can see reads as completeness).
MAX_PAGES = 10_000


class AxQueryError(RuntimeError):
    """The server was unreachable, or answered with something this build cannot read."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """axquery's own completeness statement for one time-bounded request.

    This is the thing Zeek never gave us: a SENSOR-supplied answer to "did you see all of it",
    against the range we ASKED for rather than the range it happened to hold. It is why this
    plugin can populate window health honestly instead of inferring completeness from silence.
    """
    complete: bool
    reason: str
    chunks_wanted: int
    chunks_covered: int
    chunks_missing: int
    fraction: float

    @classmethod
    def from_json(cls, blob: dict | None) -> Optional["Coverage"]:
        """`None` when the response carried no coverage block at all — unaudited, which is a
        different claim from audited-and-complete (`LES-006`)."""
        if not isinstance(blob, dict):
            return None
        return cls(
            complete=bool(blob.get("complete", False)),
            reason=str(blob.get("reason", "")),
            chunks_wanted=int(blob.get("chunksWanted", 0) or 0),
            chunks_covered=int(blob.get("chunksCovered", 0) or 0),
            chunks_missing=int(blob.get("chunksMissing", 0) or 0),
            fraction=float(blob.get("fraction", 0.0) or 0.0),
        )


class AxQuerySource:
    """One axquery instance, addressed as `axquery:<base-url>`."""

    def __init__(self, locator: str) -> None:
        self.base = self._parse_locator(locator)
        #: Set by `get()`; the most recent response's completeness statement.
        self.last_coverage: Optional[Coverage] = None

    @staticmethod
    def _parse_locator(locator: str) -> str:
        """`axquery:http://host:port` -> `http://host:port`. Fail loud on anything else.

        A bare host is REFUSED rather than defaulted to http:// — guessing a scheme is the
        silent default this project rejects, and a wrong guess here is a connection to the
        wrong service, not a readable error.
        """
        if not locator.startswith("axquery:"):
            raise AxQueryError(
                f"not an axquery locator: {locator!r} — expected 'axquery:<base-url>'")
        base = locator[len("axquery:"):].strip().rstrip("/")
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise AxQueryError(
                f"axquery locator needs an absolute http(s) base URL, got {base!r} "
                f"(e.g. 'axquery:http://127.0.0.1:19090')")
        return base

    def get(self, path: str, **params: Any) -> dict:
        """One GET returning parsed JSON. Empty parameters are DROPPED, not sent.

        axquery ignores empty query parameters by design (for Grafana template variables that
        resolve to ""), so sending them is harmless there — but dropping them here keeps the
        request URL a faithful record of what was actually asked, which matters when a
        coverage claim is attached to it.
        """
        query = {k: v for k, v in params.items() if v is not None and v != ""}
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise AxQueryError(f"axquery {path} -> HTTP {exc.code}: {url}") from exc
        except TimeoutError as exc:
            raise AxQueryError(
                f"axquery {path} timed out after {TIMEOUT_SECONDS}s — a --lazy-cold server "
                f"faulting cold chunks can be slow; narrow the time window") from exc
        # `OSError`, not `URLError`: `URLError` subclasses it, but a socket-level failure can
        # surface as a bare `OSError` and escaping here would give a caller an errno instead of
        # a named cause. `probe_vantage` in particular converts this to `None`, and it can only
        # do that for an exception type it can name.
        except OSError as exc:
            raise AxQueryError(f"axquery unreachable at {self.base}: {exc}") from exc
        if not body.strip():
            # Measured: an over-large request can return an empty body rather than an error.
            raise AxQueryError(
                f"axquery {path} returned an EMPTY body ({url}) — this is what an over-large "
                f"or over-wide request looks like on this server; narrow it rather than retrying")
        try:
            blob = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AxQueryError(f"axquery {path} returned non-JSON: {body[:200]!r}") from exc
        if not isinstance(blob, dict):
            raise AxQueryError(f"axquery {path} returned {type(blob).__name__}, expected an object")
        self.last_coverage = Coverage.from_json(blob.get("coverage"))
        return blob

    def info(self) -> dict:
        """`/api/info` — the DB census, and the one call `probe_vantage` needs."""
        return self.get("/api/info")

    def paged_rows(self, path: str, key: str, **params: Any) -> Iterator[dict]:
        """Yield every row from a cursor-paged endpoint.

        Terminates on an ABSENT `nextOffset`, never on `totalMatches` — see the module
        docstring. `AxQueryError` on running past `MAX_PAGES`, which is a bug in this loop or a
        server that never stops advancing, and either way must not spin forever.
        """
        offset = 0
        for page in range(MAX_PAGES):
            blob = self.get(path, offset=offset, limit=PAGE_ROWS, **params)
            rows = blob.get(key) or []
            yield from rows
            nxt = blob.get("nextOffset")
            if nxt is None or not rows or nxt == offset:
                return
            offset = int(nxt)
        raise AxQueryError(
            f"axquery {path} still paging after {MAX_PAGES} pages at offset {offset} — "
            f"refusing to continue")

    def bisected_rows(self, path: str, key: str, after: float, before: float,
                      start_ts, **params: Any) -> Iterator[dict]:
        """Yield every row STARTING in `[after, before)` by splitting time, never by asking for more.

        **No endpoint on this server pages.** Measured 2026-08-20: `/api/connections`,
        `/api/entities`, `/api/devices` and `/api/identity/users` all ignore `offset` and always
        return `nextOffset: null`. The only lever is `limit`, and raising `limit` is what
        OOM-killed this server once already. So completeness has to come from the one axis that
        does filter: time. A full page is treated as evidence of truncation and the interval is
        halved, which keeps peak server memory at one `PAGE_ROWS` result however dense the
        traffic — the opposite of the `limit`-walking that killed it.

        **`after`/`before` select connections that OVERLAP the range, not ones that start in
        it**, which is right for a connection database and wrong for naive bisection: a
        long-lived conn matches every sub-interval it spans. Measured before this filter existed
        — one 50-minute window returned 202,961 rows carrying 50,887 distinct identities (4x
        duplication) and a `ts` reaching 14.9 days back.

        So each leaf keeps only rows whose OWN start falls in that leaf's half-open interval.
        Leaves partition the range exactly, so every connection is emitted by exactly one leaf
        and **no dedup set is needed** — the read stays O(1) in memory and streaming, which a
        global identity set over a 26-day span would not. Assigning a flow by its start also
        matches Zeek, where a `conn.log` row's `ts` is the connection's start and windowing
        keys on it; a conn that began before `after` belongs to an earlier slice and is emitted
        when the caller reads that one.

        An interval that cannot be split further and is STILL full is raised, not truncated: the
        sensor holds more rows in one second than a bounded read can retrieve, and returning a
        prefix would be the invisible truncation this project treats as a lie (`LES-006`).
        """
        stack = [(float(after), float(before))]
        while stack:
            lo, hi = stack.pop()
            blob = self.get(path, limit=PAGE_ROWS, after=int(lo), before=int(hi), **params)
            rows = blob.get(key) or []
            if len(rows) < PAGE_ROWS:
                for r in rows:
                    ts = start_ts(r)
                    if ts is not None and lo <= ts < hi:
                        yield r
                continue
            if hi - lo <= MIN_BISECT_SECONDS:
                raise AxQueryError(
                    f"axquery {path}: {len(rows)} rows in [{int(lo)}, {int(hi)}) still fills a "
                    f"page at the {MIN_BISECT_SECONDS}s floor. **Narrowing the time range will "
                    f"NOT help** — `after`/`before` match conns that OVERLAP the interval, so "
                    f"this count converges to the number of CONCURRENT connections, not to zero. "
                    f"Raise PAGE_ROWS (currently {PAGE_ROWS}) above peak concurrency instead. "
                    f"Refusing rather than returning a silent prefix.")
            mid = lo + (hi - lo) / 2.0
            # Later half first so the LIFO stack yields in ascending time; the merge downstream
            # tolerates bounded disorder but there is no reason to manufacture any.
            stack.append((mid, hi))
            stack.append((lo, mid))

    def limited_rows(self, path: str, key: str, **params: Any) -> tuple[list[dict], bool]:
        """One bounded read of a non-pageable, non-time-filterable endpoint.

        Returns `(rows, saturated)`. `saturated` means the page came back FULL, i.e. this is a
        PREFIX of the census and the rest is unreachable — the caller must disclose it rather
        than present the prefix as the whole. Kept for callers that genuinely want a bounded
        sample; no event-producing path in this plugin uses it today (see `plugin._DECLINED`).
        """
        blob = self.get(path, limit=PAGE_ROWS, **params)
        rows = blob.get(key) or []
        return rows, len(rows) >= PAGE_ROWS
