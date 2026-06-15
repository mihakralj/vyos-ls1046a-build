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
#include <linux/unaligned.h>            /* get_unaligned_be32 */

#include "include/ask_internal.h"
#include "include/ask_fman_caps.h"      /* fman_cc_*, fman_hm_*, struct fman */

/*
 * OOT re-declarations of the mainline / board-substrate EXPORT_SYMBOLs the
 * vendored caps header does not surface (a bindeb-pkg linux-headers package
 * carries no driver headers, so re-declaring locally is the only mechanism).
 * fman_bind() resolves the FMan platform device's struct fman *.  The per-flow
 * CC-target resolvers dpaa_get_rx_fman_port() + dpaa_get_tx_fqid() (board patch
 * 0121) and fman_port_get_id() (board patch 0104) map an ASK flow's ingress /
 * egress netdevs to the (port_id, tx_fqid) the rebuild-via-install fast path
 * needs.  Mirrors the identical shim already linking in ask_flow_offload.c.
 */
struct device;
struct fman_port;
struct fman *fman_bind(struct device *dev);
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);
int dpaa_get_tx_fqid(struct net_device *dev, u32 queue, u32 *fqid);

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
struct ask_hw_cc_slot {
        bool                    used;
        u32                     key_id;   /* monotonic; == cookie.cc_handle */
        struct fman_cc_key      key;      /* 5-tuple + target_fqid + hm_handle */
};

struct ask_hw_port {
        bool            in_use;
        u8              port_id;        /* BMI hwport id (sparse 0x01..0x31) */
        bool            cc_installed;   /* a static CC tree is live on this port */
        u16             nkeys;          /* live entries in shadow[] */
        u32             next_key_id;    /* per-port monotonic id (never 0) */
        struct ask_hw_cc_slot shadow[FMAN_CC_MAX_STATIC_KEYS];
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

/*
 * Resolve (or claim) the per-ingress-port shadow record for @port_id.
 * Returns NULL only when all ASK_HW_MAX_PORTS slots are already owned by
 * other ports.  Caller holds h->lock.
 */
static struct ask_hw_port *ask_hw_port_slot_get(struct ask_hw_pcd *h, u8 port_id)
{
        unsigned int i, free_idx = ASK_HW_MAX_PORTS;

        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use) {
                        if (h->port[i].port_id == port_id)
                                return &h->port[i];
                } else if (free_idx == ASK_HW_MAX_PORTS) {
                        free_idx = i;
                }
        }
        if (free_idx == ASK_HW_MAX_PORTS)
                return NULL;

        h->port[free_idx].in_use       = true;
        h->port[free_idx].port_id      = port_id;
        h->port[free_idx].cc_installed = false;
        h->port[free_idx].nkeys        = 0;
        h->port[free_idx].next_key_id  = 1;
        return &h->port[free_idx];
}

/*
 * Rebuild-via-install (mirrors board patch 0109 dpaa_cls_reinstall): raze the
 * port's live CC static tree, then re-materialise it from the current software
 * shadow and install it.  Destroying first clears the port's KGSE_CCBS gate
 * before the MURAM is freed, so no in-flight frame walks a half-torn tree (the
 * brief gap routes frames to the RSS scheme).  Caller holds h->lock.
 */
static int ask_hw_port_reinstall(struct ask_hw_pcd *h, struct ask_hw_port *p)
{
        struct fman_cc_static_tree *tree;
        unsigned int i;
        int rc;

        if (p->cc_installed) {
                fman_cc_tree_destroy(h->fman, p->port_id);
                p->cc_installed = false;
        }
        if (p->nkeys == 0)
                return 0;

        tree = kzalloc(sizeof(*tree), GFP_KERNEL);
        if (!tree)
                return -ENOMEM;

        for (i = 0; i < FMAN_CC_MAX_STATIC_KEYS; i++) {
                if (!p->shadow[i].used)
                        continue;
                tree->keys[tree->num_keys++] = p->shadow[i].key;
        }
        /* miss_fqid 0 -> non-matching frames stay on the RSS-hash path. */
        rc = fman_cc_tree_install(h->fman, p->port_id, tree);
        if (!rc)
                p->cc_installed = true;
        kfree(tree);
        return rc;
}

/*
 * Map an ASK flow netdev ifindex to its ingress BMI hwport id via the board
 * resolver chain.  Returns -ENODEV for a non-DPAA / unknown ifindex (the
 * graceful SW-fallback signal).  ASK offload flows on this single-image board
 * live in the init namespace.
 */
static int ask_hw_resolve_iif_port(u32 ifindex, u8 *port_id)
{
        struct net_device *dev;
        struct fman_port *port;

        dev = dev_get_by_index(&init_net, ifindex);
        if (!dev)
                return -ENODEV;
        port = dpaa_get_rx_fman_port(dev);
        if (!port) {
                dev_put(dev);
                return -ENODEV;
        }
        *port_id = fman_port_get_id(port);
        dev_put(dev);
        return 0;
}

/*
 * Map an ASK flow egress netdev ifindex to its TX QMan FQID (queue 0).
 * Returns -ENODEV for a non-DPAA / unknown ifindex.
 */
static int ask_hw_resolve_oif_fqid(u32 ifindex, u32 *fqid)
{
        struct net_device *dev;
        int rc;

        dev = dev_get_by_index(&init_net, ifindex);
        if (!dev)
                return -ENODEV;
        rc = dpaa_get_tx_fqid(dev, 0, fqid);
        dev_put(dev);
        return rc ? -ENODEV : 0;
}

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       enum ask_hw_dir dir,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie ck;
        struct ask_hw_port *p;
        struct fman_cc_key *k;
        unsigned int slot;
        u32 tx_fqid = 0;
        u32 hm_handle = 0;
        u32 cookie;
        u8  port_id = 0;
        int rc;

        (void)action_flags; (void)dir;

        /* NULL-arg contract: fail without touching *out_hw_id. */
        if (!key || !out_hw_id)
                return -EINVAL;

        /* No HW backing -> SW-only fallback. */
        if (!h)
                return -ENODEV;

        /* Body ships v4 TCP/UDP only; everything else falls back to SW. */
        if (key->l3_proto != ASK_FLOW_L3_IPV4 ||
            (key->l4_proto != IPPROTO_TCP && key->l4_proto != IPPROTO_UDP))
                return -EOPNOTSUPP;

        /*
         * Neighbour not yet resolved: keep the flow in SW until the egress L2
         * header is known, then the upper layer re-inserts.
         */
        if (is_zero_ether_addr(key->next_hop_mac) ||
            is_zero_ether_addr(key->egress_mac))
                return -EAGAIN;

        /* Ingress hwport owns the CC tree; egress FQID is the forward target. */
        rc = ask_hw_resolve_iif_port(key->iif, &port_id);
        if (rc)
                return rc;
        rc = ask_hw_resolve_oif_fqid(oif, &tx_fqid);
        if (rc)
                return rc;

        mutex_lock(&h->lock);

        p = ask_hw_port_slot_get(h, port_id);
        if (!p) {
                rc = -ENOSPC;
                goto out_unlock;
        }
        if (p->nkeys >= FMAN_CC_MAX_STATIC_KEYS) {
                rc = -ENOSPC;
                goto out_unlock;
        }
        for (slot = 0; slot < FMAN_CC_MAX_STATIC_KEYS; slot++)
                if (!p->shadow[slot].used)
                        break;
        if (slot == FMAN_CC_MAX_STATIC_KEYS) {
                rc = -ENOSPC;
                goto out_unlock;
        }

        /*
         * Resolve (and refcount) the shared next-hop HM node.  MAC order per
         * the caps header: src_mac = egress port's own MAC, dst_mac = next-hop
         * MAC.  -ENOTSUPP (ucode lacks HM caps) maps to -EOPNOTSUPP so the
         * caller treats it as a clean SW fallback, not a hard error.
         */
        rc = fman_hm_nexthop_get(h->fman, port_id, tx_fqid,
                                 key->egress_mac, key->next_hop_mac,
                                 &hm_handle);
        if (rc) {
                rc = (rc == -ENOTSUPP) ? -EOPNOTSUPP : rc;
                goto out_unlock;
        }

        /* Build the masked 5-tuple CC key -> FORWARD_FQ_WITH_MANIP atom. */
        k = &p->shadow[slot].key;
        memset(k, 0, sizeof(*k));
        k->ethertype    = FMAN_CC_ETHERTYPE_IPV4;
        k->proto        = key->l4_proto;
        k->is_ipv6      = 0;
        k->src_ip       = get_unaligned_be32(&key->src_ip[0]);
        k->dst_ip       = get_unaligned_be32(&key->dst_ip[0]);
        k->src_ip_mask  = 0xffffffffu;
        k->dst_ip_mask  = 0xffffffffu;
        k->src_port     = be16_to_cpu(key->sport);
        k->dst_port     = be16_to_cpu(key->dport);
        k->target_qband = 0;
        k->target_fqid  = tx_fqid;
        k->hm_handle    = hm_handle;

        p->shadow[slot].used   = true;
        p->shadow[slot].key_id = p->next_key_id;
        p->nkeys++;

        rc = ask_hw_port_reinstall(h, p);
        if (rc)
                goto out_rollback;

        ck.fm           = h->fman;
        ck.port_id      = port_id;
        ck.cc_handle    = p->shadow[slot].key_id;
        ck.hm_handle    = hm_handle;
        ck.sink_ifindex = (int)oif;
        ck.sink_fqid    = tx_fqid;

        cookie = ask_hw_cookie_alloc(h, &ck);
        if (!cookie) {
                rc = -ENOMEM;
                goto out_rollback;
        }

        /* Commit: consume the per-port id (skip 0) and publish the cookie. */
        if (++p->next_key_id == 0)
                p->next_key_id = 1;

        mutex_unlock(&h->lock);
        *out_hw_id = cookie;
        ask_pr_dbg("hw: flow_insert: port=0x%02x fqid=%u hm=0x%x key_id=%u cookie=0x%x\n",
                   port_id, tx_fqid, hm_handle, ck.cc_handle, cookie);
        return 0;

out_rollback:
        p->shadow[slot].used = false;
        p->nkeys--;
        /* Best-effort: restore the tree to the pre-insert key set. */
        (void)ask_hw_port_reinstall(h, p);
        if (hm_handle)
                fman_hm_nexthop_put(h->fman, port_id, hm_handle);
out_unlock:
        mutex_unlock(&h->lock);
        return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_insert);

int ask_hw_flow_remove(u32 hw_flow_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie *ck;
        struct ask_hw_port *p = NULL;
        unsigned int i;

        if (!h || hw_flow_id == 0)
                return 0;

        mutex_lock(&h->lock);

        ck = ask_hw_cookie_lookup(h, hw_flow_id);
        if (!ck) {
                /* Already torn down; treat as success (idempotent). */
                mutex_unlock(&h->lock);
                return 0;
        }

        /*
         * Drop this flow's 5-tuple from the ingress port's software CC shadow
         * (matched by the monotonic key_id snapshotted in cc_handle) and
         * rebuild the static tree without it.  Then release the shared
         * next-hop HM reference (refcounted: the node survives until the last
         * flow toward that adjacency is gone).
         */
        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].port_id == ck->port_id) {
                        p = &h->port[i];
                        break;
                }
        }
        if (p && ck->cc_handle) {
                for (i = 0; i < FMAN_CC_MAX_STATIC_KEYS; i++) {
                        if (p->shadow[i].used &&
                            p->shadow[i].key_id == ck->cc_handle) {
                                p->shadow[i].used = false;
                                p->nkeys--;
                                (void)ask_hw_port_reinstall(h, p);
                                break;
                        }
                }
        }

        if (ck->hm_handle)
                fman_hm_nexthop_put(ck->fm, ck->port_id, ck->hm_handle);

        ask_hw_cookie_free(h, hw_flow_id);

        mutex_unlock(&h->lock);
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