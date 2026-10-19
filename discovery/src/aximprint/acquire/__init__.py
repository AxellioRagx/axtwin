"""xtract — read-only extraction of pcap capture windows from an Axellio Xpress
Platform appliance (axapi).

This package is built incrementally by the work orders in docs/WORK_ORDER_*.md.
See docs/CHANGELOG_acquire.md for the ratified design this implements (repointed from
docs/CHANGELOG.md 2026-08-04, the xtract-repo citation this docstring carried before the
merge — in aximprint that date resolves to an unrelated entry, the pinned-categorical-
vocabulary/`Q6` round; docs/WORK_ORDER_merge_xtract_acquire.md D6).
"""

# Recorded in the manifest header (acquire/manifest.py) so a manifest file can
# be traced back to the tool version that produced it. Kept in lockstep with
# pyproject.toml's [project].version by hand — there is no packaging metadata
# lookup here, to keep this importable with zero I/O.
__version__ = "0.0.1"

__all__ = ["__version__"]
