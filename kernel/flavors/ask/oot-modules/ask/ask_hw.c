// SPDX-License-Identifier: GPL-2.0
/*
 * ask_hw.c — ASK2 hardware backend (board-substrate consumer).
 *
 * HISTORY.  Through v1.3 this file drove the FMan PCD directly via the
 * ASK-flavor <linux/fsl/fman_pcd.h> / <linux/fsl/dpaa_flow_offload.h>
 * pointer API: a per-port private CC tree + KG scheme grafted onto the
 * kernel scheme by a pre-netdev hook, plus per-flow MANIP chains
 * (RMV_ETHERNET + INSRT_GENERIC + IPV4_FORWARD) allocated one-per-flow.
 * That API died with the single-image collapse, and the per-flow MANIP
 * model was the source of the `fman_pcd_manip_chain_create() failed -12`
 * MURAM-exhaustion blocker.
 *
 * BOARD SUBSTRATE (2026-06-15).  ask.ko now consumes ONLY the exported
 * COMMON-board FMan capability API (see include/ask_fman_caps.h):
 *
 *   - CC steering addressed by (struct fman *, u8 port_id):
 *       fman_cc_tree_install / _add_key / _remove_key / _destroy
 *     The CC tree object lives inside fsl_dpa.ko; ask.ko never owns
 *     fman_pcd_cc_node / fman_pcd_cc_tree handles.  The RSS bring-up
 *     (keygen_port_hashing_init) already armed each port's KG scheme
 *     with the CC_EN gate at MAC probe, so there is no graft, no
 *     mode-RMW, no carrier flap and no pre-netdev hook.
 *   - Shared, refcounted next-hop header manip:
 *       fman_hm_nexthop_get / fman_hm_nexthop_put (board patch 0120).
 *     MURAM use scales O(next-hops) not O(flows).
 *
 * This file (Stage B of ask2-cc-repoint) reshapes bring-up/teardown and
 * the per-flow cookie onto that substrate; the per-flow insert path is
 * a declared stub (-EOPNOTSUPP) until Stage C wires the rebuild-via-
 * install fast path (mirroring board patch 0109 dpaa_cls_reinstall).
 * ask.ko does not compile into the shipping single-image ISO until the
 * oot-ungate step drops the dead FLAVOR=ask CI guards.
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
#include <linux/etherdevice.h>          /* is_zero_ether_addr */
#include <linux/if_ether.h>             /* ETH_P_IP, ETH_ALEN */
#include <linux/in.h>                   /* IPPROTO_TCP */

#include "include/ask_internal.h"
#include "include/ask_fman_caps.h"      /* fman_cc_*, fman_hm_*, struct fman */

/*
 * OOT re-declaration of the one mainline FMan EXPORT_SYMBOL the board
 * substrate header does not surface.  fman_bind() resolves the FMan
 * platform device's struct fman *.  (The per-flow CC-target resolvers
 * dpaa_get_rx_fman_port() / dpaa_get_tx_fqid() — board patch 0121 — and
 * fman_port_get_id() are re-declared at their Stage-C call site in
 * ask_hw_flow_insert(), once the rebuild-via-install path lands.)
 */
struct device;
struct fman *fman_bind(struct device *dev);

/*
 * QEF blob structural constants (PR13). The microcode version
 * reported by ASK_CMD_GET_INFO is extracted from the FMan firmware
 * blob the bootloader publishes via the device tree at
 * /soc/fman@1a00000/fman-firmware/fsl,firmware. See PR13 commit
 * comment in include/ask_internal.h for rationale.
 */
#define ASK_QEF_MAGIC          0x51454601u   /* 'Q' 'E' 'F' 0x01 */
#define ASK_QEF_MAGIC_OFFSET   4
#define ASK_QEF_DESC_OFFSET    8
#define ASK_QEF_DESC_LEN       64
#define ASK_QEF_MIN_LEN        (ASK_QEF_DESC_OFFSET + ASK_QEF_DESC_LEN)

/* Cached ucode version, populated on first probe. */
static struct ask_hw_ucode_version ask_hw_cached;
static bool ask_hw_cached_valid;

/*
 * Standard FMan parse-result byte offsets (RM 8.7.3 Table 8-107).
 * Used by the KG scheme extract recipe so the silicon emits a
 * deterministic byte stream into the downstream CC tree key buffer.
 *
 * The recipe matches the kernel's default key extract field set
 * (DEFAULT_HASH_KEY_EXTRACT_FIELDS = IPSRC1 | IPDST1 | IPSEC_SPI |
 * L4PSRC | L4PDST). For non-IPSec TCP/UDP frames the SPI bytes are
 * silicon-zeros, so a CC key with bytes 8..11 = 0x00000000 / mask 0xff
 * matches non-IPSec frames and misses IPSec frames implicitly.
 *
 * Total emitted key width = 16 bytes: [SIP:4][DIP:4][SPI:4][SP:2][DP:2].
 */
#define ASK_HW_PR_OFF_IPV4_SIP  12
#define ASK_HW_PR_OFF_IPV4_DIP  16
#define ASK_HW_PR_OFF_L4_SPORT  20
#define ASK_HW_PR_OFF_L4_DPORT  22

#define ASK_HW_V4_KEY_WIDTH     16

#define ASK_HW_MAX_PORTS        8       /* LS1046A has 8 BMI RX ports total */

/*
 * Per-offloaded-port record.  Under the board substrate ask.ko owns no
 * private CC tree / KG scheme / pre-netdev hook — the CC tree lives in
 * fsl_dpa.ko and is addressed by (struct fman *, u8 port_id), and the
 * port's KG scheme was armed with the CC_EN gate by the COMMON-board
 * RSS bring-up.  Stage B only needs to remember which BMI ports carry a
 * live static CC tree so teardown can raze them; Stage C grows this with
 * the software CC-key shadow the rebuild-via-install per-flow model
 * maintains (mirroring board patch 0109 dpaa_cls_reinstall).
 */
struct ask_hw_port {
        bool            in_use;
        u8              port_id;        /* BMI hwport id (sparse 0x01..0x31) */
        bool            cc_installed;   /* a static CC tree is live on this port */
};

struct ask_hw_pcd {
        struct mutex    lock;
        struct fman     *fman;          /* shared FMan handle (fman_bind) */
        struct ask_hw_port port[ASK_HW_MAX_PORTS];

        /*
         * Per-flow cookie indirection table.  u32 cookie -> struct
         * ask_hw_flow_cookie{fm, port_id, cc_handle, hm_handle, ...}.
         * XA_FLAGS_ALLOC1 keeps cookie 0 as the "no HW backing" sentinel.
         */
        struct xarray   flow_cookies;
};

static struct ask_hw_pcd *ask_hw_pcd_inst;

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
/* Cookie indirection table (Phase 4.10 will populate from flow_offload)      */
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
/* Board-substrate bring-up / teardown                                        */
/* ------------------------------------------------------------------------- */

int ask_hw_pcd_bringup(void)
{
        struct ask_hw_pcd *h;
        struct device_node *np;
        struct platform_device *pdev;
        struct fman *fman;

        if (ask_hw_pcd_inst) {
                ask_pr_dbg("hw: pcd bringup already done\n");
                return 0;
        }

        np = of_find_compatible_node(NULL, NULL, "fsl,fman");
        if (!np) {
                ask_pr_info("hw: no fsl,fman in DT — HW offload not available\n");
                return 0;
        }

        pdev = of_find_device_by_node(np);
        of_node_put(np);
        if (!pdev) {
                ask_pr_info("hw: no platform_device for fsl,fman — HW offload not available\n");
                return 0;
        }

        fman = fman_bind(&pdev->dev);
        if (!fman) {
                ask_pr_info("hw: fman_bind() failed — HW offload not available\n");
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
        xa_init_flags(&h->flow_cookies, XA_FLAGS_ALLOC1);

        ask_hw_pcd_inst = h;

        /*
         * Balance the of_find_device_by_node() reference.  fman_bind()
         * took its own get_device(), which we deliberately hold for the
         * module lifetime (the FMan platform device is static on LS1046A)
         * to keep the cached struct fman * valid.
         */
        put_device(&pdev->dev);

        ask_pr_info("hw: board-substrate FMan handle bound; per-flow CC/HM offload available\n");
        return 0;
}

void ask_hw_pcd_teardown(void)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;
        struct ask_hw_flow_cookie *ck;
        unsigned long idx;
        unsigned int i;

        if (!h)
                return;

        ask_hw_pcd_inst = NULL;

        /*
         * Drain any flow cookies that survived to teardown, releasing each
         * flow's shared next-hop HM reference.  The per-port static tree is
         * razed wholesale just below, so the CC keys need no per-flow
         * remove here — only the HM refcounts must be balanced.
         */
        xa_for_each(&h->flow_cookies, idx, ck) {
                if (!ck)
                        continue;
                if (ck->hm_handle)
                        fman_hm_nexthop_put(ck->fm, ck->port_id, ck->hm_handle);
                xa_erase(&h->flow_cookies, idx);
                kfree(ck);
        }

        /* Raze every per-port CC static tree we installed. */
        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].cc_installed)
                        fman_cc_tree_destroy(h->fman, h->port[i].port_id);
        }

        xa_destroy(&h->flow_cookies);
        mutex_destroy(&h->lock);
        kfree(h);
        ask_pr_dbg("hw: pcd teardown complete\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
        return ask_hw_pcd_inst;
}

/* ------------------------------------------------------------------------- */
/* Legacy ABI stubs (consumers/tests still name these; Stage C removes them)  */
/* ------------------------------------------------------------------------- */

/*
 * Under the board substrate ask.ko owns no fman_pcd_cc_node objects (the
 * CC tree lives inside fsl_dpa.ko, addressed by (struct fman *, port_id)),
 * so these per-port node accessors always return NULL.  Kept as ABI stubs
 * until the consumers/tests that still name them are cleaned up in Stage C.
 */
struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_tcp_for_port(u8 hwport_id)
{
        (void)hwport_id;
        return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_tcp_for_port);

struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_udp_for_port(u8 hwport_id)
{
        (void)hwport_id;
        return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_udp_for_port);

int ask_hw_port_bind(u8 port_id, enum ask_hw_dir dir,
                     struct net_device *ingress_dev)
{
        /*
         * The board substrate arms each port's CC pipeline at MAC probe;
         * there is nothing to bind at flow_block time.  Returns 0 so the
         * ask_flow_offload.c BIND path treats every bind as a no-op.
         */
        (void)port_id; (void)dir; (void)ingress_dev;
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_port_bind);

int ask_hw_port_unbind(u8 port_id)
{
        (void)port_id;
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_port_unbind);

u32 ask_priv_pack_hw_flow_id(u16 node_token, u16 key_idx)
{
        /* Debug helper kept for ABI; xarray cookies are the live form. */
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
/* Per-flow fast path                                                         */
/* ------------------------------------------------------------------------- */

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       enum ask_hw_dir dir,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();

        (void)key; (void)oif; (void)action_flags; (void)dir;

        if (out_hw_id)
                *out_hw_id = 0;

        /* No HW backing — SW-only fallback. */
        if (!h)
                return -ENODEV;

        /*
         * Stage C wires the board-substrate per-flow fast path here:
         *   fman_hm_nexthop_get(fm, port_id, egress_tx_fqid, src_mac,
         *                       dst_mac, &hm_handle)
         *   build struct fman_cc_key{5-tuple, target_fqid = egress FQID,
         *     hm_handle}, rebuild the ingress port's software CC-key shadow
         *     and fman_cc_tree_destroy()->fman_cc_tree_install() it (the
         *     rebuild-via-install model from board patch 0109), then
         *   ask_hw_cookie_alloc() the {fm, port_id, cc_handle, hm_handle}
         *     snapshot into *out_hw_id.
         * Until that lands every insert declines so ask_flow.c keeps the
         * flow on the software fast path (sentinel cookie 0, no HW backing).
         */
        return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_insert);

int ask_hw_flow_remove(u32 hw_flow_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie *ck;

        if (!h || hw_flow_id == 0)
                return 0;

        ck = ask_hw_cookie_lookup(h, hw_flow_id);
        if (!ck) {
                /* Already torn down; treat as success (idempotent). */
                return 0;
        }

        /*
         * Board-substrate teardown.  Stage C extends this to also drop the
         * 5-tuple key from the ingress port's software CC-key shadow and
         * rebuild the static tree.  The shared next-hop header-manip node is
         * refcounted, so we always release our reference to it here.
         */
        if (ck->hm_handle)
                fman_hm_nexthop_put(ck->fm, ck->port_id, ck->hm_handle);

        ask_hw_cookie_free(h, hw_flow_id);

        ask_pr_dbg("hw: flow_remove: cookie=0x%x released\n", hw_flow_id);
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