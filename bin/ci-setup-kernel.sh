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
cp "$BOARD_PATCH_DIR/0068-dpaa-flavor-ops.patch"              "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0069-dpaa-flavor-hooks.patch"            "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0069a-dpaa-flavor-ops-retro-attach.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0070-dpaa1-xsk-wakeup.patch"             "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0071-dpaa1-xsk-pool-setup.patch"         "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0072-dpaa1-xsk-zc-datapath-scaffold.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0073-dpaa-af-xdp-pool-skeleton.patch"    "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0074-dpaa-af-xdp-pool-wakeup.patch"      "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0075a-dpaa-af-xdp-pool-liodn-and-attach-validation.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0075b-dpaa-af-xdp-pool-attach-bman-seed-rcu.patch"        "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0075c-dpaa-af-xdp-pool-remove-liodn-gate.patch"           "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0076-dpaa-af-xdp-pool-detach.patch"      "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0077-dpaa-xsk-max-qbands-default.patch"  "$KERNEL_PATCHES/"
# 0078 (dpaa MODULE_SOFTDEP on af_xdp_pool) intentionally NOT staged:
# under CONFIG_FSL_DPAA_ETH=y and CONFIG_DPAA_AF_XDP_POOL=y the softdep
# is unreachable (modprobe never loads either of them). Autoload is
# guaranteed by the =y flip in kernel/common/kernel-config/08-dpaa1.config
# instead — af_xdp_pool_init() runs at late_initcall before
# dpaa_eth_probe()'s register_netdev().
cp "$BOARD_PATCH_DIR/0079-dpaa-ethtool-expose-xsk-counters.patch" "$KERNEL_PATCHES/"
# M3-3 step 1: bind a real NAPI to qmap[].napi at xsk_pool_attach time
# (BSP cpu 0's per-CPU NAPI portal) and stop xsk_set_rx_need_wakeup being
# a stub. First reviewable slice of Phase 3 per spec sec 5.2 final paragraph
# + sec 5.4 RX path step 5. No throughput change yet — control-plane
# wiring; ZC RX/TX datapath lands in 0081+.
cp "$BOARD_PATCH_DIR/0080-dpaa-af-xdp-pool-bind-napi-and-arm-rx-need-wakeup.patch" "$KERNEL_PATCHES/"
# M3-3 step 2a: distribute qband NAPI across online CPUs.  Promotes
# the cpu=0 stopgap from 0080 to (queue_id % num_online_cpus()) so
# four-qband bindings fan out across all four LS1046A A72 cores
# instead of piling onto cpu 0's QMan SWP.  Still no dedicated BMan
# channels (step 2b) and no cluster-aware refinement (step 2c).
# Spec sec 5.2 "Queue mapping correctness" items 3-5.
cp "$BOARD_PATCH_DIR/0081-dpaa-af-xdp-pool-distribute-napi-across-cpus.patch" "$KERNEL_PATCHES/"
# M3-3 step 2b: observability for step 2a's pointer wiring. Adds the
# /sys/kernel/debug/af_xdp_pool/qmap node so priv->qmap[].napi/.cpu can
# be verified per-netdev without kgdb or a crash dump. Pure observability —
# zero datapath change, zero new core-driver exports. Spec sec 5.2.
cp "$BOARD_PATCH_DIR/0082-dpaa-af-xdp-pool-qmap-debugfs.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0082b-dpaa-dedicated-qman-channels-per-qband.patch" "$KERNEL_PATCHES/"
# M3-3 step 3: real dpaa_fq_to_qband() + xsk_rx_branch counter +
# observational RX hot-path eligibility probe. Strictly diagnostic --
# no datapath change. ZC redirect lands in 0084+. Spec sec 6.1.2.
cp "$BOARD_PATCH_DIR/0083-dpaa-rx-xsk-branch-eligibility-probe.patch" "$KERNEL_PATCHES/"
# M3-3 step 4: NAPI-hooked BMan refill from the XSK fill ring + new
# xsk_bman_refill_batches counter. Folded into the existing rcu_read_lock()
# block in dpaa_eth_poll() right after xsk_set_rx_need_wakeup. With no XSK
# pool bound (default flavor) the new ops->napi_refill callback walks zero
# bound qbands and returns; no datapath cost. Spec sec 6.1.3.
cp "$BOARD_PATCH_DIR/0084-dpaa-napi-hooked-bman-refill.patch" "$KERNEL_PATCHES/"
# M3-3 step 5: TX ZC submission + xsk_tx_inflight backpressure + TxConf
# round-trip closure. Three new flavor ops (napi_tx_zc, xsk_set_tx_need_wakeup,
# tx_conf_zc) wired into dpaa_eth_poll() tail (same RCU section as 0084) and
# dpaa_tx_conf() head. Two new ethtool counters (xsk_tx_zc_submit,
# xsk_tx_conf_zc). With no XSK pool bound (default flavor) all three ops
# walk zero bound qbands and the tx_conf_zc claim probe returns false on
# bpid mismatch -- skb fast path unchanged. ≥ 7 Gbps acceptance gate on
# vpp flavor. Spec sec 6.1.4.
cp "$BOARD_PATCH_DIR/0085-dpaa-tx-zc-and-inflight-backpressure.patch" "$KERNEL_PATCHES/"
# M3-3b: FMan PCD capability detection + CC-steering stub API. Adds
# CONFIG_DPAA_HW_CC_STEERING (default y), priv->fman_caps snapshot via
# dpaa_fman_get_caps() at probe, one-shot KERN_INFO log, hw_offload_unavailable
# ethtool counter, and the four fman_cc_tree_*() stubs returning -ENOTSUPP.
# Observability-only -- mainline ucode 106 silicon shows caps=0x00 and every
# productive call short-circuits. dpaa_fman_caps.force= module parameter
# lets developers simulate ucode 210 for unit testing downstream consumers
# (af_xdp_pool qband-select, ASK2 flowtable bridge, vyos-1x classify CLI).
# Spec sec 3.5 + sec 5.4.
cp "$BOARD_PATCH_DIR/0086-dpaa-fman-caps-detection-and-cc-stub.patch" "$KERNEL_PATCHES/"
# M3-3 step 6 blocker A residual: DMA device mismatch between the XSK
# pool map (was: parent MAC device, 32-bit mask) and the BMan FBPR
# validation domain (FMan RX port device, 40-bit mask). Switches
# xsk_pool_dma_map() to priv->rx_dma_dev, the same device mainline uses
# for dpaa_bp_add_8_bufs(). The two earlier blocker-A hot-fixes
# (0086 chunked release-by-8, 0087 pre-zero bmbs[i].data) were absorbed
# into 0084 v3 directly -- the patch stack is now stand-alone. Spec
# sec 6.1.5 / 6.1.6.
cp "$BOARD_PATCH_DIR/0088-dpaa-afxdp-use-rx-dma-dev-for-xsk-pool-dma-map.patch" "$KERNEL_PATCHES/"
# M3-3b productive: replace the dpaa_fman_caps.force= stub body of
# dpaa_fman_get_caps() with a real DT walk of the FMan firmware blob
# (/proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware,
# struct qe_firmware id field at bytes 8..69). Parses the "Microcode
# version <maj>.<min>.<rev> ..." string and lights up
# FMAN_CAP_CC_EXACT_MATCH | FMAN_CAP_HM_NODES | FMAN_CAP_POLICER_TRTCM
# | FMAN_CAP_PARSER_SOFTSEQ when major >= 210 (verified on Mono Gateway
# DK 2026-05-28: u-boot loads 210.10.1 from SPI mtd4). HC_DISPATCH stays
# off per PR13 finding -- the stock 210.10.1 QEF blob does not implement
# the HC doorbell. force= still wins as operator override. Caps are
# cached after first DT probe so subsequent dpaa_eth_probe() calls (5x
# on this board) don't re-walk. Spec sec 3.5.
cp "$BOARD_PATCH_DIR/0086a-dpaa-fman-caps-probe-dt.patch"      "$KERNEL_PATCHES/"
# M3-3c: HM (Header Manipulation) stub API. Mirrors the 0086 cadence
# exactly -- fman_hm_node_install/destroy stubs return -ENOTSUPP,
# fman_hm_caps_supported() wraps (caps & FMAN_CAP_HM_NODES). Adds
# CONFIG_DPAA_HW_HM_OFFLOAD (default y, depends on DPAA_HW_CC_STEERING)
# and struct fman_hm_spec opaque type. Productive impl lands in a
# follow-up patch; API is fixed now so downstream consumers (af_xdp_pool
# egress rewrite, vyos-1x NAT offload CLI, ASK2 flowtable bridge) can
# wire calls today and gracefully degrade on ucode <210 silicon. Spec
# sec 5.5.
cp "$BOARD_PATCH_DIR/0090-dpaa-fman-hm-stub.patch"              "$KERNEL_PATCHES/"
# M3-3d: Policer (srTCM/trTCM) stub API. Mirrors the 0090 cadence exactly --
# fman_policer_install returns -ENOTSUPP, fman_policer_destroy is an
# idempotent void no-op, fman_policer_caps_supported() wraps
# (caps & FMAN_CAP_POLICER_TRTCM). Adds CONFIG_DPAA_HW_POLICER_OFFLOAD
# (default y, depends on DPAA_HW_CC_STEERING) and opaque struct
# fman_policer_profile. Productive impl lands in a follow-up patch; API is
# fixed now so downstream consumers (vyos-1x firewall limit offload CLI,
# VPP per-qband rate-limit, ASK2 nft limit offload backend) can wire calls
# today and gracefully degrade on ucode <210 silicon. Spec sec 5.6.
cp "$BOARD_PATCH_DIR/0091-dpaa-fman-policer-stub.patch"         "$KERNEL_PATCHES/"
# M3-3b productive struct contract: replaces the opaque {u32 reserved;}
# placeholders for struct fman_cc_key / fman_cc_static_tree (from 0086)
# with the real 5-tuple key + static-tree layout per spec sec 5.4. The
# four fman_cc_tree_* entry points stay -ENOTSUPP stubs; only the API
# struct shape becomes productive so downstream consumers (af_xdp_pool
# qband-select, vyos-1x classify CLI, ASK2 flowtable bridge) can build
# real specs. The silicon AD/group-table CONT_LOOKUP encoding lands in a
# follow-up. Applies on the final post-0091 dpaa_fman_caps.h. Spec sec 5.4.
cp "$BOARD_PATCH_DIR/0086b-dpaa-fman-cc-productive-structs.patch" "$KERNEL_PATCHES/"
# M3-3c productive struct contract: replaces the opaque struct
# fman_hm_spec {u32 reserved;} placeholder (from 0090) with the real
# ordered-op-list layout (enum fman_hm_op_type + VLAN/MPLS op params +
# ops[8]) per spec sec 5.5. fman_hm_* entry points stay -ENOTSUPP stubs.
# Must apply AFTER 0086b (both edit dpaa_fman_caps.h). Spec sec 5.5.
cp "$BOARD_PATCH_DIR/0090a-dpaa-fman-hm-productive-structs.patch" "$KERNEL_PATCHES/"
# M3-3d productive struct contract: replaces the opaque struct
# fman_policer_profile {u32 reserved;} placeholder (from 0091) with the
# real srTCM/trTCM metering layout (enum fman_policer_mode +
# enum fman_policer_color_mode + cir/cbs/pir/pbs) per spec sec 5.6.
# fman_policer_* entry points stay -ENOTSUPP stubs; only the API struct
# shape becomes productive so consumers (vyos-1x firewall limit offload,
# VPP per-qband rate-limit, ASK2 nft limit offload) can build real
# profiles. The FMan exp/mant rate-field + MURAM record encoding (RM
# 8.7.6) lands in a follow-up. Must apply AFTER 0090a (both edit
# dpaa_fman_caps.h). Spec sec 5.6.
cp "$BOARD_PATCH_DIR/0091a-dpaa-fman-policer-productive-structs.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0093-dpaa1-true-zc-rx-eligibility-probe.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0094-dpaa1-true-zc-rx-arm-observability.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0095-dpaa1-xsk-fill-ring-guard-audit.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0096-dpaa1-true-zc-rx-recover-readside.patch" "$KERNEL_PATCHES/"
# FMan PCD (Parse/Classify/Distribute) orchestration subsystem — COMMON
# (all flavors). Forward-port of the ask20 0004 skeleton re-anchored to
# 6.18.31: new files fman_pcd.c / fman_pcd_internal.h /
# include/linux/fsl/fman_pcd.h, the fman_get_muram/pcd/dev/id accessors,
# struct fman.pcd member, and fman_pcd_init/release wired into fman_probe
# via devm_add_action_or_reset. FSL_FMAN_PCD defaults y so it is built-in
# for default/vpp/ask alike. Purely additive (new TUs + additive fman.c/.h
# hunks) — independent of the 0086/0090/0091 dpaa_fman_caps.h stub chain,
# applies last among board patches by sort order. Unblocks M3-3b/c/d:
# the per-engine CC/HM/Policer bodies (follow-up patches) reach the FMan
# MURAM/registers through this subsystem instead of -ENOTSUPP. The
# ASK2-only fman_host_cmd.c microcode-doorbell transport is intentionally
# NOT forward-ported. Spec sec 5.4/5.5/5.6.
cp "$BOARD_PATCH_DIR/0092-fman-pcd-subsystem.patch"             "$KERNEL_PATCHES/"
# 0097 (PR2): FMan PCD KeyGen exact-match scheme API. Builds on 0092 —
# promotes struct keygen_scheme / struct fman_keygen to a new module-internal
# fman_keygen_internal.h and exports the two existing keygen_scheme_setup /
# keygen_bind_port_to_schemes helpers, then adds fman_pcd_kg.c + the public
# fman_pcd_kg_* KG surface (scheme_create/bind_port/attach_cc/scheme_destroy).
# IPv4 5-tuple match-vector via KGSE_MV (RM 8.7.4); attach_cc stays -EOPNOTSUPP
# until the CC tree subsystem lands. Common (built-in via FSL_FMAN_PCD) for
# default/vpp/ask alike. Numbered 0097 (not 0093) to avoid colliding with the
# pre-existing 0093-dpaa1-true-zc-rx-eligibility-probe.patch; 0097 sorts after
# 0092 (PCD skeleton) AND after the unrelated 0093-0096 true-ZC patches (which
# do not touch Makefile/fman_pcd.h/fman_keygen.c), so the KeyGen delta still
# applies on top of the 0092 PCD skeleton. Spec sec 5.4/5.5/5.6.
cp "$BOARD_PATCH_DIR/0097-fman-pcd-keygen.patch"                "$KERNEL_PATCHES/"
# 0098 (PR3): FMan CC static-tree install (productive, M3-3b). Builds on
# 0092 (PCD subsystem) + 0097 (KeyGen) — adds the new fman_pcd_cc.c
# silicon-programming TU (struct fman_pcd_cc_tree + fman_pcd_cc_static_install/
# _destroy, MURAM match-key + AD tables + CONT_LOOKUP group-table[0] per
# LS1046A RM 8.7.4.1), publishes the neutral struct fman_pcd_cc_hw_{key,spec}
# in the public include/linux/fsl/fman_pcd.h, and makes the dpaa-side
# fman_cc_tree_install()/destroy() productive (gate on FMAN_CAP_CC_EXACT_MATCH,
# host->BE translate, delegate via fman_get_pcd()). add_key/remove_key stay
# -ENOTSUPP (HC-dispatch gated; board caps=0x17, HC bit clear). Common
# (built-in via FSL_FMAN_PCD) for default/vpp/ask alike. Sorts after 0097 so
# the Makefile/fman_pcd.h deltas apply on top of the KeyGen base. Spec sec 5.4.
cp "$BOARD_PATCH_DIR/0098-fman-pcd-cc-static-install.patch"     "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0099-fman-pcd-hm-install.patch"            "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0100-fman-pcd-plcr-install.patch"          "$KERNEL_PATCHES/"
# 0101 (M3-3c bridge): wire NETIF_F_HW_VLAN_CTAG_RX -> fman_hm_node_install via
# a new dpaa_set_features() .ndo_set_features handler in dpaa_eth.c, so the
# dormant HM install body (0099) is reachable from userspace (ethtool -K /
# the vyos-1x 'set interfaces ethernet ethX hw-offload vlan-strip' CLI).
# Depends on 0099 (fman_hm_node_install productive) + 0090a (struct fman_hm_spec)
# + 0086a (fman_hm_caps_supported), so it MUST sort after 0100. Common
# (built-in) for default/vpp/ask. Spec sec 5.5.
cp "$BOARD_PATCH_DIR/0101-dpaa-hw-vlan-strip-ndo-set-features-bridge.patch" "$KERNEL_PATCHES/"
# 0102: dormant exported fman_port_set_rx_bpool() reprogram primitive
# (M3-3 step 7 sub-increment 4, WRITE mechanism, no caller). Edits
# fman_port.c/.h only; independent of the 0092-0100 PCD stack. Spec sec 6.1.7.
cp "$BOARD_PATCH_DIR/0102-fman-port-set-rx-bpool-primitive.patch" "$KERNEL_PATCHES/"
# 0102b: one-shot dev_info FMBM_EBMPI register readback at reprogram time
# (GAP-1 evidence that the 0102 BPID re-commit reached silicon). Diagnostic
# only; stacks on 0102. Spec sec 6.1.17 / plans/ZC-RX-SCOPE.md GAP 1.
cp "$BOARD_PATCH_DIR/0102b-fman-port-debug-readback.patch" "$KERNEL_PATCHES/"
# 0103a: dormant true-ZC RX Recover sw-ring reverse-map (M3-3 step 7
# sub-increment 4a, infrastructure only, NO datapath consumer). Adds the
# per-qband chunk-DMA -> xdp_buff reverse map + record/lookup helpers that
# 0103b needs (kernel 6.18.31 has no xsk_buff_recv() retrieve-by-dma
# primitive). Self-tested at attach; byte-identical datapath to 0102.
# Spec sec 6.1.15 (corrected) / 6.1.16 (API gap).
cp "$BOARD_PATCH_DIR/0103a-dpaa1-true-zc-rx-recover-swring.patch" "$KERNEL_PATCHES/"
# 0103b: PRODUCTIVE true-ZC RX -- the INSEPARABLE reprogram-WRITE +
# Recover-redirect pair (M3-3 step 7 sub-increment 4b). Fires the FMan
# RX-port BPID swap (fman_port_set_rx_bpool, 0102) at attach AND wires the
# rx_hook (rx_default_dqrr dispatch) that Recovers the xdp_buff from the bare
# chunk DMA cookie via the 0103a reverse map and xdp_do_redirect()s it into
# the XSKMAP (xsk_zc_rx_redirect, 22nd xsk_* counter). Both halves MUST land
# together (firing either alone -> sec 6.1.8 crash class). Byte-identical on
# default/vpp (only reached on XDP_ZEROCOPY bind). Spec sec 6.1.16.
cp "$BOARD_PATCH_DIR/0103b-dpaa1-true-zc-rx-reprogram-redirect.patch" "$KERNEL_PATCHES/"
# 0103c: true-ZC RX stage-3 -- sub-increment-4 reorder + IPI wakeup +
# unconditional NAPI refill + pre-arm RX NEED_WAKEUP + BPID restore on
# detach. Makes the productive xsk_zc_rx_redirect oracle (0103b) actually
# reachable under load. Edits af_xdp_pool_main.c (+ dpaa_eth) on top of
# 0103b; sorts after 0103b, before 0104. Spec sec 6.1.17.
cp "$BOARD_PATCH_DIR/0103c-dpaa1-true-zc-rx-classify-before-bpid-guard.patch" "$KERNEL_PATCHES/"
# 0103e: bpf_net_ctx NULL-deref fix in af_xdp_pool_rx_hook (the rx_hook
# runs outside the NAPI bpf_net_ctx the redirect path assumes). Stacks on
# 0103c. Spec sec 6.1.17.
cp "$BOARD_PATCH_DIR/0103e-dpaa1-true-zc-rx-bpf-net-ctx-fix.patch" "$KERNEL_PATCHES/"
# 0103f: dispatch the qmgmt_ops->rx_hook BEFORE the dpaa_bpid2pool() NULL
# guard in rx_default_dqrr. Without this, FDs carrying the XSK bpid resolve
# to no kernel pool and are consumed/dropped at ~2855 before the 0103b hook
# at ~2901 ever sees them -> xsk_zc_rx_redirect stuck at 0. Stacks on 0103e.
cp "$BOARD_PATCH_DIR/0103f-dpaa1-true-zc-rx-rxhook-before-bpidpool.patch" "$KERNEL_PATCHES/"
# 0103g: register per-band MEM_TYPE_XSK_BUFF_POOL xdp_rxq_info at ZC attach
# + xsk_pool_set_rxq_info; fixes the NULL xdp->rxq Oops in __xsk_map_redirect
# on the first Recovered frame (HW serial capture 2026-06-09). Stacks on 0103f.
cp "$BOARD_PATCH_DIR/0103g-dpaa1-true-zc-rx-register-zc-rxq.patch" "$KERNEL_PATCHES/"
# 0104: PRODUCTIVE M3-3d policer consumer -- .ndo_setup_tc TC_SETUP_BLOCK
# handler mapping a single ingress `tc filter matchall action police` onto
# fman_policer_install() slot 0 (board 0100). Fail-soft -EOPNOTSUPP when
# !fman_policer_caps_supported(). Edits dpaa_eth.c/.h only; sorts after
# 0103e, before 101-sfp. This is the kernel backend for the vyos-1x-025
# `set interfaces ethernet ethX ingress-policer` CLI. Spec sec 5.6.
cp "$BOARD_PATCH_DIR/0104-dpaa-ingress-policer-tc-matchall-bridge.patch" "$KERNEL_PATCHES/"
# 0104a: advertise NETIF_F_HW_TC in dpaa_netdev_init() so tc_can_offload() is
# true and the tc core actually routes an ingress `matchall action police`
# filter to 0104's TC_SETUP_BLOCK handler. Without it the netdev shows
# `hw-tc-offload: off [fixed]`, skip_sw filters are rejected and non-skip_sw
# filters install software-only (not_in_hw) -- the handler never runs. Gated
# on fman_policer_caps_supported() (decl from 0091), mirrors the HM /
# NETIF_F_HW_VLAN_CTAG_RX block 0101 adds just above. Touches only
# dpaa_netdev_init() (no overlap with 0104's hunks); sorts after 0104, before
# 101-sfp. Spec sec 5.6.
cp "$BOARD_PATCH_DIR/0104a-dpaa-netdev-advertise-hw-tc.patch" "$KERNEL_PATCHES/"
# 0104b: M3-3e CEETM scaffold -- pins the QMan egress-shaper stub API
# (dpaa_ceetm_qdisc_install / dpaa_ceetm_qdisc_destroy / dpaa_ceetm_supported)
# + CONFIG_DPAA_HW_CEETM in dpaa_fman_caps.{c,h} + Kconfig. supported() returns
# false and install() returns -ENOTSUPP until the productive QMan CEETM core
# forward-port lands; fixes the VyOS CLI contract now. Touches only the tails
# of caps.{c,h}/Kconfig (no overlap with 0104/0104a); sorts after 0104a, before
# 101-sfp. Spec sec 5.7.
cp "$BOARD_PATCH_DIR/0104b-dpaa-ceetm-stub.patch" "$KERNEL_PATCHES/"
# 0105: dormant exported fman_port_set_cc_base() RX coarse-classification
# base primitive (M3-3b keystone, WRITE mechanism, no caller). Programs the
# BMI fmbm_rccb register -- the RAW MURAM offset of the 0098 CC tree root
# (NO >>4) -- which mainline NEVER writes, the single missing port->CC link
# that left M2/M3 static CC steering non-productive. The Parser->KeyGen half
# is already wired by fman_port_use_kg_hash(). Edits fman_port.c/.h only;
# independent of the 0092-0104b PCD stack (cross-module EXPORT consumed by
# the future productive caller). Sorts after 0104b, before 101-sfp. Spec
# sec 13.
cp "$BOARD_PATCH_DIR/0105-fman-port-set-cc-base-primitive.patch" "$KERNEL_PATCHES/"
# 0106: M3-3b productive CC steering wiring -- the HW-proven KGSE_CCBS graft
# (silicon captures 2026-05-23/25: NIA stays BMI direct-enqueue 0x80500002,
# a non-zero KGSE_CCBS = CC root group-table MURAM offset dispatches the CC
# walk implicitly; the NIA-flip-to-FM_CTL alternative was DISPROVEN on HW).
# Makes fman_pcd_kg_attach_cc() productive, adds the port-level graft pair
# fman_pcd_kg_port_attach_cc()/detach_cc() (mirror of the BUG 3 policer
# steering fix), and completes fman_cc_tree_install()/destroy() in
# dpaa_fman_caps.c (install -> get_base -> graft; destroy detaches first).
# Sorts after 0105, before 101-sfp. Spec sec 5.4 (M3-3b).
cp "$BOARD_PATCH_DIR/0106-fman-pcd-cc-keygen-graft-wiring.patch" "$KERNEL_PATCHES/"
# 0107: debugfs CC steering test harness -- /sys/kernel/debug/fman_pcd/<N>/
# cc_test drives the EXACT 0106 productive sequence (static_install ->
# get_base -> kg_port_attach_cc; clear = detach_cc -> static_destroy) so the
# M3-3b acceptance gate can be exercised on the DUT before a real consumer
# (vyos-1x classify CLI) lands. New TU fman_pcd_cc_test.c in
# fsl_dpaa_fman.ko + intra-module fman_pcd_cc_seq_dump() helper; 0600
# root-only node, zero datapath cost, no new EXPORT_SYMBOLs. Sorts after
# 0106, before 101-sfp. Spec sec 5.4 (M3-3b DUT validation).
cp "$BOARD_PATCH_DIR/0107-fman-pcd-cc-test-debugfs-harness.patch" "$KERNEL_PATCHES/"
# 0108: M3-3b close-out -- per-key FQ enqueue-AD + silicon-truth CC key
# layout. Replaces 0098's soft leaf-AD encoding (qband<<16|hm<<8|type,
# graceful fall-through) with the ask20-HW-PROVEN RM 8.7.4.3 hardware
# enqueue-AD (fqid@0x0, RESULT_CF[|NADEN]@0x8, HMTD@0xc; PR14z20/z22: 24M+
# frames silicon-forwarded) whenever a key carries a non-zero target_fqid,
# and fixes cc_pack_key() to the KG-emitted composite the CC walker
# actually compares under the 0106 KGSE_CCBS graft
# ([SIP|DIP|SPI=0|SPORT|DPORT], PR14z14 silicon truth). Adds
# target_fqid/miss_fqid plumbing through fman_cc_key/fman_cc_static_tree
# and extends the 0107 cc_test harness with an optional [fqid-hex] arg.
# fqid 0 keeps the DUT-validated fall-through byte-identical. Sorts after
# 0107, before 101-sfp. Spec sec 5.4 (M3-3b).
cp "$BOARD_PATCH_DIR/0108-fman-pcd-cc-per-key-fq-enqueue-ad.patch" "$KERNEL_PATCHES/"
# 0109: M3-3b production consumer -- ethtool ntuple (rxnfc) -> FMan CC
# static-tree bridge in dpaa_ethtool.c. ETHTOOL_SRXCLSRLINS/DEL rules
# rebuild the port's CC tree via fman_cc_tree_destroy()+install() (the
# 0106 graft sequence); action <queue> = Nth RX PCD FQ, resolved FQID
# carried in target_fqid so the 0108 hardware enqueue-AD steers on HIT.
# Driven by `ethtool -N`, whose config-mode consumer is vyos-1x-026
# ('set system offload classify'). Mirrors the 0104 policer pattern
# (userspace -> standard kernel tool -> driver bridge). Sorts after
# 0108, before 101-sfp. Spec sec 5.4 (M3-3b production consumer).
cp "$BOARD_PATCH_DIR/0109-dpaa-ethtool-ntuple-cc-steering-bridge.patch" "$KERNEL_PATCHES/"
# 0110: true-ZC RX NAPI-only hook dispatch + xdp_do_flush (supersedes the
# never-shipped 0103h). Fixes TWO coupled defects in the 0103e/0103f hook
# path: (1) missing xdp_do_flush() after XSKMAP redirect -- the local
# bpf_net_context was torn down without flushing so xskq_prod_submit()
# never ran (redirect>0 but probe rx_packets=0); (2) FATAL hard-IRQ panic
# in __xsk_map_flush -- 0103f dispatched the rx_hook BEFORE mainline's
# dpaa_eth_napi_schedule() deferral, so the hook + flush ran in portal_isr
# hard-IRQ context, corrupting the per-context xsk flush list across CPUs
# (dual-CPU Oops, HW 2026-06-10). Fix: defer to NAPI first when a hook is
# registered (qman_cb_dqrr_stop on hard IRQ; QMan re-delivers in NAPI),
# plus WARN_ON_ONCE(in_hardirq()) bail at hook entry. HW-validated
# 2026-06-10: functional PASS, SIGKILL-teardown stress PASS, 8-way flood
# survival PASS. Diff base is post-0109 (dpaa_eth.c overlaps 0104/0109),
# hence the 0110 number. Sorts after 0109, before 101-sfp. Spec sec 6.1.18.
cp "$BOARD_PATCH_DIR/0110-dpaa1-true-zc-rx-napi-only-flush.patch" "$KERNEL_PATCHES/"
# 0111: QMan CEETM hierarchical egress shaper core (M3-3e). Ports the NXP
# SDK CEETM API (qman_high.c 3283-5772 + qman_config.c CCSR) to mainline
# style: new drivers/soc/fsl/qbman/qman_ceetm.c (~1100 LOC, Kconfig
# FSL_QMAN_CEETM) with SP/LNI/channel/CQ/CCG/LFQ claim-release, CR/ER
# token-bucket shaper config (erratum A-010383 mps=60 honoured), CCG
# tail-drop, and qman_ceetm_create/destroy_fq in qman.c (ERN delivery via
# a reserved in-range dynamic FQID slot in fq_table -- CEETM LFQIDs
# 0xF00000+ would overflow it). CCSR side reads qman_clk from the DT
# clock-frequency property (U-Boot fixup provides 300 MHz on LS1046A) for
# prescaler math. v1 scope: strict-prio CQ0-7 only (no WBFS), no CSCN,
# DCP0/rev-3.2 only. Wire structs are explicit __beN -- BUILD_BUG_ON
# layout-asserted (cmd 63B / rsp 64B). Consumer lands in 0112. Sorts
# after 0110, before 101-sfp. Spec sec 5.7 (M3-3e).
cp "$BOARD_PATCH_DIR/0111-qman-ceetm.patch" "$KERNEL_PATCHES/"
# 0112: dpaa HTB-offload consumer of the 0111 CEETM core (M3-3e). New
# dpaa_ceetm.{c,h} (Kconfig DPAA_HW_CEETM, rewritten from the 0104b
# scaffold entry; stubs removed from dpaa_fman_caps.{c,h}). Modern
# TC_SETUP_QDISC_HTB offload (stock iproute2 `tc qdisc add ... htb
# offload`), NOT the legacy SDK ceetm qdisc: each HTB leaf class maps to
# its own CEETM channel (CR=rate, ER=ceil -- the 0111 rate API is
# channel-level) with one prio-0 CQ + 1MiB byte-mode CCG tail-drop +
# LFQ/FQ; one extra unshaped default channel carries ALL non-leaf
# traffic because sp_set_lni() stops conventional WQ dequeue on the
# port (skb + XDP TX both divert via dpaa_ceetm_egress_fq() inside
# dpaa_xmit; inactive cost = one predicted-not-taken load). Flat
# root->leaf only; LEAF_TO_INNER -> -EOPNOTSUPP. txqs: alloc grows to
# dpaa_max_num_txqs()+32, real_num grown per LEAF_ALLOC_QUEUE, restored
# on DESTROY. NB tc_htb_qopt_offload rate/ceil are BYTES/s (x8 applied).
# Sorts after 0111, before 101-sfp. Spec sec 5.7 (M3-3e consumer).
cp "$BOARD_PATCH_DIR/0112-dpaa-ceetm-htb.patch" "$KERNEL_PATCHES/"
# DCSR error observability: read-only debugfs taps for the FMan common-block
# error/status registers (fpm/bmi/qmi/parser/kg/pol). fpm_err decodes the 50
# per-hwport status words incl. STALL — the M3-3b forensic view. Spec §5.8.
cp "$BOARD_PATCH_DIR/0113-fman-pcd-dcsr-error-taps.patch" "$KERNEL_PATCHES/"
# True-ZC RX gate-counter realign: moves xsk_zc_eligible/xsk_zc_rx_recovered
# into af_xdp_pool_rx_hook() (the 0110 NAPI-only flush rework left the old
# probe site unreachable). Makes xsk-zc-check's verdict meaningful again.
cp "$BOARD_PATCH_DIR/0114-dpaa1-xsk-zc-eligible-realign.patch" "$KERNEL_PATCHES/"
# M3-3b wedge fix: SDK-convergent CC bring-up (root CONT_LOOKUP AD, RESULT
# leaf ADs, productive FMBM_RCCB bind + NIA_KG_CC_EN via fman_port_lookup_rx
# registry, KG NIA=FM_CTL|AC_CC with CCBS=grpBits). Spec §5.4, v5.19.
cp "$BOARD_PATCH_DIR/0115-fman-pcd-cc-sdk-convergent-bringup.patch" "$KERNEL_PATCHES/"
# M3-3b wedge fix iteration 3: CC result-AD NIA must exit via FM_CTL
# AC_NO_IPACC_PRE_BMI_ENQ_FRAME (0x28) on A006675/SW006 silicon — the 0115
# direct NIA_ENG_BMI|ENQ exit leaked one FMan task per CC-dispatched frame
# (MAC RDRP ate everything, no FPM stall, reboot-only). Also brings up the
# per-port FM_CTL ctrl-params page (FMBM_RGPR) the 0x28 ucode consumes.
cp "$BOARD_PATCH_DIR/0116-fman-pcd-cc-fmctl-enq-params-page.patch" "$KERNEL_PATCHES/"
# M3-3b ROOT-CAUSE fix (iter-25): mainline fman_init() clear_iram()s the
# U-Boot-uploaded FM_CTL microcode and never reloads it — IRAM all-0xFF,
# IREADY=0, so every CC dispatch (KG→FM_CTL|AC_CC) parks its FMan task and
# leaks BMI FIFO units (freeze @~46 frames). 0117 re-uploads the DTB QEF
# blob (proprietary 210.10.1, fman-firmware/fsl,firmware) into IRAM right
# after clear_iram, per SDK LoadFmanCtrlCode (fm.c:426-480). Spec §5.4.
cp "$BOARD_PATCH_DIR/0117-fman-load-ctrl-microcode.patch" "$KERNEL_PATCHES/"
# M3-3b iter-48 fix: revert 0115's KeyGen→CC dispatch encoding back to the
# HW-proven CCBS model (KGSE_MODE NIA = BMI direct-enqueue 0x80500002 +
# KGSE_CCBS = CC root group-table MURAM offset). 0115's AC_CC NIA-flip
# (0x80000006, ccbs=0) was DISPROVEN on hardware: with 0115's RCCB bind +
# 0116's SDK result-AD + 0117's 210.10.1 ucode all present it still stalls
# the FMan port on the first CC frame, whereas live-rewriting the scheme to
# CCBS cured the stall (no STL/60s, ping 5/5). Keeps the rest of 0115/0116/
# 0117 — only the 3 KeyGen/CC-scheme files revert. Spec §5.4.
cp "$BOARD_PATCH_DIR/0118-fman-pcd-cc-revert-ccbs-dispatch.patch" "$KERNEL_PATCHES/"
# ASK2 M2 step 1: extend the HM op-set (0090a/0099) with 3 additive
# L3-forward primitives — RMV_ETHERNET, INSRT_GENERIC, IPV4_FORWARD —
# across all four HM layers. SDK-grounded encodings (NXP fm_manip): single
# generic HMAN_OC=0x35 HMTD, RMV=0x01000e00 / INSRT=0x02000e00+BE payload /
# IPV4=0x0c040001 (TTL+L4 checksum). No existing VLAN/MPLS op altered.
cp "$BOARD_PATCH_DIR/0119-fman-pcd-hm-l3-forward-ops.patch" "$KERNEL_PATCHES/"
# ASK2 M2 step 2: dormant next-hop HM dedup refcount API
# (fman_hm_nexthop_get/put) caches+refcounts one shared HMTD per L3
# adjacency (egress_tx_fqid, src_mac, dst_mac) so MURAM scales
# O(next-hops) not O(flows). EXPORT_SYMBOL_GPL, dormant (ask.ko consumes).
cp "$BOARD_PATCH_DIR/0120-fman-pcd-hm-nexthop-dedup.patch" "$KERNEL_PATCHES/"
# ASK2 Gap-A: export two net_device -> hardware-id resolvers
# (dpaa_get_rx_fman_port / dpaa_get_tx_fqid) on the common dpaa_fman_caps.h
# substrate so the OOT ask.ko PCD consumer can derive the fman_cc_tree_*
# port key and a CC target_fqid. EXPORT_SYMBOL_GPL, dormant (no in-tree
# caller). Bodies are the proven dead-ask-flavor 0031/0039 reparented.
cp "$BOARD_PATCH_DIR/0121-dpaa-export-cc-target-resolvers.patch" "$KERNEL_PATCHES/"
# ASK2 Fork B M1 step 1: FE-object MURAM pool scaffold (arch/fman-fe-ehash.md
# §3 AllocFEObjs). Lazy + refcounted pool of 100×28 B FE records carved from
# FMan MURAM, driven by a new debugfs fman_pcd/<id>/fe_pool (0644) get/put
# node. fe_lock → pcd->lock order; a pristine S0 keeps the pool empty so
# engage→disengage nets zero gen_pool used (pcd-snapshot reversibility gate).
# Single-file fman_pcd.c, internal/static, no ABI export. Scaffold only —
# allocates+zeroes MURAM, does NOT program the FE records and does NOT flow
# traffic; the FE-VM core (FmPcdCcBuildFE/ContextByFE) lands later from lf-5.4.
cp "$BOARD_PATCH_DIR/0122-fman-pcd-fe-ehash-init.patch" "$KERNEL_PATCHES/"
# ASK2 Fork B M1 step 2: per-port FE support (arch/fman-fe-ehash.md §4
# FmPortSetFESupport/FmPortDeleteFESupport). Carves a per-port FE internal-
# buffer pool (total_tnums × 0x100 × 2, 256 B aligned) + a management free-list
# (5 + total_tnums bytes) from FMan MURAM, then writes the port's existing
# FM_CTL ctrl-params page +0x54 (mgmt index) / +0x58 (depletion count) — never
# allocating that page itself (it must pre-exist from a CC install, 0116) so the
# gate stays leak-clean. A faithful inverse (page→0, free mgmt, free pool, list
# del) makes engage→disengage net zero gen_pool used (pcd-snapshot gate). Adds
# fman_port_get_total_tnums() accessor (fman_port.c/.h). Driven by a new debugfs
# fman_pcd/<id>/fe_port (0644) "set <id>"/"del <id>" node. Allocate-only —
# ships DORMANT, does NOT flow classified traffic (needs §5 + FE-VM core).
# Sorts after 0122, before 101-sfp. Spec arch/fman-fe-ehash.md §4 (M1 Fork B).
cp "$BOARD_PATCH_DIR/0123-fman-pcd-fe-port-support.patch" "$KERNEL_PATCHES/"
# ASK2 Fork B M1 — FE virtual-machine core, increment 1 (arch/fman-fe-ehash.md
# §5 FE-VM). Transcribes the lf-5.4 SDK FmPcdCcBuildFE() descriptor encoder and
# the FM_PCD_Init() FE-singleton setup, adapted to mainline gen_pool MURAM (the
# SDK next-FE phys == the gen_pool offset fman_pcd_muram_alloc returns). Adds
# fman_pcd_fe_build() (big-endian MURAM image words via iowrite32be) plus the
# three core MUX/Transition/Exit singletons, programmed into pool slots from a
# new debugfs fman_pcd/<id>/fe_singletons (0644) "build"/"clear" node with a
# byte-level readback for oracle verification (§8.6 contract item 6). Ships
# DORMANT: programs FE descriptors but nothing dispatches into the FE machine
# until §5 ehash + the per-flow ENQ FE + AC_CC root-AD FE_ENTER wiring land.
# Forward (build) + inverse (clear) in this one patch; clear restores the exact
# pre-build pool state and pool_free drains the singletons, so pcd-snapshot
# gen_pool "used" returns to baseline (reversibility gate stays clean).
cp "$BOARD_PATCH_DIR/0124-fman-pcd-fe-vm-singletons.patch" "$KERNEL_PATCHES/"
# ASK2 Fork B M1 — §5 ExternalHashTableSet (arch/fman-fe-ehash.md §5/§6). The
# vendor enhanced-ehash flow store — the only config proven to FLOW on 210.10.1
# (§8). Lazily reserves a per-PCD internal-buffer-management MURAM pool (32 KiB
# pool + 256 B global, 256-aligned, refcounted — the dominant pcd-snapshot
# reversibility signal) and per-table DDR bucket arrays (kzalloc, 16 B/bucket;
# buckets MUST stay in DDR — §6 327×-ENOMEM wall) plus an en_exthash_node DDR
# template (lf-5.4 native LE packing). New debugfs fman_pcd/<id>/fe_ehash (0644)
# "set <mask_hex> <keysize> <shift>" / "clear" with node-word readback. Bounds-
# checks MURAM before reserving (§8.6 item 2). Ships DORMANT: allocates + encodes
# only; nothing dispatches into the hash store until the fm_cc.c FE_ENTER wrapper
# + FE-VM core land. Forward (set) + inverse (clear/drain) in one patch; clear
# returns gen_pool "used" to baseline (reversibility gate stays clean).
cp "$BOARD_PATCH_DIR/0125-fman-pcd-fe-ehash-table.patch" "$KERNEL_PATCHES/"
# 0126 — convert fman_pcd_muram_alloc/_free into a gen_pool sub-allocator over
# the reserved 64 KiB MURAM partition (0092 reserved the arena but the wrappers
# re-called the GLOBAL fman_muram_alloc, competing for the ~21 KiB post-CAM/FIFO
# free tail while the reservation sat dead-weight → §5/0125 int-buf 33 KiB hit
# -ENOMEM on HW 2026-06-16). Seeds a gen_pool (min_alloc_order=8, 256 B granule)
# with [muram_offset,+64KiB); all PCD MURAM now sub-allocates from it, bounding
# PCD use to the reservation and unblocking the FE/ehash forward path. Substrate
# change — full S0↔S1 + fe_pool + fe_ehash forward regression gate required.
cp "$BOARD_PATCH_DIR/0126-fman-pcd-muram-genpool.patch" "$KERNEL_PATCHES/"
# 0127 — FE-VM core increment 2 (arch/fman-fe-ehash.md §5): the per-flow ENQ
# Flow-Entry (FmPcdCcBuildContextByFE — ENQ-type FE carrying the 24-bit target
# FQID in word1) and the AC_CC root action-descriptor FE_ENTER wiring
# (FillAdOfTypeContLookup external-hash branch — CONT_LOOKUP AD: ccAdBase
# 0x40800000, pcAndOffsets 0xf6, gmask = MURAM offset of the FE to enter).
# Together they give a classified frame a terminal BMI-FIFO disposition. New
# debugfs fman_pcd/<id>/fe_enq ("build <fqid_hex> [next_fe_off_hex]" / "clear")
# and fe_enter ("build [fe_off_hex]" / "clear"), each with byte-level readback.
# Ships DORMANT (programs descriptors only; nothing dispatches into the FE VM
# until the ehash bucket indexer lands). Forward+inverse in one patch; each
# inverse re-zeros + frees its MURAM so pcd-snapshot stays reversible.
cp "$BOARD_PATCH_DIR/0127-fman-pcd-fe-vm-enq-root.patch" "$KERNEL_PATCHES/"
# 0128: FE-VM core increment 3 — per-flow ehash insertion (arch/fman-fe-ehash.md
# §5). The SDK get_indexed_hash_bucket() CRC64 bucket indexer +
# ExternalHashTableAddKey() head-insert: CRC64 the key → byte-shift+mask to a
# bucket → allocate a 256-byte DDR flow record (en_ehash_entry) → write the
# header (flags + next_entry chain to the old bucket head), the key, and the
# next-FE pointer (the 0127 ENQ FE MURAM offset) → head-insert
# (bucket->h = swab64(phys(record))). Links a classified 5-tuple to its ENQ FE.
# New debugfs fman_pcd/<id>/fe_flow ("add <tbl_idx> <key_hex> [enq_fe_off_hex]" /
# "clear") with byte-level readback. Buckets+records live in DDR by design (§6
# anti-pattern: never fall the flow store to MURAM) so gen_pool "used" is
# UNCHANGED — reversibility = all records freed + every bucket head restored.
# Ships DORMANT; forward (add) + inverse (LIFO drain, byte-exact) in one patch.
cp "$BOARD_PATCH_DIR/0128-fman-pcd-fe-vm-flow-insert.patch" "$KERNEL_PATCHES/"
# 0129: M1 coarse ask offload engage/disengage mode-switch (fman_pcd.h export).
# Adds two EXPORT_SYMBOL_GPL entry points to fman_pcd.c + their prototypes to
# <linux/fsl/fman_pcd.h>: fman_pcd_offload_engage()/_disengage(struct fman *,
# u8 hw_port_id). They resolve the PCD internally (fman_get_pcd()) and wrap the
# EXACT HW-proven reversible sequence from the cc_test harness (0107) + 100x
# soak: install a benign single-key CC tree → get_base → KGSE_CCBS graft of the
# port's KeyGen scheme, with strict reverse teardown (detach FIRST, then
# destroy). The out-of-tree ask.ko mirrors only these two prototypes (into
# ask_fman_caps.h) and drives them via /sys/kernel/debug/ask/offload. Ships
# DORMANT (nothing calls them until the debugfs trigger / M7 op-mode); M1
# carries no classification semantics. Forward + inverse in one patch.
cp "$BOARD_PATCH_DIR/0129-fman-pcd-offload-engage.patch" "$KERNEL_PATCHES/"
# 0130: D9.1 (M2 activate) increment 1 — switch the dormant FE/ehash flow store
# (0125 ehash table + 0128 per-flow records) from kzalloc()+virt_to_phys() to
# dma_alloc_coherent(). The en_exthash_node table-base words and each bucket head
# must carry true bus addresses (not raw physical) before the FE VM is armed, since
# the armed VM DMA-reads the bucket array and walks the record chain through
# PAMU/SMMU (arch/fman-fe-ehash.md §8.6 item 6; 0125/0128 flagged this as the
# pre-arming prerequisite). struct fman_pcd_ehash_table gains table_dma + dev
# (fman_get_dev(pcd->fman), captured so per-flow record alloc/free reaches the same
# device); struct fman_pcd_ehash_flow gains record_dma. Records+buckets stay in DDR
# (§6 anti-pattern: never MURAM) so gen_pool "used" is UNCHANGED — reversibility is
# still all records dma_free'd + every bucket head restored byte-exactly. Ships
# DORMANT (no new dispatch); the 0128 on-board record layout is byte-identical.
# Forward (dma_alloc) + inverse (dma_free) in one patch.
cp "$BOARD_PATCH_DIR/0130-fman-pcd-fe-ehash-dma-coherent.patch" "$KERNEL_PATCHES/"
# 0131: D9-A (M2 activate) increment 3 — the genuine 28-byte external-hash
# Flow-Entry object (SDK t_ExtHashFe) that the 0127 FE_ENTER root AD dispatches
# into. Binds the §5 DDR bucket array (0125/0130) to the FE VM and links HIT →
# MUX singleton / MISS → Exit singleton (0124). fman_pcd gains fe_hash_off;
# fman_pcd_fe_enter_build()'s default gmask now prefers the t_ExtHashFe once
# built (falls back to the MUX singleton, the 0127 default). New debugfs node
# fe_hashfe (build/clear) with a 7-word byte-level readback for the M0 oracle
# byte-diff (arch/fman-fe-ehash.md §8.6 item 6 — validate the dormant FE image
# while quiescent BEFORE arming, since the M3-3b stall latches ZERO fault).
# Ships DORMANT; forward+inverse in one patch; gen_pool "used" returns to the
# warm-S0' baseline on clear (pcd-snapshot reversibility gate stays clean).
cp "$BOARD_PATCH_DIR/0131-fman-pcd-fe-hash-object.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0132-fman-pcd-fe-arm-debugfs.patch"   "$KERNEL_PATCHES/"
# 0133: D9-B (M2 activate) — correct the fe_arm encoding from the 0132 KGSE_CCBS
# placebo (next_engine=2, mode 0x80500002, which NEVER dispatches the CC walk —
# frames bypass into RSS) to the REAL AC_CC encoding. Adds a next_engine==3 branch
# in keygen_scheme_setup that emits KGSE_MODE = FM_CTL|AC_CC (0x80000006) with
# KGSE_CCBS=0, re-adds the NIA_ENG_FM_CTL / NIA_FM_CTL_AC_CC defines 0118 dropped
# (used ONLY by the new branch; the ==2 CCBS graft, policer, M1-engage and RSS
# paths are byte-unchanged), and flips fman_pcd_kg_port_arm_fe() to next_engine=3 /
# cc_bits_sel=0. The FMBM_RCCB write (→ FE_ENTER root AD) is unchanged. disarm is
# unchanged (forces next_engine=0). Ships DORMANT: the encoding only takes effect
# on an explicit echo to the fman_pcd/<id>/fe_arm node. This is the make-or-break
# M2 dispatch experiment — the only encoding that genuinely enters the FE VM
# terminal disposition a bare exact-match leaf lacks (M3-3b iter-50 park).
# 0133: D9-B (M2 activate) — adds AC_CC keygen_scheme_setup branch (board 0133 v1); arm function now in 0132 v3
cp "$BOARD_PATCH_DIR/0133-fman-pcd-fe-arm-real-accc.patch" "$KERNEL_PATCHES/"
# 0134: CAAM/QI descriptor sharing for ASK2 IPsec HW offload (spec §8.1, PR10).
# Adds caam_qi_ext_consumer_register()/_release() to drivers/crypto/caam/qi.c +
# the ext_lock/ext_active fields in struct caam_drv_ctx (qi.h) + the new header
# include/linux/crypto/caam_qi_share.h, so a future in-kernel consumer (ask.ko's
# CAAM/xfrm datapath) can dequeue completed CAAM frames from a chosen sink FQID.
# Forward-ported VERBATIM from kernel/flavors/ask/patches/0001-caam-qi-share.patch,
# which was NEVER staged after the 2026-06-14 flavor collapse killed the dead
# FLAVOR=ask gate. Touches ONLY drivers/crypto/caam/* + a new header — zero
# overlap with the FMan PCD board patches, so apply order is irrelevant. Exports
# the symbols EXPORT_SYMBOL_GPL but they stay dormant (no caller until the CAAM
# datapath lands). This cp line is MANDATORY — the staging-completeness guard
# below fails the build if any board/*.patch lacks one.
cp "$BOARD_PATCH_DIR/0134-caam-qi-share.patch"               "$KERNEL_PATCHES/"
# 0135: FE-VM context builder — port of lf-5.4 LSDK FmPcdCcBuildContextByFE().
# Adds fman_pcd_fe_context_build() + struct fman_pcd_fe_context_params (the
# centralized per-FE context writer the SDK calls at 999-patch line 8954).
# Ships dormant (no callers yet — callers wire in a later patch to populate
# MUX/TRANSITION/ENQ/HM per-instance context after the FE descriptor build,
# matching the SDK two-step FmPcdCcBuildFE→FmPcdCcBuildContextByFE sequence).
cp "$BOARD_PATCH_DIR/0135-fman-pcd-fe-context-build.patch"   "$KERNEL_PATCHES/"
# 0136: TX confirm bypass — fman_port_set_silicon_hit_release_mode().
# Flips the TX port BMI to release silicon-HIT FDs (FCO=0) directly to BMan
# without QMan TX-confirm enqueue.  Kernel TX (FCO=1) is unaffected.
# This eliminates the ~20% CPU softirq floor proved on hardware 2026-05-25.
cp "$BOARD_PATCH_DIR/0136-fman-port-tx-confirm-bypass.patch" "$KERNEL_PATCHES/"
# 0137: MANIP creation + chain API for L3 forwarding (fman_pcd_manip_create/_destroy/_chain_create/_chain_destroy/_hmtd_off).
cp "$BOARD_PATCH_DIR/0137-fman-pcd-manip-create-chain.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0139-dpaa-af-xdp-bman-refill-bpid-fix.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/101-sfp-rollball-phylink-fallback.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4002-hwmon-ina2xx-add-ina234-support.patch" "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4005-phylink-inband-sfp-fallback.patch"  "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4006-dpaa-xdp-rxq-queue-index.patch"     "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4007-xhci-ls1046a-dwc3-quirks.patch"     "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/4009-sfp-oem-rollball-quirk.patch"       "$KERNEL_PATCHES/"
# ASK2 M2.2: external flow-offload backend registration slot (single-slot
# RCU-protected dpaa_register/unregister_flow_offload_handler). 0145 is a
# board/common patch (not flavor-gated) because the dpaa driver is built-in
# for all flavors.
cp "$BOARD_PATCH_DIR/0145-dpaa-flow-offload-backend-slot.patch" "$KERNEL_PATCHES/"
# 0146: Phase 2 integration — wire 0135 FE-VM context builder into fe_arm engage.
# Contexts (ENQ/MUX/Transition) are built immediately before the AC_CC arm
# reprograms KeyGen/BMI, so the FE-VM can resolve HIT→ENQ and MISS→Exit.
cp "$BOARD_PATCH_DIR/0146-fman-pcd-fe-context-build-integration.patch" "$KERNEL_PATCHES/"
# 0150: Phase 2 — FE-VM engage/flow API for ask.ko
#0150 (PLACEHOLDER — functions embedded into 0146)
#cp "$BOARD_PATCH_DIR/0150-fman-pcd-fe-engage-api.patch"      "$KERNEL_PATCHES/"
cp "$BOARD_PATCH_DIR/0148-keygen-debug-ekfc-log.patch" "$KERNEL_PATCHES/"


# ── Staging-completeness guard ────────────────────────────────────────
# Every kernel/common/patches/board/*.patch must either be cp'd above or
# listed here as an intentional skip. Failure mode (observed 2026-06-11,
# run 27362572444): 0113/0114/0115 were committed to board/ but their cp
# lines were forgotten, so CI silently shipped a kernel without them —
# the image looked healthy (same KVER) but lacked the new code entirely.
# Space-separated basenames of board patches deliberately not staged
# (currently none — 0078 was never committed as a file; see its comment above).
BOARD_STAGE_SKIP="0150-fman-pcd-fe-engage-api.patch"
_missing=""
for _p in "$BOARD_PATCH_DIR"/*.patch; do
  _b=$(basename "$_p")
  case " $BOARD_STAGE_SKIP " in *" $_b "*) continue ;; esac
  [ -f "$KERNEL_PATCHES/$_b" ] || _missing="$_missing $_b"
done
if [ -n "$_missing" ]; then
  echo "::error::board patches present in $BOARD_PATCH_DIR but NOT staged:$_missing"
  echo "::error::add a cp line in bin/ci-setup-kernel.sh (or list in BOARD_STAGE_SKIP)"
  exit 1
fi
echo "### Board patch staging-completeness guard: OK"

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
# Per plans/archive/ASK2-IMPLEMENTATION.md PR2/PR3 and spec §10, the ASK2
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
    # All ASK-specific kernel patches were archived to
    # archive-2026-06-21-pre-6.18.34/ on 2026-06-21 because the board/common
    # patch series (kernel/common/patches/board/0092–0145) now carries the
    # ASK2 PCD/HM/CC features directly. The ASK flavor relies solely on the
    # common board patch stack; no flavor-specific kernel patches are active.
    if ls "$ASK_PATCH_DIR"/*.patch >/dev/null 2>&1; then
        ASK_PATCH_COUNT=0
        for src_patch in "$ASK_PATCH_DIR"/*.patch; do
            [ -f "$src_patch" ] || continue
            base=$(basename "$src_patch")
            # Rename to 1xxx- to avoid collision with vyos-build's reserved
            # upstream 0001-*/0003-* patches.
            dst="1${base}"
            echo "###   $base → $dst"
            cp "$src_patch" "$KERNEL_PATCHES/$dst"
            ASK_PATCH_COUNT=$((ASK_PATCH_COUNT + 1))
        done
        echo "### ASK2: $ASK_PATCH_COUNT in-tree kernel patches staged"
    else
        echo "### ASK2: 0 kernel patches staged (common board stack carries all PCD features)"
    fi
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
scripts/config --set-val CONFIG_FUSE_FS y
scripts/config --set-val CONFIG_QORIQ_CPUFREQ y
scripts/config --set-val CONFIG_FSL_EDMA y
scripts/config --set-val CONFIG_SERIAL_OF_PLATFORM y
scripts/config --set-val CONFIG_MAXLINEAR_GPHY y
scripts/config --set-val CONFIG_IMX2_WDT y
scripts/config --set-val CONFIG_SPI_FSL_QUADSPI y
# CAAM (NXP SEC 5.4) hardware crypto built-in for ASK2 IPsec offload (spec §8.1).
# vyos_defconfig ships these tristate symbols as =m; force =y so the CAAM/QI
# backend is present at FMan bring-up and patch 0134's
# caam_qi_ext_consumer_register/_release are compiled-in + EXPORT_SYMBOL_GPL'd
# (a =m caam_jr would force fragile module load-order coupling with ask.ko).
# CONFIG_CRYPTO_DEV_FSL_CAAM_QI is the symbol that actually compiles qi.c — the
# patch's edits and exports live there; the original 5-symbol plan omitted it.
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_COMMON y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_JR y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_QI y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_CRYPTO_API_DESC y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_AHASH_API_DESC y
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
    else
        # Commit each successfully-applied patch so that subsequent patches'
        # `git apply --3way` sees the cumulative on-disk state as their merge
        # base. Without this commit step, every patch re-bases against the
        # original pristine commit and effectively falls through to a plain
        # direct apply that requires exact context match — which fails after
        # earlier patches have shifted line numbers (e.g. 1060's context in
        # fman_pcd.c after 1044's pre-netdev-hook insertions).
        git -c user.email=ci@local -c user.name=ci add -A
        git -c user.email=ci@local -c user.name=ci commit -q --allow-empty -m "applied: $pname" || true
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
# Patch-less source modification: add TC_SETUP_FT case to dpaa_setup_tc()
# TC_SETUP_FT is required by nf_flow_table_offload_setup() when the
# netdev has ndo_setup_tc.  Without it, nft 'flags offload' never
# reaches flow_indr_dev_setup_offload() — dpaa_setup_tc() returns
# -EOPNOTSUPP from its default: case.  Injected via sed (not a
# .patch file) to avoid the git apply --3way context-matching wall.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "/case TC_SETUP_BLOCK:/a\        case TC_SETUP_FT:\n                return dpaa_setup_tc_flow_block(net_dev, type_data);" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: TC_SETUP_FT case injected (sed)"
fi

# Fix fe_flow debugfs 8-byte key truncation (post-patch fixup)
# The fe_flow debugfs read handler was hardcoded to display the first 16
# bytes of DDR flow records (8-byte bucket pointer + first 8 key bytes).
# For 13-byte 5-tuple keys, this truncated PROTO+SPORT+DPORT, making
# TCP/UDP flow matching unverifiable. Fix: display only flow key at
# FMAN_EHASH_FLOW_KEY_OFF (offset 8) for flow->key_size bytes.
echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgICAgICBzcmMgPSBmLnJlYWQoKQpleGNlcHQgRmlsZU5vdEZvdW5kRXJyb3I6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IGZlX2Zsb3cgZml4IC0gZmlsZSBub3QgZm91bmQiKQogICAgc3lzLmV4aXQoMCkKCmNoYW5nZXMgPSAwCgojIEZpbmQgdGhlIGZtYW5fcGNkX2ZlX2Zsb3dfc2hvdyBmdW5jdGlvbgphbmNob3IgPSAic3RhdGljIGludCBmbWFuX3BjZF9mZV9mbG93X3Nob3ciCmlmIGFuY2hvciBub3QgaW4gc3JjOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBmZV9mbG93IGZpeCAtIGZ1bmN0aW9uIG5vdCBmb3VuZCIpCiAgICBzeXMuZXhpdCgwKQoKIyBGaW5kIHRoZSBmdW5jdGlvbiBib2R5CnBvcyA9IHNyYy5maW5kKGFuY2hvcikKZnVuY19zdGFydCA9IHNyYy5maW5kKCJ7IiwgcG9zKQpmdW5jX2VuZCA9IHNyYy5maW5kKCJcbn1cbiIsIGZ1bmNfc3RhcnQpCgppZiBmdW5jX3N0YXJ0IDwgMCBvciBmdW5jX2VuZCA8IDA6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IGZlX2Zsb3cgZml4IC0gZnVuY3Rpb24gYm9keSBub3QgZm91bmQiKQogICAgc3lzLmV4aXQoMCkKCmZ1bmNfYm9keSA9IHNyY1tmdW5jX3N0YXJ0OmZ1bmNfZW5kKzJdCgojIDEuIEFkZCBrZXkgcG9pbnRlciBhZnRlciByIGRlY2xhcmF0aW9uCm9sZF9yID0gImNvbnN0IHU4ICpyID0gZmxvdy0+cmVjb3JkOyIKbmV3X3IgPSAiY29uc3QgdTggKnIgPSBmbG93LT5yZWNvcmQ7XG5cdFx0XHRjb25zdCB1OCAqa2V5ID0gciArIEZNQU5fRUhBU0hfRkxPV19LRVlfT0ZGOyIKaWYgb2xkX3IgaW4gZnVuY19ib2R5IGFuZCAiY29uc3QgdTggKmtleSIgbm90IGluIGZ1bmNfYm9keToKICAgIGZ1bmNfYm9keSA9IGZ1bmNfYm9keS5yZXBsYWNlKG9sZF9yLCBuZXdfciwgMSkKICAgIGNoYW5nZXMgKz0gMQogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBmZV9mbG93IGZpeCAtIGFkZGVkIGtleSBwb2ludGVyIikKCiMgMi4gQ2hhbmdlIGxvb3AgZnJvbSAxNiB0byBmbG93LT5rZXlfc2l6ZQpvbGRfbG9vcCA9ICJmb3IgKGkgPSAwOyBpIDwgMTY7IGkrKykiCm5ld19sb29wID0gImZvciAoaSA9IDA7IGkgPCBmbG93LT5rZXlfc2l6ZTsgaSsrKSIKaWYgb2xkX2xvb3AgaW4gZnVuY19ib2R5OgogICAgZnVuY19ib2R5ID0gZnVuY19ib2R5LnJlcGxhY2Uob2xkX2xvb3AsIG5ld19sb29wLCAxKQogICAgY2hhbmdlcyArPSAxCiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IGZlX2Zsb3cgZml4IC0gY2hhbmdlZCBsb29wIHRvIGZsb3ctPmtleV9zaXplIikKCiMgMy4gQ2hhbmdlIHJbaV0gdG8ga2V5W2ldCm9sZF9wcmludCA9ICdzZXFfcHJpbnRmKHMsICIlMDJ4IiwgcltpXSk7JwpuZXdfcHJpbnQgPSAnc2VxX3ByaW50ZihzLCAiJTAyeCIsIGtleVtpXSk7JwppZiBvbGRfcHJpbnQgaW4gZnVuY19ib2R5OgogICAgZnVuY19ib2R5ID0gZnVuY19ib2R5LnJlcGxhY2Uob2xkX3ByaW50LCBuZXdfcHJpbnQsIDEpCiAgICBjaGFuZ2VzICs9IDEKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogZmVfZmxvdyBmaXggLSBjaGFuZ2VkIHJbaV0gdG8ga2V5W2ldIikKCmlmIGNoYW5nZXMgPiAwOgogICAgc3JjID0gc3JjWzpmdW5jX3N0YXJ0XSArIGZ1bmNfYm9keSArIHNyY1tmdW5jX2VuZCsyOl0KICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAgZi53cml0ZShzcmMpCiAgICBwcmludChmIiMjIyBmbWFuX3BjZC5jOiBmZV9mbG93IGZpeCBhcHBsaWVkICh7Y2hhbmdlc30gY2hhbmdlcykiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBmZV9mbG93IGZpeCAtIG5vIGNoYW5nZXMgbmVlZGVkIChhbHJlYWR5IGFwcGxpZWQ/KSIpCg==' | base64 -d | python3


# Performance: OVFQ=1 on TX FQ context_a for FMan hardware direct enqueue.
# OVFQ=1 means FMan uses the FQID from the ENQUEUE_PKT opcode operand
# instead of the ICAD — required for the AC_CC FE/ehash HIT path.
# B0V is kept at 1 (kernel TX confirmation safety — see plans/ASK2-
# PERFORMANCE-MODERNIZATION.md §7 for the dedicated-FQ plan with B0V=0).
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "s/0x1e00000080000000ULL/0x9e00000080000000ULL/" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: OVFQ=1 injected (sed)"

    # B0V=0: disable context_b writebacks for hardware-offloaded frames.
    # With EBD=1 (FMan deallocates buffers in hardware), the QMan portal
    # does not need to write buffer-release confirmations to context_b.
    # cdx.ko uses hi=0x9a000000 (B0V=0); we follow suit.  Safe for
    # non-offloaded TX because buffer-release confirmation goes through
    # a separate TX_CONFIRM FQ, not context_b of the TX FQ.
    sed -i "s/0x9e00000080000000ULL/0x9a00000080000000ULL/" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: B0V=0 injected (sed)"
fi

# Performance: deeper TX FQ taildrop (2MB -> 4MB) for 10G throughput.
# The 2MB default fills quickly at 10G line rate; 4MB gives more headroom
# before QMan taildrop kicks in, reducing per-flow backpressure.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "s/#define DPAA_FQ_TD 0x200000/#define DPAA_FQ_TD 0x400000/" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: DPAA_FQ_TD=4MB injected (sed)"
fi

# Performance: deeper TX FQ taildrop (2MB -> 4MB) for 10G throughput.
# The 2MB default fills quickly at 10G line rate; 4MB gives more headroom

# Fix dropped board patches: use sed injection instead of raw patch
# (raw patch -p1 silently drops hunks when line numbers drift in kernel 6.18)

# Patch 4009 equivalent: fix OEM SFP-10G-T quirk + add OEM SFP-10G-SR quirk
if [ -f drivers/net/phy/sfp.c ]; then
    # Change sfp_fixup_rollball_cc to sfp_fixup_fs_10gt for OEM SFP-10G-T
    sed -i 's/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_rollball_cc)/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt)/' \
        drivers/net/phy/sfp.c
    # Add OEM SFP-10G-SR quirk entry (our modules report "SR" but are copper rollball)
    sed -i '/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt)/a\	SFP_QUIRK_F("OEM", "SFP-10G-SR", sfp_fixup_fs_10gt),' \
        drivers/net/phy/sfp.c
    echo "### sfp.c: OEM SFP-10G-T/SR rollball quirk injected (sed)"
fi

# F-048: Set EKFC to 0x00180006 — IPSRC1|IPDST1|L4PSRC|L4PDST.
# 4-tuple extraction (12 bytes) without PTYPE1 (bit 18) which causes BMI
# stall on LS1046A FMan 210.10.1 microcode. EKFC=0x001C0006 (with PTYPE1)
# was proven to stall port 0x10/0x11 on the first frame (2026-07-14).
# The 2026-07-10 working build used 0x00180006 without stall.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    sed -i 's/scheme_regs\.kgse_ekfc = DEFAULT_HASH_KEY_EXTRACT_FIELDS;/scheme_regs.kgse_ekfc = 0x00180006; \/\* F-048-R1: 12B key = SIP+DIP+SPORT+DPORT (no PTYPE1) \*\//' \
        drivers/net/ethernet/freescale/fman/fman_keygen.c
    echo "### fman_keygen.c: EKFC 0x00180206→0x00180006 (remove PTYPE1, no stall)"
fi

# F-062c-R1: Add ENQUEUE_KG_DFLT_NIA to AC_CC branch (next_engine==3).
# Without DFLT_NIA, the FE-VM scheme has no recovery path when the CC engine
# returns → first frame through FE-VM permanently stalls the BMI port.
# The Qdrant-proven CONT_LOOKUP pass-through (7.37 Gbps on build 28809182051)
# used next_engine==2 (implicit CCBS) which has DFLT_NIA. The fe_arm function
# uses next_engine==3 (FE_ENTER arm via FMBM_RCCB) which misses DFLT_NIA.
# Bit 2 conflict between DFLT_NIA and AC_CC is mitigated by the CC engine
# taking priority when active; DFLT_NIA provides the fallback recovery path.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX2tleWdlbi5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgICAgICBzcmMgPSBmLnJlYWQoKQpleGNlcHQgRmlsZU5vdEZvdW5kRXJyb3I6CiAgICBwcmludCgiIyMjIGZtYW5fa2V5Z2VuLmM6IERGTFRfTklBIGZpeCAtIGZpbGUgbm90IGZvdW5kIikKICAgIHN5cy5leGl0KDApCgpjaGFuZ2VzID0gMAoKIyBGaXg6IEFkZCBFTlFVRVVFX0tHX0RGTFRfTklBIHRvIHRoZSBuZXh0X2VuZ2luZT09MyAoRkUtVk0gQUNfQ0MpIGJyYW5jaAojIFRoZSBidWc6IHRtcF9yZWcgfD0gTklBX0VOR19GTV9DVEwgfCBOSUFfRk1fQ1RMX0FDX0NDIHdpdGhvdXQgREZMVF9OSUEKIyBjYXVzZXMgQk1JIHN0YWxsIGJlY2F1c2UgdGhlIHNjaGVtZSBoYXMgbm8gcmVjb3ZlcnkgcGF0aCB3aGVuIENDIHJldHVybnMuCm9sZF9saW5lID0gIlx0XHRcdHRtcF9yZWcgfD0gTklBX0VOR19GTV9DVEwgfCBOSUFfRk1fQ1RMX0FDX0NDOyIKbmV3X2xpbmUgPSAiXHRcdFx0dG1wX3JlZyB8PSBFTlFVRVVFX0tHX0RGTFRfTklBIHwgTklBX0VOR19GTV9DVEwgfCBOSUFfRk1fQ1RMX0FDX0NDO1x0LyogRi0wNjJjLVIxOiBERkxUX05JQSBmb3IgQ0MgcmV0dXJuIHBhdGggKHByZXZlbnRzIEJNSSBzdGFsbCkgKi8iCgppZiBvbGRfbGluZSBpbiBzcmM6CiAgICBzcmMgPSBzcmMucmVwbGFjZShvbGRfbGluZSwgbmV3X2xpbmUsIDEpCiAgICBjaGFuZ2VzICs9IDEKICAgIHByaW50KCIjIyMgZm1hbl9rZXlnZW4uYzogREZMVF9OSUEgYWRkZWQgdG8gQUNfQ0MgYnJhbmNoIChGLTA2MmMtUjEpIikKZWxzZToKICAgICMgQWxyZWFkeSBmaXhlZD8KICAgIGlmICJcdFx0XHR0bXBfcmVnIHw9IEVOUVVFVUVfS0dfREZMVF9OSUEgfCBOSUFfRU5HX0ZNX0NUTCB8IE5JQV9GTV9DVExfQUNfQ0M7IiBpbiBzcmM6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX2tleWdlbi5jOiBERkxUX05JQSBhbHJlYWR5IHByZXNlbnQgaW4gQUNfQ0MgYnJhbmNoIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX2tleWdlbi5jOiBDb3VsZCBub3QgZmluZCBBQ19DQyBicmFuY2ggdG8gZml4IikKCmlmIGNoYW5nZXMgPiAwOgogICAgd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjoKICAgICAgICBmLndyaXRlKHNyYykKICAgIHByaW50KGYiIyMjIGZtYW5fa2V5Z2VuLmM6IERGTFRfTklBIGZpeCBhcHBsaWVkICh7Y2hhbmdlc30gY2hhbmdlcykiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX2tleWdlbi5jOiBERkxUX05JQSBmaXggLSBubyBjaGFuZ2VzIG1hZGUiKQo=' | base64 -d | python3
    echo "### fman_keygen.c: F-062c-R1 DFLT_NIA fixup applied"
fi

# F-040/F-002: fman_pcd.c post-patch MURAM zeroing + leak fix.
# Base64-encoded Python fixer (no escape issues).
# Runs after kernel post-patches commit, before compilation.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp3aXRoIG9wZW4ocGF0aCkgYXMgZjogc3JjID0gZi5yZWFkKCkKY2hhbmdlcyA9IDAKCiMgMC4gRGVmaW5lIGJvdGggZ2xvYmFscwpmb3IgZyBpbiBbInVuc2lnbmVkIGludCBmbWFuX3BjZF9oYXNoX29mZnNldCIsICJ2b2lkICpmbWFuX3BjZF9pY192YWRkciJdOgogICAgaWYgZyBub3QgaW4gc3JjOgogICAgICAgIGZpcnN0X2luYyA9IHNyYy5maW5kKCIjaW5jbHVkZSIpCiAgICAgICAgaWYgZmlyc3RfaW5jID4gMDoKICAgICAgICAgICAgc3JjID0gc3JjWzpmaXJzdF9pbmNdICsgZyArICI7XG4iICsgc3JjW2ZpcnN0X2luYzpdCiAgICAgICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgICAgICBwcmludChmIiMjIyBmbWFuX3BjZC5jOiBGLTA2OWIgdjYge2d9IGRlZmluZWQiKQoKIyAxLiBJbnNlcnQgaWNfcHJvYmVfc2hvdyBpZiBub3QgcHJlc2VudCAoaGFuZGxlIGZyZXNoIEFORCB1cGdyYWRlIGZyb20gb2xkIHY0KQppZiAiZm1hbl9wY2RfaWNfcHJvYmVfc2hvdyIgbm90IGluIHNyYzoKICAgIGFuY2hvciA9ICJzdGF0aWMgaW50IGZtYW5fcGNkX2ZlX3Byb2JlX3Nob3ciCiAgICBpZiBhbmNob3IgaW4gc3JjOgogICAgICAgIGNvZGUgPSAoCiAgICAgICAgICAgICIvKiBGLTA2OWIgdjY6IGljX3Byb2JlIGRlYnVnZnMgLSBkdW1wIEZNYW4gSUMgYXQgY29ycmVjdCBvZmZzZXRzICovXG4iCiAgICAgICAgICAgICJzdGF0aWMgaW50IGZtYW5fcGNkX2ljX3Byb2JlX3Nob3coc3RydWN0IHNlcV9maWxlICpzLCB2b2lkICp1bnVzZWQpXG4iCiAgICAgICAgICAgICJ7XG4iCiAgICAgICAgICAgICJcdHZvaWQgKnZhZGRyO1xuIgogICAgICAgICAgICAiXHR1bnNpZ25lZCBpbnQgaGFzaF9vZmYsIHByc19vZmYsIGk7XG4iCiAgICAgICAgICAgICJcbiIKICAgICAgICAgICAgIlx0c21wX3JtYigpO1xuIgogICAgICAgICAgICAiXHR2YWRkciA9IGZtYW5fcGNkX2ljX3ZhZGRyO1xuIgogICAgICAgICAgICAiXHRpZiAoIXZhZGRyKSB7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3B1dHMocywgXCJubyBmcmFtZSBjYXB0dXJlZFxcblwiKTtcbiIKICAgICAgICAgICAgIlx0XHRyZXR1cm4gMDtcbiIKICAgICAgICAgICAgIlx0fVxuIgogICAgICAgICAgICAiXHRoYXNoX29mZiA9IGZtYW5fcGNkX2hhc2hfb2Zmc2V0O1xuIgogICAgICAgICAgICAiXHRwcnNfb2ZmID0gaGFzaF9vZmYgLSAweDI4O1xuIgogICAgICAgICAgICAiXHRzZXFfcHJpbnRmKHMsIFwidmFkZHI9JXB4IGhhc2hfb2ZmPSV1IHByc19vZmY9JXVcXG5cIiwgdmFkZHIsIGhhc2hfb2ZmLCBwcnNfb2ZmKTtcbiIKICAgICAgICAgICAgIlx0c2VxX3ByaW50ZihzLCBcInBhcnNlX3Jlc3VsdDogXCIpO1xuIgogICAgICAgICAgICAiXHRmb3IgKGkgPSAwOyBpIDwgODsgaSsrKSB7XG4iCiAgICAgICAgICAgICJcdFx0dTMyIHYgPSBiZTMyX3RvX2NwdSgoKHUzMiAqKXZhZGRyKVtwcnNfb2ZmLzQgKyBpXSk7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3ByaW50ZihzLCBcIiBbJTAyZF09JTA4eFwiLCBwcnNfb2ZmLzQgKyBpLCB2KTtcbiIKICAgICAgICAgICAgIlx0fVxuIgogICAgICAgICAgICAiXHRzZXFfcHJpbnRmKHMsIFwiXFxuaGFzaDogICAgICAgXCIpO1xuIgogICAgICAgICAgICAiXHRmb3IgKGkgPSAwOyBpIDwgMjsgaSsrKSB7XG4iCiAgICAgICAgICAgICJcdFx0dTMyIHYgPSBiZTMyX3RvX2NwdSgoKHUzMiAqKXZhZGRyKVtoYXNoX29mZi80ICsgaV0pO1xuIgogICAgICAgICAgICAiXHRcdHNlcV9wcmludGYocywgXCIgWyUwMmRdPSUwOHhcIiwgaGFzaF9vZmYvNCArIGksIHYpO1xuIgogICAgICAgICAgICAiXHR9XG4iCiAgICAgICAgICAgICJcdHNlcV9wdXRzKHMsIFwiXFxuXCIpO1xuIgogICAgICAgICAgICAiXHRyZXR1cm4gMDtcbiIKICAgICAgICAgICAgIn1cbiIKICAgICAgICAgICAgIlxuIgogICAgICAgICAgICAic3RhdGljIGludCBmbWFuX3BjZF9pY19wcm9iZV9vcGVuKHN0cnVjdCBpbm9kZSAqaW5vZGUsIHN0cnVjdCBmaWxlICpmaWxlKVxuIgogICAgICAgICAgICAie1xuIgogICAgICAgICAgICAiXHRyZXR1cm4gc2luZ2xlX29wZW4oZmlsZSwgZm1hbl9wY2RfaWNfcHJvYmVfc2hvdywgaW5vZGUtPmlfcHJpdmF0ZSk7XG4iCiAgICAgICAgICAgICJ9XG4iCiAgICAgICAgICAgICJcbiIKICAgICAgICAgICAgInN0YXRpYyBjb25zdCBzdHJ1Y3QgZmlsZV9vcGVyYXRpb25zIGZtYW5fcGNkX2ljX3Byb2JlX2ZvcHMgPSB7XG4iCiAgICAgICAgICAgICJcdC5vd25lclx0XHQ9IFRISVNfTU9EVUxFLFxuIgogICAgICAgICAgICAiXHQub3Blblx0XHQ9IGZtYW5fcGNkX2ljX3Byb2JlX29wZW4sXG4iCiAgICAgICAgICAgICJcdC5yZWFkXHRcdD0gc2VxX3JlYWQsXG4iCiAgICAgICAgICAgICJcdC5sbHNlZWtcdFx0PSBzZXFfbHNlZWssXG4iCiAgICAgICAgICAgICJcdC5yZWxlYXNlXHQ9IHNpbmdsZV9yZWxlYXNlLFxuIgogICAgICAgICAgICAifTtcblxuIgogICAgICAgICkKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShhbmNob3IsIGNvZGUgKyBhbmNob3IpCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2OWIgdjYgaWNfcHJvYmVfc2hvdyBpbnNlcnRlZCIpCiAgICBlbHNlOgogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjliIHY2IGFuY2hvciBub3QgZm91bmQiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2OWIgdjYgaWNfcHJvYmVfc2hvdyBhbHJlYWR5IHByZXNlbnQiKQoKIyAyLiBSZWdpc3RlciBkZWJ1Z2ZzCmlmICdkZWJ1Z2ZzX2NyZWF0ZV9maWxlKCJpY19wcm9iZSInIG5vdCBpbiBzcmM6CiAgICBkYmdfYW5jaG9yID0gJ2RlYnVnZnNfY3JlYXRlX2ZpbGUoImZlX3Byb2JlIicKICAgIGlmIGRiZ19hbmNob3IgaW4gc3JjOgogICAgICAgIHByb2JlX2RiZyA9ICgKICAgICAgICAgICAgJ1x0XHRcdGRlYnVnZnNfY3JlYXRlX2ZpbGUoImljX3Byb2JlIiwgMDQ0NCxcbicKICAgICAgICAgICAgJ1x0XHRcdFx0XHQgICAgcGNkLT5kZWJ1Z2ZzX2RpciwgcGNkLFxuJwogICAgICAgICAgICAnXHRcdFx0XHRcdCAgICAmZm1hbl9wY2RfaWNfcHJvYmVfZm9wcyk7XG4nCiAgICAgICAgICAgICdcdFx0XHQnICsgZGJnX2FuY2hvcgogICAgICAgICkKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShkYmdfYW5jaG9yLCBwcm9iZV9kYmcpCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2OWIgdjYgaWNfcHJvYmUgcmVnaXN0ZXJlZCIpCgppZiBjaGFuZ2VzID4gMDoKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6IGYud3JpdGUoc3JjKQogICAgcHJpbnQoZiIjIyMgZm1hbl9wY2QuYzogRi0wNjliIHY2IHtjaGFuZ2VzfSBjaGFuZ2UocykgYXBwbGllZCIpCmVsc2U6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDY5YiB2NiBubyBjaGFuZ2VzIikK' | base64 -d | python3
fi


# M2-4: fix fman_port_lookup_rx — all LS1046A fman_port->port_id==0
# (mainline of_alias_get_id fallback returns -ENODEV).  The lookup
# comparison p->port_id == port_id always fails for non-zero port_id.
# Remove the port_id check; match on fm + port_type only.
# cc_test works by accident (%hhi "0x10" → port_id=0, which matches).
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgIHNyYyA9IGYucmVhZCgpCgpjaGFuZ2VzID0gMAoKIyAtLS0tIDEuIEFkZCBmZV9wb29sX29mZiB0byBzdHJ1Y3QgZm1hbl9wY2QgLS0tLQppZiAidW5zaWduZWQgbG9uZyBmZV9wb29sX29mZjsiIGluIHNyYzoKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcG9vbF9vZmYgZmllbGQgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIHN0cnVjdF9hbmNob3IgPSAiaW50IGZlX3JlZmNvdW50OyIKICAgIGlmIHN0cnVjdF9hbmNob3IgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKHN0cnVjdF9hbmNob3IsCiAgICAgICAgICAgIHN0cnVjdF9hbmNob3IgKyAiXG5cdHVuc2lnbmVkIGxvbmcgZmVfcG9vbF9vZmY7XHQvKiBGLTA2MSB2NDogbWlkLXBvb2wgRkUgc2xvdCBNVVJBTSBvZmZzZXQgZm9yIGZlX3Byb2JlICovIiwKICAgICAgICAgICAgMSkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIGZpZWxkIGFkZGVkIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBXQVJOSU5HOiBzdHJ1Y3QgYW5jaG9yICdpbnQgZmVfcmVmY291bnQ7JyBub3QgZm91bmQiKQoKIyAtLS0tIDIuIFNhdmUgZmVfcG9vbF9vZmYgYXQgc2xvdCBpbmRleCA1IChwYXN0IDMgc2luZ2xldG9ucyArIGJ1ZmZlcikgLS0tLQojIFNpbmdsZXRvbnMgKE1VWD0wLCBUcmFuc2l0aW9uPTEsIEV4aXQ9MikgZGVxdWV1ZSBmcm9tIGZlX2F2YWlsYWJsZSBIRUFELgojIEZyYW1lcyBjb25zdW1lIEhFQUQgYWZ0ZXIgc2luZ2xldG9uczogc2xvdHMgMyw0LDUsNiw3Li4uIHBlciBmcmFtZS4KIyBJbmRleCA1ID0gM3JkIGZyYW1lIHdvcmtzcGFjZS4gQWZ0ZXIgNSBwaW5ncywgc2xvdCA1IGlzIGd1YXJhbnRlZWQgdXNlZC4KIyBGaXJzdCBjaGVjayBmb3IgYW55IGV4aXN0aW5nIHNhdmUgcGF0dGVybnMgYW5kIHJlcGxhY2UgdGhlbS4Kb2xkX3YxID0gIlx0XHRpZiAoIXBjZC0+ZmVfcG9vbF9vZmYpXHQvKiBGLTA2MTogc2F2ZSBmaXJzdCBzbG90IGZvciBmZV9wcm9iZSAqL1xuXHRcdFx0cGNkLT5mZV9wb29sX29mZiA9IG9mZjsiCm9sZF92MyA9ICJcdFx0cGNkLT5mZV9wb29sX29mZiA9IG9mZjtcdC8qIEYtMDYxIHYyOiBzYXZlIGxhc3QgcG9vbCBzbG90IGZvciBmZV9wcm9iZSAob3ZlcndyaXR0ZW4gZWFjaCBpdGVyKSAqLyIKbmV3X3NhdmUgPSAiXHRcdGlmIChpID09IDUpXHQvKiBGLTA2MSB2NDogc2xvdCA1IHBhc3QgMyBzaW5nbGV0b25zICovXG5cdFx0XHRwY2QtPmZlX3Bvb2xfb2ZmID0gb2ZmOyIKCmZvdW5kID0gRmFsc2UKaWYgbmV3X3NhdmUgaW4gc3JjOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBmZV9wb29sX29mZiB2NCBzYXZlIGFscmVhZHkgcHJlc2VudCIpCiAgICBmb3VuZCA9IFRydWUKZWxpZiBvbGRfdjMgaW4gc3JjOgogICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkX3YzLCBuZXdfc2F2ZSkKICAgIGNoYW5nZXMgKz0gMQogICAgZm91bmQgPSBUcnVlCiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIHNhdmUgZml4ZWQgdjMtPnY0IChzbG90IDUpIikKZWxpZiBvbGRfdjEgaW4gc3JjOgogICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkX3YxLCBuZXdfc2F2ZSkKICAgIGNoYW5nZXMgKz0gMQogICAgZm91bmQgPSBUcnVlCiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIHNhdmUgZml4ZWQgdjEtPnY0IChzbG90IDUpIikKCmlmIG5vdCBmb3VuZDoKICAgIGFsbG9jX2FuY2hvciA9ICJcdFx0bGlzdF9hZGRfdGFpbCgmb2JqLT5ub2RlLCAmcGNkLT5mZV9hdmFpbGFibGUpOyIKICAgIGlmIGFsbG9jX2FuY2hvciBpbiBzcmM6CiAgICAgICAgIyBPbmx5IHNhdmUgb24gZmlyc3QgbWF0Y2ggKHRoZSBsb29wIGJvZHkpCiAgICAgICAgc2F2ZV9ibG9jayA9IGFsbG9jX2FuY2hvciArICJcbiIgKyBuZXdfc2F2ZSArICI7IgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKGFsbG9jX2FuY2hvciwgc2F2ZV9ibG9jaywgMSkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIHY0IHNhdmUgaW5zZXJ0ZWQgKGZyZXNoKSIpCiAgICBlbHNlOgogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgV0FSTklORzogYWxsb2MgYW5jaG9yIG5vdCBmb3VuZCIpCgojIC0tLS0gMy4gQWRkIGZlX3Byb2JlX3Nob3cgZnVuY3Rpb24gYmVmb3JlIGZlX3BvcnRfc2hvdyAtLS0tCmlmICJmbWFuX3BjZF9mZV9wcm9iZV9zaG93IiBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlX3Nob3cgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIHByb2JlX2FuY2hvciA9ICJzdGF0aWMgaW50IGZtYW5fcGNkX2ZlX3BvcnRfc2hvdyhzdHJ1Y3Qgc2VxX2ZpbGUgKnMsIHZvaWQgKnVudXNlZCkiCiAgICBpZiBwcm9iZV9hbmNob3IgaW4gc3JjOgogICAgICAgIHByb2JlX2NvZGUgPSAoCiAgICAgICAgICAgICIvKiBGLTA2MSB2NDogZmVfcHJvYmUgZGVidWdmcyAtIGR1bXAgRkUgcG9vbCBzbG90IHRvIHJlYWRcbiIKICAgICAgICAgICAgIiAqIHRoZSBLRy1leHRyYWN0ZWQga2V5IGJ5dGVzIGZyb20gdGhlIEZFX0VOVEVSIHdvcmtzcGFjZS5cbiIKICAgICAgICAgICAgIiAqIFRoZSBBTExPQ0FURSB3b3Jrc3BhY2UgaXMgbm90IHplcm9lZCBvbiBmcmVlLCBzbyBhZnRlciBhXG4iCiAgICAgICAgICAgICIgKiBmcmFtZSBwYXNzZXMgdGhyb3VnaCwgdGhlIEtHIGhhc2ggYW5kIGtleSBieXRlcyBzdXJ2aXZlLlxuIgogICAgICAgICAgICAiICovXG4iCiAgICAgICAgICAgICJzdGF0aWMgaW50IGZtYW5fcGNkX2ZlX3Byb2JlX3Nob3coc3RydWN0IHNlcV9maWxlICpzLCB2b2lkICp1bnVzZWQpXG4iCiAgICAgICAgICAgICJ7XG4iCiAgICAgICAgICAgICJcdHN0cnVjdCBmbWFuX3BjZCAqcGNkID0gcy0+cHJpdmF0ZTtcbiIKICAgICAgICAgICAgIlx0c3RydWN0IG11cmFtX2luZm8gKm11cmFtID0gZm1hbl9nZXRfbXVyYW0ocGNkLT5mbWFuKTtcbiIKICAgICAgICAgICAgIlx0dm9pZCBfX2lvbWVtICp3c19iYXNlO1xuIgogICAgICAgICAgICAiXHR1bnNpZ25lZCBpbnQgaTtcbiIKICAgICAgICAgICAgIlxuIgogICAgICAgICAgICAiXHRtdXRleF9sb2NrKCZwY2QtPmZlX2xvY2spO1xuIgogICAgICAgICAgICAiXHRpZiAoIW11cmFtIHx8IHBjZC0+ZmVfcmVmY291bnQgPT0gMCkge1xuIgogICAgICAgICAgICAiXHRcdHNlcV9wdXRzKHMsIFwiZmUgcG9vbCBub3QgZW5nYWdlZFxcblwiKTtcbiIKICAgICAgICAgICAgIlx0XHRtdXRleF91bmxvY2soJnBjZC0+ZmVfbG9jayk7XG4iCiAgICAgICAgICAgICJcdFx0cmV0dXJuIDA7XG4iCiAgICAgICAgICAgICJcdH1cbiIKICAgICAgICAgICAgIlx0aWYgKCFwY2QtPmZlX3Bvb2xfb2ZmKSB7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3B1dHMocywgXCJmZSBwb29sIG5vdCBhbGxvY2F0ZWRcXG5cIik7XG4iCiAgICAgICAgICAgICJcdFx0bXV0ZXhfdW5sb2NrKCZwY2QtPmZlX2xvY2spO1xuIgogICAgICAgICAgICAiXHRcdHJldHVybiAwO1xuIgogICAgICAgICAgICAiXHR9XG4iCiAgICAgICAgICAgICJcdHdzX2Jhc2UgPSBmbWFuX211cmFtX29mZnNldF90b192YmFzZShtdXJhbSwgcGNkLT5mZV9wb29sX29mZik7XG4iCiAgICAgICAgICAgICJcdHNlcV9wcmludGYocywgXCJwb29sPTB4JTA1bHhcXG5cIiwgcGNkLT5mZV9wb29sX29mZik7XG4iCiAgICAgICAgICAgICJcdGZvciAoaSA9IDA7IGkgPCA4OyBpKyspIHtcbiIKICAgICAgICAgICAgIlx0XHR1MzIgdiA9IGlvcmVhZDMyYmUoKHUzMiBfX2lvbWVtICopd3NfYmFzZSArIGkpO1xuIgogICAgICAgICAgICAiXHRcdHNlcV9wcmludGYocywgXCIgWyUwMmRdPSUwOHhcIiwgaSwgdik7XG4iCiAgICAgICAgICAgICJcdH1cbiIKICAgICAgICAgICAgIlx0c2VxX3B1dHMocywgXCJcXG5cIik7XG4iCiAgICAgICAgICAgICJcdG11dGV4X3VubG9jaygmcGNkLT5mZV9sb2NrKTtcbiIKICAgICAgICAgICAgIlx0cmV0dXJuIDA7XG4iCiAgICAgICAgICAgICJ9XG4iCiAgICAgICAgICAgICJcbiIKICAgICAgICAgICAgInN0YXRpYyBpbnQgZm1hbl9wY2RfZmVfcHJvYmVfb3BlbihzdHJ1Y3QgaW5vZGUgKmlub2RlLCBzdHJ1Y3QgZmlsZSAqZmlsZSlcbiIKICAgICAgICAgICAgIntcbiIKICAgICAgICAgICAgIlx0cmV0dXJuIHNpbmdsZV9vcGVuKGZpbGUsIGZtYW5fcGNkX2ZlX3Byb2JlX3Nob3csIGlub2RlLT5pX3ByaXZhdGUpO1xuIgogICAgICAgICAgICAifVxuIgogICAgICAgICAgICAiXG4iCiAgICAgICAgICAgICJzdGF0aWMgY29uc3Qgc3RydWN0IGZpbGVfb3BlcmF0aW9ucyBmbWFuX3BjZF9mZV9wcm9iZV9mb3BzID0ge1xuIgogICAgICAgICAgICAiXHQub3duZXJcdFx0PSBUSElTX01PRFVMRSxcbiIKICAgICAgICAgICAgIlx0Lm9wZW5cdFx0PSBmbWFuX3BjZF9mZV9wcm9iZV9vcGVuLFxuIgogICAgICAgICAgICAiXHQucmVhZFx0XHQ9IHNlcV9yZWFkLFxuIgogICAgICAgICAgICAiXHQubGxzZWVrXHRcdD0gc2VxX2xzZWVrLFxuIgogICAgICAgICAgICAiXHQucmVsZWFzZVx0PSBzaW5nbGVfcmVsZWFzZSxcbiIKICAgICAgICAgICAgIn07XG5cbiIKICAgICAgICApCiAgICAgICAgc3JjID0gc3JjLnJlcGxhY2UocHJvYmVfYW5jaG9yLCBwcm9iZV9jb2RlICsgcHJvYmVfYW5jaG9yKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcHJvYmVfc2hvdyBmdW5jdGlvbiBpbnNlcnRlZCIpCiAgICBlbHNlOgogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgV0FSTklORzogcHJvYmUgYW5jaG9yIG5vdCBmb3VuZCIpCgojIC0tLS0gNC4gUmVnaXN0ZXIgZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiIC4uLikgLS0tLQppZiAnZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiJyBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlIGRlYnVnZnMgYWxyZWFkeSByZWdpc3RlcmVkIikKZWxzZToKICAgIGRiZ19hbmNob3IgPSAnZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfaGFzaGZlIicKICAgIGlmIGRiZ19hbmNob3IgaW4gc3JjOgogICAgICAgIHByb2JlX2RiZyA9ICgKICAgICAgICAgICAgJ1x0XHRcdGRlYnVnZnNfY3JlYXRlX2ZpbGUoImZlX3Byb2JlIiwgMDQ0NCxcbicKICAgICAgICAgICAgJ1x0XHRcdFx0XHQgICAgcGNkLT5kZWJ1Z2ZzX2RpciwgcGNkLFxuJwogICAgICAgICAgICAnXHRcdFx0XHRcdCAgICAmZm1hbl9wY2RfZmVfcHJvYmVfZm9wcyk7XG4nCiAgICAgICAgICAgICdcdFx0XHQnICsgZGJnX2FuY2hvcgogICAgICAgICkKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShkYmdfYW5jaG9yLCBwcm9iZV9kYmcpCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBmZV9wcm9iZSBkZWJ1Z2ZzIHJlZ2lzdGVyZWQiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIFdBUk5JTkc6IGRlYnVnZnMgYW5jaG9yIG5vdCBmb3VuZCIpCgppZiBjaGFuZ2VzID09IDA6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIG5vIGNoYW5nZXMgKGFsbCBhbHJlYWR5IGFwcGxpZWQpIikKZWxzZToKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAgZi53cml0ZShzcmMpCiAgICBwcmludChmIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBmZV9wcm9iZSB2NDoge2NoYW5nZXN9IGNoYW5nZShzKSBhcHBsaWVkIikK' | base64 -d | python3
    echo "### fman_port.c: M2-4 fman_port_lookup_rx fixed"

# F-063 DISABLED FOR BISECT: EXT_HASH FE contextSize must match keysize.
# Commented out to test if contextSize change (256→key_size-1) causes stall.
: 'F-063-DISABLED'
: ' sed -i '"'"'s/(FMAN_FE_HASH_CONTEXT_SIZE - 1)/(t->key_size - 1)/'"'"' drivers/net/ethernet/freescale/fman/fman_pcd.c'
: ' echo "### fman_pcd.c: F-063 EXT_HASH contextSize fixed (key_size not record_size)"'

# M2-4: reduce FE pool 100->16 to fit 64KB MURAM
# 100x28B rounded 256B = 25600B + pool 8192B + ehash 33280B > 65536B
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/FMAN_PCD_FE_POOL_COUNT[[:space:]]*100/FMAN_PCD_FE_POOL_COUNT 16/' drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo '### fman_pcd.c: M2-4 FE pool reduced 100->16'
fi

# M2-4: fman_port_set_params_page NULL-page clear support (before params-page-free)
# Makes fman_port_set_params_page(rxport, 0, NULL) clear ctrl_params_page
# so fman_pcd_kg.c can zero the field without direct struct dereference.
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgIHNyYyA9IGYucmVhZCgpCgpjaGFuZ2VzID0gMAoKIyAtLS0tIDEuIEFkZCBmZV9wb29sX29mZiB0byBzdHJ1Y3QgLS0tLQppZiAidW5zaWduZWQgbG9uZyBmZV9wb29sX29mZjsiIGluIHNyYzoKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcG9vbF9vZmYgZmllbGQgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIHN0cnVjdF9hbmNob3IgPSAiaW50IGZlX3JlZmNvdW50OyIKICAgIGlmIHN0cnVjdF9hbmNob3IgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKHN0cnVjdF9hbmNob3IsCiAgICAgICAgICAgIHN0cnVjdF9hbmNob3IgKyAiXG5cdHVuc2lnbmVkIGxvbmcgZmVfcG9vbF9vZmY7XHQvKiBGLTA2MSB2NjogbWlkLXBvb2wgRkUgc2xvdCBNVVJBTSBvZmZzZXQgZm9yIGZlX3Byb2JlICovIiwgMSkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIGZpZWxkIGFkZGVkIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBXQVJOSU5HOiBzdHJ1Y3QgYW5jaG9yIG5vdCBmb3VuZCIpCgojIC0tLS0gMi4gU2F2ZSBmZV9wb29sX29mZiBhdCBzbG90IDUgdXNpbmcgYSBVTklRVUUgYW5jaG9yIC0tLS0KIyB2NSBCVUc6IGFsbG9jX2FuY2hvciBtYXRjaGVkIG9uIHRoZSBXUk9ORyBsaXN0X2FkZF90YWlsIGNhbGwgKHRoZXJlIGFyZQojIG11bHRpcGxlIGxpc3RfYWRkX3RhaWwgY2FsbHMgaW4gZm1hbl9wY2QuYykuIFRoZSByZXBsYWNlKC4uLiwgMSkgcmVwbGFjZWQKIyB0aGUgZmlyc3QgbWF0Y2gsIHdoaWNoIHdhcyBOT1QgaW4gZmVfcG9vbF9hbGxvYywgY29ycnVwdGluZyB0aGUgcG9vbC4KIyB2NiBGSVg6IHVzZSBhIGNvbXBvdW5kIGFuY2hvciB0aGF0IGluY2x1ZGVzIGNvbnRleHQgdW5pcXVlIHRvIHRoZSBwb29sCiMgYWxsb2NhdGlvbiBsb29wOiBnZW5fcG9vbF9hbGxvYyArIGxpc3RfYWRkX3RhaWwuCnVuaXF1ZV9hbmNob3IgPSAiZ2VuX3Bvb2xfYWxsb2MocGNkLT5mZV9nZW5fcG9vbCIKaWYgImlmIChpID09IDUpIiBpbiBzcmMgYW5kICJwY2QtPmZlX3Bvb2xfb2ZmID0gb2ZmIiBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIHY0IHNhdmUgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgICMgUmVtb3ZlIGFueSBicm9rZW4gdjUgc2F2ZSB0aGF0IG1pZ2h0IGJlIGxlZnQKICAgIGZvciBicm9rZW4gaW4gWyJcdFx0aWYgKGkgPT0gNSlcdC8qIEYtMDYxIHY0OiBzbG90IDUgcGFzdCAzIHNpbmdsZXRvbnMgKi9cblx0XHRcdHBjZC0+ZmVfcG9vbF9vZmYgPSBvZmY7OyIsCiAgICAgICAgICAgICAgICAgICAiXHRcdHBjZC0+ZmVfcG9vbF9vZmYgPSBvZmY7XHQvKiBGLTA2MSB2Mjogc2F2ZSBsYXN0IHBvb2wgc2xvdCBmb3IgZmVfcHJvYmUgKG92ZXJ3cml0dGVuIGVhY2ggaXRlcikgKi8iLAogICAgICAgICAgICAgICAgICAgIlx0XHRpZiAoIXBjZC0+ZmVfcG9vbF9vZmYpXHQvKiBGLTA2MTogc2F2ZSBmaXJzdCBzbG90IGZvciBmZV9wcm9iZSAqL1xuXHRcdFx0cGNkLT5mZV9wb29sX29mZiA9IG9mZjsiXToKICAgICAgICBpZiBicm9rZW4gaW4gc3JjOgogICAgICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShicm9rZW4sICIiKQogICAgICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICAgICAgcHJpbnQoZiIjIyMgZm1hbl9wY2QuYzogRi0wNjEgcmVtb3ZlZCBicm9rZW4gc2F2ZToge2Jyb2tlbls6NDBdfS4uLiIpCgogICAgIyBGaW5kIHRoZSBhY3R1YWwgcG9vbCBhbGxvYyBsb29wIGJvZHkgYW5kIGFkZCB0aGUgc2F2ZSBBRlRFUiBsaXN0X2FkZF90YWlsCiAgICAjIFVzZSBhIHVuaXF1ZSBlbm91Z2ggYW5jaG9yOiB0aGUgaSAqIEZNQU5fUENEX0ZFX01BWF9TSVpFIHBhdHRlcm4KICAgIGFsbG9jX3BhdHRlcm4gPSAiXHRcdHBjZC0+ZmVfb2JqW2ldLm11cmFtX29mZiA9IG9mZjsiCiAgICBpZiBhbGxvY19wYXR0ZXJuIGluIHNyYzoKICAgICAgICAjIEZpbmQgdGhlIGxpc3RfYWRkX3RhaWwgdGhhdCBpbW1lZGlhdGVseSBmb2xsb3dzIHRoaXMgbGluZQogICAgICAgIHRhaWxfYW5jaG9yID0gYWxsb2NfcGF0dGVybiArICJcblx0XHRsaXN0X2FkZF90YWlsKCZwY2QtPmZlX29ialtpXS5ub2RlLCAmcGNkLT5mZV9hdmFpbGFibGUpOyIKICAgICAgICBpZiB0YWlsX2FuY2hvciBpbiBzcmM6CiAgICAgICAgICAgIG5ld19ibG9jayA9IHRhaWxfYW5jaG9yICsgIlxuXHRcdGlmIChpID09IDUpXHQvKiBGLTA2MSB2Njogc2xvdCA1IHBhc3QgMyBzaW5nbGV0b25zICovXG5cdFx0XHRwY2QtPmZlX3Bvb2xfb2ZmID0gb2ZmOyIKICAgICAgICAgICAgc3JjID0gc3JjLnJlcGxhY2UodGFpbF9hbmNob3IsIG5ld19ibG9jaywgMSkKICAgICAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgdjY6IGZlX3Bvb2xfb2ZmIHNhdmUgYXQgc2xvdCA1IHVzaW5nIHVuaXF1ZSBhbmNob3IiKQogICAgICAgIGVsc2U6CiAgICAgICAgICAgICMgRmFsbGJhY2s6IGluc2VydCBhZnRlciB0aGUgZ2VuX3Bvb2xfYWxsb2MrbGlzdF9hZGRfdGFpbCBwYWlyCiAgICAgICAgICAgICMganVzdCBhZnRlciB0aGUgZm9yIGxvb3Agb3BlbmluZwogICAgICAgICAgICBsb29wX2JvZHkgPSAiXHRmb3IgKGkgPSAwOyBpIDwgQVJSQVlfU0laRShwY2QtPmZlX29iaik7IGkrKykgeyIKICAgICAgICAgICAgaWYgbG9vcF9ib2R5IGluIHNyYzoKICAgICAgICAgICAgICAgIHNhdmVfbGluZSA9ICJcdFx0aWYgKGkgPT0gNSlcdC8qIEYtMDYxIHY2OiBzbG90IDUgcGFzdCAzIHNpbmdsZXRvbnMgKi9cblx0XHRcdHBjZC0+ZmVfcG9vbF9vZmYgPSBvZmY7XG4iCiAgICAgICAgICAgICAgICAjIEluc2VydCBpbnNpZGUgdGhlIGxvb3AgYm9keSBhZnRlciB0aGUgZmlyc3Qgc3RhdGVtZW50CiAgICAgICAgICAgICAgICBmaXJzdF9zdG10ID0gIlxuXHRcdHBjZC0+ZmVfb2JqW2ldLm11cmFtX29mZiA9IG9mZjsiCiAgICAgICAgICAgICAgICBpZiBmaXJzdF9zdG10IGluIHNyYzoKICAgICAgICAgICAgICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShmaXJzdF9zdG10LCBmaXJzdF9zdG10ICsgIlxuIiArIHNhdmVfbGluZSwgMSkKICAgICAgICAgICAgICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICAgICAgICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIHY2OiBzYXZlIGluc2VydGVkIGFmdGVyIGZlX29ialtpXSBpbml0IikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgV0FSTklORzogY291bGQgbm90IGZpbmQgbG9vcCBhbmNob3IiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIFdBUk5JTkc6IGNvdWxkIG5vdCBmaW5kIGZlX29iaiBpbml0IGFuY2hvciIpCgojIC0tLS0gMy4gQWRkIGZlX3Byb2JlX3Nob3cgZnVuY3Rpb24gKHY1OiA2NC13b3JkIHNjYW4sIG5vbi16ZXJvIGZpbHRlcikgLS0tLQpvbGRfcHJvYmVfYW5jaG9yID0gInN0YXRpYyBpbnQgZm1hbl9wY2RfZmVfcG9ydF9zaG93KHN0cnVjdCBzZXFfZmlsZSAqcywgdm9pZCAqdW51c2VkKSIKbmV3X3Byb2JlX2NvZGUgPSAoCiAgICAiLyogRi0wNjEgdjY6IGZlX3Byb2JlIGRlYnVnZnMgLSBzY2FuIEZFIHdvcmtzcGFjZSBmb3IgS0cga2V5XG4iCiAgICAiICogUmVhZHMgNjQgdTMyIHdvcmRzICgyNTZCKSwgcGFzdCB0aGUgMjQ2QiBBTExPQ0FURSB3b3Jrc3BhY2UuXG4iCiAgICAiICogU2hvd3Mgb25seSBub24temVybyB3b3JkcyB0byBmaW5kIHRoZSBrZXkgd2hlcmV2ZXIgaXQgbGFuZHMuXG4iCiAgICAiICovXG4iCiAgICAic3RhdGljIGludCBmbWFuX3BjZF9mZV9wcm9iZV9zaG93KHN0cnVjdCBzZXFfZmlsZSAqcywgdm9pZCAqdW51c2VkKVxuIgogICAgIntcbiIKICAgICJcdHN0cnVjdCBmbWFuX3BjZCAqcGNkID0gcy0+cHJpdmF0ZTtcbiIKICAgICJcdHN0cnVjdCBtdXJhbV9pbmZvICptdXJhbSA9IGZtYW5fZ2V0X211cmFtKHBjZC0+Zm1hbik7XG4iCiAgICAiXHR2b2lkIF9faW9tZW0gKndzX2Jhc2U7XG4iCiAgICAiXHR1bnNpZ25lZCBpbnQgaTtcbiIKICAgICJcdHUzMiB2O1xuIgogICAgIlxuIgogICAgIlx0bXV0ZXhfbG9jaygmcGNkLT5mZV9sb2NrKTtcbiIKICAgICJcdGlmICghbXVyYW0gfHwgcGNkLT5mZV9yZWZjb3VudCA9PSAwKSB7XG4iCiAgICAiXHRcdHNlcV9wdXRzKHMsIFwiZmUgcG9vbCBub3QgZW5nYWdlZFxcblwiKTtcbiIKICAgICJcdFx0bXV0ZXhfdW5sb2NrKCZwY2QtPmZlX2xvY2spO1xuIgogICAgIlx0XHRyZXR1cm4gMDtcbiIKICAgICJcdH1cbiIKICAgICJcdGlmICghcGNkLT5mZV9wb29sX29mZikge1xuIgogICAgIlx0XHRzZXFfcHV0cyhzLCBcImZlIHBvb2wgbm90IGFsbG9jYXRlZFxcblwiKTtcbiIKICAgICJcdFx0bXV0ZXhfdW5sb2NrKCZwY2QtPmZlX2xvY2spO1xuIgogICAgIlx0XHRyZXR1cm4gMDtcbiIKICAgICJcdH1cbiIKICAgICJcdHdzX2Jhc2UgPSBmbWFuX211cmFtX29mZnNldF90b192YmFzZShtdXJhbSwgcGNkLT5mZV9wb29sX29mZik7XG4iCiAgICAiXHRzZXFfcHJpbnRmKHMsIFwicG9vbD0weCUwNWx4XFxuXCIsIHBjZC0+ZmVfcG9vbF9vZmYpO1xuIgogICAgIlx0Zm9yIChpID0gMDsgaSA8IDY0OyBpKyspIHtcbiIKICAgICJcdFx0diA9IGlvcmVhZDMyYmUoKHUzMiBfX2lvbWVtICopd3NfYmFzZSArIGkpO1xuIgogICAgIlx0XHRpZiAodilcbiIKICAgICJcdFx0XHRzZXFfcHJpbnRmKHMsIFwiIFslMDJkXT0lMDh4XCIsIGksIHYpO1xuIgogICAgIlx0fVxuIgogICAgIlx0c2VxX3B1dHMocywgXCJcXG5cIik7XG4iCiAgICAiXHRtdXRleF91bmxvY2soJnBjZC0+ZmVfbG9jayk7XG4iCiAgICAiXHRyZXR1cm4gMDtcbiIKICAgICJ9XG4iCiAgICAiXG4iCiAgICAic3RhdGljIGludCBmbWFuX3BjZF9mZV9wcm9iZV9vcGVuKHN0cnVjdCBpbm9kZSAqaW5vZGUsIHN0cnVjdCBmaWxlICpmaWxlKVxuIgogICAgIntcbiIKICAgICJcdHJldHVybiBzaW5nbGVfb3BlbihmaWxlLCBmbWFuX3BjZF9mZV9wcm9iZV9zaG93LCBpbm9kZS0+aV9wcml2YXRlKTtcbiIKICAgICJ9XG4iCiAgICAiXG4iCiAgICAic3RhdGljIGNvbnN0IHN0cnVjdCBmaWxlX29wZXJhdGlvbnMgZm1hbl9wY2RfZmVfcHJvYmVfZm9wcyA9IHtcbiIKICAgICJcdC5vd25lclx0XHQ9IFRISVNfTU9EVUxFLFxuIgogICAgIlx0Lm9wZW5cdFx0PSBmbWFuX3BjZF9mZV9wcm9iZV9vcGVuLFxuIgogICAgIlx0LnJlYWRcdFx0PSBzZXFfcmVhZCxcbiIKICAgICJcdC5sbHNlZWtcdFx0PSBzZXFfbHNlZWssXG4iCiAgICAiXHQucmVsZWFzZVx0PSBzaW5nbGVfcmVsZWFzZSxcbiIKICAgICJ9O1xuXG4iCikKCmlmICJmbWFuX3BjZF9mZV9wcm9iZV9zaG93IiBub3QgaW4gc3JjOgogICAgaWYgb2xkX3Byb2JlX2FuY2hvciBpbiBzcmM6CiAgICAgICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkX3Byb2JlX2FuY2hvciwgbmV3X3Byb2JlX2NvZGUgKyBvbGRfcHJvYmVfYW5jaG9yKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcHJvYmVfc2hvdyB2NiBpbnNlcnRlZCAoNjQgd29yZHMsIG5vbi16ZXJvIGZpbHRlcikiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIFdBUk5JTkc6IHByb2JlIGFuY2hvciBub3QgZm91bmQiKQplbHNlOgogICAgIyBBbHJlYWR5IHByZXNlbnQgLSB1cGdyYWRlIHRvIHY2OiBleHBhbmQgbG9vcCArIG5vbi16ZXJvIGZpbHRlcgogICAgb2xkX2xvb3AgPSAiZm9yIChpID0gMDsgaSA8IDg7IGkrKykgeyIKICAgIG5ld19sb29wID0gImZvciAoaSA9IDA7IGkgPCA2NDsgaSsrKSB7IgogICAgaWYgb2xkX2xvb3AgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKG9sZF9sb29wLCBuZXdfbG9vcCkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGxvb3AgZXhwYW5kZWQgOC0+NjQgd29yZHMiKQogICAgIyBBZGQgbm9uLXplcm8gZmlsdGVyCiAgICBvbGRfcHJpbnQgPSAnc2VxX3ByaW50ZihzLCAiIFslMDJkXT0lMDh4IiwgaSwgdik7JwogICAgbmV3X3ByaW50ID0gJ2lmICh2KVxuXHRcdFx0c2VxX3ByaW50ZihzLCAiIFslMDJkXT0lMDh4IiwgaSwgdik7JwogICAgaWYgb2xkX3ByaW50IGluIHNyYyBhbmQgImlmICh2KSIgbm90IGluIHNyYzoKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShvbGRfcHJpbnQsIG5ld19wcmludCkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIG5vbi16ZXJvIGZpbHRlciBhZGRlZCIpCgojIC0tLS0gNC4gUmVnaXN0ZXIgZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiIC4uLikgLS0tLQppZiAnZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiJyBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlIGRlYnVnZnMgYWxyZWFkeSByZWdpc3RlcmVkIikKZWxzZToKICAgIGRiZ19hbmNob3IgPSAnZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfaGFzaGZlIicKICAgIGlmIGRiZ19hbmNob3IgaW4gc3JjOgogICAgICAgIHByb2JlX2RiZyA9ICgKICAgICAgICAgICAgJ1x0XHRcdGRlYnVnZnNfY3JlYXRlX2ZpbGUoImZlX3Byb2JlIiwgMDQ0NCxcbicKICAgICAgICAgICAgJ1x0XHRcdFx0XHQgICAgcGNkLT5kZWJ1Z2ZzX2RpciwgcGNkLFxuJwogICAgICAgICAgICAnXHRcdFx0XHRcdCAgICAmZm1hbl9wY2RfZmVfcHJvYmVfZm9wcyk7XG4nCiAgICAgICAgICAgICdcdFx0XHQnICsgZGJnX2FuY2hvcgogICAgICAgICkKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShkYmdfYW5jaG9yLCBwcm9iZV9kYmcpCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBmZV9wcm9iZSBkZWJ1Z2ZzIHJlZ2lzdGVyZWQiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIFdBUk5JTkc6IGRlYnVnZnMgYW5jaG9yIG5vdCBmb3VuZCIpCgppZiBjaGFuZ2VzID09IDA6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIHY2OiBubyBjaGFuZ2VzIChhbGwgYWxyZWFkeSBhcHBsaWVkKSIpCmVsc2U6CiAgICB3aXRoIG9wZW4ocGF0aCwgInciKSBhcyBmOgogICAgICAgIGYud3JpdGUoc3JjKQogICAgcHJpbnQoZiIjIyMgZm1hbl9wY2QuYzogRi0wNjEgdjY6IHtjaGFuZ2VzfSBjaGFuZ2UocykgYXBwbGllZCIpCg==' | base64 -d | python3
    echo "### fman_port.c: M2-4 NULL-page clear support added"
fi

# F-044: Remove CCBS scaffold override of fe_enter_off in fe_arm_engage().
# The CCBS scaffold (patch 0132/0150) allocates a group table and overwrites
# fe_enter_off = gro, which redirects ALL frames to FQ 0x200 through the
# group table, completely bypassing the FE-VM ehash.  Remove the override so
# fe_enter_off retains the ehash hash-table root, allowing flow lookups to
# reach the stored keys.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/\t\t\t\tfe_enter_off = gro;/\t\t\t\t\/\* F-044: keep ehash root, skip CCBS scaffold override \*\/ \/\* fe_enter_off = gro; \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-044 CCBS scaffold bypass removed (keep ehash root)"
fi

# F-046 REVERTED per fman-keygen-flow-key-spec.md v2.0 §5.4:
# FMAN_AD_FE_ENTER_ALLOCATE (0x00800000) was set during the only confirmed
# HIT in program history (2026-07-04).  No sed needed: the original patch
# code already has the correct value.  word0 = 0x40800000.

# F-047-R2: Precise CCBS scaffold strip from 0132 patch (Python-based).
# Original sed ended at the first '+}' (line 98: inner if block), leaving
# orphaned braces at lines 99-100 that corrupt the fe_arm function structure.
# Fix: Python strips from '+\/\* 0150: CCBS scaffold' to exactly '+\t}'
# (the single-tab scaffold closing brace at line 100), preserving the
# engage code after the scaffold.
PATCH_0132="vyos-build/scripts/package-build/linux-kernel/patches/kernel/0132-fman-pcd-fe-arm-debugfs.patch"
if [ -f "$PATCH_0132" ]; then
    python3 -c "
import re, sys
with open('$PATCH_0132') as f:
    lines = f.readlines()
in_scaffold = False
out = []
brace_re = re.compile(r'^\+\t\}\s*$')
for line in lines:
    if re.match(r'\+\s*/\* 0150: CCBS scaffold', line):
        in_scaffold = True
        continue
    if in_scaffold and brace_re.match(line):
        in_scaffold = False
        continue
    if in_scaffold and line.startswith('+'):
        continue
    out.append(line)
with open('$PATCH_0132', 'w') as f:
    f.writelines(out)
" && echo "### 0132.patch: F-047-R2 CCBS scaffold precisely stripped"
fi

# F-050: Allow mask=0 in fe_ehash set for single-bucket E-EKFC-1 experiment.
# Patch 0125 rejects mask==0 ("if (mask == 0 || mask > ...) return -EINVAL").
# With mask=0, both software CRC64 and silicon hash AND to bucket 0, so flow
# HIT depends ONLY on key content, not on hash algorithm agreement (kgse_hc).
# This is the isolation experiment defined in specs/ekfc-5tuple-upgrade-spec.md
# section 6.1.  Remove the mask==0 check, keep the upper-bound check.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/if (mask == 0 || mask > FMAN_EHASH_MASK_MAX)/if (mask > FMAN_EHASH_MASK_MAX)  \/\* F-050: allow mask=0 for single-bucket E-EKFC-1 \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-050 mask=0 allowed (E-EKFC-1 single-bucket isolation)"
fi

# F-051: Force-clear kgse_bmch, kgse_bmcl, kgse_hc, and kgse_ekdv to zero
# inside keygen_scheme_setup() AFTER the scheme_regs struct is populated but
# BEFORE it's written to hardware.  The DPAA1 RSS driver may leave byte masks
# or hash config that interfere with exact-match ehash.  Anchored on the
# '/* Write scheme registers */' comment that precedes the write call.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    sed -i '/\/\* Write scheme registers \*\//i\
	/* F-051: force-clear RSS mask/hash config for exact-match ehash */\
	scheme_regs.kgse_bmch = 0;\
	scheme_regs.kgse_bmcl = 0;\
	scheme_regs.kgse_hc   = 0;\
	scheme_regs.kgse_ekdv = 0;' \
        drivers/net/ethernet/freescale/fman/fman_keygen.c
    echo "### fman_keygen.c: F-051 BM/HC/EKDV zeroed (RSS isolation)"
fi

# F-052: Suppress -Werror=unused-function for fman_pcd_debugfs_root_get.
# This static helper is defined in patch 0092/0126 but not called from any
# currently-enabled code path.  -Werror promotes the warning to error.
# Mark it with __attribute__((unused)) to silence the build.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static int fman_pcd_debugfs_root_get(void)/static __attribute__((unused)) int fman_pcd_debugfs_root_get(void)/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-052 debugfs_root_get marked __unused"
fi

# F-052b: Suppress -Werror for fman_pcd_debugfs_root_put (same root cause).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static void fman_pcd_debugfs_root_put(void)/static __attribute__((unused)) void fman_pcd_debugfs_root_put(void)/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-052b debugfs_root_put marked __unused"
fi

# F-053: Fix hash_bytes_offset in en_exthash_node descriptor ad[0] encoding.
# The DDR flow record (en_ehash_entry) has an 8-byte link-chain header (flags +
# next_entry pointer) before the key data at FMAN_EHASH_FLOW_KEY_OFF=8.  The
# hardware descriptor field hash_bytes_offset (bits 17:16 of ad[0]) was being
# written with t->hash_shift (0), telling the hardware to start key comparison
# at byte 0 of the DDR record — comparing against the link header (all zeros
# for the first flow) + partial key, which NEVER matches the KG-extracted bytes.
# The correct value for an 8-byte header is 1 (the field encodes 0→0B, 1→8B).
# The CRC64 bucket-indexer's hash_shift is a separate parameter and is unchanged.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/((u32)(t->hash_shift \& 0x3) << 16)/((u32)(1) << 16)  \/\* F-053: hash_bytes_offset=1 (8B header before key) \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-053 hash_bytes_offset=1 (key at offset 8 in DDR record)"
fi

# F-054: Fix context_build overwriting FE Action Descriptors.
# fman_pcd_fe_build_contexts() calls fman_pcd_fe_context_build(fe, offset, &p)
# where fe is the AD base address and offset is 0 for MUX.  context_build
# writes at fe+offset = fe+0 — the MUX AD type header (0x04000000) gets
# replaced with enq->muram_off.  The hardware reads a garbage FE type and
# crashes when HIT fires and tries to follow the next-FE pointer.
#
# Fix: replace context_build for MUX and Transition with direct AD writes.
# MUX AD word 0 becomes FMAN_FE_TYPE_MUX|enq_off (type+next-FE in one word).
# Transition AD word 1 becomes the exit FE offset (correct 2-word layout).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgRml4IDE6IE1VWCAtIHJlcGxhY2UgdGhlIGNvbnRleHRfYnVpbGQgY2FsbCB3aXRoIGRpcmVjdCBBRCB3cml0ZQojIFRhcmdldDogZm1hbl9wY2RfZmVfY29udGV4dF9idWlsZChmZSwgRk1BTl9GRV9NVVhfQ1RYX09GRiwgJnApOwpvbGRfbXV4X2NhbGwgPSAiZm1hbl9wY2RfZmVfY29udGV4dF9idWlsZChmZSwgRk1BTl9GRV9NVVhfQ1RYX09GRiwgJnApOyIKbmV3X211eF9jYWxsID0gImlvd3JpdGUzMmJlKEZNQU5fRkVfVFlQRV9NVVggfCAodTMyKWVucS0+bXVyYW1fb2ZmLCBmZSk7IC8qIEYtMDU0OiBkaXJlY3QgQUQgd3JpdGUsIG5vdCBjb250ZXh0X2J1aWxkICovIgppZiBvbGRfbXV4X2NhbGwgaW4gc3JjOgogICAgIyBBbHNvIHJlbW92ZSB0aGUgbGluZXMgdGhhdCBzZXQgdXAgdGhlIGNvbnRleHQgcGFyYW1zIGZvciBNVVgKICAgICMgKG1lbXNldCwgcC50eXBlLCBwLnUubXV4Lm5leHRfZmVfb2ZmKSBzaW5jZSB0aGV5J3JlIG5vIGxvbmdlciB1c2VkCiAgICBvbGRfYmxvY2sgPSAoCiAgICAgICAgIlx0XHRtZW1zZXQoJnAsIDAsIHNpemVvZihwKSk7XG4iCiAgICAgICAgIlx0XHRwLnR5cGUgPSBGTUFOX0ZFX1RZUEVfTVVYO1xuIgogICAgICAgICJcdFx0cC51Lm11eC5uZXh0X2ZlX29mZiA9IGVucS0+bXVyYW1fb2ZmO1xuIgogICAgICAgICJcdFx0Zm1hbl9wY2RfZmVfY29udGV4dF9idWlsZChmZSwgRk1BTl9GRV9NVVhfQ1RYX09GRiwgJnApOyIKICAgICkKICAgIG5ld19ibG9jayA9ICgKICAgICAgICAiXHRcdC8qIEYtMDU0OiBNVVggQUQgd29yZCAwID0gdHlwZXxuZXh0LUZFLiBjb250ZXh0X2J1aWxkIHdyb3RlIGF0XG4iCiAgICAgICAgIlx0XHQgKiBBRCswIG92ZXJ3cml0aW5nIHRoZSB0eXBlIGhlYWRlciwgY3Jhc2hpbmcgaGFyZHdhcmUuICovXG4iCiAgICAgICAgIlx0XHRpb3dyaXRlMzJiZShGTUFOX0ZFX1RZUEVfTVVYIHwgKHUzMillbnEtPm11cmFtX29mZiwgZmUpOyIKICAgICkKICAgIGlmIG9sZF9ibG9jayBpbiBzcmM6CiAgICAgICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkX2Jsb2NrLCBuZXdfYmxvY2spCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA1NCBNVVggQUQgZGlyZWN0IHdyaXRlIChibG9jayByZXBsYWNlKSIpCiAgICBlbHNlOgogICAgICAgICMgRmFsbGJhY2s6IGp1c3QgcmVwbGFjZSB0aGUgc2luZ2xlIGxpbmUKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShvbGRfbXV4X2NhbGwsIG5ld19tdXhfY2FsbCkKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU0IE1VWCBBRCBkaXJlY3Qgd3JpdGUgKGxpbmUgcmVwbGFjZSkiKQoKIyBGaXggMjogVHJhbnNpdGlvbiAtIHJlcGxhY2UgY29udGV4dF9idWlsZCB3aXRoIGRpcmVjdCBBRCB3b3JkIDEgd3JpdGUKb2xkX3RyYW5zX2NhbGwgPSAiZm1hbl9wY2RfZmVfY29udGV4dF9idWlsZChmZSwgRk1BTl9GRV9UUkFOU0lUSU9OX0NUWF9PRkYsICZwKTsiCm5ld190cmFuc19jYWxsID0gImlvd3JpdGUzMmJlKCh1MzIpcGNkLT5mZV9leGl0X29mZiwgKHUzMiBfX2lvbWVtICopZmUgKyAxKTsgLyogRi0wNTQ6IGRpcmVjdCBBRCB3b3JkIDEgd3JpdGUgKi8iCmlmIG9sZF90cmFuc19jYWxsIGluIHNyYzoKICAgIG9sZF90YmxvY2sgPSAoCiAgICAgICAgIlx0XHRtZW1zZXQoJnAsIDAsIHNpemVvZihwKSk7XG4iCiAgICAgICAgIlx0XHRwLnR5cGUgPSBGTUFOX0ZFX1RZUEVfVFJBTlNJVElPTjtcbiIKICAgICAgICAiXHRcdHAudS50cmFuc2l0aW9uLm5leHRfYWRfb2ZmID0gcGNkLT5mZV9leGl0X29mZjtcbiIKICAgICAgICAiXHRcdGZtYW5fcGNkX2ZlX2NvbnRleHRfYnVpbGQoZmUsIEZNQU5fRkVfVFJBTlNJVElPTl9DVFhfT0ZGLCAmcCk7IgogICAgKQogICAgbmV3X3RibG9jayA9ICgKICAgICAgICAiXHRcdC8qIEYtMDU0OiBUcmFuc2l0aW9uIEFEIHdvcmQgMSA9IG5leHQtQUQgb2Zmc2V0LlxuIgogICAgICAgICJcdFx0ICogU2FtZSBjb250ZXh0X2J1aWxkIGNvcnJ1cHRpb24gYnVnIGFzIE1VWC4gKi9cbiIKICAgICAgICAiXHRcdGlvd3JpdGUzMmJlKCh1MzIpcGNkLT5mZV9leGl0X29mZiwgKHUzMiBfX2lvbWVtICopZmUgKyAxKTsiCiAgICApCiAgICBpZiBvbGRfdGJsb2NrIGluIHNyYzoKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShvbGRfdGJsb2NrLCBuZXdfdGJsb2NrKQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNTQgVHJhbnNpdGlvbiBBRCBkaXJlY3Qgd3JpdGUgKGJsb2NrKSIpCiAgICBlbHNlOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKG9sZF90cmFuc19jYWxsLCBuZXdfdHJhbnNfY2FsbCkKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU0IFRyYW5zaXRpb24gQUQgZGlyZWN0IHdyaXRlIChsaW5lKSIpCgp3aXRoIG9wZW4ocGF0aCwgInciKSBhcyBmOgogICAgZi53cml0ZShzcmMpCg==' | base64 -d | python3
    echo "### fman_pcd.c: F-054 MUX/Transition AD direct writes (fix context_build corruption)"
fi

# F-056: MUX/Transition AD writes in fe_arm_engage (SDK-compliant — raw MURAM offsets).
# The 0146 patch tried to add fman_pcd_fe_build_contexts() call into
# fe_arm_engage, but F-047 context drift caused the call-insertion hunk to
# fail.  The build_contexts function was defined but never called (optimized
# away by GCC).  This fix inserts the MUX and Transition AD writes DIRECTLY
# into fe_arm_engage, right before the "ENGAGED" pr_info, bypassing the
# missing call site entirely.
#
# F-056: MUX AD word 0 = enq->muram_off (raw offset, no type byte — SDK-compliant)
# Transition AD word 1 = pcd->fe_exit_off (chains to EXIT for MISS handling)
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgRmluZCB0aGUgcHJfaW5mbyBsaW5lIGluIGZlX2FybV9lbmdhZ2UKbWFya2VyID0gJ1x0cHJfaW5mbygiZm1hbl9wY2QgZmVfYXJtOiBwb3J0IDB4JTAyeCBFTkdBR0VEIEZFX0VOVEVSPTB4JWx4IChBQ19DQylcXG4iLCcKaWYgbWFya2VyIG5vdCBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU1IG1hcmtlciBub3QgZm91bmQiKQplbHNlOgogICAgbmV3X2NvZGUgPSAoCiAgICAgICAgJ1x0LyogRi0wNTU6IE1VWC9UcmFuc2l0aW9uIEFEIHdyaXRlcy4gIFRoZSAwMTQ2IGNvbnRleHRfYnVpbGQgY2FsbFxuJwogICAgICAgICdcdCAqIGZhaWxlZCB0byBhcHBseSAoRi0wNDcgY29udGV4dCBkcmlmdCkuICBXcml0ZSB0aGUgTVVYIGNoYWluXG4nCiAgICAgICAgJ1x0ICogZGVzdGluYXRpb24gYW5kIFRyYW5zaXRpb24gQUQgd29yZCAxIGRpcmVjdGx5LiAqL1xuJwogICAgICAgICdcdHtcbicKICAgICAgICAnXHRcdHN0cnVjdCBtdXJhbV9pbmZvICptdXJhbSA9IGZtYW5fZ2V0X211cmFtKHBjZC0+Zm1hbik7XG4nCiAgICAgICAgJ1x0XHRpZiAobXVyYW0pIHtcbicKICAgICAgICAnXHRcdFx0c3RydWN0IGZtYW5fcGNkX2ZlX29iaiAqZW5xO1xuJwogICAgICAgICdcdFx0XHRlbnEgPSBsaXN0X2ZpcnN0X2VudHJ5X29yX251bGwoJnBjZC0+ZmVfZW5xLFxuJwogICAgICAgICdcdFx0XHRcdHN0cnVjdCBmbWFuX3BjZF9mZV9vYmosIG5vZGUpO1xuJwogICAgICAgICdcdFx0XHRpZiAoZW5xICYmIHBjZC0+ZmVfbXV4X29mZikge1xuJwogICAgICAgICdcdFx0XHRcdHZvaWQgX19pb21lbSAqbXV4ID1cbicKICAgICAgICAnXHRcdFx0XHRcdGZtYW5fbXVyYW1fb2Zmc2V0X3RvX3ZiYXNlKG11cmFtLFxuJwogICAgICAgICdcdFx0XHRcdFx0XHRwY2QtPmZlX211eF9vZmYpO1xuJwogICAgICAgICdcdFx0XHRcdGlvd3JpdGUzMmJlKCh1MzIpZW5xLT5tdXJhbV9vZmYsXG4nCiAgICAgICAgJ1x0XHRcdFx0XHRtdXgpO1xuJwogICAgICAgICdcdFx0XHR9XG4nCiAgICAgICAgJ1x0XHRcdGlmIChwY2QtPmZlX3RyYW5zaXRpb25fb2ZmICYmIHBjZC0+ZmVfZXhpdF9vZmYpIHtcbicKICAgICAgICAnXHRcdFx0XHR2b2lkIF9faW9tZW0gKnRyYW5zID1cbicKICAgICAgICAnXHRcdFx0XHRcdGZtYW5fbXVyYW1fb2Zmc2V0X3RvX3ZiYXNlKG11cmFtLFxuJwogICAgICAgICdcdFx0XHRcdFx0XHRwY2QtPmZlX3RyYW5zaXRpb25fb2ZmKTtcbicKICAgICAgICAnXHRcdFx0XHRpb3dyaXRlMzJiZSgodTMyKXBjZC0+ZmVfZXhpdF9vZmYsXG4nCiAgICAgICAgJ1x0XHRcdFx0XHQodTMyIF9faW9tZW0gKil0cmFucyArIDEpO1xuJwogICAgICAgICdcdFx0XHR9XG4nCiAgICAgICAgJ1x0XHR9XG4nCiAgICAgICAgJ1x0fVxuJwogICAgICAgICdcbicKICAgICAgICAnXHRwcl9pbmZvKCJmbWFuX3BjZCBmZV9hcm06IHBvcnQgMHglMDJ4IEVOR0FHRUQgRkVfRU5URVI9MHglbHggKEFDX0NDKVxcbiIsJwogICAgKQogICAgc3JjID0gc3JjLnJlcGxhY2UobWFya2VyLCBuZXdfY29kZSwgMSkKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNTUgTVVYL1RyYW5zaXRpb24gQUQgd3JpdGVzIGluc2VydGVkIikKCndpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICBmLndyaXRlKHNyYykK' | base64 -d | python3
    echo "### fman_pcd.c: F-058 MUX/Transition/ENQ AD writes in fe_arm_engage (SDK raw offsets)"
fi

# F-057: Remove per-record next-FE from DDR flow records.
# The NXP SDK's en_ehash_entry struct has NO per-record next-FE pointer.
# The HIT dispatch target is in the hash FE descriptor's word 5 (nextFEPtr
# = MUX -> ENQ).  Our code was writing enq_off at byte 24 of each DDR
# record (8-byte header + 13-byte key + 3-byte pad = 24).  The hardware
# reads this as garbage and crashes.
#
# The enq_fe_off parameter becomes unused (kept for ABI compatibility).
# All HIT flows now dispatch through the hash FE's word 5, not per-record.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgUmVtb3ZlIHRoZSBwZXItcmVjb3JkIG5leHQtRkUgcG9pbnRlciB3cml0ZSAobGluZXMgMjA0LTIwNiBvZiAwMTI4KS4KIyBUaGUgU0RLJ3MgZW5fZWhhc2hfZW50cnkgaGFzIE5PIHBlci1yZWNvcmQgbmV4dC1GRSAtLSB0aGUgSElUIGRpc3BhdGNoCiMgdGFyZ2V0IGlzIGluIHRoZSBoYXNoIEZFIGRlc2NyaXB0b3IncyB3b3JkIDUgKG5leHRGRVB0ciA9IE1VWCAtPiBFTlEpLgojIE91ciBleHRyYSB3cml0ZSBhdCBvZmZzZXQgMjQgY29ycnVwdHMgdGhlIEREUiByZWNvcmQsIGNhdXNpbmcgdGhlCiMgaGFyZHdhcmUgdG8gcmVhZCBnYXJiYWdlIGFuZCBjcmFzaC4KCm9sZF9jb2RlID0gKAogICAgJ1x0LyogbmV4dC1GRSBwb2ludGVyIChFTlEgRkUgTVVSQU0gb2Zmc2V0KSBhZnRlciB0aGUgOC1ieXRlLWFsaWduZWQga2V5LiAqL1xuJwogICAgJ1x0ZmVfcHRyX29mZiA9IEZNQU5fRUhBU0hfRkxPV19LRVlfT0ZGICsgKChrZXlfc2l6ZSArIDdVKSAmIH43VSk7XG4nCiAgICAnXHQqKF9fYmUzMiAqKShyICsgZmVfcHRyX29mZikgPSBjcHVfdG9fYmUzMigodTMyKWVucV9mZV9vZmYpO1xuJwopCmlmIG9sZF9jb2RlIG5vdCBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU3IG5leHQtRkUgcGF0dGVybiBub3QgZm91bmQiKQplbHNlOgogICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkX2NvZGUsICcnKQogICAgIyBBbHNvIGNsZWFuIHVwIHRoZSB1bnVzZWQgZmVfcHRyX29mZiB2YXJpYWJsZQogICAgc3JjID0gc3JjLnJlcGxhY2UoJ3NpemVfdCBmZV9wdHJfb2ZmO1xuXG4nLCAnJykKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNTcgcmVtb3ZlZCBwZXItcmVjb3JkIG5leHQtRkUgZnJvbSBERFIgKFNESy1jb21wbGlhbnQpIikKCndpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICBmLndyaXRlKHNyYykK' | base64 -d | python3
    echo "### fman_pcd.c: F-057 removed per-record next-FE from DDR (SDK-compliant)"
fi

# F-058: Write ENQ AD word 2 (enqueue context) to fix ENQ crash.
# With ws_offset=0, the ENQ working store IS the AD.  Word 2 (offset 8)
# is the enqueue context — the hardware reads it for enqueue parameters.
# Currently zero, causing the ENQ FE to crash on dispatch.
# Write the FQID (0x200) to word 2, matching the SDK's context write:
#   WRITE_UINT32(*tmp, (rspid << 24) | fqid)  = 0x00000200
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgQW5jaG9yIG9uIHRoZSBNVVggd3JpdGUgbGluZSAodW5pcXVlLCBmcm9tIEYtMDU2KQptYXJrZXIgPSAiaW93cml0ZTMyYmUoKHUzMillbnEtPm11cmFtX29mZiwiCmlmIG1hcmtlciBub3QgaW4gc3JjOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA1OCBtYXJrZXIgbm90IGZvdW5kIChGLTA1NiBtYXkgbm90IGhhdmUgYXBwbGllZCkiKQplbHNlOgogICAgIyBGaW5kIHRoZSBjbG9zaW5nIH0gb2YgdGhlIE1VWCBpZiBibG9jayAtIGl0IGNvbWVzIHJpZ2h0IGFmdGVyICJtdXgpO1xuXHRcdFx0fSIKICAgICMgV2UgbG9vayBmb3IgdGhlICdcdFx0XHRtdXgpOycgbGluZSBhbmQgaW5zZXJ0IGFmdGVyIHRoZSBjbG9zaW5nICdcdFx0XHR9JwogICAgaWR4ID0gc3JjLmZpbmQobWFya2VyKQogICAgIyBGaW5kIHRoZSBjbG9zaW5nIH0gb2YgdGhpcyBibG9jazogYWZ0ZXIgIm11eCk7IiB0aGVyZSdzICJcblx0XHRcdH0iIAogICAgIyBUaGUgTVVYIGJsb2NrIGVuZHMgc29tZXdoZXJlIGFmdGVyIHRoZSBtYXJrZXIKICAgIGJsb2NrX2VuZCA9IHNyYy5maW5kKCdcdFx0XHR9Jywgc3JjLmZpbmQoJ211eCk7JywgaWR4KSkKICAgIGlmIGJsb2NrX2VuZCA+IDA6CiAgICAgICAgIyBGaW5kIGVuZCBvZiBsaW5lIGZvciB0aGUgY2xvc2luZyB9CiAgICAgICAgZW9sID0gc3JjLmZpbmQoJ1xuJywgYmxvY2tfZW5kKQogICAgICAgIGlmIGVvbCA+IDA6CiAgICAgICAgICAgICMgSW5zZXJ0IEVOUSB3b3JkIDIgd3JpdGUgYWZ0ZXIgdGhlIE1VWCBibG9jayBjbG9zaW5nIH0KICAgICAgICAgICAgaW5zZXJ0ID0gKAogICAgICAgICAgICAgICAgJ1x0XHRcdC8qIEYtMDU4OiBFTlEgQUQgd29yZCAyID0gRlFJRCBjb250ZXh0LlxuJwogICAgICAgICAgICAgICAgJ1x0XHRcdCAqIHdzX29mZnNldD0wIG1lYW5zIHdvcmtpbmcgc3RvcmUgSVMgdGhlIEFEO1xuJwogICAgICAgICAgICAgICAgJ1x0XHRcdCAqIHdvcmQgMiBpcyB0aGUgZW5xdWV1ZSBjb250ZXh0LiAqL1xuJwogICAgICAgICAgICAgICAgJ1x0XHRcdGlmIChlbnEgJiYgZW5xLT5tdXJhbV9vZmYpIHtcbicKICAgICAgICAgICAgICAgICdcdFx0XHRcdHZvaWQgX19pb21lbSAqZXEgPVxuJwogICAgICAgICAgICAgICAgJ1x0XHRcdFx0XHRmbWFuX211cmFtX29mZnNldF90b192YmFzZShtdXJhbSxcbicKICAgICAgICAgICAgICAgICdcdFx0XHRcdFx0XHRlbnEtPm11cmFtX29mZik7XG4nCiAgICAgICAgICAgICAgICAnXHRcdFx0XHRpb3dyaXRlMzJiZSgweDAwMDAwMjAwLCAodTMyIF9faW9tZW0gKillcSArIDIpO1xuJwogICAgICAgICAgICAgICAgJ1x0XHRcdH1cbicKICAgICAgICAgICAgKQogICAgICAgICAgICBzcmMgPSBzcmNbOmVvbCsxXSArIGluc2VydCArIHNyY1tlb2wrMTpdCiAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNTggRU5RIEFEIHdvcmQgMiAoRlFJRCkgd3JpdHRlbiBhZnRlciBNVVggYmxvY2siKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU4IGNvdWxkIG5vdCBmaW5kIE1VWCBibG9jayBlbmQiKQoKd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjoKICAgIGYud3JpdGUoc3JjKQo=' | base64 -d | python3
    echo "### fman_pcd.c: F-058 ENQ AD word 2 (FQID context) written"
fi

# F-059: Route HIT to EXIT (overwrite hash FE word 5 = exit_off).
# Isolates the ehash comparison from the MUX->ENQ->QMan dispatch chain.
# If HIT->EXIT survives with matching traffic, the ehash comparison works
# and only the ENQ/FQ path remains broken.
#
# Overwrites hash_fe word 5 (HIT nextFEPtr) with exit_off (EXIT), making
# HIT behave identically to MISS — deallocate, no QMan interaction.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgRmluZCB0aGUgTVVYIHdyaXRlIGxpbmUgKEYtMDU2IGFuY2hvcikgYW5kIGFkZCBGLTA1OSBhZnRlcgptYXJrZXIgPSAiaW93cml0ZTMyYmUoKHUzMillbnEtPm11cmFtX29mZiwiCmlmIG1hcmtlciBub3QgaW4gc3JjOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA1OSBtYXJrZXIgbm90IGZvdW5kIikKZWxzZToKICAgICMgRmluZCB0aGUgRU5RIHdyaXRlIGxpbmUgZnJvbSBGLTA1OAogICAgZW5xX21hcmtlciA9ICJpb3dyaXRlMzJiZSgweDAwMDAwMjAwIgogICAgaWYgZW5xX21hcmtlciBpbiBzcmM6CiAgICAgICAgIyBGLTA1OCBpcyBwcmVzZW50OyBmaW5kIGl0cyBjbG9zaW5nIH0KICAgICAgICBlbnFfaWR4ID0gc3JjLmZpbmQoZW5xX21hcmtlcikKICAgICAgICBlbnFfZW5kID0gc3JjLmZpbmQoJ1x0XHRcdFx0fScsIHNyYy5maW5kKCdlcSArIDIpOycsIGVucV9pZHgpKQogICAgZWxzZToKICAgICAgICBlbnFfZW5kID0gLTEKCiAgICAjIEluc2VydCBhZnRlciB0aGUgZW5kIG9mIHRoZSBNVVgvRU5RIHdyaXRlIGJsb2NrCiAgICAjICh3aGljaGV2ZXIgY2xvc2luZyB9IGNvbWVzIGxhc3QpCiAgICBtdXhfZW5kID0gc3JjLmZpbmQoJ1x0XHRcdH0nLCBzcmMuZmluZCgnbXV4KTsnLCBzcmMuZmluZChtYXJrZXIpKSkKICAgIHVzZV9lbmQgPSBlbnFfZW5kID4gbXV4X2VuZCBhbmQgZW5xX2VuZCBvciBtdXhfZW5kCiAgICBpZiB1c2VfZW5kID4gMDoKICAgICAgICBlb2wgPSBzcmMuZmluZCgnXG4nLCB1c2VfZW5kKQogICAgICAgIGlmIGVvbCA+IDA6CiAgICAgICAgICAgIGluc2VydCA9ICgKICAgICAgICAgICAgICAgICdcdFx0XHQvKiBGLTA1OTogcm91dGUgSElUIHRvIEVYSVQgZm9yIGVoYXNoIGNvbXBhcmlzb24gaXNvbGF0aW9uLlxuJwogICAgICAgICAgICAgICAgJ1x0XHRcdCAqIE92ZXJ3cml0ZSBoYXNoIEZFIHdvcmQgNSAoS0lUIG5leHRGRVB0cikgd2l0aCBleGl0X29mZi5cbiAnCiAgICAgICAgICAgICAgICAnXHRcdFx0ICogSWYgSElULUVYSVQgc3Vydml2ZXMgd2l0aCBtYXRjaGluZyB0cmFmZmljLCBlaGFzaFxuJwogICAgICAgICAgICAgICAgJ1x0XHRcdCAqIGNvbXBhcmlzb24gd29ya3MgYW5kIG9ubHkgRU5RL0ZRIHJlbWFpbnMgYnJva2VuLlxuJwogICAgICAgICAgICAgICAgJ1x0XHRcdCAqL1xuJwogICAgICAgICAgICAgICAgJ1x0XHRcdGlmIChwY2QtPmZlX2hhc2hfb2ZmICYmIHBjZC0+ZmVfZXhpdF9vZmYpIHtcbicKICAgICAgICAgICAgICAgICdcdFx0XHRcdHZvaWQgX19pb21lbSAqaGZlID1cbicKICAgICAgICAgICAgICAgICdcdFx0XHRcdFx0Zm1hbl9tdXJhbV9vZmZzZXRfdG9fdmJhc2UobXVyYW0sXG4nCiAgICAgICAgICAgICAgICAnXHRcdFx0XHRcdFx0cGNkLT5mZV9oYXNoX29mZik7XG4nCiAgICAgICAgICAgICAgICAnXHRcdFx0XHRpb3dyaXRlMzJiZSgodTMyKXBjZC0+ZmVfZXhpdF9vZmYsXG4nCiAgICAgICAgICAgICAgICAnXHRcdFx0XHRcdCh1MzIgX19pb21lbSAqKWhmZSArIDUpO1xuJwogICAgICAgICAgICAgICAgJ1x0XHRcdH1cbicKICAgICAgICAgICAgKQogICAgICAgICAgICBzcmMgPSBzcmNbOmVvbCsxXSArIGluc2VydCArIHNyY1tlb2wrMTpdCiAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNTkgaGFzaCBGRSB3b3JkIDUgcm91dGVkIHRvIEVYSVQiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDU5IGNvdWxkIG5vdCBmaW5kIGVuZCBvZiBibG9jayIpCgp3aXRoIG9wZW4ocGF0aCwgInciKSBhcyBmOgogICAgZi53cml0ZShzcmMpCg==' | base64 -d | python3
    echo "### fman_pcd.c: F-059 hash FE word 5 (HIT) routed to EXIT for isolation test"
fi

# F-060 v3d: Fix MUX context write target — write to AD+4 (word 1), not AD+0.
# v3d avoids backslash-s (bad escape through the 4-layer pipeline) — uses [ \t]* instead.
# F-055/F-056 wrote across TWO lines; regex matches the 2-line pattern.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHJlCgpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCiMgRi0wNjAgdjM6IEZpeCBNVVggY29udGV4dCB3cml0ZSB0YXJnZXQgZnJvbSBBRCswIHRvIEFEKzQgKHdvcmQgMSkKIyBGLTA1NS9GLTA1NiB3cm90ZSB0aGUgTVVYIHdyaXRlIGFjcm9zcyBUV08gbGluZXMuICBTZWFyY2ggZm9yIHRoZQojIHBhcnRpYWwgdW5pcXVlIG1hcmtlciB0aGVuIHVzZSByZWdleCB0byByZXBsYWNlIHRoZSAyLWxpbmUgYmxvY2suCiMgQVZPSUQgXHMgaW4gcmVnZXggKGJhZCBlc2NhcGUgdGhyb3VnaCB0aGUgNC1sYXllciBuZXN0aW5nIHBpcGVsaW5lKS4KCnRyaWdnZXIgPSAiKHUzMillbnEtPm11cmFtX29mZiwiCmlmIHRyaWdnZXIgaW4gc3JjOgogICAgIyBNYXRjaDogaW93cml0ZTMyYmUoKHUzMillbnEtPm11cmFtX29mZixcbjx3aGl0ZXNwYWNlPm11eCk7CiAgICAjIFJlcGxhY2Ugd2l0aCBzaW5nbGUtbGluZSB3cml0ZSB0byB3b3JkIDEgKEFEKzQpCiAgICBvbGRfcnggPSByZS5jb21waWxlKAogICAgICAgIHIiXHRcdFx0XHRpb3dyaXRlMzJiZVwoXCh1MzJcKWVucS0+bXVyYW1fb2ZmLFsgXHRdKlxuWyBcdF0qbXV4XCk7WyBcdF0qXG4iCiAgICApCiAgICByZXBsYWNlbWVudCA9ICJcdFx0XHRcdGlvd3JpdGUzMmJlKCh1MzIpZW5xLT5tdXJhbV9vZmYsICh1MzIgX19pb21lbSAqKW11eCArIDEpOyAvKiBGLTA2MDogU0RLLWNvbXBsaWFudCBNVVggY29udGV4dCBhdCBBRCs0ICovXG4iCiAgICBzcmMsIG4gPSBvbGRfcnguc3VibihyZXBsYWNlbWVudCwgc3JjLCBjb3VudD0xKQogICAgaWYgbiA+IDA6CiAgICAgICAgcHJpbnQoZiIjIyMgZm1hbl9wY2QuYzogRi0wNjAgdjM6IE1VWCB3cml0ZSBmaXhlZCB0byBBRCs0ICh7bn0gcmVwbGFjZW1lbnQpIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MCB2MzogcmVnZXggY29tcGlsZWQgYnV0IDAgbWF0Y2hlcyAoYWxyZWFkeSBhcHBsaWVkPykiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MCB2MzogdHJpZ2dlciBub3QgZm91bmQgKEYtMDU1L0YtMDU2IG5vdCBhcHBsaWVkPykiKQoKd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjoKICAgIGYud3JpdGUoc3JjKQo=' | base64 -d | python3
    echo "### fman_pcd.c: F-060 v3d: MUX context write fixed to AD+4"

# F-061: fe_probe debugfs — dump FE pool workspace to read KG-extracted key bytes.
# The FE_ENTER ALLOCATE allocates a workspace per-frame from the FE pool.
# After exit, gen_pool_free does NOT zero the MURAM, so the KG hash result
# and extracted key bytes remain readable.  This debugfs node reads the
# first 8 u32 words from the first pool slot, capturing exactly what the
# KG silicon produced for the last classified frame — the only reliable
# way to determine the EKFC extraction byte order on LS1046A silicon.
# Idempotent: checks for existing fe_pool_off / fe_probe_show / debugfs
# registration before inserting each piece.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgIHNyYyA9IGYucmVhZCgpCgpjaGFuZ2VzID0gMAoKIyAtLS0tIDEuIEFkZCBmZV9wb29sX29mZiB0byBzdHJ1Y3QgZm1hbl9wY2QgLS0tLQppZiAidW5zaWduZWQgbG9uZyBmZV9wb29sX29mZjsiIGluIHNyYzoKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcG9vbF9vZmYgZmllbGQgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIHN0cnVjdF9hbmNob3IgPSAiaW50IGZlX3JlZmNvdW50OyIKICAgIGlmIHN0cnVjdF9hbmNob3IgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKHN0cnVjdF9hbmNob3IsCiAgICAgICAgICAgIHN0cnVjdF9hbmNob3IgKyAiXG5cdHVuc2lnbmVkIGxvbmcgZmVfcG9vbF9vZmY7XHQvKiBGLTA2MTogZmlyc3QgRkUgcG9vbCBzbG90IE1VUkFNIG9mZnNldCBmb3IgZmVfcHJvYmUgKi8iLAogICAgICAgICAgICAxKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcG9vbF9vZmYgZmllbGQgYWRkZWQiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIFdBUk5JTkc6IHN0cnVjdCBhbmNob3IgJ2ludCBmZV9yZWZjb3VudDsnIG5vdCBmb3VuZCIpCgojIC0tLS0gMi4gU2F2ZSBmZV9wb29sX29mZiBkdXJpbmcgZmVfcG9vbF9hbGxvYyAtLS0tCmlmICJpZiAoIXBjZC0+ZmVfcG9vbF9vZmYpIiBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Bvb2xfb2ZmIHNhdmUgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIGFsbG9jX2FuY2hvciA9ICJcdFx0bGlzdF9hZGRfdGFpbCgmb2JqLT5ub2RlLCAmcGNkLT5mZV9hdmFpbGFibGUpOyIKICAgIGlmIGFsbG9jX2FuY2hvciBpbiBzcmM6CiAgICAgICAgc2F2ZV9ibG9jayA9IGFsbG9jX2FuY2hvciArICJcblx0XHRpZiAoIXBjZC0+ZmVfcG9vbF9vZmYpXHQvKiBGLTA2MTogc2F2ZSBmaXJzdCBzbG90IGZvciBmZV9wcm9iZSAqL1xuXHRcdFx0cGNkLT5mZV9wb29sX29mZiA9IG9mZjsiCiAgICAgICAgc3JjID0gc3JjLnJlcGxhY2UoYWxsb2NfYW5jaG9yLCBzYXZlX2Jsb2NrLCAxKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcG9vbF9vZmYgc2F2ZSBpbnNlcnRlZCIpCiAgICBlbHNlOgogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgV0FSTklORzogYWxsb2MgYW5jaG9yIG5vdCBmb3VuZCIpCgojIC0tLS0gMy4gQWRkIGZlX3Byb2JlX3Nob3cgZnVuY3Rpb24gYmVmb3JlIGZlX3BvcnRfc2hvdyAtLS0tCmlmICJmbWFuX3BjZF9mZV9wcm9iZV9zaG93IiBpbiBzcmM6CiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlX3Nob3cgYWxyZWFkeSBwcmVzZW50IikKZWxzZToKICAgIHByb2JlX2FuY2hvciA9ICJzdGF0aWMgaW50IGZtYW5fcGNkX2ZlX3BvcnRfc2hvdyhzdHJ1Y3Qgc2VxX2ZpbGUgKnMsIHZvaWQgKnVudXNlZCkiCiAgICBpZiBwcm9iZV9hbmNob3IgaW4gc3JjOgogICAgICAgIHByb2JlX2NvZGUgPSAoCiAgICAgICAgICAgICIvKiBGLTA2MTogZmVfcHJvYmUgZGVidWdmcyAtIGR1bXAgZmlyc3QgRkUgcG9vbCBzbG90IHRvIHJlYWRcbiIKICAgICAgICAgICAgIiAqIHRoZSBLRy1leHRyYWN0ZWQga2V5IGJ5dGVzIGZyb20gdGhlIEZFX0VOVEVSIHdvcmtzcGFjZS5cbiIKICAgICAgICAgICAgIiAqIFRoZSAyNDYtYnl0ZSB3b3Jrc3BhY2UgaXMgYWxsb2MnZCBwZXItZnJhbWUgYW5kIG5vdCB6ZXJvZWRcbiIKICAgICAgICAgICAgIiAqIG9uIGZyZWUsIHNvIGFmdGVyIGEgZnJhbWUgcGFzc2VzIHRocm91Z2gsIHRoZSBLRyBoYXNoIGFuZFxuIgogICAgICAgICAgICAiICogZXh0cmFjdGVkIGtleSBieXRlcyBhcmUgc3RpbGwgcmVhZGFibGUgaW4gTVVSQU0uXG4iCiAgICAgICAgICAgICIgKi9cbiIKICAgICAgICAgICAgInN0YXRpYyBpbnQgZm1hbl9wY2RfZmVfcHJvYmVfc2hvdyhzdHJ1Y3Qgc2VxX2ZpbGUgKnMsIHZvaWQgKnVudXNlZClcbiIKICAgICAgICAgICAgIntcbiIKICAgICAgICAgICAgIlx0c3RydWN0IGZtYW5fcGNkICpwY2QgPSBzLT5wcml2YXRlO1xuIgogICAgICAgICAgICAiXHRzdHJ1Y3QgbXVyYW1faW5mbyAqbXVyYW0gPSBmbWFuX2dldF9tdXJhbShwY2QtPmZtYW4pO1xuIgogICAgICAgICAgICAiXHR2b2lkIF9faW9tZW0gKndzX2Jhc2U7XG4iCiAgICAgICAgICAgICJcdHVuc2lnbmVkIGludCBpO1xuIgogICAgICAgICAgICAiXG4iCiAgICAgICAgICAgICJcdG11dGV4X2xvY2soJnBjZC0+ZmVfbG9jayk7XG4iCiAgICAgICAgICAgICJcdGlmICghbXVyYW0gfHwgcGNkLT5mZV9yZWZjb3VudCA9PSAwKSB7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3B1dHMocywgXCJmZSBwb29sIG5vdCBlbmdhZ2VkXFxuXCIpO1xuIgogICAgICAgICAgICAiXHRcdG11dGV4X3VubG9jaygmcGNkLT5mZV9sb2NrKTtcbiIKICAgICAgICAgICAgIlx0XHRyZXR1cm4gMDtcbiIKICAgICAgICAgICAgIlx0fVxuIgogICAgICAgICAgICAiXHRpZiAoIXBjZC0+ZmVfcG9vbF9vZmYpIHtcbiIKICAgICAgICAgICAgIlx0XHRzZXFfcHV0cyhzLCBcImZlIHBvb2wgbm90IGFsbG9jYXRlZFxcblwiKTtcbiIKICAgICAgICAgICAgIlx0XHRtdXRleF91bmxvY2soJnBjZC0+ZmVfbG9jayk7XG4iCiAgICAgICAgICAgICJcdFx0cmV0dXJuIDA7XG4iCiAgICAgICAgICAgICJcdH1cbiIKICAgICAgICAgICAgIlx0d3NfYmFzZSA9IGZtYW5fbXVyYW1fb2Zmc2V0X3RvX3ZiYXNlKG11cmFtLCBwY2QtPmZlX3Bvb2xfb2ZmKTtcbiIKICAgICAgICAgICAgIlx0c2VxX3ByaW50ZihzLCBcInBvb2w9MHglMDVseFxcblwiLCBwY2QtPmZlX3Bvb2xfb2ZmKTtcbiIKICAgICAgICAgICAgIlx0Zm9yIChpID0gMDsgaSA8IDg7IGkrKykge1xuIgogICAgICAgICAgICAiXHRcdHUzMiB2ID0gaW9yZWFkMzJiZSgodTMyIF9faW9tZW0gKil3c19iYXNlICsgaSk7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3ByaW50ZihzLCBcIiBbJTAyZF09JTA4eFwiLCBpLCB2KTtcbiIKICAgICAgICAgICAgIlx0fVxuIgogICAgICAgICAgICAiXHRzZXFfcHV0cyhzLCBcIlxcblwiKTtcbiIKICAgICAgICAgICAgIlx0bXV0ZXhfdW5sb2NrKCZwY2QtPmZlX2xvY2spO1xuIgogICAgICAgICAgICAiXHRyZXR1cm4gMDtcbiIKICAgICAgICAgICAgIn1cbiIKICAgICAgICAgICAgIlxuIgogICAgICAgICAgICAic3RhdGljIGludCBmbWFuX3BjZF9mZV9wcm9iZV9vcGVuKHN0cnVjdCBpbm9kZSAqaW5vZGUsIHN0cnVjdCBmaWxlICpmaWxlKVxuIgogICAgICAgICAgICAie1xuIgogICAgICAgICAgICAiXHRyZXR1cm4gc2luZ2xlX29wZW4oZmlsZSwgZm1hbl9wY2RfZmVfcHJvYmVfc2hvdywgaW5vZGUtPmlfcHJpdmF0ZSk7XG4iCiAgICAgICAgICAgICJ9XG4iCiAgICAgICAgICAgICJcbiIKICAgICAgICAgICAgInN0YXRpYyBjb25zdCBzdHJ1Y3QgZmlsZV9vcGVyYXRpb25zIGZtYW5fcGNkX2ZlX3Byb2JlX2ZvcHMgPSB7XG4iCiAgICAgICAgICAgICJcdC5vd25lclx0XHQ9IFRISVNfTU9EVUxFLFxuIgogICAgICAgICAgICAiXHQub3Blblx0XHQ9IGZtYW5fcGNkX2ZlX3Byb2JlX29wZW4sXG4iCiAgICAgICAgICAgICJcdC5yZWFkXHRcdD0gc2VxX3JlYWQsXG4iCiAgICAgICAgICAgICJcdC5sbHNlZWtcdFx0PSBzZXFfbHNlZWssXG4iCiAgICAgICAgICAgICJcdC5yZWxlYXNlXHQ9IHNpbmdsZV9yZWxlYXNlLFxuIgogICAgICAgICAgICAifTtcblxuIgogICAgICAgICkKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShwcm9iZV9hbmNob3IsIHByb2JlX2NvZGUgKyBwcm9iZV9hbmNob3IpCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBmZV9wcm9iZV9zaG93IGZ1bmN0aW9uIGluc2VydGVkIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2MSBXQVJOSU5HOiBwcm9iZSBhbmNob3Igbm90IGZvdW5kIikKCiMgLS0tLSA0LiBSZWdpc3RlciBkZWJ1Z2ZzX2NyZWF0ZV9maWxlKCJmZV9wcm9iZSIgLi4uKSAtLS0tCmlmICdkZWJ1Z2ZzX2NyZWF0ZV9maWxlKCJmZV9wcm9iZSInIGluIHNyYzoKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgZmVfcHJvYmUgZGVidWdmcyBhbHJlYWR5IHJlZ2lzdGVyZWQiKQplbHNlOgogICAgZGJnX2FuY2hvciA9ICdkZWJ1Z2ZzX2NyZWF0ZV9maWxlKCJmZV9oYXNoZmUiJwogICAgaWYgZGJnX2FuY2hvciBpbiBzcmM6CiAgICAgICAgcHJvYmVfZGJnID0gKAogICAgICAgICAgICAnXHRcdFx0ZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiLCAwNDQ0LFxuJwogICAgICAgICAgICAnXHRcdFx0XHRcdCAgICBwY2QtPmRlYnVnZnNfZGlyLCBwY2QsXG4nCiAgICAgICAgICAgICdcdFx0XHRcdFx0ICAgICZmbWFuX3BjZF9mZV9wcm9iZV9mb3BzKTtcbicKICAgICAgICAgICAgJ1x0XHRcdCcgKyBkYmdfYW5jaG9yCiAgICAgICAgKQogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKGRiZ19hbmNob3IsIHByb2JlX2RiZykKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlIGRlYnVnZnMgcmVnaXN0ZXJlZCIpCiAgICBlbHNlOgogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgV0FSTklORzogZGVidWdmcyBhbmNob3Igbm90IGZvdW5kIikKCmlmIGNoYW5nZXMgPT0gMDoKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjEgbm8gY2hhbmdlcyAoYWxsIGFscmVhZHkgYXBwbGllZCkiKQplbHNlOgogICAgd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjoKICAgICAgICBmLndyaXRlKHNyYykKICAgIHByaW50KGYiIyMjIGZtYW5fcGNkLmM6IEYtMDYxIGZlX3Byb2JlOiB7Y2hhbmdlc30gY2hhbmdlKHMpIGFwcGxpZWQiKQo=' | base64 -d | python3
    echo "### fman_pcd.c: F-061 fe_probe debugfs (KG key dump from FE pool workspace)"
fi

# F-068: IC key probe — extend dpaa_eth IC copy to include KG key region.
# The mainline dpaa_eth IC copy (FMBM_RICP: iciof=0, size=48B) only copies
# parser results + timestamp + hash. The KG-extracted key at IC offset 0x48
# is NOT copied. This fixup adds 32 extra bytes to the IC copy size so the
# key region appears in the DDR buffer headroom, readable via the dpaa_eth
# RX path (rx_default_dqrr -> vaddr + prs_result_offset + key_offset).
# Temporary — removed once extraction order is determined.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZHBhYS9kcGFhX2V0aC5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgICAgICBzcmMgPSBmLnJlYWQoKQpleGNlcHQgRmlsZU5vdEZvdW5kRXJyb3I6CiAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IElDIGtleSBkdW1wIC0gZmlsZSBub3QgZm91bmQgKG1heSBub3QgZXhpc3Qgb24gdGhpcyBrZXJuZWwpIikKICAgIHN5cy5leGl0KDApCgojIFRoZSBJQyBjb3B5IHNpemUgaXMgZGVyaXZlZCBmcm9tIERQQUFfSFdBX1NJWkUgPSBEUEFBX1BBUlNFX1JFU1VMVFNfU0laRSArIDggKyA4CiMgV2UgbmVlZCB0byBpbmNyZWFzZSBzaXplIHRvIGNvdmVyIHRoZSBrZXkgcmVnaW9uIGF0IElDIG9mZnNldCAweDQ4LgojIFNlYXJjaCBmb3IgRFBBQV9IV0FfU0laRSBvciB0aGUgYnVmZmVyIHByZWZpeCBjb25maWd1cmF0aW9uLgoKY2hhbmdlcyA9IDAKCiMgTG9vayBmb3IgRFBBQV9IV0FfU0laRSBkZWZpbml0aW9uIGFuZCBidW1wIGl0Cm9sZF9od2EgPSAiI2RlZmluZSBEUEFBX0hXQV9TSVpFIgppZiBvbGRfaHdhIGluIHNyYzoKICAgICMgRmluZCB0aGUgYWN0dWFsIGRlZmluaXRpb24gbGluZQogICAgZm9yIGxpbmUgaW4gc3JjLnNwbGl0KCdcbicpOgogICAgICAgIGlmICJEUEFBX0hXQV9TSVpFIiBpbiBsaW5lIGFuZCAiZGVmaW5lIiBpbiBsaW5lOgogICAgICAgICAgICBwcmludChmIiMjIyBkcGFhX2V0aC5jOiBmb3VuZCB7bGluZS5zdHJpcCgpfSIpCiAgICAgICAgICAgIGJyZWFrCiAgICAKICAgICMgU3RyYXRlZ3k6IGFkZCBEUEFBX0hXQV9LRVlfU0laRSB0aGF0IGluY2x1ZGVzIGV4dHJhIGJ5dGVzIGZvciB0aGUgS0cga2V5CiAgICAjIFRoZSBrZXkgYXQgSUMgb2Zmc2V0IDB4NDggaXMgMTMgYnl0ZXMgZm9yIEVLRkM9MHgwMDFjMDAwNi4KICAgICMgV2Ugd2FudCB0byBjb3B5IGZyb20gSUMgb2Zmc2V0IDB4NDAgKGhhc2gpIHRocm91Z2ggMHg1NSAoa2V5IGVuZCkgPSAyMiBieXRlcy4KICAgICMgQnV0IHRoZSBJQyBjb3B5IGFscmVhZHkgY292ZXJzIGhhc2ggYXQgMHg0MCB2aWEgRFBBQV9IQVNIX1JFU1VMVFNfU0laRT04LgogICAgIyBXZSBqdXN0IG5lZWQgdG8gYnVtcCB0aGUgc2l6ZSB0byBpbmNsdWRlICsxMyBieXRlcyBmb3IgdGhlIGtleS4KICAgIAogICAgb2xkX2h3YV9saW5lID0gIiNkZWZpbmUgRFBBQV9IV0FfU0laRSAgICAgICAgICAgICAgKERQQUFfUEFSU0VfUkVTVUxUU19TSVpFICsgRFBBQV9USU1FX1NUQU1QX1NJWkUgKyBEUEFBX0hBU0hfUkVTVUxUU19TSVpFKSIKICAgIG5ld19od2FfbGluZSA9ICIjZGVmaW5lIERQQUFfSFdBX1NJWkUgICAgICAgICAgICAgIChEUEFBX1BBUlNFX1JFU1VMVFNfU0laRSArIERQQUFfVElNRV9TVEFNUF9TSVpFICsgRFBBQV9IQVNIX1JFU1VMVFNfU0laRSArIDMyKVx0LyogKzMyQiBmb3IgS0cga2V5IHByb2JlICovIgogICAgCiAgICBpZiBvbGRfaHdhX2xpbmUgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKG9sZF9od2FfbGluZSwgbmV3X2h3YV9saW5lKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZHBhYV9ldGguYzogRFBBQV9IV0FfU0laRSBleHRlbmRlZCArMzJCIGZvciBrZXkgcHJvYmUiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IERQQUFfSFdBX1NJWkUgbGluZSBub3QgZm91bmQgKHRhYnMgdnMgc3BhY2VzPykiKQplbHNlOgogICAgcHJpbnQoIiMjIyBkcGFhX2V0aC5jOiBEUEFBX0hXQV9TSVpFIG5vdCBmb3VuZCAoa2VybmVsIHZlcnNpb24/KSIpCgppZiBjaGFuZ2VzID4gMDoKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAgZi53cml0ZShzcmMpCiAgICBwcmludChmIiMjIyBkcGFhX2V0aC5jOiBJQyBrZXkgcHJvYmU6IHtjaGFuZ2VzfSBjaGFuZ2UocykgYXBwbGllZCIpCmVsc2U6CiAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IElDIGtleSBwcm9iZTogbm8gY2hhbmdlcyIpCg==' | base64 -d | python3
    echo "### dpaa_eth.c: F-068 IC key probe (HWA size extended +32B for KG key)"
fi

# F-069a: IC probe — capture RX buffer vaddr in dpaa_eth.c for ic_probe.
# Stores the DMA buffer virtual address in shared global fman_pcd_ic_vaddr
# at the top of rx_default_dqrr() so fman_pcd can dump the IC.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZHBhYS9kcGFhX2V0aC5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjogc3JjID0gZi5yZWFkKCkKZXhjZXB0IEZpbGVOb3RGb3VuZEVycm9yOgogICAgcHJpbnQoIiMjIyBkcGFhX2V0aC5jOiBGLTA2OWEgdjkgZmlsZSBub3QgZm91bmQiKQogICAgc3lzLmV4aXQoMCkKCmNoYW5nZXMgPSAwCgojIDEuIGV4dGVybiBkZWNsYXJhdGlvbnMKaWYgImV4dGVybiB2b2lkICpmbWFuX3BjZF9pY19idWZfYmFzZSIgbm90IGluIHNyYzoKICAgIGZpcnN0X3N0ID0gc3JjLmZpbmQoIlxuc3RhdGljICIpCiAgICBpZiBmaXJzdF9zdCA+IDA6CiAgICAgICAgc3JjID0gKHNyY1s6Zmlyc3Rfc3QrMV0gKwogICAgICAgICAgICAgICAiZXh0ZXJuIHZvaWQgKmZtYW5fcGNkX2ljX3ZhZGRyO1xuIiArCiAgICAgICAgICAgICAgICJleHRlcm4gdm9pZCAqZm1hbl9wY2RfaWNfYnVmX2Jhc2U7XG4iICsKICAgICAgICAgICAgICAgc3JjW2ZpcnN0X3N0KzE6XSkKICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IEYtMDY5YSB2OSBleHRlcm5zIGFkZGVkIikKCiMgMi4gQ2FwdHVyZSBidWZfYmFzZSBmcm9tIGRwYWFfYnAtPnZhZGRyICh0cnkgbXVsdGlwbGUgdmFyaWFibGUgbmFtZXMpCmNhcHR1cmVkID0gImZtYW5fcGNkX2ljX2J1Zl9iYXNlID0gIgppZiBjYXB0dXJlZCBub3QgaW4gc3JjOgogICAgYW5jaG9yID0gInBoeXNfdG9fdmlydChhZGRyKSIKICAgIHBvcyA9IHNyYy5maW5kKGFuY2hvcikKICAgIGlmIHBvcyA+IDA6CiAgICAgICAgZW9sID0gc3JjLmZpbmQoJ1xuJywgcG9zKQogICAgICAgICMgU2VhcmNoIGZvciBidWZmZXItcG9vbCB2YXJpYWJsZSBuZWFyIHRoZSB2YWRkciBhc3NpZ25tZW50CiAgICAgICAgIyBUcnk6IGRwYWFfYnAsIGJwLCBkcGFhX2JwX3B0ciwgcHJpdi0+YnAKICAgICAgICBzZWN0aW9uID0gc3JjW3Bvcy0zMDA6cG9zKzgwMF0KICAgICAgICBicF92YXIgPSBOb25lCiAgICAgICAgZm9yIHZhciBpbiBbJ2RwYWFfYnAtPnZhZGRyJywgJ2JwLT52YWRkcicsICdkYnAtPnZhZGRyJ106CiAgICAgICAgICAgIGlmIHZhciBpbiBzZWN0aW9uOgogICAgICAgICAgICAgICAgYnBfdmFyID0gdmFyCiAgICAgICAgICAgICAgICBicmVhawogICAgICAgIGlmIGJwX3ZhcjoKICAgICAgICAgICAgY2FwdHVyZSA9ICgnXG5cblx0LyogRi0wNjlhIHY5OiBjYXB0dXJlIGJ1ZmZlciBiYXNlICsgZnJhbWUtZGF0YSB2YWRkciAqL1xuJwogICAgICAgICAgICAgICAgICAgICAgIGYnXHRmbWFuX3BjZF9pY192YWRkciA9IHZhZGRyO1xuJwogICAgICAgICAgICAgICAgICAgICAgIGYnXHRmbWFuX3BjZF9pY19idWZfYmFzZSA9IHticF92YXJ9OycpCiAgICAgICAgICAgIHNyYyA9IHNyY1s6ZW9sKzFdICsgY2FwdHVyZSArIHNyY1tlb2wrMTpdCiAgICAgICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgICAgICBwcmludChmIiMjIyBkcGFhX2V0aC5jOiBGLTA2OWEgdjkgYnVmX2Jhc2UgY2FwdHVyZSB1c2luZyB7YnBfdmFyfSIpCiAgICAgICAgZWxzZToKICAgICAgICAgICAgY2FwdHVyZSA9ICgnXG5cblx0LyogRi0wNjlhIHY5OiBjYXB0dXJlIGZyYW1lLWRhdGEgdmFkZHIgKG5vIGJwIHZhciBmb3VuZCkgKi9cbicKICAgICAgICAgICAgICAgICAgICAgICAnXHRmbWFuX3BjZF9pY192YWRkciA9IHZhZGRyOycpCiAgICAgICAgICAgIHNyYyA9IHNyY1s6ZW9sKzFdICsgY2FwdHVyZSArIHNyY1tlb2wrMTpdCiAgICAgICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgICAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IEYtMDY5YSB2OSB2YWRkciBjYXB0dXJlIG9ubHkgKG5vIGJwIHZhcikiKQogICAgZWxzZToKICAgICAgICBwcmludCgiIyMjIGRwYWFfZXRoLmM6IEYtMDY5YSB2OSBwaHlzX3RvX3ZpcnQgYW5jaG9yIG5vdCBmb3VuZCIpCgppZiBjaGFuZ2VzOgogICAgd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjogZi53cml0ZShzcmMpCiAgICBwcmludChmIiMjIyBkcGFhX2V0aC5jOiBGLTA2OWEgdjkge2NoYW5nZXN9IGNoYW5nZShzKSBhcHBsaWVkIikKZWxzZToKICAgIHByaW50KCIjIyMgZHBhYV9ldGguYzogRi0wNjlhIHY5IG5vIGNoYW5nZXMiKQo=' | base64 -d | python3
    echo "### dpaa_eth.c: F-069a v9 buf_base + vaddr captures\n"
fi

# F-072: capture full 8-byte KG CRC-64 hash from dpaa_eth RXHASH path.
# Reads be64_to_cpu(vaddr+hash_offset) and stores in fman_pcd_kg_hash.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZHBhYS9kcGFhX2V0aC5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjogc3JjID0gZi5yZWFkKCkKZXhjZXB0IEZpbGVOb3RGb3VuZEVycm9yOgogICAgcHJpbnQoIiMjIyBGLTA3MiB2NyBmaWxlIG5vdCBmb3VuZCIpCiAgICBzeXMuZXhpdCgwKQoKY2hhbmdlcyA9IDAKCiMgQWRkIGV4dGVybnMKaWYgImV4dGVybiB1NjQgZm1hbl9wY2Rfa2dfaGFzaCIgbm90IGluIHNyYzoKICAgIGZpcnN0X3N0ID0gc3JjLmZpbmQoIlxuc3RhdGljICIpCiAgICBpZiBmaXJzdF9zdCA+IDA6CiAgICAgICAgc3JjID0gKHNyY1s6Zmlyc3Rfc3QrMV0gKwogICAgICAgICAgICAgICAiZXh0ZXJuIHU2NCBmbWFuX3BjZF9rZ19oYXNoO1xuIgogICAgICAgICAgICAgICAiZXh0ZXJuIHVuc2lnbmVkIGludCBmbWFuX3BjZF9oYXNoX29mZjtcbiIgKwogICAgICAgICAgICAgICBzcmNbZmlyc3Rfc3QrMTpdKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZHBhYV9ldGguYzogRi0wNzIgdjcgZXh0ZXJucyBhZGRlZCIpCgojIEFkZCBjYXB0dXJlOiBvbmx5IGZvciBldGg0IChzdHJjbXAgb24gbmV0X2Rldi0+bmFtZSkKaWYgImZtYW5fcGNkX2hhc2hfb2ZmID0gIiBub3QgaW4gc3JjOgogICAgYW5jaG9yID0gImZtYW5fcG9ydF9nZXRfaGFzaF9yZXN1bHRfb2Zmc2V0IgogICAgcG9zID0gc3JjLmZpbmQoYW5jaG9yKQogICAgaWYgcG9zID4gMDoKICAgICAgICByZWdpb24gPSBzcmNbcG9zOnBvcys4MDBdCiAgICAgICAgYmUzMl9wb3MgPSByZWdpb24uZmluZCgiYmUzMl90b19jcHUoKihfX2JlMzIgKikiKQogICAgICAgIGlmIGJlMzJfcG9zID4gMDoKICAgICAgICAgICAgYWN0dWFsX2VvbCA9IHNyYy5maW5kKCdcbicsIHBvcyArIGJlMzJfcG9zKQogICAgICAgICAgICBpZiBhY3R1YWxfZW9sID4gcG9zICsgYmUzMl9wb3M6CiAgICAgICAgICAgICAgICBjYXB0dXJlID0gKAogICAgICAgICAgICAgICAgICAgICdcblx0LyogRi0wNzIgdjc6IGNhcHR1cmUgaGFzaCBmb3IgZXRoNCBvbmx5ICovXG4nCiAgICAgICAgICAgICAgICAgICAgJ1x0aWYgKCFzdHJjbXAobmV0X2Rldi0+bmFtZSwgImV0aDQiKSkge1xuJwogICAgICAgICAgICAgICAgICAgICdcdFx0Zm1hbl9wY2RfaGFzaF9vZmYgPSBoYXNoX29mZnNldDtcbicKICAgICAgICAgICAgICAgICAgICAnXHRcdGZtYW5fcGNkX2tnX2hhc2ggPSBiZTY0X3RvX2NwdSgqKF9fYmU2NCAqKSh2YWRkciArIGhhc2hfb2Zmc2V0KSk7XG4nCiAgICAgICAgICAgICAgICAgICAgJ1x0fScpCiAgICAgICAgICAgICAgICBzcmMgPSBzcmNbOmFjdHVhbF9lb2wrMV0gKyBjYXB0dXJlICsgc3JjW2FjdHVhbF9lb2wrMTpdCiAgICAgICAgICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICAgICAgICAgIHByaW50KCIjIyMgZHBhYV9ldGguYzogRi0wNzIgdjcgZXRoNCBzdHJjbXAgY2FwdHVyZSIpCgppZiBjaGFuZ2VzOgogICAgd2l0aCBvcGVuKHBhdGgsICJ3IikgYXMgZjogZi53cml0ZShzcmMpCiAgICBwcmludChmIiMjIyBkcGFhX2V0aC5jOiBGLTA3MiB2NyB7Y2hhbmdlc30gY2hhbmdlKHMpIGFwcGxpZWQiKQplbHNlOgogICAgcHJpbnQoIiMjIyBkcGFhX2V0aC5jOiBGLTA3MiB2NyBubyBjaGFuZ2VzIikK' | base64 -d | python3
fi

# F-069b: IC probe debugfs node — reads buffer captured by F-069a.
# Shows 32 u32 words (128 bytes) from the DMA buffer headroom.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp3aXRoIG9wZW4ocGF0aCkgYXMgZjoKICAgIHNyYyA9IGYucmVhZCgpCgpjaGFuZ2VzID0gMAoKaWNfcHJvYmVfYm9keSA9ICgKICAgICIvKiBGLTA2OWIgdjIyOiBwYWdlLWJvdW5kYXJ5LWF3YXJlIHNjYW4gZnJvbSBmcmFtZS1kYXRhIHZhZGRyLlxuIgogICAgIiAqIHZhZGRyID0gcGh5c190b192aXJ0KGZkLmFkZHIpID0gZnJhbWUtZGF0YSBhZGRyZXNzLlxuIgogICAgIiAqIElDIGlzIGF0IHZhZGRyIC0gZGF0YV9vZmZzZXQgKGRhdGFfb2Zmc2V0IHR5cGljYWxseSA2NC0yNTYpLlxuIgogICAgIiAqIFNhZmUgYmFja3dhcmQgc2Nhbjogb25seSB1cCB0byB0aGUgcGFnZSBib3VuZGFyeSBvZiB2YWRkci5cbiIKICAgICIgKi9cbiIKICAgICJzdGF0aWMgaW50IGZtYW5fcGNkX2ljX3Byb2JlX3Nob3coc3RydWN0IHNlcV9maWxlICpzLCB2b2lkICp1bnVzZWQpXG4iCiAgICAie1xuIgogICAgIlx0dm9pZCAqdmFkZHI7XG4iCiAgICAiXHRsb25nIHBhZ2Vfb2ZmO1xuIgogICAgIlx0aW50IG9mZiwgbWF4X2JhY2s7XG4iCiAgICAiXHR1bnNpZ25lZCBpbnQgaSwgdiwgbm9uemVybztcbiIKICAgICJcbiIKICAgICJcdHNtcF9ybWIoKTtcbiIKICAgICJcdHZhZGRyID0gZm1hbl9wY2RfaWNfdmFkZHI7XG4iCiAgICAiXHRpZiAoIXZhZGRyKSB7XG4iCiAgICAiXHRcdHNlcV9wdXRzKHMsIFwibm8gZnJhbWUgY2FwdHVyZWRcXG5cIik7XG4iCiAgICAiXHRcdHJldHVybiAwO1xuIgogICAgIlx0fVxuIgogICAgIlx0cGFnZV9vZmYgPSAodW5zaWduZWQgbG9uZyl2YWRkciAmIDB4RkZGO1xuIgogICAgIlx0bWF4X2JhY2sgPSBwYWdlX29mZiA8IDI1NiA/IHBhZ2Vfb2ZmIDogMjU2O1xuIgogICAgIlx0c2VxX3ByaW50ZihzLCBcInZhZGRyPSVweCBwYWdlX29mZj0weCUwM2x4IG1heF9iYWNrPSVkXFxuXCIsXG4iCiAgICAiXHRcdCAgIHZhZGRyLCBwYWdlX29mZiwgbWF4X2JhY2spO1xuIgogICAgIlx0LyogU2NhbiAtbWF4X2JhY2sgLi4gKzUxMSBhcm91bmQgdmFkZHIgKGNvdmVycyBJQyArIGZyYW1lIHN0YXJ0KSAqL1xuIgogICAgIlx0Zm9yIChvZmYgPSAtbWF4X2JhY2s7IG9mZiA8IDUxMjsgb2ZmICs9IDE2KSB7XG4iCiAgICAiXHRcdG5vbnplcm8gPSAwO1xuIgogICAgIlx0XHRmb3IgKGkgPSAwOyBpIDwgNDsgaSsrKSB7XG4iCiAgICAiXHRcdFx0aWYgKCgodTMyICopdmFkZHIpW29mZi80ICsgaV0pIG5vbnplcm8rKztcbiIKICAgICJcdFx0fVxuIgogICAgIlx0XHRpZiAobm9uemVybykge1xuIgogICAgIlx0XHRcdHNlcV9wcmludGYocywgXCIlKzA1ZDpcIiwgb2ZmKTtcbiIKICAgICJcdFx0XHRmb3IgKGkgPSAwOyBpIDwgNDsgaSsrKSB7XG4iCiAgICAiXHRcdFx0XHR2ID0gYmUzMl90b19jcHUoKCh1MzIgKil2YWRkcilbb2ZmLzQgKyBpXSk7XG4iCiAgICAiXHRcdFx0XHRzZXFfcHJpbnRmKHMsIFwiICUwOHhcIiwgdik7XG4iCiAgICAiXHRcdFx0fVxuIgogICAgIlx0XHRcdHNlcV9wdXRzKHMsIFwiXFxuXCIpO1xuIgogICAgIlx0XHR9XG4iCiAgICAiXHR9XG4iCiAgICAiXHRyZXR1cm4gMDtcbiIKICAgICJ9XG4iCiAgICAiXG4iCiAgICAic3RhdGljIGludCBmbWFuX3BjZF9pY19wcm9iZV9vcGVuKHN0cnVjdCBpbm9kZSAqaW5vZGUsIHN0cnVjdCBmaWxlICpmaWxlKVxuIgogICAgIntcbiIKICAgICJcdHJldHVybiBzaW5nbGVfb3BlbihmaWxlLCBmbWFuX3BjZF9pY19wcm9iZV9zaG93LCBpbm9kZS0+aV9wcml2YXRlKTtcbiIKICAgICJ9XG4iCiAgICAiXG4iCiAgICAic3RhdGljIGNvbnN0IHN0cnVjdCBmaWxlX29wZXJhdGlvbnMgZm1hbl9wY2RfaWNfcHJvYmVfZm9wcyA9IHtcbiIKICAgICJcdC5vd25lclx0XHQ9IFRISVNfTU9EVUxFLFxuIgogICAgIlx0Lm9wZW5cdFx0PSBmbWFuX3BjZF9pY19wcm9iZV9vcGVuLFxuIgogICAgIlx0LnJlYWRcdFx0PSBzZXFfcmVhZCxcbiIKICAgICJcdC5sbHNlZWtcdFx0PSBzZXFfbHNlZWssXG4iCiAgICAiXHQucmVsZWFzZVx0PSBzaW5nbGVfcmVsZWFzZSxcbiIKICAgICJ9O1xuXG4iCikKCmlmICJmbWFuX3BjZF9pY19wcm9iZV9zaG93IiBpbiBzcmM6CiAgICBtYXJrZXIgPSAiLyogRi0wNjliIgogICAgc3RhcnQgPSBzcmMuZmluZChtYXJrZXIpCiAgICBpZiBzdGFydCA+IDA6CiAgICAgICAgZW5kID0gInN0YXRpYyBpbnQgZm1hbl9wY2RfZmVfcHJvYmVfc2hvdyIKICAgICAgICBlbmRfcG9zID0gc3JjLmZpbmQoZW5kLCBzdGFydCkKICAgICAgICBpZiBlbmRfcG9zID4gc3RhcnQ6CiAgICAgICAgICAgIHNyYyA9IHNyY1s6c3RhcnRdICsgaWNfcHJvYmVfYm9keSArIHNyY1tlbmRfcG9zOl0KICAgICAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjliIHYyMiBpY19wcm9iZSByZXBsYWNlZCIpCmVsc2U6CiAgICBhbmNob3IgPSAic3RhdGljIGludCBmbWFuX3BjZF9mZV9wcm9iZV9zaG93IgogICAgcG9zID0gc3JjLmZpbmQoYW5jaG9yKQogICAgaWYgcG9zID4gMDoKICAgICAgICBzcmMgPSBzcmNbOnBvc10gKyBpY19wcm9iZV9ib2R5ICsgc3JjW3BvczpdCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA2OWIgdjIyIGljX3Byb2JlIGluc2VydGVkIGZyZXNoIikKCmZlX2RiZyA9ICgnXHRcdFx0ZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiLCAwNDQ0LFxuJwogICAgICAgICAgJ1x0XHRcdFx0XHQgICAgcGNkLT5kZWJ1Z2ZzX2RpciwgcGNkLFxuJwogICAgICAgICAgJ1x0XHRcdFx0XHQgICAgJmZtYW5fcGNkX2ZlX3Byb2JlX2ZvcHMpOycpCmljX2RiZyA9ICgnXHRcdFx0ZGVidWdmc19jcmVhdGVfZmlsZSgiZmVfcHJvYmUiLCAwNDQ0LFxuJwogICAgICAgICAgJ1x0XHRcdFx0XHQgICAgcGNkLT5kZWJ1Z2ZzX2RpciwgcGNkLFxuJwogICAgICAgICAgJ1x0XHRcdFx0XHQgICAgJmZtYW5fcGNkX2ZlX3Byb2JlX2ZvcHMpO1xuJwogICAgICAgICAgJ1x0XHRcdGRlYnVnZnNfY3JlYXRlX2ZpbGUoImljX3Byb2JlIiwgMDQ0NCxcbicKICAgICAgICAgICdcdFx0XHRcdFx0ICAgIHBjZC0+ZGVidWdmc19kaXIsIHBjZCxcbicKICAgICAgICAgICdcdFx0XHRcdFx0ICAgICZmbWFuX3BjZF9pY19wcm9iZV9mb3BzKTsnKQppZiBmZV9kYmcgaW4gc3JjIGFuZCAnZGVidWdmc19jcmVhdGVfZmlsZSgiaWNfcHJvYmUiJyBub3QgaW4gc3JjOgogICAgc3JjID0gc3JjLnJlcGxhY2UoZmVfZGJnLCBpY19kYmcsIDEpCiAgICBjaGFuZ2VzICs9IDEKCmlmIGNoYW5nZXM6CiAgICB3aXRoIG9wZW4ocGF0aCwgInciKSBhcyBmOiBmLndyaXRlKHNyYykKICAgIHByaW50KGYiIyMjIGZtYW5fcGNkLmM6IEYtMDY5YiB2MjIge2NoYW5nZXN9IGNoYW5nZShzKSBhcHBsaWVkIikKZWxzZToKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNjliIHYyMiBubyBjaGFuZ2VzIikK' | base64 -d | python3
fi

# Strip EXPORT_SYMBOL_GPL placed before #include by F-069b v3.
# EXPORT_SYMBOL_GPL needs <linux/export.h> which isn't included yet.
# Both fsl_dpaa_fman and dpaa_eth are built-in, so the symbol resolves 

# F-071: hash_probe debugfs — read full 8-byte KG CRC-64 hash from annotation.
# Uses fman_pcd_ic_vaddr (from F-069a) and fman_pcd_hash_off (from F-070).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHN5cwoKcGF0aCA9ICJkcml2ZXJzL25ldC9ldGhlcm5ldC9mcmVlc2NhbGUvZm1hbi9mbWFuX3BjZC5jIgp0cnk6CiAgICB3aXRoIG9wZW4ocGF0aCkgYXMgZjogc3JjID0gZi5yZWFkKCkKZXhjZXB0IEZpbGVOb3RGb3VuZEVycm9yOgogICAgcHJpbnQoIiMjIyBGLTA3MSB2MTggZmlsZSBub3QgZm91bmQiKQogICAgc3lzLmV4aXQoMCkKCmNoYW5nZXMgPSAwCgojIDEuIEFkZCBnbG9iYWxzIChqdXN0IGhhc2ggKyBoYXNoX29mZikKaWYgImZtYW5fcGNkX2tnX2hhc2giIG5vdCBpbiBzcmM6CiAgICBmaXJzdF9zdCA9IHNyYy5maW5kKCJcbnN0YXRpYyAiKQogICAgaWYgZmlyc3Rfc3QgPiAwOgogICAgICAgIHNyYyA9IChzcmNbOmZpcnN0X3N0KzFdICsKICAgICAgICAgICAgICAgInU2NCBmbWFuX3BjZF9rZ19oYXNoO1xuIgogICAgICAgICAgICAgICAidW5zaWduZWQgaW50IGZtYW5fcGNkX2hhc2hfb2ZmO1xuIiArCiAgICAgICAgICAgICAgIHNyY1tmaXJzdF9zdCsxOl0pCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA3MSB2MTggZ2xvYmFscyBhZGRlZCIpCgojIDIuIEFkZCBoYXNoX3Byb2JlX3Nob3cgZnVuY3Rpb24KaWYgInN0YXRpYyBpbnQgZm1hbl9wY2RfaGFzaF9wcm9iZV9zaG93IiBub3QgaW4gc3JjOgogICAgYW5jaG9yID0gInN0YXRpYyBpbnQgZm1hbl9wY2RfaWNfcHJvYmVfc2hvdyIKICAgIHBvcyA9IHNyYy5maW5kKGFuY2hvcikKICAgIGlmIHBvcyA+IDA6CiAgICAgICAgc2hvd19mdW5jID0gKAogICAgICAgICAgICAic3RhdGljIGludCBmbWFuX3BjZF9oYXNoX3Byb2JlX3Nob3coc3RydWN0IHNlcV9maWxlICptLCB2b2lkICp2KVxuIgogICAgICAgICAgICAie1xuIgogICAgICAgICAgICAiXHRpZiAoIWZtYW5fcGNkX2hhc2hfb2ZmKSB7XG4iCiAgICAgICAgICAgICJcdFx0c2VxX3B1dHMobSwgXCJpZGxlIChubyBldGg0IGZyYW1lIGNhcHR1cmVkKVxcblwiKTtcbiIKICAgICAgICAgICAgIlx0XHRyZXR1cm4gMDtcbiIKICAgICAgICAgICAgIlx0fVxuIgogICAgICAgICAgICAiXHRzZXFfcHJpbnRmKG0sIFwiaGFzaF9vZmY9JXUgY2FwdHVyZWQ9JTAxNmxseFxcblwiLFxuIgogICAgICAgICAgICAiXHRcdGZtYW5fcGNkX2hhc2hfb2ZmLCBmbWFuX3BjZF9rZ19oYXNoKTtcbiIKICAgICAgICAgICAgIlx0cmV0dXJuIDA7XG4iCiAgICAgICAgICAgICJ9XG5cbiIpCiAgICAgICAgc3JjID0gc3JjWzpwb3NdICsgc2hvd19mdW5jICsgc3JjW3BvczpdCiAgICAgICAgY2hhbmdlcyArPSAxCiAgICAgICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA3MSB2MTggaGFzaF9wcm9iZV9zaG93IGNyZWF0ZWQiKQoKIyAzLiBSZWdpc3RlciBoYXNoX3Byb2JlIGRlYnVnZnMKaWYgJ2RlYnVnZnNfY3JlYXRlX2ZpbGUoImhhc2hfcHJvYmUiJyBub3QgaW4gc3JjOgogICAgZmVfYW5jaG9yID0gJ2RlYnVnZnNfY3JlYXRlX2ZpbGUoImZlX3Byb2JlIicKICAgIGZlX3BvcyA9IHNyYy5maW5kKGZlX2FuY2hvcikKICAgIGlmIGZlX3BvcyA+IDA6CiAgICAgICAgc2VtaSA9IHNyYy5maW5kKCc7JywgZmVfcG9zKQogICAgICAgIGlmIHNlbWkgPiAwOgogICAgICAgICAgICBlb2wgPSBzcmMuZmluZCgnXG4nLCBzZW1pKQogICAgICAgICAgICBpZiBlb2wgPiAwOgogICAgICAgICAgICAgICAgcmVnX2xpbmUgPSAoCiAgICAgICAgICAgICAgICAgICAgJ1xuJwogICAgICAgICAgICAgICAgICAgICdcdGlmICghZGVidWdmc19jcmVhdGVfZmlsZSgiaGFzaF9wcm9iZSIsIDA0NDQsIHBjZC0+ZGVidWdmc19kaXIsIHBjZCxcbicKICAgICAgICAgICAgICAgICAgICAnXHRcdFx0ICAgICAgICZmbWFuX3BjZF9oYXNoX3Byb2JlX2ZvcHMpKVxuJwogICAgICAgICAgICAgICAgICAgICdcdFx0cHJfd2FybigiJXM6IGVycm9yIGNyZWF0aW5nIGhhc2hfcHJvYmVcXG4iLCBfX2Z1bmNfXyk7XG4nKQogICAgICAgICAgICAgICAgc3JjID0gc3JjWzplb2wrMV0gKyByZWdfbGluZSArIHNyY1tlb2wrMTpdCiAgICAgICAgICAgICAgICBjaGFuZ2VzICs9IDEKICAgICAgICAgICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNzEgdjE4IGhhc2hfcHJvYmUgZGVidWdmcyByZWdpc3RlcmVkIikKCiMgNC4gREVGSU5FX1NIT1dfQVRUUklCVVRFIGFmdGVyIGhhc2hfcHJvYmVfc2hvdyBmdW5jdGlvbgppZiAnREVGSU5FX1NIT1dfQVRUUklCVVRFKGZtYW5fcGNkX2hhc2hfcHJvYmUpJyBub3QgaW4gc3JjOgogICAgZnVuY19tYXJrZXIgPSAic3RhdGljIGludCBmbWFuX3BjZF9oYXNoX3Byb2JlX3Nob3ciCiAgICBmdW5jX3BvcyA9IHNyYy5maW5kKGZ1bmNfbWFya2VyKQogICAgaWYgZnVuY19wb3MgPiAwOgogICAgICAgIGJyYWNlX2NvdW50ID0gMAogICAgICAgIGZ1bmNfZW5kID0gZnVuY19wb3MKICAgICAgICBmb3IgaSBpbiByYW5nZShzcmMuZmluZCgneycsIGZ1bmNfcG9zKSwgbGVuKHNyYykpOgogICAgICAgICAgICBpZiBzcmNbaV0gPT0gJ3snOiBicmFjZV9jb3VudCArPSAxCiAgICAgICAgICAgIGVsaWYgc3JjW2ldID09ICd9JzoKICAgICAgICAgICAgICAgIGJyYWNlX2NvdW50IC09IDEKICAgICAgICAgICAgICAgIGlmIGJyYWNlX2NvdW50ID09IDA6CiAgICAgICAgICAgICAgICAgICAgZnVuY19lbmQgPSBpICsgMQogICAgICAgICAgICAgICAgICAgIGJyZWFrCiAgICAgICAgc3JjID0gKHNyY1s6ZnVuY19lbmRdICsKICAgICAgICAgICAgICAgJ1xuREVGSU5FX1NIT1dfQVRUUklCVVRFKGZtYW5fcGNkX2hhc2hfcHJvYmUpO1xuJyArCiAgICAgICAgICAgICAgIHNyY1tmdW5jX2VuZDpdKQogICAgICAgIGNoYW5nZXMgKz0gMQogICAgICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogRi0wNzEgdjE4IGhhc2hfcHJvYmUgZm9wcyBkZWZpbmVkIikKCmlmIGNoYW5nZXM6CiAgICB3aXRoIG9wZW4ocGF0aCwgInciKSBhcyBmOiBmLndyaXRlKHNyYykKICAgIHByaW50KGYiIyMjIGZtYW5fcGNkLmM6IEYtMDcxIHYxOCB7Y2hhbmdlc30gY2hhbmdlKHMpIGFwcGxpZWQiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZC5jOiBGLTA3MSB2MTggbm8gY2hhbmdlcyIpCg==' | base64 -d | python3
fi

# without exporting.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i '/^EXPORT_SYMBOL_GPL(fman_pcd_ic_vaddr);$/d' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: stripped EXPORT_SYMBOL_GPL (before includes)"
fi

# Suppress -Wunused-function for fman_pcd_fe_build_contexts (leftover
# from CCBS scaffold removal). The function was called from 0150 which
# F-047 removed.  Avoids -Werror build failure.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static void fman_pcd_fe_build_contexts/static __maybe_unused void fman_pcd_fe_build_contexts/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    sed -i 's/fman_muram_offset_to_vbase(muram,/(void *)fman_muram_offset_to_vbase(muram,/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: fe_build_contexts fixed (__maybe_unused + cast)"
fi



fi

# F-062a: Reverse F-059 — route HIT to MUX→ENQ→FQ 0x200, not EXIT.
# EXIT returns to the scheme which has fqb=0 → BMI stall.  HIT through
# MUX→ENQ bypasses the scheme entirely: the ENQ AD word 2 has FQID 0x200
# (F-058) and QMan handles buffer release properly.  MISS still goes to
# EXIT (hash FE word 6 unchanged); we fix that via F-062b below.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/pcd->fe_exit_off,/pcd->fe_mux_off,/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-062a HIT route changed from EXIT to MUX→ENQ"
fi

# F-062b DISABLED: The hardcoded fqb=0x200 override is WRONG — FQ 0x200
# is not allocated by the kernel for every port (only for port 3 / eth3 where
# it happens to be in the PCD range 512-639).  For other ports, dispatching
# deallocated frames to FQ 0x200 hits an uninitialized QMan FQ → corruption
# → kernel panic in dpaa_cleanup_tx_fd.
#
# Instead, let the kernel's original keygen_port_hashing_init() set the
# correct per-port fqb.  After FE-VM processing (with DEALLOCATE stripped by
# F-062e), the scheme dispatches the intact frame to the kernel's own default
# RX FQ — the kernel polls it, receives the frame, and handles buffer lifecycle.
: 'F-062b-DISABLED'
: 'if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then'
: '    sed ...'
: 'fi'

# F-062e: Strip FMAN_FE_EXIT_DEALLOCATE from Transition only.
# Per NXP FMan microcode 210.10.1 programming reference §7.1:
#   EXIT type 0x03800000 = "Free workspace allocation, terminate frame.
#   Terminal MISS disposition."
# And §7.4: "EXIT-DEALLOCATE is a real terminal MISS disposition on
#   210.10.1: AC_CC arm → MISS → EXIT → port does NOT park."
#
# The EXIT FE with DEALLOCATE (0x00800000) provides terminal BMI-FIFO
# disposition — the FE-VM opcode interpreter handles final dispatch and
# the scheme does NOT try to enqueue after EXIT returns.  No fqb needed.
#
# The Transition FE (§7.6, encoding 0x05000000) does NOT carry DEALLOCATE
# — deallocation is EXIT's responsibility.  Strip DEALLOCATE from Transition
# only; RESTORE it on EXIT (undo the over-broad F-062e v1).
#
# For Layer 1 HIT forwarding: MUX → Transition(AD_FROM_WS) → workspace points
# to ENQ → dedicated TX FQ.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    # Transition: strip DEALLOCATE, keep AD_FROM_WS
    sed -i 's/p.flags = FMAN_FE_EXIT_DEALLOCATE | FMAN_FE_TRANSITION_AD_FROM_WS;/p.flags = FMAN_FE_TRANSITION_AD_FROM_WS;  \/\* F-062e: Transition no DEALLOCATE — EXIT handles terminal disposition \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    # EXIT: RESTORE DEALLOCATE (undo the old strip)
    # The patch 0124 sets p.flags = FMAN_FE_EXIT_DEALLOCATE; which is CORRECT.
    # F-062e v1's sed changed it to p.flags = 0; — revert that change.
    sed -i 's/p.flags = 0;  \/\* F-062e: no DEALLOCATE — scheme fqb owns buffer \*\//p.flags = FMAN_FE_EXIT_DEALLOCATE;  \/\* F-062e v2: DEALLOCATE provides terminal MISS disposition per NXP doc §7.4 \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-062e v2 — Transition no DEALLOCATE, EXIT DEALLOCATE RESTORED (terminal disposition)"
fi

# F-062d: MISS stays at EXIT (proven safe, no BMI stall per 2026-07-10 A/B test).
# Routing MISS through MUX→ENQ caused BMI stall because ENQ→QMan path has
# never been silicon-proven in FE-VM architecture (M2 gate used CONT_LOOKUP AD).
# Keep only the MUX ALLOCATE fix — MUX needs ALLOCATE for HIT frames that
# chain through MUX→ENQ.  MISS stays at EXIT→safe-drop.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    # ENQ ALLOCATE — add to p.flags (fman_pcd_fe_build encodes w[0] = type|flags)
    # EXIT has ALLOCATE and works; ENQ needs it to free FE workspace
    sed -i 's/p.flags = FMAN_FE_ENQ_FQID;/p.flags = FMAN_FE_ENQ_FQID | FMAN_AD_FE_ENTER_ALLOCATE; \/\* F-062d: free FE workspace \*\//' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c 2>/dev/null || true
    echo "### fman_pcd.c: F-062d ENQ ALLOCATE for workspace cleanup"
fi

# M2-4: free params page on disengage (was leaking 256 B per cycle)
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_kg.c ]; then
    echo 'aW1wb3J0IHN5cwpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkX2tnLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCm9sZCA9ICgnXHRpZiAocnhwb3J0KVxuJwogICAgICAgJ1x0XHQodm9pZClmbWFuX3BvcnRfc2V0X2NjX2Jhc2Uocnhwb3J0LCAwKTtcbicKICAgICAgICdcdCh2b2lkKWZtYW5fcGNkX2tnX3BvcnRfZGV0YWNoX2NjKHBjZCwgaHdfcG9ydF9pZCk7JykKbmV3ID0gKCdcdGlmIChyeHBvcnQpIHtcbicKICAgICAgICdcdFx0dTMyIHBwX29mZjtcbicKICAgICAgICdcdFx0KHZvaWQpZm1hbl9wb3J0X3NldF9jY19iYXNlKHJ4cG9ydCwgMCk7XG4nCiAgICAgICAnXHRcdHBwX29mZiA9IGZtYW5fcG9ydF9nZXRfcGFyYW1zX3BhZ2Uocnhwb3J0KTtcbicKICAgICAgICdcdFx0aWYgKHBwX29mZikge1xuJwogICAgICAgJ1x0XHRcdGZtYW5fcGNkX211cmFtX2ZyZWUocGNkLCBwcF9vZmYsIDI1Nik7XG4nCiAgICAgICAnXHRcdFx0KHZvaWQpZm1hbl9wb3J0X3NldF9wYXJhbXNfcGFnZShyeHBvcnQsIDAsIE5VTEwpO1xuJwogICAgICAgJ1x0XHR9XG4nCiAgICAgICAnXHR9XG4nCiAgICAgICAnXHQodm9pZClmbWFuX3BjZF9rZ19wb3J0X2RldGFjaF9jYyhwY2QsIGh3X3BvcnRfaWQpOycpCmlmIG9sZCBpbiBzcmM6CiAgICBzcmMgPSBzcmMucmVwbGFjZShvbGQsIG5ldywgMSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAgZi53cml0ZShzcmMpCiAgICBwcmludCgiIyMjIGZtYW5fcGNkX2tnLmM6IHBhcmFtcyBwYWdlIGZyZWVkIG9uIGRpc2FybSAoTTItNCkiKQplbHNlOgogICAgcHJpbnQoIiMjIyBmbWFuX3BjZF9rZy5jOiBwYXR0ZXJuIG5vdCBmb3VuZCAoYWxyZWFkeSBmaXhlZD8pIikK' | base64 -d | python3
    echo "### fman_pcd_kg.c: M2-4 params page freed on disarm"
fi

# M2-4: fe_port_set lazy-allocates params page if not yet created
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    echo 'aW1wb3J0IHN5cwpwYXRoID0gImRyaXZlcnMvbmV0L2V0aGVybmV0L2ZyZWVzY2FsZS9mbWFuL2ZtYW5fcGNkLmMiCndpdGggb3BlbihwYXRoKSBhcyBmOgogICAgc3JjID0gZi5yZWFkKCkKCm9sZCA9ICgnXHRwYXJhbXNfb2ZmID0gZm1hbl9wb3J0X2dldF9wYXJhbXNfcGFnZShwb3J0KTtcbicKICAgICAgICdcdGlmICghcGFyYW1zX29mZilcbicKICAgICAgICdcdFx0cmV0dXJuIC1FTlhJTzsnKQpuZXcgPSAoJ1x0cGFyYW1zX29mZiA9IGZtYW5fcG9ydF9nZXRfcGFyYW1zX3BhZ2UocG9ydCk7XG4nCiAgICAgICAnXHRpZiAoIXBhcmFtc19vZmYpIHtcbicKICAgICAgICdcdFx0aW50IF9lcnIgPSBmbWFuX3BjZF9wb3J0X2Vuc3VyZV9wYXJhbXNfcGFnZShwY2QsIHBvcnQpO1xuJwogICAgICAgJ1x0XHRpZiAoX2VycilcbicKICAgICAgICdcdFx0XHRyZXR1cm4gX2VycjtcbicKICAgICAgICdcdFx0cGFyYW1zX29mZiA9IGZtYW5fcG9ydF9nZXRfcGFyYW1zX3BhZ2UocG9ydCk7XG4nCiAgICAgICAnXHRcdGlmICghcGFyYW1zX29mZilcbicKICAgICAgICdcdFx0XHRyZXR1cm4gLUVOWElPO1xuJwogICAgICAgJ1x0fScpCmlmIG9sZCBpbiBzcmM6CiAgICBzcmMgPSBzcmMucmVwbGFjZShvbGQsIG5ldywgMSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAgZi53cml0ZShzcmMpCiAgICBwcmludCgiIyMjIGZtYW5fcGNkLmM6IGZlX3BvcnRfc2V0IGxhenkgcGFyYW1zIHBhZ2UgYWxsb2MgKE0yLTQpIikKZWxzZToKICAgIHByaW50KCIjIyMgZm1hbl9wY2QuYzogcGF0dGVybiBub3QgZm91bmQgKGFscmVhZHkgZml4ZWQ/KSIpCg==' | base64 -d | python3
fi
fi  # close M2-4 fman_port.c if (line 1212)

# === end ls1046a-build patch-loop replacement ===
"""

new, n = PATTERN.subn(lambda m: REPLACEMENT, src, count=1)
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
        # P4.1: copy include/soc/fsl/qman.h (QMan FQ allocation API) into the
        # snapshot so the OOT ask.ko can allocate its dedicated TX FQ.
        if [ -f "${CWD}/${KERNEL_DIR}/include/soc/fsl/qman.h" ]; then
            mkdir -p "$ASK_KSRC/include/soc/fsl"
            cp "${CWD}/${KERNEL_DIR}/include/soc/fsl/qman.h" \
               "$ASK_KSRC/include/soc/fsl/qman.h"
            echo "I: ASK2 v2 — copied include/soc/fsl/qman.h into snapshot"
        fi
        # P4.1a: copy include/soc/fsl/bman.h (BMan buffer-pool API) into the
        # snapshot so OOT ask.ko can allocate a dedicated BMan pool for the
        # hardware-enqueued TX FQ (future — BPID currently defaults to 0).
        if [ -f "${CWD}/${KERNEL_DIR}/include/soc/fsl/bman.h" ]; then
            mkdir -p "$ASK_KSRC/include/soc/fsl"
            cp "${CWD}/${KERNEL_DIR}/include/soc/fsl/bman.h" \
               "$ASK_KSRC/include/soc/fsl/bman.h"
            echo "I: ASK2 v2 — copied include/soc/fsl/bman.h into snapshot"
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

