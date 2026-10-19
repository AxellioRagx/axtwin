#!/bin/bash
# verify_pipeline_gates.sh — aximprint's own hermetic gates, run by verify_tier.sh.
#
# v5 split the tier runner into MECHANISM (scripts/verify_tier.sh, generic, from
# the template) and POLICY (which gates this project actually runs, declared as
# CLAUDE_VERIFY_STEPS in .claude-workflow.env). These three gates are the policy
# half: they are aximprint-specific, share one seeded synthetic fixture, and are
# far more than a one-line shell command, so they live here rather than inline.
#
# Everything runs on SYNTHETIC data in a temp dir — never a real capture, never a
# cluster, nothing written into the repo (CONVENTIONS §3 Privacy, LES-003).
#
#   1. determinism invariants   window close (§5.4) + synth compiler (§9) + archive
#   2. CLI round-trip           ingest -> status -> synth -> features (+ pinned re-run)
#   3. transport equivalence    flat log dir vs parquet: archive vs a WINDOW TREE
#                               (`docs/WORK_ORDER_multi_window_log_read.md`, `M4`) —
#                               all three, byte-identical features
#   4. SENSOR equivalence       one seeded traffic plan rendered as Zeek logs AND as
#                               Suricata eve.json, both through the real plugins and the
#                               real WindowManager, compared structurally
#                               (`docs/WORK_ORDER_axrange_integration.md` Track B)
#
# Steps 3 and 4 are different null tests and neither substitutes for the other: 3 varies
# the TRANSPORT under one sensor and demands byte-identical features; 4 varies the SENSOR
# and demands structural equivalence, since two sensors cannot be byte-identical by
# construction. Step 4 also carries its own determinism half — `--plugin suricata` in
# step 1 runs the same two-pass window-close comparison over the eve.json fixture, so the
# Suricata path is covered for both properties, not just for agreement with Zeek.
#
# The unit suite is a separate CLAUDE_VERIFY_STEPS entry and is NOT repeated here.
#
# Usage:  scripts/verify_pipeline_gates.sh [zeek-log-dir]
#   With no argument a seeded synthetic capture is generated in a temp dir. Pass a
#   real Zeek log dir only for a local spot-check — the gated real-capture run
#   belongs to the live-verifier stage.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Prefer the project venv if present (canonical form), else system python.
if [[ -x "$REPO/.venv/bin/python" ]]; then PY="$REPO/.venv/bin/python"
elif command -v python >/dev/null 2>&1;   then PY="python"
else PY="python3"; fi

WORK="$(mktemp -d -t aximprint-tier-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo -e "\n✗ TIER FAILED at: $1"; exit 1; }
step() { echo -e "\n── $1"; }

ZEEK="${1:-}"
if [[ -z "$ZEEK" ]]; then
  ZEEK="$($PY scripts/verify_determinism.py --emit-fixture "$WORK/zeek")" \
    || fail "synthetic fixture generation"
  echo "fixture: $ZEEK (seeded synthetic)"
else
  [[ -d "$ZEEK" || -f "$ZEEK" ]] || fail "zeek source not found: $ZEEK"
  echo "fixture: $ZEEK (caller-supplied)"
fi

step "1/4  determinism invariants (window close + synth compiler)"
$PY scripts/verify_determinism.py --source "$ZEEK" || fail "verify_determinism"

step "2/4  CLI round-trip (ingest -> status -> synth -> features)"
STORE="$WORK/tier.duckdb"
$PY -m aximprint.cli ingest --plugin zeek --source "$ZEEK" --store "$STORE" \
  --evidence-log "$WORK/evidence.jsonl" >/dev/null || fail "aximprint ingest"
$PY -m aximprint.cli status --store "$STORE" >/dev/null || fail "aximprint status"
$PY -m aximprint.cli synth --store "$STORE" --out "$WORK/twin_draft.yaml" >/dev/null \
  || fail "aximprint synth"
grep -q "twin_config_draft" "$WORK/twin_draft.yaml" || fail "synth produced no twin_config_draft"
[[ -s "$WORK/evidence.jsonl" ]] || fail "evidence log is empty (append-only log wrote nothing)"

# The feature path is separate from the store path on purpose (no belief store).
FEAT="$WORK/features"
$PY -m aximprint.cli features --plugin zeek --source "$ZEEK" --out "$FEAT" >/dev/null \
  || fail "aximprint features"
for f in nodes.jsonl edges.jsonl windows.jsonl identity.jsonl manifest.json; do
  [[ -s "$FEAT/$f" ]] || fail "feature sink wrote no $f"
done

# Second run, PINNED to the first archive's manifest. The pin wiring is the one
# CLI path a unit test cannot reach end to end — argparse default, loader,
# lifecycle argument, emitted provenance — so a regression in it used to survive
# the gate that exists to catch regressions.
PINNED="$WORK/features-pinned"
$PY -m aximprint.cli features --plugin zeek --source "$ZEEK" --out "$PINNED" \
  --vocabulary "$FEAT/manifest.json" >/dev/null || fail "aximprint features --vocabulary"
$PY - "$FEAT/manifest.json" "$PINNED/manifest.json" <<'EOF' || fail "pinned run did not honor the pin"
import json, sys
unpinned, pinned = (json.load(open(p))["categorical"] for p in sys.argv[1:3])
assert pinned["pinned_to"] is not None, "pinned run recorded no pin"
open_fields = [n for n, f in unpinned["fields"].items() if f["vocabulary"] == "observed"]
assert open_fields, "fixture produced no open vocabulary to pin"
for name in open_fields:
    got = pinned["fields"][name]
    assert got["vocabulary"] == "pinned", f"{name}: {got['vocabulary']}"
    assert got["index"] == unpinned["fields"][name]["index"], f"{name}: index moved"
# The digest that answers "did these two archives encode to the same columns?"
assert pinned["map_sha256"] == unpinned["map_sha256"], "same source, different columns"
assert pinned["pinned_to"]["map_sha256"] == unpinned["map_sha256"], "pinned to other columns"
EOF

# An empty --vocabulary must fail loud rather than quietly produce an unpinned
# archive (an unexpanded shell variable is the way this happens for real).
if $PY -m aximprint.cli features --plugin zeek --source "$ZEEK" \
     --out "$WORK/features-badpin" --vocabulary "" >/dev/null 2>&1; then
  fail "empty --vocabulary produced an archive instead of failing"
fi

echo "  ✓ ingest -> status -> synth -> features (+pinned) clean; evidence log and 5 artifacts non-empty"

# The transport-equivalence null test. Every transport over one capture must
# produce the SAME feature archive — not a similar one. A unit test can compare
# event streams; only this can compare the bytes a consumer will actually read,
# through the real CLI. See docs/SPEC_zeek_metadata_archive.md.
step "3/4  transport equivalence (flat dir vs parquet: archive vs a window tree)"
ARCH="$WORK/archive"
$PY scripts/make_zeek_archive.py "$ZEEK" "$ARCH" >/dev/null \
  || fail "archive conversion"
AFEAT="$WORK/features-archive"
$PY -m aximprint.cli features --plugin zeek --source "parquet:$ARCH" --out "$AFEAT" \
  >/dev/null || fail "aximprint features over parquet: archive"

for f in nodes.jsonl edges.jsonl windows.jsonl identity.jsonl; do
  # Non-empty FIRST: `cmp` on two absent-or-empty files agrees, so a comparison
  # that skipped this would report equivalence it never established.
  [[ -s "$FEAT/$f" ]]  || fail "text-path $f is empty — nothing to compare"
  [[ -s "$AFEAT/$f" ]] || fail "archive-path $f is empty"
done
for f in nodes.jsonl edges.jsonl identity.jsonl; do
  cmp -s "$FEAT/$f" "$AFEAT/$f" \
    || fail "$f differs between the text and archive transports"
done

# `windows.jsonl` is the one DECLARED exception to a strict `cmp`, not a
# weakening (`docs/WORK_ORDER_zeek_stem_coverage.md` D5, code review `S1`
# fixing four holes in the first version of this carve-out): the
# `windowing.windows.TRANSPORT_DEPENDENT_HEALTH_FIELDS` keys are EXPECTED to
# differ here, on purpose, between the text and archive transports. The
# archive's own converter (`scripts/make_zeek_archive.py`) never writes a
# skipped or unrecognized stem into the manifest at all (`ingest/zeek/sources
# .py::ParquetArchiveSource.inventory_is_complete`), so that transport can
# never audit what it is missing and correctly reports `null` (unaudited) —
# where the text transport, which walks the filesystem directly and therefore
# CAN audit completely, reports a real list. A blanket `cmp` here would either
# mask that divergence (comparing nothing meaningful) or fail the gate on a
# difference that is now CORRECT (the D5 gap reopening one layer down, exactly
# what `inventory_is_complete` exists to prevent).
#
# Four things the FIRST version of this carve-out got wrong, all found by code
# review and fixed here: (1) `dict ==` on the residual is SEMANTIC equality for
# the WHOLE line, not byte equality — `0` vs `0.0`, `true` vs `1`, and a missing
# trailing newline would all pass silently, exactly the kind of drift a
# Parquet/DuckDB transport introduces; fixed by rebuilding each carved key's
# EXACT serialized fragment from ITS OWN parsed value, asserting it occurs
# exactly once, and comparing the STRING residual instead. (2) the carved key
# was a string literal re-declared here instead of imported from where
# `windowing.windows` already declares it — the same duplicated-fact hazard
# `WALLCLOCK_FIELD`/`NONDETERMINISTIC_TABLES` already avoid; fixed by importing
# `TRANSPORT_DEPENDENT_HEALTH_FIELDS` and asserting the differing-key SET
# equals it exactly, not merely including it. (3) D5's own trap #2 (the SAME
# run-level fact must reach EVERY window identically) was never checked at
# this tier; fixed by comparing every window's value against window 0's.
# (4) two empty files pass trivially; fixed with an explicit non-empty
# assertion inside the Python process itself, not 20 lines away in bash.
$PY - "$FEAT/windows.jsonl" "$AFEAT/windows.jsonl" <<'EOF' || fail "windows.jsonl differs between the text and archive transports outside the declared TRANSPORT_DEPENDENT_HEALTH_FIELDS asymmetry"
import json, sys

from aximprint.windowing.windows import TRANSPORT_DEPENDENT_HEALTH_FIELDS

flat_lines = open(sys.argv[1]).read().splitlines()
arch_lines = open(sys.argv[2]).read().splitlines()
assert flat_lines, "windows.jsonl (text) is empty — nothing to compare"  # `S1` #4
assert arch_lines, "windows.jsonl (archive) is empty — nothing to compare"
assert len(flat_lines) == len(arch_lines), (
    f"{len(flat_lines)} window(s) (text) vs {len(arch_lines)} (archive) — "
    "transport equivalence must hold on WINDOW COUNT too")


def carve(line: str, parsed: dict, keys) -> str:
    """Replace each of `keys`'s EXACT serialized fragment in `line` with a
    placeholder (`S1` #1): what remains is compared as a STRING, so a residual
    difference anywhere else in the row — a type, a value, whitespace, a
    missing trailing newline — still fails the gate the way `cmp` always did.
    """
    out = line
    for key in sorted(keys):
        fragment = json.dumps({key: parsed[key]}, sort_keys=True)[1:-1]  # strip { }
        n = out.count(fragment)
        assert n == 1, (
            f"expected exactly one occurrence of {fragment!r} in the line, found {n} "
            "— the carve-out cannot trust its own placement")
        out = out.replace(fragment, f"<{key}>", 1)
    return out


first_flat_value: dict = {}
for i, (fl, al) in enumerate(zip(flat_lines, arch_lines)):
    f, a = json.loads(fl), json.loads(al)
    differing = {k for k in set(f) | set(a) if f.get(k) != a.get(k)}
    assert differing == TRANSPORT_DEPENDENT_HEALTH_FIELDS, (  # `S1` #2
        f"window {i}: differing key set {sorted(differing)} does not equal the "
        f"declared TRANSPORT_DEPENDENT_HEALTH_FIELDS "
        f"{sorted(TRANSPORT_DEPENDENT_HEALTH_FIELDS)} — either a new field just "
        "started diverging unexpectedly, or a declared one stopped")
    for key in TRANSPORT_DEPENDENT_HEALTH_FIELDS:
        assert a[key] is None, (
            f"window {i}: archive transport must report {key}=None (unaudited) "
            f"— got {a[key]!r}, the exact false 'audited and clean' D5 exists "
            "to refuse")
        assert f[key] is not None, (
            f"window {i}: text transport must report a real (possibly empty) "
            f"list for {key} — got None, i.e. unaudited, which this transport "
            "has no excuse for (it walks the filesystem directly)")
        if i == 0:
            first_flat_value[key] = f[key]
        else:  # D5's own trap #2, `S1` #3: the SAME fact must reach EVERY window
            assert f[key] == first_flat_value[key], (
                f"window {i}: {key}={f[key]!r} differs from window 0's "
                f"{first_flat_value[key]!r} — a run-level fact must be "
                "identical across every window, not just the first")
    residual_f = carve(fl, f, TRANSPORT_DEPENDENT_HEALTH_FIELDS)
    residual_a = carve(al, a, TRANSPORT_DEPENDENT_HEALTH_FIELDS)
    assert residual_f == residual_a, (
        f"window {i}: differs outside {sorted(TRANSPORT_DEPENDENT_HEALTH_FIELDS)}")

print(f"  (windows.jsonl: {len(flat_lines)} window(s), "
      f"{sorted(TRANSPORT_DEPENDENT_HEALTH_FIELDS)} correctly None on archive / "
      "a real, window-0-matching list on text — everything else byte-identical)")
EOF

# The manifest legitimately differs in `source` (a different locator) and in the
# one wallclock block; nothing else may move.
$PY - "$FEAT/manifest.json" "$AFEAT/manifest.json" <<'EOF' || fail "manifest diverged beyond source/wallclock"
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
allowed = {"source", "wallclock", "ts_from", "ts_to"}
moved = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
assert moved <= allowed, f"manifest keys differ beyond {sorted(allowed)}: {sorted(moved)}"
assert a["categorical"]["map_sha256"] == b["categorical"]["map_sha256"], \
    "same capture encoded to different columns"
EOF

# The window-tree transport (`docs/WORK_ORDER_multi_window_log_read.md`, `M4`):
# the same conn.log rows the flat run reads, split into N per-window directories,
# with 1-in-7 deliberately written into the NEXT window's directory instead of
# its own — the late-closing-flow pathology `LogDirSource`'s module docstring
# names, exercised for real rather than only in the unit suite (LES-002: a
# single run always agrees with itself, so only a second, differently-shaped
# run can catch a determinism break here).
TREE="$WORK/zeek-tree"
$PY scripts/split_zeek_window_tree.py "$ZEEK" "$TREE" >/dev/null \
  || fail "window-tree split"
TFEAT="$WORK/features-tree"
$PY -m aximprint.cli features --plugin zeek --source "$TREE" --out "$TFEAT" \
  >/dev/null || fail "aximprint features over a window tree"

for f in nodes.jsonl edges.jsonl windows.jsonl identity.jsonl; do
  [[ -s "$FEAT/$f" ]]  || fail "text-path $f is empty — nothing to compare"
  [[ -s "$TFEAT/$f" ]] || fail "window-tree-path $f is empty"
  cmp -s "$FEAT/$f" "$TFEAT/$f" \
    || fail "$f differs between the flat directory and the window tree"
done
$PY - "$FEAT/manifest.json" "$TFEAT/manifest.json" <<'EOF' || fail "window-tree manifest diverged beyond source/wallclock"
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
allowed = {"source", "wallclock", "ts_from", "ts_to"}
moved = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
assert moved <= allowed, f"manifest keys differ beyond {sorted(allowed)}: {sorted(moved)}"
assert a["categorical"]["map_sha256"] == b["categorical"]["map_sha256"], \
    "same capture encoded to different columns"
EOF

# Slicing is the primary access pattern at archive scale, so the gate exercises
# it: a bounded run must be a strict, non-empty subset of the full one.
SLICED="$WORK/features-sliced"
BOUNDS=$($PY - "$ARCH/manifest.json" <<'EOF'
import json, sys
t = json.load(open(sys.argv[1]))["time_bounds"]
lo = t["min_ts"] - (t["min_ts"] % 3600)          # align, so the slice cuts on a window
print(f"{lo} {lo + 3600}")
EOF
) || fail "could not derive slice bounds"
$PY -m aximprint.cli features --plugin zeek --source "parquet:$ARCH" --out "$SLICED" \
  --from ${BOUNDS% *} --to ${BOUNDS#* } >/dev/null || fail "aximprint features --from/--to"
[[ -s "$SLICED/nodes.jsonl" ]] || fail "sliced run produced no rows"
full=$(wc -l < "$FEAT/windows.jsonl"); part=$(wc -l < "$SLICED/windows.jsonl")
(( part > 0 && part < full )) \
  || fail "slice produced $part window(s) against $full — not a strict subset"

# An inverted range must be refused, not quietly yield nothing.
if $PY -m aximprint.cli features --plugin zeek --source "parquet:$ARCH" \
     --out "$WORK/features-badslice" --from 200 --to 100 >/dev/null 2>&1; then
  fail "inverted --from/--to produced an archive instead of failing"
fi

echo "  ✓ nodes/edges/identity byte-identical across all three transports (flat / parquet / window tree); windows.jsonl matches too, except the declared TRANSPORT_DEPENDENT_HEALTH_FIELDS asymmetry between the flat/window-tree pair and the archive (both audit completely, archive cannot — see above); slice is a strict subset ($part/$full windows)"


# The SENSOR-equivalence null test (`docs/WORK_ORDER_axrange_integration.md` Track B).
# Deliberately NOT parameterized by "$ZEEK": both fixtures are rendered from one seeded
# traffic plan inside the script, and the whole claim rests on the two sensors describing
# the SAME plan — a caller-supplied Zeek directory has no eve.json counterpart to compare
# it against, so honoring the argument here would mean comparing two different captures.
#
# Two legs, and they check different properties:
#   * `verify_determinism.py --plugin suricata` — the SAME two-pass window-close
#     comparison step 1 runs for Zeek, over the synthetic eve.json fixture. Determinism
#     is a per-plugin property; step 1 passing for Zeek says nothing about a plugin with
#     its own reorder buffer, its own state classifier and its own census timing.
#   * `verify_suricata_equivalence.py` — Zeek vs Suricata over one plan: node set, window
#     placement, per-node flow/byte/state_mix/listen_ports, edges + longtail, identity
#     bindings (scope- and namespace-keyed), service census, taint, health. Every
#     divergence it tolerates is a NAMED structural exception in its own module docstring.
#
# Until 2026-08-25 neither leg ran at tier 2 at all, so the Suricata plugin's entire
# gate story was "a unit suite that only ever compares a run against itself" — which
# `LES-002` says outright cannot see a determinism break, and which by construction
# cannot see a cross-sensor divergence either.
step "4/4  sensor equivalence (one seeded plan -> zeek logs AND suricata eve.json)"
$PY scripts/verify_determinism.py --plugin suricata >/dev/null \
  || fail "verify_determinism --plugin suricata"
echo "  ✓ suricata window close is deterministic (two passes, identical)"
$PY scripts/verify_suricata_equivalence.py || fail "verify_suricata_equivalence"

echo -e "\n✓ aximprint pipeline gates PASSED"
