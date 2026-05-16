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
 * Why this path instead of the spec §12.2 OP_GET_UCODE_VERSION host
 * command? See specs/ask2-rewrite-spec.md §12.8 (PR13 hardware-probe
 * findings): the standard NXP 210.x QEF microcode loaded on this
 * board does not implement a host-command opcode dispatcher — it
 * implements parser/policer/keygen via MURAM-resident config tables
 * programmed by drivers/net/ethernet/freescale/fman/fman_keygen.c,
 * fman_port.c and fman_memac.c. The host-command transport
 * (kernel/flavors/ask/patches/0003-fman-host-command-api.patch) is
 * preserved as future infrastructure for a hypothetical custom ASK2
 * microcode that does implement opcode dispatch, but is not used by
 * v1.0 against stock 210.x.
 *
 * QEF blob layout (verified 2026-05-13 against
 * /proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware on
 * the live Mono Gateway DK, kernel 6.18.28-vyos):
 *
 *   off  size  field
 *   ---  ----  -------------------------------------------------
 *   0x00   4   crc32 (big-endian, over the rest of the blob)
 *   0x04   4   magic 'Q' 'E' 'F' 0x01 (= 0x51454601 BE)
 *   0x08  64   NUL-terminated ASCII description string, e.g.
 *               "Microcode version 210.10.1 for LS1043 r1.0"
 *   0x48   N   binary microcode payload (opaque to the kernel)
 *
 * The version fields are extracted by sscanf() from the description
 * string. This is the same approach the SDK fmlib library uses, and
 * is robust across all known 210.x microcode generations.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/err.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/in.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <net/net_namespace.h>
#include <linux/fsl/fman_pcd.h>
#include <linux/fsl/dpaa_flow_offload.h>

#include "include/ask_internal.h"

/* QEF blob structural constants (see comment above). */
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

/*
 * Validate the QEF magic and return the embedded description string.
 * @blob:   pointer to the firmware blob bytes (DT property contents)
 * @len:    length of the blob in bytes
 * @desc:   output buffer of at least ASK_QEF_DESC_LEN bytes; the
 *          description is copied here NUL-terminated on success.
 *
 * Return: 0 on success, -EINVAL on bad magic or short blob.
 */
static int ask_hw_qef_get_description(const u8 *blob, size_t len,
                                      char *desc)
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

        /*
         * The description region is exactly 64 bytes wide and is
         * always NUL-terminated by the QEF spec. We copy and force a
         * terminator at the last byte as belt-and-braces against a
         * malformed blob.
         */
        memcpy(desc, blob + ASK_QEF_DESC_OFFSET, ASK_QEF_DESC_LEN);
        desc[ASK_QEF_DESC_LEN - 1] = '\0';
        return 0;
}

/*
 * Parse a QEF description string of the form
 *   "Microcode version <family>.<major>.<minor> for <soc> r<rev>"
 * (e.g. "Microcode version 210.10.1 for LS1043 r1.0") into the four
 * version fields exposed by ASK_INFO_ATTR_UCODE_*.
 *
 * The patch field (ASK_INFO_ATTR_UCODE_PATCH) is set to 0 for stock
 * NXP microcode — the QEF format does not encode a fourth version
 * component. It is reserved for any custom microcode that bumps it
 * (the ASK2 hypothetical custom microcode path per spec §12.8).
 *
 * Return: 0 on success, -EINVAL if the string does not match the
 *         expected pattern.
 */
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

/* ------------------------------------------------------------------------- */
/* DT lookup                                                                  */
/* ------------------------------------------------------------------------- */

/*
 * Walk the device tree to find the FMan firmware blob.
 *
 * The mainline FMan binding places the firmware as a child node of
 * the FMan controller:
 *
 *   /soc/fman@<addr>/fman-firmware {
 *       compatible = "fsl,fman-firmware";
 *       fsl,firmware = <BLOB BYTES>;
 *   };
 *
 * U-Boot fills in fsl,firmware from the SPI "fman-ucode" partition
 * before it boots Linux. We accept the firmware from any FMan on the
 * SoC — this is single-FMan on LS1046A, dual-FMan on LS1043A. The
 * first match wins; v1.0 does not differentiate per FMan because
 * NXP ships the same QEF microcode for every FMan on a given SoC.
 *
 * Return: 0 on success, -ENODEV if no fsl,fman-firmware node is
 *         present, -ENOENT if the node lacks the fsl,firmware
 *         property, -EINVAL if the QEF blob fails sanity checks.
 */
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

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

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
/* PR14g-body-1 (M2.5g): FMan PCD bring-up                                    */
/*                                                                            */
/* Walks the device tree to the first "fsl,fman" node, resolves the           */
/* per-FMan struct fman_pcd handle via fman_pcd_from_of_node() (a             */
/* convenience helper exported by drivers/net/ethernet/freescale/fman/        */
/* fman.c at PR14g-prep / patch 0027), then constructs the minimal silicon    */
/* programming pipeline that the M2 acceptance gate (spec §11.1 / §13.7)      */
/* needs to traverse a single IPv4 TCP flow through the 210 fast path:        */
/*                                                                            */
/*   1. one CC tree with a single group (group_count = 1 - PR14c-body-1's    */
/*      256-byte MURAM-resident group table covers up to 16 entries but       */
/*      v1.0 only uses one)                                                   */
/*   2. one EMPTY CC node, extract = KEY mode at offset 0 size 13 (the        */
/*      KG-concatenated 5-tuple width); miss action = DROP so unprogrammed    */
/*      keys fail silently to the FQ-0 trap rather than misroute              */
/*   3. one KG scheme extracting the IPv4 5-tuple from PARSE_RESULT (SIP      */
/*      offset 12 size 4, DIP offset 16 size 4, proto offset 9 size 1, sport  */
/*      offset 20 size 2, dport offset 22 size 2 - 13 bytes total, well       */
/*      under the 36-byte FMan KG generic extract budget per RM 8.7.5)        */
/*   4. KG scheme attach to the CC tree via fman_pcd_kg_attach_cc() so that  */
/*      classification hits chain into the CC node's empty key table          */
/*                                                                            */
/* All four allocations are MURAM-backed and counted against pcd->muram_      */
/* budget by the in-tree PCD subsystem (PR14a-f bodies).  Body-1 makes NO    */
/* port-bind call (fman_pcd_kg_bind_port) - the bind has to wait until the   */
/* dpaa Ethernet driver tells us which FMan port is RX-default for which     */
/* netdev, and that wiring lives in PR15 (M3).  At body-1 the chain is       */
/* programmed but quiescent: silicon does the lookup against the all-zero    */
/* miss-slot and forwards everything to the kernel fast path as before,     */
/* identical to module-not-loaded behaviour.                                 */
/*                                                                            */
/* Failure mode: ANY -E from the chain unwinds in reverse order and leaves   */
/* ask_hw_pcd.pcd = NULL.  ask_hw_pcd_get() then returns NULL and the body-2 */
/* dispatcher falls back to fake_hw_id_seq software-only mode.  This is the  */
/* expected outcome on non-DPAA hosts and any host whose fsl,fman node is    */
/* status="disabled" - ask.ko remains functionally complete (genl up,        */
/* software flow table up) just without HW-offload.                          */
/* ------------------------------------------------------------------------- */

/* Standard FMan parse-result offsets (RM 8.7.3 Table 8-107). */
#define ASK_HW_PR_OFF_L4PROTO   9
#define ASK_HW_PR_OFF_IPV4_SIP  12
#define ASK_HW_PR_OFF_IPV4_DIP  16
#define ASK_HW_PR_OFF_L4_SPORT  20
#define ASK_HW_PR_OFF_L4_DPORT  22

/* KG-concatenated 5-tuple width = SIP(4) + DIP(4) + proto(1) + sport(2) + dport(2). */
#define ASK_HW_V4_KEY_WIDTH     13

struct ask_hw_pcd {
        struct mutex lock;
        struct fman_pcd *pcd;
        struct fman_pcd_cc_tree *cc_tree;
        struct fman_pcd_cc_node *cc_v4_tcp;
        struct fman_pcd_kg_scheme *kg_v4_tcp;

        /*
         * PR14g-body-1 (part 2): port-bind tracking.
         *
         * fman_pcd_kg_bind_port() is single-port-per-scheme on LS1046A
         * silicon (the KGSE_MV match-vector slot is a single u32 with
         * one bit per port; the second bind to a different port
         * returns -EBUSY).  We cache the first successfully-bound
         * port_id so subsequent calls from FLOW_BLOCK_BIND on the
         * SAME netdev become idempotent no-ops, and the operator sees
         * a single warn line when a SECOND netdev attempts to bind to
         * a different port (the second port still works in software,
         * just unaccelerated).
         *
         * v4_tcp_bound_port_valid is false until the first successful
         * bind; v4_tcp_bound_port holds the port_id once valid.
         * Protected by ->lock.
         */
        bool v4_tcp_bound_port_valid;
        u8   v4_tcp_bound_port;
};

/*
 * Single per-module instance.  Allocated once in ask_hw_pcd_bringup()
 * and freed once in ask_hw_pcd_teardown().  Lifetime spans the whole
 * module: ask_main.c calls ask_hw_init()/ask_hw_exit() at module load/
 * unload exactly once.  Hence the bare static pointer rather than a
 * refcounted container - body-2's runtime dispatcher only ever reads
 * the pointer, never re-allocates it.
 */
static struct ask_hw_pcd *ask_hw_pcd_inst;

static int ask_hw_pcd_build_chain(struct ask_hw_pcd *h)
{
        struct fman_pcd_cc_extract extract;
        struct fman_pcd_cc_key_table keys;
        struct fman_pcd_kg_scheme_params kg_params;
        int rc;

        /* Step 1 - empty 1-group CC tree (PR14c-body-1 silicon programming). */
        h->cc_tree = fman_pcd_cc_tree_create(h->pcd, 1);
        if (IS_ERR(h->cc_tree)) {
                rc = PTR_ERR(h->cc_tree);
                h->cc_tree = NULL;
                ask_pr_warn("hw: cc_tree_create failed (%d) - HW offload disabled\n",
                            rc);
                return rc;
        }

        /*
         * Step 2 - empty v4-TCP CC node.  Extract source is KEY (i.e. the
         * KG-concatenated extract output, NOT the parse result directly),
         * starting at byte 0 of the KG result with the full 13-byte width.
         * num_keys = 0 -> body-2 fills in slots via fman_pcd_cc_node_add_key().
         * miss_action = DROP so unprogrammed slots are safe-by-default.
         */
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

        /*
         * Step 3 - IPv4 5-tuple KG scheme.  Use the first free scheme id
         * (params.id = -1) so we coexist with whatever the in-tree dpaa
         * driver may have programmed.  use_hash = true so the hash output
         * feeds into the CC tree group-table index.
         */
        memset(&kg_params, 0, sizeof(kg_params));
        kg_params.id = -1;
        kg_params.use_hash = true;
        kg_params.default_fqid = 0;     /* Unused with use_hash = true. */
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

        /* Step 4 - chain KG -> CC tree.  Once attached, classification hits
         * fan out via the CC tree group table to the (currently empty)
         * v4-TCP node.  No port bind yet - that happens in M3.
         */
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

int ask_hw_pcd_bringup(void)
{
        struct ask_hw_pcd *h;
        struct device_node *np;
        struct fman_pcd *pcd;
        int rc;

        if (ask_hw_pcd_inst) {
                ask_pr_dbg("hw: pcd bringup already done\n");
                return 0;
        }

        /*
         * Locate the first fsl,fman node and resolve to a struct fman_pcd *.
         * fman_pcd_from_of_node() handles the platform-device walk,
         * fman_bind(), fman_get_pcd() chain in driver-private scope so we
         * never need to include the in-tree fman.h.  NULL return is normal
         * on non-DPAA hosts - we treat it as a soft-failure.
         */
        np = of_find_compatible_node(NULL, NULL, "fsl,fman");
        if (!np) {
                ask_pr_info("hw: no fsl,fman in DT - HW offload not available\n");
                return 0;
        }

        pcd = fman_pcd_from_of_node(np);
        of_node_put(np);
        if (!pcd) {
                ask_pr_info("hw: fman_pcd_from_of_node returned NULL - HW offload not available\n");
                return 0;
        }

        h = kzalloc(sizeof(*h), GFP_KERNEL);
        if (!h)
                return -ENOMEM;

        mutex_init(&h->lock);
        h->pcd = pcd;

        rc = ask_hw_pcd_build_chain(h);
        if (rc) {
                /*
                 * Build failure means the chain is fully unwound (build
                 * itself rolls back on every error path).  Free the handle
                 * but DO NOT propagate -E up: ask.ko should still load and
                 * fall back to software-only mode.  The single warn line
                 * inside build_chain has already explained the failure.
                 */
                kfree(h);
                return 0;
        }

        ask_hw_pcd_inst = h;
        return 0;
}

void ask_hw_pcd_teardown(void)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;

        if (!h)
                return;

        ask_hw_pcd_inst = NULL;

        /*
         * Teardown order is exact reverse of bring-up.  Each destroy is
         * NULL-safe per the public-ABI contract in <linux/fsl/fman_pcd.h>,
         * but we set the local field to NULL after each call as a
         * defence-in-depth measure should a future leak-detection
         * dump_stack() ever walk this struct mid-teardown.
         *
         * No need to detach the KG scheme from the CC tree explicitly -
         * fman_pcd_kg_scheme_destroy() clears the CCBS register as part
         * of its silicon teardown (PR14b-body).
         */
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
        /*
         * h->pcd is owned by the FMan driver - we do not free it.  The
         * fman_bind() call inside fman_pcd_from_of_node() is balanced by
         * the FMan driver's own devm cleanup at unbind.
         */
        mutex_destroy(&h->lock);
        kfree(h);

        ask_pr_dbg("hw: FMan PCD chain torn down\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
        /*
         * No READ_ONCE/WRITE_ONCE: the pointer is only ever published
         * once in ask_hw_pcd_bringup() (under the implicit module-init
         * ordering) and only ever cleared once in ask_hw_pcd_teardown()
         * (under the implicit module-exit ordering).  Body-2 callers
         * run between init and exit; no torn-pointer hazard exists.
         */
        return ask_hw_pcd_inst;
}

/* ------------------------------------------------------------------------- */
/* PR14g-body-1 (part 2): port-bind                                           */
/*                                                                            */
/* Called from ask_flow_offload.c on FLOW_BLOCK_BIND once we have resolved   */
/* the netdev to an FMan port id via dpaa_get_fman_port_id() (kernel patch   */
/* 0030).  Wraps fman_pcd_kg_bind_port() with idempotency and single-port-   */
/* per-scheme accounting; see the kerneldoc in include/ask_internal.h for   */
/* the full return-value contract.                                            */
/* ------------------------------------------------------------------------- */

int ask_hw_port_bind(u8 port_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        int rc;

        if (!h)
                return -ENODEV;

        /*
         * Range-check matches the header doc in <linux/fsl/fman_pcd.h>:
         * "FMan port id (0..7 for 1G, 8..9 for 10G on LS1046A)".  The
         * in-tree dpaa_get_fman_port_id() helper guarantees this range
         * via -ERANGE, but we re-check defensively so ask_hw_port_bind
         * is usable from kunit harnesses that synthesise port_id values
         * directly without going through dpaa_get_fman_port_id().
         */
        if (port_id > 9) {
                ask_pr_warn("hw: port-bind port_id %u out of range (0..9)\n",
                            port_id);
                return -EINVAL;
        }

        mutex_lock(&h->lock);

        if (h->v4_tcp_bound_port_valid) {
                if (h->v4_tcp_bound_port == port_id) {
                        /* Idempotent rebind from the same netdev. */
                        mutex_unlock(&h->lock);
                        ask_pr_dbg("hw: port-bind v4-TCP idempotent (port %u)\n",
                                   port_id);
                        return 0;
                }
                /*
                 * A DIFFERENT port is asking to bind.  LS1046A KGSE_MV
                 * is single-port-per-scheme; we cannot accept this.
                 * Log once-ish (the caller bumps a counter so the dbg
                 * spam is bounded), return -EBUSY so the caller can
                 * fall back to software for this second port without
                 * tearing down the first port's acceleration.
                 */
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
/* hw_flow_id pack/unpack (PR14g-body-1)                                      */
/*                                                                            */
/* The 32-bit hw_flow_id stored in struct ask_flow encodes:                   */
/*   bits 31..16   node_token  - identifies the owning CC node                */
/*   bits 15..0    key_idx     - 0-based slot inside that node's key table    */
/*                                                                            */
/* Pure functions; no global state.  Declared in ask_internal.h so other     */
/* TUs (debugfs, genl dump) can call the unpacker for pretty-printing.       */
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
/* PR14g-body-2 (M2.5g): runtime flow insert / remove dispatcher              */
/*                                                                            */
/* Per Q2 architectural decision (operator-approved 2026-05-14): a single     */
/* ask_hw_flow_insert() entry point switches on (key->l3_proto, key->l4_proto)*/
/* and routes to a per-protocol worker.  Worker contract:                     */
/*                                                                            */
/*   return  0           -> packed hw_flow_id written through @out_hw_id      */
/*           -ENODEV     -> no HW backing for this protocol/netdev; caller    */
/*                          (ask_flow.c body-3) must fall back to the         */
/*                          fake_hw_id_seq software-only counter              */
/*           -EOPNOTSUPP -> protocol not yet implemented in HW (body-2 only   */
/*                          ships v4-TCP; v4-UDP/v6-TCP/v6-UDP land later)    */
/*           other -E    -> hard failure (MURAM exhaustion, key table full,   */
/*                          mask/size mismatch); caller MUST fail the insert  */
/*                          rather than silently fall back, so userspace      */
/*                          observes the error and does not believe a flow    */
/*                          is offloaded when it is not                       */
/*                                                                            */
/* The dispatcher itself acquires no locks; per-CC-node serialisation lives   */
/* inside the worker around the pcd->lock + fman_pcd_cc_node_add_key() pair   */
/* (the silicon-side serialisation is provided by the in-tree PCD subsystem  */
/* mutex; the per-ask_hw_pcd mutex above only protects the local handle      */
/* fields - currently a no-op until body-3 starts mutating per-flow state). */
/* ------------------------------------------------------------------------- */

/*
 * Resolve @oif to an FMan-backed dpaa netdev and look up its
 * RX-default FQID via the in-tree export from patch 0028.
 *
 * Returns 0 on success with *fqid populated, -ENODEV if @oif does
 * not name a dpaa-backed netdev (the standard "non-DPAA" fallback
 * signal), or any other -E forwarded from dpaa_get_rx_default_fqid().
 *
 * Locking: takes rcu_read_lock() across dev_get_by_index_rcu() so the
 * netdev cannot disappear underneath us; dpaa_get_rx_default_fqid()
 * is documented (see <linux/fsl/dpaa_flow_offload.h>) to require RTNL,
 * but the actual list it walks (priv->dpaa_fq_list) is only mutated
 * at probe/remove and is stable for the netdev's lifetime - rcu_read_
 * lock + dev_get_by_index_rcu is sufficient to keep the netdev alive
 * across the call.  We pass init_net here because all dpaa netdevs
 * live in the host network namespace by construction (the dpaa
 * driver does not register per-netns).
 */
static int ask_hw_resolve_oif_fqid(u32 oif, u32 *fqid)
{
        struct net_device *dev;
        int rc;

        rcu_read_lock();
        dev = dev_get_by_index_rcu(&init_net, oif);
        if (!dev) {
                rcu_read_unlock();
                return -ENODEV;
        }
        rc = dpaa_get_rx_default_fqid(dev, fqid);
        rcu_read_unlock();
        return rc;
}

/*
 * v4-TCP worker.  Builds a 13-byte exact-match key matching the KG
 * scheme's PARSE_RESULT extract layout (SIP[4] DIP[4] proto[1]
 * sport[2] dport[2]), looks up the netdev's RX-default FQID, and
 * installs the (key, FORWARD_FQ(fqid)) entry into the cc_v4_tcp
 * node's key table.
 *
 * IMPORTANT: the byte layout below MUST stay in lock-step with the
 * five PARSE_RESULT extract specs in ask_hw_pcd_build_chain() above.
 * The KG hardware concatenates extracts in slot order, so:
 *   bytes[ 0.. 3] = SIP   (matches kg_params.extracts[0])
 *   bytes[ 4.. 7] = DIP   (matches kg_params.extracts[1])
 *   bytes[    8 ] = proto (matches kg_params.extracts[2])
 *   bytes[ 9..10] = sport (matches kg_params.extracts[3])
 *   bytes[11..12] = dport (matches kg_params.extracts[4])
 *
 * The 5-tuple fields in struct ask_flow_key are already __be (network
 * byte order) per the parse-result wire format, so a straight memcpy
 * is correct - no swap required.  src_ip[16]/dst_ip[16] hold the v4
 * address packed into the first 4 bytes per the ask_flow.c contract.
 */
static int ask_hw_flow_insert_v4_tcp(struct ask_hw_pcd *h,
                                     const struct ask_flow_key *key,
                                     u32 oif, u32 action_flags,
                                     u32 *out_hw_id)
{
        struct fman_pcd_cc_key_entry entry;
        struct fman_pcd_action *act;
        u32 fqid;
        int slot;
        int rc;

        /*
         * action_flags is reserved for body-3+ when NAT/TTL-DEC/VLAN
         * manipulation actions get wired in via FMAN_PCD_ACTION_
         * MANIPULATE.  For body-2 the only action is a straight
         * FORWARD_FQ to the netdev's RX-default FQID, mirroring what
         * the kernel datapath would do for an un-offloaded flow.
         */
        (void)action_flags;

        rc = ask_hw_resolve_oif_fqid(oif, &fqid);
        if (rc)
                return rc;     /* -ENODEV propagates as fallback signal */

        memset(&entry, 0, sizeof(entry));

        /* Key bytes — see comment block above for slot layout. */
        memcpy(&entry.key[0],  &key->src_ip[0], 4);   /* SIP   */
        memcpy(&entry.key[4],  &key->dst_ip[0], 4);   /* DIP   */
        entry.key[8]  = IPPROTO_TCP;                  /* proto */
        memcpy(&entry.key[9],  &key->sport, 2);       /* sport */
        memcpy(&entry.key[11], &key->dport, 2);       /* dport */

        /* Exact match: all 13 bytes contribute. */
        memset(&entry.mask[0], 0xff, ASK_HW_V4_KEY_WIDTH);

        act = &entry.action;
        act->type             = FMAN_PCD_ACTION_FORWARD_FQ;
        act->forward_fq.fqid  = fqid;

        mutex_lock(&h->lock);
        slot = fman_pcd_cc_node_add_key(h->cc_v4_tcp, &entry);
        mutex_unlock(&h->lock);

        if (slot < 0) {
                ask_pr_warn("hw: cc_node_add_key v4-TCP failed (%d)\n", slot);
                return slot;
        }
        if (slot > U16_MAX) {
                /*
                 * Should never happen — node key table size is u16.
                 * Defensive: roll back the just-installed key so we
                 * do not leak a slot the dispatcher cannot reference.
                 */
                struct fman_pcd_action drop = { .type = FMAN_PCD_ACTION_DROP };
                ask_pr_warn("hw: cc_node_add_key v4-TCP slot %d > U16_MAX\n", slot);
                mutex_lock(&h->lock);
                (void)fman_pcd_cc_node_modify_next_action(h->cc_v4_tcp,
                                                         (u16)slot, &drop);
                mutex_unlock(&h->lock);
                return -EOVERFLOW;
        }

        *out_hw_id = ask_priv_pack_hw_flow_id(ASK_HW_FLOW_ID_TOKEN_V4_TCP,
                                              (u16)slot);
        return 0;
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
                return -ENODEV;     /* no HW backing -> SW fallback */

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
        struct fman_pcd_action drop = { .type = FMAN_PCD_ACTION_DROP };
        u16 token, key_idx;
        int rc;

        h = ask_hw_pcd_get();
        if (!h)
                return -ENODEV;

        ask_priv_unpack_hw_flow_id(hw_flow_id, &token, &key_idx);

        switch (token) {
        case ASK_HW_FLOW_ID_TOKEN_NONE:
                /*
                 * Caller handed us a software-only id.  Not our slot
                 * to free; ask_flow.c body-3 should not have called us
                 * for this id, but be defensive and silently succeed.
                 */
                return 0;

        case ASK_HW_FLOW_ID_TOKEN_V4_TCP:
                /*
                 * The CC node has no real "remove key" operation in
                 * the public ABI - the silicon record format keeps
                 * slot indices stable across the lifetime of the
                 * node so callers can hold (token, idx) tuples
                 * indefinitely.  Replacing the slot's action with
                 * DROP is the documented removal pattern: future
                 * frames that hash to this slot get dropped at the
                 * CC walker rather than mis-routed to a stale FQID.
                 */
                mutex_lock(&h->lock);
                rc = fman_pcd_cc_node_modify_next_action(h->cc_v4_tcp,
                                                         key_idx, &drop);
                mutex_unlock(&h->lock);
                if (rc)
                        ask_pr_warn("hw: remove v4-TCP slot %u failed (%d)\n",
                                    key_idx, rc);
                return rc;

        default:
                ask_pr_warn("hw: remove unknown token %u (id 0x%08x)\n",
                            token, hw_flow_id);
                return -EINVAL;
        }
}
EXPORT_SYMBOL_GPL(ask_hw_flow_remove);

int ask_hw_flow_query_stats(u32 hw_flow_id, u64 *packets, u64 *bytes)
{
        /*
         * Per-CC-key counters land in M3 (PR15h - bulk OP_FLOW_DUMP_
         * STATS poller) once the dpaa rx-default fqid wiring is in
         * place and we can read MURAM-resident per-action counters.
         * Body-2 returns -EOPNOTSUPP so the genl OP_FLOW_QUERY_STATS
         * handler reports the software-table counters (which are
         * exact for the SW fallback path and zero for HW-offloaded
         * flows until the poller starts populating them).
         */
        (void)hw_flow_id;
        (void)packets;
        (void)bytes;
        return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_query_stats);

int ask_hw_init(void)
{
        struct ask_hw_ucode_version v;
        int rc;

        /*
         * Probe at module load so dmesg carries the microcode version
         * as a single "ask: hw: FMan microcode X.Y.Z" breadcrumb. If
         * the probe fails we log it but do not fail module load —
         * userspace will still be able to query ASK_CMD_GET_INFO and
         * receive zero ucode fields plus a -ENODEV trail in dmesg
         * explaining why.
         */
        rc = ask_hw_ucode_get_version(&v);
        if (rc) {
                ask_pr_warn("hw: ucode version probe failed (%d); ASK_CMD_GET_INFO will report zeros\n",
                            rc);
                /* Continue - non-fatal. */
        }

        /*
         * PR14g-body-1: bring up the FMan PCD chain.  Always returns 0 -
         * any real failure is logged inside and results in ask_hw_pcd_get()
         * returning NULL, which body-2's dispatcher treats as the
         * software-only fallback signal.
         */
        (void)ask_hw_pcd_bringup();
        return 0;
}

void ask_hw_exit(void)
{
        ask_hw_pcd_teardown();
        WRITE_ONCE(ask_hw_cached_valid, false);
}
