#!/bin/bash
# bin/ci-stage-kernel.sh — kernel staging for CI.
#
# Thin wrapper around kernel/common/scripts/stage-kernel.sh that:
#   1. Sources bin/common.sh to resolve KERNEL_VERSION.
#   2. Invokes stage-kernel.sh.
#   3. Stages the resulting kernel tree where vyos-build's package-build
#      pipeline expects it (vyos-build/scripts/package-build/linux-kernel/...).
#
# There is one image. The default|vpp|ask FLAVOR routing was removed
# 2026-07-26 (flavor split retired 2026-06-14): every branch staged from
# kernel/common anyway, so the case statement only ever picked one path.
#
# Called by: .github/workflows/auto-build.yml "Stage kernel tree" step.
# Expects:   GITHUB_WORKSPACE set, or run from repo root.

set -euo pipefail
cd "${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

echo "### Staging kernel via kernel/common/scripts/stage-kernel.sh"
exec bash kernel/common/scripts/stage-kernel.sh "$@"
