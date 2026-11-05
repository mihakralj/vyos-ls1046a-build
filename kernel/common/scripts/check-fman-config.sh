#!/usr/bin/env bash
# Reject configurations that allow normal removal of live FMan/DPAA devices.
set -euo pipefail

config="${1:?kernel .config path required}"
for symbol in CONFIG_FSL_FMAN CONFIG_FSL_DPAA CONFIG_FSL_DPAA_ETH; do
    if ! grep -qx "${symbol}=y" "$config" ||
       grep -Eq "^${symbol}=(m|n)$|^# ${symbol} is not set$" "$config"; then
        echo "ERROR: ${symbol} must be built in for safe FMan teardown" >&2
        exit 1
    fi
done
if ! grep -qx '# CONFIG_OF_OVERLAY is not set' "$config" ||
   grep -Eq '^CONFIG_OF_OVERLAY=(y|m|n)$' "$config"; then
    echo "ERROR: CONFIG_OF_OVERLAY must be disabled for safe FMan teardown" >&2
    exit 1
fi
