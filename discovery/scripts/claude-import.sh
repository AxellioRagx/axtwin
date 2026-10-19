#!/bin/bash
# scripts/claude-import.sh
#
# Apply a tarball of doc updates from Claude chat.
# Usage: scripts/claude-import.sh <path-to-tarball>
#
# Contract with Claude chat (see docs/WORKFLOW.md):
#   - Output is a single .tar.gz (not a zip, not a zip wrapping a tarball).
#   - Tarball contains updated docs either at top level or under a single
#     project-named directory.
#   - Tarball MUST contain NEXT_SESSION_PROMPT.md at the top level — this
#     becomes the opening message for the next Claude Code session.
#   - New CHANGELOG entries must include **Authority:** lines.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <path-to-tarball>"
    echo
    echo "Applies a Claude-provided tarball of doc updates with review step."
    exit 1
fi

TARBALL="$1"
if [ ! -f "$TARBALL" ]; then
    echo "ERROR: $TARBALL not found"
    exit 1
fi

# Reject zip files outright — they are a protocol violation
case "$TARBALL" in
    *.zip|*.ZIP)
        cat <<EOF
ERROR: Zip file detected: $TARBALL

This workflow does not accept zip files. Per docs/WORKFLOW.md, Claude chat
must produce a single .tar.gz archive.

RECOVERY:
  1. Go back to the chat session.
  2. Ask Claude to re-output as a single .tar.gz (no zip wrapping).
  3. Point Claude at docs/WORKFLOW.md section "Output Format Rules".
  4. If Claude persists in producing zips, paste the recovery prompt from
     docs/WORKFLOW.md section "Recovery Protocol".
EOF
        exit 1
        ;;
esac

# Sanity-check that it's actually a gzipped tarball
if ! file "$TARBALL" 2>/dev/null | grep -qiE 'gzip|tar'; then
    # Fallback: try to peek with tar
    if ! tar tzf "$TARBALL" >/dev/null 2>&1; then
        echo "ERROR: $TARBALL does not appear to be a valid .tar.gz archive."
        echo "       docs/WORKFLOW.md requires single .tar.gz output from Claude chat."
        exit 1
    fi
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Auto-source project config
if [ -f .claude-workflow.env ]; then
    # shellcheck disable=SC1091
    source .claude-workflow.env
fi

DOC_PATHS="${CLAUDE_DOC_PATHS:-CLAUDE.md docs/}"

# Require clean working tree in the relevant paths
for p in $DOC_PATHS; do
    [ -e "$p" ] || continue
    if ! git diff --quiet HEAD -- "$p" 2>/dev/null; then
        echo "ERROR: uncommitted changes in $p"
        echo "Commit or stash before importing so the diff is clean."
        git status --short $DOC_PATHS
        exit 1
    fi
done

# Extract to staging
STAGING=$(mktemp -d)
trap "rm -rf $STAGING" EXIT

tar xzf "$TARBALL" -C "$STAGING"

# Find the source directory — handle both bare and project-prefixed structures
SOURCE_DIR="$STAGING"
TOP_ITEMS=$(ls -A "$STAGING")
if [ "$(echo "$TOP_ITEMS" | wc -l)" -eq 1 ] && [ -d "$STAGING/$TOP_ITEMS" ]; then
    # Check if the inner dir looks like a project export
    if [ -f "$STAGING/$TOP_ITEMS/CLAUDE.md" ] || [ -d "$STAGING/$TOP_ITEMS/docs" ] || [ -f "$STAGING/$TOP_ITEMS/NEXT_SESSION_PROMPT.md" ]; then
        SOURCE_DIR="$STAGING/$TOP_ITEMS"
    fi
fi

# Sanity check
if [ ! -f "$SOURCE_DIR/CLAUDE.md" ] && [ ! -d "$SOURCE_DIR/docs" ]; then
    echo "ERROR: tarball doesn't look like a Claude doc export"
    echo "Expected CLAUDE.md or docs/ at top level of: $SOURCE_DIR"
    echo "Contents:"
    ls -la "$SOURCE_DIR"
    exit 1
fi

# Check for NEXT_SESSION_PROMPT.md (warn, don't fail)
NEXT_PROMPT_SRC="$SOURCE_DIR/NEXT_SESSION_PROMPT.md"
NEXT_PROMPT_MISSING=0
if [ ! -f "$NEXT_PROMPT_SRC" ]; then
    NEXT_PROMPT_MISSING=1
    echo "⚠  WARNING: NEXT_SESSION_PROMPT.md not found in tarball."
    echo "   Per docs/WORKFLOW.md, Claude chat is supposed to include this file."
    echo "   You'll need to ask for it separately after import."
    echo
fi

# Show what will change — excluding NEXT_SESSION_PROMPT.md (handled separately)
echo "=== PROPOSED CHANGES TO REPO ==="
CHANGES_FOUND=0
for f in $(cd "$SOURCE_DIR" && find . -type f | sed 's|^\./||'); do
    # Skip NEXT_SESSION_PROMPT.md — handled separately, not committed
    if [ "$f" = "NEXT_SESSION_PROMPT.md" ]; then
        continue
    fi
    SRC="$SOURCE_DIR/$f"
    if [ -f "$f" ]; then
        if ! diff -q "$f" "$SRC" >/dev/null 2>&1; then
            echo "  MODIFIED: $f"
            CHANGES_FOUND=1
        fi
    else
        echo "  NEW:      $f"
        CHANGES_FOUND=1
    fi
done

if [ $CHANGES_FOUND -eq 0 ]; then
    echo "  (no differences detected in doc files — tarball matches current state)"
    echo
    # Still handle NEXT_SESSION_PROMPT.md if present
    if [ -f "$NEXT_PROMPT_SRC" ]; then
        mkdir -p .claude-sync
        cp "$NEXT_PROMPT_SRC" .claude-sync/NEXT_SESSION_PROMPT.md
        echo "✓ NEXT_SESSION_PROMPT.md extracted to .claude-sync/"
    fi
    exit 0
fi

echo
read -p "Apply these changes? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# Apply — excluding NEXT_SESSION_PROMPT.md
for f in $(cd "$SOURCE_DIR" && find . -type f | sed 's|^\./||'); do
    if [ "$f" = "NEXT_SESSION_PROMPT.md" ]; then
        continue
    fi
    SRC="$SOURCE_DIR/$f"
    DEST_DIR=$(dirname "$f")
    mkdir -p "$DEST_DIR"
    cp "$SRC" "$f"
done

# Handle NEXT_SESSION_PROMPT.md: drop into .claude-sync/ (gitignored)
if [ -f "$NEXT_PROMPT_SRC" ]; then
    mkdir -p .claude-sync
    cp "$NEXT_PROMPT_SRC" .claude-sync/NEXT_SESSION_PROMPT.md
fi

# Lint: check that new CHANGELOG entries have Authority markers.
# Approach: find every "## YYYY-MM-DD" heading added in the diff, then check
# the CURRENT FILE (not the diff) for whether each heading's block contains
# an **Authority:** line. This avoids false positives when an existing entry's
# heading is flagged as "added" due to repositioning in the diff.
CHANGELOG_PATH="docs/CHANGELOG.md"
if [ -f "$CHANGELOG_PATH" ]; then
    NEW_HEADINGS=$(git diff HEAD -- "$CHANGELOG_PATH" | grep -E '^\+## [0-9]{4}-' | sed 's/^\+//' || true)
    if [ -n "$NEW_HEADINGS" ]; then
        MISSING_AUTH=0
        while IFS= read -r heading; do
            [ -z "$heading" ] && continue
            # Search the current file: starting at this heading line, read until
            # the next ## heading, and check whether **Authority:** appears.
            if ! awk -v h="$heading" '
                $0 == h { in_block=1; next }
                in_block && /^## / { exit (found ? 0 : 1) }
                in_block && /\*\*Authority:\*\*/ { found=1 }
                END { exit (found ? 0 : 1) }
            ' "$CHANGELOG_PATH"; then
                if [ $MISSING_AUTH -eq 0 ]; then
                    echo
                    echo "⚠  WARNING: new CHANGELOG entries without **Authority:** lines:"
                fi
                echo "     $heading"
                MISSING_AUTH=1
            fi
        done <<< "$NEW_HEADINGS"
        if [ $MISSING_AUTH -eq 1 ]; then
            echo "   Per docs/WORKFLOW.md, every entry needs an Authority marker."
            echo "   Fix these before committing."
        fi
    fi
fi

echo
echo "=== SUMMARY DIFF ==="
git diff --stat $DOC_PATHS 2>/dev/null

echo
echo "✓ Changes applied. Review with:"
echo "    git diff $DOC_PATHS"
echo
echo "When ready to commit:"
echo "    git add $DOC_PATHS"
echo "    git commit -m 'design: <describe the change>'"
echo "    git tag chat-\$(date +%Y%m%d)"

# Show the NEXT_SESSION_PROMPT to the user
if [ -f .claude-sync/NEXT_SESSION_PROMPT.md ]; then
    echo
    echo "============================================================"
    echo "=== NEXT_SESSION_PROMPT.md (for next Claude Code session) ==="
    echo "============================================================"
    echo
    cat .claude-sync/NEXT_SESSION_PROMPT.md
    echo
    echo "============================================================"
    echo "(also saved at .claude-sync/NEXT_SESSION_PROMPT.md)"
    echo "============================================================"
elif [ $NEXT_PROMPT_MISSING -eq 1 ]; then
    echo
    echo "⚠  No NEXT_SESSION_PROMPT.md was delivered. Ask Claude chat for one"
    echo "   before starting your next Claude Code session, or write one manually."
fi
