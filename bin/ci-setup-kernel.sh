#!/bin/bash
# ci-setup-kernel.sh — Kernel config overrides and build-kernel.sh injection
# Called by: .github/workflows/auto-build.yml "Setup kernel config" step
# Expects: GITHUB_WORKSPACE set
#
# ASK2 (rewrite-in-progress): the legacy ASK_KERNEL_TAG env var and the
# ci-consume-ask-kernel.sh / ci-setup-kernel-ask.sh helpers were deleted on
# the ask20 branch along with the ASK 1.x SDK kernel stack. This script
# now runs unconditionally for all flavors (default | ask | vpp). The
# ASK_KERNEL_TAG guard below is dead code kept only as a safety belt in
# case some external caller still injects the variable.
set -ex -o pipefail
cd "${GITHUB_WORKSPACE:-.}"

if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    echo "### ASK kernel in effect ($ASK_KERNEL_TAG) — skipping kernel defconfig/patches/injection"
    exit 0
fi

### LS1046A kernel config (DPAA1/FMan networking, eMMC, serial, MTD/SPI for FMan firmware)
DEFCONFIG=vyos-build/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig

# Remove upstream explicit disables that conflict with our overrides.
# kconfig defconfig processing doesn't reliably let later entries win
# when an earlier "# CONFIG_X is not set" is present.  Removing conflicting
# lines before appending ensures our values stick after make vyos_defconfig.
sed -i '/CONFIG_DEVTMPFS_MOUNT/d'          "$DEFCONFIG"
sed -i '/CONFIG_CPU_FREQ_DEFAULT_GOV/d'     "$DEFCONFIG"
sed -i '/CONFIG_DEBUG_PREEMPT/d'            "$DEFCONFIG"
sed -i '/CONFIG_THERMAL_GOV_FAIR_SHARE/d'   "$DEFCONFIG"
sed -i '/CONFIG_THERMAL_GOV_BANG_BANG/d'     "$DEFCONFIG"
sed -i '/CONFIG_CPU_IDLE_GOV_LADDER/d'       "$DEFCONFIG"
sed -i '/CONFIG_STRICT_DEVMEM/d'            "$DEFCONFIG"
sed -i '/CONFIG_IO_STRICT_DEVMEM/d'         "$DEFCONFIG"
sed -i '/CONFIG_CMA/d'                      "$DEFCONFIG"
sed -i '/CONFIG_DMA_CMA/d'                  "$DEFCONFIG"

# Append all flavor-agnostic LS1046A kernel config fragments from the
# canonical location kernel/common/kernel-config/. Files are numbered
# (00-board.config .. 08-dpaa1.config) so a plain glob expansion sorts
# alphabetically into the intended load order. Flavor-specific fragments
# live under kernel/flavors/<flavor>/kernel-config/ and are NOT picked up
# here. ASK2 (per specs/ask2-rewrite-spec.md) does not currently
# add any flavor-specific kernel-config fragments; if it grows them they
# would live under kernel/flavors/ask/kernel-config/ and need explicit
# wiring at that point.
#
# History: prior to Phase 1c of the repo-layout refactor (2026-05-11)
# these fragments were duplicated under data/kernel-config/ls1046a-*.config
# (long-prefix names, byte-identical to the numbered copies). data/ was
# the LIVE source then because this loop read from it; kernel/common/
# was unwired dead code. Phase 1c deleted the data/ duplicates and
# rewired this loop to the kernel/common/ canonical location, also
# moving the previously-orphan ls1046a-dpaa1.config in as 08-dpaa1.config.
# NOTE: DPDK PMD support has been removed (RC#31 — bus-level init kills kernel interfaces).
for frag in kernel/common/kernel-config/*.config; do
  echo "### Appending kernel config fragment: $(basename "$frag")"
  cat "$frag" >> "$DEFCONFIG"
done

# Override the VyOS-merged net-sched fragment for NET_SCH_FQ.
# vyos-build/scripts/package-build/linux-kernel/config/13-net-sched.config
# is processed by merge_config.sh AFTER our defconfig, and it explicitly
# sets CONFIG_NET_SCH_FQ=m, overriding our ls1046a-network-perf.config =y.
# Result on hardware: kernel boots with sysctl -p applying
# net.core.default_qdisc=fq before sch_fq.ko is loaded, producing
#   "Error -ENOENT writing to proc file to set sysctl parameter
#    'net.core.default_qdisc=fq'"
# and the qdisc silently stays at pfifo_fast. The pinned ASK kernel
# (kernel-6.6.137-askN release tarball) also ships =y for the same reason —
# see AGENTS.md.
NS_FRAG=vyos-build/scripts/package-build/linux-kernel/config/13-net-sched.config
if [ -f "$NS_FRAG" ]; then
    echo "### Forcing CONFIG_NET_SCH_FQ=y in $NS_FRAG (was =m → ENOENT at boot)"
    sed -i 's/^CONFIG_NET_SCH_FQ=m$/CONFIG_NET_SCH_FQ=y/' "$NS_FRAG"
fi

### Kernel patches (INA234 hwmon, SFP rollball PHY)
KERNEL_BUILD=vyos-build/scripts/package-build/linux-kernel
KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
mkdir -p "$KERNEL_PATCHES"

# 4002-hwmon-ina2xx-add-INA234-support.patch was authored against the
# kernel 6.6 ina2xx driver structure ("for the kernel 6.6 ina2xx driver
# structure (older driver lacks ina260/sy24655)" — patch header). On
# kernel 6.7+ the upstream `ina2xx` driver was refactored to add
# ina260/sy24655 entries (and INA234 itself landed upstream around
# 6.10), and the patch's hunks no longer match. Resolve which kernel
# series we are targeting via the same logic bin/common.sh uses, and
# only stage this patch for the 6.6 series.
KSERIES_FOR_PATCH=""
if [ -f vyos-build/data/defaults.toml ]; then
    KSERIES_FOR_PATCH=$(awk -F'"' '/^[[:space:]]*kernel_version[[:space:]]*=/{print $2}' \
        vyos-build/data/defaults.toml | awk -F. '{print $1"."$2}')
fi
if [ -z "$KSERIES_FOR_PATCH" ] && [ -f versions.lock ]; then
    KSERIES_FOR_PATCH=$(awk -F= '/KERNEL_SERIES/{gsub(/[" ]/,"",$2); print $2}' versions.lock)
fi

# INA234 hwmon patch (formerly kernel/flavors/ask/patches/fixes/4002-*) was
# only meaningful on the kernel 6.6 line, since INA234 is upstream from
# kernel 6.10 onwards. The default + vpp flavors track 6.18+, so the patch
# is unnecessary. ASK2 (rewrite-in-progress) tracks the same 6.18+
# kernel as the other flavors per specs/ask2-rewrite-spec.md — no
# special handling needed here.

# Shared LS1046A board patches now live under kernel/common/patches/board/.
# Source of truth: kernel/common/patches/board/{101,4005,4006,4007,4009}.patch.
# These cover SFP rollball PHY EINVAL fallback (101 = former 4003), the
# phylink in-band SFP fallback (4005), the DPAA XDP queue-index AF_XDP fix
# (4006), the LS1046A xhci/dwc3 quirks (4007) and the OEM SFP-10G-T quirk
# (4009). All are byte-identical to the formerly-duplicated copies under
# data/kernel-patches/ which were removed in the legacy-path tidy.
BOARD_PATCH_DIR=kernel/common/patches/board
[ -d "$BOARD_PATCH_DIR" ] || { echo "ERROR: $BOARD_PATCH_DIR missing"; exit 1; }

# Clean stale patches left by prior CI runs on the same self-hosted runner.
# Failure mode (observed 2026-05-11): a prior FLAVOR=ask build on the same
# runner workspace left 003-ask-kernel-hooks, 4002-hwmon-ina2xx,
# 4003-sfp-rollball-phylink-einval-fallback (legacy name of current 101) and
# 4004-swphy-support-10g-fixed-link-speed in $KERNEL_PATCHES. They were then
# applied alphabetically alongside the current default-flavor patches by
# build-kernel.sh's `for patch in ...; patch -p1` loop, which does NOT check
# exit codes. Legacy 4003 and current 101 both touch sfp.c near line 2667;
# the second-applied silently fails, corrupts subsequent line anchors, and
# 4009-sfp-oem-rollball-quirk's @@ -579 hunk silently misses its target.
# Net result: vmlinuz shipped without the OEM/SFP-10G-T quirk entry → SFP-10G-T
# copper modules fail with "no common interface modes" on FMan memac.
# Fix: nuke everything except vyos-build's own upstream 0001-/0003- patches
# before copying ours in.
echo "### Cleaning stale patches in $KERNEL_PATCHES (preserving 0001-*, 0003-*)"
find "$KERNEL_PATCHES" -maxdepth 1 -type f -name '*.patch' \
  ! -name '0001-*' ! -name '0003-*' -print -delete

echo "### Staging LS1046A board patches from $BOARD_PATCH_DIR"
cp "$BOARD_PATCH_DIR/101-sfp-rollball-phylink-fallback.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4005-phylink-inband-sfp-fallback.patch"  "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4006-dpaa-xdp-rxq-queue-index.patch"     "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4007-xhci-ls1046a-dwc3-quirks.patch"     "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4009-sfp-oem-rollball-quirk.patch"       "$KERNEL_PATCHES/"

# Stage critical flavor-agnostic kernel fix:
#   120-perf-libperf-asm-headers-srctree.patch — fixes arm64 perf build
#   failure ("No rule to make target ... tools/perf/libperf/arch/arm64/
#   include/generated/uapi/asm/unistd_64.h"). Required for FLAVOR=default
#   and FLAVOR=vpp on kernel 6.18+.
#
# We DO NOT bulk-stage kernel/common/patches/{vyos,fixes}/ because:
#   - kernel/common/patches/vyos/{001,003}-* are byte-identical duplicates
#     of vyos-build's upstream `0001-*`/`0003-*` patches (which the
#     cleanup glob already preserves) and re-applying them fails.
#   - kernel/common/patches/fixes/095-leds-lp5812-register.patch wires
#     LP5812 Kconfig/Makefile via a unified diff, but the inject block
#     below already does the same thing via heredoc echoes — applying
#     both produces a conflict / duplicate hunks.
COMMON_FIXES_DIR=kernel/common/patches/fixes
PERF_HEADERS_PATCH="$COMMON_FIXES_DIR/120-perf-libperf-asm-headers-srctree.patch"
if [ -f "$PERF_HEADERS_PATCH" ]; then
    echo "### Staging $(basename "$PERF_HEADERS_PATCH") (arm64 perf build fix)"
    cp "$PERF_HEADERS_PATCH" "$KERNEL_PATCHES/"
else
    echo "WARNING: $PERF_HEADERS_PATCH missing — kernel arm64 perf build will fail"
fi

# Stage PR14o diagnostic patch:
#   130-nf-flow-offload-log-alloc-failure.patch — adds a
#   net_warn_ratelimited() to nf_flow_table_offload.c's
#   flow_offload_work_add() silent-return path so the operator can see
#   when nf_flow_offload_alloc() fails and HW offload is aborted before
#   reaching the driver's FLOW_CLS_REPLACE cb. Required to diagnose the
#   M2 acceptance gate failure (2026-05-17: BIND fires, REPLACE never
#   does). Flavor-agnostic; safe for default/ask/vpp.
NF_FLOW_LOG_PATCH="$COMMON_FIXES_DIR/130-nf-flow-offload-log-alloc-failure.patch"
if [ -f "$NF_FLOW_LOG_PATCH" ]; then
    echo "### Staging $(basename "$NF_FLOW_LOG_PATCH") (PR14o nf_flow_table_offload alloc-failure diagnostic)"
    cp "$NF_FLOW_LOG_PATCH" "$KERNEL_PATCHES/"
else
    echo "WARNING: $NF_FLOW_LOG_PATCH missing — PR14o REPLACE-delivery diagnostic disabled"
fi

### FLAVOR=ask: stage the ASK2 in-tree kernel patches
#
# Per plans/ASK2-IMPLEMENTATION.md PR2/PR3 and spec §10, the ASK2
# kernel surface needs three small patches (currently placeholder stubs;
# real implementations land in M2):
#   0001-caam-qi-share.patch        — caam_qi_ext_consumer_register/release
#   0002-dpaa-eth-flow-block.patch  — TC_SETUP_BLOCK in dpaa_setup_tc()
#   0003-fman-host-command-api.patch — fman_host_cmd_send() + new header
#   0004-fman-pcd-subsystem.patch   — FMan PCD orchestration scaffold (PR14a)
#   0005-fman-pcd-kg-prep.patch     — FMan PCD KeyGen public API stub (PR14b-prep)
#   0006-fman-pcd-kg-body.patch     — FMan PCD KeyGen real KGSE_* programming (PR14b-body)
#
# Naming hazard: vyos-build's own upstream patch loop reserves the
# `0001-*` and `0003-*` filenames in $KERNEL_PATCHES (preserved by the
# cleanup glob above via `! -name '0001-*' ! -name '0003-*'`). Copying our
# patches in with their authored 0001/0002/0003 names would collide with
# vyos-build's reserved upstream patches and either silently overwrite
# them or fail to apply. Solution: rename to 1001/1002/1003 at staging
# time. The build-kernel.sh patch loop applies `find … | sort`-ordered,
# producing the deterministic apply order:
#     0001 0003 101 1001 1002 1003 1004 1005 1006 1007 4005 4006 4007 4009
# i.e. vyos-build's reserved patches first, then board patches, then
# ASK patches, then the rest of the board patches.
#
# Source-of-truth filenames in the repo stay 0001/0002/0003 because that
# matches the spec §10 numbering and the authoring rule (every patch is
# `git format-patch`-style starting at 0001). The rename happens ONLY in
# the staged copies. README.md under kernel/flavors/ask/patches/ documents
# this.
if [ "${FLAVOR:-default}" = "ask" ]; then
    ASK_PATCH_DIR=kernel/flavors/ask/patches
    if [ ! -d "$ASK_PATCH_DIR" ]; then
        echo "ERROR: FLAVOR=ask but $ASK_PATCH_DIR is missing"
        exit 1
    fi
    echo "### FLAVOR=ask — staging ASK2 in-tree kernel patches from $ASK_PATCH_DIR"
    # NOTE: slots 0032, 0033, 0034, 0036, 0037, 0038, 0042, 0043, 0045,
    # 0046, 0051 were archived to $ASK_PATCH_DIR/archive-grafted-2026-05-24/
    # on 2026-05-24 as part of the v1.3 Path A architecture (graft model +
    # OH-port two-stage chain abandoned, plus the cross-file keygen.c-deps
    # debugfs regdump 0045 and the now-orphaned 0051 revert of archived
    # 0043). The KEEP halves of the PARTIAL splits of 0033 + 0037 + 0046
    # live in the active series as 0054 + 0055 + 0056 respectively.  See
    # plans/ASK2-PHASE2-PATCH-TRIAGE.md for the full audit (Option C2).
    # Archived patches are NOT applied at build time — the subdirectory
    # is not globbed below.
    ASK_PATCH_COUNT=0
    for src_patch in "$ASK_PATCH_DIR"/0001-*.patch \
                     "$ASK_PATCH_DIR"/0002-*.patch \
                     "$ASK_PATCH_DIR"/0003-*.patch \
                     "$ASK_PATCH_DIR"/0004-*.patch \
                     "$ASK_PATCH_DIR"/0005-*.patch \
                     "$ASK_PATCH_DIR"/0006-*.patch \
                     "$ASK_PATCH_DIR"/0007-*.patch \
                     "$ASK_PATCH_DIR"/0008-*.patch \
                     "$ASK_PATCH_DIR"/0009-*.patch \
                     "$ASK_PATCH_DIR"/0010-*.patch \
                     "$ASK_PATCH_DIR"/0011-*.patch \
                     "$ASK_PATCH_DIR"/0012-*.patch \
                     "$ASK_PATCH_DIR"/0013-*.patch \
                     "$ASK_PATCH_DIR"/0014-*.patch \
                     "$ASK_PATCH_DIR"/0015-*.patch \
                     "$ASK_PATCH_DIR"/0016-*.patch \
                     "$ASK_PATCH_DIR"/0017-*.patch \
                     "$ASK_PATCH_DIR"/0018-*.patch \
                     "$ASK_PATCH_DIR"/0019-*.patch \
                     "$ASK_PATCH_DIR"/0020-*.patch \
                     "$ASK_PATCH_DIR"/0021-*.patch \
                     "$ASK_PATCH_DIR"/0022-*.patch \
                     "$ASK_PATCH_DIR"/0023-*.patch \
                     "$ASK_PATCH_DIR"/0024-*.patch \
                     "$ASK_PATCH_DIR"/0025-*.patch \
                     "$ASK_PATCH_DIR"/0026-*.patch \
                     "$ASK_PATCH_DIR"/0027-*.patch \
                     "$ASK_PATCH_DIR"/0028-*.patch \
                     "$ASK_PATCH_DIR"/0029-*.patch \
                     "$ASK_PATCH_DIR"/0030-*.patch \
                     "$ASK_PATCH_DIR"/0031-*.patch \
                     "$ASK_PATCH_DIR"/0035-*.patch \
                     "$ASK_PATCH_DIR"/0039-*.patch \
                     "$ASK_PATCH_DIR"/0040-*.patch \
                     "$ASK_PATCH_DIR"/0041-*.patch \
                     "$ASK_PATCH_DIR"/0044-*.patch \
                     "$ASK_PATCH_DIR"/0047-*.patch \
                     "$ASK_PATCH_DIR"/0048-*.patch \
                     "$ASK_PATCH_DIR"/0049-*.patch \
                     "$ASK_PATCH_DIR"/0050-*.patch \
                     "$ASK_PATCH_DIR"/0052-*.patch \
                     "$ASK_PATCH_DIR"/0053-*.patch \
                     "$ASK_PATCH_DIR"/0054-*.patch \
                     "$ASK_PATCH_DIR"/0055-*.patch \
                     "$ASK_PATCH_DIR"/0056-*.patch \
                     "$ASK_PATCH_DIR"/0057-*.patch; do
        [ -f "$src_patch" ] || { echo "ERROR: missing $src_patch"; exit 1; }
        # Rename 0001-→1001-, 0002-→1002-, 0003-→1003-, 0004-→1004-,
        # 0005-→1005-, 0006-→1006-, 0007-→1007-, 0008-→1008-,
        # 0009-→1009- to avoid collision with vyos-build's reserved
        # upstream 0001-*/0003-* patches.
        base=$(basename "$src_patch")
        case "$base" in
            0001-*) dst="1001-${base#0001-}" ;;
            0002-*) dst="1002-${base#0002-}" ;;
            0003-*) dst="1003-${base#0003-}" ;;
            0004-*) dst="1004-${base#0004-}" ;;
            0005-*) dst="1005-${base#0005-}" ;;
            0006-*) dst="1006-${base#0006-}" ;;
            0007-*) dst="1007-${base#0007-}" ;;
            0008-*) dst="1008-${base#0008-}" ;;
            0009-*) dst="1009-${base#0009-}" ;;
            0010-*) dst="1010-${base#0010-}" ;;
            0011-*) dst="1011-${base#0011-}" ;;
            0012-*) dst="1012-${base#0012-}" ;;
            0013-*) dst="1013-${base#0013-}" ;;
            0014-*) dst="1014-${base#0014-}" ;;
            0015-*) dst="1015-${base#0015-}" ;;
            0016-*) dst="1016-${base#0016-}" ;;
            0017-*) dst="1017-${base#0017-}" ;;
            0018-*) dst="1018-${base#0018-}" ;;
            0019-*) dst="1019-${base#0019-}" ;;
            0020-*) dst="1020-${base#0020-}" ;;
            0021-*) dst="1021-${base#0021-}" ;;
            0022-*) dst="1022-${base#0022-}" ;;
            0023-*) dst="1023-${base#0023-}" ;;
            0024-*) dst="1024-${base#0024-}" ;;
            0025-*) dst="1025-${base#0025-}" ;;
            0026-*) dst="1026-${base#0026-}" ;;
            0027-*) dst="1027-${base#0027-}" ;;
            0028-*) dst="1028-${base#0028-}" ;;
            0029-*) dst="1029-${base#0029-}" ;;
            0030-*) dst="1030-${base#0030-}" ;;
            0031-*) dst="1031-${base#0031-}" ;;
            0035-*) dst="1035-${base#0035-}" ;;
            0039-*) dst="1039-${base#0039-}" ;;
            0040-*) dst="1040-${base#0040-}" ;;
            0041-*) dst="1041-${base#0041-}" ;;
            0044-*) dst="1044-${base#0044-}" ;;
            0047-*) dst="1047-${base#0047-}" ;;
            0048-*) dst="1048-${base#0048-}" ;;
            0049-*) dst="1049-${base#0049-}" ;;
            0050-*) dst="1050-${base#0050-}" ;;
            0052-*) dst="1052-${base#0052-}" ;;
            0053-*) dst="1053-${base#0053-}" ;;
            0054-*) dst="1054-${base#0054-}" ;;
            0055-*) dst="1055-${base#0055-}" ;;
            0056-*) dst="1056-${base#0056-}" ;;
            0057-*) dst="1057-${base#0057-}" ;;
            *)      echo "ERROR: unexpected ASK patch name: $base"; exit 1 ;;
        esac
        echo "###   $base → $dst"
        cp "$src_patch" "$KERNEL_PATCHES/$dst"
        ASK_PATCH_COUNT=$((ASK_PATCH_COUNT + 1))
    done
    # Expected count: 45 (Phase 2 Option C2 result — see
    # plans/ASK2-PHASE2-PATCH-TRIAGE.md §5 for arithmetic):
    #   53 original
    # -  8 archived 2026-05-24 stage 1 (0032/0033-RMV/0034/0036/0037-RMV/
    #                                   0038/0042/0043)
    # -  3 archived 2026-05-24 stage 2 (Option C2: 0045 wholesale due to
    #                                   keygen.c cross-file deps; 0046
    #                                   PARTIAL-split; 0051 orphaned)
    # +  3 new KEEP-half patches (0054 ex-0033, 0055 ex-0037, 0056 ex-0046)
    # = 45 active.
    if [ "$ASK_PATCH_COUNT" -ne 46 ]; then
        echo "ERROR: expected 46 ASK kernel patches, staged $ASK_PATCH_COUNT"
        exit 1
    fi
    echo "### ASK2: $ASK_PATCH_COUNT in-tree kernel patches staged"
fi

# Stage FMD Shim + LP5812 source from the new common files layout.
# Source of truth: kernel/common/files/{fsl_fmd_shim.c,lp5812/}.
FILES_DIR=kernel/common/files
[ -f "$FILES_DIR/fsl_fmd_shim.c" ] || { echo "ERROR: $FILES_DIR/fsl_fmd_shim.c missing"; exit 1; }
[ -d "$FILES_DIR/lp5812" ]         || { echo "ERROR: $FILES_DIR/lp5812 missing"; exit 1; }
cp "$FILES_DIR/fsl_fmd_shim.c" "$KERNEL_BUILD/"
cp -r "$FILES_DIR/lp5812"      "$KERNEL_BUILD/"

# Write injection block to temp file (heredoc avoids all quoting issues).
# Note: the former phylink / dpaa-xdp / xhci-ls1046a Python patchers have
# been retired — their effects are now carried by the 4005/4006/4007 unified
# diff patches staged above and applied by build-kernel.sh's patch loop.
cat > /tmp/kernel-inject.sh << 'INJECT_EOF'

# FMD Shim: inject /dev/fm0* chardev module for DPDK fmlib RSS
if [ -f "${CWD}/fsl_fmd_shim.c" ]; then
  FMD_DIR=drivers/soc/fsl/fmd_shim
  mkdir -p "$FMD_DIR"
  cp "${CWD}/fsl_fmd_shim.c" "$FMD_DIR/"
  cat > "$FMD_DIR/Kconfig" <<-KEOF
	config FSL_FMD_SHIM
		bool "FMD Shim chardev for DPDK fmlib FMan RSS"
		depends on FSL_FMAN
		default y
		help
		  Minimal character device driver that creates /dev/fm0,
		  /dev/fm0-pcd, and /dev/fm0-port-rxN devices for the
		  DPDK DPAA PMD fmlib library to program FMan KeyGen RSS.
		  Safe to enable -- completely passive until ioctls called.
	KEOF
  echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fsl_fmd_shim.o' > "$FMD_DIR/Makefile"
  # Hook into parent Kconfig and Makefile
  if ! grep -q fmd_shim drivers/soc/fsl/Kconfig 2>/dev/null; then
    echo 'source "drivers/soc/fsl/fmd_shim/Kconfig"' >> drivers/soc/fsl/Kconfig
  fi
  if ! grep -q fmd_shim drivers/soc/fsl/Makefile 2>/dev/null; then
    echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fmd_shim/' >> drivers/soc/fsl/Makefile
  fi
  echo "FMD Shim: injected into $FMD_DIR"
fi

# LP5812: inject TI LP5812 I2C LED controller driver (out-of-tree, not in mainline 6.6)
if [ -d "${CWD}/lp5812" ]; then
  LP5812_DIR=drivers/leds/lp5812
  mkdir -p "$LP5812_DIR"
  cp "${CWD}/lp5812/leds-lp5812.c" "$LP5812_DIR/"
  cp "${CWD}/lp5812/leds-lp5812.h" "$LP5812_DIR/"
  cat > "$LP5812_DIR/Kconfig" <<-KEOF
	config LEDS_LP5812
		bool "LED Support for TI LP5812 I2C LED controller"
		depends on LEDS_CLASS && I2C && LEDS_CLASS_MULTICOLOR
		default y
		help
		  TI LP5812 12-channel I2C LED controller with per-LED
		  analog and PWM dimming. Used on Mono Gateway DK for
		  4 status indicator LEDs (white/blue/green/red).
	KEOF
  echo 'obj-$(CONFIG_LEDS_LP5812) += leds-lp5812.o' > "$LP5812_DIR/Makefile"
  # Hook into parent Kconfig and Makefile
  if ! grep -q lp5812 drivers/leds/Kconfig 2>/dev/null; then
    echo 'source "drivers/leds/lp5812/Kconfig"' >> drivers/leds/Kconfig
  fi
  if ! grep -q lp5812 drivers/leds/Makefile 2>/dev/null; then
    echo 'obj-$(CONFIG_LEDS_LP5812) += lp5812/' >> drivers/leds/Makefile
  fi
  # Force-enable now that Kconfig is wired up.
  # The post-defconfig olddefconfig ran BEFORE LP5812 was injected,
  # so CONFIG_LEDS_LP5812=y was silently dropped. Re-apply and resolve.
  scripts/config --set-val CONFIG_LEDS_LP5812 y
  make olddefconfig
  echo "LP5812: injected into $LP5812_DIR (config forced)"
fi
INJECT_EOF

# Insert injection block before "# Change name of Signing Cert" in build-kernel.sh
# Verify the anchor exists before attempting injection
grep -q '# Change name of Signing Cert' "$KERNEL_BUILD/build-kernel.sh" \
  || { echo "ERROR: build-kernel.sh anchor '# Change name of Signing Cert' missing"; exit 1; }
sed -i '/# Change name of Signing Cert/r /tmp/kernel-inject.sh' "$KERNEL_BUILD/build-kernel.sh"
rm -f /tmp/kernel-inject.sh

### Post-defconfig: force LS1046A built-in configs after VyOS snippets
#
# VyOS config/*.config snippets are merged onto our LS1046A defconfig
# additions via `scripts/kconfig/merge_config.sh` (T8506, upstream
# vyos-build 2026-05). For symbols also set by VyOS snippets, the
# VyOS value wins (later in the merge order) — e.g. USB_STORAGE=m
# (VyOS) overrides our USB_STORAGE=y. This block injects scripts/config
# --set-val overrides AFTER merge_config.sh has produced .config to force
# the LS1046A-required values back in.
#
# History: pre-T8506 upstream ran `make vyos_defconfig` after `cat`-ing all
# snippets onto the defconfig, and our anchor was the `make vyos_defconfig`
# line. Upstream replaced that step with merge_config.sh on 2026-05; the
# old anchor no longer exists. The injection-anchor verification below
# ensures any future upstream refactor fails loudly instead of silently
# no-opping (which is exactly what would have shipped a kernel without
# our forced builtins).
#
cat > /tmp/ls1046a-post-defconfig.sh << 'LS1046A_POSTDEFCONFIG_EOF'

# LS1046A: Force built-in configs that VyOS snippets may have overridden
echo "I: LS1046A — Forcing built-in kernel configs after vyos_defconfig"
scripts/config --enable CONFIG_DEVTMPFS_MOUNT
scripts/config --set-val CONFIG_USB_STORAGE y
scripts/config --set-val CONFIG_VFAT_FS y
scripts/config --set-val CONFIG_FAT_FS y
scripts/config --set-val CONFIG_NLS_CODEPAGE_437 y
scripts/config --set-val CONFIG_NLS_ISO8859_1 y
scripts/config --set-val CONFIG_NLS_UTF8 y
scripts/config --set-val CONFIG_SQUASHFS y
scripts/config --set-val CONFIG_OVERLAY_FS y
scripts/config --set-val CONFIG_QORIQ_CPUFREQ y
scripts/config --set-val CONFIG_FSL_EDMA y
scripts/config --set-val CONFIG_SERIAL_OF_PLATFORM y
scripts/config --set-val CONFIG_MAXLINEAR_GPHY y
scripts/config --set-val CONFIG_IMX2_WDT y
scripts/config --set-val CONFIG_SPI_FSL_QUADSPI y
scripts/config --disable CONFIG_DEBUG_PREEMPT
scripts/config --set-val CONFIG_NEW_LEDS y
scripts/config --set-val CONFIG_LEDS_CLASS y
scripts/config --set-val CONFIG_LEDS_CLASS_MULTICOLOR y
scripts/config --set-val CONFIG_LEDS_GPIO y
scripts/config --set-val CONFIG_LEDS_LP5812 y
scripts/config --set-val CONFIG_LEDS_TRIGGERS y
scripts/config --set-val CONFIG_LEDS_TRIGGER_NETDEV y
# KVM, NFS, VFIO, CMA, thermal (match dev kernel)
scripts/config --set-val CONFIG_KVM y
scripts/config --set-val CONFIG_NFS_FS y
scripts/config --set-val CONFIG_NFS_V4 y
scripts/config --set-val CONFIG_NFS_V4_1 y
scripts/config --set-val CONFIG_SUNRPC y
scripts/config --set-val CONFIG_VFIO y
scripts/config --set-val CONFIG_CMA y
scripts/config --set-val CONFIG_DMA_CMA y
scripts/config --set-val CONFIG_CMA_SIZE_MBYTES 32
scripts/config --enable CONFIG_THERMAL_GOV_POWER_ALLOCATOR
scripts/config --disable CONFIG_THERMAL_GOV_FAIR_SHARE
scripts/config --disable CONFIG_THERMAL_GOV_BANG_BANG
scripts/config --disable CONFIG_CPU_IDLE_GOV_LADDER
scripts/config --disable CONFIG_STRICT_DEVMEM
scripts/config --disable CONFIG_IO_STRICT_DEVMEM
make olddefconfig

LS1046A_POSTDEFCONFIG_EOF

# Anchor: the line that runs `scripts/kconfig/merge_config.sh "${KCONFIG_MERGE_FRAGMENTS[@]}"`
# in the post-T8506 build-kernel.sh. Inject our forcing block IMMEDIATELY
# AFTER that line so .config exists and our `scripts/config --set-val ...`
# block can modify it, followed by `make olddefconfig` to resolve any
# auto-dependencies.
#
# Implementation note: this used to be a sed `\|addr|r file` invocation
# but BRE-sed treats `\{...\}` as an interval expression (which requires
# digits inside), so any pattern containing the literal `${...}` bash
# expansion would fail with "Invalid content of \{\}". Switched to a
# Python rewrite using the existing python3 dependency — same approach
# as the kernel-patch-loop rewrite below. The anchor is matched as a
# fixed string against full lines, so there is no regex hazard.
ANCHOR_LINE='scripts/kconfig/merge_config.sh "${KCONFIG_MERGE_FRAGMENTS[@]}"'
if ! grep -qxF "$ANCHOR_LINE" "$KERNEL_BUILD/build-kernel.sh"; then
    echo "ERROR: post-defconfig anchor missing in $KERNEL_BUILD/build-kernel.sh" >&2
    echo "       expected exact line: $ANCHOR_LINE" >&2
    echo "       upstream vyos-build's build-kernel.sh layout has changed —" >&2
    echo "       update the anchor in bin/ci-setup-kernel.sh to inject the" >&2
    echo "       LS1046A scripts/config --set-val block AFTER the new config-merge step." >&2
    exit 1
fi
python3 - "$KERNEL_BUILD/build-kernel.sh" "$ANCHOR_LINE" /tmp/ls1046a-post-defconfig.sh <<'PYEOF'
import sys, pathlib
bk = pathlib.Path(sys.argv[1])
anchor = sys.argv[2]
inject = pathlib.Path(sys.argv[3]).read_text()
lines = bk.read_text().splitlines(keepends=True)
out = []
done = False
for ln in lines:
    out.append(ln)
    if not done and ln.rstrip("\n") == anchor:
        # Ensure injected block starts on its own line and ends with newline
        if not inject.startswith("\n"):
            out.append("\n")
        out.append(inject if inject.endswith("\n") else inject + "\n")
        done = True
if not done:
    print(f"ERROR: anchor not matched line-for-line in {bk}", file=sys.stderr)
    sys.exit(1)
bk.write_text("".join(out))
print(f"### {bk}: post-defconfig block injected after merge_config.sh line")
PYEOF
rm -f /tmp/ls1046a-post-defconfig.sh

### Replace upstream `patch -p1` loop with `git apply --3way`.
#
# Upstream vyos-build build-kernel.sh applies kernel patches with:
#     for patch in $(ls ${PATCH_DIR}); do
#         patch -p1 < ${PATCH_DIR}/${patch}
#     done
# This loop:
#   - uses GNU patch (not git apply), so no blob-SHA-anchored 3-way merge,
#   - does NOT check the exit code, so a failed hunk leaves a .rej file
#     and the build continues with a partially-patched kernel,
#   - sorts via `ls` (locale-dependent) instead of `find ... | sort`.
# This silent-failure mode shipped a kernel without the OEM/SFP-10G-T
# quirk on ISO 2026.05.10-2322 (see commit c35005e changelog).
#
# We rewrite the loop to:
#   - turn the kernel tree into a throwaway git repo so `git apply --3way`
#     has blob-of-record as the 3-way merge base,
#   - iterate patches via `find … | sort` (deterministic),
#   - apply each with `git apply --3way --whitespace=nowarn`,
#   - ABORT the build on first failure (no silent .rej drops),
#   - commit the post-patch tree so any subsequent injection (e.g. the
#     LP5812 force-config block) sees the patched state.
#
# Idempotent via SENTINEL marker — re-running ci-setup-kernel.sh is a
# no-op.
echo "### Rewriting build-kernel.sh patch loop: GNU patch -p1 -> git apply --3way"
python3 - "$KERNEL_BUILD/build-kernel.sh" <<'PYEOF'
import sys, re, pathlib

bk = pathlib.Path(sys.argv[1])
src = bk.read_text()
SENTINEL = "# === ls1046a-build: git apply --3way kernel patch loop ==="

if SENTINEL in src:
    print(f"### {bk}: patch loop already replaced — no-op")
    sys.exit(0)

# Match the upstream loop EXACTLY. Indentation is 4 spaces.
PATTERN = re.compile(
    r"for patch in \$\(ls \$\{PATCH_DIR\}\)\n"
    r"do\n"
    r'    echo "I: Apply Kernel patch: \$\{PATCH_DIR\}/\$\{patch\}"\n'
    r"    patch -p1 < \$\{PATCH_DIR\}/\$\{patch\}\n"
    r"done\n",
)

REPLACEMENT = SENTINEL + """
# Initialise the kernel source tree as a throwaway git repo so that
# `git apply --3way` can fall back to a real 3-way merge using the
# pre-patch blobs in object storage when context drifts.
if [ ! -d .git ]; then
    git -c init.defaultBranch=main init -q
    git -c user.email=ci@local -c user.name=ci add -A
    git -c user.email=ci@local -c user.name=ci commit -q -m "kernel pristine (pre-patches)" --allow-empty || true
fi

PATCH_FAIL=0
PATCH_FAIL_LIST=""
for patch in $(find "${PATCH_DIR}" -maxdepth 1 -type f -name '*.patch' | sort); do
    pname=$(basename "$patch")
    echo "I: Apply Kernel patch: $patch"
    if ! git apply --3way --whitespace=nowarn "$patch"; then
        echo "::error::Kernel patch FAILED to apply (git apply --3way): $pname" >&2
        PATCH_FAIL=$((PATCH_FAIL + 1))
        PATCH_FAIL_LIST="$PATCH_FAIL_LIST $pname"
    fi
done

if [ "$PATCH_FAIL" -ne 0 ]; then
    echo "::error::$PATCH_FAIL kernel patch(es) failed to apply:$PATCH_FAIL_LIST" >&2
    echo "::error::Aborting build. The legacy patch -p1 loop would have continued silently with a partially-patched kernel." >&2
    exit 1
fi

# Snapshot the patched tree so subsequent injections (LP5812 olddefconfig,
# FMD shim, etc.) see the patched state as their merge base.
git -c user.email=ci@local -c user.name=ci add -A
git -c user.email=ci@local -c user.name=ci commit -q -m "kernel post-patches" --allow-empty || true
# === end ls1046a-build patch-loop replacement ===
"""

new, n = PATTERN.subn(REPLACEMENT, src, count=1)
if n == 0:
    print(
        f"ERROR: upstream `for patch in $(ls ${{PATCH_DIR}})` loop not found in {bk}.\n"
        "       The upstream vyos-build build-kernel.sh layout has changed —\n"
        "       update the regex in bin/ci-setup-kernel.sh accordingly.",
        file=sys.stderr,
    )
    sys.exit(1)

bk.write_text(new)
print(f"### {bk}: patch loop replaced with git apply --3way (1 substitution)")
PYEOF

### PR14z2 fix #4 (v2): persistent signing key + post-build snapshot from headers .deb
#
# Background: linux 6.18.31's `make bindeb-pkg` chain runs `make clean`
# AFTER producing the binary .debs, wiping Module.symvers, certs/signing_key.*,
# .config, scripts/sign-file, scripts/mod/modpost, include/{config,generated},
# arch/arm64/include/generated. Three earlier attempts failed:
#   (1) DPKG_FLAGS=--no-post-clean — redundant (default in dpkg 1.19+), no effect
#   (2) builddeb `set -eu` hook — anchor found and patched but the hook never
#       fires because builddeb's CWD when it runs is debian/linux-image-X.Y.Z/
#       staging dir, NOT the kernel source root, so the `[ -f Module.symvers ]`
#       test fails silently
#   (3) Pre-build snapshot — bindeb-pkg's internal `make clean` then rebuild
#       generates a NEW ephemeral signing key, leaving any pre-snapshotted
#       key paired with the wrong kernel
#
# v2 approach (this block):
#   PRE-bindeb-pkg (run while .config still exists in-tree):
#     - Pre-generate persistent RSA signing key at ${CWD}/ask-persistent-keys/
#     - Override CONFIG_MODULE_SIG_KEY to point at it
#     - Run `make olddefconfig` to resolve the change
#     - This makes the kernel embed the persistent key's cert in the
#       in-vmlinux trusted keyring, so a module signed later with the same
#       persistent key passes MODULE_SIG_FORCE verification at insmod time
#
#   POST-bindeb-pkg (after linux-image / linux-headers .debs land):
#     - Extract linux-headers-*-vyos_*_arm64.deb into ${CWD}/ask-kernel-snapshot/
#       (the headers .deb is purpose-built for OOT module compilation —
#       it ships Module.symvers, scripts/sign-file, scripts/mod/modpost,
#       include/{config,generated}, arch/<arch>/include/generated, and the
#       complete kbuild Makefile machinery)
#     - Copy the persistent key+cert into the extracted tree's certs/ dir
#     - Symlink ${CWD}/ask-kernel-snapshot/ksrc -> extracted/usr/src/linux-headers-…
#       so kernel/flavors/ask/oot-modules/ask/ci-build.sh can use it as KSRC
#     - Touch ${CWD}/ask-kernel-snapshot/.done as the "snapshot ready" flag
#
# kernel/flavors/ask/oot-modules/ask/ci-build.sh checks for the snapshot
# when its $KSRC/Module.symvers is missing and switches KSRC to the
# snapshot's extracted headers tree.
#
# Idempotency: the marker `# === ASK2 v2 persistent-key + headers-snapshot ===`
# short-circuits re-injection on re-runs of ci-setup-kernel.sh.
echo "### Injecting ASK2 v2 persistent-key + headers-snapshot blocks into build-kernel.sh"
python3 - "$KERNEL_BUILD/build-kernel.sh" <<'PYEOF'
import pathlib, sys
bk = pathlib.Path(sys.argv[1])
src = bk.read_text()

MARKER = "# === ASK2 v2 persistent-key + headers-snapshot ==="
if MARKER in src:
    print(f"### {bk}: ASK2 v2 blocks already injected — no-op")
    sys.exit(0)

# The merge_config.sh + olddefconfig sequence is duplicated 4 times in the
# current build-kernel.sh (one real + three accidental duplicates from prior
# ci-setup-kernel.sh re-runs without idempotency). We anchor against the
# FIRST `make olddefconfig` line that follows the LS1046A scripts/config
# block — that's the moment .config exists and the kernel hasn't been built
# yet. We inject the key-setup block AFTER that line.
KEY_BLOCK = '''
''' + MARKER + '''
# Pre-generate a persistent module signing key OUTSIDE the kernel tree so
# it survives the post-bindeb-pkg `make clean`. Override CONFIG_MODULE_SIG_KEY
# to point at it; vmlinux will embed this key's cert in the trusted keyring,
# enabling later signing of OOT ask.ko with the same key.
ASK_KEY_DIR="${CWD}/ask-persistent-keys"
mkdir -p "$ASK_KEY_DIR"
ASK_KEY_PEM="$ASK_KEY_DIR/signing_key.pem"
ASK_KEY_X509="$ASK_KEY_DIR/signing_key.x509"
if [ ! -f "$ASK_KEY_PEM" ]; then
    echo "I: ASK2 v2 — generating persistent module signing key at $ASK_KEY_PEM"
    openssl req -new -nodes -utf8 -sha512 -days 36500 -batch -x509 \\
        -config <(printf '%s\\n' '[req]' 'distinguished_name=req_dn' 'prompt=no' 'x509_extensions=req_ext' '[req_dn]' 'CN=ASK2 persistent module signing key' '[req_ext]' 'basicConstraints=critical,CA:FALSE' 'keyUsage=digitalSignature' 'subjectKeyIdentifier=hash' 'authorityKeyIdentifier=keyid') \\
        -keyout "$ASK_KEY_PEM" -out "$ASK_KEY_PEM"
fi
if [ ! -f "$ASK_KEY_X509" ] || [ "$ASK_KEY_PEM" -nt "$ASK_KEY_X509" ]; then
    openssl x509 -in "$ASK_KEY_PEM" -outform DER -out "$ASK_KEY_X509"
fi
echo "I: ASK2 v2 — overriding CONFIG_MODULE_SIG_KEY=$ASK_KEY_PEM"
scripts/config --set-str CONFIG_MODULE_SIG_KEY "$ASK_KEY_PEM"
# Also disable trusted-keys file injection; vyos-build's GIT_ROOT/data/certificates
# scan adds external keys via CONFIG_SYSTEM_TRUSTED_KEYS, but on ASK2 we want
# the OOT signing path to depend ONLY on our persistent key. (Empty value =
# only the MODULE_SIG_KEY cert + system keyring built-ins are trusted.)
make olddefconfig
# === end ASK2 v2 persistent-key block ===

'''

# Find the FIRST `make olddefconfig` line that follows the LS1046A force-config
# block ("scripts/config --disable CONFIG_IO_STRICT_DEVMEM" + "make olddefconfig").
ANCHOR_FIRST = "scripts/config --disable CONFIG_IO_STRICT_DEVMEM\nmake olddefconfig\n"
idx = src.find(ANCHOR_FIRST)
if idx < 0:
    print(f"ERROR: ASK2 v2 anchor not found in {bk} (expected post-LS1046A olddefconfig)", file=sys.stderr)
    sys.exit(1)
insert_at = idx + len(ANCHOR_FIRST)
src = src[:insert_at] + KEY_BLOCK + src[insert_at:]

# Inject the snapshot block AFTER the `make bindeb-pkg ...` line.
BINDEB_ANCHOR = "make bindeb-pkg BUILD_TOOLS=1 LOCALVERSION=${KERNEL_SUFFIX} KDEB_PKGVERSION=${KERNEL_VERSION}-1"
bidx = src.find(BINDEB_ANCHOR)
if bidx < 0:
    print(f"ERROR: ASK2 v2 bindeb-pkg anchor not found in {bk}", file=sys.stderr)
    sys.exit(1)
# Find end-of-line after the bindeb-pkg invocation.
eol = src.find("\n", bidx)
if eol < 0:
    print(f"ERROR: ASK2 v2 bindeb-pkg line has no newline in {bk}", file=sys.stderr)
    sys.exit(1)

SNAPSHOT_BLOCK = '''

# === ASK2 v2 post-bindeb-pkg headers snapshot ===
# bindeb-pkg has just produced linux-image-*.deb + linux-headers-*.deb and
# (in 6.18.x) wiped the in-tree build state. Extract linux-headers .deb to
# ${CWD}/ask-kernel-snapshot/extracted/ — that's a complete OOT-module-build
# tree (Module.symvers, scripts/sign-file, generated headers, kbuild
# Makefiles). Copy the persistent signing key into the extracted certs/ dir
# so OOT builds can sign ask.ko with the SAME key embedded in vmlinux's
# trusted keyring.
ASK_SNAP_DIR="${CWD}/ask-kernel-snapshot"
ASK_HEADERS_DEB=$(ls "${CWD}"/linux-headers-*-vyos_*_arm64.deb 2>/dev/null | head -1)
if [ -n "$ASK_HEADERS_DEB" ] && [ -f "$ASK_KEY_PEM" ]; then
    echo "I: ASK2 v2 — extracting $ASK_HEADERS_DEB into $ASK_SNAP_DIR/extracted/"
    rm -rf "$ASK_SNAP_DIR"
    mkdir -p "$ASK_SNAP_DIR/extracted"
    dpkg-deb -x "$ASK_HEADERS_DEB" "$ASK_SNAP_DIR/extracted"
    ASK_KSRC=$(find "$ASK_SNAP_DIR/extracted/usr/src" -maxdepth 1 -type d -name 'linux-headers-*' 2>/dev/null | head -1)
    if [ -n "$ASK_KSRC" ]; then
        ln -sfn "$ASK_KSRC" "$ASK_SNAP_DIR/ksrc"
        mkdir -p "$ASK_KSRC/certs"
        cp "$ASK_KEY_PEM"  "$ASK_KSRC/certs/signing_key.pem"
        cp "$ASK_KEY_X509" "$ASK_KSRC/certs/signing_key.x509"
        # PR14z12-D (2026-05-19): the headers .deb that bindeb-pkg
        # produces does NOT include private FSL headers like
        # include/linux/fsl/fman_pcd.h, fman_host_cmd.h, or
        # dpaa_flow_offload.h — they are added by our ASK patch stack
        # 0003 / 0004 / 0027 / 0028 etc and are required by the OOT
        # ask.ko (ask_hw.c includes <linux/fsl/fman_pcd.h>). Without
        # this rsync the OOT build fails with
        # "fatal error: linux/fsl/fman_pcd.h: No such file or directory".
        # Copy them — and any other ASK-injected include/linux/fsl/*.h —
        # from the original kernel source tree into the snapshot before
        # signalling .done.
        if [ -d "${CWD}/${KERNEL_DIR}/include/linux/fsl" ]; then
            mkdir -p "$ASK_KSRC/include/linux/fsl"
            cp -av "${CWD}/${KERNEL_DIR}/include/linux/fsl/." \
                   "$ASK_KSRC/include/linux/fsl/" 2>&1 | tail -5 || true
            echo "I: ASK2 v2 — copied include/linux/fsl/ headers into snapshot"
        fi
        touch "$ASK_SNAP_DIR/.done"
        echo "I: ASK2 v2 — snapshot ready: $ASK_SNAP_DIR/ksrc -> $ASK_KSRC"
        ls -la "$ASK_KSRC/Module.symvers" "$ASK_KSRC/scripts/sign-file" "$ASK_KSRC/certs/signing_key.pem" 2>&1 || true
    else
        echo "WARNING: ASK2 v2 — extracted .deb but no usr/src/linux-headers-* dir found"
    fi
else
    echo "WARNING: ASK2 v2 — snapshot skipped: ASK_HEADERS_DEB='$ASK_HEADERS_DEB' ASK_KEY_PEM='$ASK_KEY_PEM'"
fi
# === end ASK2 v2 post-bindeb-pkg headers snapshot ===
'''

src = src[:eol+1] + SNAPSHOT_BLOCK + src[eol+1:]

bk.write_text(src)
print(f"### {bk}: ASK2 v2 persistent-key + headers-snapshot blocks injected")
PYEOF

echo "### Kernel setup complete"
