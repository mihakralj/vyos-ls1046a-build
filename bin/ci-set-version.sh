#!/bin/bash
# ci-set-version.sh — Set build version, timestamp, and builder identity
# Called by: .github/workflows/auto-build.yml "Set env variables" step
# Inputs (env): INPUT_BUILD_BY, INPUT_BUILD_VERSION, REPO_OWNER_ID, REPO_OWNER
# Outputs: writes to $GITHUB_ENV and $GITHUB_OUTPUT
set -ex

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
echo "PREVIOUS_SUCCESS_BUILD_TIMESTAMP=$(cat version.json | jq -r '.[0].timestamp')" >> "$GITHUB_OUTPUT"
