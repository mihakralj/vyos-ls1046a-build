#!/bin/bash
# bin/ci-stage-kernel.sh — kernel staging for CI (single collapsed image).
#
# Thin wrapper around kernel/common/scripts/stage-kernel.sh that:
#   1. Sources bin/common.sh to resolve KERNEL_VERSION.
#   2. Invokes stage-kernel.sh to assemble the kernel tree.
#   3. Stages the resulting kernel tree where vyos-build's package-build
#      pipeline expects it (vyos-build/scripts/package-build/linux-kernel/...).
#
# The multi-flavor (default | ask | vpp) build split was retired — there is
# ONE collapsed image. The kernel is always staged from kernel/common
# (mainline + board patches); the ASK2 in-tree patches stay parked under
# kernel/flavors/ask/ until ASK2 lands per specs/ask2-rewrite-spec.md.
#
# Expects: GITHUB_WORKSPACE set, or run from repo root.

set -euo pipefail
cd "${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

echo "### Staging kernel via kernel/common/scripts/stage-kernel.sh"
exec bash kernel/common/scripts/stage-kernel.sh "$@"
