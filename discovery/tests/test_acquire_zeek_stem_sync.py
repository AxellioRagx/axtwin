"""D1 of `docs/WORK_ORDER_zeek_stem_coverage.md`: the two Zeek stem lists that describe the
SAME coverage claim from opposite sides of a boundary that forbids the import.

`KNOWN_PARSED_STEMS`/`KNOWN_SKIPPED_STEMS` (`aximprint.acquire.harvest.zeek`) are the
harvest-time view: what `ZeekResult.unrecognized` (surfaced to the operator running
`aximprint acquire harvest`, and rolled up into `RunReport.unrecognized_stems`, `Q24`)
excludes because aximprint already has an opinion about a stem, one way or the other.
`_PARSERS`/`_SKIP` (`aximprint.ingest.zeek.plugin`) are the ingest-time view of the exact
same claim: which stem actually becomes events, and which is a named, deliberate non-parse.
`acquire` must not import `ingest` (CONVENTIONS §3, `tests/test_acquire_layering.py`), so
the two pairs can never be derived from one another in `src/` -- they are kept in agreement
BY HAND, and until this file, nothing checked that they still were.

`_COLUMNS` (also `plugin.py`) needs no third check here: it is already pinned to `_PARSERS`
at import (`plugin.py`'s own `assert set(_COLUMNS) == set(_PARSERS)`) and mirrored test-side
(`tests/test_zeek_parquet_source.py`), so the `_PARSERS`-vs-`KNOWN_PARSED_STEMS` check below
covers it transitively.

A TEST may import across the boundary the layering rule forbids in `src/`:
`tests/test_acquire_layering.py`'s two checks (`_ACQUIRE_ROOT`, `_AXIMPRINT_ROOT`) both walk
only paths under `src/aximprint` -- confirmed by reading that file before writing this one --
so a module under `tests/` is structurally outside anything either walk ever visits, whatever
it imports. The layering rule binds `src/`'s own import graph; it says nothing about a test
importing both sides to check they still agree.

**No `requests` involved.** `requests` is `acquire`'s only runtime dependency beyond the
stdlib, gated behind the optional `[acquire]` extra (`pyproject.toml`) so a bare
`pip install -e .` still runs the discovery pipeline with none of it present.
`tests/test_acquire_ingest_chain.py` already imports `aximprint.acquire.harvest.zeek`
unguarded from this same `tests/` root, so this is precedented, not a new risk. Checked
anyway before writing this file (a blocking `sys.meta_path` finder simulating "`requests` not
installed", then both the raw import and a full in-process `pytest` run of this module): no
`requests` reaches `sys.modules` either way. So no `pytest.importorskip` guard is used --
adding one anyway would silently mask a real regression if a future edit ever does make this
import chain requests-dependent.
"""
from __future__ import annotations

from aximprint.acquire.harvest import zeek
from aximprint.ingest.zeek import plugin as zp

# Relative to the repo root -- named so a failure message can say which file to open, not just
# which dotted symbol disagrees (a dotted module path isn't something you can open).
_ZEEK_PY = "src/aximprint/acquire/harvest/zeek.py"
_PLUGIN_PY = "src/aximprint/ingest/zeek/plugin.py"


def _ingest_known_stems() -> frozenset[str]:
    """Every stem `ingest.zeek.plugin` has ANY opinion about -- parsed or explicitly skipped.
    The ingest-side counterpart to `zeek.KNOWN_ZEEK_STEMS`; no such combined constant exists
    in `plugin.py` itself, so it is built fresh here rather than assumed.
    """
    return frozenset(zp._PARSERS) | frozenset(zp._SKIP)


def _stem_set_diff(*, acquire_label: str, acquire_path: str, acquire_stems: frozenset[str],
                   acquire_known: frozenset[str],
                   ingest_label: str, ingest_path: str, ingest_stems: frozenset[str],
                   ingest_known: frozenset[str]) -> str:
    """A failure message naming which stems disagree, classified into the three distinct ways
    that matters -- never a bare `assert a == b` on two frozensets, and never a two-way
    UNDER/OVER split either.

    A two-way split (only-in-`acquire_stems` => UNDER, only-in-`ingest_stems` => OVER) is
    wrong for a stem that is on BOTH sides' radar but filed under the WRONG table on one of
    them: `unrecognized_stems()` (`zeek.py`) tests membership in the whole acquire-side union,
    `KNOWN_ZEEK_STEMS`, not in `acquire_stems` alone, so such a stem never moves
    `ZeekResult.unrecognized` either way. A two-way version of this function reported it as
    BOTH an under-report (from the skipped-pair call) AND an over-report (from the parsed-pair
    call) for the identical stem, simultaneously -- both claims false -- caught in review
    against D3's own scenario: `analyzer` declared skipped on the acquire side, wired into
    `_PARSERS` on the ingest side. `acquire_known`/`ingest_known` (each side's FULL opinion,
    parsed union skipped) are what let this function tell "genuinely unheard-of on the other
    side" (a real UNDER/OVER-report bug) apart from "known to the other side too, just filed
    under its OTHER table" (a classification disagreement, MISFILED, invisible to
    `ZeekResult.unrecognized` either way).
    """
    acquire_only = acquire_stems - ingest_stems
    ingest_only = ingest_stems - acquire_stems
    under = sorted(acquire_only - ingest_known)
    over = sorted(ingest_only - acquire_known)
    misfiled = sorted((acquire_only | ingest_only) & acquire_known & ingest_known)

    lines = [f"{acquire_label} and {ingest_label} disagree on stem coverage:"]
    if under:
        lines.append(
            f"  {under}: {acquire_label} claims these, but {ingest_path} has no record of "
            f"them at all (absent from both {ingest_label} and its sibling table) -- "
            f"ZeekResult.unrecognized UNDER-reports: harvest time calls them accounted-for, "
            f"ingest genuinely does not know them. Fix: add a parser (or a documented skip) "
            f"to {ingest_path}, or remove from {acquire_path} if {acquire_label} was wrong "
            f"to claim them."
        )
    if over:
        lines.append(
            f"  {over}: {ingest_label} already handles these ({ingest_path}), but "
            f"{acquire_path} has no record of them at all (absent from both {acquire_label} "
            f"and its sibling table) -- ZeekResult.unrecognized OVER-reports: harvest flags "
            f"them as noise even though ingest already parses or explicitly skips them. Fix: "
            f"add to {acquire_path}, or remove from {ingest_path} if {ingest_label} was "
            f"wrong to claim them."
        )
    if misfiled:
        lines.append(
            f"  {misfiled}: known on BOTH sides, but filed under DIFFERENT tables on each -- "
            f"ZeekResult.unrecognized is UNAFFECTED (the stem is in KNOWN_ZEEK_STEMS "
            f"regardless of which acquire-side table names it) -- this is a classification "
            f"disagreement, not a reporting bug. Fix: decide which classification is "
            f"correct, then move the stem to agree in BOTH {acquire_path} and {ingest_path}."
        )
    return "\n".join(lines)


def test_known_parsed_stems_matches_the_ingest_parser_table():
    """`KNOWN_PARSED_STEMS` (acquire, harvest-time) and `_PARSERS` (ingest, the only route
    into the event stream) must classify every stem identically. Verified equal today
    (`docs/WORK_ORDER_zeek_stem_coverage.md` D1's own evidence table); this is the check that
    keeps it that way once D2/D3 start editing both lists at once.
    """
    assert zeek.KNOWN_PARSED_STEMS == frozenset(zp._PARSERS), _stem_set_diff(
        acquire_label="KNOWN_PARSED_STEMS", acquire_path=_ZEEK_PY,
        acquire_stems=zeek.KNOWN_PARSED_STEMS, acquire_known=zeek.KNOWN_ZEEK_STEMS,
        ingest_label="_PARSERS", ingest_path=_PLUGIN_PY,
        ingest_stems=frozenset(zp._PARSERS), ingest_known=_ingest_known_stems(),
    )


def test_known_skipped_stems_matches_the_ingest_skip_table():
    """Same hazard as the parsed pair, one table over. Not named by D1's own prose, but build
    step 2 ("extend `KNOWN_SKIPPED_STEMS`") is exactly where this pair's drift enters next, so
    it is covered here rather than left for that round to rediscover the need.
    """
    assert zeek.KNOWN_SKIPPED_STEMS == frozenset(zp._SKIP), _stem_set_diff(
        acquire_label="KNOWN_SKIPPED_STEMS", acquire_path=_ZEEK_PY,
        acquire_stems=zeek.KNOWN_SKIPPED_STEMS, acquire_known=zeek.KNOWN_ZEEK_STEMS,
        ingest_label="_SKIP", ingest_path=_PLUGIN_PY,
        ingest_stems=frozenset(zp._SKIP), ingest_known=_ingest_known_stems(),
    )
