#!/usr/bin/env bash
# integration-test.sh — end-to-end acceptance test for kernel/ integration (PR 1+2).
#
# Verifies that the consumer's per-flavor kernel/ tree is functionally equivalent
# to the producer's release/ tree (kernel-ls1046a-build), per
# plans/INTEGRATION-PLAN.md §2 + §4.
#
# Runs four checkpoints:
#   A. Layout + count parity  — every required dir exists; file counts match producer.
#   B. FLAVOR=ask              — Pass: 16, 0 SDK conflicts, 266 SDK files.
#   C. FLAVOR=default          — Pass: 6, no SDK section.
#   D. FLAVOR=vpp              — Pass: 6, no SDK section (smoke; no vpp-specific patches yet).
#
# Plus assertions:
#   - 35 ASK-edit-marked SDK files preserved
#   - kernel/common/scripts/patch-health.sh exists + is executable
#   - per-flavor README.md present for default, ask, vpp
#
# Usage:
#   bash kernel/common/scripts/integration-test.sh
#
# Optional env:
#   PRODUCER_REPO=/path/to/kernel-ls1046a-build  # default: ../kernel-ls1046a-build
#   SKIP_PATCH_HEALTH=1                          # skip checkpoints B/C/D (layout-only)
#
# Exit codes:
#   0  all checkpoints pass — integration verified
#   1  at least one assertion failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PRODUCER_REPO="${PRODUCER_REPO:-$(cd "$REPO_ROOT/../kernel-ls1046a-build" 2>/dev/null && pwd || true)}"

# ── tiny logger (don't depend on common.sh here — keep tests independent) ──
_red=$'\033[31m'; _grn=$'\033[32m'; _ylw=$'\033[33m'; _cyn=$'\033[36m'; _rst=$'\033[0m'
pass() { printf '   %s✓%s %s\n' "$_grn" "$_rst" "$1"; PASSED=$((PASSED+1)); }
fail() { printf '   %s✗%s %s%s%s\n' "$_red" "$_rst" "$1" "${2:+ — }" "${2:-}"; FAILED=$((FAILED+1)); FAILURES+=("$1"); }
hdr()  { printf '\n%s== %s ==%s\n' "$_cyn" "$1" "$_rst"; }
note() { printf '   %s· %s%s\n' "$_ylw" "$1" "$_rst"; }
info() { printf '   %s· %s%s\n' "$_cyn" "$1" "$_rst"; }

PASSED=0; FAILED=0; FAILURES=()

# ─────────────────────────────────────────────────────────────────────
hdr "Checkpoint A — layout + count parity"

# A.1 — required dirs exist
for d in \
    kernel/common/patches/vyos \
    kernel/common/patches/board \
    kernel/common/patches/fixes \
    kernel/common/kernel-config \
    kernel/common/vyos-base \
    kernel/common/scripts \
    kernel/flavors/default/patches \
    kernel/flavors/default/kernel-config \
    kernel/flavors/ask/patches/ask \
    kernel/flavors/ask/patches/fixes \
    kernel/flavors/ask/kernel-config \
    kernel/flavors/ask/sdk-sources \
    kernel/flavors/ask/oot-modules \
    kernel/flavors/ask/userspace-patches \
    kernel/flavors/vpp/patches \
    kernel/flavors/vpp/kernel-config \
; do
    if [[ -d "$REPO_ROOT/$d" ]]; then
        pass "dir exists: $d"
    else
        fail "missing dir: $d"
    fi
done

# A.2 — patch counts (against expected, per INTEGRATION-PLAN + PR 2)
# After PR 3 (data/kernel-patches/ migration): board=1 (101-sfp-rollball)
# 100-hwmon-ina234 parked at plans/needs-forward-port/ — see README.
declare -A EXPECTED_PATCH_COUNT=(
    ["kernel/common/patches/vyos"]=2
    ["kernel/common/patches/board"]=1
    ["kernel/common/patches/fixes"]=4
    ["kernel/flavors/ask/patches/ask"]=7
    ["kernel/flavors/ask/patches/fixes"]=3
    ["kernel/flavors/default/patches"]=0
    ["kernel/flavors/vpp/patches"]=0
)
for d in "${!EXPECTED_PATCH_COUNT[@]}"; do
    actual=$(find "$REPO_ROOT/$d" -maxdepth 3 -type f -name '*.patch' 2>/dev/null | wc -l)
    expected="${EXPECTED_PATCH_COUNT[$d]}"
    if [[ "$actual" == "$expected" ]]; then
        pass "patch count: $d = $actual (expected $expected)"
    else
        fail "patch count: $d = $actual (expected $expected)"
    fi
done

# A.3 — total kernel patch count = 17 after PR 3 (16 producer-absorbed + 1 board)
TOTAL_PATCHES=$(find \
    "$REPO_ROOT/kernel/common/patches" \
    "$REPO_ROOT/kernel/flavors/ask/patches" \
    -maxdepth 3 -type f -name '*.patch' 2>/dev/null | wc -l)
if [[ "$TOTAL_PATCHES" == "17" ]]; then
    pass "total ASK kernel patches = 17 (16 producer + 1 consumer-board after PR 3)"
else
    fail "total ASK kernel patches = $TOTAL_PATCHES (expected 17)"
fi

# A.4 — SDK source file count = 266
SDK_FILES=$(find "$REPO_ROOT/kernel/flavors/ask/sdk-sources" -type f 2>/dev/null | wc -l)
if [[ "$SDK_FILES" == "266" ]]; then
    pass "SDK source file count = 266 (matches producer invariant)"
else
    fail "SDK source file count = $SDK_FILES (expected 266)"
fi

# A.5 — ASK-edit marker preservation = 35 files
ASK_EDIT_FILES=$(grep -rl 'ASK-edit' "$REPO_ROOT/kernel/flavors/ask/sdk-sources/" 2>/dev/null | wc -l)
if [[ "$ASK_EDIT_FILES" == "35" ]]; then
    pass "ASK-edit-marked SDK files = 35 (preserved from producer ask26..ask41)"
else
    fail "ASK-edit-marked SDK files = $ASK_EDIT_FILES (expected 35)"
fi

# A.6 — per-flavor README.md present
for f in default ask vpp; do
    if [[ -f "$REPO_ROOT/kernel/flavors/$f/README.md" ]]; then
        pass "README.md present: kernel/flavors/$f/"
    else
        fail "README.md missing: kernel/flavors/$f/"
    fi
done

# A.7 — patch-health.sh adapter
if [[ -x "$REPO_ROOT/kernel/common/scripts/patch-health.sh" ]]; then
    pass "patch-health.sh exists + executable"
else
    fail "patch-health.sh missing or not executable"
fi

# A.7b — sync-kernel-version.sh exists + tracks vyos-build/data/defaults.toml
SYNC="$REPO_ROOT/kernel/common/scripts/sync-kernel-version.sh"
if [[ -x "$SYNC" ]]; then
    pass "sync-kernel-version.sh exists + executable"
    UPSTREAM_KVER=$(awk -F'"' '/^[[:space:]]*kernel_version[[:space:]]*=/ { print $2; exit }' \
        "$REPO_ROOT/vyos-build/data/defaults.toml" 2>/dev/null || echo "")
    LOCK_KVER=$(awk -F'[:=}]' '/KERNEL_VERSION:=/ { gsub(/"/, "", $4); print $4; exit }' \
        "$REPO_ROOT/versions.lock" 2>/dev/null || echo "")
    if [[ -n "$UPSTREAM_KVER" ]]; then
        SYNC_OUT=$(bash "$SYNC" 2>/dev/null | grep '^KERNEL_VERSION=' | cut -d= -f2)
        if [[ "$SYNC_OUT" == "$UPSTREAM_KVER" ]]; then
            pass "sync-kernel-version → KERNEL_VERSION=$SYNC_OUT (matches vyos-build/data/defaults.toml)"
        else
            fail "sync-kernel-version → $SYNC_OUT (expected $UPSTREAM_KVER from vyos-build)"
        fi
        if [[ "$LOCK_KVER" == "$UPSTREAM_KVER" ]]; then
            pass "versions.lock pin ($LOCK_KVER) matches vyos-build upstream ($UPSTREAM_KVER)"
        else
            fail "versions.lock=$LOCK_KVER drifts from vyos-build upstream=$UPSTREAM_KVER (run sync-kernel-version.sh --update)"
        fi
    else
        note "vyos-build/data/defaults.toml not present — skipping upstream-pin assertions"
    fi
else
    fail "sync-kernel-version.sh missing or not executable"
fi

# A.8 — producer parity (if producer repo accessible, cross-check counts)
# After PR 3 the consumer adds 1 board patch on top of the 16 producer patches.
if [[ -n "$PRODUCER_REPO" && -d "$PRODUCER_REPO/release/patches" ]]; then
    PROD_TOTAL=$(find "$PRODUCER_REPO/release/patches/vyos" \
                       "$PRODUCER_REPO/release/patches/ask" \
                       "$PRODUCER_REPO/release/patches/fixes" \
                       -maxdepth 1 -type f -name '*.patch' 2>/dev/null | wc -l)
    CONSUMER_BOARD_COUNT=$(find "$REPO_ROOT/kernel/common/patches/board" -maxdepth 1 -type f -name '*.patch' 2>/dev/null | wc -l)
    EXPECTED_TOTAL=$((PROD_TOTAL + CONSUMER_BOARD_COUNT))
    PROD_SDK=$(find "$PRODUCER_REPO/release/patches/kernel/sdk-sources" -type f 2>/dev/null | wc -l)
    PROD_ASK_EDIT=$(grep -rl 'ASK-edit' "$PRODUCER_REPO/release/patches/kernel/sdk-sources/" 2>/dev/null | wc -l)

    [[ "$EXPECTED_TOTAL" == "$TOTAL_PATCHES" ]] \
        && pass "producer-parity: kernel patch count ($TOTAL_PATCHES == producer $PROD_TOTAL + consumer-board $CONSUMER_BOARD_COUNT)" \
        || fail "producer-parity: kernel patch count diverged (consumer=$TOTAL_PATCHES, producer+board=$EXPECTED_TOTAL)"
    [[ "$PROD_SDK" == "$SDK_FILES" ]] \
        && pass "producer-parity: SDK file count ($SDK_FILES == $PROD_SDK)" \
        || fail "producer-parity: SDK file count diverged (consumer=$SDK_FILES, producer=$PROD_SDK)"
    [[ "$PROD_ASK_EDIT" == "$ASK_EDIT_FILES" ]] \
        && pass "producer-parity: ASK-edit marker count ($ASK_EDIT_FILES == $PROD_ASK_EDIT)" \
        || fail "producer-parity: ASK-edit marker count diverged (consumer=$ASK_EDIT_FILES, producer=$PROD_ASK_EDIT)"
else
    note "PRODUCER_REPO not accessible at $PRODUCER_REPO — skipping cross-repo parity"
fi

# ─────────────────────────────────────────────────────────────────────
if [[ "${SKIP_PATCH_HEALTH:-0}" == "1" ]]; then
    note "SKIP_PATCH_HEALTH=1 — skipping checkpoints B/C/D"
else
    # Need a fresh kernel tree per checkpoint (patches are tested with --check
    # but SDK staging in checkpoint B mutates the tree).
    KVER_FILE="$REPO_ROOT/work/.kernel-version"
    [[ -f "$KVER_FILE" ]] || { fail "work/.kernel-version missing — run patch-health.sh once to fetch the kernel"; FAILED=$((FAILED+1)); }
    KVER=$(cat "$KVER_FILE" 2>/dev/null || echo "")
    KDIR="$REPO_ROOT/work/linux-$KVER"
    TARBALL="$REPO_ROOT/work/linux-$KVER.tar.xz"

    reset_tree() {
        if [[ -f "$TARBALL" ]]; then
            rm -rf "$KDIR"
            tar -xf "$TARBALL" -C "$REPO_ROOT/work/" 2>/dev/null
        fi
    }

    run_checkpoint() {
        local label="$1" flavor="$2" expect_pass="$3" expect_sdk="$4"
        hdr "Checkpoint $label — FLAVOR=$flavor"
        reset_tree
        local out
        if out=$(bash "$REPO_ROOT/kernel/common/scripts/patch-health.sh" --flavor "$flavor" 2>&1); then
            local actual_pass
            actual_pass=$(printf '%s\n' "$out" | grep -oP 'Pass: \K\d+' | head -1)
            if [[ "$actual_pass" == "$expect_pass" ]]; then
                pass "FLAVOR=$flavor → Pass: $actual_pass (expected $expect_pass)"
            else
                fail "FLAVOR=$flavor → Pass: ${actual_pass:-?} (expected $expect_pass)"
            fi
            if [[ "$expect_sdk" == "yes" ]]; then
                if printf '%s\n' "$out" | grep -q 'no SDK file conflicts (266 files'; then
                    pass "FLAVOR=$flavor → 0 SDK conflicts, 266 files"
                else
                    fail "FLAVOR=$flavor → SDK conflict assertion failed"
                fi
            else
                if printf '%s\n' "$out" | grep -q 'SDK file conflicts'; then
                    fail "FLAVOR=$flavor → unexpected SDK section in non-ASK flavor"
                else
                    pass "FLAVOR=$flavor → no SDK section (correct for non-ASK)"
                fi
            fi
        else
            fail "FLAVOR=$flavor → patch-health.sh exited non-zero" "see output above"
            printf '%s\n' "$out" | tail -10 | sed 's/^/      /'
        fi
    }

    # After PR 3: ask=17 (16 producer + 1 board), default=7 (2 vyos+1 board+4 fixes), vpp=7
    run_checkpoint B ask     17 yes
    run_checkpoint C default  7 no
    run_checkpoint D vpp      7 no
fi

# ─────────────────────────────────────────────────────────────────────
# Checkpoint E — stage-kernel.sh produces a buildable tree (default flavor)
if [[ "${SKIP_PATCH_HEALTH:-0}" != "1" && "${SKIP_BUILD:-0}" != "1" ]]; then
    hdr "Checkpoint E — stage-kernel.sh + kernel build (FLAVOR=default)"
    if [[ -x "$REPO_ROOT/kernel/common/scripts/stage-kernel.sh" ]]; then
        pass "stage-kernel.sh exists + executable"
    else
        fail "stage-kernel.sh missing or not executable"
    fi

    # Stage the tree (this resets via tarball internally)
    if bash "$REPO_ROOT/kernel/common/scripts/stage-kernel.sh" --flavor default >/tmp/stage-kernel.log 2>&1; then
        pass "stage-kernel.sh --flavor default → exit 0"
    else
        fail "stage-kernel.sh --flavor default → non-zero exit (see /tmp/stage-kernel.log)"
    fi

    # Verify .config has critical defconfig invariants
    KVER=$(cat "$REPO_ROOT/work/.kernel-version" 2>/dev/null || echo unknown)
    KCFG="$REPO_ROOT/work/linux-$KVER/.config"
    if [[ -f "$KCFG" ]]; then
        for sym in CONFIG_FSL_FMAN=y CONFIG_FSL_DPAA_ETH=y CONFIG_LEDS_LP5812 CONFIG_FSL_FMD_SHIM; do
            if grep -qE "^${sym}|^${sym%=*}=[ym]" "$KCFG"; then
                pass "default .config has $sym"
            else
                fail "default .config missing $sym"
            fi
        done
    else
        fail "default .config not produced at $KCFG"
    fi

    # Build the kernel Image (~2 min on aarch64 native, full ccache hit on rerun)
    if [[ -d "$REPO_ROOT/work/linux-$KVER" ]]; then
        info "compiling kernel Image (ARCH=arm64, $(nproc) jobs)…"
        local_start=$SECONDS
        if ( cd "$REPO_ROOT/work/linux-$KVER" && make ARCH=arm64 -j"$(nproc)" Image >/tmp/kbuild.log 2>&1 ); then
            elapsed=$((SECONDS - local_start))
            pass "kernel Image built (FLAVOR=default, ${elapsed}s)"

            # Verify Image is a valid ARM64 kernel
            IMG="$REPO_ROOT/work/linux-$KVER/arch/arm64/boot/Image"
            if file "$IMG" 2>/dev/null | grep -q 'Linux kernel ARM64 boot executable Image'; then
                pass "Image is a valid ARM64 kernel boot image ($(stat -c%s "$IMG") bytes)"
            else
                fail "Image at $IMG is not a valid ARM64 kernel"
            fi

            # Verify version banner.
            # NOTE: do NOT pipe `strings` into `grep -q`. With `set -o pipefail`
            # the early SIGPIPE from grep makes the pipeline exit non-zero even on
            # a successful match. Materialize the matches first, then test.
            BANNER=$(strings "$REPO_ROOT/work/linux-$KVER/vmlinux" 2>/dev/null \
                     | grep -m1 -E "^Linux version $KVER" || true)
            if [[ -n "$BANNER" ]]; then
                pass "vmlinux Linux version banner = $KVER"
            else
                fail "vmlinux version banner does not say Linux version $KVER"
            fi
        else
            fail "kernel Image build failed (see /tmp/kbuild.log — last 40 lines:)"
            tail -40 /tmp/kbuild.log | sed 's/^/      /'
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────
hdr "Verdict"
printf 'Pass: %d   Fail: %d\n' "$PASSED" "$FAILED"

if (( FAILED > 0 )); then
    echo "Failed assertions:"
    printf '  - %s\n' "${FAILURES[@]}"
    printf '\n%s✗ Integration NOT verified — see failures above.%s\n' "$_red" "$_rst"
    exit 1
fi
printf '\n%s✓ PR 1+2 integration VERIFIED — kernel/ tree is functionally equivalent to producer.%s\n' "$_grn" "$_rst"