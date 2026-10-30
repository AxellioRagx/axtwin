"""Shared scaffolding for every `http` pull plugin — lifted from `ingest/axquery/sources.py`.

`ingest/siem/SPEC.md` is the design of record. One family of plugins (axquery, then each SIEM)
pulls connection-level rows over a search API, and they all share four things that are easy
to get subtly wrong and must not be re-derived per vendor:

* **The locator parser.** `<name>:<absolute http(s) URL>[?<options>]`. A bare host is REFUSED
  rather than defaulted to `http://` — guessing a scheme is the silent default this project
  rejects, and a wrong guess is a connection to the wrong service, not a readable error. A
  credential in the URL's userinfo, or under a credential-named option key, is refused by
  name: a locator is written into every run's provenance.
* **Credentials from the environment, never `argv` and never the locator**
  (`acquire/auth.py`'s rule — a value in `argv` is in `ps` and shell history for every user
  on the box). `axrange serve` sets `AXIMPRINT_SOURCE_USER`/`_PASSWORD`/`_API_KEY` for the one
  `aximprint` subprocess a stored discovery source runs (`api/Spec.md` A7); the plugin
  translates them into its vendor's header (Basic always; the key's scheme is the vendor's —
  `Bearer` for axquery and Splunk, `ApiKey` for Elastic).
* **Time-bisected leaves.** No vendor cap is trusted as "all of it": an interval whose page
  came back FULL is split, down to a one-second floor, and a floor-width leaf that is still
  full is re-read with a doubled row limit up to a ceiling — that leaf only, never the window.
  Past the ceiling the leaf is refused, never truncated (`LES-006`: a truncation nobody can
  see reads as completeness). Each leaf emits only rows whose OWN start falls inside it, so
  leaves partition the range exactly and no dedup set is needed.
* **Leaf coverage to `SensorHealthEvent`.** A server that states its own completeness for a
  request (axquery's `coverage` block, Elasticsearch's `_shards`) has it recorded per LEAF,
  in the same `capture_loss` channel Zeek uses, because a bisected read makes many requests
  and averaging them would hide the one incomplete sub-interval.

**stdlib `urllib` on purpose, not `requests`.** `requests` sits in the optional `[acquire]`
extra precisely so a bare `pip install -e .` still runs the whole discovery pipeline.
"""
from __future__ import annotations

import base64
import heapq
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from aximprint.events import Event, SensorHealthEvent
from aximprint.ingest.base import IngestPlugin

__all__ = [
    "ENV_API_KEY", "ENV_PASSWORD", "ENV_USER", "CREDENTIAL_OPTION_KEYS", "LEAF_ROWS_MAX",
    "MIN_BISECT_SECONDS", "PAGE_ROWS", "TIMEOUT_SECONDS", "HttpPullPlugin", "HttpSourceError",
    "Locator", "auth_headers", "bisected_rows", "coverage_event", "fetch", "fetch_json",
    "parse_locator",
]

#: How a credential reaches a pull: the ENVIRONMENT (see the module docstring). Basic wins
#: over the key when both are set, and both are optional: an `--auth-disabled` axquery on a
#: loopback tunnel needs neither.
ENV_USER = "AXIMPRINT_SOURCE_USER"
ENV_PASSWORD = "AXIMPRINT_SOURCE_PASSWORD"
ENV_API_KEY = "AXIMPRINT_SOURCE_API_KEY"

#: Locator option keys that LOOK like a credential. Refused by name whatever the plugin's own
#: option table says, so no vendor module can admit one by accident.
CREDENTIAL_OPTION_KEYS = frozenset({
    "user", "username", "password", "pass", "passwd", "api_key", "apikey", "api-key",
    "token", "auth", "authorization", "bearer", "secret", "key", "sec",
})

#: Rows per leaf request for the SIEM family. 10,000 is Elasticsearch's default
#: `index.max_result_window` (the largest `size` one search accepts without a cluster-setting
#: change) and a comfortable `| head` for a Splunk export; a vendor module that measured a
#: different right value for ITS server keeps its own constant (axquery: 20,000, measured on
#: CorpAPCN) and passes it in — the discipline is shared, the number is not.
PAGE_ROWS = 10_000

#: Ceiling on ONE floor-width leaf, past which a prefix is refused rather than fetched. Same
#: value axquery measured against a one-second port sweep (`axquery/sources.py`).
LEAF_ROWS_MAX = 200_000

#: Floor on time-bisection. Every server in this family filters time at second granularity
#: (axquery's `after`/`before`, Splunk's `earliest`/`latest`, ES's `@timestamp` range on a
#: whole-second bound), so nothing narrower than a second asks a different question.
MIN_BISECT_SECONDS = 1.0

#: Wall-clock per request. A cold read can be slow; a hang must not be forever.
TIMEOUT_SECONDS = 120


class HttpSourceError(RuntimeError):
    """The server was unreachable, refused the pull, or answered with something this build
    cannot read. Each vendor module subclasses it so a caller can still name the vendor."""


def auth_headers(env: Optional[dict] = None, *, key_scheme: str = "Bearer") -> dict[str, str]:
    """`Authorization` for every request, from the environment; `{}` when unset.

    `ENV_USER`/`ENV_PASSWORD` is RFC 7617 Basic on every vendor; `ENV_API_KEY` is sent under
    `key_scheme` (`Bearer` for axquery's token gate and Splunk's auth tokens, `ApiKey` for
    Elasticsearch). Nothing here decides which the server wants — the operator chose an access
    mode when the source was saved, and the control plane set exactly one pair.
    """
    env = os.environ if env is None else env
    user = env.get(ENV_USER)
    password = env.get(ENV_PASSWORD)
    if user:
        raw = f"{user}:{password or ''}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    key = env.get(ENV_API_KEY)
    if key:
        return {"Authorization": f"{key_scheme} {key}"}
    return {}


@dataclass(frozen=True, slots=True)
class Locator:
    """A parsed `<prefix>:<base URL>[?options]`: the base with its trailing slash stripped, and
    the vendor options as a plain mapping (every key already checked against the plugin's
    table, no key repeated, no credential among them)."""
    base: str
    options: dict[str, str]


def parse_locator(locator: str, prefix: str, *, options: tuple[str, ...] = (),
                  error: type[Exception] = HttpSourceError, example: str = "") -> Locator:
    """`<prefix><absolute http(s) URL>[?k=v&…]` -> `Locator`. Fail loud on anything else.

    `prefix` includes its colon (`"axquery:"`). Refused by name: a missing prefix, a URL
    without an http(s) scheme and a host (a bare host is NOT defaulted — see the module
    docstring), a credential in the URL's userinfo, an option key that names a credential
    (`CREDENTIAL_OPTION_KEYS`), an option key outside `options`, and a repeated key. A plugin
    that admits no option at all passes `options=()`, and then any `?` is refused.
    """
    if not locator.startswith(prefix):
        raise error(f"not a {prefix[:-1]} locator: {locator!r} — expected "
                    f"'{prefix}<base-url>'" + (f" (e.g. {example!r})" if example else ""))
    rest = locator[len(prefix):].strip()
    raw_base, sep, query = rest.partition("?")
    base = raw_base.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise error(f"{prefix[:-1]} locator needs an absolute http(s) base URL, got {base!r}"
                    + (f" (e.g. {example!r})" if example else ""))
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise error(f"{prefix[:-1]} locator carries a credential in its URL; credentials "
                    f"ride the environment only ({ENV_USER}/{ENV_PASSWORD} or {ENV_API_KEY}), "
                    f"never the locator")
    opts: dict[str, str] = {}
    if sep:
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
            if key.lower() in CREDENTIAL_OPTION_KEYS:
                raise error(f"{prefix[:-1]} locator option {key!r} names a credential; "
                            f"credentials ride the environment only ({ENV_USER}/"
                            f"{ENV_PASSWORD} or {ENV_API_KEY}), never the locator")
            if key not in options:
                raise error(f"{prefix[:-1]} locator option {key!r} is not one of "
                            f"{sorted(options)}: {locator!r}")
            if key in opts:
                raise error(f"{prefix[:-1]} locator option {key!r} given twice: {locator!r}")
            opts[key] = value
    return Locator(base=base, options=opts)


def fetch(request: urllib.request.Request, *, label: str, base: str,
          error: type[Exception] = HttpSourceError,
          timeout: float = TIMEOUT_SECONDS) -> bytes:
    """One request, the body as bytes, every failure mode named.

    A 401/403 names the environment settings (the operator needs to know WHICH knob); a
    timeout says to narrow the window; a socket-level failure says unreachable. `OSError`,
    not `URLError`: `URLError` subclasses it, but a socket failure can surface as a bare
    `OSError` and escaping here would hand a caller an errno instead of a named cause —
    `probe` in particular converts this to `reachable: False`, and it can only do that for
    an exception type it can name.
    """
    url = request.full_url
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (401, 403):
            hint = (" -- the server refused the credential (or wants one): "
                    f"set {ENV_USER}/{ENV_PASSWORD} or {ENV_API_KEY}")
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 — the body is a courtesy, never a second failure
            pass
        raise error(f"{label} -> HTTP {exc.code}: {url}{hint}"
                    + (f" :: {detail}" if detail.strip() else "")) from exc
    except TimeoutError as exc:
        raise error(f"{label} timed out after {timeout}s — narrow the time window") from exc
    except OSError as exc:
        raise error(f"{label} unreachable at {base}: {exc}") from exc


def fetch_json(request: urllib.request.Request, *, label: str, base: str,
               error: type[Exception] = HttpSourceError,
               timeout: float = TIMEOUT_SECONDS) -> Any:
    """`fetch`, decoded as JSON. An empty body is refused by name (measured on axquery: an
    over-large request returns an empty body rather than an error) and so is non-JSON."""
    body = fetch(request, label=label, base=base, error=error, timeout=timeout)
    if not body.strip():
        raise error(f"{label} returned an EMPTY body ({request.full_url}) — this is what an "
                    f"over-large or over-wide request looks like; narrow it rather than "
                    f"retrying")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise error(f"{label} returned non-JSON: {body[:200]!r}") from exc


def bisected_rows(fetch_leaf: Callable[[float, float, int], list[dict]], after: float,
                  before: float, start_ts: Callable[[dict], Optional[float]], *,
                  page_rows: int, leaf_rows_max: int, label: str,
                  error: type[Exception] = HttpSourceError,
                  min_bisect_seconds: float = MIN_BISECT_SECONDS) -> Iterator[dict]:
    """Yield every row STARTING in `[after, before)` by splitting time, never by trusting a cap.

    `fetch_leaf(lo, hi, limit)` is the vendor's one leaf request: the rows the server returns
    for the interval under a row limit, in any order. The discipline here is the one axquery
    measured (`axquery/sources.py`'s docstrings carry the numbers):

    * A page that came back FULL (`len(rows) >= limit`) is evidence of truncation and is NEVER
      consumed — the interval is halved instead. Peak server memory stays at one page however
      dense the traffic.
    * A time filter selects rows that OVERLAP the interval (a connection DB) or whose index
      time falls in it (a SIEM); either way each leaf keeps only rows whose OWN start
      (`start_ts(row)`) falls in its half-open interval, so leaves partition the range exactly
      and every row is emitted by exactly one leaf. A row `start_ts` cannot place is skipped.
    * A leaf at the floor that is STILL full is re-read with a doubled limit, up to
      `leaf_rows_max`, because a one-second burst (a port sweep) cannot be split any finer.
      Past the ceiling it is raised, not truncated.

    `page_rows`/`leaf_rows_max` are passed in, not read from this module, so a vendor keeps
    its own measured values and a test can shrink them on the vendor's module.
    """
    stack = [(float(after), float(before), int(page_rows))]
    while stack:
        lo, hi, limit = stack.pop()
        rows = fetch_leaf(lo, hi, limit)
        if len(rows) < limit:
            for r in rows:
                ts = start_ts(r)
                if ts is not None and lo <= ts < hi:
                    yield r
            continue
        if hi - lo <= min_bisect_seconds:
            if limit >= leaf_rows_max:
                raise error(
                    f"{label}: {len(rows)} rows in [{int(lo)}, {int(hi)}) still fills a page "
                    f"at the {min_bisect_seconds}s floor and the {leaf_rows_max}-row leaf "
                    f"ceiling. **Narrowing the time range will NOT help** — a time filter "
                    f"matches rows that OVERLAP the interval, so this count converges to the "
                    f"number of CONCURRENT connections plus one second of starts, not to "
                    f"zero. Raise LEAF_ROWS_MAX above that peak instead. Refusing rather "
                    f"than returning a silent prefix.")
            # The same second, asked wider. Only a floor leaf ever escalates.
            stack.append((lo, hi, min(limit * 2, int(leaf_rows_max))))
            continue
        mid = lo + (hi - lo) / 2.0
        # Later half first so the LIFO stack yields in ascending time; the merge downstream
        # tolerates bounded disorder but there is no reason to manufacture any.
        stack.append((mid, hi, int(page_rows)))
        stack.append((lo, mid, int(page_rows)))


def coverage_event(ts: float, fraction: float, detail: str) -> SensorHealthEvent:
    """A server's own completeness statement for one leaf, as the loss percentage Zeek's
    `capture_loss` already speaks — one health vocabulary for one question, not a second."""
    return SensorHealthEvent(ts=ts, kind="capture_loss",
                             value=round(max(0.0, 1.0 - float(fraction)) * 100.0, 6),
                             detail=detail)


class HttpPullPlugin(IngestPlugin):
    """The driver every SIEM plugin shares: parse the locator, resolve bounds, k-way merge the
    vendor's streams by `ts`, and carry per-leaf health events beside the rows.

    A vendor module supplies `prefix`, `locator_options`, `error`, `_DECLINED`, and the two
    hooks: `_span(locator)` (the data span, for an unbounded call) and `_streams(locator, lo,
    hi)` (one ts-ascending iterator per table it reads). `events()` k-way MERGES those exactly
    like the axquery and zeek plugins do — never a concatenation, which would land every
    identity event hours behind the clock the flow stream had already advanced (measured on
    axquery, 2026-09-08).

    **The health side channel.** A leaf fetcher runs inside `bisected_rows` and can only
    return rows, yet a leaf has things to say that are not rows: a shard that failed, an
    aggregate that silenced features. It appends a `SensorHealthEvent` to `self._health`
    (ts = the leaf's start) and `_leaf_stream` drains that list before the leaf's rows reach
    the merge, so the health event precedes the rows it describes.
    """

    source_kind = "http"
    prefix: str = ""
    locator_options: tuple[str, ...] = ()
    error: type[HttpSourceError] = HttpSourceError
    example: str = ""

    #: Tables declined BY NAME with a reason, so a reader finds them here instead of inferring
    #: them from absence (`ingest/zeek/plugin.py::_SKIP`'s discipline). Named into
    #: `unparsed_stems` on every run.
    _DECLINED: dict[str, str] = {}

    def __init__(self) -> None:
        self._health: list[SensorHealthEvent] = []

    def locator(self, source: str) -> Locator:
        return parse_locator(source, self.prefix, options=self.locator_options,
                             error=self.error, example=self.example)

    @abstractmethod
    def _span(self, loc: Locator) -> tuple[Optional[float], Optional[float]]:
        """`(first_ts, last_ts)` the source holds, `last_ts` INCLUSIVE; `None`s when empty."""

    @abstractmethod
    def _streams(self, loc: Locator, lo: float, hi: float) -> list[Iterator[Event]]:
        """One ts-ascending event iterator per table read over `[lo, hi)`."""

    def events(self, source: str, ts_from: float | None = None,
               ts_to: float | None = None) -> Iterator[Event]:
        loc = self.locator(source)
        # The declined tables are this build's own closed list, final before the first yield;
        # rows seen for a declined/alert table during the pull are COUNTED onto it.
        self.unparsed_stems = {name: 1 for name in self._DECLINED}
        self.unrecognized_stems = {}
        self._health = []
        if ts_from is None or ts_to is None:
            first, last = self._span(loc)
            if first is None or last is None:
                raise self.error(f"{self.name} at {loc.base} holds no data span to bisect — "
                                 f"pass --from/--to")
            ts_from = float(first) if ts_from is None else ts_from
            ts_to = float(last) + 1.0 if ts_to is None else ts_to     # inclusive -> half-open
        streams = self._streams(loc, float(ts_from), float(ts_to))
        yield from heapq.merge(*streams, key=lambda ev: ev.ts)

    def _count(self, table: dict[str, int], name: str) -> None:
        table[name] = table.get(name, 0) + 1

    def _leaf_stream(self, rows: Iterator[dict],
                     mapper: Callable[[dict], Iterator[Event]]) -> Iterator[Event]:
        for row in rows:
            while self._health:
                yield self._health.pop(0)
            yield from mapper(row)
        while self._health:
            yield self._health.pop(0)
