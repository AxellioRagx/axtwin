#!/bin/bash
# scripts/claude-export.sh
#
# Prepare a Claude chat session by bundling current doc state AND producing
# a single paste-ready OPENING_PROMPT.md that contains the delta since the
# last chat session, the workflow protocol rules, and placeholders for the
# user to fill in.
#
# Run from repo root or any subdirectory.
#
# Configuration (via .claude-workflow.env in repo root, or environment):
#   CLAUDE_DOC_PATHS         - which paths to export (default: "CLAUDE.md docs/")
#   CLAUDE_TAG_PREFIX        - tag prefix (default: "chat-")
#   CLAUDE_EXPORT_DIR        - output directory (default: /tmp/<repo>-exports)
#   CLAUDE_EXPORT_EXCLUDE    - space-separated globs to leave OUT of the tarball.
#                              The append-only registers (LESSONS/PRODUCT_GAPS) grow
#                              into hundreds of KB and are pipeline-internal; a chat
#                              session rarely needs them.
#   CLAUDE_EXPORT_MAX_MB     - warn past this bundle size (default: 2). A mature
#                              project's docs/ outgrows a chat context window; this
#                              catches it before you paste rather than after.
#   CLAUDE_CHANGELOG_ENTRIES - include only the newest N CHANGELOG entries instead
#                              of the whole file (default: 0 = whole file).
#
# Anything excluded or truncated is DISCLOSED in the opening prompt, so chat knows
# its picture is partial rather than silently working from a gap.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Auto-source project-local config if present
if [ -f .claude-workflow.env ]; then
    # shellcheck disable=SC1091
    source .claude-workflow.env
fi

REPO_NAME=$(basename "$REPO_ROOT")
TAG_PREFIX="${CLAUDE_TAG_PREFIX:-chat-}"
DOC_PATHS="${CLAUDE_DOC_PATHS:-CLAUDE.md docs/}"
OUT_DIR="${CLAUDE_EXPORT_DIR:-/tmp/${REPO_NAME}-exports}"
EXPORT_EXCLUDE="${CLAUDE_EXPORT_EXCLUDE:-}"
MAX_MB="${CLAUDE_EXPORT_MAX_MB:-2}"
CHANGELOG_ENTRIES="${CLAUDE_CHANGELOG_ENTRIES:-0}"

mkdir -p "$OUT_DIR"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
COMMIT_HASH=$(git rev-parse --short HEAD)
COMMIT_SUBJECT=$(git log -1 --pretty=format:"%s")
BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")

EXPORT_TAR="$OUT_DIR/${REPO_NAME}-${TIMESTAMP}-${COMMIT_HASH}.tar.gz"
OPENING_PROMPT="$OUT_DIR/${REPO_NAME}-${TIMESTAMP}-OPENING_PROMPT.md"

# Determine which paths actually exist
ACTUAL_PATHS=()
for p in $DOC_PATHS; do
    if [ -e "$p" ]; then
        ACTUAL_PATHS+=("$p")
    fi
done

if [ ${#ACTUAL_PATHS[@]} -eq 0 ]; then
    echo "ERROR: none of the doc paths exist: $DOC_PATHS"
    echo "Hint: set CLAUDE_DOC_PATHS in .claude-workflow.env"
    exit 1
fi

# Warn on uncommitted changes
if ! git diff --quiet HEAD -- "${ACTUAL_PATHS[@]}" 2>/dev/null; then
    echo "⚠  WARNING: uncommitted changes in doc paths."
    echo "   Export will include uncommitted state."
    git status --short "${ACTUAL_PATHS[@]}"
    echo
    read -p "   Continue? [y/N] " -n 1 -r
    echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
fi

# Find the most recent chat tag
LAST_CHAT_TAG=$(git tag -l "${TAG_PREFIX}*" | sort -V | tail -1 || true)

# --- Stage the bundle --------------------------------------------------------
# Staged (rather than tarred straight from the repo) so exclusions and CHANGELOG
# truncation are applied to a copy, the real size is known BEFORE the prompt is
# written, and the prompt can disclose exactly what chat is and isn't getting.
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
BUNDLE="$STAGING/$REPO_NAME"
mkdir -p "$BUNDLE"

TAR_EXCLUDES=()
EXCLUDED_FILES=()
for g in $EXPORT_EXCLUDE; do
    TAR_EXCLUDES+=(--exclude="$g")
    for f in $g; do
        [ -e "$f" ] && EXCLUDED_FILES+=("$f")
    done
done

tar cf - "${TAR_EXCLUDES[@]}" "${ACTUAL_PATHS[@]}" | (cd "$BUNDLE" && tar xf -)

# Optionally keep only the newest N CHANGELOG entries (the file is newest-first).
CHANGELOG_NOTE=""
CL_REL="docs/CHANGELOG.md"
if [ "$CHANGELOG_ENTRIES" -gt 0 ] && [ -f "$BUNDLE/$CL_REL" ]; then
    TOTAL_ENTRIES=$(grep -c '^## [0-9][0-9][0-9][0-9]-' "$BUNDLE/$CL_REL" || true)
    if [ "${TOTAL_ENTRIES:-0}" -gt "$CHANGELOG_ENTRIES" ]; then
        awk -v n="$CHANGELOG_ENTRIES" '
            /^## [0-9][0-9][0-9][0-9]-/ { count++ }
            count > n { stop=1 }
            stop != 1 { print }
        ' "$BUNDLE/$CL_REL" > "$BUNDLE/$CL_REL.tmp"
        {
            echo
            echo "---"
            echo
            echo "_**Truncated for this export.** Showing the $CHANGELOG_ENTRIES newest of"
            echo "$TOTAL_ENTRIES entries. Older decisions are still binding — ask for a specific"
            echo "one rather than assuming it doesn't exist._"
        } >> "$BUNDLE/$CL_REL.tmp"
        mv "$BUNDLE/$CL_REL.tmp" "$BUNDLE/$CL_REL"
        CHANGELOG_NOTE="newest $CHANGELOG_ENTRIES of $TOTAL_ENTRIES entries"
    fi
fi

# Size guard: a mature project's docs/ outgrows a chat context window quietly.
BUNDLE_KB=$(du -sk "$BUNDLE" 2>/dev/null | awk '{print $1}')
BUNDLE_KB=${BUNDLE_KB:-0}
MAX_KB=$(( MAX_MB * 1024 ))
if [ "$BUNDLE_KB" -gt "$MAX_KB" ]; then
    echo "⚠  WARNING: bundle is ${BUNDLE_KB}KB (over the ${MAX_MB}MB guideline)."
    echo "   Chat may truncate or refuse it. Largest files:"
    BIGGEST=$(find "$BUNDLE" -type f -exec du -k {} + 2>/dev/null | sort -rn | head -5)
    echo "$BIGGEST" | while read -r kb path; do
        printf '     %6sKB  %s\n' "$kb" "${path#$BUNDLE/}"
    done
    # Never suggest dropping what chat cannot work without: CLAUDE.md is the
    # orientation, WORKFLOW.md is the protocol it must follow, and the CHANGELOG
    # has its own truncation knob rather than being cut entirely.
    SUGGEST=$(echo "$BIGGEST" | awk -v p="$BUNDLE/" '
        { sub(p, "", $2)
          if ($2 == "CLAUDE.md" || $2 == "docs/WORKFLOW.md" || $2 == "docs/CHANGELOG.md") next
          if (n++ < 3) printf "%s%s", (n > 1 ? " " : ""), $2 }')
    echo
    echo "   To slim it down, set in .claude-workflow.env:"
    if [ -n "$SUGGEST" ]; then
        echo "     export CLAUDE_EXPORT_EXCLUDE=\"$SUGGEST\""
    else
        echo "     export CLAUDE_EXPORT_EXCLUDE=\"docs/LESSONS.md docs/PRODUCT_GAPS.md\""
    fi
    echo "     export CLAUDE_CHANGELOG_ENTRIES=20    # newest N entries only"
    echo "   (CLAUDE.md and docs/WORKFLOW.md are never worth excluding — chat needs both.)"
    echo
    if [ -t 0 ]; then
        read -p "   Continue anyway? [y/N] " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    else
        echo "   (non-interactive — continuing)"
    fi
fi

# Build the opening prompt — this is the single file the user pastes to chat
{
    cat <<'PROMPT_HEADER'
# Opening Prompt — Claude Chat Session

**Copy everything below this line and paste it as the first message in a new Claude chat session, after attaching the tarball.** Fill in the two `[FILL IN]` placeholders before sending.

---

Here is the current project state. The attached tarball contains the full current docs, including `docs/WORKFLOW.md`. **Read `docs/WORKFLOW.md` before proposing any changes.** It contains the output format rules and collaboration protocol you must follow this session.

PROMPT_HEADER

    echo "## Project snapshot"
    echo
    echo "- **Repository:** \`$REPO_NAME\`"
    echo "- **Branch:** \`$BRANCH\`"
    echo "- **HEAD commit:** \`$COMMIT_HASH\` — $COMMIT_SUBJECT"
    echo "- **Export timestamp:** $TIMESTAMP"
    echo "- **Exported paths:**"
    for p in "${ACTUAL_PATHS[@]}"; do echo "  - \`$p\`"; done
    echo "- **Bundle size:** ${BUNDLE_KB}KB"
    if [ ${#EXCLUDED_FILES[@]} -gt 0 ] || [ -n "$CHANGELOG_NOTE" ]; then
        echo
        echo "### What this bundle does NOT contain"
        echo
        if [ ${#EXCLUDED_FILES[@]} -gt 0 ]; then
            echo "Excluded from the tarball (they exist in the repo):"
            for f in "${EXCLUDED_FILES[@]}"; do echo "  - \`$f\`"; done
        fi
        if [ -n "$CHANGELOG_NOTE" ]; then
            echo "  - \`docs/CHANGELOG.md\` is **truncated** — $CHANGELOG_NOTE."
        fi
        echo
        echo "Treat these as *present but not shown*, not as absent. If a decision or"
        echo "rule you need would live in one, **ask me for it** rather than assuming it"
        echo "doesn't exist or re-deciding it."
    fi
    echo
    echo "## Recent commits (last 15)"
    echo
    echo '```'
    git log --oneline -15
    echo '```'
    echo

    if [ -n "$LAST_CHAT_TAG" ]; then
        echo "## Changes since last chat session (\`$LAST_CHAT_TAG\`)"
        echo
        echo "### Commits touching exported paths"
        echo
        echo '```'
        if git log "$LAST_CHAT_TAG..HEAD" --oneline -- "${ACTUAL_PATHS[@]}" 2>/dev/null | grep -q .; then
            git log "$LAST_CHAT_TAG..HEAD" --oneline -- "${ACTUAL_PATHS[@]}"
        else
            echo "(no doc changes since $LAST_CHAT_TAG)"
        fi
        echo '```'
        echo
        echo "### File-level summary"
        echo
        echo '```'
        git diff --stat "$LAST_CHAT_TAG..HEAD" -- "${ACTUAL_PATHS[@]}" 2>/dev/null || echo "(no diff)"
        echo '```'
        echo
        echo "### Files modified since last chat"
        echo
        echo '```'
        git diff --name-only "$LAST_CHAT_TAG..HEAD" -- "${ACTUAL_PATHS[@]}" 2>/dev/null || echo "(none)"
        echo '```'
    else
        echo "## First chat session"
        echo
        echo "No previous \`${TAG_PREFIX}*\` tag found. This appears to be the first chat export for this project, or previous sessions were not tagged."
    fi
    echo
    echo "## All existing chat tags"
    echo
    echo '```'
    git tag -l "${TAG_PREFIX}*" | sort -V || echo "(none)"
    echo '```'
    echo

    cat <<'PROMPT_PROTOCOL'
## Protocol reminder (from `docs/WORKFLOW.md`)

Before proposing any doc changes, confirm in your own words:

1. **Output format.** Your end-of-session deliverable is a single `.tar.gz` (not a zip, not a zip wrapping a tarball), containing updated docs at the top level plus a `NEXT_SESSION_PROMPT.md` file that tells Claude Code what to do next.
2. **CHANGELOG discipline.** Every new CHANGELOG entry needs an `**Authority:**` line. Entries that reverse prior decisions need a `**Supersedes:**` line.
3. **No silent overwrites.** Preserve changes Claude Code made since the last chat tag (listed above). If you need to override one, say so explicitly with rationale.

After you acknowledge those three rules, restate:

- What you see as the current state of the design.
- What changed since the last chat session (in your words, from the delta above).
- Any inconsistencies you notice between the docs.

Then wait for me to proceed. **Do not propose changes yet.**

## What I want to discuss this session

**Last session summary:** [FILL IN — one paragraph on what we decided and what was left open]

**Today's topic:** [FILL IN — what you want to work on this session]
PROMPT_PROTOCOL

} > "$OPENING_PROMPT"

# Build the archive from the staged copy (exclusions + truncation already applied)
tar czf "$EXPORT_TAR" -C "$STAGING" "$REPO_NAME"

# Update state tracker (informational; tags are the real anchor)
mkdir -p .claude-sync
echo "$COMMIT_HASH" > .claude-sync/last-export.txt
echo "$TIMESTAMP" >> .claude-sync/last-export.txt

# Summary output
cat <<EOF

✓ Export complete.

FILES:
  Tarball:         $EXPORT_TAR
  Opening prompt:  $OPENING_PROMPT

TO USE IN CLAUDE CHAT:
  1. Start a new Claude chat session.
  2. Attach the tarball:
       $EXPORT_TAR
  3. Open: $OPENING_PROMPT
     Fill in the two [FILL IN] placeholders near the bottom.
  4. Copy the entire file contents (or paste the file) as your first message.

AFTER THE CHAT:
  1. Download the tarball Claude produced (single .tar.gz — reject anything else).
  2. ./scripts/claude-import.sh <downloaded-tarball>
  3. git diff  # review carefully
  4. git add -A && git commit -m "design: ..."
  5. git tag ${TAG_PREFIX}\$(date +%Y%m%d)
  6. Read .claude-sync/NEXT_SESSION_PROMPT.md before opening the next Claude Code session.
EOF

# Try clipboard copy (Linux: xclip or wl-copy; Mac: pbcopy)
if command -v xclip >/dev/null 2>&1; then
    echo -n "$EXPORT_TAR" | xclip -selection clipboard 2>/dev/null && \
        echo "" && echo "(tarball path copied to clipboard)"
elif command -v wl-copy >/dev/null 2>&1; then
    echo -n "$EXPORT_TAR" | wl-copy 2>/dev/null && \
        echo "" && echo "(tarball path copied to clipboard)"
elif command -v pbcopy >/dev/null 2>&1; then
    echo -n "$EXPORT_TAR" | pbcopy && \
        echo "" && echo "(tarball path copied to clipboard)"
fi
