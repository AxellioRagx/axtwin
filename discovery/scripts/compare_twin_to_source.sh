#!/usr/bin/env bash
# Capture the deployed twin through XPress-SW-A, run it through the SAME pipeline the source went
# through, and diff the two shape fingerprints.
#
# `docs/JOURNAL_corpapcn_fidelity.md`. Identical Zeek -> ingest -> materialize path on both sides
# is the whole point: any difference in the fingerprint is then the twin, never the pipeline.
#
#   compare_twin_to_source.sh <source-ir.duckdb> <workdir> [minutes] [interval] [http-timeout] [sink-cidrs]
set -euo pipefail
SRC_IR="$1"; WORK="$2"; MINS="${3:-10}"
# Extraction chunk size. 60s windows read-timed-out on CorpAPCN volumes (4.6 GB/window);
# 10s keeps each request small. This is the HARVEST chunk only -- the ingest --window below
# stays 60 to match the source side, so fingerprint comparability is unaffected.
INTERVAL="${4:-5s}"
# Per-request HTTP timeout. The default 30s is sized for a quiet capture; a twin driving
# hundreds of Mbit/s makes each WINDOW large enough that assembling and returning it
# overruns the default, and extraction dies mid-run regardless of how short the span is.
HTTP_TIMEOUT="${5:-300}"
# Segment CIDR(s) of the twin's synthetic internet sink, counted EXTERNAL on the TWIN side of
# the fingerprint only (the source has no sink). Defaulted rather than required so callers that
# pass five args -- `measure.sh` does -- need no edit: on a twin with no sink deployed no entity
# carries this segment, the override matches nothing, and every axis is byte-identical to a run
# without it. `${6-...}` not `${6:-...}` on purpose: passing an explicit "" DISABLES the
# override, which is how you reproduce a pre-sink iteration's numbers exactly.
#
# A CIDR, never a segment name: `--relocate` rewrites addresses but not the observed-octet names
# (`seg-10-80-72-0`), so a name in a relocated twin points at where a segment used to be.
SINK_CIDRS="${6-10.200.254.0/24}"
AX=/home/rocky/aximprint-axrange
cd "$AX"

echo "== 1/4  extract ${MINS}m from XPress-SW-A and Zeek it =="
rm -rf "$WORK/twin-cap"
.venv/bin/aximprint acquire --profile xpressa --insecure harvest \
    --streams 0 --start "${MINS}m ago" --end now --interval "$INTERVAL" \
    --timeout "$HTTP_TIMEOUT" \
    --out "$WORK/twin-cap" --spool-cap 20GB | tail -2

echo "== 2/4  ingest (window 60, same as source) =="
rm -f "$WORK/twin.duckdb" "$WORK/twin-ir.duckdb"
.venv/bin/aximprint ingest --plugin zeek --source "$WORK/twin-cap/zeek" \
    --store "$WORK/twin.duckdb" --network corpapcn-twin --window 60 | tail -2

echo "== 3/4  materialize =="
.venv/bin/aximprint materialize --store "$WORK/twin.duckdb" --out "$WORK/twin-ir.duckdb" \
    | grep -E '"ir_entity"|"ir_segment"|"ir_edge"'

echo "== 4/4  compare  (A = source CorpAPCN, B = deployed twin) =="
.venv/bin/python scripts/shape_fingerprint.py compare "$SRC_IR" "$WORK/twin-ir.duckdb" "$SINK_CIDRS"
