/* SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later */
/*
 * dpaa_fman_caps.h - FMan PCD capability bits + HW-offload stub API
 *
 * The FMan PCD blocks (CC trees, HM nodes, Policer profiles, HC dispatch,
 * Parser soft sequences) are unlocked by NXP FMan microcode 210+ which
 * U-Boot loads into MURAM from SPI mtd4 at SoC bring-up.  Public mainline
 * 6.18 cannot consume these blocks because:
 *
 *   1. There is no in-tree probe that reports the loaded ucode version.
 *   2. There are no exported helpers (fman_cc_*, fman_hm_*, fman_pol_*).
 *
 * This header is the M3-3b stub layer for both gaps.  It defines:
 *
 *   - FMAN_CAP_* bits matching dpaa1-afxdp-modernization-spec.md sec 3.5
 *   - dpaa_fman_get_caps() returning a u32 bitmask of available caps
 *   - fman_cc_tree_install/add_key/remove_key/destroy stubs returning
 *     -ENOTSUPP unconditionally (productive implementation lands in
 *     follow-up patches per spec sec 5.4)
 *
 * Until the productive ucode-version detection lands (planned: read
 * FMan ucode header from MURAM at fman_load_firmware time, or a DT
 * property "fsl,fman-ucode-version"), dpaa_fman_get_caps() returns 0
 * unless the operator overrides via the dpaa_fman_caps.force= module
 * parameter for development testing.
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md v5.0 sec 3.5 + sec 5.4.
 */

#ifndef __DPAA_FMAN_CAPS_H
#define __DPAA_FMAN_CAPS_H

#include <linux/types.h>
#include <linux/bits.h>

struct device;
struct fman;
struct fman_cc_static_tree;
struct fman_cc_key;

/* Spec sec 3.5: FMan PCD capability bitmask.  Each bit indicates that
 * the loaded FMan microcode supports the corresponding PCD primitive.
 * Add new bits at the end; do not renumber.
 */
#define FMAN_CAP_CC_EXACT_MATCH   BIT(0)   /* ucode 210+: CC trees */
#define FMAN_CAP_HM_NODES         BIT(1)   /* ucode 210+: HM nodes */
#define FMAN_CAP_POLICER_TRTCM    BIT(2)   /* ucode 210+: srTCM/trTCM */
#define FMAN_CAP_HC_DISPATCH      BIT(3)   /* ucode 210+: Host Command */
#define FMAN_CAP_PARSER_SOFTSEQ   BIT(4)   /* ucode 210+: Parser soft seq */

#define FMAN_CAP_ALL (FMAN_CAP_CC_EXACT_MATCH | FMAN_CAP_HM_NODES | \
		      FMAN_CAP_POLICER_TRTCM | FMAN_CAP_HC_DISPATCH | \
		      FMAN_CAP_PARSER_SOFTSEQ)

/**
 * dpaa_fman_get_caps - return the FMan PCD capability bitmask
 *
 * Currently returns the value of the dpaa_fman_caps.force module
 * parameter (default 0 = "no caps", matching mainline ucode 106
 * behaviour).  Future revisions will probe the loaded ucode at
 * runtime and populate this from FMan firmware headers / DT.
 *
 * Caller (dpaa_eth_probe) stores the result in priv->fman_caps so
 * that per-netdev fman_*_install() helpers can short-circuit to
 * -ENOTSUPP on unsupported silicon/ucode combinations without
 * touching MURAM.
 *
 * Spec sec 3.5: capability layer.
 */
u32  dpaa_fman_get_caps(void);

/**
 * dpaa_fman_caps_log - one-shot KERN_INFO log of the cap bitmask
 *
 * Called from dpaa_eth_probe() for the first netdev only.  Emits one
 * line of the form:
 *
 *   fsl_dpa: FMan PCD caps = 0x00 (mainline ucode 106 / no PCD offload)
 *   fsl_dpa: FMan PCD caps = 0x1F (CC HM POL HC PARSER)
 *
 * so operators can confirm at boot whether the cap-detection layer
 * sees any PCD primitives.  Idempotent across multiple probes.
 */
void dpaa_fman_caps_log(struct device *dev, u32 caps);

/* ------------------------------------------------------------------ *
 * M3-3b STUB API  (spec sec 5.4 CC steering)
 *
 * All four helpers below are unconditional -ENOTSUPP stubs in this
 * patch.  Productive implementations replace the bodies in follow-up
 * patches (0086+ chain).  The API contract is fixed now so consumers
 * (af_xdp_pool.ko qband-select, future vyos-1x set-system-offload-
 * classify CLI, ASK2 flowtable bridge) can call them today and
 * gracefully degrade on ucode-106 silicon.
 * ------------------------------------------------------------------ */

int  fman_cc_tree_install(struct fman *fm, u8 port_id,
			  const struct fman_cc_static_tree *spec);
int  fman_cc_tree_add_key(struct fman *fm, u8 port_id,
			  const struct fman_cc_key *key, u32 *handle);
int  fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle);
void fman_cc_tree_destroy(struct fman *fm, u8 port_id);

/* Opaque types referenced by the stub signatures; concrete layout
 * lands with the productive implementation.  Declaring them here keeps
 * callers source-compatible across the stub -> productive transition.
 */
struct fman_cc_static_tree {
	u32 reserved;          /* productive layout TBD per spec sec 5.4 */
};

struct fman_cc_key {
	u32 reserved;          /* productive layout TBD per spec sec 5.4 */
};

#endif /* __DPAA_FMAN_CAPS_H */