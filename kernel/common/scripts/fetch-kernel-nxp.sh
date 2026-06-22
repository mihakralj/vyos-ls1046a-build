#!/usr/bin/env bash
# fetch-kernel-nxp.sh — clone the NXP lf-6.12.49-2.2.0 kernel tree for ASK flavor
#
# The NXP kernel tree includes SDK DPAA drivers (sdk_fman, sdk_dpaa, fsl_qbman)
# and NXP-specific modifications to core headers (phylink, phy, networking) that
# the SDK drivers depend on. Vanilla kernel.org tarballs do NOT have these
# changes, so the ASK flavor MUST use this tree as the base.
#
# Source this or run directly. Uses git shallow-clone for speed; caches the
# bare repository in $WORK_DIR/nxp-linux-bare.git for re-use across runs.
#
# Side effects:
#   work/nxp-linux-bare.git/         (bare clone, reused across runs)
#   work/linux-6.12.49/               (extracted source tree)
#   work/.kernel-version              (contains "6.12.49")

set -euo pipefail
source "$(dirname "$0")/common.sh"

need git

NXP_REPO="https://github.com/nxp-qoriq/linux.git"
NXP_TAG="lf-6.12.49-2.2.0"
NXP_KVER="6.12.49"
BARE_DIR="$WORK_DIR/nxp-linux-bare.git"
SRCDIR="$WORK_DIR/linux-$NXP_KVER"

# ── Fetch or update bare clone ───────────────────────────────────────────
if [[ -d "$BARE_DIR" ]]; then
    # Check if the tag is already present — skip fetch if so (avoids
    # expensive depth-1 refetch on shallow clones).
    if git -C "$BARE_DIR" rev-parse --verify "refs/tags/$NXP_TAG" >/dev/null 2>&1; then
        info "NXP tag $NXP_TAG already in bare clone — skipping fetch"
    else
        info "updating NXP kernel bare clone (fetching $NXP_TAG)…"
        git -C "$BARE_DIR" fetch --tags --depth 1 origin "$NXP_TAG" 2>&1 | head -3
    fi
else
    info "cloning NXP kernel ($NXP_TAG) — this will take a few minutes…"
    git clone --bare --depth 1 --branch "$NXP_TAG" "$NXP_REPO" "$BARE_DIR" 2>&1 | tail -3
    ok "bare clone ready"
fi

# ── Extract working tree from bare clone ──────────────────────────────────
# Always extract fresh so re-runs are deterministic (matching fetch-kernel.sh
# behavior with tarball extraction).
if [[ -d "$SRCDIR" ]]; then
    info "removing previous NXP kernel tree…"
    rm -rf "$SRCDIR"
fi

info "extracting NXP kernel tree (linux-$NXP_KVER)…"
mkdir -p "$SRCDIR"
if ! git -C "$BARE_DIR" archive "$NXP_TAG" 2>/dev/null | tar -x -C "$SRCDIR" 2>&1; then
    warn "git archive failed — bare clone may be corrupted. Re-cloning…"
    rm -rf "$BARE_DIR"
    info "cloning NXP kernel ($NXP_TAG)…"
    git clone --bare --depth 1 --branch "$NXP_TAG" "$NXP_REPO" "$BARE_DIR" 2>&1 | tail -3
    ok "bare clone ready"
    mkdir -p "$SRCDIR"
    git -C "$BARE_DIR" archive "$NXP_TAG" | tar -x -C "$SRCDIR"
fi
ok "extracted to $SRCDIR"

# ── Record version ───────────────────────────────────────────────────────
echo "$NXP_KVER" > "$WORK_DIR/.kernel-version"

# ── Normalised state ─────────────────────────────────────────────────────
set +e
set +e
# fetch_state_write returns 0 on unchanged, 10 on new/changed. We always
# exit 0 on success — the state-change code is a diagnostic for the caller.
fetch_state_write "kernel" "$NXP_KVER" || true
set -e
STATE_RC=$?
set -e

# ── Summary ──────────────────────────────────────────────────────────────
KVER=$(awk '/^VERSION/{v=$3} /^PATCHLEVEL/{p=$3} /^SUBLEVEL/{s=$3} END{print v"."p"."s}' \
    "$SRCDIR/Makefile")
[[ "$KVER" == "$NXP_KVER" ]] || warn "Makefile reports $KVER, expected $NXP_KVER"

ok "kernel ready: ${SRCDIR} (${KVER})"
exit 0
