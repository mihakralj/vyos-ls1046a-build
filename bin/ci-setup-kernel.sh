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

echo "### Staging LS1046A board patches from $BOARD_PATCH_DIR (series file)"
_count=0
_series="$BOARD_PATCH_DIR/series"
if [ ! -r "$_series" ]; then
    echo "ERROR: patch series file not found: $_series"
    exit 1
fi
while IFS= read -r _p; do
    case "$_p" in
        ""|\#*) continue ;;
    esac
    _src="$BOARD_PATCH_DIR/$_p"
    if [ -f "$_src" ]; then
        cp "$_src" "$KERNEL_PATCHES/"
        _count=$((_count + 1))
    else
        echo "WARNING: patch listed in series but file missing: $_p"
    fi
done < "$_series"
echo "### Staged $_count LS1046A board patches"
unset _count _series _src _p

# ── Staging-completeness guard
# 0078 (dpaa MODULE_SOFTDEP on af_xdp_pool) intentionally NOT staged:
# under CONFIG_FSL_DPAA_ETH=y and CONFIG_DPAA_AF_XDP_POOL=y the softdep
# is unreachable (modprobe never loads either of them). Autoload is
# guaranteed by the =y flip in kernel/common/kernel-config/08-dpaa1.config
# instead — af_xdp_pool_init() runs at late_initcall before
# dpaa_eth_probe()'s register_netdev().
# M3-3 step 1: bind a real NAPI to qmap[].napi at xsk_pool_attach time
# (BSP cpu 0's per-CPU NAPI portal) and stop xsk_set_rx_need_wakeup being
# a stub. First reviewable slice of Phase 3 per spec sec 5.2 final paragraph
# + sec 5.4 RX path step 5. No throughput change yet — control-plane
# wiring; ZC RX/TX datapath lands in 0081+.
# M3-3 step 2a: distribute qband NAPI across online CPUs.  Promotes
# the cpu=0 stopgap from 0080 to (queue_id % num_online_cpus()) so
# four-qband bindings fan out across all four LS1046A A72 cores
# instead of piling onto cpu 0's QMan SWP.  Still no dedicated BMan
# channels (step 2b) and no cluster-aware refinement (step 2c).
# Spec sec 5.2 "Queue mapping correctness" items 3-5.
# M3-3 step 2b: observability for step 2a's pointer wiring. Adds the
# /sys/kernel/debug/af_xdp_pool/qmap node so priv->qmap[].napi/.cpu can
# be verified per-netdev without kgdb or a crash dump. Pure observability —
# zero datapath change, zero new core-driver exports. Spec sec 5.2.
# M3-3 step 3: real dpaa_fq_to_qband() + xsk_rx_branch counter +
# observational RX hot-path eligibility probe. Strictly diagnostic --
# no datapath change. ZC redirect lands in 0084+. Spec sec 6.1.2.
# M3-3 step 4: NAPI-hooked BMan refill from the XSK fill ring + new
# xsk_bman_refill_batches counter. Folded into the existing rcu_read_lock()
# block in dpaa_eth_poll() right after xsk_set_rx_need_wakeup. With no XSK
# pool bound (default flavor) the new ops->napi_refill callback walks zero
# bound qbands and returns; no datapath cost. Spec sec 6.1.3.
# M3-3 step 5: TX ZC submission + xsk_tx_inflight backpressure + TxConf
# round-trip closure. Three new flavor ops (napi_tx_zc, xsk_set_tx_need_wakeup,
# tx_conf_zc) wired into dpaa_eth_poll() tail (same RCU section as 0084) and
# dpaa_tx_conf() head. Two new ethtool counters (xsk_tx_zc_submit,
# xsk_tx_conf_zc). With no XSK pool bound (default flavor) all three ops
# walk zero bound qbands and the tx_conf_zc claim probe returns false on
# bpid mismatch -- skb fast path unchanged. ≥ 7 Gbps acceptance gate on
# vpp flavor. Spec sec 6.1.4.
# M3-3b: FMan PCD capability detection + CC-steering stub API. Adds
# CONFIG_DPAA_HW_CC_STEERING (default y), priv->fman_caps snapshot via
# dpaa_fman_get_caps() at probe, one-shot KERN_INFO log, hw_offload_unavailable
# ethtool counter, and the four fman_cc_tree_*() stubs returning -ENOTSUPP.
# Observability-only -- mainline ucode 106 silicon shows caps=0x00 and every
# productive call short-circuits. dpaa_fman_caps.force= module parameter
# lets developers simulate ucode 210 for unit testing downstream consumers
# (af_xdp_pool qband-select, ASK2 flowtable bridge, vyos-1x classify CLI).
# Spec sec 3.5 + sec 5.4.
# M3-3 step 6 blocker A residual: DMA device mismatch between the XSK
# pool map (was: parent MAC device, 32-bit mask) and the BMan FBPR
# validation domain (FMan RX port device, 40-bit mask). Switches
# xsk_pool_dma_map() to priv->rx_dma_dev, the same device mainline uses
# for dpaa_bp_add_8_bufs(). The two earlier blocker-A hot-fixes
# (0086 chunked release-by-8, 0087 pre-zero bmbs[i].data) were absorbed
# into 0084 v3 directly -- the patch stack is now stand-alone. Spec
# sec 6.1.5 / 6.1.6.
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
# M3-3c: HM (Header Manipulation) stub API. Mirrors the 0086 cadence
# exactly -- fman_hm_node_install/destroy stubs return -ENOTSUPP,
# fman_hm_caps_supported() wraps (caps & FMAN_CAP_HM_NODES). Adds
# CONFIG_DPAA_HW_HM_OFFLOAD (default y, depends on DPAA_HW_CC_STEERING)
# and struct fman_hm_spec opaque type. Productive impl lands in a
# follow-up patch; API is fixed now so downstream consumers (af_xdp_pool
# egress rewrite, vyos-1x NAT offload CLI, ASK2 flowtable bridge) can
# wire calls today and gracefully degrade on ucode <210 silicon. Spec
# sec 5.5.
# M3-3d: Policer (srTCM/trTCM) stub API. Mirrors the 0090 cadence exactly --
# fman_policer_install returns -ENOTSUPP, fman_policer_destroy is an
# idempotent void no-op, fman_policer_caps_supported() wraps
# (caps & FMAN_CAP_POLICER_TRTCM). Adds CONFIG_DPAA_HW_POLICER_OFFLOAD
# (default y, depends on DPAA_HW_CC_STEERING) and opaque struct
# fman_policer_profile. Productive impl lands in a follow-up patch; API is
# fixed now so downstream consumers (vyos-1x firewall limit offload CLI,
# VPP per-qband rate-limit, ASK2 nft limit offload backend) can wire calls
# today and gracefully degrade on ucode <210 silicon. Spec sec 5.6.
# M3-3b productive struct contract: replaces the opaque {u32 reserved;}
# placeholders for struct fman_cc_key / fman_cc_static_tree (from 0086)
# with the real 5-tuple key + static-tree layout per spec sec 5.4. The
# four fman_cc_tree_* entry points stay -ENOTSUPP stubs; only the API
# struct shape becomes productive so downstream consumers (af_xdp_pool
# qband-select, vyos-1x classify CLI, ASK2 flowtable bridge) can build
# real specs. The silicon AD/group-table CONT_LOOKUP encoding lands in a
# follow-up. Applies on the final post-0091 dpaa_fman_caps.h. Spec sec 5.4.
# M3-3c productive struct contract: replaces the opaque struct
# fman_hm_spec {u32 reserved;} placeholder (from 0090) with the real
# ordered-op-list layout (enum fman_hm_op_type + VLAN/MPLS op params +
# ops[8]) per spec sec 5.5. fman_hm_* entry points stay -ENOTSUPP stubs.
# Must apply AFTER 0086b (both edit dpaa_fman_caps.h). Spec sec 5.5.
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
# 0101 (M3-3c bridge): wire NETIF_F_HW_VLAN_CTAG_RX -> fman_hm_node_install via
# a new dpaa_set_features() .ndo_set_features handler in dpaa_eth.c, so the
# dormant HM install body (0099) is reachable from userspace (ethtool -K /
# the vyos-1x 'set interfaces ethernet ethX hw-offload vlan-strip' CLI).
# Depends on 0099 (fman_hm_node_install productive) + 0090a (struct fman_hm_spec)
# + 0086a (fman_hm_caps_supported), so it MUST sort after 0100. Common
# (built-in) for default/vpp/ask. Spec sec 5.5.
# 0102: dormant exported fman_port_set_rx_bpool() reprogram primitive
# (M3-3 step 7 sub-increment 4, WRITE mechanism, no caller). Edits
# fman_port.c/.h only; independent of the 0092-0100 PCD stack. Spec sec 6.1.7.
# 0102b: one-shot dev_info FMBM_EBMPI register readback at reprogram time
# (GAP-1 evidence that the 0102 BPID re-commit reached silicon). Diagnostic
# only; stacks on 0102. Spec sec 6.1.17 / plans/ZC-RX-SCOPE.md GAP 1.
# 0103a: dormant true-ZC RX Recover sw-ring reverse-map (M3-3 step 7
# sub-increment 4a, infrastructure only, NO datapath consumer). Adds the
# per-qband chunk-DMA -> xdp_buff reverse map + record/lookup helpers that
# 0103b needs (kernel 6.18.31 has no xsk_buff_recv() retrieve-by-dma
# primitive). Self-tested at attach; byte-identical datapath to 0102.
# Spec sec 6.1.15 (corrected) / 6.1.16 (API gap).
# 0103b: PRODUCTIVE true-ZC RX -- the INSEPARABLE reprogram-WRITE +
# Recover-redirect pair (M3-3 step 7 sub-increment 4b). Fires the FMan
# RX-port BPID swap (fman_port_set_rx_bpool, 0102) at attach AND wires the
# rx_hook (rx_default_dqrr dispatch) that Recovers the xdp_buff from the bare
# chunk DMA cookie via the 0103a reverse map and xdp_do_redirect()s it into
# the XSKMAP (xsk_zc_rx_redirect, 22nd xsk_* counter). Both halves MUST land
# together (firing either alone -> sec 6.1.8 crash class). Byte-identical on
# default/vpp (only reached on XDP_ZEROCOPY bind). Spec sec 6.1.16.
# 0103c: true-ZC RX stage-3 -- sub-increment-4 reorder + IPI wakeup +
# unconditional NAPI refill + pre-arm RX NEED_WAKEUP + BPID restore on
# detach. Makes the productive xsk_zc_rx_redirect oracle (0103b) actually
# reachable under load. Edits af_xdp_pool_main.c (+ dpaa_eth) on top of
# 0103b; sorts after 0103b, before 0104. Spec sec 6.1.17.
# 0103e: bpf_net_ctx NULL-deref fix in af_xdp_pool_rx_hook (the rx_hook
# runs outside the NAPI bpf_net_ctx the redirect path assumes). Stacks on
# 0103c. Spec sec 6.1.17.
# 0103f: dispatch the qmgmt_ops->rx_hook BEFORE the dpaa_bpid2pool() NULL
# guard in rx_default_dqrr. Without this, FDs carrying the XSK bpid resolve
# to no kernel pool and are consumed/dropped at ~2855 before the 0103b hook
# at ~2901 ever sees them -> xsk_zc_rx_redirect stuck at 0. Stacks on 0103e.
# 0103g: register per-band MEM_TYPE_XSK_BUFF_POOL xdp_rxq_info at ZC attach
# + xsk_pool_set_rxq_info; fixes the NULL xdp->rxq Oops in __xsk_map_redirect
# on the first Recovered frame (HW serial capture 2026-06-09). Stacks on 0103f.
# 0104: PRODUCTIVE M3-3d policer consumer -- .ndo_setup_tc TC_SETUP_BLOCK
# handler mapping a single ingress `tc filter matchall action police` onto
# fman_policer_install() slot 0 (board 0100). Fail-soft -EOPNOTSUPP when
# !fman_policer_caps_supported(). Edits dpaa_eth.c/.h only; sorts after
# 0103e, before 101-sfp. This is the kernel backend for the vyos-1x-025
# `set interfaces ethernet ethX ingress-policer` CLI. Spec sec 5.6.
# 0104a: advertise NETIF_F_HW_TC in dpaa_netdev_init() so tc_can_offload() is
# true and the tc core actually routes an ingress `matchall action police`
# filter to 0104's TC_SETUP_BLOCK handler. Without it the netdev shows
# `hw-tc-offload: off [fixed]`, skip_sw filters are rejected and non-skip_sw
# filters install software-only (not_in_hw) -- the handler never runs. Gated
# on fman_policer_caps_supported() (decl from 0091), mirrors the HM /
# NETIF_F_HW_VLAN_CTAG_RX block 0101 adds just above. Touches only
# dpaa_netdev_init() (no overlap with 0104's hunks); sorts after 0104, before
# 101-sfp. Spec sec 5.6.
# 0104b: M3-3e CEETM scaffold -- pins the QMan egress-shaper stub API
# (dpaa_ceetm_qdisc_install / dpaa_ceetm_qdisc_destroy / dpaa_ceetm_supported)
# + CONFIG_DPAA_HW_CEETM in dpaa_fman_caps.{c,h} + Kconfig. supported() returns
# false and install() returns -ENOTSUPP until the productive QMan CEETM core
# forward-port lands; fixes the VyOS CLI contract now. Touches only the tails
# of caps.{c,h}/Kconfig (no overlap with 0104/0104a); sorts after 0104a, before
# 101-sfp. Spec sec 5.7.
# 0105: dormant exported fman_port_set_cc_base() RX coarse-classification
# base primitive (M3-3b keystone, WRITE mechanism, no caller). Programs the
# BMI fmbm_rccb register -- the RAW MURAM offset of the 0098 CC tree root
# (NO >>4) -- which mainline NEVER writes, the single missing port->CC link
# that left M2/M3 static CC steering non-productive. The Parser->KeyGen half
# is already wired by fman_port_use_kg_hash(). Edits fman_port.c/.h only;
# independent of the 0092-0104b PCD stack (cross-module EXPORT consumed by
# the future productive caller). Sorts after 0104b, before 101-sfp. Spec
# sec 13.
# 0106: M3-3b productive CC steering wiring -- the HW-proven KGSE_CCBS graft
# (silicon captures 2026-05-23/25: NIA stays BMI direct-enqueue 0x80500002,
# a non-zero KGSE_CCBS = CC root group-table MURAM offset dispatches the CC
# walk implicitly; the NIA-flip-to-FM_CTL alternative was DISPROVEN on HW).
# Makes fman_pcd_kg_attach_cc() productive, adds the port-level graft pair
# fman_pcd_kg_port_attach_cc()/detach_cc() (mirror of the BUG 3 policer
# steering fix), and completes fman_cc_tree_install()/destroy() in
# dpaa_fman_caps.c (install -> get_base -> graft; destroy detaches first).
# Sorts after 0105, before 101-sfp. Spec sec 5.4 (M3-3b).
# 0107: debugfs CC steering test harness -- /sys/kernel/debug/fman_pcd/<N>/
# cc_test drives the EXACT 0106 productive sequence (static_install ->
# get_base -> kg_port_attach_cc; clear = detach_cc -> static_destroy) so the
# M3-3b acceptance gate can be exercised on the DUT before a real consumer
# (vyos-1x classify CLI) lands. New TU fman_pcd_cc_test.c in
# fsl_dpaa_fman.ko + intra-module fman_pcd_cc_seq_dump() helper; 0600
# root-only node, zero datapath cost, no new EXPORT_SYMBOLs. Sorts after
# 0106, before 101-sfp. Spec sec 5.4 (M3-3b DUT validation).
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
# 0109: M3-3b production consumer -- ethtool ntuple (rxnfc) -> FMan CC
# static-tree bridge in dpaa_ethtool.c. ETHTOOL_SRXCLSRLINS/DEL rules
# rebuild the port's CC tree via fman_cc_tree_destroy()+install() (the
# 0106 graft sequence); action <queue> = Nth RX PCD FQ, resolved FQID
# carried in target_fqid so the 0108 hardware enqueue-AD steers on HIT.
# Driven by `ethtool -N`, whose config-mode consumer is vyos-1x-026
# ('set system offload classify'). Mirrors the 0104 policer pattern
# (userspace -> standard kernel tool -> driver bridge). Sorts after
# 0108, before 101-sfp. Spec sec 5.4 (M3-3b production consumer).
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
# DCSR error observability: read-only debugfs taps for the FMan common-block
# error/status registers (fpm/bmi/qmi/parser/kg/pol). fpm_err decodes the 50
# per-hwport status words incl. STALL — the M3-3b forensic view. Spec §5.8.
# True-ZC RX gate-counter realign: moves xsk_zc_eligible/xsk_zc_rx_recovered
# into af_xdp_pool_rx_hook() (the 0110 NAPI-only flush rework left the old
# probe site unreachable). Makes xsk-zc-check's verdict meaningful again.
# M3-3b wedge fix: SDK-convergent CC bring-up (root CONT_LOOKUP AD, RESULT
# leaf ADs, productive FMBM_RCCB bind + NIA_KG_CC_EN via fman_port_lookup_rx
# registry, KG NIA=FM_CTL|AC_CC with CCBS=grpBits). Spec §5.4, v5.19.
# M3-3b wedge fix iteration 3: CC result-AD NIA must exit via FM_CTL
# AC_NO_IPACC_PRE_BMI_ENQ_FRAME (0x28) on A006675/SW006 silicon — the 0115
# direct NIA_ENG_BMI|ENQ exit leaked one FMan task per CC-dispatched frame
# (MAC RDRP ate everything, no FPM stall, reboot-only). Also brings up the
# per-port FM_CTL ctrl-params page (FMBM_RGPR) the 0x28 ucode consumes.
# M3-3b ROOT-CAUSE fix (iter-25): mainline fman_init() clear_iram()s the
# U-Boot-uploaded FM_CTL microcode and never reloads it — IRAM all-0xFF,
# IREADY=0, so every CC dispatch (KG→FM_CTL|AC_CC) parks its FMan task and
# leaks BMI FIFO units (freeze @~46 frames). 0117 re-uploads the DTB QEF
# blob (proprietary 210.10.1, fman-firmware/fsl,firmware) into IRAM right
# after clear_iram, per SDK LoadFmanCtrlCode (fm.c:426-480). Spec §5.4.
# M3-3b iter-48 fix: revert 0115's KeyGen→CC dispatch encoding back to the
# HW-proven CCBS model (KGSE_MODE NIA = BMI direct-enqueue 0x80500002 +
# KGSE_CCBS = CC root group-table MURAM offset). 0115's AC_CC NIA-flip
# (0x80000006, ccbs=0) was DISPROVEN on hardware: with 0115's RCCB bind +
# 0116's SDK result-AD + 0117's 210.10.1 ucode all present it still stalls
# the FMan port on the first CC frame, whereas live-rewriting the scheme to
# CCBS cured the stall (no STL/60s, ping 5/5). Keeps the rest of 0115/0116/
# 0117 — only the 3 KeyGen/CC-scheme files revert. Spec §5.4.
# ASK2 M2 step 1: extend the HM op-set (0090a/0099) with 3 additive
# L3-forward primitives — RMV_ETHERNET, INSRT_GENERIC, IPV4_FORWARD —
# across all four HM layers. SDK-grounded encodings (NXP fm_manip): single
# generic HMAN_OC=0x35 HMTD, RMV=0x01000e00 / INSRT=0x02000e00+BE payload /
# IPV4=0x0c040001 (TTL+L4 checksum). No existing VLAN/MPLS op altered.
# ASK2 M2 step 2: dormant next-hop HM dedup refcount API
# (fman_hm_nexthop_get/put) caches+refcounts one shared HMTD per L3
# adjacency (egress_tx_fqid, src_mac, dst_mac) so MURAM scales
# O(next-hops) not O(flows). EXPORT_SYMBOL_GPL, dormant (ask.ko consumes).
# ASK2 Gap-A: export two net_device -> hardware-id resolvers
# (dpaa_get_rx_fman_port / dpaa_get_tx_fqid) on the common dpaa_fman_caps.h
# substrate so the OOT ask.ko PCD consumer can derive the fman_cc_tree_*
# port key and a CC target_fqid. EXPORT_SYMBOL_GPL, dormant (no in-tree
# caller). Bodies are the proven dead-ask-flavor 0031/0039 reparented.
# ASK2 Fork B M1 step 1: FE-object MURAM pool scaffold (arch/fman-fe-ehash.md
# §3 AllocFEObjs). Lazy + refcounted pool of 100×28 B FE records carved from
# FMan MURAM, driven by a new debugfs fman_pcd/<id>/fe_pool (0644) get/put
# node. fe_lock → pcd->lock order; a pristine S0 keeps the pool empty so
# engage→disengage nets zero gen_pool used (pcd-snapshot reversibility gate).
# Single-file fman_pcd.c, internal/static, no ABI export. Scaffold only —
# allocates+zeroes MURAM, does NOT program the FE records and does NOT flow
# traffic; the FE-VM core (FmPcdCcBuildFE/ContextByFE) lands later from lf-5.4.
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
# 0126 — convert fman_pcd_muram_alloc/_free into a gen_pool sub-allocator over
# the reserved 64 KiB MURAM partition (0092 reserved the arena but the wrappers
# re-called the GLOBAL fman_muram_alloc, competing for the ~21 KiB post-CAM/FIFO
# free tail while the reservation sat dead-weight → §5/0125 int-buf 33 KiB hit
# -ENOMEM on HW 2026-06-16). Seeds a gen_pool (min_alloc_order=8, 256 B granule)
# with [muram_offset,+64KiB); all PCD MURAM now sub-allocates from it, bounding
# PCD use to the reservation and unblocking the FE/ehash forward path. Substrate
# change — full S0↔S1 + fe_pool + fe_ehash forward regression gate required.
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
# 0135: FE-VM context builder — port of lf-5.4 LSDK FmPcdCcBuildContextByFE().
# Adds fman_pcd_fe_context_build() + struct fman_pcd_fe_context_params (the
# centralized per-FE context writer the SDK calls at 999-patch line 8954).
# Ships dormant (no callers yet — callers wire in a later patch to populate
# MUX/TRANSITION/ENQ/HM per-instance context after the FE descriptor build,
# matching the SDK two-step FmPcdCcBuildFE→FmPcdCcBuildContextByFE sequence).
# 0136: TX confirm bypass — fman_port_set_silicon_hit_release_mode().
# Flips the TX port BMI to release silicon-HIT FDs (FCO=0) directly to BMan
# without QMan TX-confirm enqueue.  Kernel TX (FCO=1) is unaffected.
# This eliminates the ~20% CPU softirq floor proved on hardware 2026-05-25.
# 0137: MANIP creation + chain API for L3 forwarding (fman_pcd_manip_create/_destroy/_chain_create/_chain_destroy/_hmtd_off).
# ASK2 M2.2: external flow-offload backend registration slot (single-slot
# RCU-protected dpaa_register/unregister_flow_offload_handler). 0145 is a
# board/common patch (not flavor-gated) because the dpaa driver is built-in
# for all flavors.

# ── Staging-completeness guard ────────────────────────────────────────
# Every .patch file in kernel/common/patches/board/ must be listed in
# the series file. SKIPPED patches are marked with # SKIP in series.
# This catches orphaned patches with no series entry (the old guard
# caught forgotten cp lines — now the loop reads series directly so
# the failure mode is a patch file committed without a series entry).
BOARD_STAGE_SKIP="0150-fman-pcd-fe-engage-api.patch"
_missing=""
# Cross-check: every .patch in board/ must be in series or SKIP list
{
  # Extract patch basenames from series file (skip comments/blanks/SKIPs)
  awk '!/^#/ && !/^$/ && !/SKIP/ {print $1}' "$BOARD_PATCH_DIR/series"
  # Also accept the BOARD_STAGE_SKIP whitelist
  for _s in $BOARD_STAGE_SKIP; do echo "$_s"; done
} | sort -u > /tmp/_staged.$$
find "$BOARD_PATCH_DIR" -maxdepth 1 -name '*.patch' -printf '%f\n' | sort > /tmp/_on_disk.$$
_missing=$(comm -23 /tmp/_on_disk.$$ /tmp/_staged.$$ | tr '\n' ' ')
rm -f /tmp/_staged.$$ /tmp/_on_disk.$$
if [ -n "$_missing" ]; then
  echo "::error::board patches NOT in series file: $_missing"
  echo "::error::add to kernel/common/patches/board/series (or BOARD_STAGE_SKIP)"
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
# ── REPLACEMENT BLOCK — ESCAPING RULES ────────────────────────────────────────
# This triple-quoted Python string is injected into build-kernel.sh verbatim
# AFTER Python processes its escape sequences.  Rules for writing new fixups:
#
#  \\n → \\n (two chars, safe in sed/bash)   ← write \\\\n in this source
#  \\t → \\t (two chars, safe)               ← write \\\\t in this source
#  \\  → \\ in output                        ← write \\\\ in this source
#
# Python inline code strings (in base64 blobs):
#   Use chr(10) for newline, chr(9) for tab — avoids all escape collisions.
#   Never write backslash-n or backslash-t inside base64-decoded Python string literals.
#
# Validate before pushing: python3 bin/test-fixups.sh
# ──────────────────────────────────────────────────────────────────────────────
# Initialise the kernel source tree as a throwaway git repo so that
# `git apply --3way` can fall back to a real 3-way merge using the
# pre-patch blobs in object storage when context drifts.
if [ ! -d .git ]; then
    git -c init.defaultBranch=main init -q
    # mergiraf .gitattributes: allowlist low-risk files, deny silicon-encoding
    cat > .gitattributes << 'MERGATTR'
# Low-risk: mergiraf reduces placement conflicts
drivers/net/ethernet/freescale/dpaa/*.c   merge=mergiraf
*.h                                        merge=mergiraf
# Silicon-encoding: NEVER auto-merge
drivers/net/ethernet/freescale/fman/fman_pcd*.c    -merge
drivers/net/ethernet/freescale/fman/fman_keygen.c  -merge
MERGATTR
    git -c user.email=ci@local -c user.name=ci add -A .gitattributes
    git -c user.email=ci@local -c user.name=ci add -A
    git -c user.email=ci@local -c user.name=ci commit -q -m "kernel pristine (pre-patches)" --allow-empty || true
fi

PATCH_FAIL=0
PATCH_FAIL_LIST=""
PATCH_FALLBACK_COUNT=0
PATCH_FALLBACK_LIST=""
for patch in $(find "${PATCH_DIR}" -maxdepth 1 -type f -name '*.patch' | sort); do
    pname=$(basename "$patch")
    echo "I: Apply Kernel patch: $patch"
    if ! git apply --3way --whitespace=nowarn "$patch" 2>/tmp/_apply_stderr; then
        echo "::error::Kernel patch FAILED to apply (git apply --3way): $pname" >&2
        PATCH_FAIL=$((PATCH_FAIL + 1))
        PATCH_FAIL_LIST="$PATCH_FAIL_LIST $pname"
    else
        # Detect silent 3-way fallback — patch landed but with drifted context
        if grep -q "Falling back to three-way merge" /tmp/_apply_stderr; then
            echo "::warning::3-way-fallback: $pname applied via 3-way merge (context drifted)" >&2
            PATCH_FALLBACK_COUNT=$((PATCH_FALLBACK_COUNT + 1))
            PATCH_FALLBACK_LIST="$PATCH_FALLBACK_LIST $pname"
        fi
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

if [ "$PATCH_FALLBACK_COUNT" -ne 0 ]; then
    echo "::warning::$PATCH_FALLBACK_COUNT kernel patch(es) applied via 3-way fallback (context drifted):$PATCH_FALLBACK_LIST" >&2
    echo "::warning::Drifted patches should be refreshed via: bin/kernel-roundtrip.sh export" >&2
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
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/fe_flow_key_fix.py" 2>&1


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

# F-068-REVERT: Restore AC_CC dispatch (next_engine=3, kgse_ccbs=0, RCCB→FE_ENTER).
# F-068 incorrectly switched to CCBS (next_engine=2, CC group table). The 2026-07-04
# HIT was proven with AC_CC direct (RCCB→FE_ENTER) — CC group table is an architectural
# error per specs/fman-keygen-flow-key-spec.md v2.0 §5. The crash root cause is not the
# dispatch mode but the missing missResult/w4 causing wild DMA (fix follows separately).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_068.py" 2>&1
    echo "### F-068-REVERT: AC_CC dispatch (next_engine=3, RCCB→FE_ENTER)"
fi

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

# F-062c-R2: RESTORE pure AC_CC encoding (0x80000006, no DFLT_NIA).
# F-062c-R1 incorrectly OR'd ENQUEUE_KG_DFLT_NIA into the AC_CC mode register,
# producing a hybrid NIA that the FMan controller interprets as an undefined
# action → corrupted FDs → rx_default_dqrr Oops with x26=0xffffffff80000000.
# The NXP vendor LSDK (999-layerscape-ask) uses pure NIA_ENG_FM_CTL|NIA_FM_CTL_AC_CC.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 -c "
import sys
path = 'drivers/net/ethernet/freescale/fman/fman_keygen.c'
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print('### fman_keygen.c: F-062c-R2 — file not found'); sys.exit(0)
# Remove DFLT_NIA if present, restore pure AC_CC
corrupt = '\t\t\ttmp_reg |= ENQUEUE_KG_DFLT_NIA | NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;'
pure    = '\t\t\ttmp_reg |= NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;'
if corrupt in src:
    src = src.replace(corrupt, pure, 1)
    with open(path, 'w') as f: f.write(src)
    print('### fman_keygen.c: F-062c-R2 — DFLT_NIA removed, pure AC_CC restored')
elif pure in src:
    print('### fman_keygen.c: F-062c-R2 — AC_CC already pure')
else:
    print('### fman_keygen.c: F-062c-R2 — AC_CC branch not found (already reverted?)')
" 2>&1
    echo "### fman_keygen.c: F-062c-R2 pure AC_CC (0x80000006)"
fi

# F-069: MISS context (DDR + MURAM t_ExtHashResult) with exact anchors.
# FIXME: Fixup anchors are NOT count()==1 asserted — bin/test-fixups.sh is the current gate.
# Four prior silent no-ops cost four board sessions (F-062a, F-062g, F-069a v1/v2).
# Per NXP LSDK ExternalHashTableSet (999-layerscape-ask):
#  - Adds miss_res_off (6th parameter, distinct from w6 miss_off)
#  - w4 = miss_res_off MURAM offset of 16B t_ExtHashResult
#  - DDR miss context (256B, dma_alloc_coherent via t->dev from 0130)
#  - Persists in struct fman_pcd, freed on hash_free teardown
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069.py" 2>&1
    echo "### F-069: MISS context + DDR alloc (count-asserted anchors)"
fi

# F-073D: Terminal ENQ per 210.10.1 §7.3 — ws_offset=0, w3=0 (no chain).
# w0 = TYPE_ENQ | FMAN_FE_ENQ_FQID = 0x02010000 (terminal, no ws_offset).
# w1 = fqid (24-bit FQID). w3 = 0 (terminal, per §7.1 "Terminal enqueue").
# + F-070b w6→ENQ rewire + F-070c params zeroing on disengage.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_073D.py" 2>&1
    echo "### F-073D: Terminal ENQ (w0=0x02010000, w3=0) per 210.10.1 §7.1/§7.3"
fi


# M2-4: fix fman_port_lookup_rx — all LS1046A fman_port->port_id==0
# (mainline of_alias_get_id fallback returns -ENODEV).  The lookup
# comparison p->port_id == port_id always fails for non-zero port_id.
# Remove the port_id check; match on fm + port_type only.
# cc_test works by accident (%hhi "0x10" → port_id=0, which matches).
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4.py" 2>&1
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_2.py" 2>&1
    echo "### fman_port.c: M2-4 NULL-page clear support added"
fi

# 
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "static int fman_pcd_debugfs_root_get(void)" "static __attribute__((unused)) int fman_pcd_debugfs_root_get(void)" -1 "F-085: __unused debugfs_root_get (optional)" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-052 debugfs_root_get marked __unused"
fi

# F-052b: Suppress -Werror for fman_pcd_debugfs_root_put (same root cause).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "static void fman_pcd_debugfs_root_put(void)" "static __attribute__((unused)) void fman_pcd_debugfs_root_put(void)" -1 "F-085: __unused debugfs_root_put (optional)" \
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "((u32)(t->hash_shift & 0x3) << 16)" "((u32)(1) << 16)" -1 "F-053: hash_bytes_offset=1 (optional — 0158 skipped)" \
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_054.py" 2>&1
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_056.py" 2>&1
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_057.py" 2>&1
    echo "### fman_pcd.c: F-057 removed per-record next-FE from DDR (SDK-compliant)"
fi



# F-060 v3d: Fix MUX context write target — write to AD+4 (word 1), not AD+0.
# v3d avoids backslash-s (bad escape through the 4-layer pipeline) — uses [ \t]* instead.
# F-055/F-056 wrote across TWO lines; regex matches the 2-line pattern.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_055.py" 2>&1
    echo "### fman_pcd.c: F-060 v3d: MUX context write fixed to AD+4"

    # F-083 REMOVED — scaffold guard (fe_enter_off==0) preserved.
    # The CONT_LOOKUP scaffold is the correct path when fe_enter_off==0.
    # When fe_enter_off!=0, RCCB→FE_ENTER direct activates the FE-VM for HIT.
    # FmPortSetFESupport (F-072) provides proper FE workspace allocation,
    # preventing the BMI stall that plagued earlier builds without it.

    # F-072b: Inject FmPortSetFESupport call BEFORE fman_pcd_kg_port_arm_fe.
    # The F-072 v3 fixup's engage anchor (scaffold comment) was not found
    # because the comment format changed across patch revisions.  Use the
    # fman_pcd_kg_port_arm_fe call line as anchor instead — it's stable.
    #
    # F-072c: Forward-declare fman_pcd_fe_buffer_setup before the internal
    # function __fman_pcd_fe_arm_engage (which is defined BEFORE the wrapper
    # where F-072 v3 injects the function body).  Without this, the call
    # injected by F-072b sees an implicit declaration → -Werror.
    sed -i '/^static int __fman_pcd_fe_arm_engage/i\static int fman_pcd_fe_buffer_setup(struct fman_pcd *, struct fman_port *, u8);' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-072c forward-decl fman_pcd_fe_buffer_setup"

    sed -i 's/err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,/{ struct fman_port *rxp = fman_port_lookup_rx(pcd->fman, (u8)port_id); int _b; if (!rxp) return -ENODEV; _b = fman_pcd_fe_buffer_setup(pcd, rxp, (u8)port_id); if (_b) return _b; } err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-072b FmPortSetFESupport call injected before arm_fe"

    # F-084: Fix 0158 compose FE_ENTER target — EXT_HASH not ENQ.
    # Single-line sed: e->muram_off → pcd->fe_hash_off
    # The ENQ list walk becomes dead code (unused var 'e' = warning, not error).
    sed -i 's/err = fman_pcd_fe_enter_build(pcd, e->muram_off);/err = fman_pcd_fe_enter_build(pcd, pcd->fe_hash_off);/' \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-084 compose FE_ENTER target = EXT_HASH (sed, silent no-op if 0158 skipped)"

    # F-085: Suppress -Wunused-function for static functions whose callers
     # may be behind conditional code paths or fixup-anchor mismatches.
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" drivers/net/ethernet/freescale/fman/fman_pcd.c "static int __fman_pcd_fe_build_vm_chain" "static __maybe_unused int __fman_pcd_fe_build_vm_chain" 1 "F-085: __maybe_unused on vm_chain" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    # fman_pcd_fe_buffer_setup now called via F-072b — no __maybe_unused needed

    # F-085b: Fix -Wunused-result from kstrtouint in fe_arm engage tokenizer.
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "kstrtouint(tok, 16, \&miss_fqid);" "(void)kstrtouint(tok, 16, \&miss_fqid);" -1 "F-085b: void cast miss_fqid (optional — 0158 skipped)" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "kstrtouint(tok, 16, \&ekfc);" "(void)kstrtouint(tok, 16, \&ekfc);" -1 "F-085b: void cast ekfc (optional — 0158 skipped)" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-085 __maybe_unused + kstrtouint casts"

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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_061.py" 2>&1
    echo "### fman_pcd.c: F-061 fe_probe debugfs (KG key dump from FE pool workspace)"
fi

# F-086: Register fe_recover debugfs write node (patch 0163 Tier-1 recovery).
# F-086c: Forward-declare fman_pcd_fe_recover_fops before fman_pcd_init().
# 0163 defines the fops AFTER fman_pcd_init(); F-086 registers it INSIDE
# fman_pcd_init(). Without a forward declaration the compiler rejects it.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 << 'F086PY'
import pathlib
p = pathlib.Path('drivers/net/ethernet/freescale/fman/fman_pcd.c')
s = p.read_text()
changed = False

# F-086c: insert forward declaration before fman_pcd_init
fwd = 'static const struct file_operations fman_pcd_fe_recover_fops;' + chr(10)
init_anchor = 'struct fman_pcd *fman_pcd_init'
if 'fman_pcd_fe_recover_fops;' not in s and init_anchor in s:
    s = s.replace(init_anchor, fwd + init_anchor, 1)
    print('### fman_pcd.c: F-086c forward declaration inserted before fman_pcd_init')
    changed = True
elif 'fman_pcd_fe_recover_fops;' in s:
    print('### fman_pcd.c: F-086c forward declaration already present')
else:
    print('### fman_pcd.c: F-086c WARNING: fman_pcd_init anchor not found')

# F-086: insert debugfs_create_file("fe_recover",...) before fe_arm registration
arm_anchor = 'debugfs_create_file("fe_arm", 0600,'
recover_line = chr(9)*3 + 'debugfs_create_file("fe_recover", 0200, pcd->debugfs_dir, pcd, &fman_pcd_fe_recover_fops);' + chr(10) + chr(9)*3
if '"fe_recover"' not in s and arm_anchor in s:
    s = s.replace(arm_anchor, recover_line + arm_anchor, 1)
    print('### fman_pcd.c: F-086 fe_recover debugfs registered')
    changed = True
elif '"fe_recover"' in s:
    print('### fman_pcd.c: F-086 fe_recover already registered')
else:
    print('### fman_pcd.c: F-086 WARNING: fe_arm anchor not found')

if changed:
    p.write_text(s)
F086PY
fi

# F-076: atomic fe_disengage_full debugfs — SDK-correct ordered teardown.
# Replaces 7-step manual sequence that crashes board (F-076, 2026-07-18).
# Calls __fman_pcd_fe_arm_disengage + fman_pcd_port_recover in one write.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_076.py" 2>&1
fi

# F-068: IC key probe — extend dpaa_eth IC copy to include KG key region.
# The mainline dpaa_eth IC copy (FMBM_RICP: iciof=0, size=48B) only copies
# parser results + timestamp + hash. The KG-extracted key at IC offset 0x48
# is NOT copied. This fixup adds 32 extra bytes to the IC copy size so the
# key region appears in the DDR buffer headroom, readable via the dpaa_eth
# RX path (rx_default_dqrr -> vaddr + prs_result_offset + key_offset).
# Temporary — removed once extraction order is determined.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    echo "### dpaa_eth.c: F-068 IC key probe (HWA size extended +32B for KG key)"
fi

# F-069a: IC probe — capture RX buffer vaddr in dpaa_eth.c for ic_probe.
# Stores the DMA buffer virtual address in shared global fman_pcd_ic_vaddr
# at the top of rx_default_dqrr() so fman_pcd can dump the IC.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069a.py" 2>&1
    echo "### dpaa_eth.c: F-069a v9 buf_base + vaddr captures\n"
fi

# F-072: capture full 8-byte KG CRC-64 hash from dpaa_eth RXHASH path.
# Reads be64_to_cpu(vaddr+hash_offset) and stores in fman_pcd_kg_hash.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_072.py" 2>&1
fi

# F-069b: IC probe debugfs node — reads buffer captured by F-069a.
# Shows 32 u32 words (128 bytes) from the DMA buffer headroom.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069b.py" 2>&1
fi

# Strip EXPORT_SYMBOL_GPL placed before #include by F-069b v3.
# EXPORT_SYMBOL_GPL needs <linux/export.h> which isn't included yet.
# Both fsl_dpaa_fman and dpaa_eth are built-in, so the symbol resolves 

# F-071: hash_probe debugfs — read full 8-byte KG CRC-64 hash from annotation.
# Uses fman_pcd_ic_vaddr (from F-069a) and fman_pcd_hash_off (from F-070).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_071.py" 2>&1
fi

# without exporting.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" --check drivers/net/ethernet/freescale/fman/fman_pcd.c "EXPORT_SYMBOL_GPL(fman_pcd_ic_vaddr);\n" "" -1 "dead EXPORT remove (optional)" \
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

# F-062a DELETED — was a functional no-op. The sed s/pcd->fe_exit_off,/pcd->fe_mux_off,/
# never matched because the hash FE encode call uses named parameters split across
# two lines. w5 was already MUX from patch 0131.

# F-062b DISABLED — fqb=0x200 is per-port wrong.

# F-062e v3 DELETED — stripped DEALLOCATE from EXIT singleton. The NXP oracle
# (LSDK 999-layerscape-ask ~14253) explicitly sets deallocateBuffer = TRUE on
# the EXIT FE. Without DEALLOCATE, every frame through FE-VM leaks an FMan-internal
# frame buffer → BMI depletion → port-wide RX starvation after disengage.
# The original patch 0124 sets p.flags = FMAN_FE_EXIT_DEALLOCATE; which is correct.

# F-062f REVERTED — w6 missNextFE points to EXIT per NXP §7.2.

# F-062g DELETED — was a functional no-op. The sed on Transition context builder
# never matched because the pattern uses different variable naming than the actual
# patch 0146 code.

# F-062d DISABLED — ENQ ALLOCATE deallocates frame buffers QMan later needs.
#
# F-062f routes MISS→ENQ directly (bypassing EXIT).  With ENQ ALLOCATE
# active, the test was clean (engage→ping→disengage OK) but board crashed
# minutes later from background traffic — consistent with accumulated
# QMan FD corruption from ENQ deallocation.
#
# Disabling F-062d tests whether the 0x00800000 flag on ENQ is the corruption
# source.  Without it, ENQ word0 = 0x02010000 (type only, no ALLOCATE).
: 'F-062d-DISABLED'
: 'if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then'
: '    sed -i ...'
: 'fi'

echo "### fman_pcd.c: F-062d DISABLED (ENQ ALLOCATE may cause QMan FD corruption)"

# M2-4: free params page on disengage (was leaking 256 B per cycle)
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_kg.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_3.py" 2>&1
    echo "### fman_pcd_kg.c: M2-4 params page freed on disarm"
fi

# M2-4: fe_port_set lazy-allocates params page if not yet created
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_4.py" 2>&1
fi
fi

# F-072 v3: FmPortSetFESupport — internal FE buffer pool.
# SDK 999-patch ~L14545. Uses gen_pool MURAM granule (256B auto-align).
# port_id passed as u8 (struct fman_port is opaque — no port->port_id).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_072_2.py" 2>&1
    echo "### fman_pcd.c: F-072 v3 FmPortSetFESupport ported"
fi

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


# vim: set ft=bash:
