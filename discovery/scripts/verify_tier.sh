#!/bin/bash
# verify_tier.sh — the hermetic (non-mutating) middle verification gate.
#
# Sits between the unit suite and the human-gated live verification. Every step
# here is NON-MUTATING — it never creates, destroys, publishes, or deploys
# anything — so it is safe to run unattended in the pipeline. Most regressions
# die here, off the shared environment.
#
# Steps come from CLAUDE_VERIFY_STEPS in .claude-workflow.env, each formatted:
#
#     "label|shell command"          hard gate — a failure stops the tier
#     "?label|shell command"         soft step — a failure is reported, not fatal
#
#   Example (.claude-workflow.env):
#     CLAUDE_VERIFY_STEPS=(
#       "fast unit tests|.venv/bin/python -m pytest -q -m 'not slow'"
#       "lint|ruff check ."
#       "schema validation|./bin/myapp validate examples/"
#       "?integration plan (needs network)|./bin/myapp plan --dry-run"
#     )
#
# With CLAUDE_VERIFY_STEPS unset, the tier auto-detects the project's test
# runner. Configure it explicitly as soon as the project has a real gate.
#
# Usage:  scripts/verify_tier.sh [extra args passed to every step as "$@"]

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

if [ -f .claude-workflow.env ]; then
    # shellcheck disable=SC1091
    source .claude-workflow.env
fi

STEPS=()
if declare -p CLAUDE_VERIFY_STEPS >/dev/null 2>&1; then
    STEPS=("${CLAUDE_VERIFY_STEPS[@]}")
else
    # --- Auto-detect: run whatever this project obviously has -----------------
    if [ -x .venv/bin/python ]; then PY=".venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then PY="python3"
    else PY="python"; fi

    if [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -f setup.cfg ] || [ -d tests ]; then
        if $PY -c 'import pytest' >/dev/null 2>&1; then
            STEPS+=("fast unit tests|$PY -m pytest -q -m 'not slow'")
        fi
    fi
    if [ -f package.json ] && grep -q '"test"' package.json 2>/dev/null; then
        STEPS+=("npm test|npm test --silent")
    fi
    if [ -f Cargo.toml ]; then
        STEPS+=("cargo test|cargo test --quiet")
    fi
    if [ -f go.mod ]; then
        STEPS+=("go test|go test ./...")
    fi
    if [ -f Makefile ] && grep -qE '^test:' Makefile 2>/dev/null; then
        STEPS+=("make test|make test")
    fi
    if [ -f .claude-doc-stats.json ]; then
        STEPS+=("?doc-counter drift|$PY scripts/doc-stats.py --check")
    fi

    if [ ${#STEPS[@]} -eq 0 ]; then
        cat <<'EOF'
verify_tier: no steps configured and nothing auto-detected.

Define the project's hermetic gates in .claude-workflow.env:

    CLAUDE_VERIFY_STEPS=(
      "fast unit tests|<your fast test command>"
      "?optional step|<a best-effort check>"
    )

Until then this tier is a no-op (exit 0) — it never blocks the pipeline.
EOF
        exit 0
    fi
fi

TOTAL=${#STEPS[@]}
IDX=0
SOFT_FAILURES=0

fail() { echo -e "\n✗ TIER FAILED at: $1"; exit 1; }

for entry in "${STEPS[@]}"; do
    IDX=$((IDX + 1))
    LABEL="${entry%%|*}"
    CMD="${entry#*|}"
    SOFT=0
    if [ "${LABEL:0:1}" = "?" ]; then
        SOFT=1
        LABEL="${LABEL:1}"
    fi

    echo -e "\n── $IDX/$TOTAL  $LABEL"
    if [ $# -gt 0 ]; then
        bash -c "$CMD \"\$@\"" _ "$@"
    else
        bash -c "$CMD"
    fi
    RC=$?

    if [ $RC -ne 0 ]; then
        if [ $SOFT -eq 1 ]; then
            echo "  ⚠ soft step failed (rc=$RC) — not a tier failure"
            SOFT_FAILURES=$((SOFT_FAILURES + 1))
        else
            fail "$LABEL"
        fi
    fi
done

if [ $SOFT_FAILURES -gt 0 ]; then
    echo -e "\n✓ verification tier PASSED with $SOFT_FAILURES soft failure(s) — review them before the gated live run."
else
    echo -e "\n✓ verification tier PASSED — safe to proceed to the (gated) live verification."
fi
