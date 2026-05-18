// SPDX-License-Identifier: GPL-2.0
/*
 * ask_hw.c — hardware-derived information shared with userspace.
 *
 * PR13 (M2.4) populates the ucode-version fields of ASK_CMD_GET_INFO
 * from the QEF (QorIQ Embedded Firmware) microcode blob that U-Boot
 * loads from the SPI flash "fman-ucode" partition (mtd3 on the Mono
 * Gateway DK) into FMan IRAM at boot, and re-publishes via the device
 * tree property /soc/fman@1a00000/fman-firmware/fsl,firmware so any
 * in-kernel consumer can identify the loaded microcode without
 * touching MMIO.
 *
 * PR14g (M2.5g) added the FMan PCD bring-up + body-2 flow dispatcher.
 *
 * PR14j (M2.5j) reshapes the body-2 dispatcher into a true two-stage
 * silicon bypass: ingress KG+CC -> OH-port input FQ ->
 * MANIP{RMV_ETHERNET, INSRT_GENERIC, FIELD_UPDATE_IPV4_FORWARD} ->
 * peer netdev's existing TX FQ (via dpaa_get_tx_fqid()).  The previous
 * single-stage FORWARD_FQ to RX-default-FQ path looped frames back
 * through the kernel NAPI and never achieved the M2 perf gate.
 *
 * See plans/PR14j-DESIGN.md for the full PR14j architecture, the
 * rollback sequence (err_drop_slot -> err_clear_chain -> err_free_insrt),
 * and the 5 open risks called out in §8.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/err.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/in.h>
#include <linux/if_ether.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/xarray.h>
#include <net/net_namespace.h>
#include <linux/fsl/fman_pcd.h>
#include <linux/fsl/dpaa_flow_offload.h>

#include "include/ask_internal.h"

/*
 * Patch 0027 declares fman_bind() / fman_get_pcd() / fman_get_dev() /
 * fman_get_id() and the convenience wrapper fman_pcd_from_of_node()
 * inside <linux/fsl/fman_pcd.h> (re-declared there for OOT consumers
 * since the FMan driver's private fman.h is not exported).
 */

/* QEF blob structural constants (see PR13 comment, preserved verbatim). */
#define ASK_QEF_MAGIC          0x51454601u   /* 'Q' 'E' 'F' 0x01 */
#define ASK_QEF_MAGIC_OFFSET   4
#define ASK_QEF_DESC_OFFSET    8
#define ASK_QEF_DESC_LEN       64
#define ASK_QEF_MIN_LEN        (ASK_QEF_DESC_OFFSET + ASK_QEF_DESC_LEN)

/* Cached version, populated on first successful probe. */
static struct ask_hw_ucode_version ask_hw_cached;
static bool ask_hw_cached_valid;

/*
 * PR14l (2026-05-17): default flipped to true.  Silicon-verified on mono
 * with PR14k (kernel patch 0034 — fman_pcd_oh_port_claim lock-split into
 * 3 phases so MURAM alloc no longer recurses on pcd->lock).  Boot is
 * clean, dmesg reports "ask: hw: PR14j OH-port chain ready (oh_idx=0,
 * input_fqid=0x40)" and there is no hung_task at +122s.
 *
 * History:
 *   PR14j-hotfix (2026-05-16): added this gate (default off) because
 *     pre-PR14k fman_pcd_oh_port_claim() held pcd->lock across
 *     oh_port_alloc_ad_chain() which re-entered fman_pcd_muram_alloc()
 *     taking the same mutex — systemd-modules-load hung_task at +122s.
 *   PR14k (2026-05-17):       split fman_pcd_oh_port_claim() into 3
 *     phases (locked lookup, unlocked construction incl. MURAM alloc,
 *     locked race-check+insert).  Verified on mono with
 *     enable_oh_chain=1 forced via /etc/modprobe.d.
 *   PR14l (2026-05-17):       flipped default to true so fresh boots
 *     arm the OH-port chain without needing the modprobe.d override.
 *
 *   ask.enable_oh_chain=1  (default) — call bringup_oh(); on success
 *                                       silicon-bypass HW offload is
 *                                       live.  On failure (claim error,
 *                                       MURAM exhaustion, …) ask.ko
 *                                       still loads and HW offload
 *                                       falls back to SW.
 *   ask.enable_oh_chain=0            — skip bringup_oh() entirely;
 *                                       KG+CC stays armed but every
 *                                       ask_hw_flow_insert_v4_tcp()
 *                                       returns -ENODEV → SW fallback.
 *                                       Kept as a kill-switch for
 *                                       diagnostics and bisection.
 *
 * Diagnostic memos in qdrant: oh-port-deadlock / muram-alloc-recursion /
 * pcd-lock / recursive-mutex / pr14k / silicon-verified-2026-05-17.
 */
static bool ask_enable_oh_chain = true;
module_param_named(enable_oh_chain, ask_enable_oh_chain, bool, 0444);
MODULE_PARM_DESC(enable_oh_chain,
        "PR14j OH-port silicon-bypass chain bring-up (default on since "
        "PR14l/PR14k — set to 0 only as a kill-switch for diagnostics)");

/* ------------------------------------------------------------------------- */
/* QEF blob parsing                                                           */
/* ------------------------------------------------------------------------- */

static int ask_hw_qef_get_description(const u8 *blob, size_t len, char *desc)
{
        u32 magic;

        if (!blob || len < ASK_QEF_MIN_LEN) {
                ask_pr_warn("hw: firmware blob too short (%zu < %d)\n",
                            len, ASK_QEF_MIN_LEN);
                return -EINVAL;
        }

        magic = ((u32)blob[ASK_QEF_MAGIC_OFFSET]     << 24) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 1] << 16) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 2] <<  8) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 3]);

        if (magic != ASK_QEF_MAGIC) {
                ask_pr_warn("hw: firmware magic mismatch (got 0x%08x, want 0x%08x)\n",
                            magic, ASK_QEF_MAGIC);
                return -EINVAL;
        }

        memcpy(desc, blob + ASK_QEF_DESC_OFFSET, ASK_QEF_DESC_LEN);
        desc[ASK_QEF_DESC_LEN - 1] = '\0';
        return 0;
}

static int ask_hw_parse_desc(const char *desc,
                             struct ask_hw_ucode_version *out)
{
        unsigned int family, major, minor;
        int matched;

        matched = sscanf(desc, "Microcode version %u.%u.%u",
                         &family, &major, &minor);
        if (matched != 3) {
                ask_pr_warn("hw: unrecognised QEF description '%s'\n", desc);
                return -EINVAL;
        }

        if (family > U16_MAX || major > U8_MAX || minor > U8_MAX) {
                ask_pr_warn("hw: QEF version fields out of range: %u.%u.%u\n",
                            family, major, minor);
                return -EINVAL;
        }

        out->family = (u16)family;
        out->major  = (u8)major;
        out->minor  = (u8)minor;
        out->patch  = 0;
        strscpy(out->description, desc, sizeof(out->description));
        return 0;
}

static int ask_hw_probe_ucode_locked(struct ask_hw_ucode_version *out)
{
        struct device_node *np;
        const u8 *blob;
        int blob_len;
        char desc[ASK_QEF_DESC_LEN];
        int rc;

        np = of_find_compatible_node(NULL, NULL, "fsl,fman-firmware");
        if (!np) {
                ask_pr_warn("hw: no fsl,fman-firmware node in device tree\n");
                return -ENODEV;
        }

        blob = of_get_property(np, "fsl,firmware", &blob_len);
        if (!blob || blob_len <= 0) {
                ask_pr_warn("hw: fsl,firmware property missing or empty\n");
                of_node_put(np);
                return -ENOENT;
        }

        rc = ask_hw_qef_get_description(blob, (size_t)blob_len, desc);
        if (rc) {
                of_node_put(np);
                return rc;
        }

        rc = ask_hw_parse_desc(desc, out);
        of_node_put(np);
        if (rc)
                return rc;

        ask_pr_info("hw: FMan microcode %u.%u.%u (\"%s\")\n",
                    out->family, out->major, out->minor, out->description);
        return 0;
}

int ask_hw_ucode_get_version(struct ask_hw_ucode_version *out)
{
        int rc;

        if (!out)
                return -EINVAL;

        if (READ_ONCE(ask_hw_cached_valid)) {
                *out = ask_hw_cached;
                return 0;
        }

        rc = ask_hw_probe_ucode_locked(out);
        if (rc)
                return rc;

        ask_hw_cached = *out;
        WRITE_ONCE(ask_hw_cached_valid, true);
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_ucode_get_version);

/* ------------------------------------------------------------------------- */
/* PR14g/j: FMan PCD bring-up                                                 */
/* ------------------------------------------------------------------------- */

/* Standard FMan parse-result offsets (RM 8.7.3 Table 8-107). */
#define ASK_HW_PR_OFF_L4PROTO   9
#define ASK_HW_PR_OFF_IPV4_SIP  12
#define ASK_HW_PR_OFF_IPV4_DIP  16
#define ASK_HW_PR_OFF_L4_SPORT  20
#define ASK_HW_PR_OFF_L4_DPORT  22

/* KG-concatenated 5-tuple width = SIP(4) + DIP(4) + proto(1) + sport(2) + dport(2). */
#define ASK_HW_V4_KEY_WIDTH     13

/*
 * PR14j: which OH port we use for the v4-TCP forwarding chain.
 *
 * LS1046A exposes 6 OH ports at port@82000..port@87000 (cell-index 0x2..0x7).
 * The fman_pcd_oh.c API uses oh_idx = cell_index - 2, so cell-index 0x2
 * corresponds to oh_idx = 0.  We pick 0 deterministically for v4-TCP;
 * future protocol bring-ups (v4-UDP, v6-TCP) get 1, 2, 3 etc.
 *
 * NOTE: the design memo §2 quotes "OH port 0x2" referring to the
 * DT cell-index; that translates to oh_idx = 0 here.
 */
#define ASK_HW_V4_TCP_OH_IDX    0

/*
 * PR14s (2026-05-18): number of Offline Host ports available on
 * LS1046A FMan v3.  Cell-index 0x2..0x7 → oh_idx 0..5.  ask.ko claims
 * as many as it can at bring-up and hands one out per HW-offloaded
 * flow so the AD chain is never shared / rewritten between flows.
 */
#define ASK_HW_NUM_OH_PORTS     6

/*
 * PR14v (2026-05-18): maximum number of distinct FMan ingress ports
 * that can be HW-accelerated for v4-TCP at the same time.  LS1046A
 * has 8x 1G + 2x 10G MACs; ASK's M2 reference forwards between
 * eth3 (port 8) and eth4 (port 9), so 2 is the immediate target.
 * Sized to 4 to give headroom for adding routed RJ45 ports later
 * (eth0/eth1/eth2 = ports 5/6/2) without re-spinning the kernel.
 *
 * Each entry costs one KGSE_MV silicon slot plus ~256 B of host
 * memory; the KGSE register file has 32 slots total on FMan v3 so
 * 4 is well below the silicon ceiling.
 *
 * Each scheme is created with the same kg_params (saved in
 * ask_hw_pcd.kg_params_v4_tcp at bring-up) and attached to the
 * shared cc_tree, so all schemes funnel into the SAME CC node —
 * the FORWARD_FQ → OH-port AD chain stays single-instance per
 * (cookie, OH-port-slot) pair.
 */
#define ASK_HW_V4_TCP_MAX_BINDS 4

/* PR14v dual-port bind entry. */
struct ask_hw_kg_bind {
        u8                          port_id;     /* FMan MAC cell-index 1..10 */
        struct fman_pcd_kg_scheme  *scheme;      /* per-port KGSE_MV slot */
};

struct ask_hw_pcd {
        struct mutex lock;
        struct fman *fman;             /* PR14j: kept for OH-port claim */
        struct fman_pcd *pcd;
        struct fman_pcd_cc_tree *cc_tree;
        struct fman_pcd_cc_node *cc_v4_tcp;

        /*
         * PR14v (2026-05-18): per-port KG schemes.  Each entry is one
         * KGSE_MV slot bound to its own port; all entries are attached
         * to the same cc_tree above so traffic from any bound port
         * lands in cc_v4_tcp.
         *
         * kg_params_v4_tcp is the recipe used by ask_hw_pcd_build_chain
         * at bring-up; we keep it so ask_hw_port_bind() can spin up
         * additional schemes on later port-bind requests without
         * re-deriving the KG extract layout.
         */
        struct fman_pcd_kg_scheme_params kg_params_v4_tcp;
        struct ask_hw_kg_bind            v4_tcp_binds[ASK_HW_V4_TCP_MAX_BINDS];
        u8                               v4_tcp_bind_count;

        /*
         * PR14w (2026-05-18): per-resource ENOSPC counters.  Exposed
         * via debugfs (ask_debugfs.c) so the operator can pinpoint
         * which silicon resource is exhausted when HW offload
         * silently falls back to SW.  Each counter is bumped under
         * h->lock at the precise return site so the counts are
         * race-free relative to insert / remove ordering.
         *
         * enospc_oh_pool  — bitmap full: every OH port owned by a
         *                   live cookie (insert returns -ENOSPC,
         *                   flow stays in SW).
         * enospc_oh_chain — fman_pcd_oh_port_set_chain failed
         *                   with -ENOSPC (per-OH AD chain MURAM
         *                   exhaustion).
         * enospc_cc_keys  — fman_pcd_cc_node_add_key returned
         *                   -ENOSPC (CC node hit cap-255 silicon
         *                   limit per PR14r).
         * other_enospc    — any other -ENOSPC return inside the
         *                   insert path that does not match the
         *                   three sites above (currently zero —
         *                   reserved for future bring-up).
         */
        u64                              enospc_oh_pool;
        u64                              enospc_oh_chain;
        u64                              enospc_cc_keys;
        u64                              other_enospc;

        /*
         * PR14s additions (2026-05-18): per-flow OH-port pool.
         *
         * LS1046A FMan v3 exposes six Offline Host ports at cell-index
         * 0x2..0x7 → oh_idx 0..5.  ask.ko claims as many as it can at
         * bring-up (graceful — recorded in `oh_pool_count`) and hands
         * one out per HW-offloaded flow.  The old single-OH model
         * (PR14j..PR14r) shared one port across all flows and reset
         * its AD chain at every insert; that race is the M2 gate
         * blocker documented in the PR14r retest memo.
         *
         * `oh_pool_count == 0` is the "no HW backing" sentinel —
         * insert returns -ENODEV and the caller stays on SW.
         *
         * `oh_alloc_bitmap` tracks which pool slots are currently
         * owned by a live cookie.  Always taken under `h->lock`.
         */
        struct fman_pcd_manip   *m_v4_rmv;      /* shared MANIP_RMV_ETHERNET */
        struct fman_pcd_manip   *m_v4_ipv4;     /* shared TTL-- + cksum */
        struct fman_pcd_oh_port *oh_pool[ASK_HW_NUM_OH_PORTS];
        u8                       oh_pool_count;
        unsigned long            oh_alloc_bitmap;

        /*
         * Cookie indirection table.  Index space is 1..U32_MAX (0 is
         * the "no HW backing" sentinel — XA_FLAGS_ALLOC1 skips it).
         * Entries are kzalloc'd in ask_hw_cookie_alloc() and freed in
         * ask_hw_cookie_free().  The struct content (cc_node, key_idx,
         * m_insrt, …) is the per-flow silicon state ask_hw_flow_remove()
         * needs to disarm the slot, destroy the per-flow m_insrt, and
         * release the cookie.
         */
        struct xarray flow_cookies;
};

static struct ask_hw_pcd *ask_hw_pcd_inst;

/* ------------------------------------------------------------------------- */
/* PR14j cookie indirection table helpers                                     */
/* ------------------------------------------------------------------------- */

u32 ask_hw_cookie_alloc(struct ask_hw_pcd *h,
                        const struct ask_hw_flow_cookie *src)
{
        struct ask_hw_flow_cookie *entry;
        u32 cookie = 0;
        int rc;

        if (!h || !src)
                return 0;

        entry = kzalloc(sizeof(*entry), GFP_KERNEL);
        if (!entry)
                return 0;
        *entry = *src;

        rc = xa_alloc(&h->flow_cookies, &cookie, entry,
                      XA_LIMIT(1, U32_MAX), GFP_KERNEL);
        if (rc) {
                kfree(entry);
                return 0;
        }
        return cookie;
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_alloc);

struct ask_hw_flow_cookie *
ask_hw_cookie_lookup(struct ask_hw_pcd *h, u32 cookie)
{
        if (!h || cookie == 0)
                return NULL;
        return xa_load(&h->flow_cookies, cookie);
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_lookup);

void ask_hw_cookie_free(struct ask_hw_pcd *h, u32 cookie)
{
        struct ask_hw_flow_cookie *entry;

        if (!h || cookie == 0)
                return;
        entry = xa_erase(&h->flow_cookies, cookie);
        kfree(entry);
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_free);

/* ------------------------------------------------------------------------- */
/* PR14g KG/CC bring-up (preserved verbatim from body-1)                      */
/* ------------------------------------------------------------------------- */

static int ask_hw_pcd_build_chain(struct ask_hw_pcd *h)
{
        struct fman_pcd_cc_extract extract;
        struct fman_pcd_cc_key_table keys;
        struct fman_pcd_kg_scheme_params *kg_params = &h->kg_params_v4_tcp;
        int rc;

        h->cc_tree = fman_pcd_cc_tree_create(h->pcd, 1);
        if (IS_ERR(h->cc_tree)) {
                rc = PTR_ERR(h->cc_tree);
                h->cc_tree = NULL;
                ask_pr_warn("hw: cc_tree_create failed (%d) - HW offload disabled\n",
                            rc);
                return rc;
        }

        memset(&extract, 0, sizeof(extract));
        extract.type   = FMAN_PCD_CC_EXTRACT_KEY;
        extract.offset = 0;
        extract.size   = ASK_HW_V4_KEY_WIDTH;

        /*
         * PR14r (2026-05-17): pre-size the CC v4-TCP match table to
         * 255 slots — the silicon hard cap (FMAN_PCD_CC_NODE_KEYS_MAX
         * in drivers/net/ethernet/freescale/fman/fman_pcd_cc.c line
         * 127, validated at line 660 of the same file).  The SDK API
         * treats keys->num_keys at create-time as BOTH the initial
         * entry count AND the lifetime capacity (fman_pcd_cc.c line
         * 680: "node->max_keys = keys->num_keys").  Subsequent
         * fman_pcd_cc_node_add_key() calls fail with -ENOSPC once
         * node->num_keys reaches max_keys (line 826).  Worse, the SDK
         * provides NO API to remove a key after add — ask_hw_flow_remove()
         * can only re-program the AD slot to DROP (a "tombstone"; see
         * fman_pcd_cc.c line 882 doc-comment).  So every flow insert is
         * append-only, and the M2 acceptance gate (8 iperf3 -P streams
         * × 2 directions × 2 dpaa block dups + control traffic) burns
         * ~32 slots in the first second.  With max_keys=0 the very
         * first add_key still SEEMS to succeed because slot 0 is the
         * miss-AD row that pre-exists; the second add_key onward fails.
         *
         * 1024 was the original PR14r target for headroom but is
         * rejected by the SDK's validator (-EINVAL at line 660:
         * "keys->num_keys > FMAN_PCD_CC_NODE_KEYS_MAX").  255 is the
         * hardware-imposed ceiling — going above it would require a
         * second CC node and a CC-tree branch, which is out of scope
         * for PR14r.  At 255 the M2 gate has headroom for ~127 unique
         * cookies after PR14r-B dedupe halves the duplicate-arrival
         * burn rate; for the standard 8-stream iperf3 workload that
         * leaves >100 spare slots even before the inevitable
         * tombstone accumulation under flow churn.  v1.1 (PR15) will
         * add a second CC node + tree branch to lift the ceiling.
         *
         * Setting keys.keys = NULL is the documented "pre-allocate
         * empty slots, fill them later via add_key" idiom — the SDK
         * kcalloc()s the in-memory mirror to 255 entries, the MURAM
         * match+AD tables are sized for 256 rows (255 keys + 1 miss),
         * and node->num_keys starts at 0 so add_key happily appends
         * from slot 0 onward.
         *
         * Each slot costs (key_stride + AD_ENTRY_SIZE) = (2*13 + 16)
         * = 42 B of MURAM.  256 rows × 42 B = 10.5 KiB out of the
         * LS1046A's 384 KiB FMan MURAM pool — trivially within budget.
         */
        memset(&keys, 0, sizeof(keys));
        keys.num_keys = 255;
        keys.keys = NULL;
        keys.miss_action.type = FMAN_PCD_ACTION_DROP;

        h->cc_v4_tcp = fman_pcd_cc_node_create(h->cc_tree, &extract, &keys);
        if (IS_ERR(h->cc_v4_tcp)) {
                rc = PTR_ERR(h->cc_v4_tcp);
                h->cc_v4_tcp = NULL;
                ask_pr_warn("hw: cc_node_create v4-TCP failed (%d)\n", rc);
                goto err_tree;
        }

        /*
         * PR14v (2026-05-18): build the KG recipe ONCE and save it
         * inside h.  ask_hw_port_bind() spins up a NEW scheme from
         * this recipe per ingress port (up to ASK_HW_V4_TCP_MAX_BINDS),
         * attaches each to the shared cc_tree, and binds each to its
         * own port.  KGSE_MV is single-port-per-scheme on LS1046A so
         * dual-port classification requires dual schemes.
         *
         * No scheme is created at bring-up — bring-up leaves the
         * silicon armed but quiescent (cc_tree+cc_node ready, KG
         * recipe saved), waiting for the first FLOW_BLOCK_BIND.
         * This avoids burning a KGSE slot on a scheme that may
         * never get a port-bind in some deployment shapes.
         */
        memset(kg_params, 0, sizeof(*kg_params));
        kg_params->id = -1;
        kg_params->use_hash = true;
        kg_params->default_fqid = 0;
        kg_params->num_extracts = 5;

        kg_params->extracts[0].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params->extracts[0].offset = ASK_HW_PR_OFF_IPV4_SIP;
        kg_params->extracts[0].size   = 4;
        kg_params->extracts[0].mask   = 0xff;

        kg_params->extracts[1].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params->extracts[1].offset = ASK_HW_PR_OFF_IPV4_DIP;
        kg_params->extracts[1].size   = 4;
        kg_params->extracts[1].mask   = 0xff;

        kg_params->extracts[2].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params->extracts[2].offset = ASK_HW_PR_OFF_L4PROTO;
        kg_params->extracts[2].size   = 1;
        kg_params->extracts[2].mask   = 0xff;

        kg_params->extracts[3].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params->extracts[3].offset = ASK_HW_PR_OFF_L4_SPORT;
        kg_params->extracts[3].size   = 2;
        kg_params->extracts[3].mask   = 0xff;

        kg_params->extracts[4].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params->extracts[4].offset = ASK_HW_PR_OFF_L4_DPORT;
        kg_params->extracts[4].size   = 2;
        kg_params->extracts[4].mask   = 0xff;

        h->v4_tcp_bind_count = 0;
        memset(h->v4_tcp_binds, 0, sizeof(h->v4_tcp_binds));

        ask_pr_info("hw: FMan PCD chain up (v4-TCP CC node + KG recipe ready; per-port KG schemes deferred to FLOW_BLOCK_BIND)\n");
        return 0;

err_tree:
        fman_pcd_cc_tree_destroy(h->cc_tree);
        h->cc_tree = NULL;
        return rc;
}

/* ------------------------------------------------------------------------- */
/* PR14j OH-port + shared MANIP bring-up                                      */
/* ------------------------------------------------------------------------- */

static int ask_hw_pcd_bringup_oh(struct ask_hw_pcd *h)
{
        struct fman_pcd_manip_params mp;
        struct fman_pcd_oh_port *oh;
        unsigned int i;
        int rc;

        /*
         * PR14s: build the shared MANIPs first.  These are pcd-wide
         * (RMV-Ethernet header strip + IPv4 TTL--/cksum) and are
         * referenced by every per-flow AD chain; per-flow MANIP_INSRT
         * carrying the new L2 header is built at insert time.
         */
        memset(&mp, 0, sizeof(mp));
        mp.type = FMAN_PCD_MANIP_RMV_ETHERNET;
        h->m_v4_rmv = fman_pcd_manip_create(h->pcd, &mp);
        if (IS_ERR_OR_NULL(h->m_v4_rmv)) {
                rc = h->m_v4_rmv ? PTR_ERR(h->m_v4_rmv) : -ENOMEM;
                h->m_v4_rmv = NULL;
                ask_pr_warn("hw: manip_create(RMV_ETHERNET) failed (%d)\n", rc);
                return rc;
        }

        memset(&mp, 0, sizeof(mp));
        mp.type = FMAN_PCD_MANIP_FIELD_UPDATE_IPV4_FORWARD;
        mp.ipv4_forward.recompute_cksum = true;
        mp.ipv4_forward.rewrite_dscp    = false;
        mp.ipv4_forward.new_dscp        = 0;
        h->m_v4_ipv4 = fman_pcd_manip_create(h->pcd, &mp);
        if (IS_ERR_OR_NULL(h->m_v4_ipv4)) {
                rc = h->m_v4_ipv4 ? PTR_ERR(h->m_v4_ipv4) : -ENOMEM;
                h->m_v4_ipv4 = NULL;
                ask_pr_warn("hw: manip_create(IPV4_FORWARD) failed (%d)\n", rc);
                goto err_destroy_rmv;
        }

        /*
         * PR14s: claim every available OH port into the pool.  Partial
         * success is fine — `oh_pool_count >= 1` is enough for HW
         * offload to function (one in-flight HW-offloaded flow at a
         * time, beyond which insert returns -ENOSPC and SW takes over).
         */
        h->oh_pool_count   = 0;
        h->oh_alloc_bitmap = 0;
        for (i = 0; i < ASK_HW_NUM_OH_PORTS; i++) {
                oh = fman_pcd_oh_port_claim(h->fman, i);
                if (IS_ERR_OR_NULL(oh)) {
                        ask_pr_dbg("hw: oh_port_claim(%u) skipped (%ld)\n",
                                   i, oh ? PTR_ERR(oh) : -ENODEV);
                        h->oh_pool[i] = NULL;
                        continue;
                }
                h->oh_pool[i] = oh;
                h->oh_pool_count++;
        }

        if (h->oh_pool_count == 0) {
                ask_pr_warn("hw: no OH ports claimable - HW offload falls back to SW\n");
                rc = -ENODEV;
                goto err_destroy_ipv4;
        }

        ask_pr_info("hw: PR14s OH-port pool ready (claimed %u/%u ports)\n",
                    h->oh_pool_count, ASK_HW_NUM_OH_PORTS);
        return 0;

err_destroy_ipv4:
        fman_pcd_manip_destroy(h->m_v4_ipv4);
        h->m_v4_ipv4 = NULL;
err_destroy_rmv:
        fman_pcd_manip_destroy(h->m_v4_rmv);
        h->m_v4_rmv = NULL;
        return rc;
}

static void ask_hw_pcd_teardown_oh(struct ask_hw_pcd *h)
{
        struct ask_hw_flow_cookie *ck;
        unsigned long idx;
        unsigned int i;

        /*
         * PR14s: drain any per-flow cookies still alive.  Each entry
         * implies a leaked per-flow m_insrt and (if oh_owned) an
         * armed OH-port AD chain.  Disarm the owning OH port's chain
         * first, then destroy the m_insrt, then erase the xarray slot.
         *
         * xa_for_each + xa_erase under the same critical section is
         * safe per the xarray API contract (the iterator caches the
         * next key before yielding to the body).
         */
        xa_for_each(&h->flow_cookies, idx, ck) {
                if (!ck)
                        continue;
                if (ck->oh_owned && ck->oh_idx < ASK_HW_NUM_OH_PORTS &&
                    h->oh_pool[ck->oh_idx]) {
                        (void)fman_pcd_oh_port_set_chain(h->oh_pool[ck->oh_idx],
                                                         NULL, 0, 0);
                }
                if (ck->m_insrt)
                        fman_pcd_manip_destroy(ck->m_insrt);
                xa_erase(&h->flow_cookies, idx);
                kfree(ck);
        }

        /* Release every OH port we successfully claimed at bring-up. */
        for (i = 0; i < ASK_HW_NUM_OH_PORTS; i++) {
                if (h->oh_pool[i]) {
                        /* Belt-and-braces disarm in case a cookie was
                         * leaked without oh_owned set. */
                        (void)fman_pcd_oh_port_set_chain(h->oh_pool[i],
                                                         NULL, 0, 0);
                        fman_pcd_oh_port_release(h->oh_pool[i]);
                        h->oh_pool[i] = NULL;
                }
        }
        h->oh_pool_count   = 0;
        h->oh_alloc_bitmap = 0;

        if (h->m_v4_ipv4) {
                fman_pcd_manip_destroy(h->m_v4_ipv4);
                h->m_v4_ipv4 = NULL;
        }
        if (h->m_v4_rmv) {
                fman_pcd_manip_destroy(h->m_v4_rmv);
                h->m_v4_rmv = NULL;
        }
}

/* ------------------------------------------------------------------------- */
/* Bring-up / teardown                                                        */
/* ------------------------------------------------------------------------- */

int ask_hw_pcd_bringup(void)
{
        struct ask_hw_pcd *h;
        struct device_node *np;
        struct platform_device *pdev;
        struct fman *fman;
        struct fman_pcd *pcd;
        int rc;

        if (ask_hw_pcd_inst) {
                ask_pr_dbg("hw: pcd bringup already done\n");
                return 0;
        }

        /*
         * PR14j: locate the FMan, then derive both the struct fman * (for
         * OH-port claim) and the struct fman_pcd * (for KG/CC/MANIP work)
         * from the same platform device.  Both bind paths are read-only
         * on the FMan driver state — they bump no refcount that we have
         * to drop, the FMan driver owns the lifetime through devm.
         */
        np = of_find_compatible_node(NULL, NULL, "fsl,fman");
        if (!np) {
                ask_pr_info("hw: no fsl,fman in DT - HW offload not available\n");
                return 0;
        }

        pdev = of_find_device_by_node(np);
        of_node_put(np);
        if (!pdev) {
                ask_pr_info("hw: no platform_device for fsl,fman - HW offload not available\n");
                return 0;
        }

        fman = fman_bind(&pdev->dev);
        if (!fman) {
                ask_pr_info("hw: fman_bind() failed - HW offload not available\n");
                put_device(&pdev->dev);
                return 0;
        }

        pcd = fman_get_pcd(fman);
        if (!pcd) {
                ask_pr_info("hw: fman_get_pcd() returned NULL - HW offload not available\n");
                put_device(&pdev->dev);
                return 0;
        }

        h = kzalloc(sizeof(*h), GFP_KERNEL);
        if (!h) {
                put_device(&pdev->dev);
                return -ENOMEM;
        }

        mutex_init(&h->lock);
        h->fman = fman;
        h->pcd  = pcd;
        xa_init_flags(&h->flow_cookies, XA_FLAGS_ALLOC1);

        rc = ask_hw_pcd_build_chain(h);
        if (rc) {
                xa_destroy(&h->flow_cookies);
                mutex_destroy(&h->lock);
                kfree(h);
                put_device(&pdev->dev);
                return 0;
        }

        /*
         * PR14j OH-port chain.  Non-fatal: if claim or shared MANIPs fail,
         * ask_hw_flow_insert_v4_tcp() will see h->oh_pool_count == 0 and
         * return -ENODEV so ask_flow.c falls back to SW.  Frames still
         * traverse the kernel slow path — they just are not silicon-
         * accelerated.
         *
         * PR14l (2026-05-17): the ask.enable_oh_chain module parameter
         * now defaults to true since PR14k fixed the pcd->lock recursive
         * deadlock in fman_pcd_oh_port_claim() (kernel patch 0034 —
         * see top-of-file note + qdrant entry tagged pr14k /
         * silicon-verified-2026-05-17).  Set ask.enable_oh_chain=0
         * as a kill-switch for diagnostics or bisection.
         */
        if (ask_enable_oh_chain) {
                (void)ask_hw_pcd_bringup_oh(h);
        } else {
                ask_pr_info("hw: OH-port chain bring-up SKIPPED (ask.enable_oh_chain=0); HW offload will fall back to SW\n");
        }

        ask_hw_pcd_inst = h;

        /*
         * Drop the platform_device reference taken by of_find_device_by_node.
         * The FMan driver retains its own lifetime tracking via devm; our
         * `struct fman *` remains valid for ask.ko's entire lifetime so
         * long as the FMan driver does not unbind underneath us (a kernel
         * regression that would be visible at NAPI / phylink layers long
         * before it manifested here).
         */
        put_device(&pdev->dev);
        return 0;
}

void ask_hw_pcd_teardown(void)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;

        if (!h)
                return;

        ask_hw_pcd_inst = NULL;

        /* PR14j OH side first — it may dereference shared MANIPs. */
        ask_hw_pcd_teardown_oh(h);

        /*
         * PR14v: destroy every per-port KG scheme.  Each scheme's
         * destroy path internally unbinds its KGSE_MV slot from the
         * port it was bound to and re-clears the KGSE registers, so
         * the silicon goes back to the same quiescent state as
         * before any ask_hw_port_bind() call.
         */
        {
                unsigned int i;
                for (i = 0; i < h->v4_tcp_bind_count; i++) {
                        if (h->v4_tcp_binds[i].scheme) {
                                fman_pcd_kg_scheme_destroy(h->v4_tcp_binds[i].scheme);
                                h->v4_tcp_binds[i].scheme = NULL;
                        }
                        h->v4_tcp_binds[i].port_id = 0;
                }
                h->v4_tcp_bind_count = 0;
        }

        if (h->cc_v4_tcp) {
                fman_pcd_cc_node_destroy(h->cc_v4_tcp);
                h->cc_v4_tcp = NULL;
        }
        if (h->cc_tree) {
                fman_pcd_cc_tree_destroy(h->cc_tree);
                h->cc_tree = NULL;
        }

        xa_destroy(&h->flow_cookies);
        mutex_destroy(&h->lock);
        kfree(h);
        ask_pr_dbg("hw: FMan PCD chain torn down\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
        return ask_hw_pcd_inst;
}

/* ------------------------------------------------------------------------- */
/* PR14v port-bind — per-port KG scheme allocator                             */
/*                                                                            */
/* KGSE_MV silicon-fact: an FMan v3 KG scheme can be bound to exactly one     */
/* port.  Pre-PR14v ask.ko owned a single scheme created at bring-up and the  */
/* second port-bind hit -EBUSY, leaving the second ingress port in SW.       */
/*                                                                            */
/* PR14v inverts this: at bring-up we save the KG recipe (h->kg_params_v4_tcp)*/
/* but allocate ZERO schemes.  Each call to ask_hw_port_bind() that names a   */
/* new port allocates a fresh scheme from the recipe, attaches it to the     */
/* shared h->cc_tree (so the new port's hashed traffic indexes into the same */
/* CC node), and binds it to the requested port.  Idempotent on a port that  */
/* already has a scheme.  Returns -ENOSPC once the per-pcd bind array is     */
/* full (ASK_HW_V4_TCP_MAX_BINDS).                                            */
/* ------------------------------------------------------------------------- */

int ask_hw_port_bind(u8 port_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct fman_pcd_kg_scheme *new_scheme = NULL;
        unsigned int i, slot;
        int rc;

        if (!h)
                return -ENODEV;

        /*
         * fman_pcd_kg_bind_port() rejects port_id == 0 (reserved for
         * OP) and port_id > 10.  LS1046A FMan v3 ports 1..8 are 1G
         * MACs, 9..10 are 10G MACs; we cap externally at 10.
         */
        if (port_id == 0 || port_id > 10) {
                ask_pr_warn("hw: port-bind port_id %u out of range (1..10)\n",
                            port_id);
                return -EINVAL;
        }

        mutex_lock(&h->lock);

        /* Idempotent fast-path: port already has a scheme. */
        for (i = 0; i < h->v4_tcp_bind_count; i++) {
                if (h->v4_tcp_binds[i].port_id == port_id) {
                        mutex_unlock(&h->lock);
                        ask_pr_dbg("hw: port-bind v4-TCP idempotent (port %u, slot %u)\n",
                                   port_id, i);
                        return 0;
                }
        }

        if (h->v4_tcp_bind_count >= ASK_HW_V4_TCP_MAX_BINDS) {
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: port-bind v4-TCP: bind-array full (%u/%u), "
                            "cannot bind port %u — falling back to SW for "
                            "this port\n",
                            h->v4_tcp_bind_count, ASK_HW_V4_TCP_MAX_BINDS,
                            port_id);
                return -ENOSPC;
        }
        slot = h->v4_tcp_bind_count;

        mutex_unlock(&h->lock);

        /*
         * Heavy work (scheme_create, attach_cc, bind_port) is done
         * outside h->lock because the underlying fman_pcd_* APIs each
         * take pcd->lock internally — nesting our mutex around them
         * would risk inversion against the OH-port claim path which
         * also touches pcd->lock.
         */
        new_scheme = fman_pcd_kg_scheme_create(h->pcd, &h->kg_params_v4_tcp);
        if (IS_ERR_OR_NULL(new_scheme)) {
                rc = new_scheme ? PTR_ERR(new_scheme) : -ENOMEM;
                ask_pr_warn("hw: port-bind v4-TCP: kg_scheme_create for port %u failed: %d\n",
                            port_id, rc);
                return rc;
        }

        rc = fman_pcd_kg_attach_cc(new_scheme, h->cc_tree);
        if (rc) {
                ask_pr_warn("hw: port-bind v4-TCP: kg_attach_cc for port %u failed: %d\n",
                            port_id, rc);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return rc;
        }

        rc = fman_pcd_kg_bind_port(new_scheme, port_id);
        if (rc) {
                ask_pr_warn("hw: port-bind v4-TCP: kg_bind_port(port=%u) failed: %d\n",
                            port_id, rc);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return rc;
        }

        /* Re-acquire h->lock to commit the new bind into the array. */
        mutex_lock(&h->lock);

        /*
         * Re-check that no concurrent binder filled the slot we
         * reserved.  Under normal flow_offload bind ordering this
         * cannot happen (the flow_block_cb path serialises through
         * the nft flowtable), but we re-check to keep the function
         * safe under hypothetical reentrant callers.
         */
        if (h->v4_tcp_bind_count >= ASK_HW_V4_TCP_MAX_BINDS) {
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: port-bind v4-TCP: bind-array filled by concurrent binder before slot %u for port %u could commit; destroying scheme\n",
                            slot, port_id);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return -ENOSPC;
        }
        /* Re-check that port wasn't bound by a concurrent caller. */
        for (i = 0; i < h->v4_tcp_bind_count; i++) {
                if (h->v4_tcp_binds[i].port_id == port_id) {
                        mutex_unlock(&h->lock);
                        ask_pr_warn("hw: port-bind v4-TCP: port %u was bound concurrently in slot %u; destroying duplicate scheme\n",
                                    port_id, i);
                        fman_pcd_kg_scheme_destroy(new_scheme);
                        return 0;
                }
        }

        slot = h->v4_tcp_bind_count;
        h->v4_tcp_binds[slot].port_id = port_id;
        h->v4_tcp_binds[slot].scheme  = new_scheme;
        h->v4_tcp_bind_count++;

        mutex_unlock(&h->lock);

        ask_pr_info("hw: FMan PCD v4-TCP scheme bound to port %u (slot %u/%u — HW offload active)\n",
                    port_id, slot + 1, ASK_HW_V4_TCP_MAX_BINDS);
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_port_bind);

/* ------------------------------------------------------------------------- */
/* Legacy hw_flow_id pack/unpack (debugfs / kunit use only after PR14j)       */
/* ------------------------------------------------------------------------- */

u32 ask_priv_pack_hw_flow_id(u16 node_token, u16 key_idx)
{
        return ((u32)node_token << 16) | (u32)key_idx;
}
EXPORT_SYMBOL_GPL(ask_priv_pack_hw_flow_id);

void ask_priv_unpack_hw_flow_id(u32 hw_flow_id,
                                u16 *node_token, u16 *key_idx)
{
        if (node_token)
                *node_token = (u16)(hw_flow_id >> 16);
        if (key_idx)
                *key_idx    = (u16)(hw_flow_id & 0xffffu);
}
EXPORT_SYMBOL_GPL(ask_priv_unpack_hw_flow_id);

/* ------------------------------------------------------------------------- */
/* PR14j: per-flow OH-chain insert / remove                                   */
/* ------------------------------------------------------------------------- */

/*
 * mac_is_zero() — defensive check used at insert time.  The
 * ask_flow_offload.c FLOW_CLS_REPLACE path is expected to populate
 * next_hop_mac/egress_mac via neigh_lookup() before invoking the
 * dispatcher.  If either MAC is still zero we MUST refuse the HW
 * insert (otherwise the OH chain would push a broadcast/unspec L2
 * header and frames would be dropped or misrouted on the egress
 * link).  -EAGAIN is the documented signal for "neighbour not yet
 * resolved, keep this flow in SW for now".
 */
static bool mac_is_zero(const u8 *m)
{
        u8 acc = m[0] | m[1] | m[2] | m[3] | m[4] | m[5];
        return acc == 0;
}

/*
 * ask_hw_resolve_oif_tx_fqid() — look up the peer netdev's per-queue
 * TX FQID via the in-tree export from patch 0031.  Returns the FQID
 * the OH-port AD chain's trailing FORWARD_FQ should aim at.  -ENODEV
 * propagates from non-DPAA netdevs so the caller knows to SW-fallback.
 */
static int ask_hw_resolve_oif_tx_fqid(u32 oif, u32 *fqid)
{
        struct net_device *dev;
        int rc;

        rcu_read_lock();
        dev = dev_get_by_index_rcu(&init_net, oif);
        if (!dev) {
                rcu_read_unlock();
                return -ENODEV;
        }
        rc = dpaa_get_tx_fqid(dev, /*queue=*/0, fqid);
        rcu_read_unlock();
        return rc;
}

static int ask_hw_flow_insert_v4_tcp(struct ask_hw_pcd *h,
                                     const struct ask_flow_key *key,
                                     u32 oif, u32 action_flags,
                                     u32 *out_hw_id)
{
        struct fman_pcd_cc_key_entry entry;
        struct fman_pcd_action *act;
        struct fman_pcd_manip_params insrt_params;
        struct fman_pcd_manip *manips[3];
        struct ask_hw_flow_cookie ck = { 0 };
        u32 oh_input_fqid;
        u32 peer_tx_fqid;
        u32 cookie;
        int slot;
        int rc;

        (void)action_flags;

        /* PR14s gate: bring-up may have failed; behave like "no HW". */
        if (!h->oh_pool_count || !h->m_v4_rmv || !h->m_v4_ipv4)
                return -ENODEV;

        /*
         * Neighbour MUST be resolved before we burn silicon resources.
         * ask_flow_offload.c populates these fields via neigh_lookup()
         * with NUD_CONNECTED gating — but defence in depth: if either
         * MAC is still zero, refuse and let SW handle the flow.
         */
        if (mac_is_zero(key->next_hop_mac) || mac_is_zero(key->egress_mac))
                return -EAGAIN;

        /* 1. Resolve peer egress TX FQ. */
        rc = ask_hw_resolve_oif_tx_fqid(oif, &peer_tx_fqid);
        if (rc)
                return rc;        /* -ENODEV / -ERANGE propagate */

        /* 2. Build per-flow MANIP_INSRT_GENERIC with the new L2 header. */
        memset(&insrt_params, 0, sizeof(insrt_params));
        insrt_params.type = FMAN_PCD_MANIP_INSRT_GENERIC;
        insrt_params.insrt_generic.size = ETH_HLEN;
        memcpy(&insrt_params.insrt_generic.hdr[0],
               &key->next_hop_mac[0], ETH_ALEN);
        memcpy(&insrt_params.insrt_generic.hdr[6],
               &key->egress_mac[0],   ETH_ALEN);
        insrt_params.insrt_generic.hdr[12] = (ETH_P_IP >> 8) & 0xff;
        insrt_params.insrt_generic.hdr[13] =  ETH_P_IP        & 0xff;

        ck.m_insrt = fman_pcd_manip_create(h->pcd, &insrt_params);
        if (IS_ERR_OR_NULL(ck.m_insrt)) {
                rc = ck.m_insrt ? PTR_ERR(ck.m_insrt) : -ENOMEM;
                ck.m_insrt = NULL;
                return rc;
        }

        /*
         * 3. PR14s: allocate a dedicated OH port from the pool for
         *    this flow.  Each OH port's AD chain is owned by exactly
         *    one cookie for its lifetime — no inter-flow rewrite race.
         *
         *    Pool exhaustion returns -ENOSPC; ask_flow.c treats this
         *    like -ENODEV / -EOPNOTSUPP and falls back to SW.
         */
        {
                int oh_slot;

                mutex_lock(&h->lock);
                oh_slot = -1;
                {
                        unsigned int i;
                        for (i = 0; i < ASK_HW_NUM_OH_PORTS; i++) {
                                if (!h->oh_pool[i])
                                        continue;
                                if (h->oh_alloc_bitmap & BIT(i))
                                        continue;
                                h->oh_alloc_bitmap |= BIT(i);
                                oh_slot = (int)i;
                                break;
                        }
                }
                mutex_unlock(&h->lock);

                if (oh_slot < 0) {
                        /*
                         * PR14w site (1/3): OH-port pool fully owned.
                         * Bump the per-resource ENOSPC counter under
                         * h->lock so debugfs sees a coherent read, and
                         * print a ratelimited info banner so the
                         * operator can see in dmesg WHICH resource was
                         * exhausted (vs a generic -ENOSPC which could
                         * come from the chain or the CC keys).
                         */
                        mutex_lock(&h->lock);
                        h->enospc_oh_pool++;
                        mutex_unlock(&h->lock);
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at OH pool "
                                "(all %u/%u OH ports owned; flow stays in SW)\n",
                                h->oh_pool_count, ASK_HW_NUM_OH_PORTS);
                        rc = -ENOSPC;
                        goto err_free_insrt;
                }
                ck.oh_idx   = (u8)oh_slot;
                ck.oh_owned = true;
        }

        manips[0] = h->m_v4_rmv;
        manips[1] = ck.m_insrt;
        manips[2] = h->m_v4_ipv4;

        mutex_lock(&h->lock);
        rc = fman_pcd_oh_port_set_chain(h->oh_pool[ck.oh_idx],
                                        manips, 3, peer_tx_fqid);
        if (rc == -ENOSPC)
                h->enospc_oh_chain++;   /* PR14w site (2/3) */
        mutex_unlock(&h->lock);
        if (rc) {
                if (rc == -ENOSPC)
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at "
                                "oh_port_set_chain(oh_idx=%u) — per-OH AD-chain "
                                "MURAM exhausted; flow stays in SW\n",
                                ck.oh_idx);
                else
                        ask_pr_warn("hw: oh_port_set_chain(oh_idx=%u) failed (%d)\n",
                                    ck.oh_idx, rc);
                goto err_release_oh_slot;
        }

        oh_input_fqid = fman_pcd_oh_port_input_fqid(h->oh_pool[ck.oh_idx]);

        /* 4. Install ingress CC key with FORWARD_FQ -> oh_input_fqid. */
        memset(&entry, 0, sizeof(entry));
        memcpy(&entry.key[0],  &key->src_ip[0], 4);
        memcpy(&entry.key[4],  &key->dst_ip[0], 4);
        entry.key[8] = IPPROTO_TCP;
        memcpy(&entry.key[9],  &key->sport, 2);
        memcpy(&entry.key[11], &key->dport, 2);
        memset(&entry.mask[0], 0xff, ASK_HW_V4_KEY_WIDTH);

        act = &entry.action;
        act->type            = FMAN_PCD_ACTION_FORWARD_FQ;
        act->forward_fq.fqid = oh_input_fqid;

        mutex_lock(&h->lock);
        slot = fman_pcd_cc_node_add_key(h->cc_v4_tcp, &entry);
        if (slot == -ENOSPC)
                h->enospc_cc_keys++;    /* PR14w site (3/3) */
        mutex_unlock(&h->lock);
        if (slot < 0) {
                rc = slot;
                if (slot == -ENOSPC)
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at "
                                "cc_node_add_key v4-TCP — CC node hit "
                                "cap-255 silicon limit (PR14r); flow stays in SW\n");
                else
                        ask_pr_warn("hw: cc_node_add_key v4-TCP failed (%d)\n", slot);
                goto err_clear_chain;
        }
        if (slot > U16_MAX) {
                rc = -EOVERFLOW;
                goto err_drop_slot;
        }

        /* 5. Snapshot cookie fields. */
        ck.cc_node      = h->cc_v4_tcp;
        ck.key_idx      = (u16)slot;
        ck.m_rmv        = h->m_v4_rmv;
        ck.m_ipv4       = h->m_v4_ipv4;
        ck.sink_ifindex = (int)oif;
        ck.sink_fqid    = peer_tx_fqid;

        /* 6. Stash in cookie table; the returned cookie is the public hw_id. */
        cookie = ask_hw_cookie_alloc(h, &ck);
        if (cookie == 0) {
                rc = -ENOMEM;
                goto err_drop_slot;
        }
        *out_hw_id = cookie;
        return 0;

err_drop_slot:
        {
                struct fman_pcd_action drop = { .type = FMAN_PCD_ACTION_DROP };
                mutex_lock(&h->lock);
                (void)fman_pcd_cc_node_modify_next_action(h->cc_v4_tcp,
                                                          ck.key_idx, &drop);
                mutex_unlock(&h->lock);
        }
err_clear_chain:
        /*
         * PR14s: disarm THIS flow's OH port (we own it exclusively).
         */
        mutex_lock(&h->lock);
        if (ck.oh_owned && ck.oh_idx < ASK_HW_NUM_OH_PORTS &&
            h->oh_pool[ck.oh_idx]) {
                (void)fman_pcd_oh_port_set_chain(h->oh_pool[ck.oh_idx],
                                                 NULL, 0, 0);
        }
        mutex_unlock(&h->lock);
err_release_oh_slot:
        mutex_lock(&h->lock);
        if (ck.oh_owned && ck.oh_idx < ASK_HW_NUM_OH_PORTS)
                h->oh_alloc_bitmap &= ~BIT(ck.oh_idx);
        mutex_unlock(&h->lock);
        ck.oh_owned = false;
err_free_insrt:
        fman_pcd_manip_destroy(ck.m_insrt);
        return rc;
}

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h;

        if (!key || !out_hw_id)
                return -EINVAL;

        h = ask_hw_pcd_get();
        if (!h)
                return -ENODEV;

        if (key->l3_proto == ASK_FLOW_L3_IPV4 &&
            key->l4_proto == IPPROTO_TCP)
                return ask_hw_flow_insert_v4_tcp(h, key, oif, action_flags,
                                                 out_hw_id);

        return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_insert);

int ask_hw_flow_remove(u32 hw_flow_id)
{
        struct ask_hw_pcd *h;
        struct ask_hw_flow_cookie *ck;
        struct fman_pcd_action drop = { .type = FMAN_PCD_ACTION_DROP };

        h = ask_hw_pcd_get();
        if (!h)
                return -ENODEV;

        /*
         * PR14j: hw_flow_id is now an xarray cookie (>= 1).  Cookie 0
         * is the SW-only sentinel — return 0 unconditionally so
         * ask_flow_remove() can call us without inspecting the id.
         */
        if (hw_flow_id == 0)
                return 0;

        ck = ask_hw_cookie_lookup(h, hw_flow_id);
        if (!ck) {
                /*
                 * PR14t (2026-05-18): unknown-cookie is benign on the
                 * remove path — it most commonly comes from the second
                 * DESTROY emitted by the duplicate eth4 binder after
                 * PR14r REPLACE dedupe (the first DESTROY already
                 * freed this cookie).  Returning -EINVAL here caused
                 * ~10 spurious warnings per M2 run that masked the
                 * actual M2 gate signal in dmesg.  Switch to a
                 * ratelimited info log + return 0; the SW table has
                 * already released ownership and there is nothing
                 * left to free in silicon.
                 */
                pr_info_ratelimited("ask: hw: remove: unknown cookie 0x%08x (already freed?)\n",
                                    hw_flow_id);
                return 0;
        }

        /*
         * PR14s: disarm the ingress CC slot AND this flow's OH-port
         * AD chain in one critical section, then free the per-flow
         * m_insrt.  The shared m_rmv / m_ipv4 stay alive; they belong
         * to ask_hw_pcd and are destroyed in teardown.  Returning the
         * OH port to the pool (clearing the bitmap bit) is also done
         * under h->lock so a concurrent insert sees the slot free.
         */
        mutex_lock(&h->lock);
        (void)fman_pcd_cc_node_modify_next_action(ck->cc_node, ck->key_idx,
                                                  &drop);
        if (ck->oh_owned && ck->oh_idx < ASK_HW_NUM_OH_PORTS &&
            h->oh_pool[ck->oh_idx]) {
                (void)fman_pcd_oh_port_set_chain(h->oh_pool[ck->oh_idx],
                                                 NULL, 0, 0);
                h->oh_alloc_bitmap &= ~BIT(ck->oh_idx);
        }
        mutex_unlock(&h->lock);

        if (ck->m_insrt)
                fman_pcd_manip_destroy(ck->m_insrt);

        ask_hw_cookie_free(h, hw_flow_id);
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_remove);

int ask_hw_flow_query_stats(u32 hw_flow_id, u64 *packets, u64 *bytes)
{
        (void)hw_flow_id;
        (void)packets;
        (void)bytes;
        return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_query_stats);

/* ------------------------------------------------------------------------- */
/* Module hooks                                                               */
/* ------------------------------------------------------------------------- */

int ask_hw_init(void)
{
        struct ask_hw_ucode_version v;
        int rc;

        rc = ask_hw_ucode_get_version(&v);
        if (rc)
                ask_pr_warn("hw: ucode version probe failed (%d); ASK_CMD_GET_INFO will report zeros\n",
                            rc);

        (void)ask_hw_pcd_bringup();
        return 0;
}

void ask_hw_exit(void)
{
        ask_hw_pcd_teardown();
        WRITE_ONCE(ask_hw_cached_valid, false);
}