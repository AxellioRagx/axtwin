"""cli — the `xtract` command line: `xtract streams`
(`docs/WORK_ORDER_catalog_streams.md` WO-3 Phase 3) and `xtract pcap`
(`docs/WORK_ORDER_runner_pcap.md` WO-6 Phase 4).

**`xtract streams` delivers the user's first task**, reporting, for every
stream or a named subset, the first/last timestamp of stored capture data,
file count, byte count, and where the gaps are — all assembled by
`catalog.py`.

**`xtract pcap` delivers the user's second task** — the point at which the
tool does what it was asked to do: walk `[--start, --end)` in `--interval`
steps and write one pcap per window to `--out`, resumably. This module owns
only argument parsing, output formatting, and mapping outcomes to process
exit codes; every window-plan/resume/collision decision happens in
`runner.py`, every wire call in `catalog.py`/`extract.py`.

Exit codes (work order §2 Phase 3 for `streams`; WO-6 §2 Phase 4 for `pcap`):
  * `0` — success. **A stream with no data is not an error; an empty pcap
    window is not an error.**
  * `1` — `pcap` only: one or more windows failed (a mix of written/empty is
    still `0`).
  * `2` — usage error (bad flags, a malformed `--streams` spec, an unknown
    stream id, a window-count ceiling without `--yes`, a manifest collision
    without the override).
  * `3` — auth failure.
  * `4` — transport/server error (an unexpected `axapi` response, including
    a malformed catalog response, or every requested stream being empty
    when resolving an omitted `--end`).
  * `130` — `pcap` only: interrupted by SIGINT mid-run (resumable; see the
    printed instructions).

`main()` never lets an unhandled exception reach the caller as a traceback —
every error path here ends in a one-line `stderr` message and a typed exit
code, per the work order's "an unknown stream name produces a clear error,
not a traceback."
"""

from __future__ import annotations

import argparse
import json as jsonlib
import re
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aximprint.acquire import (
    __version__,
    catalog,
    manifest,
    runner,
    streamspec,
    windows,
)
from aximprint.acquire.auth import (
    Credential,
    CredentialError,
    resolve_credential,
    resolve_host,
)
from aximprint.acquire.client import (
    DEFAULT_TIMEOUT,
    Client,
    silence_insecure_tls_warnings,
)
from aximprint.acquire.errors import (
    AuthenticationError,
    UnauthorizedError,
    XtractAPIError,
    XtractError,
)
from aximprint.acquire.harvest import pipeline
from aximprint.acquire.harvest.zeek import (
    DEFAULT_ZEEK_IMAGE,
    PodmanZeekRunner,
    ZeekError,
    ZeekRunner,
)
from aximprint.acquire.timeutil import (
    NS_PER_SECOND,
    Instant,
    TimeUtilError,
    format_instant,
    parse_duration,
    parse_instant,
)

__all__ = ["main"]

# `xtract pcap`'s default output directory when `--out` is omitted — the
# current directory, same "write where you're invoked" default as most
# extraction CLIs.
_DEFAULT_OUT_DIR = "."

# `xtract harvest`'s default output root — deliberately not "." (unlike `pcap`'s), since harvest
# writes two durable subtrees (`spool/`, `zeek/`) rather than one flat directory of pcaps, and a
# stray `harvest` invocation in an arbitrary working directory should not scatter those into
# whatever else lives there.
_DEFAULT_HARVEST_OUT_DIR = "./harvest"

# A conservative, clearly-a-placeholder default spool cap — every real run should override this
# with the actual disk budget (`docs/PLAN_metadata_harvester.md` §5: 168 GB on the reference host).
_DEFAULT_SPOOL_CAP = "10GB"

# `--yes` (work order §2 Phase 4) raises `windows.plan`'s ceiling rather than
# reimplementing it — effectively unbounded, since the operator has already
# opted in explicitly.
_YES_MAX_WINDOWS = sys.maxsize

# SIGINT exit code: the conventional 128+SIGINT, distinct from the four
# documented pcap outcomes (0/1/2/3/4) — a run interrupted mid-flight isn't
# any of "succeeded", "some windows failed", "bad usage", or "auth/transport
# error", it's its own thing, and the printed message says how to resume.
_SIGINT_EXIT_CODE = 130


class UsageError(XtractError):
    """The invocation itself is unusable — exit `2`.

    Most usage errors are caught inline at the point they're detected and
    returned as `2` directly. This type exists for the ones raised deeper in
    the call stack (currently host resolution, which happens inside the
    client factory) where returning a code isn't possible; `_run_mapping_errors`
    maps it back to `2` so both routes agree.
    """

# --- argument parsing ---------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aximprint acquire", description="Read-only pcap extraction from an axapi appliance.")
    # `action="version"` short-circuits argparse's own parsing before the
    # `--host`/subcommand requirement checks run, so `xtract --version` works
    # standalone — no host, no credentials, no subcommand needed (packaging
    # work order §2 Phase 1: "a clean-venv install exposes ... xtract
    # --version reports the packaged version").
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--host",
        help=(
            "axapi host, e.g. https://appliance.example.com. Optional when the "
            "selected config profile sets `host`; an explicit --host overrides it."
        ),
    )
    parser.add_argument("--api-key-file", metavar="PATH", help="path to a file containing the API key")
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="config profile in ~/.config/aximprint/config.toml "
             "(falls back to the deprecated ~/.config/xtract/config.toml if the new "
             "location does not exist)",
    )
    parser.add_argument("--ca-bundle", metavar="PATH", help="path to a CA bundle for TLS verification")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification (dangerous — sends the bearer credential unverified)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    streams = subparsers.add_parser("streams", help="report per-stream data ranges")
    streams.add_argument(
        "--streams",
        metavar="SPEC",
        help="restrict to a stream_ids spec, e.g. '2-4' or '0,2..5' (default: every stream)",
    )
    streams.add_argument("--json", action="store_true", help="emit machine-readable JSON records")
    streams.add_argument("--gaps", action="store_true", help="include a per-stream gap breakdown")

    pcap = subparsers.add_parser("pcap", help="extract pcap windows over a time range, resumably")
    pcap.add_argument(
        "--streams",
        required=True,
        metavar="SPEC",
        help="stream_ids spec sent verbatim to /axapi/pcap, e.g. '2-4' or '0,2..5'",
    )
    pcap.add_argument("--start", required=True, metavar="TS", help="range start, a wire timestamp (epoch, optionally sub-second)")
    pcap.add_argument(
        "--end",
        metavar="TS",
        help="range end, a wire timestamp; omit to resolve to the union max of stored data across --streams",
    )
    pcap.add_argument("--interval", required=True, metavar="DUR", help="window size, e.g. '60s', '5m', '1h'")
    pcap.add_argument("--filter", metavar="BPF", help="BPF/community-ID filter passed verbatim to /axapi/pcap")
    pcap.add_argument("--out", metavar="DIR", default=_DEFAULT_OUT_DIR, help=f"output directory (default: {_DEFAULT_OUT_DIR!r})")
    pcap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the window plan and exit; makes no /axapi/pcap call (no credentials needed if --end is explicit)",
    )
    pcap.add_argument(
        "--yes",
        action="store_true",
        help="permit a window count above the default ceiling (docs/WORK_ORDER_windows.md)",
    )
    pcap.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop at the first failed window instead of continuing and reporting at the end",
    )
    pcap.add_argument(
        "--allow-manifest-mismatch",
        action="store_true",
        help="proceed even if --out's manifest was written by a run with different --streams/--filter/--interval",
    )

    harvest = subparsers.add_parser(
        "harvest",
        help="extract pcap windows, run Zeek over each, and delete the pcap once Zeek succeeds "
        "(or, with --from-pcaps, run only the Zeek stage over pcaps already on disk)",
    )
    # `--streams`/`--start`/`--interval` are required UNLESS `--from-pcaps` is given, in which case
    # they are REJECTED instead (D4, docs/WORK_ORDER_harvest_from_pcaps.md) — so `required=True`
    # is deliberately NOT used here; `_validate_harvest_flags` enforces both directions.
    harvest.add_argument(
        "--streams", metavar="SPEC", default=None, help="stream_ids spec sent verbatim to /axapi/pcap, e.g. '2-4' or '0,2..5'"
    )
    harvest.add_argument(
        "--start",
        default=None,
        metavar="EXPR",
        help="range start, a timespec expression: an absolute epoch, 'now', '<duration> ago'/'back from now'",
    )
    harvest.add_argument(
        "--end",
        metavar="EXPR",
        help="range end, a timespec expression (also accepts '<duration> from the start'); "
        "omit to resolve to the union max of stored data across --streams",
    )
    harvest.add_argument("--interval", default=None, metavar="DUR", help="window size, e.g. '300s', '5m'")
    harvest.add_argument(
        "--from-pcaps",
        metavar="SRC",
        default=None,
        help="run only the Zeek stage, over pcaps already on disk at SRC (a directory of *.pcap), "
        "instead of extracting from the appliance; touches no network, needs no --host/credentials "
        "or any other appliance flag; incompatible with --streams/--start/--end/--interval/"
        "--filter/--concurrency/--spool-cap/--host/--profile/--api-key-file/--ca-bundle/--insecure "
        "(docs/WORK_ORDER_harvest_from_pcaps.md); an <epoch>.pcap-shaped filename (pcap's own "
        "output naming) makes the resulting <out>/zeek/<id>/ read downstream as a window tree — "
        "any other safe filename is still accepted and Zeeked, just noted as reading as a flat "
        "directory instead",
    )
    harvest.add_argument(
        "--out",
        metavar="DIR",
        default=_DEFAULT_HARVEST_OUT_DIR,
        help=f"output root — pcaps spool under <out>/spool, Zeek logs land under <out>/zeek "
        f"(default: {_DEFAULT_HARVEST_OUT_DIR!r})",
    )
    # `--spool-cap`/`--concurrency` default to `None` (not their ordinary defaults, resolved below
    # in `cmd_harvest`) so an operator who explicitly types the SAME value as the default is still
    # distinguishable, for D4 rejection purposes, from one who never typed the flag at all.
    harvest.add_argument(
        "--spool-cap",
        metavar="SIZE",
        default=None,
        help=f"pcap spool disk cap, e.g. '50GB', '168000000000' (default: {_DEFAULT_SPOOL_CAP!r})",
    )
    harvest.add_argument(
        "--concurrency",
        type=int,
        metavar="N",
        default=None,
        help=f"concurrent extract workers (default: {pipeline.DEFAULT_EXTRACT_CONCURRENCY})",
    )
    harvest.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        default=None,
        help="per-request HTTP timeout in seconds (default: "
             f"{DEFAULT_TIMEOUT}). Raise it when a single --interval window is large enough "
             "that the appliance cannot assemble and return it inside the default: a busy "
             "capture makes the WINDOW big, not just the total, and the read then times out "
             "no matter how small the overall span is",
    )
    harvest.add_argument(
        "--zeek-workers",
        type=int,
        metavar="M",
        default=pipeline.DEFAULT_ZEEK_WORKERS,
        help=f"concurrent Zeek containers (default: {pipeline.DEFAULT_ZEEK_WORKERS})",
    )
    harvest.add_argument(
        "--zeek-image", metavar="REF", default=DEFAULT_ZEEK_IMAGE, help=f"pinned Zeek container image (default: {DEFAULT_ZEEK_IMAGE!r})"
    )
    harvest.add_argument("--filter", metavar="BPF", help="BPF/community-ID filter passed verbatim to /axapi/pcap")
    harvest.add_argument(
        "--dry-run",
        action="store_true",
        help="print the harvest plan and exit; makes no /axapi/pcap call and runs no Zeek container "
        "(no credentials needed if --end is explicit, or if --from-pcaps is given)",
    )
    harvest.add_argument(
        "--yes",
        action="store_true",
        help="permit a window count above the default ceiling (with --from-pcaps: a source "
        "directory holding more than the ceiling's worth of *.pcap files)",
    )
    harvest.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop admitting new work at the first failed window instead of continuing and reporting at the end",
    )

    return parser


# --- the real client factory (production default) ----------------------------


def _resolve_host_or_fail(args: argparse.Namespace) -> str:
    """`--host` if given, else the selected profile's `host`.

    An explicit flag always wins, so a profile can be reused against a
    standby appliance without editing the config. When neither supplies one
    the error names both routes — argparse's own "required" message could
    only ever mention the flag, which is why `--host` is not marked required.
    """
    if args.host:
        return args.host
    host = resolve_host(profile=args.profile)
    if host:
        return host
    raise UsageError(
        "no host: pass --host, or set `host` in the config profile "
        "(~/.config/aximprint/config.toml, or the deprecated "
        "~/.config/xtract/config.toml, under [profile.<name>])"
    )


def _default_client_factory(args: argparse.Namespace) -> AbstractContextManager:
    host = _resolve_host_or_fail(args)
    if args.insecure:
        # State the risk once, plainly, then silence the per-request repeats —
        # see `client.silence_insecure_tls_warnings`.
        silence_insecure_tls_warnings()
        print(
            "aximprint acquire: warning — TLS verification disabled (--insecure); the credential "
            "is sent over an unverified channel",
            file=sys.stderr,
        )
    credential: Credential = resolve_credential(api_key_file=args.api_key_file, profile=args.profile)
    kwargs = {}
    timeout = getattr(args, "timeout", None)
    if timeout is not None:
        kwargs["timeout"] = timeout
    return Client(
        host,
        credential,
        ca_bundle=args.ca_bundle,
        verify=not args.insecure,
        **kwargs,
    )



def _api_error_detail(exc: XtractAPIError) -> str:
    """`str(exc)`, plus the appliance's own error envelope when it carried one (GAP-L2).

    `axapi` returns a structured body — `{"errors": {"reason": ..., "message": ...}}` — that is
    consistently more specific than the status word the exception is named for. Appended rather
    than substituted: the exception's own message names the operation that failed, which the
    appliance's message does not.

    Deliberately tolerant of shape. A body that is absent, not a dict, or missing the keys is
    simply not appended — a diagnostic helper must never itself raise while reporting a failure.
    """
    detail = str(exc)
    errors = (exc.body or {}).get("errors")
    if not isinstance(errors, dict):
        return detail
    parts = [str(errors[k]) for k in ("reason", "message") if errors.get(k)]
    return f"{detail} ({' — '.join(parts)})" if parts else detail

def _build_client_cm(args: argparse.Namespace, client_factory) -> AbstractContextManager | int:
    """Build the client context manager via `client_factory` (the real one if none was injected),
    mapping the two ways doing so can fail to their exit codes. Shared by `main()`'s ordinary
    dispatch and `_dispatch_harvest` so this mapping is written once.
    """
    factory = client_factory if client_factory is not None else _default_client_factory
    try:
        return factory(args)
    except UsageError as exc:
        # Host resolution happens inside the factory (it is only needed when a client is actually
        # built), so its usage failure surfaces here — checked before CredentialError: a missing
        # host is a usage problem, not an auth one.
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2
    except CredentialError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 3


def _default_zeek_runner_factory(args: argparse.Namespace) -> ZeekRunner:
    """`xtract harvest`'s real default: `PodmanZeekRunner` pinned to `--zeek-image`. Tests inject
    a `FakeZeekRunner`-returning factory through `main(..., zeek_runner_factory=...)`, the same
    seam shape as `client_factory` — no test here ever invokes `podman`.
    """
    return PodmanZeekRunner(args.zeek_image)


# --- formatting helpers --------------------------------------------------------


def _human_utc(instant: Instant) -> str:
    seconds = instant.ns // NS_PER_SECOND
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_duration_ns(ns: int) -> str:
    """Exact, no-float duration display: `%d.%09ds` when there's a
    sub-second remainder, else a bare `%ds` — same discipline as
    `timeutil.format_instant`.
    """
    seconds, remainder = divmod(ns, NS_PER_SECOND)
    if remainder:
        return f"{seconds}.{remainder:09d}s"
    return f"{seconds}s"


def _record_for_json(rng: catalog.StreamRange, *, include_gap_breakdown: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stream": rng.stream,
        "first": format_instant(rng.first) if rng.first is not None else None,
        "first_utc": _human_utc(rng.first) if rng.first is not None else None,
        "last": format_instant(rng.last) if rng.last is not None else None,
        "last_utc": _human_utc(rng.last) if rng.last is not None else None,
        "duration_ns": (rng.last.ns - rng.first.ns) if (rng.first is not None and rng.last is not None) else None,
        "files": rng.file_count,
        "bytes": rng.total_bytes,
        "gaps": {"count": rng.gaps.count},
    }
    if include_gap_breakdown:
        record["gaps"]["total_duration_ns"] = int(rng.gaps.total_duration)
        record["gaps"]["largest_duration_ns"] = int(rng.gaps.largest) if rng.gaps.largest is not None else None
    return record


def _human_bytes(n: int) -> str:
    """`2277843753946` -> `'2.28 TB'`.

    **Decimal** units (1000-based), not binary, deliberately: the appliance
    reports its own capacity in decimal TB ("20.538 TB of 20.958 TB
    available"), so an operator comparing `xtract streams` against the box's
    own figures gets numbers that reconcile instead of ones 10% adrift.
    The exact byte count is always available via `--json`.
    """
    if n < 1000:
        return f"{n} B"
    value = float(n)
    for unit in ("kB", "MB", "GB", "TB", "PB"):
        value /= 1000.0
        if value < 1000.0 or unit == "PB":
            return f"{value:.2f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def _stream_sort_key(stream: str) -> tuple[int, int, str]:
    """Sort `stream10` after `stream9`, and keep unparseable names last but
    still deterministically ordered.

    The appliance returns streams in arbitrary order (observed: stream08,
    stream04, stream10, ...), which makes the table tedious to scan and two
    runs hard to diff.
    """
    try:
        return (0, catalog.stream_id_for_name(stream), stream)
    except catalog.CatalogError:
        return (1, 0, stream)


def _print_json(records: list[catalog.StreamRange], *, include_gap_breakdown: bool) -> None:
    payload = [_record_for_json(rng, include_gap_breakdown=include_gap_breakdown) for rng in records]
    print(jsonlib.dumps(payload, indent=2))


def _print_table(records: list[catalog.StreamRange], *, include_gap_breakdown: bool) -> None:
    headers = ["stream", "first", "first (utc)", "last", "last (utc)", "duration", "files", "bytes", "gaps"]
    rows: list[list[str]] = []
    for rng in records:
        if rng.first is None or rng.last is None:
            rows.append([rng.stream, "-", "-", "-", "-", "-", str(rng.file_count), _human_bytes(rng.total_bytes), str(rng.gaps.count)])
            continue
        duration_ns = rng.last.ns - rng.first.ns
        rows.append(
            [
                rng.stream,
                format_instant(rng.first),
                _human_utc(rng.first),
                format_instant(rng.last),
                _human_utc(rng.last),
                _format_duration_ns(duration_ns),
                str(rng.file_count),
                _human_bytes(rng.total_bytes),
                str(rng.gaps.count),
            ]
        )

    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i]) for i in range(len(headers))]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))

    if include_gap_breakdown:
        for rng in records:
            if rng.gaps.count == 0:
                continue
            print(
                f"stream {rng.stream}: {rng.gaps.count} gap(s), "
                f"total {_format_duration_ns(int(rng.gaps.total_duration))}, "
                f"largest {_format_duration_ns(int(rng.gaps.largest))}"
            )


# --- the `streams` command -----------------------------------------------------


def _select_listings(
    listings: list[catalog.StreamListing], spec: str | None
) -> list[catalog.StreamListing] | int:
    """Resolve which of `listings` `--streams <spec>` selects.

    Returns the selected listings, sorted by numeric stream id, or an `int`
    exit code (already printed to stderr) on a usage error: a malformed spec
    (`2`) or a spec naming a stream id absent from `listings` (`2`) — a clear
    message, never a traceback, per the work order.
    """
    if spec is None:
        return listings

    try:
        streamspec.validate(spec)
        target_ids = set(streamspec.expand(spec))
    except streamspec.StreamSpecError as exc:
        print(f"aximprint acquire: invalid --streams spec: {exc}", file=sys.stderr)
        return 2

    id_to_listing: dict[int, catalog.StreamListing] = {}
    for listing in listings:
        try:
            sid = catalog.stream_id_for_name(listing.name)
        except catalog.StreamIdMappingError:
            # Not resolvable under the current (Q21-unresolved) assumption —
            # can't be a target of a numeric --streams spec either way.
            continue
        id_to_listing[sid] = listing

    unknown = sorted(target_ids - id_to_listing.keys())
    if unknown:
        print(
            "aximprint acquire: unknown stream id(s) not present on the appliance: "
            + ", ".join(str(i) for i in unknown),
            file=sys.stderr,
        )
        return 2

    return [id_to_listing[sid] for sid in sorted(target_ids)]


def cmd_streams(args: argparse.Namespace, client: Client) -> int:
    try:
        listings = catalog.list_streams(client)
    except catalog.CatalogError as exc:
        print(f"aximprint acquire: error reading stream catalog: {exc}", file=sys.stderr)
        return 4

    selected = _select_listings(listings, args.streams)
    if isinstance(selected, int):
        return selected

    records: list[catalog.StreamRange] = []
    for listing in selected:
        try:
            records.append(catalog.stream_range(client, listing.name))
        except catalog.CatalogError as exc:
            print(f"aximprint acquire: error reading stream {listing.name!r}: {exc}", file=sys.stderr)
            return 4

    records.sort(key=lambda r: _stream_sort_key(r.stream))

    if args.json:
        _print_json(records, include_gap_breakdown=args.gaps)
    else:
        _print_table(records, include_gap_breakdown=args.gaps)
    return 0


# --- the `pcap` command ----------------------------------------------------------


def _pcap_needs_client(args: argparse.Namespace) -> bool:
    """`xtract pcap --dry-run` with an explicit `--end` never touches the
    network (work order §2 Phase 4: "with an explicit --end it requires no
    credentials at all") — every other combination does (resolving an
    omitted `--end` reads the catalog; a real run extracts).
    """
    return not (args.dry_run and args.end is not None)


def _print_pcap_dry_run(
    plan_windows: list[windows.Window],
    *,
    end_explicit: bool,
    end_warning: str | None,
    decisions: list[runner.WindowDecision],
    out_dir: str,
) -> None:
    skip_count = sum(1 for d in decisions if d.skip)
    print("aximprint acquire pcap: dry run — no /axapi/pcap calls made")
    print(f"  out:      {out_dir}")
    print(f"  manifest: {manifest.manifest_path(out_dir)}")
    if end_warning:
        print(f"  warning:  {end_warning}")
    if not plan_windows:
        print("  windows:  0 (empty range)")
        return
    first, last = plan_windows[0], plan_windows[-1]
    total_duration_ns = last.end.ns - first.start.ns
    print(f"  end:      {'explicit' if end_explicit else 'resolved from stored data (union max)'}")
    print(f"  windows:  {len(plan_windows)} ({skip_count} already done, {len(plan_windows) - skip_count} to run)")
    print(f"  first:    {format_instant(first.start)} .. {format_instant(first.end)}")
    print(f"  last:     {format_instant(last.start)} .. {format_instant(last.end)}")
    print(f"  total duration: {_format_duration_ns(total_duration_ns)}")


def _print_pcap_progress(window: windows.Window, event: str, detail: str) -> None:
    start_str = window.wire_params()["start_time"]
    prefix = f"[{window.index}/{window.total}] {start_str}"
    if event == "skip":
        print(f"{prefix}: skip ({detail})")
    elif event == "written":
        print(f"{prefix}: written")
    elif event == "reextracted":
        print(f"{prefix}: re-extracted (previous record covered a shorter window)")
    elif event == "empty":
        print(f"{prefix}: empty ({detail})")
    elif event == "failed":
        print(f"{prefix}: FAILED ({detail})")
    elif event == "interrupted":
        print(f"{prefix}: interrupted")


def _print_pcap_summary(summary: runner.RunSummary) -> None:
    print(
        "aximprint acquire pcap: "
        f"written={summary.written} reextracted={summary.reextracted} empty={summary.empty} "
        f"failed={summary.failed} skipped={summary.skipped} bytes={summary.total_bytes} "
        f"elapsed={_format_duration_ns(int(summary.elapsed_s * 1_000_000_000))}"
    )
    if summary.failed_windows:
        print("  failed windows: " + ", ".join(summary.failed_windows), file=sys.stderr)


def cmd_pcap(args: argparse.Namespace, client: Client | None) -> int:
    try:
        streamspec.validate(args.streams)
    except streamspec.StreamSpecError as exc:
        print(f"aximprint acquire: invalid --streams spec: {exc}", file=sys.stderr)
        return 2

    try:
        start = parse_instant(args.start)
    except TimeUtilError as exc:
        print(f"aximprint acquire: invalid --start: {exc}", file=sys.stderr)
        return 2

    try:
        interval = parse_duration(args.interval)
    except TimeUtilError as exc:
        print(f"aximprint acquire: invalid --interval: {exc}", file=sys.stderr)
        return 2

    filter_ = args.filter or None
    out_dir = args.out

    end_explicit = args.end is not None
    end_warning: str | None = None
    if end_explicit:
        try:
            end = parse_instant(args.end)
        except TimeUtilError as exc:
            print(f"aximprint acquire: invalid --end: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            stream_ids = streamspec.expand(args.streams)
        except streamspec.StreamSpecError as exc:
            print(f"aximprint acquire: invalid --streams spec: {exc}", file=sys.stderr)
            return 2
        try:
            resolved = runner.resolve_end(client, stream_ids, interval)
        except runner.AllStreamsEmptyError as exc:
            print(f"aximprint acquire: {exc}", file=sys.stderr)
            return 4
        except catalog.CatalogError as exc:
            print(f"aximprint acquire: error resolving --end from the stream catalog: {exc}", file=sys.stderr)
            return 4
        end = resolved.end
        end_warning = resolved.warning
        if end_warning:
            print(f"aximprint acquire: warning: {end_warning}", file=sys.stderr)

    max_windows = _YES_MAX_WINDOWS if args.yes else windows.DEFAULT_MAX_WINDOWS
    try:
        plan = windows.plan(start, end, interval, max_windows=max_windows)
    except windows.WindowsError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2

    # The collision guard runs *before* `plan_resume`, in both this path and
    # the `--dry-run` path below — never after, and never skipped under
    # `--dry-run`. A `--dry-run` against a foreign manifest (different
    # --streams/--filter/--interval) must refuse (or warn, under the
    # override) exactly like a real run would, not silently report resume
    # counts computed against files that don't belong to this invocation.
    try:
        mismatches = runner.check_collision(
            out_dir,
            streams=args.streams,
            filter=filter_,
            interval=interval,
            override=args.allow_manifest_mismatch,
        )
    except runner.ManifestCollisionError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2
    if mismatches:
        print("aximprint acquire: warning: proceeding despite manifest mismatch: " + "; ".join(mismatches), file=sys.stderr)

    decisions = runner.plan_resume(out_dir, plan)

    if args.dry_run:
        _print_pcap_dry_run(plan, end_explicit=end_explicit, end_warning=end_warning, decisions=decisions, out_dir=out_dir)
        return 0

    runner.ensure_manifest_header(
        out_dir, streams=args.streams, filter=filter_, interval=interval, tool_version=__version__
    )

    run_id = runner.run_id_for(args.streams, filter_, interval)
    summary = runner.execute(
        client,
        out_dir=out_dir,
        windows=plan,
        decisions=decisions,
        streams=args.streams,
        filter=filter_,
        run_id=run_id,
        fail_fast=args.fail_fast,
        on_window=_print_pcap_progress,
    )

    _print_pcap_summary(summary)
    if summary.interrupted:
        print(
            "aximprint acquire: interrupted — re-run the same command (same --streams/--filter/--interval/--out) to resume",
            file=sys.stderr,
        )
        return _SIGINT_EXIT_CODE
    if summary.failed:
        return 1
    return 0


# --- the `harvest` command -------------------------------------------------------

# Decimal (1000-based) units, same convention as `_human_bytes` above — a value an operator
# copies from `xtract streams`' own output means the same number of bytes here.
_SIZE_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}
_SIZE_RE = re.compile(r"^(?P<value>\d+)\s*(?P<unit>[a-zA-Z]*)$")


def _parse_size(s: str) -> int:
    """Parse a `--spool-cap`-style size string: a bare integer (bytes) or an integer with a
    B/KB/MB/GB/TB suffix, case-insensitive. Raises `ValueError` (caught inline in `cmd_harvest`,
    like every other malformed-flag check in this module) rather than a typed `xtract` error —
    this is pure string parsing local to the CLI, not a fact about the appliance or the pipeline.
    """
    match = _SIZE_RE.fullmatch(s.strip())
    if match is None:
        raise ValueError(f"not a valid size: {s!r} — expected e.g. '50GB', '500MB', '168000000000'")
    unit = (match.group("unit") or "b").lower()
    if unit not in _SIZE_UNITS:
        raise ValueError(f"unknown size unit {match.group('unit')!r} in {s!r} — expected one of B/KB/MB/GB/TB")
    value = int(match.group("value"))
    if value <= 0:
        raise ValueError(f"size must be positive, got {s!r}")
    return value * _SIZE_UNITS[unit]


def _harvest_needs_client(args: argparse.Namespace) -> bool:
    """`xtract harvest --dry-run` with an explicit `--end` never touches the network — same
    reasoning as `_pcap_needs_client`. `--from-pcaps` NEVER touches the network, dry-run or not:
    it reads a local directory of pcaps and makes no `/axapi/pcap` call at any point (build step
    1, `docs/WORK_ORDER_harvest_from_pcaps.md`) — checked first, since the `--end`-driven
    reasoning below does not apply to a mode that rejects `--end` outright (D4).
    """
    if args.from_pcaps is not None:
        return False
    return not (args.dry_run and args.end is not None)


def _resolve_harvest_plan(config: pipeline.HarvestConfig, client, now: Instant) -> pipeline.HarvestPlan | int:
    """`pipeline.plan_harvest`, with its failure modes mapped to exit codes — shared by the
    `--dry-run` and real-run paths in `cmd_harvest` so the plan is computed (and any catalog call
    it requires is made) exactly once per invocation, never twice.
    """
    try:
        return pipeline.plan_harvest(config, client=client, now=now)
    except pipeline.PlanNotComputableError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2
    except runner.AllStreamsEmptyError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 4
    except catalog.CatalogError as exc:
        print(f"aximprint acquire: error resolving --end from the stream catalog: {exc}", file=sys.stderr)
        return 4


def _print_harvest_dry_run(harvest_plan: pipeline.HarvestPlan, *, config: pipeline.HarvestConfig) -> None:
    print("aximprint acquire harvest: dry run — no /axapi/pcap calls made, no Zeek container run")
    print(f"  out:          {config.out_dir}")
    print(f"  spool cap:    {_human_bytes(config.spool_cap_bytes)}")
    print(f"  concurrency:  {config.extract_concurrency} extract / {config.zeek_workers} zeek")
    if not harvest_plan.windows:
        print("  windows:      0 (empty range)")
        return
    first, last = harvest_plan.windows[0], harvest_plan.windows[-1]
    total_duration_ns = last.end.ns - first.start.ns
    est_total = harvest_plan.seed_bytes * len(harvest_plan.windows)
    print(f"  windows:      {len(harvest_plan.windows)}")
    print(f"  first:        {format_instant(first.start)} .. {format_instant(first.end)}")
    print(f"  last:         {format_instant(last.start)} .. {format_instant(last.end)}")
    print(f"  total span:   {_format_duration_ns(total_duration_ns)}")
    print(f"  est. bytes/window: {_human_bytes(harvest_plan.seed_bytes)}")
    print(f"  est. total:        {_human_bytes(est_total)}")
    print(f"  spool headroom (one window): {_human_bytes(config.spool_cap_bytes - harvest_plan.seed_bytes)}")


def _print_harvest_progress(window_id: str, event: str, detail: str) -> None:
    prefix = f"[{window_id}]"
    if event == "extracted":
        print(f"{prefix}: extracted")
    elif event == "empty":
        print(f"{prefix}: empty ({detail})")
    elif event == "extract_failed":
        print(f"{prefix}: EXTRACT FAILED ({detail})")
    elif event == "zeeked":
        print(f"{prefix}: zeek ok — released")
    elif event == "zeeked_kept":
        # `--from-pcaps` only (must-fix 1, docs/WORK_ORDER_harvest_from_pcaps.md): the pcap was
        # never admitted anywhere to release in the first place — "released" would claim space
        # was reclaimed when none was.
        print(f"{prefix}: zeek ok — source pcap retained")
    elif event == "zeek_failed":
        print(f"{prefix}: ZEEK FAILED — pcap retained for retry ({detail})")


def _print_harvest_summary(report: pipeline.RunReport) -> None:
    print(
        "aximprint acquire harvest: "
        f"planned={report.planned} extracted={report.extracted} empty={report.empty} "
        f"extract_failed={report.extract_failed} zeeked={report.zeeked} zeek_failed={report.zeek_failed} "
        f"released={report.released} reconciled_orphans={report.reconciled_orphans} "
        f"bytes={report.bytes_moved} peak_spool={_human_bytes(report.peak_spool_bytes)} "
        f"elapsed={_format_duration_ns(int(report.elapsed_s * 1_000_000_000))}"
    )
    if report.unrecognized_stems:
        print("  unrecognized zeek log stems: " + ", ".join(report.unrecognized_stems), file=sys.stderr)
    if report.failed_windows:
        print("  extraction failures: " + ", ".join(report.failed_windows), file=sys.stderr)
    if report.zeek_failed_windows:
        print("  zeek failures (pcap retained): " + ", ".join(report.zeek_failed_windows), file=sys.stderr)


# --- `harvest --from-pcaps`: D4's rejection set (docs/WORK_ORDER_harvest_from_pcaps.md, extended
# to twelve flags by ruling B) -----------------------------------------------------------------
#
# The original seven extraction-stage flags, plus five credential/transport flags: `--from-pcaps`
# never builds a client at all (`_harvest_needs_client`), so none of `--host`/`--profile`/
# `--api-key-file`/`--ca-bundle`/`--insecure` has a receiver either (LES-021).
#
# Split into two tables by "supplied" test, each matching the flag's OWN existing semantics
# elsewhere in this module/`auth.py`/`client.py` — never a blanket `is not None` (should-fix 8):
# `--filter`/`--host`/`--profile` are already treated as unset-when-EMPTY by the code that
# actually reads them (`args.filter or None` in `cmd_harvest` below, `if args.host:` in
# `_resolve_host_or_fail`, `profile or config.get("default_profile")` in `auth.resolve_host`), so
# an `is not None` test here would report an operator-meant-nothing `--filter ''` as "supplied"
# and reject a run that should be allowed — the same root mistake as must-fix 3's
# `--from-pcaps ""`, just aimed at this check instead of the source path. `--api-key-file`/
# `--ca-bundle` go the OTHER way (`auth.resolve_credential`'s `if api_key_file is not None:`,
# `client.Client.__init__`'s `if ca_bundle is not None:`) — an empty string IS meaningfully
# "supplied" for those two, so `is not None` is the correct test for them specifically, not a
# shortcut. Value flags default to `None` at the parser level (see `_build_parser`) specifically
# so "the operator typed it" is distinguishable from "it holds its own ordinary default" —
# `--concurrency`/`--spool-cap` in particular have real, non-`None` defaults in the ordinary
# (non-`--from-pcaps`) path, resolved in `cmd_harvest` AFTER this check runs.
_FROM_PCAPS_REJECTED_VALUE_FLAGS: tuple[tuple[str, str], ...] = (
    ("--streams", "streams"),
    ("--start", "start"),
    ("--end", "end"),
    ("--interval", "interval"),
    ("--concurrency", "concurrency"),
    ("--spool-cap", "spool_cap"),
    ("--api-key-file", "api_key_file"),
    ("--ca-bundle", "ca_bundle"),
)
# Truthy-tested: each already treated as unset-when-falsy by the code that actually reads it (see
# the block comment above). `--insecure` (a `store_true` flag — no value argument, default
# `False`, so there is no "typed the same value as the default" ambiguity `--concurrency 1` has to
# resolve) belongs here too, trivially: its only two states already ARE "supplied"/"not".
_FROM_PCAPS_REJECTED_TRUTHY_FLAGS: tuple[tuple[str, str], ...] = (
    ("--filter", "filter"),
    ("--host", "host"),
    ("--profile", "profile"),
    ("--insecure", "insecure"),
)


def _from_pcaps_flag_conflicts(args: argparse.Namespace) -> list[str]:
    """Which of the twelve D4-rejected flags were actually supplied alongside `--from-pcaps` —
    declaration order (value flags, then truthy flags), not a `set`, so the reported order is
    deterministic run to run (CONVENTIONS §3).
    """
    conflicts = [flag for flag, attr in _FROM_PCAPS_REJECTED_VALUE_FLAGS if getattr(args, attr) is not None]
    conflicts += [flag for flag, attr in _FROM_PCAPS_REJECTED_TRUTHY_FLAGS if getattr(args, attr)]
    return conflicts


def _validate_harvest_flags(args: argparse.Namespace) -> str | None:
    """The full "required without, refused with" rule for `--from-pcaps` in one place
    (should-fix 7), plus the one precondition both modes share (`--zeek-workers` — previously
    checked separately, identically, in both `cmd_harvest`'s ordinary branch and
    `_cmd_harvest_from_pcaps`). Replaces what used to be two halves
    (`_harvest_missing_required_flags`/the inline D4 check in `cmd_harvest`) joined only by a
    comment asserting an invariant across a function boundary.

    Returns the ready-to-print usage-error message (without the `"aximprint acquire: "` prefix —
    the caller adds that), or `None` if `args` is valid for whichever mode `--from-pcaps` selects.
    Called once, from `_dispatch_harvest`, before any client or zeek runner is touched —
    `cmd_harvest`/`_cmd_harvest_from_pcaps` trust this has already run and never re-check any of
    it themselves.
    """
    if args.zeek_workers < 1:
        return f"--zeek-workers must be positive, got {args.zeek_workers}"

    if args.from_pcaps is not None:
        conflicts = _from_pcaps_flag_conflicts(args)
        if not conflicts:
            return None
        noun, verb = ("flag", "has") if len(conflicts) == 1 else ("flags", "have")
        return (
            f"--from-pcaps is incompatible with the following {noun}, which {verb} no receiver "
            f"once it replaces the extraction stage: {', '.join(conflicts)} "
            "(docs/WORK_ORDER_harvest_from_pcaps.md D4)"
        )

    missing = [flag for flag, attr in (("--streams", "streams"), ("--start", "start"), ("--interval", "interval")) if getattr(args, attr) is None]
    if missing:
        return f"the following arguments are required: {', '.join(missing)}"
    return None


def _print_from_pcaps_dry_run(plan: pipeline.FromPcapsPlan, *, config: pipeline.FromPcapsConfig) -> None:
    print("aximprint acquire harvest: dry run (--from-pcaps) — no Zeek container run")
    print(f"  source:       {config.source}")
    print(f"  out:          {config.out_dir}")
    print(f"  zeek workers: {config.zeek_workers}")
    print(f"  windows:      {len(plan.windows)}")
    if plan.windows:
        first_id, _first_path = plan.windows[0]
        last_id, _last_path = plan.windows[-1]
        print(f"  first:        {first_id}")
        print(f"  last:         {last_id}")


def _print_non_epoch_shaped_note(plan: pipeline.FromPcapsPlan) -> None:
    """Ruling C: informational only, printed for both `--dry-run` and a real run, never a reason
    to reject (`plan_from_pcaps` already accepted every one of these `window_id`s).
    """
    ids = plan.non_epoch_shaped_window_ids
    sample = ", ".join(ids[:3]) + (", ..." if len(ids) > 3 else "")
    print(
        f"aximprint acquire: note: {len(ids)} of {len(plan.windows)} window id(s) are not "
        f"epoch-shaped ({sample}) — the ingest side detects a window tree by directory-name shape "
        "(docs/WORK_ORDER_multi_window_log_read.md); a non-epoch-shaped <out>/zeek/<id>/ subtree "
        "reads downstream as a flat directory of logs, not as part of a window tree",
        file=sys.stderr,
    )


def _cmd_harvest_from_pcaps(
    args: argparse.Namespace, zeek_runner_factory: Callable[[argparse.Namespace], ZeekRunner]
) -> int:
    """`cmd_harvest`'s `--from-pcaps` branch: `_validate_harvest_flags` has already run, from
    `_dispatch_harvest`, before this is reached — D4's rejection set and `--zeek-workers`
    positivity are both already guaranteed. No client is ever passed in — `_harvest_needs_client`
    already keeps `_dispatch_harvest` from building one for this mode.
    """
    config = pipeline.FromPcapsConfig(
        source=args.from_pcaps,  # NOT Path(...)-wrapped here — see FromPcapsConfig's own docstring
        out_dir=Path(args.out),
        zeek_workers=args.zeek_workers,
        fail_fast=args.fail_fast,
        yes=args.yes,
    )

    try:
        plan = pipeline.plan_from_pcaps(config)
    except pipeline.FromPcapsSourceError as exc:
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2

    if plan.non_epoch_shaped_window_ids:
        _print_non_epoch_shaped_note(plan)

    if args.dry_run:
        _print_from_pcaps_dry_run(plan, config=config)
        return 0

    try:
        zeek_runner = zeek_runner_factory(args)
    except ZeekError as exc:
        print(f"aximprint acquire: invalid --zeek-image: {exc}", file=sys.stderr)
        return 2

    report = pipeline.run_harvest_from_pcaps(config, plan=plan, zeek_runner=zeek_runner, on_event=_print_harvest_progress)

    _print_harvest_summary(report)
    if report.interrupted:
        print(
            "aximprint acquire: interrupted — re-run the same command to resume; --from-pcaps has "
            "no per-window skip, so windows already zeeked successfully simply run again",
            file=sys.stderr,
        )
        return _SIGINT_EXIT_CODE
    if report.failed:
        return 1
    return 0


def cmd_harvest(
    args: argparse.Namespace,
    client: Client | None,
    zeek_runner_factory: Callable[[argparse.Namespace], ZeekRunner],
) -> int:
    # `_dispatch_harvest` already ran `_validate_harvest_flags` — D4's rejection set (with
    # `--from-pcaps`), `--streams`/`--start`/`--interval` all non-None (without it), and
    # `--zeek-workers` positivity either way. Nothing here re-checks any of it; this function's
    # only remaining job is to branch on `args.from_pcaps` and do the rest of its own mode's
    # flag-specific work (`--interval`/`--spool-cap`/`--concurrency` parsing below apply only to
    # the ordinary path, since `--from-pcaps` already rejected all three as flags).
    if args.from_pcaps is not None:
        return _cmd_harvest_from_pcaps(args, zeek_runner_factory)

    try:
        streamspec.validate(args.streams)
    except streamspec.StreamSpecError as exc:
        print(f"aximprint acquire: invalid --streams spec: {exc}", file=sys.stderr)
        return 2

    try:
        interval = parse_duration(args.interval)
    except TimeUtilError as exc:
        print(f"aximprint acquire: invalid --interval: {exc}", file=sys.stderr)
        return 2

    spool_cap_str = args.spool_cap if args.spool_cap is not None else _DEFAULT_SPOOL_CAP
    try:
        spool_cap_bytes = _parse_size(spool_cap_str)
    except ValueError as exc:
        print(f"aximprint acquire: invalid --spool-cap: {exc}", file=sys.stderr)
        return 2

    concurrency = args.concurrency if args.concurrency is not None else pipeline.DEFAULT_EXTRACT_CONCURRENCY
    if concurrency < 1:
        print(f"aximprint acquire: --concurrency must be positive, got {concurrency}", file=sys.stderr)
        return 2
    # `--zeek-workers` positivity is `_validate_harvest_flags`'s job now (should-fix 11: it was
    # duplicated here and in `_cmd_harvest_from_pcaps` — folded into the one shared check
    # `_dispatch_harvest` runs before either branch).

    config = pipeline.HarvestConfig(
        streams=args.streams,
        start_expr=args.start,
        end_expr=args.end,
        interval=interval,
        out_dir=Path(args.out),
        spool_cap_bytes=spool_cap_bytes,
        filter=args.filter or None,
        extract_concurrency=concurrency,
        zeek_workers=args.zeek_workers,
        yes=args.yes,
        fail_fast=args.fail_fast,
    )

    # Resolved once, here, and threaded through — never read from the clock again for this run
    # (`timespec.py`'s own discipline: "a single run can't resolve `now` twice and get two
    # answers"). `time.time_ns()` is stdlib-integer nanoseconds, never a float (LES-045's pattern).
    now = Instant(time.time_ns(), True)

    plan_or_code = _resolve_harvest_plan(config, client, now)
    if isinstance(plan_or_code, int):
        return plan_or_code
    harvest_plan = plan_or_code

    if args.dry_run:
        _print_harvest_dry_run(harvest_plan, config=config)
        return 0

    try:
        zeek_runner = zeek_runner_factory(args)
    except ZeekError as exc:
        print(f"aximprint acquire: invalid --zeek-image: {exc}", file=sys.stderr)
        return 2

    report = pipeline.run_harvest(
        config, plan=harvest_plan, client=client, zeek_runner=zeek_runner, on_event=_print_harvest_progress
    )

    _print_harvest_summary(report)
    if report.interrupted:
        print(
            "aximprint acquire: interrupted — re-run the same command (same --streams/--filter/--interval/--out) to resume "
            "(pcaps already landed are re-driven through Zeek automatically on the next run)",
            file=sys.stderr,
        )
        return _SIGINT_EXIT_CODE
    if report.failed:
        return 1
    return 0


# --- entry point ---------------------------------------------------------------

_COMMANDS: dict[str, Callable[[argparse.Namespace, Client | None], int]] = {
    "streams": cmd_streams,
    "pcap": cmd_pcap,
}


def _dispatch_harvest(
    args: argparse.Namespace,
    client_factory: Callable[[argparse.Namespace], AbstractContextManager] | None,
    zeek_runner_factory: Callable[[argparse.Namespace], ZeekRunner] | None,
) -> int:
    """`harvest`'s own dispatch, parallel to `main()`'s handling of `pcap`/`streams`: it needs a
    second injectable factory (`zeek_runner_factory`, real default `_default_zeek_runner_factory`)
    that neither other command uses, so it is kept out of the shared `_COMMANDS`/client-only path
    rather than forcing that path to carry a parameter only one command needs.
    """
    error = _validate_harvest_flags(args)
    if error is not None:
        # Checked before anything else in this function — no client, no zeek runner — matching
        # the immediacy `required=True` used to give the missing-flags case at the parser level
        # before build step 1 of `docs/WORK_ORDER_harvest_from_pcaps.md` made those three flags
        # conditional.
        print(f"aximprint acquire: {error}", file=sys.stderr)
        return 2

    zeek_factory = zeek_runner_factory if zeek_runner_factory is not None else _default_zeek_runner_factory

    if not _harvest_needs_client(args):
        return _run_mapping_errors(lambda: cmd_harvest(args, None, zeek_factory))

    cm_or_code = _build_client_cm(args, client_factory)
    if isinstance(cm_or_code, int):
        return cm_or_code
    client_cm = cm_or_code

    def _invoke() -> int:
        with client_cm as client:
            return cmd_harvest(args, client, zeek_factory)

    return _run_mapping_errors(_invoke)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[argparse.Namespace], AbstractContextManager] | None = None,
    zeek_runner_factory: Callable[[argparse.Namespace], ZeekRunner] | None = None,
) -> int:
    """The `xtract` entry point. Returns an exit code; never raises for an
    ordinary usage/auth/transport failure — see the module docstring for the
    exit-code table. `client_factory` is the sole test seam for every command:
    real runs use `_default_client_factory` (credential resolution + a real
    `Client`), tests inject a factory that returns a stub context manager
    wrapping a fake client, so no test here opens a network socket.
    `zeek_runner_factory` is `harvest`'s own additional seam, same shape,
    real default `_default_zeek_runner_factory` — no test here invokes `podman`.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 2

    if args.command == "harvest":
        return _dispatch_harvest(args, client_factory, zeek_runner_factory)

    command = _COMMANDS[args.command]

    # `xtract pcap --dry-run --end <ts>` needs no credentials at all (work
    # order §2 Phase 4) — the only command/flag combination that skips
    # client construction entirely, so a dry-run plan can be printed with no
    # config file, no env var, and no TTY prompt in the way.
    if args.command == "pcap" and not _pcap_needs_client(args):
        return _run_mapping_errors(lambda: command(args, None))

    cm_or_code = _build_client_cm(args, client_factory)
    if isinstance(cm_or_code, int):
        return cm_or_code
    client_cm = cm_or_code

    def _invoke() -> int:
        with client_cm as client:
            return command(args, client)

    return _run_mapping_errors(_invoke)


def _run_mapping_errors(fn: Callable[[], int]) -> int:
    """Run `fn`, mapping the exceptions it may let escape (auth,
    transport/server, or anything unexpected) to their exit codes — shared
    by both the client-constructing and the client-skipping paths in
    `main()` so neither duplicates this mapping. `fn` may itself run a
    `with client_cm as client:` block, so this also covers a login failure
    surfacing from `__enter__`, not just failures from the command body.
    """
    try:
        return fn()
    except UsageError as exc:
        # Checked before the generic XtractError branch below — a UsageError is
        # an XtractError, and the broader handler would otherwise claim it as
        # exit 4.
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 2
    except (AuthenticationError, UnauthorizedError) as exc:
        print(f"aximprint acquire: authentication failed: {exc}", file=sys.stderr)
        return 3
    except XtractAPIError as exc:
        # GAP-L2: print the appliance's OWN diagnosis, not just our one-word status word. A
        # bare `Forbidden` sent an operator into a 40-minute investigation whose entire answer
        # ("This endpoint requires one of: Extractor. Account 'x' does not have any of them.")
        # was sitting unread in the response body the whole time.
        print(f"aximprint acquire: {_api_error_detail(exc)}", file=sys.stderr)
        return 4
    except XtractError as exc:
        # A known `xtract` error type (from any module — `TimeUtilError`,
        # `WindowsError`, `StreamSpecError`, `CatalogError`, `ManifestError`,
        # `CredentialError`, `RunnerError`, ...) that reached here without a
        # more specific inline handler. Still exit `4` like the last-resort
        # branch below, but the message must not say "unexpected error" —
        # this is a recognized failure mode, not an internal bug.
        print(f"aximprint acquire: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001 -- last-resort: never a bare traceback for the operator
        print(f"aximprint acquire: unexpected error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
