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
#   FLAVOR=default | vpp | ask  → stage from kernel/common + kernel/flavors/$FLAVOR
#
# ASK 2.0 (rewrite-in-progress): the legacy `ask` branch that delegated
# to ci-consume-ask-kernel.sh was removed on the ask20 branch. Until ASK
# 2.0 lands per specs/ask-2.0-rewrite-spec.md, FLAVOR=ask is staged the
# same way as default/vpp (vanilla kernel, no SDK, no ASK fast-path).
#
# Called by: .github/workflows/auto-build.yml "Stage kernel tree" step.
# Expects:   GITHUB_WORKSPACE set, or run from repo root.

set -euo pipefail
cd "${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

case "$FLAVOR" in
    default|vpp|ask)
        echo "### FLAVOR=$FLAVOR — staging kernel via kernel/common/scripts/stage-kernel.sh"
        exec bash kernel/common/scripts/stage-kernel.sh --flavor "$FLAVOR" "$@"
        ;;
    *)
        echo "ci-stage-kernel.sh: ERROR — unknown FLAVOR='$FLAVOR'" >&2
        exit 1
        ;;
esac
