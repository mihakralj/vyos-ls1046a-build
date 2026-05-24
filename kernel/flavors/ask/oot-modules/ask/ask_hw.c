// SPDX-License-Identifier: GPL-2.0
/*
 * ask_hw.c — ASK2 hardware backend, Path A (v1.3 plan §4.9).
 *
 * v1.3 Phase 4.9 (2026-05-24) rewrite per plans/ASK2-COURSE-CORRECTION.md.
 *
 * Path A architecture
 * -------------------
 * ASK2 owns the FMan PCD chain from boot. It NEVER grafts onto live
 * silicon and NEVER tears the chain down at runtime.
 *
 * On module load (built-in via Kconfig bool → fs_initcall, so we run
 * BEFORE dpaa_eth_probe), ask_hw_pcd_bringup() registers a
 * pre-netdev hook via fman_pcd_register_pre_netdev_hook() (kernel
 * patch 0044). When dpaa_eth_probe later calls fman_port_init() for
 * each FMan ingress port, the hook fires BEFORE register_netdev() and
 * before mainline's keygen_port_hashing_init():
 *
 *   1. Allocate a per-port KG scheme via fman_pcd_kg_scheme_create()
 *      with the silicon-truth 16-byte key layout
 *      [ SIP:4 | DIP:4 | SPI:4=0 | SPORT:2 | DPORT:2 ].
 *   2. Create empty cc_v4_tcp_in + cc_v4_udp_in CC trees whose
 *      miss_action = FORWARD_FQ(default_base_fqid). The base_fqid is
 *      handed to us by the hook — it's the FQID mainline would have
 *      programmed for this port's RX hash distribution. Unmatched
 *      frames therefore fall back into the kernel's per-CPU RX FQ
 *      pool, preserving the ARP / SYN / ICMP / VPP AF_XDP control
 *      plane exactly as if no PCD chain were present.
 *   3. Attach the CC tree to the scheme via fman_pcd_kg_attach_cc().
 *   4. Bind the scheme to this port via fman_pcd_kg_bind_port().
 *   5. Return 0 → fman_port_init() calls fman_port_use_kg_hash(port,
 *      true) → silicon walks our CC tree on every RX frame. dpaa_eth
 *      proceeds to register_netdev() with the PCD chain already live.
 *
 * No graft, no kgse_mode RMW, no late-stage KGSE_CCBS write, no
 * scheme-discovery walk, no race window. The kernel netdev sits
 * downstream of the PCD chain — it sees a frame ONLY when no
 * offloaded CC key matched.
 *
 * Per-flow updates (Phase 4.10, this file provides scaffolding only):
 *   - REPLACE: build per-flow MANIPs [rmv_eth, insrt_generic(new L2),
 *     ipv4_forward] → fuse into one chain handle → install one CC key
 *     with FMAN_PCD_ACTION_MANIPULATE{chain, peer_tx_fqid}. No graft.
 *   - DESTROY: cc_node_remove_key(slot) → chain_destroy →
 *     manip_destroy(per-flow m_insrt). Shared MANIPs untouched.
 *
 * Phase 4.9 boundary
 * ------------------
 * This file lands the boot-time install + per-port CC tree creation
 * + cookie indirection table. It deletes ~700 LOC of v1.2 graft code
 * (ask_hw_port_bind, ask_hw_port_unbind, ask_hw_pcd_build_chain,
 * ask_hw_pcd_bringup_shared_manips, the per-direction pipeline state
 * machine, the cc_v4_tcp lazy-create-on-bind logic).
 *
 * ask_hw_port_bind() and ask_hw_port_unbind() are KEPT as thin
 * pass-through stubs (return 0 / -ENODEV) so ask_flow_offload.c still
 * compiles unchanged. Phase 4.10 will rewrite ask_flow_offload.c to
 * stop calling them, after which the stub declarations are deleted
 * from ask_internal.h and this file.
 *
 * ask_hw_flow_insert() / ask_hw_flow_remove() are KEPT as -EOPNOTSUPP
 * / 0 stubs for the same reason. Phase 4.10 replaces the bodies with
 * the per-flow MANIP-chain + CC-key-add path.
 *
 * Refs:
 *   - plans/ASK2-COURSE-CORRECTION.md §2.4 Phase 4.3-4.5
 *   - kernel/flavors/ask/patches/0044-fman-pcd-pre-netdev-hook.patch
 *   - kernel/flavors/ask/patches/0050-fman-pcd-cc-wire-group-table-and-miss-ad.patch
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
#include <linux/etherdevice.h>          /* is_zero_ether_addr */
#include <linux/if_ether.h>             /* ETH_P_IP, ETH_ALEN */
#include <linux/in.h>                   /* IPPROTO_TCP */

#include "include/ask_internal.h"

/*
 * Forward declarations of the dpaa-eth helpers exported by patches
 * 0030 (dpaa-export-fman-port-id) and 0031 (dpaa-export-tx-fqid).
 * The dpaa private header is not OOT-friendly so we re-declare here.
 */
int dpaa_get_fman_port_id(struct net_device *dev, u8 *port_id);
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

/*
 * Per-port CC pipeline. The pre-netdev hook creates one of these
 * per hwport that fman_port_init() invites us to claim. Each
 * pipeline owns a private CC tree (one-group) carrying two CC
 * nodes (v4-TCP, v4-UDP) and a private KG scheme bound to the port
 * via fman_pcd_kg_bind_port().
 *
 * The CC trees start empty (num_keys=0) with
 * miss_action = FORWARD_FQ(default_base_fqid) so unmatched RX
 * frames flow to the kernel's per-CPU RX FQ pool exactly as if no
 * PCD chain were installed. Per-flow keys are added/removed at
 * runtime by ask_flow_offload.c (Phase 4.10) — no graft, no
 * mode-RMW, no carrier flap.
 */
#define ASK_HW_MAX_PORTS        8       /* LS1046A has 8 BMI RX ports total */

struct ask_hw_port {
        bool                       in_use;
        u8                         hwport_id;
        u32                        default_base_fqid;
        struct fman_pcd_cc_tree   *cc_tree;
        struct fman_pcd_cc_node   *cc_v4_tcp;
        struct fman_pcd_cc_node   *cc_v4_udp;
        struct fman_pcd_kg_scheme *scheme;
};

struct ask_hw_pcd {
        struct mutex                lock;
        struct fman                *fman;
        struct fman_pcd            *pcd;
        bool                        hook_registered;

        /*
         * Per-port pipeline records. Indexed densely by the order
         * in which the pre-netdev hook fires (hwport_id is sparse,
         * 0x01..0x31; using it as a direct array index would waste
         * 60 slots). next_slot tracks the next free entry; lookups
         * scan linearly which is fine for ≤ 8 ports.
         */
        struct ask_hw_port          port[ASK_HW_MAX_PORTS];
        unsigned int                next_slot;

        /*
         * Per-flow cookie indirection table. Phase 4.10 will populate
         * this from ask_flow_offload.c's REPLACE handler with the
         * (cc_node, key_idx, manip_chain, m_insrt) state needed for
         * subsequent DESTROY. xarray with XA_FLAGS_ALLOC1 so cookie 0
         * stays the "no HW backing" sentinel.
         */
        struct xarray               flow_cookies;
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
/* Path A pre-netdev hook — claim port, install empty CC pipeline             */
/* ------------------------------------------------------------------------- */

/*
 * Build the per-port KG extract recipe. The kernel's default scheme
 * uses (IPSRC1, IPDST1, IPSEC_SPI, L4PSRC, L4PDST). We replicate that
 * exactly so the downstream CC key layout matches stock RSS — any
 * out-of-band test that puts the scheme back into hash mode should
 * see identical CPU steering.
 *
 * default_fqid is the miss-action FQID — frames that don't match any
 * CC key fall into this FQ. We point it at the FQID mainline would
 * have programmed (handed to us by the hook), preserving the kernel
 * control plane.
 */
static void ask_hw_kg_params_fill(struct fman_pcd_kg_scheme_params *kg,
                                  u32 default_fqid)
{
        memset(kg, 0, sizeof(*kg));
        kg->id            = -1;          /* let driver allocate next free */
        kg->use_hash      = true;
        kg->default_fqid  = default_fqid;
        kg->num_extracts  = 5;

        kg->extracts[0].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg->extracts[0].offset = ASK_HW_PR_OFF_IPV4_SIP;
        kg->extracts[0].size   = 4;
        kg->extracts[0].mask   = 0xff;

        kg->extracts[1].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg->extracts[1].offset = ASK_HW_PR_OFF_IPV4_DIP;
        kg->extracts[1].size   = 4;
        kg->extracts[1].mask   = 0xff;

        /*
         * IPSEC_SPI slot — silicon zero-fills for TCP/UDP frames; CC
         * keys later install with bytes 8..11=0, mask 0xff so non-IPSec
         * frames match and IPSec frames implicitly miss.
         *
         * No public KG extract enum for IPSEC_SPI in the in-tree ABI;
         * we use a PARSE_RESULT extract at the SPI byte offset. The
         * silicon parse result places SPI at PR offset 32 for IPv4
         * ESP, but for non-IPSec frames the bytes there are
         * indeterminate. The downstream CC tree's mask-0xff equality
         * check therefore drops anything where those bytes happen to
         * look non-zero — acceptable for the M2 scope (non-IPSec
         * TCP/UDP only). v1.1 will widen the recipe for IPSec.
         */
        kg->extracts[2].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg->extracts[2].offset = 32;   /* IPv4 ESP SPI offset, RM 8.7.3 */
        kg->extracts[2].size   = 4;
        kg->extracts[2].mask   = 0xff;

        kg->extracts[3].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg->extracts[3].offset = ASK_HW_PR_OFF_L4_SPORT;
        kg->extracts[3].size   = 2;
        kg->extracts[3].mask   = 0xff;

        kg->extracts[4].src    = FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT;
        kg->extracts[4].offset = ASK_HW_PR_OFF_L4_DPORT;
        kg->extracts[4].size   = 2;
        kg->extracts[4].mask   = 0xff;
}

/*
 * Create one empty CC node attached to @tree. miss_action targets
 * @miss_fqid so unmatched frames go to the kernel's RX FQ pool.
 * num_keys = 127 pre-sizes the MURAM key table for the M2 workload
 * (16 active 5-tuples) with substantial headroom; the per-port
 * MURAM cost is (127+1) × (2×16 + 16) = 6,144 B, well within the
 * 384 KiB FMan MURAM budget even across all 8 BMI RX ports.
 */
static struct fman_pcd_cc_node *
ask_hw_create_empty_cc_node(struct fman_pcd_cc_tree *tree, u32 miss_fqid)
{
        struct fman_pcd_cc_extract extract;
        struct fman_pcd_cc_key_table keys;

        memset(&extract, 0, sizeof(extract));
        extract.type   = FMAN_PCD_CC_EXTRACT_KEY;
        extract.offset = 0;
        extract.size   = ASK_HW_V4_KEY_WIDTH;

        memset(&keys, 0, sizeof(keys));
        keys.num_keys                    = 127;
        keys.keys                        = NULL;        /* pre-allocate empty */
        keys.miss_action.type            = FMAN_PCD_ACTION_FORWARD_FQ;
        keys.miss_action.forward_fq.fqid = miss_fqid;

        return fman_pcd_cc_node_create(tree, &extract, &keys);
}

/*
 * Tear down a port's pipeline. Called from teardown and from the
 * install-error path. NULL-safe on every field.
 */
static void ask_hw_port_destroy(struct ask_hw_port *p)
{
        if (!p)
                return;

        if (p->scheme) {
                fman_pcd_kg_scheme_destroy(p->scheme);
                p->scheme = NULL;
        }
        if (p->cc_v4_udp) {
                fman_pcd_cc_node_destroy(p->cc_v4_udp);
                p->cc_v4_udp = NULL;
        }
        if (p->cc_v4_tcp) {
                fman_pcd_cc_node_destroy(p->cc_v4_tcp);
                p->cc_v4_tcp = NULL;
        }
        if (p->cc_tree) {
                fman_pcd_cc_tree_destroy(p->cc_tree);
                p->cc_tree = NULL;
        }
        p->in_use = false;
}

/*
 * The pre-netdev hook itself. Invoked from fman_port_init() (kernel
 * patch 0044) BEFORE register_netdev() runs for the dpaa_eth that
 * sits on top of this port. We have ~free reign over PCD state at
 * this point — no concurrent flow_block_offload callbacks, no NAPI
 * running on this port yet.
 *
 * Returns 0 on success → fman_port_init() will call
 * fman_port_use_kg_hash(port, true) and continue, leaving our PCD
 * chain in place.
 *
 * Returns negative errno on failure → fman_port_init() returns the
 * same error → dpaa_eth_probe fails for THIS port only. Other ports
 * continue probing. The hook is invoked for EVERY port mainline
 * would have RSS-hashed; we currently claim all of them.
 */
static int ask_pcd_install_hook(struct fman_pcd *pcd, u8 hwport_id,
                                u32 default_base_fqid,
                                u32 default_hash_size,
                                void *priv)
{
        struct ask_hw_pcd *h = priv;
        struct fman_pcd_kg_scheme_params kg;
        struct ask_hw_port *p;
        unsigned int slot;
        int rc;

        if (!h || h->pcd != pcd) {
                ask_pr_warn("hw: pcd_install_hook: pcd mismatch (got %p, expected %p)\n",
                            pcd, h ? h->pcd : NULL);
                return -EINVAL;
        }

        mutex_lock(&h->lock);
        if (h->next_slot >= ASK_HW_MAX_PORTS) {
                mutex_unlock(&h->lock);
                ask_pr_warn("hw: pcd_install_hook: port table full at port 0x%02x\n",
                            hwport_id);
                return -ENOSPC;
        }
        slot = h->next_slot;
        p = &h->port[slot];
        p->in_use            = true;
        p->hwport_id         = hwport_id;
        p->default_base_fqid = default_base_fqid;
        h->next_slot++;
        mutex_unlock(&h->lock);

        ask_pr_info("hw: pcd_install hook: port 0x%02x base_fqid=0x%x hash_size=%u — claiming\n",
                    hwport_id, default_base_fqid, default_hash_size);

        /* Build per-port CC tree (one group). */
        p->cc_tree = fman_pcd_cc_tree_create(pcd, 1);
        if (IS_ERR_OR_NULL(p->cc_tree)) {
                rc = p->cc_tree ? PTR_ERR(p->cc_tree) : -ENOMEM;
                p->cc_tree = NULL;
                ask_pr_warn("hw: pcd_install hook: port 0x%02x cc_tree_create failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        /*
         * Empty v4-TCP CC node. miss_action → default_base_fqid so
         * unmatched frames go to the kernel RX FQ.
         */
        p->cc_v4_tcp = ask_hw_create_empty_cc_node(p->cc_tree,
                                                   default_base_fqid);
        if (IS_ERR_OR_NULL(p->cc_v4_tcp)) {
                rc = p->cc_v4_tcp ? PTR_ERR(p->cc_v4_tcp) : -ENOMEM;
                p->cc_v4_tcp = NULL;
                ask_pr_warn("hw: pcd_install hook: port 0x%02x cc_v4_tcp create failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        /* Empty v4-UDP CC node (Phase 4.10 will install per-flow keys). */
        p->cc_v4_udp = ask_hw_create_empty_cc_node(p->cc_tree,
                                                   default_base_fqid);
        if (IS_ERR_OR_NULL(p->cc_v4_udp)) {
                rc = p->cc_v4_udp ? PTR_ERR(p->cc_v4_udp) : -ENOMEM;
                p->cc_v4_udp = NULL;
                ask_pr_warn("hw: pcd_install hook: port 0x%02x cc_v4_udp create failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        /* Allocate and program our own KG scheme. */
        ask_hw_kg_params_fill(&kg, default_base_fqid);
        p->scheme = fman_pcd_kg_scheme_create(pcd, &kg);
        if (IS_ERR_OR_NULL(p->scheme)) {
                rc = p->scheme ? PTR_ERR(p->scheme) : -ENOMEM;
                p->scheme = NULL;
                ask_pr_warn("hw: pcd_install hook: port 0x%02x scheme_create failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        /* Attach our CC tree to the scheme (writes KGSE_CCBS). */
        rc = fman_pcd_kg_attach_cc(p->scheme, p->cc_tree);
        if (rc) {
                ask_pr_warn("hw: pcd_install hook: port 0x%02x attach_cc failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        /* Bind the scheme to this port (writes KGSE_MV). */
        rc = fman_pcd_kg_bind_port(p->scheme, hwport_id);
        if (rc) {
                ask_pr_warn("hw: pcd_install hook: port 0x%02x bind_port failed: %d\n",
                            hwport_id, rc);
                goto err;
        }

        ask_pr_info("hw: pcd_install hook: port 0x%02x INSTALLED — empty cc_v4_tcp + cc_v4_udp trees, miss→FQ 0x%x, ready for per-flow CC keys\n",
                    hwport_id, default_base_fqid);
        return 0;

err:
        ask_hw_port_destroy(p);
        mutex_lock(&h->lock);
        h->next_slot--;
        mutex_unlock(&h->lock);
        return rc;
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

        pcd = fman_get_pcd(fman);
        if (!pcd) {
                ask_pr_info("hw: fman_get_pcd() NULL — HW offload not available\n");
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

        /*
         * Register the pre-netdev hook. Patch 0044 expects this to
         * happen BEFORE dpaa_eth_probe runs — which, for a built-in
         * ask.ko, is guaranteed by initcall ordering: ask.ko is
         * fs_initcall and dpaa_eth runs much later. For an OOT ask.ko
         * loaded after dpaa_eth has already probed, the hook will
         * register but only fire on subsequent FMan port hot-plugs
         * (which don't happen on LS1046A — the ports are static).
         * That's the v1.0 limitation; ask.ko built-in is the
         * supported configuration.
         */
        rc = fman_pcd_register_pre_netdev_hook(pcd, ask_pcd_install_hook, h);
        if (rc) {
                ask_pr_warn("hw: pre-netdev hook registration failed (%d) — HW offload not available\n",
                            rc);
                xa_destroy(&h->flow_cookies);
                mutex_destroy(&h->lock);
                kfree(h);
                put_device(&pdev->dev);
                return 0;
        }
        h->hook_registered = true;

        ask_hw_pcd_inst = h;

        /*
         * v1.1-A late-registration replay. Empirical M2 verification
         * on 2026-05-24 (kernel 6.18.31-vyos) confirmed all five FMan
         * MAC ports completed fman_port_init() BEFORE this point in
         * ask_init() ran -- async/parallel platform probing plus the
         * MODULE_SIG_FORCE startup cost stalled the hook registration
         * past every fman_port probe. Without replay the hook never
         * fires, ask_hw_flow_insert_v4_tcp() returns -ENODEV for every
         * insert, and kernel-net CPU sits at 33.14 % during forwarding.
         *
         * Drive a one-shot drain of pcd->pending_ports right now so
         * any port that captured (port_id, base_fqid, hash_size) on
         * the -ENOENT fall-through gets its install hook replayed.
         * Errors are non-fatal: a failure here only means HW offload
         * is unavailable for some ports; the kernel still forwards
         * via mainline RSS.
         */
        rc = fman_pcd_install_now_for_existing_ports(pcd);
        if (rc && rc != -ENOENT)
                ask_pr_warn("hw: install_now replay returned %d (some ports may stay on default RSS)\n",
                            rc);

        put_device(&pdev->dev);

        ask_pr_info("hw: Path A pre-netdev hook armed; CC pipelines install on first fman_port_init\n");
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
         * Unregister the hook FIRST so no concurrent fman_port_init()
         * can invoke us mid-teardown. On LS1046A this is moot (ports
         * don't hot-plug) but it keeps the contract clean.
         */
        if (h->hook_registered) {
                fman_pcd_unregister_pre_netdev_hook(h->pcd);
                h->hook_registered = false;
        }

        /* Drain any flow cookies that survived to teardown. */
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

        /* Tear down every per-port pipeline. */
        for (i = 0; i < ASK_HW_MAX_PORTS; i++)
                ask_hw_port_destroy(&h->port[i]);

        xa_destroy(&h->flow_cookies);
        mutex_destroy(&h->lock);
        kfree(h);
        ask_pr_dbg("hw: pcd teardown complete\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
        return ask_hw_pcd_inst;
}

/*
 * Helper for Phase 4.10's flow_offload path: look up the per-port
 * v4-TCP CC node by hwport_id. Returns NULL if no pipeline is
 * installed for that port.
 *
 * Callers must hold an RCU-equivalent guarantee that the pipeline
 * isn't being torn down concurrently — in practice the pipeline
 * lifetime equals ask.ko module lifetime, so any caller running
 * inside an ask_flow_offload.c callback is safe.
 */
struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_tcp_for_port(u8 hwport_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;
        unsigned int i;

        if (!h)
                return NULL;

        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].hwport_id == hwport_id)
                        return h->port[i].cc_v4_tcp;
        }
        return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_tcp_for_port);

struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_udp_for_port(u8 hwport_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;
        unsigned int i;

        if (!h)
                return NULL;

        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].hwport_id == hwport_id)
                        return h->port[i].cc_v4_udp;
        }
        return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_udp_for_port);

/* ------------------------------------------------------------------------- */
/* Phase 4.9 → 4.10 boundary stubs                                            */
/*                                                                            */
/* The following five entry points are KEPT as ABI-compatible stubs so       */
/* ask_flow_offload.c continues to compile across Phase 4.9 / 4.10.          */
/* Phase 4.10 will:                                                           */
/*  - rewrite ask_flow_offload.c to stop calling ask_hw_port_bind /          */
/*    ask_hw_port_unbind (they're meaningless under Path A; the pipelines   */
/*    are pre-installed at boot, not at FLOW_BLOCK_BIND time)                */
/*  - rewrite ask_hw_flow_insert / ask_hw_flow_remove to do real per-flow   */
/*    MANIP-chain construction + cc_node_add_key / _remove_key               */
/*  - delete ask_priv_pack_hw_flow_id / _unpack_hw_flow_id (debug-only,     */
/*    superseded by xarray cookie indirection)                               */
/* ------------------------------------------------------------------------- */

int ask_hw_port_bind(u8 port_id, enum ask_hw_dir dir,
                     struct net_device *ingress_dev)
{
        /*
         * Path A: pipelines are installed at boot via the pre-netdev
         * hook, not at flow_block bind time. This stub returns 0 so
         * the legacy ask_flow_offload.c BIND path treats every bind
         * as a successful no-op. Phase 4.10 deletes the caller.
         */
        (void)port_id; (void)dir; (void)ingress_dev;
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_port_bind);

int ask_hw_port_unbind(u8 port_id)
{
        /*
         * Path A: pipelines persist for the lifetime of ask.ko.
         * Unbind is a runtime no-op; the CC trees are emptied by
         * per-flow remove_key calls and torn down only at
         * ask_hw_exit().
         */
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

/*
 * Build per-flow MANIP chain (v1.3 Path A, plan §4.2):
 *
 *   1. m_rmv   = MANIP_RMV_ETHERNET  (strip 14-byte ingress L2)
 *   2. m_insrt = MANIP_INSRT_GENERIC (push 14-byte egress L2:
 *                next_hop_mac + egress_mac + ETH_P_IP)
 *   3. m_ipv4  = MANIP_FIELD_UPDATE_IPV4_FORWARD (TTL-- + cksum
 *                recompute)
 *   4. chain   = fman_pcd_manip_chain_create([m_rmv, m_insrt, m_ipv4])
 *
 * The chain handle is consumed by a CC-key action atom of type
 * FMAN_PCD_ACTION_MANIPULATE { .manip = chain, .next_fqid = tx_fqid }.
 *
 * On any failure all partially-constructed manips are destroyed and
 * an errno is propagated to the caller (ask_flow_offload.c REPLACE
 * handler). The ASK_HW path NEVER leaks MURAM on the error path —
 * caller falls back to SW-only.
 */
static int ask_hw_build_manip_chain(struct ask_hw_pcd *h,
                                    const struct ask_flow_key *key,
                                    struct fman_pcd_manip **out_rmv,
                                    struct fman_pcd_manip **out_insrt,
                                    struct fman_pcd_manip **out_ipv4,
                                    struct fman_pcd_manip **out_chain)
{
        struct fman_pcd_manip_params p;
        struct fman_pcd_manip *m_rmv = NULL, *m_insrt = NULL;
        struct fman_pcd_manip *m_ipv4 = NULL, *chain = NULL;
        struct fman_pcd_manip *src[3];
        __be16 etype = htons(ETH_P_IP);
        int rc;

        /* 1. RMV_ETHERNET — strip 14-byte ingress L2 header. */
        memset(&p, 0, sizeof(p));
        p.type = FMAN_PCD_MANIP_RMV_ETHERNET;
        m_rmv = fman_pcd_manip_create(h->pcd, &p);
        if (IS_ERR_OR_NULL(m_rmv)) {
                rc = m_rmv ? PTR_ERR(m_rmv) : -ENOMEM;
                m_rmv = NULL;
                goto err;
        }

        /* 2. INSRT_GENERIC — push new 14-byte L2 header. */
        memset(&p, 0, sizeof(p));
        p.type = FMAN_PCD_MANIP_INSRT_GENERIC;
        p.insrt_generic.size = 14;
        memcpy(&p.insrt_generic.hdr[0], key->next_hop_mac, ETH_ALEN);
        memcpy(&p.insrt_generic.hdr[6], key->egress_mac,   ETH_ALEN);
        memcpy(&p.insrt_generic.hdr[12], &etype, 2);
        m_insrt = fman_pcd_manip_create(h->pcd, &p);
        if (IS_ERR_OR_NULL(m_insrt)) {
                rc = m_insrt ? PTR_ERR(m_insrt) : -ENOMEM;
                m_insrt = NULL;
                goto err;
        }

        /* 3. FIELD_UPDATE_IPV4_FORWARD — TTL-- + IPv4 cksum recompute. */
        memset(&p, 0, sizeof(p));
        p.type = FMAN_PCD_MANIP_FIELD_UPDATE_IPV4_FORWARD;
        p.ipv4_forward.recompute_cksum = true;
        m_ipv4 = fman_pcd_manip_create(h->pcd, &p);
        if (IS_ERR_OR_NULL(m_ipv4)) {
                rc = m_ipv4 ? PTR_ERR(m_ipv4) : -ENOMEM;
                m_ipv4 = NULL;
                goto err;
        }

        /* 4. Concatenate into one HMCT. */
        src[0] = m_rmv;
        src[1] = m_insrt;
        src[2] = m_ipv4;
        chain = fman_pcd_manip_chain_create(h->pcd, src, 3);
        if (IS_ERR_OR_NULL(chain)) {
                rc = chain ? PTR_ERR(chain) : -ENOMEM;
                chain = NULL;
                goto err;
        }

        *out_rmv   = m_rmv;
        *out_insrt = m_insrt;
        *out_ipv4  = m_ipv4;
        *out_chain = chain;
        return 0;

err:
        if (m_ipv4)  fman_pcd_manip_destroy(m_ipv4);
        if (m_insrt) fman_pcd_manip_destroy(m_insrt);
        if (m_rmv)   fman_pcd_manip_destroy(m_rmv);
        return rc;
}

/*
 * Build the 16-byte exact-match CC key matching the per-port KG
 * scheme's 5-extract recipe [SIP:4][DIP:4][SPI:4=0][SP:2][DP:2].
 *
 * Bytes 8..11 (SPI slot) are silicon-zero for non-IPSec TCP/UDP, so
 * we leave them at 0 with mask 0xff — non-IPSec frames match, IPSec
 * frames implicitly miss (v1.0 scope).
 */
static void ask_hw_build_cc_key_v4(const struct ask_flow_key *key,
                                   struct fman_pcd_cc_key_entry *entry)
{
        memset(entry, 0, sizeof(*entry));

        /* Bytes 0..3 : src IPv4 */
        memcpy(&entry->key[0], &key->src_ip[0], 4);
        /* Bytes 4..7 : dst IPv4 */
        memcpy(&entry->key[4], &key->dst_ip[0], 4);
        /* Bytes 8..11: SPI = 0 (non-IPSec); already zeroed. */
        /* Bytes 12..13: L4 src port */
        memcpy(&entry->key[12], &key->sport, 2);
        /* Bytes 14..15: L4 dst port */
        memcpy(&entry->key[14], &key->dport, 2);

        /* Exact-match mask across all 16 emitted key bytes. */
        memset(&entry->mask[0], 0xff, ASK_HW_V4_KEY_WIDTH);
}

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       enum ask_hw_dir dir,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct net_device *dev_iif = NULL, *dev_oif = NULL;
        struct fman_pcd_cc_node *cc_node;
        struct fman_pcd_manip *m_rmv = NULL, *m_insrt = NULL;
        struct fman_pcd_manip *m_ipv4 = NULL, *chain = NULL;
        struct fman_pcd_cc_key_entry entry;
        struct ask_hw_flow_cookie ck;
        u32 tx_fqid = 0, cookie = 0;
        u8 hwport_id;
        int rc, slot;

        (void)action_flags; (void)dir;

        if (out_hw_id)
                *out_hw_id = 0;

        /* No HW backing → SW-only fallback. */
        if (!h)
                return -ENODEV;

        /* v1.0 scope: IPv4 TCP only. UDP v6 land in M3. */
        if (key->l3_proto != ASK_FLOW_L3_IPV4 ||
            key->l4_proto != IPPROTO_TCP)
                return -EOPNOTSUPP;

        /* Neigh not yet resolved → caller retries after netevent. */
        if (is_zero_ether_addr(key->next_hop_mac) ||
            is_zero_ether_addr(key->egress_mac))
                return -EAGAIN;

        /* Resolve ingress kernel ifindex → FMan BMI hwport_id. */
        dev_iif = dev_get_by_index(&init_net, key->iif);
        if (!dev_iif) {
                rc = -ENODEV;
                goto out;
        }
        rc = dpaa_get_fman_port_id(dev_iif, &hwport_id);
        if (rc)
                goto out;

        /* Per-port CC node installed by the pre-netdev hook. */
        cc_node = ask_hw_pcd_cc_v4_tcp_for_port(hwport_id);
        if (!cc_node) {
                rc = -ENODEV;
                goto out;
        }

        /* Resolve egress ifindex → DPAA TX FQID. Use queue 0
         * (single TX path; M3 will add per-CPU TX queue selection).
         */
        dev_oif = dev_get_by_index(&init_net, oif);
        if (!dev_oif) {
                rc = -ENODEV;
                goto out;
        }
        rc = dpaa_get_tx_fqid(dev_oif, 0, &tx_fqid);
        if (rc)
                goto out;

        /* Build the per-flow 3-MANIP chain (rmv + insrt + ipv4). */
        rc = ask_hw_build_manip_chain(h, key, &m_rmv, &m_insrt,
                                      &m_ipv4, &chain);
        if (rc)
                goto out;

        /* Build the CC key and install it. */
        ask_hw_build_cc_key_v4(key, &entry);
        entry.action.type = FMAN_PCD_ACTION_MANIPULATE;
        entry.action.manipulate.manip     = chain;
        entry.action.manipulate.next_fqid = tx_fqid;

        slot = fman_pcd_cc_node_add_key(cc_node, &entry);
        if (slot < 0) {
                rc = slot;
                ask_pr_warn("hw: flow_insert: cc_node_add_key failed: %d\n", rc);
                goto out_chain;
        }

        /* Stash cookie state for the matching remove. */
        memset(&ck, 0, sizeof(ck));
        ck.cc_node      = cc_node;
        ck.key_idx      = (u16)slot;
        ck.m_rmv        = m_rmv;
        ck.m_insrt      = m_insrt;
        ck.m_ipv4       = m_ipv4;
        ck.manip_chain  = chain;
        ck.sink_ifindex = oif;
        ck.sink_fqid    = tx_fqid;

        cookie = ask_hw_cookie_alloc(h, &ck);
        if (cookie == 0) {
                rc = -ENOMEM;
                ask_pr_warn("hw: flow_insert: cookie_alloc failed\n");
                fman_pcd_cc_node_remove_key(cc_node, (u16)slot);
                goto out_chain;
        }

        *out_hw_id = cookie;
        rc = 0;

        ask_pr_dbg("hw: flow_insert: port 0x%02x slot=%d tx_fqid=0x%x cookie=0x%x\n",
                   hwport_id, slot, tx_fqid, cookie);
        goto out;

out_chain:
        if (chain)   fman_pcd_manip_chain_destroy(chain);
        if (m_ipv4)  fman_pcd_manip_destroy(m_ipv4);
        if (m_insrt) fman_pcd_manip_destroy(m_insrt);
        if (m_rmv)   fman_pcd_manip_destroy(m_rmv);
out:
        if (dev_oif) dev_put(dev_oif);
        if (dev_iif) dev_put(dev_iif);
        return rc;
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

        if (ck->cc_node)
                fman_pcd_cc_node_remove_key(ck->cc_node, ck->key_idx);
        if (ck->manip_chain)
                fman_pcd_manip_chain_destroy(ck->manip_chain);
        if (ck->m_ipv4)
                fman_pcd_manip_destroy(ck->m_ipv4);
        if (ck->m_insrt)
                fman_pcd_manip_destroy(ck->m_insrt);
        if (ck->m_rmv)
                fman_pcd_manip_destroy(ck->m_rmv);

        ask_hw_cookie_free(h, hw_flow_id);

        ask_pr_dbg("hw: flow_remove: cookie=0x%x torn down\n", hw_flow_id);
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