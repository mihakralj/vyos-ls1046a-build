#!/bin/bash
# ci-set-version.sh — Set build version, timestamp, and builder identity
# Called by: .github/workflows/auto-build.yml "Set env variables" step
# Inputs (env): INPUT_BUILD_BY, INPUT_BUILD_VERSION, REPO_OWNER_ID, REPO_OWNER
# Outputs: writes to $GITHUB_ENV and $GITHUB_OUTPUT
set -ex

# Resolve FLAVOR (default | ask | vpp) so we read the right per-flavor feed
# below. bin/common.sh handles env-var → data/flavor.pin → "default" fallback.
BC_QUIET=1 source "$(dirname "$0")/common.sh"

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
# call to compose the release notes. Read it from the per-flavor feed so
# each flavor's release notes only cover commits since *that* flavor's
# last successful build. Fall back to the legacy version.json (which is
# the default-flavor alias) and finally the epoch if neither file exists
# (e.g. the very first build of a new flavor).
FEED="version-${FLAVOR}.json"
if [ -s "$FEED" ]; then
    PREV_TS=$(jq -r '.[0].timestamp // empty' "$FEED")
elif [ -s version.json ]; then
    # First-ever build of a new flavor: use the default stream's timestamp
    # so the changelog window is at least bounded.
    PREV_TS=$(jq -r '.[0].timestamp // empty' version.json)
else
    PREV_TS=""
fi
[ -z "$PREV_TS" ] && PREV_TS="1970-01-01T00:00:00Z"
echo "PREVIOUS_SUCCESS_BUILD_TIMESTAMP=$PREV_TS" >> "$GITHUB_OUTPUT"
