#!/bin/bash
# ci-setup-kernel.sh — Kernel config overrides and build-kernel.sh injection
# Called by: .github/workflows/auto-build.yml "Setup kernel config" step
# Expects: GITHUB_WORKSPACE set
#
# ASK2 (rewrite-in-progress): the legacy ASK_KERNEL_TAG env var and the
# ci-consume-ask-kernel.sh / ci-setup-kernel-ask.sh helpers were deleted on
# the ask20 branch along with the ASK 1.x SDK kernel stack. This script
# now runs unconditionally in the single-image build. The
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

# Append all LS1046A kernel config fragments from the
# canonical location kernel/common/kernel-config/. Files are numbered
# (00-board.config .. 08-dpaa1.config) so a plain glob expansion sorts
# alphabetically into the intended load order. kernel/ask/kernel-config/
# holds an opt-in kunit fragment only, and is NOT picked up
# here. ASK2 (per specs/ask2-rewrite-spec.md) does not currently
# add any ASK-specific kernel-config fragments; if it grows them they
# would live under kernel/ask/kernel-config/ and need explicit
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

# INA234 hwmon patch (formerly kernel/ask/patches/fixes/4002-*) was
# only meaningful on the kernel 6.6 line, since INA234 is upstream from
# kernel 6.10 onwards. The build tracks 6.18+, so the patch
# is unnecessary. ASK2 (rewrite-in-progress) tracks the same 6.18+
# kernel per specs/ask2-rewrite-spec.md — no
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
# Failure mode (observed 2026-05-11): a prior ASK-flavor build on the same
# runner workspace left 003-ask-kernel-hooks, 4002-hwmon-ina2xx,
# 4003-sfp-rollball-phylink-einval-fallback (legacy name of current 101) and
# 4004-swphy-support-10g-fixed-link-speed in $KERNEL_PATCHES. They were then
# applied alphabetically alongside the current patches by
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
# pool bound the new ops->napi_refill callback walks zero
# bound qbands and returns; no datapath cost. Spec sec 6.1.3.
# M3-3 step 5: TX ZC submission + xsk_tx_inflight backpressure + TxConf
# round-trip closure. Three new flavor ops (napi_tx_zc, xsk_set_tx_need_wakeup,
# tx_conf_zc) wired into dpaa_eth_poll() tail (same RCU section as 0084) and
# dpaa_tx_conf() head. Two new ethtool counters (xsk_tx_zc_submit,
# xsk_tx_conf_zc). With no XSK pool bound all three ops
# walk zero bound qbands and the tx_conf_zc claim probe returns false on
# bpid mismatch -- skb fast path unchanged. ≥ 7 Gbps acceptance gate on
# VPP dataplane. Spec sec 6.1.4.
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
# Forward-port of the ask20 0004 skeleton re-anchored to
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
# caller). Bodies are the proven retired-ASK-tree 0031/0039 reparented.
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
# Forward-ported VERBATIM from kernel/ask/patches/0001-caam-qi-share.patch,
# which was NEVER staged after the 2026-06-14 flavor collapse killed the dead
# ask-only gate. Touches ONLY drivers/crypto/caam/* + a new header — zero
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
# board/common patch because the dpaa driver is always built-in.

# ── Staging-completeness guard ────────────────────────────────────────
# Every .patch file in kernel/common/patches/board/ must be listed in
# the series file. SKIPPED patches are marked with # SKIP in series.
# This catches orphaned patches with no series entry (the old guard
# caught forgotten cp lines — now the loop reads series directly so
# the failure mode is a patch file committed without a series entry).
# 2026-08-04: 0127/0128/0129 filenames below are untracked on-disk WIP that
# collides with the REAL, already-committed, in-series patches of the same
# number (0127-fman-pcd-fe-vm-enq-root.patch, 0128-fman-pcd-fe-vm-flow-
# insert.patch, 0129-fman-pcd-offload-engage.patch — unrelated FE-VM/ehash
# work). Content looks like a genuine MURAM segregated-fit allocator +
# Risk #13 fix, but it was never renumbered/reviewed, so it is excluded
# here rather than either overwriting the real 0127-0129 or guessing new
# numbers for unreviewed content. 0138 has no number collision but is
# skipped alongside it for the same reason (unreviewed, depends on 0128/0129
# APIs). Revisit: renumber to free slots (0165+) and review before staging.
BOARD_STAGE_SKIP="0150-fman-pcd-fe-engage-api.patch 0127-fman-pcd-cc-node-slab.patch 0128-fman-pcd-muram-segpool.patch 0129-fman-pcd-muram-largest-free.patch 0138-fman-pcd-manip-frag-check.patch"
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

# Stage critical kernel fix:
#   120-perf-libperf-asm-headers-srctree.patch — fixes arm64 perf build
#   failure ("No rule to make target ... tools/perf/libperf/arch/arm64/
#   include/generated/uapi/asm/unistd_64.h"). Required on kernel 6.18+.
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
#   does).
NF_FLOW_LOG_PATCH="$COMMON_FIXES_DIR/130-nf-flow-offload-log-alloc-failure.patch"
if [ -f "$NF_FLOW_LOG_PATCH" ]; then
    echo "### Staging $(basename "$NF_FLOW_LOG_PATCH") (PR14o nf_flow_table_offload alloc-failure diagnostic)"
    cp "$NF_FLOW_LOG_PATCH" "$KERNEL_PATCHES/"
else
    echo "WARNING: $NF_FLOW_LOG_PATCH missing — PR14o REPLACE-delivery diagnostic disabled"
fi

### ASK2 in-tree kernel patches: none.
#
# There is no flavor-gated patch bucket any more. This block used to stage
# kernel/ask/patches/*.patch under FLAVOR=ask, renaming them to 1xxx- to dodge
# vyos-build's reserved upstream 0001-*/0003-* filenames. Two things killed it:
# the ASK-specific patches were archived on 2026-06-21 once the common board
# series (kernel/common/patches/board/0092-0164) absorbed the PCD/HM/CC
# features, and the flavor split itself was retired on 2026-06-14 — so the
# FLAVOR=ask gate never fired again. Removed 2026-07-26 along with FLAVOR.
#
# ASK2's kernel surface is now entirely: kernel/common/patches/board/ (in-tree)
# plus kernel/ask/oot-modules/ask/ (ask.ko, out-of-tree).

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
END_MARKER = "# === end ls1046a-build patch-loop replacement ===\n"

# 2026-08-05: the SENTINEL-present check used to be a hard no-op ("already
# replaced, skip"). That made every REPLACEMENT edit below (a new F_XXX.py
# fixup, say) silently never apply on a persistent local dev-build machine
# that already ran ci-setup-kernel.sh once before -- build-kernel.sh stays
# frozen at whatever REPLACEMENT looked like on the FIRST run, forever,
# with no error and no indication anything was skipped. Root-caused this
# 2026-08-04 (a 1269-line-stale build-kernel.sh silently dropped F-152
# through F-159 across multiple local builds) and again 2026-08-05 (a
# freshly-added F-160 silently skipped the same way, one build cycle after
# the first fix). A fresh GitHub Actions runner never hits this (always a
# clean clone), which is why it went unnoticed there. Fix: when SENTINEL
# is present, strip the previously-injected block back out (SENTINEL
# through END_MARKER) so the match-and-replace below can run fresh EVERY
# time, keeping build-kernel.sh in sync with the current REPLACEMENT
# content rather than whatever was first injected.
reinject_at = None
if SENTINEL in src:
    start = src.index(SENTINEL)
    end_marker_pos = src.index(END_MARKER, start)
    end = end_marker_pos + len(END_MARKER)
    src = src[:start] + src[end:]
    reinject_at = start
    print(f"### {bk}: previously-injected patch loop found — stripping for a fresh re-inject")

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

# Sanitise the persistent tree before re-applying the series. Prior runs
# can leave uncommitted fixup edits and .rej droppings behind; they must
# never leak into the per-patch "applied:" commits that later runs use as
# --3way merge bases (a leaked edit silently rewrites the merge base and
# downstream patches stop applying -- ARM64-runner2 failure 2026-08-14).
git -c user.email=ci@local -c user.name=ci reset -q --hard || true
git clean -fdxq || true

PATCH_FAIL=0
PATCH_FAIL_LIST=""
PATCH_FALLBACK_COUNT=0
PATCH_FALLBACK_LIST=""
for patch in $(find "${PATCH_DIR}" -maxdepth 1 -type f -name '*.patch' | sort); do
    pname=$(basename "$patch")
    echo "I: Apply Kernel patch: $patch"
    APPLIED=0
    if git apply --3way --whitespace=nowarn "$patch" 2>/tmp/_apply_stderr; then
        APPLIED=1
        # Detect silent 3-way fallback — patch landed but with drifted context
        if grep -q "Falling back to three-way merge" /tmp/_apply_stderr; then
            echo "::warning::3-way-fallback: $pname applied via 3-way merge (context drifted)" >&2
            PATCH_FALLBACK_COUNT=$((PATCH_FALLBACK_COUNT + 1))
            PATCH_FALLBACK_LIST="$PATCH_FALLBACK_LIST $pname"
        fi
    else
        # Fall back to legacy patch -p1 when --3way fails (e.g. missing blobs
        # in a fresh shallow cache).  This is a one-time bootstrap path: once
        # the cache accumulates blobs from a successful build, subsequent
        # builds will use the safer --3way path.
        echo "::warning::git apply --3way failed for $pname — falling back to patch -p1" >&2
        # A failed `git apply --3way` can leave merge-stage entries in the
        # index; clear them so the commit below records only the fallback's
        # on-disk result.
        git reset -q || true
        # patch(1) exit codes are unreliable: hunks applied with fuzz or
        # offsets can return 0, and hunks silently skipped in batch mode
        # can also return 0. The trustworthy failure signal is a .rej file
        # left behind (--no-backup-if-mismatch writes one per mismatch).
        _rej_before=$(find . -name '*.rej' 2>/dev/null | wc -l)
        patch -p1 -s -t --no-backup-if-mismatch < "$patch" >/dev/null 2>&1 || true
        _rej_after=$(find . -name '*.rej' 2>/dev/null | wc -l)
        if [ "$_rej_after" -gt "$_rej_before" ]; then
            echo "::error::Kernel patch FAILED to apply (both git apply --3way and patch -p1): $pname" >&2
            PATCH_FAIL=$((PATCH_FAIL + 1))
            PATCH_FAIL_LIST="$PATCH_FAIL_LIST $pname"
        else
            APPLIED=1
            echo "::warning::patch -p1 fallback succeeded for $pname (cache will be seeded for next build)" >&2
            PATCH_FALLBACK_COUNT=$((PATCH_FALLBACK_COUNT + 1))
            PATCH_FALLBACK_LIST="$PATCH_FALLBACK_LIST $pname(fallback)"
        fi
    fi
    if [ "$APPLIED" -eq 1 ]; then
        # Commit each successfully-applied patch so that subsequent patches'
        # `git apply --3way` sees the cumulative on-disk state as their merge
        # base. Without this commit step, every patch re-bases against the
        # original pristine commit and effectively falls through to a plain
        # direct apply that requires exact context match — which fails after
        # earlier patches have shifted line numbers.
        git -c user.email=ci@local -c user.name=ci add -A
        git -c user.email=ci@local -c user.name=ci commit -q --allow-empty -m "applied: $pname" || true
    fi
done

if [ "$PATCH_FAIL" -ne 0 ]; then
    # BOOTSTRAP: when the git cache is fresh/shallow, some patches may fail
    # even with the patch -p1 fallback.  Allow the build to continue so the
    # cache accumulates blobs for the patches that DID apply; the next build
    # will use --3way which can merge the remaining patches.
    echo "::warning::$PATCH_FAIL kernel patch(es) failed to apply:$PATCH_FAIL_LIST" >&2
    echo "::warning::Continuing build with partially-patched kernel (bootstrap mode — next build will use --3way)." >&2
    echo "::warning::Failed patches:$PATCH_FAIL_LIST" >&2
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
#
# IMPORTANT (2026-08-23 fix): the earlier form of this injection matched
# 'case TC_SETUP_BLOCK:' and inserted an unconditional
# 'return dpaa_setup_tc_flow_block(...)' right after it. That predated
# board patch 0145, which had already rewritten the TC_SETUP_BLOCK case to
# try the ingress-policer block handler FIRST (dpaa_setup_tc_block) and only
# fall through to the ASK flow-offload backend on -EOPNOTSUPP. The old
# injection therefore SHADOWED the policer path: every TC_SETUP_BLOCK bind
# returned via the ASK backend, dpaa_setup_tc_block() (the matchall/police
# registrar) was never invoked, and 'tc ... matchall action police skip_sw'
# failed with EOPNOTSUPP (ask.ko: "unexpected tc_setup_type=4"). This broke
# the hardware ingress-policer on every shipping image.
#
# Correct shape: keep the 0145 policer-first dispatch for TC_SETUP_BLOCK and
# add TC_SETUP_FT as its OWN case routing to the flow-offload backend. Match
# the exact 4-line 0145 block so the count-gate hard-fails if 0145 ever drifts.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c \
        $'\t\terr = dpaa_setup_tc_block(net_dev, type_data);\n\t\tif (err != -EOPNOTSUPP)\n\t\t\treturn err;\n\t\treturn dpaa_setup_tc_flow_block(net_dev, type_data);' \
        $'\t\terr = dpaa_setup_tc_block(net_dev, type_data);\n\t\tif (err != -EOPNOTSUPP)\n\t\t\treturn err;\n\t\treturn dpaa_setup_tc_flow_block(net_dev, type_data);\n\tcase TC_SETUP_FT:\n\t\treturn dpaa_setup_tc_flow_block(net_dev, type_data);' \
        1 \
        "TC_SETUP_FT: separate case added (policer-first TC_SETUP_BLOCK preserved)"
    echo "### dpaa_eth.c: TC_SETUP_FT case added, policer TC_SETUP_BLOCK dispatch preserved (mutate)"
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
# B0V is kept at 1 (kernel TX confirmation safety — see plans/archive/ASK2-
# PERFORMANCE-MODERNIZATION.md §7 for the dedicated-FQ plan with B0V=0).
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c \
        '0x1e00000080000000ULL' \
        '0x9e00000080000000ULL' \
        1 \
        "F-052b: OVFQ=1 on TX FQ context_a"
    echo "### dpaa_eth.c: OVFQ=1 injected (mutate)"

    # B0V=0: disable context_b writebacks for hardware-offloaded frames.
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c \
        '0x9e00000080000000ULL' \
        '0x9a00000080000000ULL' \
        1 \
        "F-052: B0V=0 on TX FQ context_a"
    echo "### dpaa_eth.c: B0V=0 injected (mutate)"
fi

# Performance: deeper TX FQ taildrop (2MB -> 4MB) for 10G throughput.
# The 2MB default fills quickly at 10G line rate; 4MB gives more headroom
# before QMan taildrop kicks in, reducing per-flow backpressure.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/dpaa/dpaa_eth.c \
        '#define DPAA_FQ_TD 0x200000' \
        '#define DPAA_FQ_TD 0x400000' \
        1 \
        "F-053: TX FQ taildrop 2MB->4MB"
    echo "### dpaa_eth.c: DPAA_FQ_TD=4MB injected (mutate)"
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

# Patch 4009 equivalent: add OEM SFP-10G-SR quirk entry
# (4009-sfp-oem-rollball-quirk.patch already renames sfp_fixup_rollball_cc→sfp_fixup_fs_10gt)
if [ -f drivers/net/phy/sfp.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/phy/sfp.c \
        'SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt),' \
        'SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt),\n\tSFP_QUIRK_F("OEM", "SFP-10G-SR", sfp_fixup_fs_10gt),' \
        1 \
        "SFP: OEM SFP-10G-SR quirk appended (4009 handles rename)"
    echo "### sfp.c: OEM SFP-10G-SR rollball quirk injected (mutate)"
fi

# F-048: Set EKFC to 0x00180006 — IPSRC1|IPDST1|L4PSRC|L4PDST.
# 4-tuple extraction (12 bytes) without PTYPE1 (bit 18) which causes BMI
# stall on LS1046A FMan 210.10.1 microcode. EKFC=0x001C0006 (with PTYPE1)
# was proven to stall port 0x10/0x11 on the first frame (2026-07-14).
# The 2026-07-10 working build used 0x00180006 without stall.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_keygen.c \
        'scheme_regs.kgse_ekfc = DEFAULT_HASH_KEY_EXTRACT_FIELDS;' \
        'scheme_regs.kgse_ekfc = 0x00180006; /* F-048-R1: 12B key = SIP+DIP+SPORT+DPORT (no PTYPE1) */' \
        1 \
        "F-048-R1: EKFC 4-tuple extraction (no PTYPE1)"
    echo "### fman_keygen.c: EKFC 0x00180006 (remove PTYPE1, no stall) (mutate)"
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
    : # M2_4 folded into M2_4_2.py (fe_probe v4→v6 consolidation)

# F-063 DISABLED FOR BISECT: EXT_HASH FE contextSize must match keysize.
# Commented out to test if contextSize change (256→key_size-1) causes stall.
: 'F-063-DISABLED'
: ' sed -i '"'"'s/(FMAN_FE_HASH_CONTEXT_SIZE - 1)/(t->key_size - 1)/'"'"' drivers/net/ethernet/freescale/fman/fman_pcd.c'
: ' echo "### fman_pcd.c: F-063 EXT_HASH contextSize fixed (key_size not record_size)"'

# M2-4: reduce FE pool 100->16 to fit 64KB MURAM
# 100x28B rounded 256B = 25600B + pool 8192B + ehash 33280B > 65536B
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c \
        'FMAN_PCD_FE_POOL_COUNT	100' \
        'FMAN_PCD_FE_POOL_COUNT 16' \
        1 \
        "M2-4: FE pool reduced 100→16"
    echo "### fman_pcd.c: M2-4 FE pool reduced 100->16 (mutate)"
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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_keygen.c \
        '\t/* Write scheme registers */' \
        '\t/* F-051: force-clear RSS mask/hash config for exact-match ehash */\n\tscheme_regs.kgse_bmch = 0;\n\tscheme_regs.kgse_bmcl = 0;\n\tscheme_regs.kgse_hc   = 0;\n\tscheme_regs.kgse_ekdv = 0;\n\t/* Write scheme registers */' \
        1 \
        "F-051: BM/HC/EKDV zeroed before scheme write"
    echo "### fman_keygen.c: F-051 BM/HC/EKDV zeroed (RSS isolation) (mutate)"
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

# F-053 RETRACTED (2026-08-07, T-M3-R "999 patch" forensic finding). The
# original rationale (below, kept for the record) assumed en_exthash_node
# word_0's hash_bytes_offset field tells hardware where the key starts
# within the DDR flow record (an 8-byte link-chain header precedes it) --
# a theory formed from a /dev/mem DDR dump, before any vendor source was
# available to check it against. It was wrong. The vendor's pristine (not
# ASK-modified) fm_pcd_ext.h documents this field via the API param that
# feeds it (t_FmPcdHashTableParams.hashShift, a DIFFERENT field from the
# genuinely-obsolete kgHashShift): "Byte offset from the beginning of the
# KeyGen hash result to the 2-bytes to be used as hash index" -- i.e. it
# controls HARDWARE's own live bucket-index derivation from a KeyGen hash,
# not DDR record layout. Vendor's real cdx_pcd.xml sets hashshift="0" on
# every one of its 16 production hashtable distributions, despite having
# the identical 8-byte DDR header this project's does -- proving the field
# has nothing to do with skipping it. This project's own software bucket
# placement (fman_pcd_ehash_bucket_index(), patch 0128) has always used
# t->hash_shift=0 (every `fe_ehash set` call this project has ever run
# passed 0), unaffected by this fixup. Forcing hash_bytes_offset=1 in the
# AD word means hardware's live bucket-index derivation (if it reads this
# field the same way) computes a DIFFERENT bucket than the one software
# inserted into -- a silent, structural, always-present mismatch
# mechanistically consistent with the zero-HIT symptom chased across
# F-141 through F-177. Original F-053 rationale, kept for the record:
# "The DDR flow record (en_ehash_entry) has an 8-byte link-chain header
# (flags + next_entry pointer) before the key data at
# FMAN_EHASH_FLOW_KEY_OFF=8. The hardware descriptor field
# hash_bytes_offset (bits 17:16 of ad[0]) was being written with
# t->hash_shift (0), telling the hardware to start key comparison at byte
# 0 of the DDR record -- comparing against the link header (all zeros for
# the first flow) + partial key, which NEVER matches the KG-extracted
# bytes. The correct value for an 8-byte header is 1 (the field encodes
# 0->0B, 1->8B)." No mutation applied now -- hash_bytes_offset reverts to
# tracking t->hash_shift dynamically, matching every vendor production
# table's value (0) for this project's own test configuration.

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
    : # F_055 folded into F_054.py (MUX write AD+0→AD+4 correction)

    # F-083 REMOVED — scaffold guard (fe_enter_off==0) preserved.
    # The CONT_LOOKUP scaffold is the correct path when fe_enter_off==0.
    # When fe_enter_off!=0, RCCB→FE_ENTER direct activates the FE-VM for HIT.
    # FmPortSetFESupport (F-072) provides proper FE workspace allocation,
    # preventing the BMI stall that plagued earlier builds without it.

    # F-072b/c: Bypassed. ASK2's fman_pcd_fe_port_set handles the FmPortSetFESupport pool natively.
    # Leaving the injection active would cause double-allocations and ENOMEM failures in production engage path.

    # F-084: Fix 0158 compose FE_ENTER target — EXT_HASH not ENQ.
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c \
        'err = fman_pcd_fe_enter_build(pcd, e->muram_off);' \
        'err = fman_pcd_fe_enter_build(pcd, pcd->fe_hash_off);' \
        1 \
        "F-084: compose FE_ENTER target = EXT_HASH"
    echo "### fman_pcd.c: F-084 compose FE_ENTER target = EXT_HASH (mutate)"

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
    : # F_061 folded into M2_4_2.py (fe_probe v6 consolidation)
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

# F-089: §17 FE descriptor static_asserts + KUnit test injection.
# Injects fman-pcd-fe-static-asserts.h (compile-time BUILD_BUG_ON guards
# for all 6 FE types, NIA encodings, sizes) and fman_pcd_fe_test.c
# (KUnit suite, 8 test cases). Both copied from kernel/common/files/.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_089.py" 2>&1
    echo "### F-089: §17 static_asserts + KUnit injected"
fi

# F-080 (DELETED — folded into F-069)

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
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c \
        'static void fman_pcd_fe_build_contexts' \
        'static __maybe_unused void fman_pcd_fe_build_contexts' \
        1 \
        "F-085: __maybe_unused on fman_pcd_fe_build_contexts"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
        drivers/net/ethernet/freescale/fman/fman_pcd.c \
        'fman_muram_offset_to_vbase(muram,' \
        '(void *)fman_muram_offset_to_vbase(muram,' \
        34 \
        "F-085: cast addition on muram_offset_to_vbase (34 occ.)"
    echo "### fman_pcd.c: fe_build_contexts fixed (__maybe_unused + cast) (mutate)"
fi

# F-130: Grow PCD MURAM arena 64 KiB -> 84 KiB.
# The ehash int_buf (33280 B) + two per-port pools (~9029 B each) = 51338 B
# fits in 65536 B by total bytes but fails on placement because the int_buf
# at offset 0x4c100 fragments the arena. The contiguous MURAM extent from
# 0x4ac00 to 0x60000 is 86016 B (84 KiB). Must run BEFORE F-092 (VM chain
# build) so the gen_pool is sized correctly before any allocation.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_130.py" 2>&1
    echo "### F-130: PCD MURAM arena 64 KiB -> 84 KiB"
fi

# F-131: Guard fman_pcd_muram_free() against kexec-stale MURAM offsets.
# After a kexec reboot, the new kernel's gen_pool has a fresh chunk at a
# potentially different muram_offset.  Offsets from the previous kernel are
# not valid, and calling gen_pool_free() on them hits BUG() in lib/genalloc.c.
# Adds gen_pool_has_addr() check before gen_pool_free() with a pr_warn and
# budget adjustment for stale offsets.  Board-verified 2026-07-28 on .185
# (ISO 0422): disengaging from kexec-preserved state triggered:
#   kernel BUG at lib/genalloc.c:508!
#   gen_pool_free_owner -> fman_pcd_muram_free -> fman_pcd_fe_pool_free ->
#   fman_pcd_fe_pool_put -> fman_pcd_fe_disengage
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_131.py" 2>&1
    echo "### F-131: gen_pool_has_addr() guard in fman_pcd_muram_free()"
fi

# F-090: MISS→kernel bypass ENQ — route non-matching frames to kernel FQ.
# Adds a second ENQ FE that enqueues MISS frames to miss_fqid instead of EXIT drop.
# Enables ARP/ICMP to work through FE-VM, which is the #1 blocker for HIT testing.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_090.py" 2>&1
    echo "### F-090: MISS->kernel bypass ENQ"
fi

# F-091: QMan FQ frame counter debugfs (fq_stats node).
# Write FQID hex to read frame count. Answers "did any frame reach this FQ?"
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_091.py" 2>&1
    echo "### F-091: QMan FQ fq_stats debugfs"
fi

# F-092: Production-ready fman_pcd_fe_engage/disengage.
# Builds FE-VM chain before arming, tears down after disarming.
# Enables ask.ko to call kernel APIs instead of debugfs bridge.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_092.py" 2>&1
    echo "### F-092: production fe_engage/disengage (VM chain build/teardown)"
fi

# F-128: F-125(c) — free ehash on LAST port disengage.
# Changes F_092's teardown guard from fe_vm_chain_built to
# fe_vm_chain_built && list_empty(&pcd->fe_ports). The shared FE-VM chain
# (pool, singletons, ehash, hashfe, enq) is torn down only when the last
# port disengages, returning 33280 B MURAM + 512 KiB DDR. Runs after F-092.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_128.py" 2>&1
fi

# F-129: Add FE-VM chain teardown to production fman_pcd_fe_disengage().
# F_092 inserted teardown into the DEBUGFS handler only; the production
# YNL/genl path had ZERO teardown. Board-verified 2026-07-27 on .185:
# disengage leaves ehash int_buf refcount=1, 33280 B held, fe_pool engaged=YES.
# Inserts teardown with list_empty guard after __fman_pcd_fe_arm_disengage()
# in the production function. Runs after F-128.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_129.py" 2>&1
    echo "### F-129: VM chain teardown in production fe_disengage()"
fi

# F-132: DISABLED 2026-07-29 — M2_4_3.py already frees params pages in
# fman_pcd_kg_port_disarm_fe() (fman_pcd_kg.c).  Running both causes a
# double-free panic: F-132 frees the page in the F-129 teardown block,
# then F-134's reordered disarm calls kg_port_disarm_fe which frees it
# again → gen_pool_free_owner BUG.
# Board-verified on .185 (ISO 0146): panic at fman_pcd_kg_port_disarm_fe
# → fman_pcd_muram_free(0x4b600, 256) → gen_pool_free_owner BUG.
# if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
#     python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_132.py" 2>&1
#     echo "### F-132: params page cleanup in F-129 teardown"
# fi

# F-133: REMOVED 2026-08-17. The muram_allocations diagnostic tracker
# produced a FALSE MURAM-leak signal: its free-side record removal was
# mis-anchored (by F-131's stale-offset early-return rewrite) onto the
# kexec-stale branch only, so normal fman_pcd_muram_free() of valid
# per-port objects never dropped its tracking record — muram_allocations
# over-reported (52,634 B) versus the authoritative muram_budget
# (34,992 B, stable across engage/disengage). It also nested
# muram_track_lock under pcd->lock on the stale path (latent lock
# inversion) and leaked orphaned records at teardown. It touches no
# datapath; muram_budget is the authoritative accounting. Removed
# entirely (fixup + manifest + invocation) rather than repaired, per the
# plan's "fix or remove" bar. See qdrant + plan §4.2.

# F-134: Reorder __fman_pcd_fe_arm_disengage — KG disarm BEFORE MURAM free.
# Fixes the second-cycle disengage hang (bus lockup from BMI dereferencing
# freed MURAM via stale FMBM_RCCB).  Must run AFTER 0157 (typed impl).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_134.py" 2>&1
    echo "### F-134: KG disarm before MURAM free"
fi

# F-135: Clear fe_port_armed bit on disengage.
# F-107 sets the bit for per-port engagement guarding, F-122 tests it for
# idempotency, but nothing clears it on disengage.  After engage→disengage
# the stale bit blocks re-engage: F-122 returns "already armed (idempotent)"
# without actually re-arming.  Board-verified on .106 (ISO 0242).
# Must run AFTER F-134 (which reorders the function this modifies).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_135.py" 2>&1
    echo "### F-135: clear fe_port_armed on disengage"
fi

# F-136: Keep FE-VM chain warm across disengage/re-engage cycles.
# The F-129 teardown frees the entire chain (ehash, pool, singletons,
# enq, hash) causing arena fragmentation on re-engage (-12 ENOMEM for
# second port) and disengage hangs (BMI dereferences freed MURAM).
# Instead, keep the chain allocated — only disarm KG and free per-port
# resources.  F-092 v3 detects the existing chain on re-engage and
# skips re-allocation.  Must run AFTER F-129 (modifies its teardown).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_136.py" 2>&1
    echo "### F-136: keep FE-VM chain warm across cycles"
fi

# F-137: Allocate per-port FE buffer pools from global FMan MURAM.
# Bypassed: the global FMan MURAM has no contiguous space for 8.4 KB allocations.
# Since F-072b/c is disabled, there is no double-allocation, and the pools fit comfortably
# within the 46 KB tail fragment of the 84 KB PCD arena.
# if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
#     python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_137.py" 2>&1
#     echo "### F-137: per-port pools from global FMan MURAM"
# fi

# F-139: Move scaffold tracking from singleton to per-port (fp->scaffold_*).
# Fixes 304 B/cycle MURAM leak (CR-013).  Must run BEFORE F-134 (reorder).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_139.py" 2>&1
    echo "### F-139: scaffold tracking moved to per-port fp->scaffold_*"
fi

# F-140: M6 Piece 2 — IPv6 ehash table (key_size=37) + v6 KG scheme.
# Adds second ehash table and v6 KG scheme to FE-VM chain build.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_140.py" 2>&1
    echo "### F-140: v6 ehash table + KG scheme"
fi

# F-142: Convert ehash flow records from kzalloc to dma_alloc_coherent.
# Fixes F-141: the FMan DMA engine reads flow records from DDR and needs
# uncached memory (dma_alloc_coherent), not cacheable kmalloc memory.
# Without this, the FE-VM ehash path (Fork-B) cannot produce a HIT.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_142.py" 2>&1
    echo "### F-142: dma_alloc_coherent for ehash flow records"
fi

# F-143: Place en_exthash_node descriptor at start of DDR table allocation.
# The FE-VM EXT_HASH FE reads the en_exthash_node from DDR at table_base to
# get hash_bytes_offset, key_size, hash_mask_bits, etc.  Without this, the
# FE-VM reads garbage and cannot configure the ehash lookup correctly.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_143.py" 2>&1
    echo "### F-143: en_exthash_node at DDR table base"
fi

# F-144: Fix EXT_HASH FE word1 byte order to match SDK's packed struct.
# The NXP SDK's t_ExtHashFe is _Packed (little-endian fields).  The FMan
# reads word1 as big-endian: hashShift<<24 | contextSize<<16 | hashMask.
# Our code was writing hashMask<<16 | contextSize<<8 | hashShift — reversed.
# This caused the microcode to use hashShift=0x7F (high byte of hashMask)
# and hashMask=0x0C00 (contextSize+hashShift packed), producing wrong buckets.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_144.py" 2>&1
    echo "### F-144: EXT_HASH FE word1 byte order fixed"
fi

# F-145: Fix contextSize to 256 (DDR record size, not key size).
# The NXP SDK passes contextSize=256 (MAX_EN_EHASH_ENTRY_SIZE), not the key
# size.  The microcode uses this for DMA read sizing of the full DDR record.
# The F-063 "fix" that changed it to key_size was incorrect — the BMI stall
# was actually caused by the word1 byte order bug (F-144), not contextSize.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_145.py" 2>&1
    echo "### F-145: contextSize=255 (DDR record size) — REVERTED by F-149 below"
fi

# F-149: Revert F-145 — restore contextSize = key_size (microcode §7.2).
# F-145 changed contextSize to 256 (DDR record allocation size), but the
# EXT_HASH FE uses contextSize for KEY COMPARISON, not DMA read sizing.
# With contextSize=256, the FE compares 256 bytes per entry — bytes 21-255
# are uninitialized padding, so the comparison can never match.
# This is the root cause of F-141 (ehash HIT failure).
# The microcode reference explicitly documents this bug and its fix.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_149.py" 2>&1
    echo "### F-149: contextSize = key_size (revert F-145)"
fi

# F-152: Revert F-144 — restore original EXT_HASH FE word1 bit-position
# formula.  F-144 changed the encoding to hashShift<<24|contextSize<<16|
# hashMask based on an unverified theory, without first checking
# arch/fman-microcode-210-programming-reference.md — which already
# documented (since 2026-07-17) that the ORIGINAL patch 0131 formula
# (hashMask<<16)|((contextSize-1)<<8)|hashShift is correct (verified
# value 0x7fff0c00).  F-144 produced 0x000C7FFF instead — hashMask,
# contextSize, and hashShift all in the wrong bit positions.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_152.py" 2>&1
    echo "### F-152: revert F-144 (restore hashMask<<16|contextSize-1<<8|hashShift)"
fi

# F-147: Fix RCCB to point directly to FE_ENTER AD (not group table).
# F-091 introduced a bug: fe_enter_off = gro overrides the correct
# fe_enter_off = ato+32.  The settled architecture requires RCCB→FE_ENTER
# direct dispatch (no CC group table).  This was proven working on
# 2026-07-04 (the only confirmed HIT in program history).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_147.py" 2>&1
    echo "### F-147: hybrid CONT_LOOKUP + FE-VM topology (fe_enter_off = gro)"
fi

# F-153: Fix 0146's MUX/TRANSITION wiring.  0146 wires MUX directly to
# ENQ (skipping TRANSITION) and mislabels TRANSITION as a "MISS -> Exit"
# relay wired to fe_exit_off.  Per microcode reference Sec 7.5/7.6/7.1
# (line 60, "DDR -->|HIT| MUX[MUX FE]") and Sec 7.3 line 384 ("ENQ's
# proven role is the HIT terminal: MUX -> TRANSITION -> ENQ -> TX FQ"),
# the correct wiring is MUX->TRANSITION->ENQ.  TRANSITION is a MUX-HIT
# relay, not a MISS-path object (MISS is EXT_HASH's own missNextFE, w6).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_153.py" 2>&1
    echo "### F-153: MUX->TRANSITION->ENQ wiring (was MUX->ENQ direct)"
fi

# F-148 v4: Write flow key to CC match table on ehash insert.
# The CONT_LOOKUP group table has numKeys=0, routing ALL frames to miss-AD.
# To enter the FE-VM, the CC engine must match a key.  This fixup writes
# the flow key to the CC match table and increments numKeys when a flow
# is inserted.  Matching frames → FE_ENTER → EXT_HASH → ehash → HIT →
# MUX → TRANSITION → ENQ.  Non-matching frames → miss-AD → kernel.
# Limited to 32 entries.  v4 copies the real FE_ENTER AD content (4 words)
# into the HIT-AD slot instead of writing the raw offset as word0 (v3 bug).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_148.py" 2>&1
    echo "### F-148 v4: CC match table key write + real FE_ENTER AD copy"
fi

# F-157: Wire the dedicated TX FQ into the FE-VM ENQ (HIT destination).
# __fman_pcd_fe_build_vm_chain() built ENQ with tx_fqid=0x200, identical to
# the CC miss-AD target — HIT and MISS converged on kernel FQ 0x200 and no
# instrument could discriminate them.  R1: fman_pcd_fe_engage() now takes a
# caller-supplied enq_fqid (ask.ko's dedicated TX FQ, P4.1, ch 0x801 = eth4
# TX), stored on pcd->fe_enq_fqid and used by the chain builder (fallback
# 0x200).  A HIT frame now goes to the dedicated TX FQ (observable) while a
# miss still goes miss-AD -> kernel FQ 0x200 on eth3.  Runs after F-148.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_157.py" 2>&1
    echo "### F-157: dedicated TX FQ wired into FE-VM ENQ (HIT destination)"
fi

# F-093: Dynamic FQID resolution — kill hardcoded 0x200.
# Uses fman_pcd_resolve_miss_fqid() from port params page instead.
# Also removes miss_fqid=0x200 fallback in arm_engage (all callers resolved).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_093.py" 2>&1
    echo "### F-093: dynamic FQID (kill hardcoded 0x200)"
fi

# F-107: gen_pool double-free prevention — per-port engagement guard.
# Replaces u8 fe_armed_port with DECLARE_BITMAP(fe_port_armed, 32).
# Adds -EBUSY guard in fman_pcd_fe_engage() to prevent double-arm,
# which overwrites the KG scheme MURAM pointer and causes gen_pool_free_owner
# panic on disengage (lib/genalloc.c:508).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_107.py" 2>&1
    echo "### F-107: gen_pool double-free prevention (fe_port_armed bitmap)"
fi

# F-094: Retype fman_pcd_fe_flow_add to use structured flow_action.
# Replaces raw (key, key_size, enq_off) with const struct fman_pcd_fe_flow_action *.
# Breaking API change before anyone depends on the old signature.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_094.py" 2>&1
    echo "### F-094: flow_add retype → struct fman_pcd_fe_flow_action *"
fi

: # F-109 folded into patch 0153 (fman_pcd_fe_enq_get_offset export).
: # Phase 2 fold 2026-08-18: the fixup edit was regenerated into
: # 0153-fman-pcd-fe-engage-api.patch (its owning patch); the resulting
: # fman_pcd.c/.h are byte-identical modulo two normalized blank lines.
: # Verified: fresh-tree apply clean + tree-equivalence vs current+F_109.

# F-096: Call fman_pcd_fe_build_contexts() during fe_arm engage.
# Patch 0146 defines the function but the call site was lost when
# F-091/F-092 modified __fman_pcd_fe_arm_engage(). Without working-store
# context, the FE-VM MUX cannot read its next-FE pointer and parks on
# first frame under load. This unparks the FE-VM for hardware forwarding.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_096.py" 2>&1
    echo "### F-096: FE-VM context build call (unparks FE-VM)"
fi

# F-097 (T-P1-1 / F-08): fman_pcd_fe_verify — arm-time readback gate.
# Injects fman_pcd_fe_verify_internal() + call in engage path BEFORE KG arm.
# Catches F-072..F-079 silent-write defects (params page, EXT_HASH, MUX,
# EXIT, ENQ descriptor validation) before frames reach the silicon.
# Approximately 60 LOC.  Full 150-LOC version re-land incrementally.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_097.py" 2>&1
    echo "### F-097: fman_pcd_fe_verify arm-time readback gate"
fi

: # F-098 removed 2026-08-22 (Phase 2 cleanup): dead defensive no-op.
: # Its owning patch 0135-fman-pcd-fe-context-build.patch already defines
: # fman_pcd_fe_context_build(void __iomem *ctx, ...) writing (ctx + offset)
: # with no ctx->cpu dereference, and no other patch/fixup introduces the
: # bad `struct fman_ddr_region *ctx` retype F_098 guarded against. The
: # fixup body only fired `if "ctx->cpu" in src`, which is never true in the
: # current tree, so it was a permanent no-op. S0 qdrant-gated.

: # F-116 folded into patch 0153 (FE-VM flow-delete NULL guards).
: # Phase 2 fold 2026-08-18: the two NULL guards (fman_pcd_ehash_flow_clear_all
: # + fman_pcd_fe_flow_del) were regenerated into 0153-fman-pcd-fe-engage-api.patch.
: # Verified byte-identical vs current+F_116 across the full series (incl. F-117
: # which anchors on F-116's guarded fe_flow_del body). CI + board gated.

# F-117 (Fix B pt1): per-key FE-VM ehash delete. Adds fman_pcd_ehash_del_key
# (head + mid-chain collision-chain unlink, prev_head LIFO invariant kept) and
# rewrites fman_pcd_fe_flow_del to delete by key (NULL key => clear-all). Runs
# AFTER F-116 (matches F-116's guarded fe_flow_del body). Pairs with the
# ask.ko real-fm + built-key wiring in ask_flow_offload.c.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_117.py" 2>&1
    echo "### F-117: FE-VM per-key ehash delete"
fi

# F-118 (Fix B pt2): add a "del <key>" verb to the fe_flow debugfs node routing
# to fman_pcd_ehash_del_key (table 0), so Fix B's per-key collision-chain unlink
# is unit-testable via pure ehash ops (fe_ehash set / fe_flow add / fe_flow del)
# with NO fe_arm. Additive; runs AFTER F-117 (needs fman_pcd_ehash_del_key).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_118.py" 2>&1
    echo "### F-118: fe_flow 'del <key>' unit-test hook"
fi

# F-125: make FE-VM engage transactional. __fman_pcd_fe_arm_engage() allocated
# the 304-byte FE_ENTER scaffold (gro 256 + mto 16 + ato 32) and, when
# fman_pcd_kg_port_arm_fe() failed, returned with pcd->fe_scaffold_* still set.
# The next attempt re-entered the fe_enter_off == 0 path and OVERWROTE those
# fields, orphaning the triple permanently — measured at exactly 304 B per
# failed engage on both .185 and .106, reclaimable only by reboot, and the
# source of the arena fragmentation that made even a single-port engage fail
# -ENOMEM with plenty of free bytes. Also unwinds a partial 3-way allocation.
# Reuses the existing fman_pcd_fe_arm_free_scaffold(); adds no second helper.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_125.py" 2>&1
fi

# F-122: make fe_arm engage idempotent. Adds test_bit(fe_port_armed) check at
# the top of __fman_pcd_fe_arm_engage() (shared core, protects both debugfs and
# API paths) and changes the F-107 -EBUSY guard in fman_pcd_fe_engage() to
# return 0 with pr_info. The caller asked for the port to be engaged and it
# already is — the desired state is achieved. Runs before F-125/F-126 so the
# idempotency check is the first thing in the function.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_122.py" 2>&1
fi

# F-126 / F-127 DIAGNOSTICS RETIRED 2026-08-24: the F-125 engage investigation
# they instrumented is long closed (genl engage works, E25/E26/F-190/F-097).
# Removed so the per-early-return pr_err instrumentation does not ship.

# F-095 (DELETED — stub, never implemented)

fi

# F-099 RETIRED 2026-08-24: AF_XDP ZC bind pr_err instrumentation removed so it
# does not ship. (M4 ZC diagnostic; not part of the routed-offload release.)

# F-100: Instrument dpaa_eth_afxdp.c attach path for ZC debugging.
# Runs AFTER all patches (dpaa_eth_afxdp.c is created by patch 0073+).
# Temporary — remove once M4 root cause is identified.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_100.py" 2>&1
echo "### F-100: AF_XDP pool attach path instrumented"

# F-101: Lower DPAA1_MIN_UMEM_CHUNK 3840→2048 for M4 ZC testing.
# VPP's af_xdp plugin creates 2048-byte UMEM chunks but the driver
# requires >=3840. Temporary — remove once VPP uses 4096-byte chunks.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_101.py" 2>&1
echo "### F-101: DPAA1_MIN_UMEM_CHUNK lowered to 2048"

# F-102: Add NULL guard for fq in __poll_portal_fast SDQCR path.
# The ZC datapath can produce DQRR entries with invalid context_b.
# Without this guard, fq->cb.dqrr dereferences NULL and panics.
# Temporary — remove once ZC RX path properly initializes all FQ context_b.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_102.py" 2>&1
echo "### F-102: NULL fq guard in QMan poll path"

# F-108: Ratelimit 'Err FD status' console spam in dpaa_eth.c
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_108.py" 2>&1
echo "### F-108: Ratelimited Err FD status in dpaa_eth.c"

# F-103: SUPERSEDED 2026-07-21 — BPID reprogram re-enabled.
# F_102 (NULL fq guard) provides sufficient protection against the
# QMan context_b corruption crash. The BPID reprogram is required
# for true-ZC RX — without it, FMan DMA writes to kernel page-pool,
# not XSK UMEM, and xsk_zc_rx_redirect stays at 0.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_103.py" 2>&1
echo "### F-103: SUPERSEDED — BPID reprogram re-enabled (F_102 guards crash path)"

: # F-104 folded into patch 0109 (DPAA1 get_channels ethtool op).
: # Phase 2 fold 2026-08-18: dpaa_get_channels() + the ethtool_ops entry were
: # regenerated into 0109-dpaa-ethtool-ntuple-cc-steering-bridge.patch (its
: # owning patch — 0109 last touches dpaa_ethtool_ops). Verified byte-identical
: # to current+F_104 across the full series. CI-gated.

# F-105 / F-106 RETIRED 2026-08-24: rx_hook reject diagnostics removed so they do
# not ship. (M4 ZC datapath diagnostics; not part of the routed-offload release.)

# F-115: Fix DMA-index headroom mismatch (recover=0 bug) + diagnostic.
# dpaa_xsk_build_dma_index stores pool->heads[i].dma (base) but seed/refill
# store xsk_buff_xdp_get_dma (base+headroom); FMan reports base+headroom so
# the bsearch misses every frame → recover=0. Adds headroom to the index key.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_115.py" 2>&1
echo "### F-115: DMA-index headroom fix + recover-miss diagnostic"

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

# F-158: debugfs dump node (fe_scaffold) — ground truth on CC match-table
# layout.  STRICT_DEVMEM blocks /dev/mem MURAM reads, so this kernel node is
# the only way to see what F-148 actually wrote (key+mask) vs what the CC
# comparator reads.  Dumps group/match/AD tables for every armed port.
# Runs after F-072 (which also anchors on fe_arm_show) to keep function order.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_158.py" 2>&1
    echo "### fman_pcd.c: F-158 fe_scaffold debugfs dump node"
fi

# F-159 (2026-08-04, CC-Tree Rebuild Plan Phase 0): fix cc_pack_key()'s KG
# composite from patch 0108's ask20-branch layout (SIP|DIP|SPI=0|SPORT|DPORT,
# EKFC 0x00180206) to this branch's real EKFC 0x001C0006 (SIP|DIP|PROTO|
# SPORT|DPORT) — running the 0107/0108 debugfs CC-tree harness against the
# wrong composite would produce a false-negative MISS.  Also extends the
# cc_test debugfs read handler with a raw match-table hex dump (F-158's
# ground-truth philosophy, applied to the CC-tree harness).  Runs after 0108
# is applied (fman_pcd_cc.c must already have 0108's cc_pack_key() body).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_cc.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_159.py" 2>&1
    echo "### fman_pcd_cc.c: F-159 dpaa1 EKFC cc_pack_key fix + match-table dump"
fi

# F-160 (2026-08-04, CC-Tree Rebuild Plan Phase 1): fix fman_pcd_kg_port_
# attach_cc()'s KeyGen NIA dispatch mode. next_engine=2 (unchanged since
# patch 0106) is a project-confirmed no-op for CC dispatch (patch 0133's
# own commit message: "NEVER invokes the CC walk"). Board-tested on .185:
# a byte-exact match table (F-159) with FMBM_RCCB correctly bound still
# produced a clean MISS via a real fqid-redirect HIT/MISS test. Switches
# to next_engine=3, the real AC_CC encoding already used by the FE-VM arm
# path (patch 0133) but never wired into the CC-tree graft path until now.
#
# DISABLED (F-184, 2026-08-09): the AC_CC flip is a REGRESSION, not a fix.
# With AC_CC (next_engine=3, mode 0x80000006, CCBS=0) a frame that reaches
# KG scheme4 (spc++) never produces a CC match against THIS branch's
# CONT_LOOKUP tree (w1 = numKeys<<24|LCL_MASK|match_off) -- the 210 ref
# 7.11a vendor-group-table audit shows AC_CC expects the vendor .106
# encoding (w1 = hash/CRC config, w2 = parse-code family, w3 ~ KG-direct
# NIA, keysize direct). Observed twice (F-182 v3, F-183): pkt_count=0,
# FE pool mgmt cursor frozen, netdev frozen, no errors. The committed
# HEAD form (next_engine=2, CCBS = group offset, 24M+ frames through CC
# match per fman_keygen.c CC chaining comment) is restored by disabling
# this fixup. F-162 (KG-direct rfpne) remains active.
if false; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_160.py" 2>&1
    echo "### fman_pcd_kg.c: F-160 CC-tree graft real-AC_CC NIA fix (DISABLED by F-184)"
fi

# F-161 (2026-08-05, CC-Tree Rebuild Plan Phase 1 board test): supersedes
# F-159's cc_pack_key() layout. Board-testing F-160 on .185 caused hwport
# 0x11's RX to go totally silent (matching AND non-matching traffic) on
# every cc_test install, requiring a reboot to recover — surviving `clear`.
# dmesg from the install/detach cycle directly observed hwport 0x11's own
# live KeyGen scheme (scheme4) using EKFC 0x00180006 (SIP|DIP|SPORT|DPORT,
# NO proto), not F-159's assumed 0x001C0006 (which added PROTO based on the
# separate EHASH/FE-VM path, never confirmed against the CC comparator).
# Realigns cc_pack_key()'s software match-table layout to this directly
# observed hardware EKFC. Must run after F-159 (targets F-159's own output
# text) and after F-160 (same board-test cycle that surfaced this).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_cc.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_161.py" 2>&1
    echo "### fman_pcd_cc.c: F-161 board-confirmed EKFC cc_pack_key fix (supersedes F-159)"
fi

# F-162 (2026-08-05, CC-Tree Rebuild Plan): F-159/F-160/F-161 all confirmed
# correct against vendor source and board dmesg, yet hwport 0x11 still goes
# totally RX-silent within a handful of frames of any cc_test install.
# Reading the vendor SDK's actual FM_PORT_SetPCD()/SetPcd() (Peripherals/FM/
# Port/fm_port.c) shows the PRS_AND_KG_AND_CC case ORs NIA_KG_DIRECT |
# physicalSchemeId into fmbm_rfpne for a port with exactly one bound scheme
# -- this project's CC-graft model exactly -- and no code path here has ever
# written it (confirmed absent from every "rfpne 0x00480200" dmesg line all
# session: the NIA_ENG_KG|NIA_KG_CC_EN bits are right, but NIA_KG_DIRECT and
# the scheme id are missing). Without it, the KeyGen falls back to the
# generic SI/match-vector scheme-selection walk instead of deterministically
# using the CC-attached scheme. Adds fman_port_set/clear_kg_direct_scheme()
# and wires them into fman_pcd_kg_port_attach_cc()/detach_cc().
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_kg.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_162.py" 2>&1
    echo "### fman_port.c/fman_pcd_kg.c: F-162 KeyGen direct-scheme addressing (NIA_KG_DIRECT)"
fi

# F-205 (T-M6-1 Phase 3 S1, 2026-08-19): dormant parser LCV-split port
# primitive. Adds fman_port_set/clear_lcv_split() with readback; no caller yet.
# MUST run after F-162 (shares its fman_port.c/h tail anchors).
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_205.py" 2>&1
    echo "### fman_port.c/h: F-205 dormant IPv4/IPv6 parser LCV-split primitive"
fi

# F-165 (2026-08-05, Task #26 follow-up): fe_arm engage with an explicit
# non-zero offset must not be silently overwritten by the CONT_LOOKUP
# scaffold's own fe_enter_off = gro reassignment. Debugfs-test-only;
# production engage (fe_enter_off always 0) is unaffected.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_165.py" 2>&1
    echo "### fman_pcd.c: F-165 fe_arm engage honors caller's explicit fe_enter_off"
fi

# F-167 (2026-08-06, Task #26 follow-up, Option D): standalone FMFP_EXTC
# (FPM External Requests Control, CCSR 0x074) SYNC probe. RM §5.12.14.1
# documents this register as required by all three officially-documented
# Custom Classifier table update flows; fman_pcd_ehash_add_key() has never
# asserted it. Adds an inert-by-default debugfs node (fe_extc: cat reads
# the register, "echo sync" asserts INV0 and polls for HW to clear it) so
# the register's basic behavior can be tested standalone, without engaging
# any port. Does not touch fman_pcd_ehash_add_key() or any existing fe_*
# code path -- purely additive.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_167.py" 2>&1
    echo "### fman_pcd.c: F-167 fe_extc FMFP_EXTC SYNC probe (debugfs, inert by default)"
fi

# F-168 (2026-08-06, Task #26 follow-up to F-167): F-167's standalone probe
# board-confirmed FMFP_EXTC is safe/responsive to touch in isolation. F-168
# wires a real SYNC assertion into fman_port_set_cc_base() -- between the
# fmbm_rccb write (repoints the AC_CC dispatch target) and the fmbm_rfpne
# write (enables dispatch into it) -- to test whether this prevents the
# port-wedge that has reproducibly occurred on every prior arm attempt this
# session. Scoped to the arm path only (cc_muram_off != 0); teardown is
# untouched. Depends on F-167's fman_get_fpm_extc()/fman_set_fpm_extc()
# accessors already being present in fman.c/fman.h.
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_168.py" 2>&1
    echo "### fman_port.c: F-168 FMFP_EXTC SYNC assertion in fman_port_set_cc_base() (arm path)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_215.py" 2>&1
    echo "### fman.c/h + fman_port.c: F-215 gate global INV0 SYNC to first engaged port only"
fi

# F-169 (2026-08-06, Task #26 / T-M3-R attempt 2 follow-up): T-M3-R attempt 1
# stalled on the FE_ENTER-direct arm path with a real test-harness gap found
# -- KeyGen scheme4's EKFC was never reconfigured to match the 14-byte ehash
# table it was pointed at (stayed on its own 12-byte CC-tree format,
# 0x00180006, instead of F-163's 0x801C0006). Adds a debugfs verb
# (`fe_kg_ekfc`, `echo "set <scheme_id_hex> <ekfc_hex>" > fe_kg_ekfc`) to
# reconfigure a live scheme's EKFC via the correct disable/mutate/re-enable
# sequence keygen_scheme_setup() requires for an already-bound scheme.
# Purely additive; only fires on an explicit `set` write.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_169.py" 2>&1
    echo "### fman_pcd.c: F-169 fe_kg_ekfc live KeyGen scheme EKFC reconfig (debugfs)"
fi

# F-216 (2026-08-19, image 2228 panic): dual-port v6 arm produced a separate
# good-status default-FQ FD with addr==0; rx_default_dqrr phys_to_virt(0) +
# hash_offset 0x108 panicked at ffffffff80000108. Reject/log zero-address FDs
# before DMA/headroom access and strip the obsolete F-072/F-170 be64 frame-data
# diagnostic (F-170 deleted). MUST run after F-072 so it removes its capture.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_216.py" 2>&1
    echo "### dpaa_eth.c: F-216 zero-FD guard + F-072/F-170 RXHASH diagnostic removal"
fi

# F-171 (2026-08-06, T-M3-R attempt 5): every off!=0 arm this session has
# written FE_ENTER directly to FMBM_RCCB -- the deprecated topology RM
# section 7.11 says was superseded 2026-07-16. Adds a `fe_group` debugfs
# verb that wraps the existing FE_ENTER chain (fe_enter build) in a genuine
# CONT_LOOKUP group AD with an all-wildcard match row (sidesteps the CC
# compare-window layout question entirely), so RCCB gets armed with the
# RM-documented AD species instead. Purely additive; does not touch fe_arm
# or any existing fe_* verb.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_171.py" 2>&1
    echo "### fman_pcd.c: F-171 fe_group CONT_LOOKUP group AD wrapper (debugfs)"
fi

# F-172 (2026-08-06, T-M3-R attempt 6): F-171's fe_group always programmed
# an all-wildcard match row, which a discriminator test proved does not
# distinguish HIT from MISS (all traffic dispatches through the same path).
# Documentation review found F-158 (2026-08-01), the only prior real-key
# test of this dispatch shape, predates F-168's FMFP_EXTC SYNC fix -- so a
# real key + real participate-mask has never been tried WITH F-168 present.
# Extends fe_group's write handler to accept an explicit 16-byte key and
# 16-byte mask (falls back to F-171's wildcard default when omitted).
# Purely additive on top of F-171; no other fe_* verb touched.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_172.py" 2>&1
    echo "### fman_pcd.c: F-172 fe_group extended to accept explicit key+mask"
fi

# F-173 (2026-08-06, task #26): a deep read of vendor's own original,
# unmodified source (we-are-mono/ASK) found their fix/security-hardening
# branch fixing this project's exact symptom class ("byte-correct key/
# chain, never a genuine HIT") via an explicit wmb() before their ehash
# bucket-head-pointer publish -- weak-ordered ARM64 can let the pointer
# write reach visibility before the record's own field writes do, so
# FMan's independent DMA-capable walker dereferences a still-stale
# record. F-142 already fixed the adjacent cache-coherency half of this
# bug (kzalloc -> dma_alloc_coherent) but never added the ordering half.
# Adds wmb() immediately before this branch's own bucket-head publish in
# fman_pcd_ehash_add_key(), matching vendor's exact fix location/rationale.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_173.py" 2>&1
    echo "### fman_pcd.c: F-173 wmb() before ehash bucket-head publish"
fi

# F-174: NOT WIRED IN. Built and CI-tested clean (2026-08-07) but never
# armed on a board. A same-day 2026-07-15 post-mortem (F-069) found that
# stripping DEALLOCATE from EXIT is independently suspected of CAUSING
# port-deafness (BMI internal buffer leak, per the SDK oracle: EXIT's
# deallocateBuffer=TRUE is required, not optional) -- a worse failure mode
# than the one F-174 was trying to fix, and its underlying "DEALLOCATE
# causes FD corruption" theory (F-062e) was itself later called a
# misattribution by that same post-mortem. Superseded by F-175, which
# targets the actual documented gap (missing per-flow workspace context
# block + wrong ENQ NIA encoding) instead. Kept on disk for the record;
# do not wire in without addressing the port-deafness risk first.

# F-175 (2026-08-07, T-M3-R attempt 8): a deep re-read of this project's
# own history surfaced a 2026-07-15 finding that the real NXP design is a
# two-layer machine -- static singleton FEs (EXT_HASH->MUX->ENQ->EXIT) plus
# a per-flow FE CONTEXT that hardware auto-loads into the frame's transient
# workspace on a genuine HIT. An earlier fixup, F-057, had already tried
# writing 4 bytes trailing the key inline in the ehash record and found it
# corrupts the record -- F-057 was right about that specific location, but
# for the wrong stated reason. Reading the actual SDK oracle source
# directly (the real, non-stub FmPcdCcBuildContextByFE()/
# ExternalHashTableAddKey(), not a stub found in a different, newer patch)
# resolves the disagreement: the FE context is a SEPARATELY allocated 16B
# buffer (this branch needs only MUX+ENQ, no HM/replication chain), never
# inline in the ehash record -- the record instead carries an 8-byte DMA
# pointer to it (matching the SDK's t_FmExtHashResult.contex_addr width),
# confirming F-057's fix was correct for that location and this fixup's
# byte-level field encoding (validated byte-exact against the real
# encoder) was always correct, just aimed at the wrong destination. Also
# corrects ENQ's own encoding to the board-tested vendor form (word1 =
# genuine NIA, not a raw FQID -- tried on this exact silicon 2026-07-16,
# F-073B, got one frame through before stopping). Updates both call sites
# that share fman_pcd_ehash_add_key() (the fe_flow debugfs path and the
# ask.ko-facing kernel API) so the build stays consistent.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_175.py" 2>&1
    echo "### fman_pcd.c: F-175 per-flow FE context buffer + vendor ENQ NIA"
fi

# F-176 (2026-08-07, Phase 1 of plans/EHASH-DUAL-FIX-VERIFICATION-PLAN.md):
# adds a dispatch/FQID-independent HIT discriminator. Phase 0 of that plan
# first misread the wrong vendor function family (ext_hash_add_key() /
# t_FmPcdCcNodeExtHashInfo, reachable only via FM_PCD_HashTableSet(), never
# called by cdx_ehash.c) and concluded this project's ehash bucket/record
# format needed a 16x-stride structural rewrite -- WRONG, retracted same
# day. The function cdx_ehash.c actually calls, ExternalHashTableAddKey(),
# operates on en_exthash_bucket/en_exthash_node/en_ehash_entry -- checked
# field-by-field, this project's existing 16-byte bucket, 4-word DDR
# descriptor encoding, and flow-record header are bit-exact correct. No
# format fix needed (see arch/fman-microcode-210-programming-reference.md
# section 10 for the full correction). What IS new: en_ehash_entry is a
# union whose second view exposes hardware-writeback packet_count/
# packet_bytes/timestamp counters at offset 256, gated by
# SET_STATS_ENABLE/SET_TIMESTAMP_ENABLE flag bits, in a 320B (not 256B)
# entry. F-176 enables this unconditionally (diagnostic build) and adds a
# new debugfs node "fe_ehash_stats" to read it back -- the first way to
# tell, independent of FQID/dispatch, whether hardware ever actually
# performed a compare against an inserted key.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_176.py" 2>&1
    echo "### fman_pcd.c: F-176 ehash stats/timestamp HIT discriminator"
fi

# F-181 (2026-08-11): vendor-faithful per-key opcode-script in the ehash DDR
# record.  Source-grounded against the genuine vendor ExternalHashTableAddKey()
# (we-are-mono/ASK 010-ask-fman-dpaa-ehash.patch): the vendor en_ehash_entry
# flags carry SET_OPC_OFFSET/SET_PARAM_OFFSET + an inline ENQUEUE_PKT opcode
# list + en_ehash_enqueue_param(flow fqid).  Our record had flags=STATS_EN only
# + key + u32 ENQ offset, so on a HIT the FE-VM walks opc_offset=0 (header
# bytes) and cannot ENQUEUE -- the ehash comparator never completes.  F-181
# writes the vendor opcode-script.  MUST run AFTER F-176 (sets STATS_EN in
# flags) and BEFORE F-175's record-model blocks (which F-181's payload
# supersedes for the dispatch).  No container change -- 16B bucket path kept.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_181.py" 2>&1
    echo "### fman_pcd.c: F-181 ehash record opcode-script + ENQ fqid"
fi

# F-177 (2026-08-07, T-M3-R Phase 2 item 2): Phase 1 (F-176 corrected to
# STATS_EN-only) confirmed the FE-VM ehash zero-HIT result is real. Phase 2
# item 1 (int_buf_pool_addr/global_mem_offset byte-for-byte re-check against
# vendor's ExternalHashTableSet()) found this project's encoding already
# bit-exact correct -- no fix, no board risk, closed by code review alone.
# Item 2 (this fixup): F-168 wired an FMFP_EXTC[INV0] SYNC assertion into
# fman_port_set_cc_base()'s FMBM_RCCB write (the AC_CC dispatch topology)
# and it fixed the historical port-wedge on arm. RM S5.12.14.1 documents
# this SYNC as required after changing ANY live FMan-controller-walked
# structure before dispatch into it is safe -- the ehash bucket table is
# exactly such a structure, yet fman_pcd_ehash_add_key()'s own bucket-head
# publish (F-173's wmb()-then-publish) has never asserted it. Weaker
# hypothesis than F-168's (vendor's own ExternalHashTableAddKey() fast
# insert path calls no sync of any kind -- see fm_ehash.c, arch/fman-
# microcode-210-programming-reference.md sec 12.1), but cheap and the last
# concrete insert-path lead before Phase 3 (new diagnostic capability /
# Fork-B viability reassessment).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_177.py" 2>&1
    echo "### fman_pcd.c: F-177 FMFP_EXTC SYNC on ehash bucket-head publish"
fi

# F-178 (2026-08-07, direct response to: "vendor's real ASK code works on
# this exact board -- what are we doing differently?"). Vendor's real
# FM_PORT_SetPCD()/SetPcd() ALWAYS ORs NIA_KG_DIRECT | physicalSchemeId
# into fmbm_rfpne for a single-bound-scheme port. F-162 already wrote and
# CI-wired the fix (fman_port_set_kg_direct_scheme()) but only ever called
# it from the abandoned attach_cc()/detach_cc() CC-graft mechanism -- the
# ACTUAL arm path every T-M3-R test has used, fman_pcd_kg_port_arm_fe()/
# _disarm_fe(), never calls it. Confirmed directly by this session's own
# dmesg on every arm: "rfpne 0x00480200", never "... | NIA_KG_DIRECT |
# scheme_id". Without it, KeyGen uses the generic SI/match-vector walk
# instead of deterministically dispatching to the CC-attached scheme --
# meaning every carefully-configured EKFC/key-format/hash_bytes_offset/
# PORT_ID value tested on "scheme 4" so far may never have been consulted
# for live traffic if the generic walk selects a different scheme.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_kg.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_178.py" 2>&1
    echo "### fman_pcd_kg.c: F-178 NIA_KG_DIRECT wired into the FE-VM arm path"
fi

# F-179 (2026-08-07, follow-up while resolving <combine>/PORT_ID from
# vendor's real FMC compiler source). fm_pcd_ext.h's t_FmPcdExtractEntry has
# no dedicated union member for e_FM_PCD_KG_EXTRACT_PORT_PRIVATE_INFO, and
# vendor's fm_kg.c BuildSchemeRegs() assigns kgse_dv0/dv1 straight from the
# scheme's privateDflt0/1 fields -- meaning KG_SCH_KN_PORT_ID (EKFC bit 31)
# draws its extracted value from these same "scheme default register 0/1"
# fields. This project's own fman_keygen.c (mainline-derived, unmodified for
# this logic) populates kgse_dv0/dv1 with DEFAULT_HASH_KEY_IPv4_ADDR/
# DEFAULT_HASH_KEY_L4_PORT (0x0A0A0A0A/0x0B0B0B0B) inside the
# `if (scheme->use_hashing)` branch -- a completely unrelated mainline
# RSS-fallback mechanism. Live-read on .185 scheme 4 confirmed an exact
# byte-for-byte match. This project's own EKFC override never reprograms
# these registers, so every PORT_ID board measurement so far (the
# 2026-07-13 brute force and the 2026-08-07 16-candidate sweep) tested
# against this uncontrolled value, not a value either test accounted for --
# neither result can be trusted to rule the mechanism out. Fix: zero
# kgse_dv0/kgse_dv1/kgse_ekdv whenever this project's own EKFC override
# (scheme->ekfc) is in control, so any PORT_ID extraction reads a known
# 0x00 instead of a leftover RSS constant.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_179.py" 2>&1
    echo "### fman_keygen.c: F-179 kgse_dv0/dv1/ekdv zeroed under EKFC override"
fi

# F-182 (2026-08-12, E20 corrected track step 1): the F-181 first silicon
# test's DDR record dump exposed three record bugs independent of dispatch:
# (1) F-175's per-flow ctx pointer write (8B be64 at 8+align8(keysize))
# CLOBBERS F-181's opcode slot (8+ALIGN(keysize,4)) -- both = 24 for
# keysize 14; dump showed opcode[24]=0x00 under the ctx_dma be64. Relocate
# the ctx pointer past opcode area + param block (offset 56 for keysize 14).
# (2) param.fqid carried the ENQ FE MURAM offset (dump: 0x00055f00) -- the
# vendor writes the flow's actual target FQID there (cdx create_enque_hm:
# param->fqid = cpu_to_be32(l2_info.fqid)). Write the add_key fqid param.
# (3) SET_STATS_ENABLE on a 256B record: vendor sets it only with the 320B
# ext entry (stats at +256) + UPDATE_STATS in the hashfe word; we have
# neither, so clear it (pkt_count was never a valid discriminator -- the
# M3 gate is the fe_obs canary, patch 0169). Anchored on F-175/F-181
# outputs; MUST run after both.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_182.py" 2>&1
    echo "### fman_pcd.c: F-182 ehash record fixes (opcode-slot clobber, fqid source, STATS_EN)"
fi

# F-183 (2026-08-12, E20 corrected track step 2 -- Delta 1 adapted to what
# .185 silicon survives): the F-181 test stalled on the first dispatched
# frame because engage used the bare-FE_ENTER-root-at-RCCB form (known
# staller) + AC_CC mode (stalls on .185 mainline) + KG_DIRECT rfpne
# (0x00480304, vendor is 0x00480200). Assembles the ONLY combination where
# every element is individually proven non-stalling on .185:
#   - CCBS-graft dispatch: KGSE_MODE stays EN|ENQUEUE_KG_DFLT_NIA
#     (0x80500002), KGSE_CCBS = group-table offset -- written to scheme
#     window WORD 3 (struct kgse_bmch, 0x10C), NOT word 19 (kgse_ccbs,
#     0x14C) which 210.10.1 ignores (F-184 board proof 2026-08-10). This
#     is a real kernel bug fix in keygen_scheme_setup().
#   - RCCB -> group table numKeys=0 with the MISS SLOT = verbatim copy of
#     the caller's FE_ENTER AD: the CC comparator is proven INSENSITIVE to
#     match rows (5 negative variants), so FE_ENTER cannot ride a match
#     leaf; every frame -> FE_ENTER -> the ehash decides HIT/MISS.
#   - rfpne stays 0x00480200 (F-178's KG_DIRECT OR removed from arm_fe).
#   - F-148's numKeys bump is pinned at 0 (numKeys=1 would move the miss
#     slot off the FE_ENTER copy after the first flow insert).
# Teardown unchanged: detach_cc restores next_engine=0 + cc_bits_sel=0,
# clearing word 3 via the same path. Anchored on F-051/F-148/F-165/F-178
# outputs; MUST run after all of them.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_183.py" 2>&1
    echo "### fman_keygen.c/fman_pcd_kg.c/fman_pcd.c: F-183 Delta-1 dispatch (CCBS word-3, group-root miss-slot FE_ENTER)"
fi

# F-201 (2026-08-17): F-051 unconditionally zeroed kgse_hc immediately
# before every scheme write, clobbering the 128-FQ distribution computed by
# keygen_port_hashing_init() for all five RSS schemes.  Live .185 readback:
# schemes 0-4 all had range=0/HMASK=0 (one FQ), and 8 distinct software-
# forwarding flows all landed on CPU0.  Preserve the computed kgse_hc for RSS/
# policer schemes (next_engine 0/1); retain the clear only for CC/FE exact-match
# (next_engine 2/3). Must run after F-183 (its anchor includes F-051's block).
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_201.py" 2>&1
    echo "### fman_keygen.c: F-201 RSS kgse_hc preserved (128-FQ spread)"
fi

# F-209 (T-M6-1 productization step 1, 2026-08-19): carry per-scheme CCOBASE
# in the AC_CC branch. v4 cc_base_offset=0 -> byte-identical; v6 uses 1.
# MUST run after F-201 (same keygen_scheme_setup function, separate anchor).
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_209.py" 2>&1
    echo "### fman_keygen.c: F-209 AC_CC CCOBASE encoding (v6 row select)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_224.py" 2>&1
    echo "### fman_keygen.c: F-224 46-byte dual-lane GEC key on AC_CC FE scheme"
fi

# F-184 (2026-08-12): the first live `fe_obs arm` (patch 0169's canary
# discriminator, until then only compile-verified) panicked the kernel on
# .185 -- reproduced twice:
#   list_add double add ... kernel BUG at lib/list_debug.c:35!
#   fman_pcd_fe_obs_enq_one -> __list_add_valid_or_report -> panic=60 reboot
# Root cause: fe_obs_enq_one() takes the canary FE object via
# list_first_entry_or_null(&pcd->fe_available) (which does NOT unlink) and
# list_add_tail's it onto fe_singletons WITHOUT list_del -- a double-add.
# Every other fe_available consumer (singletons/enq/hashfe builders) does
# list_del first; fe_obs missed the pattern. Insert the list_del.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_184.py" 2>&1
    echo "### fman_pcd.c: F-184 fe_obs_enq_one list_del fix (arm-panic)"
fi

# F-185 (2026-08-12, E23): vendor-faithful CC dispatch -- AC_CC mode +
# VARIANT B en_exthash_node at RCCB (the NXP ASK SDK production path;
# user-approved direction). Ghidra re-analysis decoded the 210.10.1 CC
# engine: the single AD-type extraction site (c600001e >>30 @ w1857,
# br_tbl[0xf000]) routes type-1 (CONT_LOOKUP) into the enhanced
# external-hash machine, which parses the 16B AD as an en_exthash_node
# VARIANT B (field widths proven by the microcode's own extraction
# census; .106 production row tcp4 4e400008 eb700100 0402080f 00480308
# decodes it 4 independent ways). ROOT CAUSE of F-183's no-HIT/no-MISS/
# no-delivery: the RM-8.7.4.1 group AD parses as a garbage node
# (miss_action DONE, keysize 0, table_base = MURAM offset misread as
# DDR, pool out of range) -> every frame terminated with no disposition.
# The historical AC_CC stalls (0118 iter-48, Path A 08-10) were
# invalid-CONTENT stalls (bare FE_ENTER = node with table_base=0, pool=0
# -> pool-0 wait), not invalid-MODE: .106 runs AC_CC 0x8x000006 ccbs=0
# on this identical blob in production. 6 blocks / 3 files: (1) engage
# scaffold writes the node at gro (needs fe_ehash table + fe_int_buf
# pool; falls back to F-183's numKeys=0 miss-enq group without them),
# (2) fman_pcd_fe_node_set_miss_nia() setter, (3) internal.h decl,
# (4) arm_fe next_engine 2->3 + cc_bits_sel=0 (vendor AC_CC, ccbs=0),
# (5) miss-NIA commit NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme_id
# (byte-exact the .106 0x0048030x encoding) before the EXTC SYNC,
# (6) ENGAGED dmesg. HIT = DDR entry opcode script ENQUEUE_PKT ->
# param.fqid (F-181/F-182 records); MISS = word3 NIA -> KG-direct
# re-entry -> scheme fqb -> kernel. Anchored on F-183/F-184 outputs.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_185.py" 2>&1
    echo "### fman_pcd.c/fman_pcd_kg.c/fman_pcd_internal.h: F-185 vendor AC_CC + VARIANT B node dispatch"
fi

# F-186 (2026-08-12, E25 silicon-proof): correct the F-185 node miss-action
# to the form that actually works on 210.10.1. E25 (live /dev/mem patches,
# .185 6.18.44-vyos) proved the F-185 miss form (miss_action_type=NIA, word3
# = KG-direct NIA) is FATAL: empty-bucket MISS -> KG re-classification into
# the AC_CC scheme -> node -> MISS -> NIA = INFINITE LOOP (~4.5M
# classifications/sec, no hop limit, no stall); KG-direct to a foreign
# scheme -> FM_FD_ERR_NO_SCHEME 0x00004000 -> error FQ 0x291. The correct
# form (999 patch e_FM_PCD_DONE + E25 verified): miss_action_type=ENQUE
# (word0 bits[31:30]=0b10) + word3 = fqid = direct enqueue, loop-free, and
# the fqid MUST be the frame's own-port base fqid (0x300/eth4): cross-port
# fqb (0x200/eth3) delivers to eth3's FQ but the dpaa driver drops it (FD
# buffer belongs to the frame's own BM pool). 2 blocks / 2 files:
# (1) fman_pcd.c engage node word0 -> 0b10 ENQUE; (2) fman_pcd_kg.c arm_fe
# captures slot->base_fqid under the lock and commits it as node word3 via
# fman_pcd_fe_node_set_miss_nia() instead of the KG-direct NIA. bpid
# intentionally NOT changed (record stays bpid=0; whether the machine uses
# param.bpid on the ENQUEUE_PKT path is an E26 matrix test, F-187 if it
# matters). Anchored on the exact F-185 derived output; runs after F-185.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_186.py" 2>&1
    echo "### fman_pcd.c/fman_pcd_kg.c: F-186 ENQUE miss form + own-port fqb (loop fix)"
fi

# F-187 (2026-08-12, E26 follow-up): free the fe_hashfe miss-result
# allocations on hash free. fman_pcd_fe_hash_build() allocates
# pcd->miss_res_off (16 B MURAM t_ExtHashResult) + pcd->miss_ctx (256 B
# DMA) per build; fman_pcd_fe_hash_free() returned only the FE object to
# the pool and never freed them. Measured on .185 (muram_budget): exactly
# +16 B MURAM per build/clear cycle, linear across cycles (the 256 B DMA
# leaks alongside, invisible to the MURAM budget). The minimal arm (no
# hashfe build) leaks 0, isolating the defect to this pair. The hash FE is
# legacy (node dispatch does not use it), but the leak violates the
# "used MUST return to baseline" reversibility invariant. Fix: free
# miss_res_off (fman_pcd_muram_free, 16 B) and miss_ctx
# (dma_free_coherent via the last ehash table's dev -- same lookup the
# build uses) in fe_hash_free, resetting both, guarded on non-zero/NULL.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_187.py" 2>&1
    echo "### fman_pcd.c: F-187 fe_hashfe miss-result leak fix (16B MURAM + 256B DMA/cycle)"
fi

# F-188 (2026-08-12, P0-2 production-path audit): align the production
# genl/flowtable path with the E25/E26-verified 14-byte mechanism. Three
# stale bits guaranteed no production HIT: (1) __fman_pcd_fe_build_vm_chain
# created the ehash table with key_size 13 + fman_pcd_fe_engage armed EKFC
# 0x001C0006 (13-byte, no PORT_ID) while records are 14-byte
# (ASK_FE_KEY_SIZE=14) -> comparator/bucket never agree; (2) the record
# target FQID was action->enq_off, fed by ask.ko's
# ask_hw_get_enq_fe_off() = the ENQ FE MURAM offset (invalid FQID);
# (3) ask_fe_build_key wrote k[0]=key->port_id (hw port 0x11) but the
# silicon's PORT_ID extraction reads the zeroed dv default 0x00 (fixed by
# a direct OOT edit to kernel/ask/oot-modules/ask/ask_flow_offload.c).
# Fixes: ehash_key_sz 13->14, fe_engage EKFC -> 0x801C0006, fe_flow_add
# target -> fman_pcd_resolve_miss_fqid(pcd, hw_port_id) (own-port RX
# FQID, same source as the miss path). The genl engage itself already
# reaches the F-185/F-186 arm (verified live: ENQUE node + own-port miss
# fqid + dv0/dv1=0); the nft flowtable offload additionally needs active
# conntrack on the board (nf_conntrack_count was 0) -- separate item.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_188.py" 2>&1
    echo "### fman_pcd.c: F-188 production-path alignment (14-byte key + own-port flow target)"
fi

# F-189 (2026-08-14, EHASH-DUAL-FIX Phase 1.1): stats-enabled fe_flow insert
# ('add stats' form) + bucket pad readback in fe_ehash_stats. Investigative
# instrument for the Phase 3 board session -- dispatch-independent compare
# discriminator. Anchored on the exact post-F-188 derived state.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_189.py" 2>&1
    echo "### fman_pcd.c: F-189 stats-enabled flow insert (EHASH-DUAL-FIX Phase 1.1)"
fi

# F-190 (2026-08-14, Phase 3 fix): write en_exthash_node vendor node to the
# root AD in fe_enter_build so the CC dispatch reads the correct node type.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_190.py" 2>&1
    echo "### fman_pcd.c: F-190 en_exthash_node at root AD (CC dispatch fix)"
fi

# F-192 / F-194 / F-196 DIAGNOSTICS RETIRED 2026-08-24: the E2/flow-add
# investigation they instrumented is closed (F-195/F-197 production path proven,
# M3 HIT gate passed). Removed so the read-only workspace/flow-add/resolver
# logging does not ship.

# F-193 (STRUCTURAL, was a diagnostic): hoists the own-port fallback FQID into a
# local `target_fqid` and passes it to fman_pcd_ehash_add_key(). KEPT — F-198's
# hardware TX terminal depends on this variable (hit_fqid = tx_fqid || target_fqid).
# All diagnostic logging removed; behavior identical to the original inline resolve.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_193.py" 2>&1
    echo "### fman_pcd.c: F-193 target-fqid hoist (structural prerequisite for F-198)"
fi

# F-197 (2026-08-15): when the populated params-page FQID is zero, use
# the unique non-zero base FQID of a used KeyGen scheme bound to this port.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_197.py" 2>&1
    echo "### fman_pcd.c: F-197 same-port KeyGen target-FQID fallback"
fi

# F-198 (2026-08-15, T-M7-2 S1): hardware TX terminal. When the OOT caller
# supplies a per-egress-interface TX FQ + routed L2 rewrite, the FE record
# emits INSERT_L2_HDR(0x41) + ENQUEUE_PKT(0x01) to that TX FQ (direct-to-wire);
# otherwise it keeps F-197's own-port RX-FQID reinjection terminal. Must run
# after F-094 (struct), F-181/F-182 (record writer), and F-188 (flow_add).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_198.py" 2>&1
    echo "### fman_pcd.c: F-198 hardware TX terminal (INSERT_L2_HDR + ENQUEUE_PKT)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_228.py" 2>&1
    echo "### fman_pcd.c: F-228 key-addressed per-flow stats getter (T-M8-3)"
fi

# F-199 (2026-08-16, T-M7-2 S4): per-egress no-confirm TX FQ. Adds a
# FQ_TYPE_TX_NO_CONFIRM dpaa_fq with context_a=0x1c00000080000000 (B0V=0,
# EBD=1), exports dpaa_alloc_offload_tx_fq(), and leaves the kernel's normal
# confirm-enabled TX FQs untouched. ask.ko caches one FQ per output netdev.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_199.py" 2>&1
    echo "### dpaa_eth: F-199 per-egress no-confirm offload TX FQ"
fi

# F-200 (2026-08-16, T-M7-2 S3): UPDATE_TTL(0x21) for routed IPv4. Prepends the
# TTL-decrement opcode (+4B zero DSCP param) ahead of INSERT_L2_HDR so the FE
# decrements TTL and fixes the IPv4 checksum in hardware. IPv4 only; must run
# AFTER F-198 (extends its TX branch).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_200.py" 2>&1
    echo "### fman_pcd.c: F-200 UPDATE_TTL routed-IPv4 opcode"
fi

# F-202 (2026-08-17): production fman_pcd_fe_flow_add/del violated the
# fman_pcd_ehash_{add,del}_key contract requiring pcd->fe_lock. nft's async
# nf_ft_offload_del workqueue raced per-key delete against duplicate delete /
# clear / drain and panicked in list_del with LIST_POISON2. Serialize the
# production wrappers and make an already-removed key idempotent.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_202.py" 2>&1
    echo "### fman_pcd.c: F-202 production ehash flow lifecycle serialization"
fi

# F-203 (2026-08-17): order-1 (8 KiB) DPAA RX buffers.  The mainline
# order-0/4K pool caps one contiguous RX frame at ~3.6K even though the
# mEMAC/FMan port allows 9600; oversized frames hit FM_FD_ERR_PHYSICAL and
# wedge RX.  Make RX pool alloc/free order-aware (DPAA_BP_ORDER=1) while
# leaving TX-SGT/XDP-copy scratch pages order-0.  Keeps jumbo-ish frames
# contiguous and eligible for ASK FE HW offload (unlike RX SG).
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_203.py" 2>&1
    echo "### dpaa_eth.c: F-203 order-1 8KiB RX buffers (contiguous jumbo)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_222.py" 2>&1
    echo "### dpaa_eth.c: F-222 revert DPAA_BP_ORDER 1->0 (order-1 wedges RX under load)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_227.py" 2>&1
    echo "### dpaa_eth.c: F-227 crash-safe TX-confirm guard (reject FMan HIT FD on confirmed FQ)"
fi

# F-229 (issue #45, 2026-08-22): the GPY115C-reported link on a 1G RJ45
# port flaps under sustained LAN->WAN forwarding when pause resolves OFF.
# After PHY attach, force symmetric 802.3x pause only for SGMII links; leave the
# eth3/eth4 10G XGMII ASK FE offload path unchanged.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_229.py" 2>&1
    echo "### dpaa_eth.c: F-229 force symmetric pause on 1G SGMII links (issue #45)"
fi

# F-204 (T-M6-1 Phase 2a, 2026-08-19): additive, v4-byte-identical ehash
# table selector. Adds action->table_idx (0=v4, 1=dormant v6) without
# overloading hw_port_id — F-195's own-port miss-FQID semantics stay intact.
# MUST run after F-198 (final action struct), F-194/F-202 (final flow-add body).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_204.py" 2>&1
    echo "### fman_pcd.c/h: F-204 explicit ehash table_idx selector (v4=0, v6=1)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_218.py" 2>&1
    echo "### fman_pcd.c: F-218 v6 flow_del selects table1 by 38-byte key length"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_220.py" 2>&1
    echo "### fman_pcd.c: F-220 per-port routed-IPv4 ehash table lifecycle foundation"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_221.py" 2>&1
    echo "### fman_pcd.c: F-221 repoint v4 node/add/del to per-port table"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_225.py" 2>&1
    echo "### fman_pcd.c: F-225 v4 ehash key_size 14->46 (dual-lane GEC key)"
fi

# F-210/F-211/F-212 (T-M6-1 IPv6 productization steps 2-4, 2026-08-19):
# default-OFF, byte-identical-for-v4 production plumbing for the silicon-proven
# dual-family dispatch recipe. F-210 writes table1's vendor node at gro+16;
# F-211 arms/binds the v6 scheme with mv=V6BIT + CCOBASE=1 and narrows v4's
# mv to V4BIT; F-212 applies/restores the parser LCV split. All three are gated
# by fsl_dpaa_fman.v6_enable=0 (default; 0644 sysfs knob); ask.ko v6 preflight remains separately
# -EOPNOTSUPP until the board gate passes. MUST run after F-204 (table1 selector),
# F-205 (LCV primitive), F-209 (AC_CC CCOBASE), F-185/F-186 (vendor node/miss).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_210.py" 2>&1
    echo "### fman_pcd.c/h: F-210 v6 gate + dual-node engage writer (gro+16)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_219.py" 2>&1
    echo "### fman_pcd.c/h: F-219 per-port v6 intent bitmap + setter/predicate"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_211.py" 2>&1
    echo "### fman_pcd.c/kg.c/h: F-211 gated v6 KeyGen scheme arm/bind"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_212.py" 2>&1
    echo "### fman_pcd_kg.c: F-212 gated parser LCV split + restore"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_214.py" 2>&1
    echo "### fman_keygen.c/pcd_kg.c: F-214 gated cls-plan0 pass-all (QLCV fix)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_226.py" 2>&1
    echo "### fman_pcd.c: F-226 dual-lane v6 enable (kill LCV schemes/gro+16, add HOPLIMIT)"
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_230.py" 2>&1
    echo "### fman_pcd.c: F-230 FE-VM NAT/PAT opcode emitter (T-M6-7.1, dormant unless nat->flags)"
    # T-M6-8 VLAN RE-ARCHITECTURE R1 (2026-08-26, plans/ASK2-VLAN-REARCH.md):
    # the inline FE-VM VLAN opcode path (F-233) is silicon-proven dead -- it
    # freezes at exactly 5+tnums=21 packets (a per-task FE-VM management-index
    # resource the strip/rebuild handlers consume without release), and two
    # microcode oracle patches on the action-interpreter epilogue both failed.
    # VLAN is being re-architected onto the FMan HMCD/HMTD engine (a separate
    # engine from the FE-VM, so the freeze cannot occur) referenced from a CC
    # leaf action via NADEN. F-233 (inline VLAN opcode emitter) and F-234 (its
    # frag-info context) are RETIRED from the build here; ask.ko's VLAN gate
    # now fails closed to software until the HMTD path (R2-R5) lands. The
    # fixup files are kept as the vendor-encoding reference only. Routed/NAT
    # records are byte-identical to the pre-F-233 baseline (F-230 unchanged).
    #python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_233_vlan.py" 2>&1  # RETIRED (R1)
    #python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_234_frag.py" 2>&1  # RETIRED (R1)
    echo "### VLAN: F-233/F-234 inline FE-VM path RETIRED (R1); HMTD re-arch pending"
fi

# F-191 (2026-08-14, ASK2-PRODUCTION-ARCHITECTURE Phase 1): gate the debugfs
# surface behind CONFIG_FMAN_PCD_DEBUG_FS (board patch 0170 adds the symbol).
# MUST run after every fixup that registers a debugfs node (F-086, F-167,
# F-169, F-171, F-172, F-176, F-189, F-192) so the wrap covers them all.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_191.py" 2>&1
    echo "### fman_pcd.c: F-191 debugfs surface gated behind CONFIG_FMAN_PCD_DEBUG_FS"
fi

# === end ls1046a-build patch-loop replacement ===
"""

if reinject_at is not None:
    # Re-inject case: the original upstream loop pattern is long gone (it
    # was replaced on the first run), so there is nothing left for PATTERN
    # to match. Splice the current REPLACEMENT text directly back into the
    # gap left by the strip above.
    new = src[:reinject_at] + REPLACEMENT + src[reinject_at:]
    n = 1
else:
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
#     - Pre-generate persistent RSA signing key at ${GITHUB_WORKSPACE}/ask-persistent-keys/
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
#       so kernel/ask/oot-modules/ask/ci-build.sh can use it as KSRC
#     - Touch ${CWD}/ask-kernel-snapshot/.done as the "snapshot ready" flag
#
# kernel/ask/oot-modules/ask/ci-build.sh checks for the snapshot
# when its $KSRC/Module.symvers is missing and switches KSRC to the
# snapshot's extracted headers tree.
#
# Idempotency/update behavior: if the marker already exists in the persistent
# runner's build-kernel.sh, strip BOTH old injected blocks and re-inject the
# current templates. A hard no-op here caused the stale ${CWD} key path to
# survive F-217 forever on the self-hosted runner (images 2323/2348).
echo "### Injecting ASK2 v2 persistent-key + headers-snapshot blocks into build-kernel.sh"
python3 - "$KERNEL_BUILD/build-kernel.sh" <<'PYEOF'
import pathlib, sys
bk = pathlib.Path(sys.argv[1])
src = bk.read_text()

MARKER = "# === ASK2 v2 persistent-key + headers-snapshot ==="
# F-217 fix: previously this was a hard no-op when MARKER was present, which
# on the PERSISTENT self-hosted runner meant a stale injected block (with the
# old ${CWD}/ask-persistent-keys path) survived forever and every edit to the
# KEY_BLOCK/SNAPSHOT_BLOCK below was silently ignored — the kernel kept
# embedding CONFIG_MODULE_SIG_KEY from the OLD package-dir path while ask.ko was
# signed with the new workspace key (image 2323/2348 "Key was rejected"). Same
# class of bug the patch-loop injector already fixed (see SENTINEL strip above).
# Fix: when the markers are present, STRIP both previously-injected blocks so
# the injection below re-runs fresh EVERY time, keeping build-kernel.sh in sync
# with the current template.
KEY_END = "# === end ASK2 v2 persistent-key block ===\n"
SNAP_BEGIN = "\n\n# === ASK2 v2 post-bindeb-pkg headers snapshot ==="
SNAP_END = "# === end ASK2 v2 post-bindeb-pkg headers snapshot ===\n"
if MARKER in src:
    print(f"### {bk}: ASK2 v2 blocks present — stripping stale blocks for a fresh re-inject")
    # Strip the KEY_BLOCK (from its leading MARKER line through KEY_END).
    ks = src.find(MARKER)
    ks_line = src.rfind("\n", 0, ks) + 1  # include the block's leading blank line boundary
    ke = src.find(KEY_END, ks)
    if ke != -1:
        ke += len(KEY_END)
        # also swallow one trailing blank line if present
        if src[ke:ke+1] == "\n":
            ke += 1
        src = src[:ks_line] + src[ke:]
    # Strip the SNAPSHOT_BLOCK (from SNAP_BEGIN through SNAP_END).
    ss = src.find(SNAP_BEGIN)
    if ss != -1:
        se = src.find(SNAP_END, ss)
        if se != -1:
            se += len(SNAP_END)
            # Preserve a line boundary between the bindeb-pkg command before
            # the old snapshot block and the command that followed it.
            src = src[:ss] + "\n" + src[se:]

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
# F-217 fix: anchor the persistent key to ONE absolute, symlink-free,
# non-tmpfs workspace path. ${CWD} inside build-kernel.sh is the kernel source
# root, which is a symlink into ~/kernel-git-cache/linux (tmpfs) — a DIFFERENT
# directory than the ./ask-persistent-keys the OOT signing snapshot reads from
# ci-build-packages.sh's package dir. That divergence signed vmlinux and ask.ko
# with two different keys (same CN, different SKID) -> "Key was rejected by
# service" at insmod (image 2323). One absolute GITHUB_WORKSPACE path fixes it.
ASK_KEY_DIR="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set for ASK persistent key}/ask-persistent-keys"
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
# F-217/kernel-skew fix: append a strictly-monotonic per-build suffix to the
# kernel .deb version. Without it, every rebuild of 6.18.44 produces version
# "6.18.44-1"; the persistent chroot already has that version installed, so
# `apt install linux-image-6.18.44-vyos` is a no-op and the ISO ships the
# PREVIOUS run's vmlinuz (stale kernel, old module-signing keyring) alongside a
# freshly rebuilt squashfs/ask.ko -> "Key was rejected by service" on the board.
# BUILD_VERSION (e.g. 2026.08.20-0026-rolling) reduces to digits (20260820.0026)
# which sorts monotonically; fall back to the epoch second if unset. The
# ask-modules .deb Depends on the release string (linux-image-<KVER>-vyos), NOT
# this Debian version, so the OOT dependency stays satisfied.
BINDEB_REPLACE = (
    'KDEB_SUFFIX="$(printf "%s" "${BUILD_VERSION:-}" | tr -cd "0-9." | sed "s/^[.]*//;s/[.]*$//")"\n'
    '[ -n "$KDEB_SUFFIX" ] || KDEB_SUFFIX="$(date -u +%Y%m%d.%H%M%S)"\n'
    'echo "I: ASK2 kernel .deb version = ${KERNEL_VERSION}-1+b${KDEB_SUFFIX} (per-build, forces chroot upgrade)"\n'
    "make bindeb-pkg BUILD_TOOLS=1 LOCALVERSION=${KERNEL_SUFFIX} "
    'KDEB_PKGVERSION="${KERNEL_VERSION}-1+b${KDEB_SUFFIX}"'
)
PERBUILD_MARK = 'KDEB_PKGVERSION="${KERNEL_VERSION}-1+b${KDEB_SUFFIX}"'
bidx = src.find(BINDEB_ANCHOR)
if bidx >= 0:
    # Replace the fixed-version bindeb-pkg line with the per-build-versioned form
    # so the kernel .deb version is strictly monotonic (forces chroot upgrade).
    src = src[:bidx] + BINDEB_REPLACE + src[bidx + len(BINDEB_ANCHOR):]
elif PERBUILD_MARK in src:
    # Already converted on a prior strip-and-reinject run — leave it in place.
    print(f"### {bk}: bindeb-pkg already per-build-versioned — keeping")
else:
    print(f"ERROR: ASK2 v2 bindeb-pkg anchor not found in {bk}", file=sys.stderr)
    sys.exit(1)
# Re-find the (now replaced) bindeb line to anchor the snapshot block after it.
bidx = src.find("KDEB_PKGVERSION=\"${KERNEL_VERSION}-1+b${KDEB_SUFFIX}\"")
if bidx < 0:
    print(f"ERROR: ASK2 per-build bindeb line not found after replace in {bk}", file=sys.stderr)
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
# ASK2 v2 snapshot extraction moved to ci-build-packages.sh (post-build.py, after bindeb-pkg).
# No shell body remains here; importantly, do NOT emit a bare `fi`. The old
# injector inherited an `fi` from a pre-refactor surrounding conditional; after
# the body moved out, it became orphaned and made build-kernel.sh exit 2 AFTER a
# successful bindeb-pkg (run 32324764983).

# === end ASK2 v2 post-bindeb-pkg headers snapshot ===
'''

src = src[:eol+1] + SNAPSHOT_BLOCK + src[eol+1:]

bk.write_text(src)
print(f"### {bk}: ASK2 v2 persistent-key + headers-snapshot blocks injected")
PYEOF

echo "### Kernel setup complete"


# vim: set ft=bash: