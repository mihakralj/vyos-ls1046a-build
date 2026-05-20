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
 * PR14x (2026-05-18): the OH-port silicon-bypass model (PR14j..PR14w)
 * has been retired in favour of the single-stage MANIP-chain
 * primitive landed in kernel patch 0036.  The ask.enable_oh_chain
 * module parameter is gone with it; HW offload bring-up no longer
 * has any kill-switch knob because there is no longer a deadlock /
 * MURAM-recursion failure mode to bisect around.  See
 * plans/PR14x-DESIGN.md and the cookie-struct comment block in
 * include/ask_internal.h for the architectural rationale.
 */

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
 * PR14z5 (2026-05-19): one independent classifier pipeline per
 * direction.  Each pipeline owns its own cc_tree + cc_v4_tcp + KG
 * scheme + bound port.  FWD = first-arrival ingress (forward
 * direction, eth3 RX on Mono Gateway).  REV = second-arrival ingress
 * (reverse direction, eth4 RX).
 *
 * Rationale: PR14z4 measured that binding a second KG scheme to the
 * same cc_tree HALVES forward-direction throughput (6.83 → 5.24 Gbps)
 * without enabling reverse-direction silicon.  Working hypothesis:
 * FMan v3 cannot usefully share a single cc_tree across two schemes
 * — either the KG hash distributions of the two schemes conflict in
 * the same CC slot population, or a shared QBMan resource is starved.
 * The fix is to give each direction its own classifier tree.
 *
 * bound_pid == 0xff means the pipeline is unbound (no scheme yet).
 * cc_tree + cc_v4_tcp are created up front at bring-up; scheme is
 * lazily allocated on first ask_hw_port_bind() for this direction.
 */
struct ask_hw_pipeline {
        struct fman_pcd_cc_tree   *cc_tree;
        struct fman_pcd_cc_node   *cc_v4_tcp;
        struct fman_pcd_kg_scheme *scheme;     /* single port per pipeline */
        struct fman_port          *fman_port;  /* PR14z7: arms FMBM_RFPNE → NIA_ENG_HWK */
        u8                         bound_pid;  /* 0xff = unbound */
};

struct ask_hw_pcd {
        struct mutex lock;
        struct fman *fman;             /* PR14j: kept for OH-port claim */
        struct fman_pcd *pcd;

        /*
         * PR14z5: per-direction classifier pipelines.  Indexed by
         * enum ask_hw_dir.  Each pipeline owns exactly one ingress
         * port; the two cc_trees are completely independent.
         *
         * kg_params_v4_tcp is the recipe shared between pipelines —
         * the KG extract layout (SIP/DIP/proto/sport/dport) is the
         * same for both directions; only the resulting hash output
         * gets fed into different cc_trees.
         */
        struct fman_pcd_kg_scheme_params kg_params_v4_tcp;
        struct ask_hw_pipeline           pipe[ASK_HW_DIR_NR];

        /*
         * PR14w/PR14x (2026-05-18): per-resource ENOSPC counters.
         * Exposed via debugfs (ask_debugfs.c) so the operator can
         * pinpoint which silicon resource is exhausted when HW
         * offload silently falls back to SW.  Each counter is
         * bumped under h->lock at the precise return site so the
         * counts are race-free relative to insert / remove ordering.
         *
         * enospc_chain_muram — fman_pcd_manip_chain_create() returned
         *                      -ENOSPC (PR14x kernel patch 0036
         *                      primitive: MURAM exhaustion building
         *                      the fused HMCT chain handle).
         * enospc_cc_keys     — fman_pcd_cc_node_add_key returned
         *                      -ENOSPC (CC node hit cap-255 silicon
         *                      limit per PR14r).
         * other_enospc       — any other -ENOSPC return inside the
         *                      insert path that does not match the
         *                      two sites above (currently zero —
         *                      reserved for future bring-up).
         *
         * The legacy enospc_oh_pool / enospc_oh_chain counters from
         * PR14w are retired: the OH-port pool model they tracked no
         * longer exists (see PR14x).
         */
        u64                              enospc_chain_muram;
        u64                              enospc_cc_keys;
        u64                              other_enospc;

        /*
         * PR14x shared MANIPs (2026-05-18).  These two pcd-wide
         * handles are created at bring-up and freed at teardown.
         * Every per-flow chain handle returned by
         * fman_pcd_manip_chain_create() fuses these (plus the
         * per-flow m_insrt) into a single HMCT consumed directly by
         * the ingress CC key's FMAN_PCD_ACTION_MANIPULATE arm — no
         * Offline Host port hop, no two-stage AD routing.
         *
         * If either is NULL the HW insert path returns -ENODEV and
         * the flow stays in SW (graceful degradation).
         */
        struct fman_pcd_manip   *m_v4_rmv;      /* shared MANIP_RMV_ETHERNET */
        struct fman_pcd_manip   *m_v4_ipv4;     /* shared TTL-- + cksum */

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
        unsigned int d;

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
        /*
         * PR14z5 (2026-05-19 hotfix): halve per-pipeline capacity
         * from 255 → 127 so the two pipelines together fit the same
         * MURAM budget as the single-pipeline PR14z3 used.  127×2 =
         * 254 cumulative slots, only one fewer than PR14z3, and the
         * M2 workload (16 active 5-tuples) fits comfortably in 127
         * slots per direction.
         *
         * Measured 2026-05-19: with num_keys=255 per pipeline, the
         * second fman_pcd_cc_node_create() returns -ENOMEM at boot
         * because the PCD MURAM allocator cannot honour back-to-
         * back 256-slot allocations.  Halving each pipeline gives
         * (127+1)*(2*13 + 16) = 5,376 B per pipeline = 10,752 B
         * total vs PR14z3's single 256*(2*13+16) = 10,752 B — same
         * footprint, split across two trees.
         */
        memset(&keys, 0, sizeof(keys));
        keys.num_keys = 127;
        keys.keys = NULL;
        keys.miss_action.type = FMAN_PCD_ACTION_DROP;

        /*
         * PR14z5: build ONE cc_tree + cc_v4_tcp per direction.  Each
         * pipeline gets its own 255-slot match table so direction
         * pressure is decoupled (a 255-flow forward burst no longer
         * starves reverse).  The KG extract recipe below is shared
         * across pipelines — only the resulting hash output is fed
         * into different cc_trees.
         */
        for (d = 0; d < ASK_HW_DIR_NR; d++) {
                struct ask_hw_pipeline *p = &h->pipe[d];

                p->bound_pid = 0xff;
                p->scheme    = NULL;

                p->cc_tree = fman_pcd_cc_tree_create(h->pcd, 1);
                if (IS_ERR(p->cc_tree)) {
                        rc = PTR_ERR(p->cc_tree);
                        p->cc_tree = NULL;
                        ask_pr_warn("hw: cc_tree_create dir=%u failed (%d)\n",
                                    d, rc);
                        goto err_unwind;
                }

                p->cc_v4_tcp = fman_pcd_cc_node_create(p->cc_tree, &extract,
                                                      &keys);
                if (IS_ERR(p->cc_v4_tcp)) {
                        rc = PTR_ERR(p->cc_v4_tcp);
                        p->cc_v4_tcp = NULL;
                        ask_pr_warn("hw: cc_node_create v4-TCP dir=%u failed (%d)\n",
                                    d, rc);
                        goto err_unwind;
                }
        }

        /*
         * PR14z5: build the KG recipe ONCE and save it inside h.
         * ask_hw_port_bind(pid, dir) spins up a NEW scheme from this
         * recipe per direction, attaches it to h->pipe[dir].cc_tree,
         * and binds it to the requested port.  KGSE_MV is single-
         * port-per-scheme on LS1046A; each pipeline owns exactly one
         * KGSE slot bound to its own ingress port.
         *
         * No scheme is created at bring-up — bring-up leaves the
         * silicon armed but quiescent (cc_tree+cc_node ready, KG
         * recipe saved), waiting for the first FLOW_BLOCK_BIND.
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

        ask_pr_info("hw: FMan PCD chain up — PR14z5 dual-pipeline v4-TCP (FWD + REV cc_trees + KG recipe ready; per-direction KG schemes deferred to FLOW_BLOCK_BIND)\n");
        return 0;

err_unwind:
        for (d = 0; d < ASK_HW_DIR_NR; d++) {
                struct ask_hw_pipeline *p = &h->pipe[d];
                if (p->cc_v4_tcp) {
                        fman_pcd_cc_node_destroy(p->cc_v4_tcp);
                        p->cc_v4_tcp = NULL;
                }
                if (p->cc_tree) {
                        fman_pcd_cc_tree_destroy(p->cc_tree);
                        p->cc_tree = NULL;
                }
        }
        return rc;
}

/* ------------------------------------------------------------------------- */
/* PR14x shared-MANIP bring-up / teardown                                     */
/* ------------------------------------------------------------------------- */

/*
 * Build the two pcd-wide shared MANIPs (RMV_ETHERNET, IPv4 TTL--/cksum)
 * consumed by every per-flow chain handle.  Non-fatal: on failure,
 * h->m_v4_rmv / h->m_v4_ipv4 stay NULL and ask_hw_flow_insert_v4_tcp()
 * gates on them so the flow falls back to SW.
 */
static int ask_hw_pcd_bringup_shared_manips(struct ask_hw_pcd *h)
{
        struct fman_pcd_manip_params mp;
        int rc;

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
                fman_pcd_manip_destroy(h->m_v4_rmv);
                h->m_v4_rmv = NULL;
                return rc;
        }

        ask_pr_info("hw: PR14x shared MANIPs ready (RMV_ETHERNET + IPv4 TTL--/cksum)\n");
        return 0;
}

/*
 * Drain any per-flow cookies that survived to teardown (leaked refs),
 * destroying each cookie's per-flow MANIP_INSRT_GENERIC and chain
 * handle, then release the two pcd-wide shared MANIPs.
 *
 * xa_for_each + xa_erase under the same critical section is safe per
 * the xarray API contract (the iterator caches the next key before
 * yielding to the body).
 */
static void ask_hw_pcd_teardown_shared_manips(struct ask_hw_pcd *h)
{
        struct ask_hw_flow_cookie *ck;
        unsigned long idx;

        xa_for_each(&h->flow_cookies, idx, ck) {
                if (!ck)
                        continue;
                if (ck->manip_chain)
                        fman_pcd_manip_chain_destroy(ck->manip_chain);
                if (ck->m_insrt)
                        fman_pcd_manip_destroy(ck->m_insrt);
                xa_erase(&h->flow_cookies, idx);
                kfree(ck);
        }

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
         * PR14x (2026-05-18): build the two pcd-wide shared MANIPs.
         * Non-fatal — if either fails, h->m_v4_rmv / h->m_v4_ipv4
         * stay NULL and ask_hw_flow_insert_v4_tcp() returns -ENODEV
         * so ask_flow.c falls back to SW for every flow.  Frames
         * still traverse the kernel slow path; they just are not
         * silicon-accelerated.
         */
        (void)ask_hw_pcd_bringup_shared_manips(h);

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
        unsigned int d;

        if (!h)
                return;

        ask_hw_pcd_inst = NULL;

        /* PR14x: drain cookies + release shared MANIPs. */
        ask_hw_pcd_teardown_shared_manips(h);

        /*
         * PR14z5: tear down both per-direction pipelines.  Scheme
         * destroy internally unbinds its KGSE_MV slot from the port
         * it was bound to.  CC node + CC tree destroy release the
         * MURAM match/AD tables.
         */
        for (d = 0; d < ASK_HW_DIR_NR; d++) {
                struct ask_hw_pipeline *p = &h->pipe[d];

                /*
                 * PR14z7: disarm FMBM_RFPNE before destroying the KG
                 * scheme so the port reverts to boot-default routing
                 * (Parser → default FQ) instead of pointing at a
                 * scheme handle we just freed.
                 */
                if (p->fman_port) {
                        fman_port_use_kg_hash(p->fman_port, false);
                        p->fman_port = NULL;
                }
                if (p->scheme) {
                        fman_pcd_kg_scheme_destroy(p->scheme);
                        p->scheme = NULL;
                }
                if (p->cc_v4_tcp) {
                        fman_pcd_cc_node_destroy(p->cc_v4_tcp);
                        p->cc_v4_tcp = NULL;
                }
                if (p->cc_tree) {
                        fman_pcd_cc_tree_destroy(p->cc_tree);
                        p->cc_tree = NULL;
                }
                p->bound_pid = 0xff;
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
/* PR14z5 port-bind — per-direction independent classifier pipeline           */
/*                                                                            */
/* Caller decides direction from (ingress_dev, egress_dev) of the flow.       */
/* Each pipeline owns exactly one ingress port; subsequent calls with the     */
/* SAME (port_id, dir) are idempotent.  Calls with a DIFFERENT port_id for    */
/* the same dir return -EBUSY — the caller picked the wrong direction.       */
/* ------------------------------------------------------------------------- */

int ask_hw_port_bind(u8 port_id, enum ask_hw_dir dir,
                     struct net_device *ingress_dev)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_pipeline *p;
        struct fman_pcd_kg_scheme *new_scheme = NULL;
        struct fman_port *fp = NULL;
        int rc;

        if (!h)
                return -ENODEV;

        if (dir >= ASK_HW_DIR_NR) {
                ask_pr_warn("hw: port-bind: dir=%u out of range\n", dir);
                return -EINVAL;
        }

        /*
         * PR14z12-C (2026-05-19): port_id is the **BMI port hwport_id**
         * (DTS cell-index of the fman-v3-port-rx node), NOT the MAC
         * cell-index.  On LS1046A FMan v3 BMI hwport_ids occupy the
         * range 0x01..0x31 (RX 1G base 0x08, RX 10G base 0x10, OH
         * base 0x02, TX 1G base 0x28, TX 10G base 0x30).  Silicon caps
         * at 0x3F (KGAR PORT_ENTRY is 6 bits).  Reject port_id == 0
         * (reserved for OP / null) and >= 0x40.  Previous bound of 10
         * was the MAC cell-index ceiling and rejected legitimate 10G
         * RX hwports 0x10 / 0x11 — see PR14z12-B diagnostic.
         */
        if (port_id == 0 || port_id > 0x3F) {
                ask_pr_warn("hw: port-bind port_id 0x%02x out of range (0x01..0x3F)\n",
                            port_id);
                return -EINVAL;
        }

        p = &h->pipe[dir];

        mutex_lock(&h->lock);

        if (p->bound_pid == port_id) {
                mutex_unlock(&h->lock);
                ask_pr_dbg("hw: port-bind v4-TCP idempotent (port %u, dir %u)\n",
                           port_id, dir);
                return 0;
        }
        if (p->bound_pid != 0xff) {
                u8 cur = p->bound_pid;
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: port-bind v4-TCP: pipeline dir=%u already owns port %u, refusing port %u\n",
                            dir, cur, port_id);
                return -EBUSY;
        }

        mutex_unlock(&h->lock);

        /*
         * Heavy work (scheme_create, attach_cc, bind_port) is done
         * outside h->lock because the underlying fman_pcd_* APIs each
         * take pcd->lock internally — nesting our mutex around them
         * would risk inversion against the FMan driver's own paths.
         */
        new_scheme = fman_pcd_kg_scheme_create(h->pcd, &h->kg_params_v4_tcp);
        if (IS_ERR_OR_NULL(new_scheme)) {
                rc = new_scheme ? PTR_ERR(new_scheme) : -ENOMEM;
                ask_pr_warn("hw: port-bind v4-TCP: kg_scheme_create for port %u dir %u failed: %d\n",
                            port_id, dir, rc);
                return rc;
        }

        rc = fman_pcd_kg_attach_cc(new_scheme, p->cc_tree);
        if (rc) {
                ask_pr_warn("hw: port-bind v4-TCP: kg_attach_cc for port %u dir %u failed: %d\n",
                            port_id, dir, rc);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return rc;
        }

        rc = fman_pcd_kg_bind_port(new_scheme, port_id);
        if (rc) {
                ask_pr_warn("hw: port-bind v4-TCP: kg_bind_port(port=%u dir=%u) failed: %d\n",
                            port_id, dir, rc);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return rc;
        }

        mutex_lock(&h->lock);
        if (p->bound_pid != 0xff) {
                /* Concurrent caller beat us to it. */
                u8 cur = p->bound_pid;
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: port-bind v4-TCP: race detected, pipeline dir=%u was bound to port %u concurrently; destroying duplicate scheme for port %u\n",
                            dir, cur, port_id);
                fman_pcd_kg_scheme_destroy(new_scheme);
                return cur == port_id ? 0 : -EBUSY;
        }
        p->scheme    = new_scheme;
        p->bound_pid = port_id;
        mutex_unlock(&h->lock);

        /*
         * PR14z7 (2026-05-19): arm FMBM_RFPNE → NIA_ENG_HWK on the
         * ingress port's BMI Rx so post-Parser frames are routed into
         * KeyGen instead of dropping out to the default-FQ.  Without
         * this, the LS1046A DTS leaves pcd_fqs_count=0 → mainline
         * fman_port_init() never calls fman_port_use_kg_hash() →
         * FMBM_RFPNE stays at boot default NIA_ENG_BMI|ENQ_FRAME,
         * meaning our KG scheme and CC tree are inert and every flow
         * bypasses silicon.  Kernel patch 0039 exports
         * dpaa_get_rx_fman_port() so we can resolve the netdev's RX
         * port handle without poking dpaa_eth internals.
         *
         * Non-fatal: if fp is NULL, leave the port armed for KG-bind
         * but not for FMBM_RFPNE — the flow will still install but
         * frames will silently bypass.  Logged as warn so the
         * operator can spot it in dmesg.
         */
        fp = dpaa_get_rx_fman_port(ingress_dev);
        if (fp) {
                fman_port_use_kg_hash(fp, true);
                mutex_lock(&h->lock);
                p->fman_port = fp;
                mutex_unlock(&h->lock);
                ask_pr_info("hw: port %u dir %u: FMBM_RFPNE → NIA_ENG_HWK (KeyGen enabled)\n",
                            port_id, dir);
        } else {
                ask_pr_warn("hw: port %u dir %u: dpaa_get_rx_fman_port returned NULL — frames will bypass KG scheme (SW fallback)\n",
                            port_id, dir);
        }

        ask_pr_info("hw: FMan PCD v4-TCP scheme bound to port %u dir %u (PR14z5 dual-pipeline — HW offload active)\n",
                    port_id, dir);
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
                                     enum ask_hw_dir dir,
                                     u32 *out_hw_id)
{
        struct fman_pcd_cc_key_entry entry;
        struct fman_pcd_action *act;
        struct fman_pcd_manip_params insrt_params;
        struct fman_pcd_manip *manips[3];
        struct ask_hw_flow_cookie ck = { 0 };
        struct fman_pcd_cc_node *cc_v4_tcp;
        u32 peer_tx_fqid;
        u32 cookie;
        int slot;
        int rc;

        (void)action_flags;

        /* PR14x gate: bring-up may have failed; behave like "no HW". */
        if (!h->m_v4_rmv || !h->m_v4_ipv4)
                return -ENODEV;

        if (dir >= ASK_HW_DIR_NR)
                return -EINVAL;

        /*
         * PR14z5: route this flow into the per-direction CC node.
         * The pipeline must already be bound (ask_hw_port_bind ran
         * via ask_flow_offload_replace); if it isn't, the silicon
         * has no ingress KG scheme to populate this CC node and the
         * insert would be a no-op.  Refuse with -ENODEV so the SW
         * path stays authoritative.
         */
        cc_v4_tcp = h->pipe[dir].cc_v4_tcp;
        if (!cc_v4_tcp)
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
         * 3. PR14x kernel patch 0036: fuse [m_v4_rmv, m_insrt, m_v4_ipv4]
         *    into a single chain handle.  Each source manip's HMCT
         *    command bytes are memcpied into one fresh HMCT with
         *    HMCD_LAST cleared on the first two so silicon walks all
         *    three before terminating.  The returned handle is
         *    consumed directly by the ingress CC key's MANIPULATE
         *    action — no OH-port hop, no two-stage AD routing.
         */
        manips[0] = h->m_v4_rmv;
        manips[1] = ck.m_insrt;
        manips[2] = h->m_v4_ipv4;

        ck.manip_chain = fman_pcd_manip_chain_create(h->pcd, manips, 3);
        if (IS_ERR_OR_NULL(ck.manip_chain)) {
                rc = ck.manip_chain ? PTR_ERR(ck.manip_chain) : -ENOMEM;
                ck.manip_chain = NULL;
                if (rc == -ENOSPC) {
                        mutex_lock(&h->lock);
                        h->enospc_chain_muram++;
                        mutex_unlock(&h->lock);
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at "
                                "manip_chain_create — fused-HMCT MURAM "
                                "exhausted; flow stays in SW\n");
                } else {
                        ask_pr_warn("hw: manip_chain_create failed (%d)\n", rc);
                }
                goto err_free_insrt;
        }

        /* 4. Install ingress CC key with MANIPULATE -> chain -> peer_tx_fqid. */
        memset(&entry, 0, sizeof(entry));
        memcpy(&entry.key[0],  &key->src_ip[0], 4);
        memcpy(&entry.key[4],  &key->dst_ip[0], 4);
        entry.key[8] = IPPROTO_TCP;
        memcpy(&entry.key[9],  &key->sport, 2);
        memcpy(&entry.key[11], &key->dport, 2);
        memset(&entry.mask[0], 0xff, ASK_HW_V4_KEY_WIDTH);

        act = &entry.action;
        act->type                  = FMAN_PCD_ACTION_MANIPULATE;
        act->manipulate.manip      = ck.manip_chain;
        act->manipulate.next_fqid  = peer_tx_fqid;

        mutex_lock(&h->lock);
        slot = fman_pcd_cc_node_add_key(cc_v4_tcp, &entry);
        if (slot == -ENOSPC)
                h->enospc_cc_keys++;
        mutex_unlock(&h->lock);
        if (slot < 0) {
                rc = slot;
                if (slot == -ENOSPC)
                        pr_info_ratelimited("ask: hw: insert: ENOSPC at "
                                "cc_node_add_key v4-TCP — CC node hit "
                                "cap-255 silicon limit (PR14r); flow stays in SW\n");
                else
                        ask_pr_warn("hw: cc_node_add_key v4-TCP failed (%d)\n", slot);
                goto err_destroy_chain;
        }
        if (slot > U16_MAX) {
                rc = -EOVERFLOW;
                goto err_drop_slot;
        }

        /* 5. Snapshot cookie fields. */
        ck.cc_node      = cc_v4_tcp;
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
                (void)fman_pcd_cc_node_modify_next_action(cc_v4_tcp,
                                                          ck.key_idx, &drop);
                mutex_unlock(&h->lock);
        }
err_destroy_chain:
        if (ck.manip_chain)
                fman_pcd_manip_chain_destroy(ck.manip_chain);
err_free_insrt:
        if (ck.m_insrt)
                fman_pcd_manip_destroy(ck.m_insrt);
        return rc;
}

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       enum ask_hw_dir dir,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h;

        if (!key || !out_hw_id)
                return -EINVAL;

        if (dir >= ASK_HW_DIR_NR)
                return -EINVAL;

        h = ask_hw_pcd_get();
        if (!h)
                return -ENODEV;

        if (key->l3_proto == ASK_FLOW_L3_IPV4 &&
            key->l4_proto == IPPROTO_TCP)
                return ask_hw_flow_insert_v4_tcp(h, key, oif, action_flags,
                                                 dir, out_hw_id);

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
         * PR14x: disarm the ingress CC slot (tombstone to DROP), then
         * release the per-flow chain handle and the per-flow m_insrt.
         * The shared m_rmv / m_ipv4 stay alive; they belong to
         * ask_hw_pcd and are destroyed in teardown.  No OH-port pool
         * accounting any more.
         */
        mutex_lock(&h->lock);
        (void)fman_pcd_cc_node_modify_next_action(ck->cc_node, ck->key_idx,
                                                  &drop);
        mutex_unlock(&h->lock);

        if (ck->manip_chain)
                fman_pcd_manip_chain_destroy(ck->manip_chain);
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