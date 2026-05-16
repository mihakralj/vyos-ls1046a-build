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

struct ask_hw_pcd {
        struct mutex lock;
        struct fman *fman;             /* PR14j: kept for OH-port claim */
        struct fman_pcd *pcd;
        struct fman_pcd_cc_tree *cc_tree;
        struct fman_pcd_cc_node *cc_v4_tcp;
        struct fman_pcd_kg_scheme *kg_v4_tcp;

        /* PR14g port-bind tracking (single-port-per-scheme KGSE_MV). */
        bool v4_tcp_bound_port_valid;
        u8   v4_tcp_bound_port;

        /*
         * PR14j additions: OH-port + shared MANIPs + cookie table.
         *
         * oh_v4_tcp == NULL means the OH-port chain was not brought
         * up (no fsl,fman, claim failed, MURAM exhaustion, …).
         * ask_hw_flow_insert_v4_tcp() detects this and returns -ENODEV
         * so the caller falls back to SW.
         */
        struct fman_pcd_oh_port *oh_v4_tcp;
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
        struct fman_pcd_kg_scheme_params kg_params;
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

        memset(&keys, 0, sizeof(keys));
        keys.num_keys = 0;
        keys.keys = NULL;
        keys.miss_action.type = FMAN_PCD_ACTION_DROP;

        h->cc_v4_tcp = fman_pcd_cc_node_create(h->cc_tree, &extract, &keys);
        if (IS_ERR(h->cc_v4_tcp)) {
                rc = PTR_ERR(h->cc_v4_tcp);
                h->cc_v4_tcp = NULL;
                ask_pr_warn("hw: cc_node_create v4-TCP failed (%d)\n", rc);
                goto err_tree;
        }

        memset(&kg_params, 0, sizeof(kg_params));
        kg_params.id = -1;
        kg_params.use_hash = true;
        kg_params.default_fqid = 0;
        kg_params.num_extracts = 5;

        kg_params.extracts[0].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params.extracts[0].offset = ASK_HW_PR_OFF_IPV4_SIP;
        kg_params.extracts[0].size   = 4;
        kg_params.extracts[0].mask   = 0xff;

        kg_params.extracts[1].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params.extracts[1].offset = ASK_HW_PR_OFF_IPV4_DIP;
        kg_params.extracts[1].size   = 4;
        kg_params.extracts[1].mask   = 0xff;

        kg_params.extracts[2].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params.extracts[2].offset = ASK_HW_PR_OFF_L4PROTO;
        kg_params.extracts[2].size   = 1;
        kg_params.extracts[2].mask   = 0xff;

        kg_params.extracts[3].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params.extracts[3].offset = ASK_HW_PR_OFF_L4_SPORT;
        kg_params.extracts[3].size   = 2;
        kg_params.extracts[3].mask   = 0xff;

        kg_params.extracts[4].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg_params.extracts[4].offset = ASK_HW_PR_OFF_L4_DPORT;
        kg_params.extracts[4].size   = 2;
        kg_params.extracts[4].mask   = 0xff;

        h->kg_v4_tcp = fman_pcd_kg_scheme_create(h->pcd, &kg_params);
        if (IS_ERR(h->kg_v4_tcp)) {
                rc = PTR_ERR(h->kg_v4_tcp);
                h->kg_v4_tcp = NULL;
                ask_pr_warn("hw: kg_scheme_create v4-TCP failed (%d)\n", rc);
                goto err_node;
        }

        rc = fman_pcd_kg_attach_cc(h->kg_v4_tcp, h->cc_tree);
        if (rc) {
                ask_pr_warn("hw: kg_attach_cc v4-TCP failed (%d)\n", rc);
                goto err_kg;
        }

        ask_pr_info("hw: FMan PCD chain up (v4-TCP empty CC node, KG-attached, awaiting port-bind from flow_offload)\n");
        return 0;

err_kg:
        fman_pcd_kg_scheme_destroy(h->kg_v4_tcp);
        h->kg_v4_tcp = NULL;
err_node:
        fman_pcd_cc_node_destroy(h->cc_v4_tcp);
        h->cc_v4_tcp = NULL;
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
        int rc;

        h->oh_v4_tcp = fman_pcd_oh_port_claim(h->fman, ASK_HW_V4_TCP_OH_IDX);
        if (IS_ERR_OR_NULL(h->oh_v4_tcp)) {
                rc = h->oh_v4_tcp ? PTR_ERR(h->oh_v4_tcp) : -ENODEV;
                h->oh_v4_tcp = NULL;
                ask_pr_warn("hw: oh_port_claim(%u) failed (%d) - HW offload falls back to SW for v4-TCP\n",
                            ASK_HW_V4_TCP_OH_IDX, rc);
                return rc;
        }

        memset(&mp, 0, sizeof(mp));
        mp.type = FMAN_PCD_MANIP_RMV_ETHERNET;
        h->m_v4_rmv = fman_pcd_manip_create(h->pcd, &mp);
        if (IS_ERR_OR_NULL(h->m_v4_rmv)) {
                rc = h->m_v4_rmv ? PTR_ERR(h->m_v4_rmv) : -ENOMEM;
                h->m_v4_rmv = NULL;
                ask_pr_warn("hw: manip_create(RMV_ETHERNET) failed (%d)\n", rc);
                goto err_release_oh;
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

        ask_pr_info("hw: PR14j OH-port chain ready (oh_idx=%u, input_fqid=0x%x)\n",
                    ASK_HW_V4_TCP_OH_IDX,
                    fman_pcd_oh_port_input_fqid(h->oh_v4_tcp));
        return 0;

err_destroy_rmv:
        fman_pcd_manip_destroy(h->m_v4_rmv);
        h->m_v4_rmv = NULL;
err_release_oh:
        fman_pcd_oh_port_release(h->oh_v4_tcp);
        h->oh_v4_tcp = NULL;
        return rc;
}

static void ask_hw_pcd_teardown_oh(struct ask_hw_pcd *h)
{
        /*
         * Drain any per-flow cookies still alive.  Each entry implies a
         * leaked per-flow m_insrt and a still-attached OH-port chain.
         * Free the m_insrt explicitly; the OH-port AD chain is reset
         * to NULL once below, which is the documented "disarm" form.
         *
         * xa_for_each + xa_erase under the same critical section is
         * safe per the xarray API contract (the iterator caches the
         * next key before yielding to the body).
         */
        if (h->oh_v4_tcp) {
                struct ask_hw_flow_cookie *ck;
                unsigned long idx;

                xa_for_each(&h->flow_cookies, idx, ck) {
                        if (ck && ck->m_insrt)
                                fman_pcd_manip_destroy(ck->m_insrt);
                        xa_erase(&h->flow_cookies, idx);
                        kfree(ck);
                }

                /* Disarm the OH AD chain so no stale RMV/INSRT can run. */
                (void)fman_pcd_oh_port_set_chain(h->oh_v4_tcp, NULL, 0, 0);
        }

        if (h->m_v4_ipv4) {
                fman_pcd_manip_destroy(h->m_v4_ipv4);
                h->m_v4_ipv4 = NULL;
        }
        if (h->m_v4_rmv) {
                fman_pcd_manip_destroy(h->m_v4_rmv);
                h->m_v4_rmv = NULL;
        }
        if (h->oh_v4_tcp) {
                fman_pcd_oh_port_release(h->oh_v4_tcp);
                h->oh_v4_tcp = NULL;
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
         * ask_hw_flow_insert_v4_tcp() will see h->oh_v4_tcp == NULL and
         * return -ENODEV so ask_flow.c falls back to SW.  Frames still
         * traverse the kernel slow path — they just are not silicon-
         * accelerated.
         */
        (void)ask_hw_pcd_bringup_oh(h);

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

        if (h->kg_v4_tcp) {
                fman_pcd_kg_scheme_destroy(h->kg_v4_tcp);
                h->kg_v4_tcp = NULL;
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
/* PR14g port-bind (unchanged from body-1 + part-3)                           */
/* ------------------------------------------------------------------------- */

int ask_hw_port_bind(u8 port_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        int rc;

        if (!h)
                return -ENODEV;

        if (port_id > 9) {
                ask_pr_warn("hw: port-bind port_id %u out of range (0..9)\n",
                            port_id);
                return -EINVAL;
        }

        mutex_lock(&h->lock);

        if (h->v4_tcp_bound_port_valid) {
                if (h->v4_tcp_bound_port == port_id) {
                        mutex_unlock(&h->lock);
                        ask_pr_dbg("hw: port-bind v4-TCP idempotent (port %u)\n",
                                   port_id);
                        return 0;
                }
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: port-bind v4-TCP: already bound to port %u, "
                            "cannot also bind port %u - second port will run "
                            "in software\n",
                            h->v4_tcp_bound_port, port_id);
                return -EBUSY;
        }

        rc = fman_pcd_kg_bind_port(h->kg_v4_tcp, port_id);
        if (rc) {
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: fman_pcd_kg_bind_port(v4-TCP, port=%u) failed: %d\n",
                            port_id, rc);
                return rc;
        }

        h->v4_tcp_bound_port = port_id;
        h->v4_tcp_bound_port_valid = true;
        mutex_unlock(&h->lock);

        ask_pr_info("hw: FMan PCD v4-TCP scheme bound to port %u (HW offload active)\n",
                    port_id);
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

        /* PR14j gate: bring-up may have failed; behave like "no HW". */
        if (!h->oh_v4_tcp || !h->m_v4_rmv || !h->m_v4_ipv4)
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
         * 3. Program the OH-port AD chain.
         *
         * NOTE: per the design memo §8 Q2, the OH AD chain is per-port,
         * not per-flow.  Each new flow's set_chain() re-programs the
         * chain with that flow's per-flow m_insrt.  This is correct for
         * v1.0 because *all* v4-TCP flows hit the same OH port and the
         * chain shape (RMV -> INSRT -> IPv4-FWD) is identical; only the
         * INSRT inline header bytes differ per flow.  set_chain pauses
         * the OH BMI dequeue for the rewrite window per the in-tree
         * driver semantics, so frames in flight either traverse the old
         * chain (for the prior flow's MAC pair) or the new one.  v1.0
         * accepts this latency-of-rewrite for the first packet of each
         * new flow; v1.1 (PR15) gets one OH port per flow if needed.
         */
        manips[0] = h->m_v4_rmv;
        manips[1] = ck.m_insrt;
        manips[2] = h->m_v4_ipv4;

        mutex_lock(&h->lock);
        rc = fman_pcd_oh_port_set_chain(h->oh_v4_tcp, manips, 3, peer_tx_fqid);
        mutex_unlock(&h->lock);
        if (rc) {
                ask_pr_warn("hw: oh_port_set_chain failed (%d)\n", rc);
                goto err_free_insrt;
        }

        oh_input_fqid = fman_pcd_oh_port_input_fqid(h->oh_v4_tcp);

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
        mutex_unlock(&h->lock);
        if (slot < 0) {
                rc = slot;
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
         * Best-effort disarm.  If other flows share this OH port they
         * already failed at insert time too (single OH port per protocol
         * at v1.0); no inflight-flow corruption risk.
         */
        mutex_lock(&h->lock);
        (void)fman_pcd_oh_port_set_chain(h->oh_v4_tcp, NULL, 0, 0);
        mutex_unlock(&h->lock);
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
                ask_pr_warn("hw: remove: unknown cookie 0x%08x\n", hw_flow_id);
                return -EINVAL;
        }

        /*
         * Disarm the ingress CC slot first so no new frames enter the
         * OH chain via this flow's key.  Then free the per-flow
         * m_insrt.  The shared m_rmv / m_ipv4 stay alive; they belong
         * to ask_hw_pcd and are destroyed in teardown.
         *
         * We deliberately do NOT re-program the OH chain to NULL here
         * because other flows may still be using it.  When the last
         * flow on the OH port is removed the chain is left programmed
         * with the previous flow's m_insrt — that m_insrt has now been
         * destroyed, so the OH BMI would dereference a stale MURAM
         * offset.  This is a latent issue documented in the memo §8 Q2:
         * the OH port is per-protocol, not per-flow, at v1.0.  v1.1 will
         * either (a) give each flow its own OH port (we have 6), or
         * (b) refcount the OH chain and disarm-on-zero here.  For now
         * we rely on the fact that ask.ko under sustained load always
         * has flows present; the teardown path resets the chain.
         */
        mutex_lock(&h->lock);
        (void)fman_pcd_cc_node_modify_next_action(ck->cc_node, ck->key_idx,
                                                  &drop);
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