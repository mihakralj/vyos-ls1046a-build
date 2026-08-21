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
 * BOARD SUBSTRATE (2026-06-15, updated 2026-07-27).  ask.ko now consumes ONLY
 * the exported COMMON-board FMan capability API (see include/ask_fman_caps.h):
 *
 *   - FE-VM offload via kernel API (Fork-B path):
 *       fman_pcd_fe_engage / fman_pcd_fe_disengage / fman_pcd_fe_flow_add / _del
 *     The FE-VM pipeline (pool/singletons/ehash/hashfe/enq/enter/arm) is built
 *     via the kernel API. Flow insert uses ask_fe_flow_insert() in
 *     ask_flow_offload.c. This is the Fork-B FE-VM ehash path.
 *
 * Fix C1 (2026-07-14): Removed Fork-A path (CC static tree via fman_cc_tree_install).
 * CR-007 (2026-07-27): Removed Fork-A shadow/HM bookkeeping (shadow[], nkeys,
 * next_key_id, cc_installed, cc_handle, hm_handle). The FE-VM ehash path
 * manages its own flow keys; the cookie now snapshots only port identity
 * and sink metadata for teardown.
 *
 * This file (Stage B of ask2-cc-repoint) reshapes bring-up/teardown and
 * the per-flow cookie onto that substrate; the per-flow insert path uses
 * the Fork-B FE-VM ehash path (ask_debugfs_fe_flow_write in ask_flow_offload.c).
 * Since the 2026-06-14 oot-ungate, ask.ko builds, signs, and packages
 * (dormant) into every single-image ISO; it engages only at runtime.
 */
#include <linux/fs.h>
#include <linux/file.h>

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/atomic.h>
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
#include <linux/fsl/fman_pcd.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/xarray.h>
#include <net/net_namespace.h>
#include <linux/etherdevice.h>          /* is_zero_ether_addr */
#include <linux/if_ether.h>             /* ETH_P_IP, ETH_ALEN */
#include <linux/in.h>                   /* IPPROTO_TCP */
#include <linux/unaligned.h>            /* get_unaligned_be32 */
#include <net/addrconf.h>               /* F-219: ipv6_addr_type, in6_dev_get */
#include <net/if_inet6.h>               /* F-219: struct inet6_dev / inet6_ifaddr */
#include <soc/fsl/qman.h>          /* qman_alloc_fqid, qman_create_fq, QMAN_FQ_FLAG_NO_ENQUEUE */

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
struct device *fman_get_dev(struct fman *fman);
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);
int dpaa_get_tx_fqid(struct net_device *dev, u32 queue, u32 *fqid);
/* F-199 (T-M7-2 S4): allocate a dedicated no-confirm TX FQ for the FE
 * hardware offload terminal (context_a B0V=0). Board patch exports it. */
int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);

/*
 * PER-PORT OFFLOAD FAMILY MASK (2026-08-21). IPv4 and IPv6 are selected
 * INDEPENDENTLY PER INTERFACE via the CLI `offload ipv4` / `offload ipv6`
 * knobs (the old single `offload ask` is removed). Userspace sends the mask
 * with ASK_CMD_ENGAGE (ASK_ATTR_FAMILY_MASK); ask_hw_offload_set_family()
 * records it here keyed by hardware port id. Flow admission
 * (ask_hw_flow_family_ok) consults the TRUE INGRESS port's mask — never the
 * interface's addresses and never engage timing — so late DHCP/SLAAC works
 * per-flow with no re-engage, exactly like IPv4 always has.
 *
 * The unified 46-byte dual-lane scheme/table (F-224/F-225) is family-neutral
 * and always programmed at engage regardless of the mask; the mask only gates
 * which families ask.ko publishes flow records for. Index is hw_port_id
 * (sparse 0x08..0x27, < 64). Default 0 = no family (a port not engaged, or
 * engaged with an explicit family, is set before its first flow).
 */
bool fman_pcd_v6_enabled(void);	/* F-210 export; still consulted by kernel */
static u8 ask_hw_port_family[64];

void ask_hw_offload_set_family(u8 hw_port_id, u8 family_mask)
{
	if (hw_port_id < ARRAY_SIZE(ask_hw_port_family))
		WRITE_ONCE(ask_hw_port_family[hw_port_id],
			   family_mask & (ASK_FAM_V4 | ASK_FAM_V6));
}
EXPORT_SYMBOL_GPL(ask_hw_offload_set_family);

static u8 ask_hw_port_family_get(u8 hw_port_id)
{
	if (hw_port_id < ARRAY_SIZE(ask_hw_port_family))
		return READ_ONCE(ask_hw_port_family[hw_port_id]);
	return 0;
}

/* Flow admission for the preflight + insert gates, gated by the INGRESS
 * port's selected family mask. TCP/UDP only (both families). Returns 0 if the
 * flow may be hardware-offloaded on @hw_port_id, -EOPNOTSUPP otherwise (SW
 * fallback). */
static int ask_hw_flow_family_ok(u8 hw_port_id, const struct ask_flow_key *key)
{
	u8 fmask = ask_hw_port_family_get(hw_port_id);

	if (key->l4_proto != IPPROTO_TCP && key->l4_proto != IPPROTO_UDP)
		return -EOPNOTSUPP;
	if (key->l3_proto == ASK_FLOW_L3_IPV4)
		return (fmask & ASK_FAM_V4) ? 0 : -EOPNOTSUPP;
	if (key->l3_proto == ASK_FLOW_L3_IPV6)
		return (fmask & ASK_FAM_V6) ? 0 : -EOPNOTSUPP;
	return -EOPNOTSUPP;
}

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
 * Fix M2: The recipe now uses EKFC=0x001C0006 (IPSRC1 | IPDST1 | PTYPE1 |
 * L4PSRC | L4PDST) instead of the kernel's default 0x00180206 which included
 * IPSEC_SPI. The SPI field is removed because non-IPSec frames have undefined
 * SPI bytes, and PTYPE1 is added to distinguish TCP/UDP flows with the same
 * IP:port 4-tuple. This matches the spec requirement in
 * specs/fman-keygen-flow-key-spec.md §3.4.
 *
 * F-163 (2026-08-05): recipe extended to EKFC=0x801C0006, adding
 * KG_SCH_KN_PORT_ID (bit 31) so the extracted key carries a leading
 * ingress-port byte matching the real vendor cdx.ko external-hash key
 * format (union dpa_key, nxp-sdk branch cdx_common.h). Being the highest
 * set bit, PORT_ID lands first under the silicon's confirmed MSB-first
 * descending assembly order (spec §3.4).
 *
 * Total emitted key width = 14 bytes:
 * [PORT_ID:1][SIP:4][DIP:4][PROTO:1][SP:2][DP:2]. This is the Fork-B
 * FE-VM ehash path key format used by ask_fe_build_key() in
 * ask_flow_offload.c (ASK_FE_KEY_SIZE).
 */
#define ASK_HW_PR_OFF_IPV4_SIP  12
#define ASK_HW_PR_OFF_IPV4_DIP  16
#define ASK_HW_PR_OFF_L4_SPORT  20
#define ASK_HW_PR_OFF_L4_DPORT  22

#define ASK_HW_V4_KEY_WIDTH     14      /* F-163: was 13, +1 for PORT_ID prefix */

#define ASK_HW_MAX_PORTS        8       /* LS1046A has 8 BMI RX ports total */

#define ASK_HW_NOCONF_SLOTS     16      /* T-M7-2 S4: no-confirm TX FQ cache */

/*
 * Per-offloaded-port record.  Under the board substrate ask.ko owns no
 * private CC tree / KG scheme / pre-netdev hook — the CC tree lives in
 * CR-007 (2026-07-27): Fork-A shadow/HM bookkeeping removed. The FE-VM
 * ehash path (Fork-B) manages its own flow keys via ask_fe_flow_insert/remove().
 */
struct ask_hw_port {
        bool            in_use;
        u8              port_id;        /* BMI hwport id (sparse 0x01..0x31) */
        bool            offload_engaged;/* M1 coarse S1 mode-switch active (0129) */
};

struct ask_hw_pcd {
        struct mutex    lock;
        struct fman     *fman;          /* shared FMan handle (fman_bind) */
        struct ask_hw_port port[ASK_HW_MAX_PORTS];

        /*
         * Per-flow cookie indirection table.  u32 cookie -> struct
         * ask_hw_flow_cookie{fm, port_id, sink_ifindex, sink_fqid}.
         * XA_FLAGS_ALLOC1 keeps cookie 0 as the "no HW backing" sentinel.
         */
        struct xarray   flow_cookies;
        /* P4.1: dedicated QMan TX FQ for hardware direct-enqueue.
         * Allocated at bringup, released at teardown, used by
         * ask_hw_resolve_oif_fqid() as the preferred egress FQ
         * for all CC-tree flows (avoids mainline dpaa_eth FQ
         * taildrop bottleneck). */
        struct qman_fq  dedicated_fq;
        bool            dedicated_fq_ready;

        /*
         * T-M7-2 S4 (2026-08-15): per-egress-interface no-confirm TX FQ cache.
         * The FE hardware terminal (F-198) must enqueue to a TX FQ whose FQD
         * has B0V=0 so FMan emits no per-frame TX-confirm FD (the ~2.2 Gbps
         * ceiling on the shared queue-0 TX FQ). dpaa_alloc_offload_tx_fq()
         * (board patch F-199) allocates one such FQ per netdev on that
         * netdev's own QMan channel; the FQ lives in the netdev's
         * dpaa_fq_list and is freed by fsl_dpa on teardown, so ask.ko only
         * caches the resolved FQID (never destroys it). Direct-mapped by
         * ifindex % ASK_HW_NOCONF_SLOTS; a collision falls back to a fresh
         * alloc without caching (correctness over reuse; impossible with the
         * board's <=5 dpaa netdevs).
         */
        struct {
                u32     ifindex;
                u32     fqid;
        }               noconf_tx[ASK_HW_NOCONF_SLOTS];

};

static struct ask_hw_pcd *ask_hw_pcd_inst;

/* F-113: kmem_cache for struct ask_hw_flow_cookie — O(1) alloc/free
 * under heavy flow churn (50k+ active flows).  Replaces per-entry
 * kzalloc/kfree to reduce slab fragmentation and allocation pressure. */
static struct kmem_cache *ask_hw_cookie_cache;

/* ENQ FE MURAM offset, captured during engage for use in flow insert.
 * F-110: Write-once-read-many — WRITE_ONCE() on engage (under h->lock),
 * READ_ONCE() in flow_offload REPLACE path (lockless, hot path). */
static unsigned long ask_hw_enq_fe_off;

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

        magic = get_unaligned_be32(&blob[ASK_QEF_MAGIC_OFFSET]);

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

        /* Acquire pairs with the smp_store_release() below so a reader that
         * observes the valid flag is guaranteed to see the fully-published
         * ask_hw_cached struct (never a torn copy) on weakly-ordered arm64. */
        if (smp_load_acquire(&ask_hw_cached_valid)) {
                *out = ask_hw_cached;
                return 0;
        }

        rc = ask_hw_probe_ucode_locked(out);
        if (rc)
                return rc;

        ask_hw_cached = *out;
        /* Publish the struct before the valid flag (release barrier). */
        smp_store_release(&ask_hw_cached_valid, true);
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

        /* F-113: Use kmem_cache for O(1) alloc under flow churn.
         * Falls back to kzalloc if cache creation failed at bringup. */
        if (ask_hw_cookie_cache)
                entry = kmem_cache_zalloc(ask_hw_cookie_cache, GFP_KERNEL);
        else
                entry = kzalloc(sizeof(*entry), GFP_KERNEL);
        if (!entry)
                return 0;
        *entry = *src;

        rc = xa_alloc(&h->flow_cookies, &cookie, entry,
                      XA_LIMIT(1, U32_MAX), GFP_KERNEL);
        if (rc) {
                if (ask_hw_cookie_cache)
                        kmem_cache_free(ask_hw_cookie_cache, entry);
                else
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
        /* F-113: Use kmem_cache_free for O(1) dealloc. */
        if (ask_hw_cookie_cache)
                kmem_cache_free(ask_hw_cookie_cache, entry);
        else
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

        /* F-113: Create kmem_cache for flow cookie entries.
         * Falls back to kzalloc/kfree if cache creation fails (non-fatal). */
        ask_hw_cookie_cache = kmem_cache_create("ask_hw_flow_cookie",
                sizeof(struct ask_hw_flow_cookie),
                __alignof__(struct ask_hw_flow_cookie),
                0, NULL);
        if (!ask_hw_cookie_cache)
                ask_pr_warn("hw: kmem_cache_create failed — falling back to kzalloc\n");

        ask_hw_pcd_inst = h;

        /*
         * Balance the of_find_device_by_node() reference.  fman_bind()
         * took its own get_device(), which we deliberately hold for the
         * module lifetime (the FMan platform device is static on LS1046A)
         * to keep the cached struct fman * valid.
         */
        put_device(&pdev->dev);

        ask_pr_info("hw: board-substrate FMan handle bound; per-flow CC/HM offload available\n");
        /* P4.1: allocate a dedicated QMan TX FQ for hardware direct-enqueue.
         * All CC-tree flows use this FQ as their egress target, bypassing the
         * mainline dpaa_eth per-port TX FQ (whose taildrop limits sustained
         * throughput to ~1.5 Gbps). Falls back to dpaa_get_tx_fqid() on failure.
         *
         * The FQ is scheduled onto the FMan DC-portal channel 0x801 (MAC10/eth4
         * TX side) so QMan delivers hardware-enqueued frames direct-to-wire
         * without software portal involvement. Prior to 2026-07-08 the flag was
         * NO_ENQUEUE which silently dropped all frames (107K retransmits). */
        {
                u32 fqid;
                int rc = qman_alloc_fqid(&fqid);
                if (rc == 0) {
                        struct qman_fq *fq = &h->dedicated_fq;
                        struct qm_mcc_initfq initfq;
                        memset(fq, 0, sizeof(*fq));
                        fq->fqid = fqid;
                        rc = qman_create_fq(fqid, QMAN_FQ_FLAG_TO_DCPORTAL, fq);
                        if (rc == 0) {
                                memset(&initfq, 0, sizeof(initfq));
                                initfq.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL |
                                                             QM_INITFQ_WE_DESTWQ);
                                initfq.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE);
                                /* Channel 0x801 = FMan MAC10 (eth4) TX DC portal. */
                                qm_fqd_set_destwq(&initfq.fqd, 0x801, 0);
                                rc = qman_init_fq(fq, QMAN_INITFQ_FLAG_SCHED, &initfq);
                                if (rc == 0) {
                                        h->dedicated_fq_ready = true;
                                        ask_pr_info("hw: dedicated TX FQ 0x%x allocated ch=0x801\n",
                                                    fqid);
                                } else {
                                        qman_destroy_fq(fq);
                                        qman_release_fqid(fqid);
                                        ask_pr_warn("hw: qman_init_fq(0x%x) failed rc=%d\n",
                                                    fqid, rc);
                                }
                        } else {
                                qman_release_fqid(fqid);
                                ask_pr_warn("hw: qman_create_fq(0x%x) failed rc=%d\n",
                                            fqid, rc);
                        }
                } else {
                        ask_pr_warn("hw: qman_alloc_fqid failed rc=%d\n", rc);
                }
        }
        return 0;
}

/* F-109: debugfs_fe_write forward declaration removed — all callers
 * now use kernel API (fman_pcd_fe_disengage, fman_pcd_fe_flow_add). */
void ask_hw_pcd_teardown(void)
{
        struct ask_hw_pcd *h = ask_hw_pcd_inst;
        struct ask_hw_flow_cookie *ck;
        unsigned long idx;
        unsigned int i;

        if (!h)
                return;

        ask_hw_pcd_inst = NULL;
        /* P4.1: release dedicated TX FQ before draining flow cookies. */
        if (h->dedicated_fq_ready) {
                u32 fqid = h->dedicated_fq.fqid;
                qman_destroy_fq(&h->dedicated_fq);
                qman_release_fqid(fqid);
                h->dedicated_fq_ready = false;
                ask_pr_info("hw: dedicated TX FQ 0x%x released\n", fqid);
        }

        /*
         * Drain any flow cookies that survived to teardown.
         * CR-007: Fork-A HM refcount balancing removed — the FE-VM ehash
         * path (Fork-B) manages its own teardown via fman_pcd_fe_flow_del().
         */
        xa_for_each(&h->flow_cookies, idx, ck) {
                if (!ck)
                        continue;
                xa_erase(&h->flow_cookies, idx);
                /* F-113: Use kmem_cache_free for O(1) dealloc. */
                if (ask_hw_cookie_cache)
                        kmem_cache_free(ask_hw_cookie_cache, ck);
                else
                        kfree(ck);
        }

        /* 2026-08-21 LEAK FIX: global TX-confirm bypass removed (see engage);
         * no per-port TX register state to restore on teardown. */

        /* Disengage any port still in the M1 coarse S1 mode-switch (0129).
         * F-109: Use kernel API fman_pcd_fe_disengage() instead of
         * debugfs loopback (filp_open + kernel_write). */
        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].offload_engaged) {
                        fman_pcd_fe_disengage(h->fman, h->port[i].port_id);
                        h->port[i].offload_engaged = false;
                }
        }

        /* CR-007: Fork-A CC static tree teardown removed — cc_installed was
         * never set after C1 removed the Fork-A path. */

        xa_destroy(&h->flow_cookies);
        mutex_destroy(&h->lock);
        if (h->fman)
                put_device(fman_get_dev(h->fman));
        /* F-113: Destroy the flow-cookie kmem_cache. */
        if (ask_hw_cookie_cache) {
                kmem_cache_destroy(ask_hw_cookie_cache);
                ask_hw_cookie_cache = NULL;
        }
        kfree(h);
        ask_pr_dbg("hw: pcd teardown complete\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
        return ask_hw_pcd_inst;
}

/*
 * Fix B: expose the cached FMan handle (resolved via fman_bind() at bringup)
 * so ask_flow_offload.c can drive fman_pcd_fe_flow_add/_del against the real
 * FMan instead of passing NULL. Returns NULL before bringup / after teardown.
 */
struct fman *ask_hw_get_fman(void)
{
        return ask_hw_pcd_inst ? ask_hw_pcd_inst->fman : NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_get_fman);

/* T-M7-2 S1: recover the per-egress-interface TX FQID saved in the
 * ask_hw_flow_insert() cookie. The caller invokes this immediately after a
 * successful insert and before the cookie is published to any destroy path;
 * RCU still protects the xarray load against asynchronous teardown. */
int ask_hw_flow_get_sink_fqid(u32 hw_flow_id, u32 *fqid)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie *ck;

        if (!h || !fqid || !hw_flow_id)
                return -EINVAL;

        *fqid = 0;
        rcu_read_lock();
        ck = ask_hw_cookie_lookup(h, hw_flow_id);
        if (ck)
                *fqid = READ_ONCE(ck->sink_fqid);
        rcu_read_unlock();

        return *fqid ? 0 : -ENOENT;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_get_sink_fqid);

/* ------------------------------------------------------------------------- */
/* M1 coarse dataplane mode-switch (control-plane plumbing; ships dormant)    */
/* ------------------------------------------------------------------------- */

/* F-109: debugfs_fe_write(), debugfs_fe_read(), and parse_enq_offset()
 * DELETED.  All callers now use kernel API:
 *   - fman_pcd_fe_disengage() replaces debugfs_fe_write("fe_arm", ...)
 *   - fman_pcd_fe_enq_get_offset() replaces debugfs_fe_read("fe_enq", ...)
 *   - fman_pcd_fe_flow_add() replaces ask_debugfs_fe_flow_write()
 * Debugfs is for diagnostics only (Decision 10, 2026-07-19).
 */

/* Accessor for ENQ FE offset (used by flow_offload REPLACE handler).
 * F-110: READ_ONCE() — this is called lockless from the flow offload
 * hot path.  The writer (engage) holds h->lock and uses WRITE_ONCE(). */
unsigned long ask_hw_get_enq_fe_off(void)
{
        return READ_ONCE(ask_hw_enq_fe_off);
}
EXPORT_SYMBOL_GPL(ask_hw_get_enq_fe_off);

/* Defined further down with the per-flow fast path; forward-declared here. */
static struct ask_hw_port *ask_hw_port_slot_get(struct ask_hw_pcd *h,
						u8 port_id);

/*
 * Engage/disengage the coarse S0<->S1 PCD mode-switch on one FMan RX port.
 * These forward to the board-exported fman_pcd_offload_engage()/_disengage()
 * (board patch 0129) - the EXACT reversible KGSE_CCBS graft sequence proven by
 * the cc_test harness + 100x soak.  A per-port "engaged" flag makes both
 * idempotent so a double-engage / stray-disengage is a safe no-op, and
 * teardown reverts any port left engaged.  M1 carries no classification
 * semantics and is NOT wired to a traffic path - this is control-plane
 * plumbing only (the traffic-carrying engage is blocked on M2 AC_CC
 * disposition).  Triggered manually via /sys/kernel/debug/ask/offload; M7
 * routes `set system offload ask` here.
 */
int ask_hw_offload_engage(u8 hw_port_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_port *p;
        int rc;

        if (!h)
                return -ENODEV;

        mutex_lock(&h->lock);

        p = ask_hw_port_slot_get(h, hw_port_id);
        if (!p) {
                rc = -ENOSPC;
                goto out_unlock;
        }
        if (p->offload_engaged) {
                rc = 0;                 /* idempotent */
                goto out_unlock;
        }

	/*
	 * Family selection is now per-port and address-independent: userspace
	 * set this port's family mask (ASK_ATTR_FAMILY_MASK) via
	 * ask_hw_offload_set_family() before this engage. The unified 46-byte
	 * dual-lane scheme/table (F-224/F-225) is family-neutral and armed
	 * unconditionally below; ask_hw_flow_family_ok() enforces the mask per
	 * flow. The retired F-219 netdev-address auto-detect and the dead
	 * fman_pcd_fe_set_port_v6() intent bitmap (reader forced false by F-226)
	 * are gone — v6 now behaves exactly like v4.
	 */

	/* F-092: Build + arm FE-VM via kernel API (not debugfs).
         * fman_pcd_fe_engage() now builds the full VM chain via
         * __fman_pcd_fe_build_vm_chain(), creates the CONT_LOOKUP scaffold
         * with numKeys=1 (F-091), and arms the port for FE-VM dispatch.
         * This replaces the debugfs bridge — debugfs is for diagnostics only.
         *
         * F-157 (2026-08-01) historically passed h->dedicated_fq (a single
         * module-global QMan FQ pinned to channel 0x801 = eth4/MAC10 TX) as
         * the shared FE-VM ENQ-singleton target. That is WRONG for any ingress
         * port other than eth4: the shared ENQ singleton feeds the record's
         * next-FE for flows inserted WITHOUT a per-egress action.tx_fqid, so a
         * non-eth4 ingress flow would enqueue onto eth4's channel and the dpaa
         * driver drops the cross-port frame (E25/E26). Five-port readiness:
         * pass 0 so the shared ENQ singleton falls back to the generic 0x200
         * builder default (F-175), decoupling it from eth4. The production
         * routed HIT terminal is per-egress via action.tx_fqid (F-198/F-199)
         * and is unaffected; the CC MISS disposition is already per-ingress-port
         * via fman_pcd_resolve_miss_fqid() inside fman_pcd_fe_engage().
         */
        rc = fman_pcd_fe_engage(h->fman, hw_port_id, 0);
        if (rc == -EBUSY) {
                /*
                 * F-122/F-124: treat "already armed" as idempotent success.
                 * The FE state may have been left engaged while our software
                 * flag is false (e.g. after prior partial transitions). We
                 * still return success so userspace can issue disengage and
                 * converge hardware/software state deterministically.
                 */
                ask_pr_warn("hw: fman_pcd_fe_engage port 0x%02x already armed; treating as idempotent success\n",
                            hw_port_id);
                rc = 0;
                goto out_unlock;
        } else if (rc) {
                ask_pr_err("hw: fman_pcd_fe_engage port 0x%02x failed: %d\n",
                           hw_port_id, rc);
                goto out_unlock;
        }

        /* F-109: Capture ENQ FE offset via kernel API instead of
         * debugfs loopback (filp_open + kernel_read + sscanf).
         * F-110: WRITE_ONCE() — paired with READ_ONCE() in the
         * lockless flow_offload REPLACE hot path. */
        WRITE_ONCE(ask_hw_enq_fe_off, fman_pcd_fe_enq_get_offset(h->fman));
        ask_pr_info("hw: ENQ FE offset 0x%lx (kernel API)\n",
                    ask_hw_enq_fe_off);

        /*
         * Enable silicon HIT-release on all FMan TX ports so that
         * HIT frames bypass QMan and go directly to the wire.
         * F-108: Refcount-guarded — only enable hardware bypass on the
         * first engage (0→1 transition).  Subsequent engages on other
         * ports increment the refcount without touching hardware.
         * Reversed symmetrically in ask_hw_offload_disengage() so the
         * S1→S0 cycle restores the TX-confirm bit to its S0 default and
         * `pcd-snapshot diff` stays byte-clean (DUAL-DATAPLANE.md M1
         * reversibility contract).  Also reversed in ask_hw_pcd_teardown()
         * for module-unload cleanup as a belt-and-suspenders.
         */
        /*
         * 2026-08-21 LEAK FIX: the global TX-confirm bypass
         * (fman_port_set_silicon_hit_release_all) is REMOVED. It set
         * NIA_BMI_AC_TX_RELEASE on EVERY TX port, which suppressed the
         * TX-confirm FD for the kernel's confirmed egress_fqs[] too, so any
         * kernel-forwarded skb (software flowtable / plain forward) on those
         * ports was never freed -> ~1.7 GB/s skbuff_head leak -> OOM. No-
         * confirm behaviour is now provided PER-FRAME by the per-egress-port
         * no-confirm FQ (F-198/F-199, B0V=0/EBD=1) that the HIT record's
         * ENQUEUE_PKT targets via ask_hw_resolve_oif_fqid(); the resolver now
         * fails closed (SW forward) rather than using a confirmed FQ, so a
         * HIT frame never lands on a confirmed FQ. Nothing to arm here.
         */

        p->offload_engaged = true;
        ask_pr_info("hw: offload ENGAGED on port 0x%02x (S0->S1)\n", hw_port_id);

out_unlock:
        mutex_unlock(&h->lock);
        return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_offload_engage);

void ask_hw_offload_disengage(u8 hw_port_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_port *p;
        unsigned int i;

        if (!h)
                return;

        mutex_lock(&h->lock);

        p = NULL;
        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use && h->port[i].port_id == hw_port_id) {
                        p = &h->port[i];
                        break;
                }
        }
        if (!p) {
                mutex_unlock(&h->lock);
                return;                 /* idempotent no-op */
        }

        /* F-092: Disarm + tear down FE-VM via kernel API (not debugfs).
         * fman_pcd_fe_disengage() now tears down the VM chain after disarming.
         */
        fman_pcd_fe_disengage(h->fman, hw_port_id);

        /*
         * Reverse the FMan-global TX-confirm bypass set at engage so the
         * S1→S0 cycle restores the silicon HIT-release bit to its S0
         * default.  Mirrors the engage-side call at line ~598; keeps
         * `pcd-snapshot diff` byte-clean per the DUAL-DATAPLANE.md M1
         * reversibility contract.
         * 2026-08-21 LEAK FIX: global TX-confirm bypass removed (see engage);
         * nothing to clear here.
         */

	p->offload_engaged = false;
	mutex_unlock(&h->lock);

	/* Clear this port's family selection so a later flowtable callback
	 * cannot re-admit a family the operator disabled. Re-engage sets it
	 * again from the CLI mask. */
	ask_hw_offload_set_family(hw_port_id, 0);

	ask_pr_info("hw: offload DISENGAGED on port 0x%02x (S1->S0)\n", hw_port_id);
}
EXPORT_SYMBOL_GPL(ask_hw_offload_disengage);

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
        int rc;

        /*
         * A successful FLOW_CLS_REPLACE must arm the ingress FE pipeline
         * before it can add an ehash record.  fman_pcd_fe_engage() publishes
         * the per-port FE workspace and the shared ENQ FE offset consumed by
         * ask_fe_flow_insert().  The wrapper is idempotent for subsequent
         * flows on this port.
         */
        rc = ask_hw_offload_engage(port_id);
        if (rc)
                ask_pr_warn("hw: port-bind engage port 0x%02x dir=%u dev=%s failed: %d\n",
                            port_id, dir,
                            ingress_dev ? netdev_name(ingress_dev) : "?", rc);
        return rc;
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
        return &h->port[free_idx];
}

/*
 * T-M6-A4: non-allocating capacity probe. Returns true if @port_id already
 * has a slot OR a free slot exists, i.e. ask_hw_port_slot_get() would succeed.
 * Caller holds h->lock. Pure read — no side effects, so a preflight that
 * ultimately fails elsewhere does not consume a port slot.
 */
static bool ask_hw_port_slot_available(struct ask_hw_pcd *h, u8 port_id)
{
        unsigned int i;
        bool have_free = false;

        for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
                if (h->port[i].in_use) {
                        if (h->port[i].port_id == port_id)
                                return true;
                } else {
                        have_free = true;
                }
        }
        return have_free;
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
        struct ask_hw_pcd *h = ask_hw_pcd_inst;
        struct net_device *dev;
        unsigned int slot;
        int rc;

        /* T-M7-2 S4 (2026-08-15): resolve a PER-EGRESS-INTERFACE NO-CONFIRM
         * TX FQ for the FE hardware terminal. S1 used dpaa_get_tx_fqid(dev,0),
         * the netdev's shared queue-0 TX FQ whose FQD has B0V=1 -> FMan emits
         * a TX-confirm FD per frame -> per-CPU NAPI skb-free cost -> the
         * ~2.2 Gbps TCP ceiling. dpaa_alloc_offload_tx_fq() (F-199) returns a
         * dedicated FQ with B0V=0 (no confirmation) on the same channel; we
         * cache it per ifindex so each egress port allocates exactly once.
         * The FQ is owned by fsl_dpa (netdev dpaa_fq_list) — never destroyed
         * here. On any allocation failure we fall back to the confirmed
         * queue-0 TX FQ so forwarding still works (just slower). */
        dev = dev_get_by_index(&init_net, ifindex);
        if (!dev)
                return -ENODEV;

        slot = ifindex % ASK_HW_NOCONF_SLOTS;
        if (h && h->noconf_tx[slot].fqid &&
            h->noconf_tx[slot].ifindex == ifindex) {
                *fqid = h->noconf_tx[slot].fqid;
                dev_put(dev);
                return 0;
        }

        rc = dpaa_alloc_offload_tx_fq(dev, fqid);
        if (rc) {
                /*
                 * FAIL CLOSED (2026-08-21 leak fix): if we cannot get a
                 * NO-CONFIRM (B0V=0) TX FQ, we MUST NOT fall back to the
                 * confirmed queue-0 FQ. Since the global TX-confirm bypass
                 * (F-108) is removed, an FMan-HIT frame on a confirmed FQ
                 * would generate a TX-confirm FD that the kernel would
                 * confirm-process as a bogus skb. Returning an error here
                 * makes the caller keep the flow in SOFTWARE forwarding
                 * (whose skbs ARE freed by the normal TX confirm), instead
                 * of hardware-offloading onto a confirmed FQ. Correctness
                 * over speed; on this board (<=5 dpaa netdevs) the per-port
                 * no-confirm FQ alloc does not realistically fail.
                 */
                ask_pr_warn("hw: no-confirm TX FQ alloc failed on ifindex=%u (%d); NOT offloading (SW forward)\n",
                            ifindex, rc);
                dev_put(dev);
                return rc ? rc : -ENODEV;
        }

        if (h && h->noconf_tx[slot].fqid == 0) {
                h->noconf_tx[slot].ifindex = ifindex;
                h->noconf_tx[slot].fqid = *fqid;
        } else if (h && h->noconf_tx[slot].ifindex != ifindex) {
                ask_pr_warn("hw: no-confirm TX FQ cache collision slot=%u ifindex=%u (cached %u); not caching\n",
                            slot, ifindex, h->noconf_tx[slot].ifindex);
        }

        ask_pr_info("hw: no-confirm TX FQ 0x%x resolved for ifindex=%u\n",
                    *fqid, ifindex);
        dev_put(dev);
        return 0;
}

int ask_hw_flow_preflight(const struct ask_flow_key *key,
                          u32 oif, u32 action_flags,
                          enum ask_hw_dir dir)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        u32 tx_fqid = 0;
        u8  port_id = 0;
        int rc;

        (void)dir;

        if (!key)
                return -EINVAL;

        /* No HW backing -> caller keeps the flow in software. */
        if (!h)
                return -ENODEV;

        /*
         * T-M6-A4: resource-class gate. The plain IPv4/IPv6 unicast HW path
         * consumes exactly one DDR ehash record + one cookie + a pre-existing
         * per-egress TX FQ; it needs NO per-flow MURAM, policer, or CAAM slot.
         * Any action that WOULD require an unprovisioned resource class must
         * fail to software here, before publication, rather than partially
         * program silicon. NAT/PAT/VLAN are already rejected upstream by the
         * A2 strict action parser, but re-assert defensively: if action_flags
         * ever carries a class we cannot back, refuse.
         */
        if (action_flags & (ASK_ACT_NAT_SRC | ASK_ACT_NAT_DST | ASK_ACT_PAT |
                            ASK_ACT_VLAN_PUSH | ASK_ACT_VLAN_POP |
                            ASK_ACT_TO_CAAM | ASK_ACT_TO_OP))
                return -EOPNOTSUPP;

	/* Ingress hwport must resolve first (it owns the classifier AND its
	 * per-port family mask decides v4/v6 admission). Resolve BEFORE the
	 * family gate so admission is by TRUE INGRESS PORT, address-independent. */
	rc = ask_hw_resolve_iif_port(key->iif, &port_id);
	if (rc)
		return rc;

	/* Per-port family/proto gate: TCP/UDP, and the ingress port must have
	 * this L3 family selected (CLI offload ipv4 / offload ipv6). Otherwise
	 * fall back to software. */
	rc = ask_hw_flow_family_ok(port_id, key);
	if (rc)
		return rc;

	/* Egress L2 header not yet resolved -> defer (SW carries it). */
	if (is_zero_ether_addr(key->next_hop_mac) ||
	    is_zero_ether_addr(key->egress_mac))
		return -EAGAIN;

	/* Egress forward FQ must exist before we publish. */
	rc = ask_hw_resolve_oif_fqid(oif, &tx_fqid);
	if (rc)
		return rc;

        /* Port slot must be available (the CC/FE port context). Non-allocating
         * probe: this is the -ENOSPC condition the real insert would hit, but
         * without consuming a slot if the flow later fails to publish. */
        mutex_lock(&h->lock);
        rc = ask_hw_port_slot_available(h, port_id) ? 0 : -ENOSPC;
        mutex_unlock(&h->lock);

        return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_preflight);

int ask_hw_flow_insert(const struct ask_flow_key *key,
                       u32 oif, u32 action_flags,
                       enum ask_hw_dir dir,
                       u32 *out_hw_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie ck;
        struct ask_hw_port *p;
        u32 tx_fqid = 0;
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

	/* Ingress hwport owns the CC tree AND its per-port family mask; resolve
	 * first, then apply the same per-port family/proto gate as preflight. */
	rc = ask_hw_resolve_iif_port(key->iif, &port_id);
	if (rc)
		return rc;

	rc = ask_hw_flow_family_ok(port_id, key);
	if (rc)
		return rc;

	/*
	 * Neighbour not yet resolved: keep the flow in SW until the egress L2
	 * header is known, then the upper layer re-inserts.
	 */
	if (is_zero_ether_addr(key->next_hop_mac) ||
	    is_zero_ether_addr(key->egress_mac))
		return -EAGAIN;

	rc = ask_hw_resolve_oif_fqid(oif, &tx_fqid);
	if (rc)
		return rc;

        mutex_lock(&h->lock);

        p = ask_hw_port_slot_get(h, port_id);
        if (!p) {
                rc = -ENOSPC;
                goto out_unlock;
        }

        /* CR-007: Fork-A shadow/HM bookkeeping removed. The FE-VM ehash path
         * (Fork-B) manages its own keys via ask_fe_flow_insert() in
         * ask_flow_offload.c. This function now only snapshots the cookie
         * metadata for teardown (port_id, sink_ifindex, sink_fqid). */

        ck.fm           = h->fman;
        ck.port_id      = port_id;
        ck.sink_ifindex = (int)oif;
        ck.sink_fqid    = tx_fqid;

        cookie = ask_hw_cookie_alloc(h, &ck);
        if (!cookie) {
                rc = -ENOMEM;
                goto out_unlock;
        }

        mutex_unlock(&h->lock);
        *out_hw_id = cookie;
        ask_pr_dbg("hw: flow_insert: port=0x%02x fqid=%u cookie=0x%x\n",
                   port_id, tx_fqid, cookie);
        return 0;

out_unlock:
        mutex_unlock(&h->lock);
        return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_insert);

int ask_hw_flow_remove(u32 hw_flow_id)
{
        struct ask_hw_pcd *h = ask_hw_pcd_get();
        struct ask_hw_flow_cookie *ck;

        if (!h || hw_flow_id == 0)
                return 0;

        mutex_lock(&h->lock);

        ck = ask_hw_cookie_lookup(h, hw_flow_id);
        if (!ck) {
                /* Already torn down; treat as success (idempotent). */
                mutex_unlock(&h->lock);
                return 0;
        }

        /* CR-007: Fork-A shadow/HM teardown removed. The FE-VM ehash path
         * (Fork-B) manages its own flow deletion via ask_fe_flow_remove()
         * in ask_flow_offload.c. */

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