// SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later
/*
 * dpaa_fman_caps.c - FMan PCD capability detection + HW-offload stubs
 *
 * Observability-only stub for spec sec 3.5 (cap detection) and sec 5.4
 * (CC steering API).  All productive paths return -ENOTSUPP; the only
 * runtime knob is the dpaa_fman_caps.force= module parameter which
 * allows developers to simulate "ucode 210 loaded" for unit testing
 * downstream consumers without flashing real ucode.
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md v5.0 sec 3.5 + sec 5.4.
 */

#include <linux/bitops.h>
#include <linux/device.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/printk.h>
#include <linux/types.h>

#include "dpaa_fman_caps.h"

/* dpaa_fman_caps.force=<u32>
 *
 * Default 0: report "no PCD caps" matching mainline ucode 106 silicon.
 * Operators can set bits per FMAN_CAP_* to simulate ucode 210 loaded
 * for development / CI of downstream consumers.  This will be removed
 * once the productive ucode-version probe lands (planned: parse FMan
 * firmware header from MURAM at fman_load_firmware() time).
 */
static u32 dpaa_fman_caps_force;
module_param_named(force, dpaa_fman_caps_force, uint, 0444);
MODULE_PARM_DESC(force,
 "Force FMan PCD capability bitmask (FMAN_CAP_* bits) for dev/CI. "
 "Default 0 = no PCD offload (mainline ucode 106 behaviour).");

u32 dpaa_fman_get_caps(void)
{
return dpaa_fman_caps_force & FMAN_CAP_ALL;
}
EXPORT_SYMBOL_GPL(dpaa_fman_get_caps);

void dpaa_fman_caps_log(struct device *dev, u32 caps)
{
static bool logged;

if (logged)
return;
logged = true;

if (!caps) {
dev_info(dev,
 "FMan PCD caps = 0x00 (mainline ucode 106 / no PCD offload)\n");
return;
}

dev_info(dev,
 "FMan PCD caps = 0x%02x (%s%s%s%s%s)\n",
 caps,
 (caps & FMAN_CAP_CC_EXACT_MATCH) ? "CC "     : "",
 (caps & FMAN_CAP_HM_NODES)       ? "HM "     : "",
 (caps & FMAN_CAP_POLICER_TRTCM)  ? "POL "    : "",
 (caps & FMAN_CAP_HC_DISPATCH)    ? "HC "     : "",
 (caps & FMAN_CAP_PARSER_SOFTSEQ) ? "PARSER"  : "");
}
EXPORT_SYMBOL_GPL(dpaa_fman_caps_log);

/* ------------------------------------------------------------------ *
 * M3-3b stub: CC (Coarse Classification) steering API.
 *
 * Productive implementation replaces these bodies in a follow-up patch
 * per spec sec 5.4.  Until then every entry-point returns -ENOTSUPP so
 * downstream consumers (af_xdp_pool qband-select, ASK2 flowtable
 * bridge, vyos-1x set-system-offload-classify CLI) can wire calls
 * today and gracefully degrade on ucode-106 silicon.
 * ------------------------------------------------------------------ */

int fman_cc_tree_install(struct fman *fm, u8 port_id,
 const struct fman_cc_static_tree *spec)
{
return -ENOTSUPP;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_install);

int fman_cc_tree_add_key(struct fman *fm, u8 port_id,
 const struct fman_cc_key *key, u32 *handle)
{
return -ENOTSUPP;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_add_key);

int fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle)
{
return -ENOTSUPP;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_remove_key);

void fman_cc_tree_destroy(struct fman *fm, u8 port_id)
{
/* no-op stub */
}
EXPORT_SYMBOL_GPL(fman_cc_tree_destroy);

MODULE_LICENSE("Dual BSD/GPL");
MODULE_DESCRIPTION("DPAA1 FMan PCD capability detection + HW-offload stubs");