#!/usr/bin/env bash
# run-pipeline.sh — orchestrate the ASK 6.6 build pipeline end-to-end.
#
# Pipeline (post-redistribution; the build is now self-contained):
#   1. fetch-kernel.sh       (linux-6.6.y stable tarball)
#   2. patch-health.sh       (verify in-tree patches apply cleanly)
#   3. apply-to-tree.sh      (--build: stamp kernel tree)
#   4. build-kernel.sh       (--build: produce linux-image/headers .debs)
#   5. build-ask-modules.sh  (--ask-extras: cdx/fci/auto_bridge OOT .deb)
#   6. build-ask-iptables.sh (--ask-extras: patched iptables + QOSMARK xt)
#   7. build-ask-ppp.sh      (--ask-extras: patched ppp + rp-pppoe)
#   8. publish-binaries.sh   (--release-binaries: upload to GitHub Release)
#
# All inputs are committed in this repo:
#   - Kernel patches:    release/patches/{vyos,ask,fixes}/
#   - SDK overlay:       release/patches/kernel/sdk-sources/
#   - OOT module src:    release/oot-modules/{cdx,fci,auto_bridge,iptables-extensions}/
#   - Userspace patches: release/userspace-patches/{ppp,rp-pppoe}/
#   - Defconfig:         release/vyos-base/, release/ask.config
#
# Usage:
#   ./scripts/run-pipeline.sh                   # full pipeline (kernel only by default)
#   ./scripts/run-pipeline.sh 6.6.123           # pin kernel version
#   ./scripts/run-pipeline.sh --skip-fetch      # reuse existing work/linux-* cache
#   ./scripts/run-pipeline.sh --no-health       # skip patch-apply probe
#   ./scripts/run-pipeline.sh --build           # apply-to-tree + build-kernel
#   ./scripts/run-pipeline.sh --ask-extras      # + OOT modules + iptables + ppp
#   ./scripts/run-pipeline.sh --release-binaries # upload work/build/*.deb to Releases
#   ./scripts/run-pipeline.sh --dry-run         # print steps, do not execute
#
# Exit codes:
#   0  pipeline completed successfully
#   1  patch-health failed (patches do not apply)
#   4  --build: apply-to-tree or build-kernel failed
#   5  --release-binaries: publish-binaries failed

set -euo pipefail
source "$(dirname "$0")/common.sh"

# ── Argument parsing ────────────────────────────────────────────────────
KERNEL_VERSION_ARG=""
SKIP_FETCH=0
DO_HEALTH=1
DO_BUILD=0
DO_ASK_EXTRAS=0
DO_RELEASE_BIN=0
DRY_RUN=0

while (( $# )); do
    case "$1" in
        --skip-fetch)        SKIP_FETCH=1;     shift ;;
        --no-health)         DO_HEALTH=0;      shift ;;
        --build)             DO_BUILD=1;       shift ;;
        --ask-extras)        DO_ASK_EXTRAS=1; DO_BUILD=1; shift ;;
        --release-binaries)  DO_RELEASE_BIN=1; shift ;;
        --dry-run)           DRY_RUN=1;        shift ;;
        -h|--help)           sed -n '1,36p' "$0"; exit 0 ;;
        --) shift; break ;;
        -*) err "unknown flag: $1" ;;
        *)  KERNEL_VERSION_ARG="$1"; shift ;;
    esac
done

# ── Step runner ─────────────────────────────────────────────────────────
STEP=0
_STEP_START=0
_step_begin() {
    local label="$1"
    STEP=$((STEP+1))
    echo
    begin_group "Step $STEP: $label"
    info "── Step $STEP: $label ──"
    _STEP_START=$(date +%s)
}
_step_end() {
    local elapsed=$(( $(date +%s) - _STEP_START ))
    dim "   (step $STEP done in ${elapsed}s)"
    end_group
}

run_step() {
    local label="$1"; shift
    _step_begin "$label"
    dim "   \$ $*"
    if (( DRY_RUN )); then
        _step_end
        return 0
    fi
    "$@"
    _step_end
}

LAST_EXIT=0
run_step_softfail() {
    local allow_exit="$1"; shift
    local label="$1"; shift
    _step_begin "$label"
    dim "   \$ $*"
    if (( DRY_RUN )); then
        LAST_EXIT=0
        _step_end
        return 0
    fi
    set +e
    "$@"
    LAST_EXIT=$?
    set -e
    _step_end
    if (( LAST_EXIT != 0 && LAST_EXIT != allow_exit )); then
        err "step '$label' failed with exit $LAST_EXIT"
    fi
}

# "ship what builds" softfail: NEVER aborts the pipeline on any exit code.
# Used for the optional ASK extras layers where a single broken layer
# must not lose the kernel .debs.
run_step_tolerate_all() {
    local label="$1"; shift
    _step_begin "$label"
    dim "   \$ $*"
    if (( DRY_RUN )); then
        LAST_EXIT=0
        _step_end
        return 0
    fi
    set +e
    "$@"
    LAST_EXIT=$?
    set -e
    _step_end
    if (( LAST_EXIT != 0 )); then
        warn "step '$label' returned exit $LAST_EXIT (tolerated; see summary)"
    fi
}

# ── Banner ──────────────────────────────────────────────────────────────
echo
ok "ASK lts_6.6_ls1046a — pipeline starting"
[[ -n "$KERNEL_VERSION_ARG" ]] && dim "   pinned kernel: $KERNEL_VERSION_ARG"
(( SKIP_FETCH )) && dim "   --skip-fetch: reusing existing work/ caches"
(( DO_HEALTH  )) || dim "   --no-health:  patch-health will be skipped"
(( DO_BUILD   )) && dim "   --build:      apply-to-tree + build-kernel will run"
(( DO_ASK_EXTRAS )) && dim "   --ask-extras: + build-ask-modules (cdx/fci/auto_bridge OOT .debs)"
(( DO_RELEASE_BIN )) && dim "   --release-binaries: .debs → GitHub Release"
(( DRY_RUN    )) && warn "DRY-RUN: no commands will actually execute"

# ── 1. Fetch kernel tarball ─────────────────────────────────────────────
if (( SKIP_FETCH )); then
    shopt -s nullglob
    _kdirs=( "$WORK_DIR"/linux-*/ )
    shopt -u nullglob
    (( ${#_kdirs[@]} > 0 )) \
        || err "--skip-fetch but no work/linux-*/ found; run without --skip-fetch first"
    info "skipping kernel fetch (--skip-fetch); reusing: ${_kdirs[0]##*/}"
else
    if [[ -n "$KERNEL_VERSION_ARG" ]]; then
        run_step_softfail 10 "fetch kernel ($KERNEL_VERSION_ARG)" \
            "$SCRIPTS_DIR/fetch-kernel.sh" "$KERNEL_VERSION_ARG"
    else
        run_step_softfail 10 "fetch kernel (latest 6.6.y)" \
            "$SCRIPTS_DIR/fetch-kernel.sh"
    fi
fi

# ── 2. Patch health ─────────────────────────────────────────────────────
HEALTH_EXIT=0
if (( DO_HEALTH )); then
    run_step_softfail 1 "verify patches apply (patch-health)" \
        "$SCRIPTS_DIR/patch-health.sh"
    HEALTH_EXIT=$LAST_EXIT
fi

# ── 3. Build (optional) ─────────────────────────────────────────────────
BUILD_STATUS="skipped"
if (( DO_BUILD )); then
    if (( HEALTH_EXIT != 0 )); then
        warn "--build: refusing (patch-health failed)"
        BUILD_STATUS="refused (health failed)"
    elif (( ! DO_HEALTH )); then
        warn "--build: refusing (--no-health given; cannot verify safety)"
        BUILD_STATUS="refused (no-health)"
    else
        run_step_softfail 1 "apply artefacts to kernel tree (apply-to-tree)" \
            "$SCRIPTS_DIR/apply-to-tree.sh"
        APPLY_EXIT=$LAST_EXIT
        if (( APPLY_EXIT != 0 )); then
            BUILD_STATUS="apply-to-tree failed"
        else
            run_step_softfail 1 "build kernel (build-kernel)" \
                "$SCRIPTS_DIR/build-kernel.sh"
            BUILD_EXIT=$LAST_EXIT
            if (( BUILD_EXIT != 0 )); then
                BUILD_STATUS="build-kernel failed"
            else
                BUILD_STATUS="built (work/build/*.deb)"
            fi
        fi
    fi
fi

# ── 4. ASK extras: out-of-tree modules, userspace, xtables (optional) ───
ASK_MODULES_STATUS="skipped"
ASK_IPTABLES_STATUS="skipped"
ASK_PPP_STATUS="skipped"
if (( DO_ASK_EXTRAS )); then
    if [[ "$BUILD_STATUS" != "built"* ]]; then
        warn "--ask-extras: refusing (kernel build did not succeed)"
        ASK_MODULES_STATUS="refused (kernel build failed)"
        ASK_IPTABLES_STATUS="refused (kernel build failed)"
        ASK_PPP_STATUS="refused (kernel build failed)"
    else
        # Layer 1 + 2: OOT kernel modules from release/oot-modules/.
        # Exit 77 = soft-skip (NXP FMan SDK layer absent in tree); any other
        # non-zero is a real build failure that aborts the pipeline.
        run_step_softfail 77 "build ASK OOT modules (cdx/fci/auto_bridge)" \
            "$SCRIPTS_DIR/build-ask-modules.sh"
        if (( LAST_EXIT == 77 )); then
            ASK_MODULES_STATUS="skipped (NXP FMan SDK not layered — see build log)"
        elif (( LAST_EXIT == 0 )); then
            ASK_MODULES_STATUS="built (ask-modules-*.deb)"
        else
            ASK_MODULES_STATUS="build-ask-modules failed"
        fi

        # Layer 3 + 4: patched iptables + QOSMARK/QOSCONNMARK xtables plugins
        run_step_tolerate_all "build patched iptables (+libxt_QOSMARK/QOSCONNMARK)" \
            "$SCRIPTS_DIR/build-ask-iptables.sh"
        if (( LAST_EXIT == 0 )); then
            if compgen -G "$WORK_DIR/build/iptables_*+ask*_arm64.deb" >/dev/null; then
                ASK_IPTABLES_STATUS="built (iptables +ask*.deb)"
            else
                ASK_IPTABLES_STATUS="script succeeded but no .deb produced (investigate)"
            fi
        else
            ASK_IPTABLES_STATUS="build-ask-iptables failed"
        fi

        # Layer 5: ppp + rp-pppoe NXP/ASK patches
        run_step_tolerate_all "build patched ppp + rp-pppoe (NXP ASK offload/CMM)" \
            "$SCRIPTS_DIR/build-ask-ppp.sh"
        if (( LAST_EXIT == 0 )); then
            _ppp_built=0; _pppoe_built=0
            compgen -G "$WORK_DIR/build/ppp_*+ask*_arm64.deb"    >/dev/null && _ppp_built=1
            compgen -G "$WORK_DIR/build/pppoe_*+ask*_arm64.deb"  >/dev/null && _pppoe_built=1
            if (( _ppp_built && _pppoe_built )); then
                ASK_PPP_STATUS="built (ppp + rp-pppoe)"
            elif (( _ppp_built )); then
                ASK_PPP_STATUS="partial (ppp only; rp-pppoe missing)"
            elif (( _pppoe_built )); then
                ASK_PPP_STATUS="partial (rp-pppoe only; ppp missing)"
            else
                ASK_PPP_STATUS="script succeeded but no .deb produced (investigate)"
            fi
        elif (( LAST_EXIT == 2 )); then
            ASK_PPP_STATUS="patch rejected — manual reconciliation needed"
        else
            ASK_PPP_STATUS="build-ask-ppp failed"
        fi
    fi
fi

# ── 5. Release binaries (optional) ──────────────────────────────────────
RELEASE_BIN_STATUS="skipped"
if (( DO_RELEASE_BIN )); then
    if (( ! DO_BUILD )); then
        warn "--release-binaries: refusing (requires --build)"
        RELEASE_BIN_STATUS="refused (no --build)"
    elif [[ "$BUILD_STATUS" != "built"* ]]; then
        warn "--release-binaries: refusing (build did not succeed: $BUILD_STATUS)"
        RELEASE_BIN_STATUS="refused ($BUILD_STATUS)"
    else
        run_step_softfail 1 "upload binaries to GitHub Release (publish-binaries)" \
            "$SCRIPTS_DIR/publish-binaries.sh"
        if (( LAST_EXIT == 0 )); then
            RELEASE_BIN_STATUS="published"
        else
            RELEASE_BIN_STATUS="publish-binaries failed"
        fi
    fi
fi

# ── Summary ─────────────────────────────────────────────────────────────
echo
info "── Pipeline summary ──"
if (( DO_HEALTH )); then
    printf '   patch-health:    %s\n' "$( ((HEALTH_EXIT==0)) && echo 'all patches apply' || echo 'REJECTS' )"
else
    printf '   patch-health:    skipped\n'
fi
printf '   build-kernel:    %s\n' "$BUILD_STATUS"
printf '   ask-modules:     %s\n' "$ASK_MODULES_STATUS"
printf '   ask-iptables:    %s\n' "$ASK_IPTABLES_STATUS"
printf '   ask-ppp:         %s\n' "$ASK_PPP_STATUS"
printf '   release-bin:     %s\n' "$RELEASE_BIN_STATUS"

# ── Exit code policy ────────────────────────────────────────────────────
if (( HEALTH_EXIT != 0 )); then
    err "patch-health failed — kernel patches do not apply cleanly"
fi
if (( DO_BUILD )) && [[ "$BUILD_STATUS" != "built"* && "$BUILD_STATUS" != "skipped" ]]; then
    err "--build stage failed: $BUILD_STATUS"
fi
if (( DO_RELEASE_BIN )) && [[ "$RELEASE_BIN_STATUS" == *"failed"* ]]; then
    warn "--release-binaries: $RELEASE_BIN_STATUS"
    exit 5
fi

ok "pipeline complete"
exit 0