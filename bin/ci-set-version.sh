#!/bin/bash
# ci-set-version.sh — Set build version, timestamp, and builder identity
# Called by: .github/workflows/auto-build.yml "Set env variables" step
# Inputs (env): INPUT_BUILD_BY, INPUT_BUILD_VERSION, REPO_OWNER_ID, REPO_OWNER
# Outputs: writes to $GITHUB_ENV and $GITHUB_OUTPUT
set -ex -o pipefail

if [ -n "$INPUT_BUILD_BY" ]; then
  echo "BUILD_BY=$INPUT_BUILD_BY" >> "$GITHUB_ENV"
  echo "BUILD_BY=$INPUT_BUILD_BY" >> "$GITHUB_OUTPUT"
else
  echo "BUILD_BY=${REPO_OWNER_ID}+${REPO_OWNER}@users.noreply.github.com" >> "$GITHUB_ENV"
  echo "BUILD_BY=${REPO_OWNER_ID}+${REPO_OWNER}@users.noreply.github.com" >> "$GITHUB_OUTPUT"
fi

if [ -z "$INPUT_BUILD_VERSION" ]; then
  echo "build_version=$(date -u +%Y.%m.%d-%H%M)-rolling" >> "$GITHUB_OUTPUT"
else
  echo "build_version=$INPUT_BUILD_VERSION" >> "$GITHUB_OUTPUT"
fi

echo "TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$GITHUB_OUTPUT"

# PREVIOUS_SUCCESS_BUILD_TIMESTAMP feeds the publish job's `git log --since`
# call to compose the release notes.
# On nxp-sdk: read version-nxp.json.
# On main/other branches: read the canonical version.json.
# Fall back to the epoch on the very first build when no feed exists yet.
FEED_FILE="version.json"
if [[ "${GITHUB_REF_NAME:-}" == nxp-sdk* ]] && [ -s version-nxp.json ]; then
    FEED_FILE="version-nxp.json"
fi

if [ -s "$FEED_FILE" ]; then
    if command -v jq >/dev/null 2>&1; then
        PREV_TS=$(jq -r '.[0].timestamp // empty' "$FEED_FILE")
    else
        PREV_TS=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d[0].get("timestamp","") if isinstance(d,list) and d else "")' "$FEED_FILE")
    fi
else
    PREV_TS=""
fi
[ -z "$PREV_TS" ] && PREV_TS="1970-01-01T00:00:00Z"
echo "PREVIOUS_SUCCESS_BUILD_TIMESTAMP=$PREV_TS" >> "$GITHUB_OUTPUT"
