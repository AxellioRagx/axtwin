"""zeek — `acquire`'s name for the Zeek runner, which lives in `aximprint.zeekrun`.

The runner (`ZeekRunner`, `PodmanZeekRunner`, `FakeZeekRunner`, `ZeekResult`, `ScriptPolicy`,
the stem tables, the pinned-image check) moved to `aximprint/zeekrun.py` — a module below both
`acquire` and `ingest` that imports nothing from `aximprint`, the `vantage.py` precedent — so
that `acquire/harvest` and the `ingest/pcap` plugin share one copy of the runner and of the
stem tables without either layer importing the other (CONVENTIONS §3; `ingest/pcap/SPEC.md`,
"Seam"). This module re-exports every name, public and the two private ones
`tests/acquire/test_zeek_runner.py` reaches (`_SCHEMAS`, `_require_pinned_image`), so every
`acquire` caller and test is unchanged: `from aximprint.acquire.harvest.zeek import X` still
works for every `X` it ever did.

One consequence to know: `ZeekError` roots in `ValueError`, no longer in
`acquire.errors.XtractError` — the hoisted module may not reach up for that base. The two
`acquire/cli.py` sites that can see one (`zeek_runner_factory(args)` at construction) already
name `ZeekError` directly, and `ZeekRunner.run()` never raises by contract, so no acquire path
changes behavior.
"""

from __future__ import annotations

# `tests/acquire/test_zeek_runner.py` patches `zeek.subprocess.run` — an attribute of the stdlib
# module itself, so the patch reaches `aximprint.zeekrun`'s own `subprocess.run` calls through
# this name exactly as it did when the runner lived here.
import subprocess  # noqa: F401

from aximprint.zeekrun import (  # noqa: F401  (re-exports)
    DEFAULT_IGNORE_CHECKSUMS,
    DEFAULT_SCRIPT_POLICY,
    DEFAULT_TIMEOUT_S,
    DEFAULT_ZEEK_IMAGE,
    KNOWN_PARSED_STEMS,
    KNOWN_SKIPPED_STEMS,
    KNOWN_ZEEK_STEMS,
    PROBE_TIMEOUT_S,
    RUNNER_FAILED_RETURNCODE,
    TIMEOUT_RETURNCODE,
    FakeZeekRunner,
    NativeZeekRunner,
    PinnedImageRequiredError,
    PodmanZeekRunner,
    ProbeResult,
    ScriptPolicy,
    ZeekError,
    ZeekResult,
    ZeekRunner,
    _FALLBACK_SCHEMA,
    _SCHEMAS,
    _collect_logs,
    _parse_zeek_version,
    _require_pinned_image,
    _write_zeek_log,
    unrecognized_stems,
)

__all__ = [
    "DEFAULT_SCRIPT_POLICY",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_ZEEK_IMAGE",
    "KNOWN_PARSED_STEMS",
    "KNOWN_SKIPPED_STEMS",
    "KNOWN_ZEEK_STEMS",
    "PROBE_TIMEOUT_S",
    "RUNNER_FAILED_RETURNCODE",
    "TIMEOUT_RETURNCODE",
    "FakeZeekRunner",
    "PinnedImageRequiredError",
    "PodmanZeekRunner",
    "ProbeResult",
    "ScriptPolicy",
    "ZeekError",
    "ZeekResult",
    "ZeekRunner",
    "unrecognized_stems",
]
