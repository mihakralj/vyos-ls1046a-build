#!/bin/bash
# bin/ci-stage-kernel.sh — FLAVOR-aware kernel staging for CI.
#
# Thin wrapper around kernel/common/scripts/stage-kernel.sh that:
#   1. Sources bin/common.sh to resolve FLAVOR + KERNEL_VERSION.
#   2. Invokes the new FLAVOR-aware stage-kernel.sh path.
#   3. Stages the resulting kernel tree where vyos-build's package-build
#      pipeline expects it (vyos-build/scripts/package-build/linux-kernel/...).
#
# Routing:
#   FLAVOR=default | vpp  → stage from kernel/common + kernel/flavors/$FLAVOR
#   FLAVOR=ask            → defer to bin/ci-consume-ask-kernel.sh (legacy
#                           prebuilt-tag consumption path; replaced in PR 5).
#
# Called by: .github/workflows/auto-build.yml "Stage kernel tree" step.
# Expects:   GITHUB_WORKSPACE set, or run from repo root.

set -euo pipefail
cd "${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

case "$FLAVOR" in
    ask)
        echo "### FLAVOR=ask — delegating to ci-consume-ask-kernel.sh (legacy path)"
        # Honour the existing PIN/TAG override semantics.
        exec bin/ci-consume-ask-kernel.sh "$@"
        ;;
    default|vpp)
        echo "### FLAVOR=$FLAVOR — staging kernel via kernel/common/scripts/stage-kernel.sh"
        exec bash kernel/common/scripts/stage-kernel.sh --flavor "$FLAVOR" "$@"
        ;;
    *)
        echo "ci-stage-kernel.sh: ERROR — unknown FLAVOR='$FLAVOR'" >&2
        exit 1
        ;;
esac