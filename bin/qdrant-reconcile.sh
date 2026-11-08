#!/usr/bin/env bash
# qdrant-reconcile.sh — identify knowledge gaps between git commits and qdrant
# Usage: bin/qdrant-reconcile.sh [days_back]
# Default: 14 days back. Outputs commit clusters that may be missing from qdrant.
set -euo pipefail

DAYS="${1:-14}"
SINCE="$(date -d "-${DAYS} days" +%Y-%m-%d)"
COMMITS=$(git log --oneline --since="${SINCE}")
FULL_LOG=$(git log --format="%h %ai %s" --since="${SINCE}")

echo "=== QDRANT RECONCILIATION REPORT ==="
echo "Period: ${SINCE} to $(date +%Y-%m-%d)"
echo ""

# Count commits in period
TOTAL=$(printf '%s\n' "$COMMITS" | awk 'NF { n++ } END { print n+0 }')
echo "Total commits in period: ${TOTAL}"
echo ""

# Categorize commits
echo "--- COMMIT CATEGORIES ---"
echo ""

count_matches() {
  printf '%s\n' "$COMMITS" | awk -v pattern="$1" \
    'tolower($0) ~ pattern { n++ } END { print n+0 }'
}
FIXES=$(count_matches 'fix|f-[0-9]')
DOCS=$(count_matches 'docs|plan|spec|arch')
BUILD=$(count_matches 'build|ci|chore')
FEAT=$(count_matches 'feat')
REVERT=$(count_matches 'revert')

echo "  Fixes (F-xxx, fix):     ${FIXES}"
echo "  Documentation:          ${DOCS}"
echo "  Build/CI:               ${BUILD}"
echo "  Features:               ${FEAT}"
echo "  Reverts:                ${REVERT}"
echo ""

# Extract fixup IDs from commits
echo "--- FIXUP IDs IN PERIOD ---"
printf '%s\n' "$COMMITS" | awk '{
  line = $0
  while (match(line, /F-[0-9]+/)) {
    print substr(line, RSTART, RLENGTH)
    line = substr(line, RSTART + RLENGTH)
  }
}' | sort -t'-' -k2 -n -u
echo ""

# List all commits for agent review
echo "--- FULL COMMIT LOG (newest first) ---"
[ -z "$FULL_LOG" ] || printf '%s\n' "$FULL_LOG"
echo ""

# Identify high-value knowledge signals
echo "--- HIGH-VALUE KNOWLEDGE SIGNALS ---"
echo "(These commit patterns almost always contain qdrant-worthy knowledge)"
echo ""

show_matches() {
  printf '%s\n' "$COMMITS" | awk -v pattern="$1" \
    'tolower($0) ~ pattern { print; found=1 } END { if (!found) print "  (none)" }'
}

echo "Board test results:"
show_matches 'board|silicon|hit|miss|test.*pass|test.*fail|soak|deploy|iso'
echo ""

echo "Root cause findings:"
show_matches 'root cause|discovered|found|oracle|decisive|confirmed|disproven|refuted'
echo ""

echo "Architectural decisions:"
show_matches 'retire|un-retire|architectural|pivot|shipping|dead.end|decision'
echo ""

echo "Reverts and oscillations:"
show_matches 'revert|oscillat|supersed'
echo ""

echo "Iteration sagas (multiple versions of same fix):"
printf '%s\n' "$COMMITS" | awk '
  match($0, /F-[0-9]+.*v[0-9]+/) { print substr($0, RSTART, RLENGTH); found=1 }
  END { if (!found) print "  (none)" }
' | sort -u
echo ""

echo "=== ACTION REQUIRED ==="
echo "1. Run 'qdrant-find' for each fixup ID listed above"
echo "2. For each HIGH-VALUE signal, check if qdrant already holds the knowledge"
echo "3. For any gap found: create dense prose and 'qdrant-store' it"
echo "4. Include: symptom, root cause, fix (paths + patch numbers), verification, date, tags"
echo ""
echo "Rule: every board test result, root cause finding, architectural decision,"
echo "revert/oscillation, and multi-iteration saga MUST have a qdrant entry."
