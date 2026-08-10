#!/usr/bin/env bash
# qdrant-reconcile.sh — identify knowledge gaps between git commits and qdrant
# Usage: bin/qdrant-reconcile.sh [days_back]
# Default: 14 days back. Outputs commit clusters that may be missing from qdrant.
set -euo pipefail

DAYS="${1:-14}"
SINCE="$(date -d "-${DAYS} days" +%Y-%m-%d)"

echo "=== QDRANT RECONCILIATION REPORT ==="
echo "Period: ${SINCE} to $(date +%Y-%m-%d)"
echo ""

# Count commits in period
TOTAL=$(git log --oneline --since="${SINCE}" | wc -l)
echo "Total commits in period: ${TOTAL}"
echo ""

# Categorize commits
echo "--- COMMIT CATEGORIES ---"
echo ""

FIXES=$(git log --oneline --since="${SINCE}" | grep -iE "fix|F-[0-9]" | wc -l)
DOCS=$(git log --oneline --since="${SINCE}" | grep -iE "docs|plan|spec|arch" | wc -l)
BUILD=$(git log --oneline --since="${SINCE}" | grep -iE "build|ci|chore" | wc -l)
FEAT=$(git log --oneline --since="${SINCE}" | grep -iE "feat" | wc -l)
REVERT=$(git log --oneline --since="${SINCE}" | grep -iE "revert" | wc -l)

echo "  Fixes (F-xxx, fix):     ${FIXES}"
echo "  Documentation:          ${DOCS}"
echo "  Build/CI:               ${BUILD}"
echo "  Features:               ${FEAT}"
echo "  Reverts:                ${REVERT}"
echo ""

# Extract fixup IDs from commits
echo "--- FIXUP IDs IN PERIOD ---"
git log --oneline --since="${SINCE}" | grep -oE "F-[0-9]+" | sort -t'-' -k2 -n -u
echo ""

# List all commits for agent review
echo "--- FULL COMMIT LOG (newest first) ---"
git log --format="%h %ai %s" --since="${SINCE}"
echo ""

# Identify high-value knowledge signals
echo "--- HIGH-VALUE KNOWLEDGE SIGNALS ---"
echo "(These commit patterns almost always contain qdrant-worthy knowledge)"
echo ""

echo "Board test results:"
git log --oneline --since="${SINCE}" | grep -iE "board|silicon|HIT|MISS|test.*pass|test.*fail|soak|deploy|ISO" || echo "  (none)"
echo ""

echo "Root cause findings:"
git log --oneline --since="${SINCE}" | grep -iE "root cause|discovered|found|oracle|decisive|confirmed|disproven|refuted" || echo "  (none)"
echo ""

echo "Architectural decisions:"
git log --oneline --since="${SINCE}" | grep -iE "retire|un-retire|architectural|pivot|shipping|dead.end|decision" || echo "  (none)"
echo ""

echo "Reverts and oscillations:"
git log --oneline --since="${SINCE}" | grep -iE "revert|oscillat|supersed" || echo "  (none)"
echo ""

echo "Iteration sagas (multiple versions of same fix):"
git log --oneline --since="${SINCE}" | grep -oE "F-[0-9]+.*v[0-9]+" | sort -u || echo "  (none)"
echo ""

echo "=== ACTION REQUIRED ==="
echo "1. Run 'qdrant-find' for each fixup ID listed above"
echo "2. For each HIGH-VALUE signal, check if qdrant already holds the knowledge"
echo "3. For any gap found: create dense prose and 'qdrant-store' it"
echo "4. Include: symptom, root cause, fix (paths + patch numbers), verification, date, tags"
echo ""
echo "Rule: every board test result, root cause finding, architectural decision,"
echo "revert/oscillation, and multi-iteration saga MUST have a qdrant entry."
