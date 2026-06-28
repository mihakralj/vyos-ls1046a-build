/*
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */

#include <linux/version.h>
#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <net/pkt_sched.h>
#include <linux/rcupdate.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/if_ether.h>
#include <linux/if_arp.h>
#include <linux/if_bridge.h>
#include <linux/if_vlan.h>
#include <linux/rtnetlink.h>
#include <linux/jhash.h>
#include <linux/netfilter.h>
#include <linux/netfilter_ipv4.h>
#include <net/ip.h>
#include <net/checksum.h>
#include <linux/netfilter_ipv6.h>
#include <linux/netfilter_bridge.h>
#include <linux/irqnr.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/ppp_defs.h>
#include <linux/highmem.h>
#include <linux/proc_fs.h>
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
#include <net/xfrm.h>
#endif
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/net_namespace.h>

#include <linux/spinlock.h>
#include <linux/fsl_bman.h>
#include <linux/fsl_qman.h>
#include "portdefs.h"
#include "dpa_ipsec.h"
#include "cdx_ioctl.h"
#include "cdx.h"
#include "misc.h"
#include "dpaa_eth_common.h"
#include "dpa_wifi.h"
#include "procfs.h"
#include "control_ipsec.h"

/*
* DPA_FQ_TD_BYTES is frame queue tail drop bytes mode  threshold value. This 
* threshold is per frame queue.
*/
#define DPA_FQ_TD_BYTES	316000000

#ifdef DPA_IPSEC_OFFLOAD 
//#define DPA_IPSEC_DEBUG  	1
//#define DPA_IPSEC_TEST_ENABLE	1

#define DPAIPSEC_ERROR(fmt, ...)\
{\
        printk(KERN_CRIT fmt, ## __VA_ARGS__);\
}
#ifdef CDX_DPA_DEBUG
#define DPAIPSEC_INFO(fmt, ...)\
{\
        printk(KERN_INFO fmt, ## __VA_ARGS__);\
}
#else
#define DPAIPSEC_INFO(fmt, ...)
#endif

#define MAX_IPSEC_SA_INFO	16
#define IPSEC_WQ_ID		2

/*
* FQ_TAIL_DROP support for the tail drop support per frame queue base.
* It means based on the on the threshold(default is in bytes mode, DPA_FQ_TD_BYTES)
* value it drops the packet per frame queue. Basically this support framework is
* added only for "to sec ipsec" frame queues only. Now it is disabled, as CS_TAIL_DROP
* support is enabled and that is sufficient. To enable FQ_TAIL_DROP support uncomment
* below macro.
*/
//#define FQ_TAIL_DROP

/*
* CS_TAIL_DROP support for the tail drop support is per congestion group record.
* Each congestion group record can have multiple frame queues can group together.
* In our case all "to sec ipsec" frame queues are grouped into one congestion group.
* This threshold works on group all frame queues bytes at that moment. This also
* by default in bytes mode. It checks thresold with CDX_DPAA_INGRESS_CS_TD.
*/
#define CS_TAIL_DROP
#ifdef CS_TAIL_DROP
struct cgr_priv {
/*	bool use_ingress_cgr;*/
	struct qman_cgr ingress_cgr;
};
/* The following macro is used as default value before introducing module param */
#define CDX_DPAA_INGRESS_CS_TD	4000/*DPA_FQ_TD_BYTES*/ /*316000000*/

#define SEC_CONGESTION_DISABLE	0

unsigned int sec_congestion = SEC_CONGESTION_DISABLE;
module_param(sec_congestion, uint, S_IRUGO);
MODULE_PARM_DESC(sec_congestion, "0: congestion disable n: congestion threshold");

#endif

struct dpa_ipsec_sainfo {
	void *shdesc_mem;
	struct sec_descriptor *shared_desc;
	dma_addr_t shared_desc_dma;
	struct dpa_fq sec_fq[NUM_FQS_PER_SA];
	struct dpa_fq compat_tx_fq;
	uint32_t compat_tx_channel;
	uint32_t compat_tx_wq;
	struct net_device *compat_tx_dev;
	uint16_t peer_sa_handle;
	uint32_t fromsec_last_status;
	atomic64_t fromsec_status_transition_cnt;
	atomic64_t fromsec_bad_50000008_cnt;
	atomic64_t fromsec_bad_50000008_runlen;
	uint8_t compat_l2_hdr[96];
	uint8_t compat_l2_len;
	uint8_t compat_l2_pppoe_off;
	struct net_device *compat_lan_dev;
	uint16_t compat_mtu;
	void *sa_proc_entry;
};

struct ipsec_info {
	uint32_t crypto_channel_id;
	int ofport_handle;
	uint32_t ofport_channel;
	uint32_t ofport_portid;
	void *ofport_td[MAX_MATCH_TABLES];
	uint32_t expt_fq_count ;
	struct dpa_bp *ipsec_bp;
	struct dpa_fq *pcd_fq;
	struct dpa_fq ofport_reinject_fq;
	uint8_t ofport_reinject_fq_valid;
	struct dpa_fq		*ipsec_exception_fq;
	struct port_bman_pool_info parent_pool_info;
#ifdef CS_TAIL_DROP
	struct cgr_priv	cgr;
#endif
};

static struct ipsec_info ipsecinfo;
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
extern struct xfrm_state *xfrm_state_lookup_byhandle(struct net *net, u16 handle);
#endif
extern struct device *jrdev_g;
extern int dpaa_ipsec_release_compound_reclaim_ctx(const struct qm_fd *fd);
extern int cdx_ipsec_handle_get_inbound_sagd(U32 spi, U16 *sagd);

/* Forward declarations for internal functions */
static int cdx_find_ipsec_pcd_fqinfo(int fqid, struct ipsec_info *info);
static void ipsec_addfq_to_exceptionfq_list(struct dpa_fq *frameq,
		struct ipsec_info *info);
static void ipsec_delfq_from_exceptionfq_list(uint32_t fqid,
		struct ipsec_info *info);
static enum qman_cb_dqrr_result ipsec_exception_pkt_handler(
		struct qman_portal *qm, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq);
static void dpa_ipsec_ern_cb(struct qman_portal *qm, struct qman_fq *fq,
		const struct qm_mr_entry *msg);

struct dpa_bp* get_ipsec_bp(void)
{
	return (ipsecinfo.ipsec_bp);
}
struct sec_descriptor *get_shared_desc(void *handle)
{
	return (((struct dpa_ipsec_sainfo *)handle)->shared_desc);
}

dma_addr_t get_shared_desc_dma(void *handle)
{
	return (((struct dpa_ipsec_sainfo *)handle)->shared_desc_dma);
}

int cdx_ipsec_sync_shared_desc_for_device(void *handle)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = handle;

	if (!ipsecsa_info || !jrdev_g)
		return -EINVAL;

	dma_sync_single_for_device(jrdev_g, ipsecsa_info->shared_desc_dma,
			sizeof(struct sec_descriptor), DMA_BIDIRECTIONAL);

	return 0;
}

uint32_t get_fqid_to_sec(void *handle)
{
	return (((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_TO_SEC].fqid);
}

uint32_t get_fqid_from_sec(void *handle)
{
	return (((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_FROM_SEC].fqid);
}
struct qman_fq *get_from_sec_fq(void *handle)
{
	return (struct qman_fq *)&(((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_FROM_SEC]);
} 
struct qman_fq *get_to_sec_fq(void *handle)
{
	return (struct qman_fq *)&(((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_TO_SEC]);
} 

#ifdef UNIQUE_IPSEC_CP_FQID
uint32_t ipsec_get_to_cp_fqid(void *handle)
{
	return (((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_TO_CP].fqid);
}

struct qman_fq *get_to_cp_fq(void *handle)
{
	return (struct qman_fq *)&(((struct dpa_ipsec_sainfo *)handle)->sec_fq[FQ_TO_CP]);
}
#endif

/*
 * Focus debug on one SA handle by default (22089). Set to 0 to log all.
 */
static unsigned int ipsec_dbg_sa_handle = 22089;
module_param(ipsec_dbg_sa_handle, uint, 0644);
MODULE_PARM_DESC(ipsec_dbg_sa_handle,
	"SA handle filter for IPSEC SEC return-path correlation (0=all)");

/* Copy exception-return packets into host skbs and release SEC FDs early.
 * Default off: this path increased RTT jitter/latency in live soak tests. */
static bool ipsec_excp_copy_skb = false;
module_param(ipsec_excp_copy_skb, bool, 0644);
MODULE_PARM_DESC(ipsec_excp_copy_skb,
	"Copy SEC exception returns into host skb and release SEC FD early (default off)");

/* Runtime snapshots are debug-only; keep disabled in production. */
static bool ipsec_nofq_snapshot_enable = false;
module_param(ipsec_nofq_snapshot_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_nofq_snapshot_enable,
	"Enable periodic runtime snapshot on no-compat-FQ direct-return fallback");

static unsigned int ipsec_nofq_snapshot_period = 64;
module_param(ipsec_nofq_snapshot_period, uint, 0644);
MODULE_PARM_DESC(ipsec_nofq_snapshot_period,
	"Emit one snapshot every N no-compat-FQ fallback events (default 64)");

/* Native OH-port reinjection policy:
 * 0 = disabled
 * 1 = enabled for no-compat-TX return-shapes (default)
 */
static unsigned int ipsec_compat_reinject_mode = 1;
module_param(ipsec_compat_reinject_mode, uint, 0644);
MODULE_PARM_DESC(ipsec_compat_reinject_mode,
	"0=disabled, 1=enable native OH-port reinjection for no-compat-TX returns (default)");

static bool ipsec_compat_learning_punt = true;
module_param(ipsec_compat_learning_punt, bool, 0644);
MODULE_PARM_DESC(ipsec_compat_learning_punt,
	"Punt first inbound decrypted TCP/UDP packet per inner flow to Linux for conntrack learning");

static bool ipsec_compat_txdev_prefer = true;
module_param(ipsec_compat_txdev_prefer, bool, 0644);
MODULE_PARM_DESC(ipsec_compat_txdev_prefer,
	"Prefer normal DPAA netdev TX for post-SEC compat frames when an SA TX device is known");

static bool ipsec_reinject_snapshot_enable = false;
module_param(ipsec_reinject_snapshot_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_reinject_snapshot_enable,
	"Enable periodic reinjection decision snapshots");

static unsigned int ipsec_reinject_snapshot_period = 64;
module_param(ipsec_reinject_snapshot_period, uint, 0644);
MODULE_PARM_DESC(ipsec_reinject_snapshot_period,
	"Emit one reinjection snapshot every N attempts");

static bool ipsec_signal_snapshot_enable = false;
module_param(ipsec_signal_snapshot_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_signal_snapshot_enable,
	"Enable periodic aggregate IPsec signal snapshots");

static unsigned int ipsec_signal_snapshot_period = 256;
module_param(ipsec_signal_snapshot_period, uint, 0644);
MODULE_PARM_DESC(ipsec_signal_snapshot_period,
	"Emit one aggregate signal snapshot every N trigger events");

static bool ipsec_fromsec_snapshot_enable = false;
module_param(ipsec_fromsec_snapshot_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_fromsec_snapshot_enable,
	"Enable periodic from-SEC entry snapshots with FQ runtime");

static unsigned int ipsec_fromsec_snapshot_period = 128;
module_param(ipsec_fromsec_snapshot_period, uint, 0644);
MODULE_PARM_DESC(ipsec_fromsec_snapshot_period,
	"Emit one from-SEC snapshot every N from-SEC entries");

static atomic64_t ipsec_nofq_fallback_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_nofq_snapshot_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_compat_posttx_tag_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_attempt_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_ok_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_err_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_ern_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_skipped_noncontig_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_skipped_missing_fq_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_fallback_excp_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_reinject_fallback_drop_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_signal_snapshot_seq = ATOMIC64_INIT(0);
static atomic64_t ipsec_excp_status_total_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_excp_status_nonzero_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_excp_status_bucket_cnt[16];
static atomic64_t ipsec_reinject_status_bucket_cnt[16];
static atomic64_t ipsec_from_sec_entry_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_from_sec_format_cnt[4];
static atomic64_t ipsec_learning_punt_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_learning_seen_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_learning_skip_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_from_sec_status_bucket_cnt[16];

enum ipsec_bp_site {
	IPSEC_BP_SGLIST,
	IPSEC_BP_PAYLOAD,
	IPSEC_BP_REBUILD,
	IPSEC_BP_SITE_MAX,
};

static atomic64_t ipsec_bp_alloc_ok_cnt[IPSEC_BP_SITE_MAX];
static atomic64_t ipsec_bp_alloc_enobufs_cnt[IPSEC_BP_SITE_MAX];
static atomic64_t ipsec_bp_alloc_fail_cnt[IPSEC_BP_SITE_MAX];
static atomic64_t ipsec_bp_release_sg_payload_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_release_rebuild_old_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_release_compat_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_release_ern_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_txdev_enqueue_ok_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_compat_enqueue_ok_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_bp_enqueue_fail_cnt = ATOMIC64_INIT(0);

/* Keep success-path diagnostics off by default to avoid dmesg overhead. */
static bool ipsec_compat_diag_enable = false;
module_param(ipsec_compat_diag_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_compat_diag_enable,
	"Enable compat success-path diagnostics (posttx-tag/tx-enqueue-ok)");

/* Keep lan-neigh fallback info logs off by default (high-frequency). */
static bool ipsec_lan_neigh_diag_enable = false;
module_param(ipsec_lan_neigh_diag_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_lan_neigh_diag_enable,
	"Enable LAN neighbour fallback/miss info logs");

#ifdef CS_TAIL_DROP
static bool ipsec_cgr_snapshot_enable = false;
module_param(ipsec_cgr_snapshot_enable, bool, 0644);
MODULE_PARM_DESC(ipsec_cgr_snapshot_enable,
	"Enable periodic IPSEC CGR (congestion) snapshots");

static unsigned int ipsec_cgr_snapshot_period = 256;
module_param(ipsec_cgr_snapshot_period, uint, 0644);
MODULE_PARM_DESC(ipsec_cgr_snapshot_period,
	"Emit one CGR snapshot every N congestion transitions");

static atomic64_t ipsec_cgr_enter_cnt = ATOMIC64_INIT(0);
static atomic64_t ipsec_cgr_exit_cnt = ATOMIC64_INIT(0);
#endif

#define IPSEC_DEBUG_HELPERS 0

static inline bool ipsec_compat_diag_should_log(u64 v)
{
	return (v <= 8) || ((v & 0x3fULL) == 0);
}

static inline bool ipsec_dbg_sa_match(uint16_t sa)
{
	if (!ipsec_dbg_sa_handle)
		return true;

	return ((uint16_t)ipsec_dbg_sa_handle == sa);
}

#define IPSEC_ST_ERR_UNSUPPORTED_FORMAT	0x04000000
#define IPSEC_ST_ERR_LENGTH		0x02000000
#define IPSEC_ST_ERR_DMA			0x01000000
#define IPSEC_ST_ERR_NON_FM		0x00400000
#define IPSEC_COMPAT_MAX_SG_ENTRIES	64

static void ipsec_dbg_log_status(const char *stage, uint16_t sa, uint32_t st)
{
	if (!IPSEC_DEBUG_HELPERS)
		return;

	if (!ipsec_dbg_sa_match(sa))
		return;

	printk_ratelimited(KERN_INFO
		"IPSEC_ST: stage=%s sa=%u st=0x%08x nonfm=%u dma=%u len=%u unsup=%u\n",
		stage, sa, st,
		!!(st & IPSEC_ST_ERR_NON_FM),
		!!(st & IPSEC_ST_ERR_DMA),
		!!(st & IPSEC_ST_ERR_LENGTH),
		!!(st & IPSEC_ST_ERR_UNSUPPORTED_FORMAT));
}

static void ipsec_dbg_log_pkt_identity(const char *stage, uint32_t fqid,
		uint32_t fd_status, const uint8_t *pkt, uint32_t len,
		uint16_t sa_hint)
{
	if (!IPSEC_DEBUG_HELPERS)
		return;

	uint16_t sa = sa_hint;
	uint16_t eth;
	uint32_t l3 = ETH_HLEN;
	uint8_t ipver = 0;
	uint8_t proto = 0;
	uint16_t ipid = 0;
	uint16_t icmp_seq = 0;
	uint32_t esp_spi = 0;
	uint32_t esp_seq = 0;
	uint16_t ppp_proto = 0;
	uint8_t ihl;
	bool have_icmp_seq = false;
	bool have_esp = false;

	if (!pkt || (len < 2))
		return;

	if (!sa)
		memcpy(&sa, pkt + len - 2, sizeof(sa));

	if (!ipsec_dbg_sa_match(sa))
		return;

	if (len < ETH_HLEN) {
		printk_ratelimited(KERN_INFO
			"IPSEC_RET: stage=%s sa=%u fqid=0x%x short-l2 len=%u st=0x%08x\n",
			stage, sa, fqid, len, fd_status);
		return;
	}

	eth = ((uint16_t)pkt[12] << 8) | pkt[13];

	while ((eth == 0x8100 || eth == 0x88a8) && (len >= (l3 + 4))) {
		eth = ((uint16_t)pkt[l3 + 2] << 8) | pkt[l3 + 3];
		l3 += 4;
	}

	if ((eth == 0x8864) && (len >= (l3 + 8))) {
		ppp_proto = ((uint16_t)pkt[l3 + 6] << 8) | pkt[l3 + 7];
		l3 += 8;
		if (ppp_proto == 0x0021)
			eth = 0x0800;
		else if (ppp_proto == 0x0057)
			eth = 0x86dd;
	}

	if ((eth == 0x0800) && (len >= (l3 + 20))) {
		ipver = pkt[l3] >> 4;
		ihl = (pkt[l3] & 0x0f) << 2;
		if ((ihl >= 20) && (len >= (l3 + ihl))) {
			proto = pkt[l3 + 9];
			ipid = ((uint16_t)pkt[l3 + 4] << 8) | pkt[l3 + 5];
			if ((proto == IPPROTO_ICMP) && (len >= (l3 + ihl + 8))) {
				icmp_seq = ((uint16_t)pkt[l3 + ihl + 6] << 8) |
						pkt[l3 + ihl + 7];
				have_icmp_seq = true;
			} else if ((proto == IPPROTO_ESP) && (len >= (l3 + ihl + 8))) {
				esp_spi = ((uint32_t)pkt[l3 + ihl] << 24) |
					((uint32_t)pkt[l3 + ihl + 1] << 16) |
					((uint32_t)pkt[l3 + ihl + 2] << 8) |
					pkt[l3 + ihl + 3];
				esp_seq = ((uint32_t)pkt[l3 + ihl + 4] << 24) |
					((uint32_t)pkt[l3 + ihl + 5] << 16) |
					((uint32_t)pkt[l3 + ihl + 6] << 8) |
					pkt[l3 + ihl + 7];
				have_esp = true;
			}
		}
	}

	printk_ratelimited(KERN_INFO
		"IPSEC_RET: stage=%s sa=%u fqid=0x%x st=0x%08x len=%u l3=%u eth=0x%04x ppp=0x%04x ipver=%u proto=%u id=0x%04x icmp_seq=%u esp_spi=0x%08x esp_seq=%u flags[i=%u e=%u]\n",
		stage, sa, fqid, fd_status, len, l3, eth, ppp_proto, ipver, proto,
		ipid, icmp_seq, esp_spi, esp_seq, have_icmp_seq, have_esp);
}

static int ipsec_extract_esp_spi_from_ip(const uint8_t *ip, uint32_t len,
		uint32_t *spi)
{
	uint8_t ipver;
	uint8_t ihl;

	if (!ip || !spi || len < 8)
		return -EINVAL;

	ipver = ip[0] >> 4;
	if (ipver == 4) {
		if (len < 20)
			return -EINVAL;
		ihl = (ip[0] & 0x0f) << 2;
		if (ihl < 20 || len < (uint32_t)(ihl + 8) || ip[9] != 50)
			return -EINVAL;

		*spi = ((uint32_t)ip[ihl] << 24) |
			((uint32_t)ip[ihl + 1] << 16) |
			((uint32_t)ip[ihl + 2] << 8) |
			(uint32_t)ip[ihl + 3];
		return 0;
	}

	if (ipver == 6) {
		if (len < 48 || ip[6] != 50)
			return -EINVAL;

		*spi = ((uint32_t)ip[40] << 24) |
			((uint32_t)ip[41] << 16) |
			((uint32_t)ip[42] << 8) |
			(uint32_t)ip[43];
		return 0;
	}

	return -EINVAL;
}

static int ipsec_extract_esp_spi_scan(const uint8_t *pkt, uint32_t len,
		uint32_t *spi, uint32_t *hit_off)
{
	uint32_t off;
	uint32_t max_scan;

	if (!pkt || !spi || !len)
		return -EINVAL;

	max_scan = (len > 64) ? 64 : len;
	for (off = 0; off < max_scan; off++) {
		if (ipsec_extract_esp_spi_from_ip(pkt + off, len - off, spi) == 0) {
			if (hit_off)
				*hit_off = off;
			return 0;
		}
	}

	return -EINVAL;
}

/* Parse outer ESP SPI from return frame:
 * - ETH(+VLAN)+[PPPoE+PPP]+IP
 * - raw IP (no L2)
 * - PPP protocol (0x0021/0x0057) + IP
 */
static int ipsec_extract_esp_spi_from_l2(const uint8_t *pkt, uint32_t len,
		uint32_t *spi)
{
	uint32_t l3 = ETH_HLEN;
	uint16_t eth, ppp_proto = 0;

	if (!pkt || !spi || !len)
		return -EINVAL;

	/* First try ETH(+VLAN/PPPoE/PPP) shape. */
	if (len >= ETH_HLEN) {
		eth = ((uint16_t)pkt[12] << 8) | pkt[13];

		while ((eth == ETH_P_8021Q || eth == ETH_P_8021AD) &&
		       len >= (l3 + 4)) {
			eth = ((uint16_t)pkt[l3 + 2] << 8) | pkt[l3 + 3];
			l3 += 4;
		}

		if (eth == ETH_P_PPP_SES && len >= (l3 + 8)) {
			ppp_proto = ((uint16_t)pkt[l3 + 6] << 8) | pkt[l3 + 7];
			l3 += 8;
			if (ppp_proto == 0x0021)
				eth = ETH_P_IP;
			else if (ppp_proto == 0x0057)
				eth = ETH_P_IPV6;
		}

		if ((eth == ETH_P_IP || eth == ETH_P_IPV6) && len > l3) {
			if (ipsec_extract_esp_spi_from_ip(pkt + l3, len - l3,
						spi) == 0)
				return 0;
		}
	}

	/* Fallback: direct L3 return (no Ethernet header). */
	if (ipsec_extract_esp_spi_from_ip(pkt, len, spi) == 0)
		return 0;

	/* Fallback: PPP protocol + IP payload (no PPPoE shim). */
	if (len > 2 &&
	    (((uint16_t)pkt[0] << 8) | pkt[1]) == 0x0021 &&
	    ipsec_extract_esp_spi_from_ip(pkt + 2, len - 2, spi) == 0)
		return 0;
	if (len > 2 &&
	    (((uint16_t)pkt[0] << 8) | pkt[1]) == 0x0057 &&
	    ipsec_extract_esp_spi_from_ip(pkt + 2, len - 2, spi) == 0)
		return 0;

	/* Final fallback: scan a bounded prefix for IP{v4,v6}/ESP header. */
	{
		uint32_t hit_off = 0;
		if (ipsec_extract_esp_spi_scan(pkt, len, spi, &hit_off) == 0) {
			return 0;
		}
	}

	return -EINVAL;
}

static void ipsec_dbg_log_hex_sample(const char *stage, uint32_t fqid,
		const uint8_t *pkt, uint32_t len, uint16_t sa_hint)
{
	if (!IPSEC_DEBUG_HELPERS)
		return;

	uint16_t sa = sa_hint;
	uint32_t dump_len;

	if (!pkt || !len)
		return;

	if (!sa && len >= 2)
		memcpy(&sa, pkt + len - 2, sizeof(sa));

	if (!ipsec_dbg_sa_match(sa))
		return;

	dump_len = (len > 64) ? 64 : len;
	print_hex_dump(KERN_INFO, "IPSEC_HEX: ", DUMP_PREFIX_OFFSET, 16, 1,
			pkt, dump_len, false);
	printk_ratelimited(KERN_INFO
		"IPSEC_HEX: stage=%s sa=%u fqid=0x%x sample_len=%u total_len=%u\n",
		stage, sa, fqid, dump_len, len);
}

static void ipsec_dbg_log_fq_runtime(const char *stage, struct qman_fq *fq,
		uint16_t sa_hint)
{
	if (!IPSEC_DEBUG_HELPERS)
		return;

	struct qm_fqd fqd;
	struct qm_mcr_queryfq_np np;
	int qret;

	if (!fq || !ipsec_dbg_sa_match(sa_hint))
		return;

	memset(&fqd, 0, sizeof(fqd));
	memset(&np, 0, sizeof(np));

	qret = qman_query_fq(fq, &fqd);
	if (qret) {
		printk_ratelimited(KERN_INFO
			"IPSEC_FQRT: stage=%s fqid=0x%x query_fq_err=%d\n",
			stage, fq->fqid, qret);
		return;
	}

	qret = qman_query_fq_np(fq, &np);
	if (qret) {
		printk_ratelimited(KERN_INFO
			"IPSEC_FQRT: stage=%s fqid=0x%x query_np_err=%d\n",
			stage, fq->fqid, qret);
		return;
	}

	printk_ratelimited(KERN_INFO
		"IPSEC_FQRT: stage=%s sa=%u fqid=0x%x dest_ch=0x%x wq=%u fq_ctrl=0x%x cgid=%u ctxa_hi=0x%x ctxa_lo=0x%x ctxb=0x%x state=%u frm_cnt=%u byte_cnt=%u\n",
		stage, sa_hint, fq->fqid, fqd.dest.channel, fqd.dest.wq,
		fqd.fq_ctrl, fqd.cgid, fqd.context_a.hi, fqd.context_a.lo,
		fqd.context_b, np.state, np.frm_cnt, np.byte_cnt);
}

static void ipsec_log_fq_runtime_nofilter(const char *stage, struct qman_fq *fq)
{
	struct qm_fqd fqd;
	struct qm_mcr_queryfq_np np;
	int qret;

	if (!fq)
		return;

	memset(&fqd, 0, sizeof(fqd));
	memset(&np, 0, sizeof(np));

	qret = qman_query_fq(fq, &fqd);
	if (qret) {
		printk_ratelimited(KERN_INFO
			"IPSEC_SNAP_FQ: stage=%s fqid=0x%x query_fq_err=%d\n",
			stage, fq->fqid, qret);
		return;
	}

	qret = qman_query_fq_np(fq, &np);
	if (qret) {
		printk_ratelimited(KERN_INFO
			"IPSEC_SNAP_FQ: stage=%s fqid=0x%x query_np_err=%d\n",
			stage, fq->fqid, qret);
		return;
	}

	printk_ratelimited(KERN_INFO
		"IPSEC_SNAP_FQ: stage=%s fqid=0x%x dest_ch=0x%x wq=%u fq_ctrl=0x%x cgid=%u state=%u frm_cnt=%u byte_cnt=%u\n",
		stage, fq->fqid, fqd.dest.channel, fqd.dest.wq,
		fqd.fq_ctrl, fqd.cgid, np.state, np.frm_cnt, np.byte_cnt);
}

enum ipsec_nofq_remap_reason {
	IPSEC_NOFQ_REMAP_OK = 0,
	IPSEC_NOFQ_REMAP_INPUT_INVALID = 1,
	IPSEC_NOFQ_REMAP_PKT_UNMAPPED = 2,
	IPSEC_NOFQ_REMAP_CACHED_PEER_INVALID = 3,
	IPSEC_NOFQ_REMAP_ESP_SAGD_UNRESOLVED = 4,
	IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED = 5,
	IPSEC_NOFQ_REMAP_SECONDARY_NO_PEER = 6,
	IPSEC_NOFQ_REMAP_PEER_L2_UNSUITABLE = 7,
	IPSEC_NOFQ_REMAP_MAX
};

static atomic64_t ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_MAX];

static inline uint8_t ipsec_status_bucket(uint32_t st)
{
	return (uint8_t)((st >> 28) & 0xf);
}

static bool ipsec_fd_from_ipsec_bp(const struct qm_fd *fd)
{
	return fd && ipsecinfo.ipsec_bp &&
		fd->bpid == ipsecinfo.ipsec_bp->bpid;
}

static void ipsec_bp_note_alloc(unsigned int site, int err)
{
	if (site >= IPSEC_BP_SITE_MAX)
		return;

	if (!err)
		atomic64_inc(&ipsec_bp_alloc_ok_cnt[site]);
	else if (err == -ENOBUFS)
		atomic64_inc(&ipsec_bp_alloc_enobufs_cnt[site]);
	else
		atomic64_inc(&ipsec_bp_alloc_fail_cnt[site]);
}

static void ipsec_bp_release_fd(struct qm_fd *fd, atomic64_t *counter)
{
	if (ipsec_fd_from_ipsec_bp(fd) && counter)
		atomic64_inc(counter);

	dpa_fd_release(NULL, fd);
}

static void ipsec_log_bp_snapshot(const char *stage)
{
	printk_ratelimited(KERN_INFO
		"IPSEC_BP_SNAP: stage=%s alloc_ok[sg=%llu payload=%llu rebuild=%llu] enobufs[sg=%llu payload=%llu rebuild=%llu] fail[sg=%llu payload=%llu rebuild=%llu] release[sg_payload=%llu rebuild_old=%llu compat=%llu ern=%llu] enqueue[txdev_ok=%llu compat_ok=%llu fail=%llu]\n",
		stage ? stage : "unknown",
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_ok_cnt[IPSEC_BP_SGLIST]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_ok_cnt[IPSEC_BP_PAYLOAD]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_ok_cnt[IPSEC_BP_REBUILD]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_enobufs_cnt[IPSEC_BP_SGLIST]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_enobufs_cnt[IPSEC_BP_PAYLOAD]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_enobufs_cnt[IPSEC_BP_REBUILD]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_fail_cnt[IPSEC_BP_SGLIST]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_fail_cnt[IPSEC_BP_PAYLOAD]),
		(unsigned long long)atomic64_read(&ipsec_bp_alloc_fail_cnt[IPSEC_BP_REBUILD]),
		(unsigned long long)atomic64_read(&ipsec_bp_release_sg_payload_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_release_rebuild_old_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_release_compat_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_release_ern_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_txdev_enqueue_ok_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_compat_enqueue_ok_cnt),
		(unsigned long long)atomic64_read(&ipsec_bp_enqueue_fail_cnt));
}

static void ipsec_note_nofq_remap_reason(uint8_t remap_reason)
{
	if (remap_reason >= IPSEC_NOFQ_REMAP_MAX)
		return;
	atomic64_inc(&ipsec_nofq_remap_reason_cnt[remap_reason]);
}

static void ipsec_log_signal_snapshot(const char *trigger)
{
	uint64_t total;
	uint64_t seq;

	if (!ipsec_signal_snapshot_enable || !ipsec_signal_snapshot_period)
		return;

	total = atomic64_read(&ipsec_nofq_fallback_cnt) +
		atomic64_read(&ipsec_reinject_attempt_cnt) +
		atomic64_read(&ipsec_excp_status_total_cnt);
	if (total != 1 && (total % ipsec_signal_snapshot_period))
		return;

	seq = atomic64_inc_return(&ipsec_signal_snapshot_seq);
	printk_ratelimited(KERN_INFO
		"IPSEC_SIGNAL_SNAP: seq=%llu trigger=%s nofq=%llu remap[ok=%llu in=%llu unm=%llu cache=%llu esp=%llu peer=%llu sec=%llu l2=%llu] reinject[att=%llu ok=%llu err=%llu ern=%llu] excp[total=%llu nonzero=%llu b0=%llu b1=%llu b2=%llu b3=%llu b4=%llu b5=%llu b6=%llu b7=%llu b8=%llu b9=%llu ba=%llu bb=%llu bc=%llu bd=%llu be=%llu bf=%llu] reinj_st[b0=%llu b1=%llu b2=%llu b3=%llu b4=%llu b5=%llu b6=%llu b7=%llu b8=%llu b9=%llu ba=%llu bb=%llu bc=%llu bd=%llu be=%llu bf=%llu]\n",
		(unsigned long long)seq,
		trigger ? trigger : "unknown",
		(unsigned long long)atomic64_read(&ipsec_nofq_fallback_cnt),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_OK]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_INPUT_INVALID]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PKT_UNMAPPED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_CACHED_PEER_INVALID]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_ESP_SAGD_UNRESOLVED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_SECONDARY_NO_PEER]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PEER_L2_UNSUITABLE]),
		(unsigned long long)atomic64_read(&ipsec_reinject_attempt_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_ok_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_err_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_ern_cnt),
		(unsigned long long)atomic64_read(&ipsec_excp_status_total_cnt),
		(unsigned long long)atomic64_read(&ipsec_excp_status_nonzero_cnt),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[0]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[1]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[2]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[3]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[4]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[5]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[6]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[7]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[8]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[9]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[10]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[11]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[12]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[13]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[14]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_bucket_cnt[15]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[0]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[1]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[2]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[3]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[4]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[5]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[6]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[7]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[8]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[9]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[10]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[11]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[12]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[13]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[14]),
		(unsigned long long)atomic64_read(&ipsec_reinject_status_bucket_cnt[15]));
}

static void ipsec_log_from_sec_snapshot(struct dpa_ipsec_sainfo *ipsecsa_info,
		const struct qman_fq *from_sec_fq,
		const struct qm_dqrr_entry *dq)
{
	u64 ent;

	if (!ipsec_fromsec_snapshot_enable || !ipsec_fromsec_snapshot_period)
		return;

	ent = atomic64_read(&ipsec_from_sec_entry_cnt);
	if (ent != 1 && (ent % ipsec_fromsec_snapshot_period))
		return;

	printk_ratelimited(KERN_INFO
		"IPSEC_FROMSEC_SNAP: entries=%llu fmt[c=%llu sg=%llu cmp=%llu other=%llu] st[b0=%llu b1=%llu b2=%llu b3=%llu b4=%llu b5=%llu b6=%llu b7=%llu b8=%llu b9=%llu ba=%llu bb=%llu bc=%llu bd=%llu be=%llu bf=%llu] dq[fmt=%u st=0x%08x len=%u off=%u bpid=%u] from_sec=0x%x sa_tr=%llu sa_bad5=%llu sa_bad5_run=%llu sa_last=0x%08x\n",
		(unsigned long long)ent,
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[0]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[1]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[2]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[3]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[0]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[1]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[2]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[3]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[4]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[5]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[6]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[7]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[8]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[9]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[10]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[11]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[12]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[13]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[14]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_status_bucket_cnt[15]),
		dq ? dq->fd.format : 0,
		dq ? dq->fd.status : 0,
		dq ? dq->fd.length20 : 0,
		dq ? dq->fd.offset : 0,
		dq ? dq->fd.bpid : 0,
		from_sec_fq ? from_sec_fq->fqid : 0,
		ipsecsa_info ?
			(unsigned long long)atomic64_read(
				&ipsecsa_info->fromsec_status_transition_cnt) : 0,
		ipsecsa_info ?
			(unsigned long long)atomic64_read(
				&ipsecsa_info->fromsec_bad_50000008_cnt) : 0,
		ipsecsa_info ?
			(unsigned long long)atomic64_read(
				&ipsecsa_info->fromsec_bad_50000008_runlen) : 0,
		ipsecsa_info ? READ_ONCE(ipsecsa_info->fromsec_last_status) : 0);

	ipsec_log_bp_snapshot("fromsec-snapshot");

	if (ipsecsa_info) {
		ipsec_log_fq_runtime_nofilter("fromsec-fq",
				&ipsecsa_info->sec_fq[FQ_FROM_SEC].fq_base);
		ipsec_log_fq_runtime_nofilter("tosec-fq",
				&ipsecsa_info->sec_fq[FQ_TO_SEC].fq_base);
#ifdef UNIQUE_IPSEC_CP_FQID
		ipsec_log_fq_runtime_nofilter("tocp-fq",
				&ipsecsa_info->sec_fq[FQ_TO_CP].fq_base);
#endif
	}

	if (ipsecinfo.ofport_reinject_fq_valid)
		ipsec_log_fq_runtime_nofilter("reinject-fq",
				&ipsecinfo.ofport_reinject_fq.fq_base);
}

static void ipsec_track_fromsec_status(struct dpa_ipsec_sainfo *ipsecsa_info,
		const struct qman_fq *from_sec_fq, uint32_t status)
{
	uint32_t prev;
	uint64_t runlen;

	if (!ipsecsa_info)
		return;

	prev = READ_ONCE(ipsecsa_info->fromsec_last_status);

	if (prev != status) {
		atomic64_inc(&ipsecsa_info->fromsec_status_transition_cnt);

		if (prev == 0x50000008 && status != 0x50000008) {
			runlen = atomic64_read(&ipsecsa_info->fromsec_bad_50000008_runlen);
			printk_ratelimited(KERN_INFO
				"IPSEC_FROMSEC_STATUS: event=bad5-exit from_sec=0x%x prev=0x%08x new=0x%08x run=%llu sa_bad5=%llu sa_tr=%llu\n",
				from_sec_fq ? from_sec_fq->fqid : 0, prev, status,
				(unsigned long long)runlen,
				(unsigned long long)atomic64_read(
					&ipsecsa_info->fromsec_bad_50000008_cnt),
				(unsigned long long)atomic64_read(
					&ipsecsa_info->fromsec_status_transition_cnt));
		}

		if (status == 0x50000008) {
			atomic64_inc(&ipsecsa_info->fromsec_bad_50000008_cnt);
			atomic64_set(&ipsecsa_info->fromsec_bad_50000008_runlen, 1);
			printk_ratelimited(KERN_ERR
				"IPSEC_FROMSEC_STATUS: event=bad5-enter from_sec=0x%x prev=0x%08x new=0x%08x sa_bad5=%llu sa_tr=%llu\n",
				from_sec_fq ? from_sec_fq->fqid : 0, prev, status,
				(unsigned long long)atomic64_read(
					&ipsecsa_info->fromsec_bad_50000008_cnt),
				(unsigned long long)atomic64_read(
					&ipsecsa_info->fromsec_status_transition_cnt));
			ipsec_log_bp_snapshot("bad5-enter");
		} else {
			atomic64_set(&ipsecsa_info->fromsec_bad_50000008_runlen, 0);
		}

		WRITE_ONCE(ipsecsa_info->fromsec_last_status, status);
		return;
	}

	if (status != 0x50000008)
		return;

	runlen = atomic64_inc_return(&ipsecsa_info->fromsec_bad_50000008_runlen);
	if (runlen <= 4 || !(runlen & 0x3fULL)) {
		printk_ratelimited(KERN_ERR
			"IPSEC_FROMSEC_STATUS: event=bad5-sticky from_sec=0x%x st=0x%08x run=%llu sa_bad5=%llu sa_tr=%llu\n",
			from_sec_fq ? from_sec_fq->fqid : 0, status,
			(unsigned long long)runlen,
			(unsigned long long)atomic64_read(
				&ipsecsa_info->fromsec_bad_50000008_cnt),
			(unsigned long long)atomic64_read(
				&ipsecsa_info->fromsec_status_transition_cnt));
	}
}

static void ipsec_log_nofq_snapshot(struct dpa_ipsec_sainfo *src_ipsecsa_info,
		struct dpa_ipsec_sainfo *tx_ipsecsa_info,
		const struct qman_fq *from_sec_fq,
		const struct qm_dqrr_entry *dq,
		const struct qm_fd *compat_fd,
		uint16_t sa_hint, uint16_t peer_sa_hint, uint8_t remap_reason,
		uint64_t nofq_cnt)
{
	uint64_t snap_seq;

	if (!ipsec_nofq_snapshot_enable || !ipsec_nofq_snapshot_period)
		return;

	if (nofq_cnt != 1 && (nofq_cnt % ipsec_nofq_snapshot_period))
		return;

	snap_seq = atomic64_inc_return(&ipsec_nofq_snapshot_cnt);

	printk_ratelimited(KERN_INFO
		"IPSEC_SNAP: type=nofq-fallback seq=%llu nofq_cnt=%llu remap_reason=%u from_sec=0x%x src_sa=0x%x peer_sa=0x%x dq[fmt=%u st=0x%08x len=%u off=%u bpid=%u] compat[fmt=%u len=%u off=%u bpid=%u] src_compat_tx=0x%x remap_compat_tx=0x%x\n",
		(unsigned long long)snap_seq,
		(unsigned long long)nofq_cnt,
		(unsigned int)remap_reason,
		from_sec_fq ? from_sec_fq->fqid : 0,
		sa_hint, peer_sa_hint,
		dq ? dq->fd.format : 0,
		dq ? dq->fd.status : 0,
		dq ? dq->fd.length20 : 0,
		dq ? dq->fd.offset : 0,
		dq ? dq->fd.bpid : 0,
		compat_fd ? compat_fd->format : 0,
		compat_fd ? compat_fd->length20 : 0,
		compat_fd ? compat_fd->offset : 0,
		compat_fd ? compat_fd->bpid : 0,
		src_ipsecsa_info ? src_ipsecsa_info->compat_tx_fq.fqid : 0,
		tx_ipsecsa_info ? tx_ipsecsa_info->compat_tx_fq.fqid : 0);

	if (src_ipsecsa_info) {
		ipsec_log_fq_runtime_nofilter("src-from-sec",
				&src_ipsecsa_info->sec_fq[FQ_FROM_SEC].fq_base);
		ipsec_log_fq_runtime_nofilter("src-to-sec",
				&src_ipsecsa_info->sec_fq[FQ_TO_SEC].fq_base);
#ifdef UNIQUE_IPSEC_CP_FQID
		ipsec_log_fq_runtime_nofilter("src-to-cp",
				&src_ipsecsa_info->sec_fq[FQ_TO_CP].fq_base);
#endif
		if (src_ipsecsa_info->compat_tx_fq.fqid)
			ipsec_log_fq_runtime_nofilter("src-compat-tx",
					&src_ipsecsa_info->compat_tx_fq.fq_base);
	}

	if (tx_ipsecsa_info && tx_ipsecsa_info != src_ipsecsa_info &&
			tx_ipsecsa_info->compat_tx_fq.fqid)
		ipsec_log_fq_runtime_nofilter("remap-compat-tx",
				&tx_ipsecsa_info->compat_tx_fq.fq_base);

	ipsec_log_signal_snapshot("nofq");
}

static void ipsec_log_reinject_snapshot(const struct qman_fq *from_sec_fq,
		const struct qm_fd *compat_fd, uint16_t sa_hint,
		const char *decision, int err)
{
	uint64_t attempt_cnt;

	if (!ipsec_reinject_snapshot_enable || !ipsec_reinject_snapshot_period)
		return;

	attempt_cnt = atomic64_read(&ipsec_reinject_attempt_cnt);
	if (attempt_cnt != 1 && (attempt_cnt % ipsec_reinject_snapshot_period))
		return;

	printk_ratelimited(KERN_INFO
		"IPSEC_REINJECT_SNAP: decision=%s attempts=%llu ok=%llu err=%llu ern=%llu skip_noncontig=%llu skip_nofq=%llu fb_excp=%llu fb_drop=%llu from_sec=0x%x sa=0x%x compat[fmt=%u st=0x%08x len=%u off=%u bpid=%u] err=%d mode=%u\n",
		decision ? decision : "unknown",
		(unsigned long long)attempt_cnt,
		(unsigned long long)atomic64_read(&ipsec_reinject_ok_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_err_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_ern_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_skipped_noncontig_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_skipped_missing_fq_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_fallback_excp_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_fallback_drop_cnt),
		from_sec_fq ? from_sec_fq->fqid : 0,
		sa_hint,
		compat_fd ? compat_fd->format : 0,
		compat_fd ? compat_fd->status : 0,
		compat_fd ? compat_fd->length20 : 0,
		compat_fd ? compat_fd->offset : 0,
		compat_fd ? compat_fd->bpid : 0,
		err, ipsec_compat_reinject_mode);

	if (ipsecinfo.ofport_reinject_fq_valid)
		ipsec_log_fq_runtime_nofilter("reinject-fq",
				&ipsecinfo.ofport_reinject_fq.fq_base);
	if (from_sec_fq)
		ipsec_log_fq_runtime_nofilter("reinject-from-sec",
				(struct qman_fq *)from_sec_fq);
}

static void ipsec_destroy_compat_tx_fq(struct dpa_ipsec_sainfo *ipsecsa_info)
{
	struct qm_mcr_queryfq_np np;
	struct qman_fq *fq;
	int qret;

	if (!ipsecsa_info || !ipsecsa_info->compat_tx_fq.fqid)
		return;

	fq = &ipsecsa_info->compat_tx_fq.fq_base;
	memset(&np, 0, sizeof(np));
	qret = qman_query_fq_np(fq, &np);
	if (qret) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT_FQ: destroy-query-failed fqid=0x%x err=%d\n",
			fq->fqid, qret);
	} else {
		printk_ratelimited(KERN_INFO
			"IPSEC_COMPAT_FQ: destroy fqid=0x%x state=%u frm_cnt=%u byte_cnt=%u\n",
			fq->fqid, np.state, np.frm_cnt, np.byte_cnt);
	}
	cdx_remove_fqid_info_in_procfs(ipsecsa_info->compat_tx_fq.fqid);
	qman_destroy_fq(fq, 0);
	memset(&ipsecsa_info->compat_tx_fq, 0,
			sizeof(ipsecsa_info->compat_tx_fq));
}

static int ipsec_create_ofport_reinject_fq(struct ipsec_info *info)
{
	struct dpa_fq *dpa_fq = &info->ofport_reinject_fq;
	struct qman_fq *fq = &dpa_fq->fq_base;
	struct qm_mcc_initfq opts;
	uint32_t flags = 0;

	memset(dpa_fq, 0, sizeof(*dpa_fq));
	memset(&opts, 0, sizeof(opts));

	flags |= QMAN_FQ_FLAG_TO_DCPORTAL;
	flags |= QMAN_FQ_FLAG_DYNAMIC_FQID;

	dpa_fq->fq_type = FQ_TYPE_RX_PCD;
	dpa_fq->channel = info->ofport_channel;
	dpa_fq->wq = DEFA_WQ_ID;
	dpa_fq->fq_base.cb.ern = dpa_ipsec_ern_cb;
	opts.fqd.fq_ctrl = QM_FQCTRL_PREFERINCACHE;
	opts.fqd.context_a.hi = 0;
	opts.fqd.context_a.lo = 0;

	if (qman_create_fq(0, flags, fq)) {
		DPAIPSEC_ERROR("%s::qman_create_fq failed\n", __FUNCTION__);
		return FAILURE;
	}

	dpa_fq->fqid = fq->fqid;
	opts.fqid = dpa_fq->fqid;
	opts.count = 1;
	opts.fqd.dest.channel = dpa_fq->channel;
	opts.fqd.dest.wq = dpa_fq->wq;
	opts.we_mask = (QM_INITFQ_WE_DESTWQ | QM_INITFQ_WE_FQCTRL |
			QM_INITFQ_WE_CONTEXTB | QM_INITFQ_WE_CONTEXTA);
	if (qman_init_fq(fq, QMAN_INITFQ_FLAG_SCHED, &opts)) {
		DPAIPSEC_ERROR("%s::qman_init_fq failed fqid=%u\n",
				__FUNCTION__, dpa_fq->fqid);
		qman_destroy_fq(fq, 0);
		memset(dpa_fq, 0, sizeof(*dpa_fq));
		return FAILURE;
	}

	info->ofport_reinject_fq_valid = 1;
	printk_ratelimited(KERN_INFO
		"IPSEC_REINJECT: stage=fq-create fqid=0x%x ch=0x%x wq=%u mode=%u\n",
		dpa_fq->fqid, dpa_fq->channel, dpa_fq->wq,
		ipsec_compat_reinject_mode);
	return SUCCESS;
}

static void ipsec_destroy_ofport_reinject_fq(struct ipsec_info *info)
{
	struct dpa_fq *dpa_fq = &info->ofport_reinject_fq;
	struct qman_fq *fq = &dpa_fq->fq_base;

	if (!info->ofport_reinject_fq_valid || !dpa_fq->fqid)
		return;

	qman_destroy_fq(fq, 0);
	printk_ratelimited(KERN_INFO
		"IPSEC_REINJECT: stage=fq-destroy fqid=0x%x\n", dpa_fq->fqid);
	memset(dpa_fq, 0, sizeof(*dpa_fq));
	info->ofport_reinject_fq_valid = 0;
}

static bool ipsec_compat_l2_is_pppoe(const struct dpa_ipsec_sainfo *ipsecsa_info)
{
	const uint8_t *hdr;
	uint8_t len;
	uint16_t ethtype;

	if (!ipsecsa_info)
		return false;

	hdr = ipsecsa_info->compat_l2_hdr;
	len = ipsecsa_info->compat_l2_len;
	if (!hdr || len < 14)
		return false;

	memcpy(&ethtype, hdr + 12, sizeof(ethtype));
	ethtype = ntohs(ethtype);
	if (ethtype == ETH_P_PPP_SES)
		return true;

	if ((ethtype == ETH_P_8021Q || ethtype == ETH_P_8021AD) && len >= 18) {
		memcpy(&ethtype, hdr + 16, sizeof(ethtype));
		ethtype = ntohs(ethtype);
		if (ethtype == ETH_P_PPP_SES)
			return true;
	}

	return false;
}

/* Strict no-FQ remap gate:
 * - frame must be direct and ESP-shaped
 * - SPI must resolve to a valid inbound SA
 * - resolved SA must map to a compat-capable peer context
 * Otherwise caller keeps current exception fallback path.
 */
static PSAEntry ipsec_find_src_sa_by_ipsecsa_info(
		struct dpa_ipsec_sainfo *src_ipsecsa_info)
{
	unsigned int i;
	struct slist_entry *entry;
	PSAEntry cand_sa;

	if (!src_ipsecsa_info)
		return NULL;

	for (i = 0; i < NUM_SA_ENTRIES; i++) {
		slist_for_each(cand_sa, entry, &sa_cache_by_h[i], list_h) {
			if (!cand_sa || (cand_sa->flags & SA_DELETE))
				continue;
			if (!cand_sa->pSec_sa_context ||
			    !cand_sa->pSec_sa_context->dpa_ipsecsa_handle)
				continue;
			if ((struct dpa_ipsec_sainfo *)
			    cand_sa->pSec_sa_context->dpa_ipsecsa_handle ==
			    src_ipsecsa_info)
				return cand_sa;
		}
	}

	return NULL;
}

static struct dpa_ipsec_sainfo *ipsec_find_peer_by_src_sa(
		PSAEntry src_sa, struct dpa_ipsec_sainfo *src_ipsecsa_info,
		uint16_t *peer_sa_hint, bool non_pppoe_only)
{
	unsigned int i;
	struct slist_entry *entry;
	PSAEntry cand_sa;
	PSAEntry fallback_sa = NULL;

	if (peer_sa_hint)
		*peer_sa_hint = 0;
	if (!src_sa || !src_sa->netdev)
		return NULL;

	for (i = 0; i < NUM_SA_ENTRIES; i++) {
		slist_for_each(cand_sa, entry, &sa_cache_by_h[i], list_h) {
			struct dpa_ipsec_sainfo *cand_ipsecsa_info;

			if (!cand_sa || cand_sa == src_sa)
				continue;
			if (cand_sa->flags & SA_DELETE)
				continue;
			if (!cand_sa->pSec_sa_context ||
			    !cand_sa->pSec_sa_context->dpa_ipsecsa_handle)
				continue;
			if (cand_sa->netdev != src_sa->netdev)
				continue;
			if (cand_sa->family != src_sa->family ||
			    cand_sa->id.proto != src_sa->id.proto)
				continue;
			if (src_sa->pRtEntry && cand_sa->pRtEntry &&
			    cand_sa->pRtEntry != src_sa->pRtEntry)
				continue;

			cand_ipsecsa_info = (struct dpa_ipsec_sainfo *)
				cand_sa->pSec_sa_context->dpa_ipsecsa_handle;
			if (!cand_ipsecsa_info || cand_ipsecsa_info == src_ipsecsa_info)
				continue;
			if (!cand_ipsecsa_info->compat_tx_fq.fqid ||
			    !cand_ipsecsa_info->compat_l2_len)
				continue;
			if (non_pppoe_only &&
			    ipsec_compat_l2_is_pppoe(cand_ipsecsa_info))
				continue;

			/* Prefer opposite SA direction (inbound <-> outbound). */
			if (cand_sa->direction != src_sa->direction) {
				if (peer_sa_hint)
					*peer_sa_hint = cand_sa->handle;
				return cand_ipsecsa_info;
			}

			if (!fallback_sa)
				fallback_sa = cand_sa;
		}
	}

	if (fallback_sa && fallback_sa->pSec_sa_context &&
	    fallback_sa->pSec_sa_context->dpa_ipsecsa_handle) {
		if (peer_sa_hint)
			*peer_sa_hint = fallback_sa->handle;
		return (struct dpa_ipsec_sainfo *)
			fallback_sa->pSec_sa_context->dpa_ipsecsa_handle;
	}

	return NULL;
}

static struct dpa_ipsec_sainfo *ipsec_resolve_cached_peer_by_handle(
		struct dpa_ipsec_sainfo *src_ipsecsa_info,
		PSAEntry src_sa, uint16_t *peer_sa_hint, uint8_t *remap_reason,
		bool non_pppoe_only)
{
	PSAEntry peer_sa;
	struct dpa_ipsec_sainfo *peer_ipsecsa_info;
	uint16_t cached;

	if (!src_ipsecsa_info)
		return NULL;

	cached = src_ipsecsa_info->peer_sa_handle;
	if (!cached)
		return NULL;

	peer_sa = (PSAEntry)M_ipsec_sa_cache_lookup_by_h(cached);
	if (!peer_sa || (peer_sa->flags & SA_DELETE) ||
	    !peer_sa->pSec_sa_context ||
	    !peer_sa->pSec_sa_context->dpa_ipsecsa_handle)
		goto clear_cache;

	peer_ipsecsa_info = (struct dpa_ipsec_sainfo *)
		peer_sa->pSec_sa_context->dpa_ipsecsa_handle;
	if (!peer_ipsecsa_info || peer_ipsecsa_info == src_ipsecsa_info)
		goto clear_cache;
	if (!peer_ipsecsa_info->compat_tx_fq.fqid ||
	    !peer_ipsecsa_info->compat_l2_len)
		goto clear_cache;
	if (non_pppoe_only &&
	    ipsec_compat_l2_is_pppoe(peer_ipsecsa_info))
		goto clear_cache;
	if (src_sa && src_sa->netdev && peer_sa->netdev &&
	    src_sa->netdev != peer_sa->netdev)
		goto clear_cache;

	if (peer_sa_hint)
		*peer_sa_hint = cached;
	return peer_ipsecsa_info;

clear_cache:
	src_ipsecsa_info->peer_sa_handle = 0;
	if (remap_reason && *remap_reason == IPSEC_NOFQ_REMAP_OK)
		*remap_reason = IPSEC_NOFQ_REMAP_CACHED_PEER_INVALID;
	return NULL;
}

static struct dpa_ipsec_sainfo *ipsec_resolve_compat_remap_peer(
		struct dpa_ipsec_sainfo *src_ipsecsa_info,
		const struct qm_fd *compat_fd,
		uint16_t src_sa_hint,
		uint16_t *peer_sa_hint,
		uint8_t *remap_reason)
{
	uint8_t *pkt;
	uint32_t spi = 0;
	int esp_parse_rc;
	int sagd_lookup_rc = -1;
	uint16_t recovered_sagd = 0;
	bool non_pppoe_only = false;
	PSAEntry src_sa = NULL;
	PSAEntry peer_sa = NULL;
	struct dpa_ipsec_sainfo *peer_ipsecsa_info = NULL;

	if (peer_sa_hint)
		*peer_sa_hint = 0;
	if (remap_reason)
		*remap_reason = IPSEC_NOFQ_REMAP_OK;

	if (!src_ipsecsa_info || !compat_fd ||
	    compat_fd->format != qm_fd_contig ||
	    !qm_fd_addr(compat_fd) || !compat_fd->length20) {
		if (remap_reason)
			*remap_reason = IPSEC_NOFQ_REMAP_INPUT_INVALID;
		return NULL;
	}

	pkt = (uint8_t *)(phys_to_virt((uint64_t)qm_fd_addr(compat_fd)) +
			compat_fd->offset);
	if (!pkt) {
		if (remap_reason)
			*remap_reason = IPSEC_NOFQ_REMAP_PKT_UNMAPPED;
		return NULL;
	}

	/* Primary source-SA identity should come from owning from-sec queue
	 * context (deterministic). Packet-tail hints are best-effort only. */
	src_sa = ipsec_find_src_sa_by_ipsecsa_info(src_ipsecsa_info);
	if (!src_sa && src_sa_hint)
		src_sa = (PSAEntry)M_ipsec_sa_cache_lookup_by_h(src_sa_hint);
	if (src_sa && src_sa->pSec_sa_context &&
	    src_sa->pSec_sa_context->dpa_ipsecsa_handle &&
	    (struct dpa_ipsec_sainfo *)
	    src_sa->pSec_sa_context->dpa_ipsecsa_handle != src_ipsecsa_info)
		src_sa = NULL;

	/* Primary gate: ESP-shaped frame with recoverable inbound SAGD. */
	esp_parse_rc = ipsec_extract_esp_spi_from_l2(pkt, compat_fd->length20, &spi);
	non_pppoe_only = !!esp_parse_rc;

	peer_ipsecsa_info = ipsec_resolve_cached_peer_by_handle(
			src_ipsecsa_info, src_sa, peer_sa_hint, remap_reason,
			non_pppoe_only);
	if (peer_ipsecsa_info)
		return peer_ipsecsa_info;

	if (!esp_parse_rc)
		sagd_lookup_rc = cdx_ipsec_handle_get_inbound_sagd(spi, &recovered_sagd);
	if (!esp_parse_rc && sagd_lookup_rc == NO_ERR && recovered_sagd) {
		peer_sa = (PSAEntry)M_ipsec_sa_cache_lookup_by_h(recovered_sagd);
		if (!peer_sa || (peer_sa->flags & SA_DELETE) ||
		    !peer_sa->pSec_sa_context ||
		    !peer_sa->pSec_sa_context->dpa_ipsecsa_handle) {
			if (remap_reason)
				*remap_reason = IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED;
			return NULL;
		}

		peer_ipsecsa_info = (struct dpa_ipsec_sainfo *)
			peer_sa->pSec_sa_context->dpa_ipsecsa_handle;
		if (!peer_ipsecsa_info || peer_ipsecsa_info == src_ipsecsa_info) {
			if (remap_reason)
				*remap_reason = IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED;
			return NULL;
		}
		if (!peer_ipsecsa_info->compat_tx_fq.fqid ||
		    !peer_ipsecsa_info->compat_l2_len) {
			if (remap_reason)
				*remap_reason = IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED;
			return NULL;
		}
		if (non_pppoe_only &&
		    ipsec_compat_l2_is_pppoe(peer_ipsecsa_info)) {
			if (remap_reason)
				*remap_reason = IPSEC_NOFQ_REMAP_PEER_L2_UNSUITABLE;
			return NULL;
		}

		if (src_sa && src_sa->netdev && peer_sa->netdev &&
		    src_sa->netdev != peer_sa->netdev) {
			if (remap_reason)
				*remap_reason = IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED;
			return NULL;
		}

		if (peer_sa_hint)
			*peer_sa_hint = recovered_sagd;
		src_ipsecsa_info->peer_sa_handle = recovered_sagd;
		return peer_ipsecsa_info;
	}
	if (!esp_parse_rc && (!recovered_sagd || sagd_lookup_rc != NO_ERR) &&
	    remap_reason && *remap_reason == IPSEC_NOFQ_REMAP_OK)
		*remap_reason = IPSEC_NOFQ_REMAP_ESP_SAGD_UNRESOLVED;

	/* Secondary gate for direct non-ESP return shape:
	 * SA-consistent remap based on source SA relationship. */
	peer_ipsecsa_info = ipsec_find_peer_by_src_sa(src_sa,
			src_ipsecsa_info, peer_sa_hint, non_pppoe_only);
	if (peer_ipsecsa_info && peer_sa_hint && *peer_sa_hint)
		src_ipsecsa_info->peer_sa_handle = *peer_sa_hint;
	if (!peer_ipsecsa_info && remap_reason &&
	    *remap_reason == IPSEC_NOFQ_REMAP_OK)
		*remap_reason = IPSEC_NOFQ_REMAP_SECONDARY_NO_PEER;
	return peer_ipsecsa_info;
}

#define IPSEC_LEARN_FLOW_TABLE_SIZE 256
#define IPSEC_LEARN_FLOW_TTL (60 * HZ)

struct ipsec_learn_flow_key {
	uint8_t family;
	uint8_t proto;
	__be16 sport;
	__be16 dport;
	__be32 addr[8];
};

struct ipsec_learn_flow_entry {
	struct ipsec_learn_flow_key key;
	unsigned long stamp;
	bool valid;
};

static DEFINE_SPINLOCK(ipsec_learn_flow_lock);
static struct ipsec_learn_flow_entry
	ipsec_learn_flows[IPSEC_LEARN_FLOW_TABLE_SIZE];
static uint32_t ipsec_learn_flow_next;

static bool ipsec_compat_parse_inner_flow(const uint8_t *pkt, uint32_t len,
		struct ipsec_learn_flow_key *key)
{
	uint8_t version;
	uint8_t proto;
	uint32_t l4off;
	const struct iphdr *iph;

	if (!pkt || !len || !key)
		return false;

	memset(key, 0, sizeof(*key));
	version = pkt[0] >> 4;

	if (version == 4) {
		if (len < sizeof(*iph))
			return false;

		iph = (const struct iphdr *)pkt;
		if (iph->version != 4 || iph->ihl < 5)
			return false;

		l4off = iph->ihl * 4;
		if (len < l4off + 4)
			return false;
		if (ntohs(iph->frag_off) & (IP_MF | IP_OFFSET))
			return false;

		proto = iph->protocol;
		if (proto != IPPROTO_TCP && proto != IPPROTO_UDP)
			return false;

		key->family = AF_INET;
		key->proto = proto;
		memcpy(&key->sport, pkt + l4off, sizeof(key->sport));
		memcpy(&key->dport, pkt + l4off + 2, sizeof(key->dport));
		key->addr[0] = iph->saddr;
		key->addr[1] = iph->daddr;
		return true;
	}

	if (version == 6) {
		if (len < 44)
			return false;

		proto = pkt[6];
		if (proto != IPPROTO_TCP && proto != IPPROTO_UDP)
			return false;

		l4off = 40;
		key->family = AF_INET6;
		key->proto = proto;
		memcpy(&key->sport, pkt + l4off, sizeof(key->sport));
		memcpy(&key->dport, pkt + l4off + 2, sizeof(key->dport));
		memcpy(&key->addr[0], pkt + 8, 16);
		memcpy(&key->addr[4], pkt + 24, 16);
		return true;
	}

	return false;
}

static bool ipsec_compat_should_learning_punt(const uint8_t *pkt, uint32_t len)
{
	struct ipsec_learn_flow_entry *entry;
	struct ipsec_learn_flow_key key;
	struct ipsec_learn_flow_entry *empty = NULL;
	unsigned long now = jiffies;
	uint32_t idx;
	uint32_t i;

	if (!ipsec_compat_learning_punt)
		return false;

	if (!ipsec_compat_parse_inner_flow(pkt, len, &key)) {
		atomic64_inc(&ipsec_learning_skip_cnt);
		return false;
	}

	idx = jhash(&key, sizeof(key), 0) &
		(IPSEC_LEARN_FLOW_TABLE_SIZE - 1);

	spin_lock(&ipsec_learn_flow_lock);
	for (i = 0; i < 4; i++) {
		entry = &ipsec_learn_flows[
			(idx + i) & (IPSEC_LEARN_FLOW_TABLE_SIZE - 1)];
		if (!entry->valid || time_after(now, entry->stamp +
		    IPSEC_LEARN_FLOW_TTL)) {
			if (!empty)
				empty = entry;
			continue;
		}

		if (!memcmp(&entry->key, &key, sizeof(key))) {
			entry->stamp = now;
			spin_unlock(&ipsec_learn_flow_lock);
			atomic64_inc(&ipsec_learning_seen_cnt);
			return false;
		}
	}

	if (!empty) {
		empty = &ipsec_learn_flows[
			ipsec_learn_flow_next++ &
			(IPSEC_LEARN_FLOW_TABLE_SIZE - 1)];
	}

	empty->key = key;
	empty->stamp = now;
	empty->valid = true;
	spin_unlock(&ipsec_learn_flow_lock);

	atomic64_inc(&ipsec_learning_punt_cnt);
	return true;
}

static int ipsec_compat_punt_inner_to_stack(struct dpa_ipsec_sainfo *src_info,
		struct qm_fd *compat_fd)
{
	struct sec_path *sp;
	struct sk_buff *skb = NULL;
	struct xfrm_state *x = NULL;
	struct net_device *net_dev;
	struct timespec64 ktime;
	PSAEntry src_sa;
	uint8_t *base;
	uint8_t *pkt;
	uint32_t len;
	__be16 proto;
	uint16_t sagd;
	int rc;

	if (!src_info || !compat_fd || compat_fd->format != qm_fd_contig ||
	    !qm_fd_addr(compat_fd) || !compat_fd->length20)
		return -EINVAL;

	src_sa = ipsec_find_src_sa_by_ipsecsa_info(src_info);
	if (!src_sa || !src_sa->netdev || !src_sa->handle)
		return -ENODEV;

	base = (uint8_t *)phys_to_virt((uint64_t)qm_fd_addr(compat_fd));
	if (!base)
		return -EFAULT;

	pkt = base + compat_fd->offset;
	len = compat_fd->length20;
	if (len < 1)
		return -EINVAL;

	if ((pkt[0] >> 4) == 4)
		proto = htons(ETH_P_IP);
	else if ((pkt[0] >> 4) == 6)
		proto = htons(ETH_P_IPV6);
	else
		return -EPROTONOSUPPORT;

	net_dev = src_sa->netdev;
	sagd = src_sa->handle;

	x = xfrm_state_lookup_byhandle(dev_net(net_dev), sagd);
	if (!x)
		return -ENOENT;

	skb = netdev_alloc_skb_ip_align(net_dev, len);
	if (!skb) {
		rc = -ENOMEM;
		goto err_put_xfrm;
	}

	skb_put_data(skb, pkt, len);
	skb->dev = net_dev;
	skb->skb_iif = net_dev->ifindex;
	skb->protocol = proto;
	skb->pkt_type = PACKET_HOST;
	skb->ip_summed = CHECKSUM_NONE;
	skb->csum_level = 0;
	skb->encapsulation = 0;
	skb_reset_mac_header(skb);
	skb->mac_len = 0;
	skb_reset_network_header(skb);
	skb_probe_transport_header(skb);

	sp = secpath_set(skb);
	if (!sp) {
		rc = -ENOMEM;
		goto err_free_skb;
	}

	sp->xvec[sp->len++] = x;
	if (!x->curlft.use_time) {
		ktime_get_real_ts64(&ktime);
		x->curlft.use_time = (unsigned long)ktime.tv_sec;
	}
	x = NULL;

	ipsec_bp_release_fd(compat_fd, &ipsec_bp_release_compat_cnt);
	netif_receive_skb(skb);
	return 0;

err_free_skb:
	dev_kfree_skb(skb);
err_put_xfrm:
	xfrm_state_put(x);
	return rc;
}

static int ipsec_create_compat_tx_fq(struct dpa_ipsec_sainfo *ipsecsa_info,
		uint32_t channel, uint32_t wq, uint16_t sa_handle)
{
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	struct qm_mcc_initfq opts;
	uint32_t flags;

	if (!ipsecsa_info || !channel)
		return -EINVAL;

	ipsec_destroy_compat_tx_fq(ipsecsa_info);

	memset(&opts, 0, sizeof(opts));
	dpa_fq = &ipsecsa_info->compat_tx_fq;
	fq = &dpa_fq->fq_base;
	dpa_fq->fq_type = FQ_TYPE_TX_CONFIRM;
	dpa_fq->channel = channel;
	dpa_fq->wq = wq;
	dpa_fq->fq_base.cb.ern = dpa_ipsec_ern_cb;
	flags = (QMAN_FQ_FLAG_TO_DCPORTAL | QMAN_FQ_FLAG_DYNAMIC_FQID);
	if (qman_create_fq(0, flags, fq))
		return -EINVAL;

	dpa_fq->fqid = fq->fqid;
	opts.fqid = dpa_fq->fqid;
	opts.count = 1;
	opts.fqd.dest.channel = dpa_fq->channel;
	opts.fqd.dest.wq = dpa_fq->wq;
	opts.fqd.fq_ctrl = QM_FQCTRL_PREFERINCACHE;
	/* Match CDX accelerated forward-TX FQs for pool-backed frames.
	 * Keep B0V=0 with context_b=0 so FMAN does not send TX confirmation
	 * to FQ 0, but retain the full context override used by create_fwd_tx_fqs().
	 */
	opts.fqd.context_a.hi = 0x9a000000;
	opts.fqd.context_a.lo = 0xC0000000;
	opts.fqd.context_b = 0;
	opts.we_mask = (QM_INITFQ_WE_DESTWQ | QM_INITFQ_WE_FQCTRL |
			QM_INITFQ_WE_CONTEXTB | QM_INITFQ_WE_CONTEXTA);
	if (qman_init_fq(fq, QMAN_INITFQ_FLAG_SCHED, &opts)) {
		qman_destroy_fq(fq, 0);
		memset(&ipsecsa_info->compat_tx_fq, 0,
				sizeof(ipsecsa_info->compat_tx_fq));
		return -EINVAL;
	}

	ipsecsa_info->compat_tx_channel = channel;
	ipsecsa_info->compat_tx_wq = wq;
	cdx_create_type_fqid_info_in_procfs(fq, SA_DIR, ipsecsa_info->sa_proc_entry,
			"compat_tx");
	printk_ratelimited(KERN_INFO
		"IPSEC_COMPAT_FQ: create fqid=0x%x channel=0x%x wq=%u sa=0x%x\n",
		dpa_fq->fqid, channel, wq, sa_handle);
	return 0;
}

int cdx_ipsec_sa_set_compat_tx(void *handle, uint32_t channel, uint32_t wq)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = handle;

	if (!ipsecsa_info || !channel)
		return -EINVAL;

	if (ipsecsa_info->compat_tx_fq.fqid &&
			ipsecsa_info->compat_tx_channel == channel &&
			ipsecsa_info->compat_tx_wq == wq)
		return 0;

	return ipsec_create_compat_tx_fq(ipsecsa_info, channel, wq, 0);
}

int cdx_ipsec_sa_set_compat_l2(void *handle, const uint8_t *hdr, uint8_t len,
		uint8_t pppoe_off)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = handle;

	if (!ipsecsa_info || !hdr || !len ||
			len > sizeof(ipsecsa_info->compat_l2_hdr))
		return -EINVAL;

	memcpy(ipsecsa_info->compat_l2_hdr, hdr, len);
	ipsecsa_info->compat_l2_len = len;
	ipsecsa_info->compat_l2_pppoe_off = pppoe_off;

	return 0;
}

int cdx_ipsec_sa_set_compat_tx_dev(void *handle, struct net_device *dev)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = (struct dpa_ipsec_sainfo *)handle;

	if (!ipsecsa_info || !dev)
		return -EINVAL;
	ipsecsa_info->compat_tx_dev = dev;
	return 0;
}

int cdx_ipsec_sa_set_compat_lan_dev(void *handle, struct net_device *dev)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = (struct dpa_ipsec_sainfo *)handle;
	if (!ipsecsa_info)
		return -EINVAL;
	ipsecsa_info->compat_lan_dev = dev;
	ipsecsa_info->compat_tx_dev = dev;
	return 0;
}

int cdx_ipsec_sa_set_compat_mtu(void *handle, uint16_t mtu)
{
	struct dpa_ipsec_sainfo *ipsecsa_info = (struct dpa_ipsec_sainfo *)handle;

	if (!ipsecsa_info)
		return -EINVAL;

	ipsecsa_info->compat_mtu = mtu;
	return 0;
}

static bool ipsec_compat_ipv6_tcp_offset(const uint8_t *pkt, uint32_t len,
		unsigned int *thoff)
{
	uint8_t nexthdr;
	unsigned int off;

	if (len < sizeof(struct ipv6hdr))
		return false;

	nexthdr = ((const struct ipv6hdr *)pkt)->nexthdr;
	off = sizeof(struct ipv6hdr);

	for (;;) {
		unsigned int hdrlen;

		if (nexthdr == IPPROTO_TCP) {
			*thoff = off;
			return len >= off + sizeof(struct tcphdr);
		}

		switch (nexthdr) {
		case NEXTHDR_HOP:
		case NEXTHDR_ROUTING:
		case NEXTHDR_DEST:
			if (len < off + 2)
				return false;
			nexthdr = pkt[off];
			hdrlen = (pkt[off + 1] + 1) * 8;
			break;
		default:
			return false;
		}

		if (!hdrlen || len < off + hdrlen)
			return false;
		off += hdrlen;
	}
}

static void ipsec_compat_mss_clamp(struct qm_fd *fd, uint16_t mtu)
{
	unsigned int tcp_hdrlen, thoff;
	struct tcphdr *th;
	uint8_t *pkt, *opt;
	uint16_t newmss;
	uint32_t len;
	int i;

	if (!mtu || !fd || fd->format != qm_fd_contig || !qm_fd_addr(fd))
		return;

	pkt = (uint8_t *)phys_to_virt((uint64_t)qm_fd_addr(fd)) + fd->offset;
	len = fd->length20;
	if (len < 1)
		return;

	switch (pkt[0] >> 4) {
	case 4: {
		struct iphdr *iph;

		if (len < sizeof(struct iphdr))
			return;
		iph = (struct iphdr *)pkt;
		thoff = iph->ihl * 4;
		if (thoff < sizeof(struct iphdr) ||
		    len < thoff + sizeof(struct tcphdr) ||
		    iph->protocol != IPPROTO_TCP ||
		    (ntohs(iph->frag_off) & (IP_MF | IP_OFFSET)))
			return;
		break;
	}
	case 6:
		if (!ipsec_compat_ipv6_tcp_offset(pkt, len, &thoff))
			return;
		break;
	default:
		return;
	}

	th = (struct tcphdr *)(pkt + thoff);
	if (!th->syn || th->rst)
		return;

	tcp_hdrlen = th->doff * 4;
	if (tcp_hdrlen < sizeof(struct tcphdr) ||
	    len < thoff + tcp_hdrlen ||
	    mtu <= thoff + tcp_hdrlen)
		return;

	newmss = mtu - thoff - tcp_hdrlen;
	opt = (uint8_t *)th;

	for (i = sizeof(struct tcphdr); i < tcp_hdrlen;) {
		unsigned int optlen;
		uint16_t oldmss;

		if (opt[i] == TCPOPT_EOL)
			break;
		if (opt[i] == TCPOPT_NOP) {
			i++;
			continue;
		}
		if (i + 1 >= tcp_hdrlen)
			break;

		optlen = opt[i + 1];
		if (optlen < 2 || i + optlen > tcp_hdrlen)
			break;

		if (opt[i] == TCPOPT_MSS && optlen == TCPOLEN_MSS) {
			oldmss = (opt[i + 2] << 8) | opt[i + 3];
			if (oldmss <= newmss)
				return;

			opt[i + 2] = newmss >> 8;
			opt[i + 3] = newmss & 0xff;
			csum_replace2(&th->check, htons(oldmss), htons(newmss));
			return;
		}
		i += optlen;
	}
}

static void dpa_ipsec_ern_cb(struct qman_portal *qm, struct qman_fq *fq,
		const struct qm_mr_entry *msg)
{
	const struct qm_fd *fd = &msg->ern.fd;
	uint32_t len;
	uint8_t *ptr;
	uint16_t sa_hint = 0;
	printk_ratelimited(KERN_INFO
		"IPSEC_ERN: fqid=0x%x rc=%u fd[fmt=%u bpid=%u off=%u len=%u st=0x%08x] addr=0x%llx\n",
		fq->fqid, msg->ern.rc, fd->format, fd->bpid, fd->offset,
		fd->length20, fd->status, (unsigned long long)qm_fd_addr(fd));
	if (fd->format == qm_fd_contig) {
		len = fd->length20;
		ptr = (uint8_t *)(phys_to_virt((uint64_t)qm_fd_addr(fd)) +
				fd->offset);
		if (len >= 2)
			memcpy(&sa_hint, ptr + len - 2, sizeof(sa_hint));
		ipsec_dbg_log_pkt_identity("ern", fq->fqid, fd->status, ptr, len,
				sa_hint);
		ipsec_dbg_log_status("ern", sa_hint, fd->status);
	}

	if (ipsecinfo.ofport_reinject_fq_valid &&
	    fq == &ipsecinfo.ofport_reinject_fq.fq_base) {
		atomic64_inc(&ipsec_reinject_ern_cnt);
		printk_ratelimited(KERN_ERR
			"IPSEC_REINJECT: stage=ern reinject_fq=0x%x rc=%u len=%u off=%u fmt=%u st=0x%08x\n",
			fq->fqid, msg->ern.rc, fd->length20, fd->offset,
			fd->format, fd->status);
	}

	DPAIPSEC_ERROR("%s::fqid %x(%d)\n", __FUNCTION__, fq->fqid, fq->fqid);

	/* Release the buffer that was rejected by the enqueue.
	 * Without this, ERN'd buffers leak from the source pool. */
	if (qm_fd_addr(fd))
		ipsec_bp_release_fd((struct qm_fd *)fd,
				&ipsec_bp_release_ern_cnt);
}

static int ipsec_compat_dma_sync_fd(const struct qm_fd *fd,
		enum dma_data_direction dir, bool for_device)
{
	struct dpa_bp *bp;

	if (!fd || fd->format != qm_fd_contig || !qm_fd_addr(fd))
		return -EINVAL;

	bp = dpa_bpid2pool(fd->bpid);
	if (!bp || !bp->dev || !bp->size)
		return -ENODEV;

	if (for_device)
		dma_sync_single_for_device(bp->dev, qm_fd_addr(fd), bp->size, dir);
	else
		dma_sync_single_for_cpu(bp->dev, qm_fd_addr(fd), bp->size, dir);

	return 0;
}

static void ipsec_compat_dma_sync_sg_for_cpu(const struct qm_sg_entry *sg)
{
	struct dpa_bp *bp;

	if (!sg || !qm_sg_addr(sg))
		return;

	bp = dpa_bpid2pool(qm_sg_entry_get_bpid(sg));
	if (!bp || !bp->dev || !bp->size)
		return;

	dma_sync_single_for_cpu(bp->dev, qm_sg_addr(sg), bp->size,
			DMA_BIDIRECTIONAL);
}

static void ipsec_compat_dma_sync_buf_for_cpu(struct dpa_bp *bp, dma_addr_t addr)
{
	if (!bp || !bp->dev || !bp->size || !addr)
		return;

	dma_sync_single_for_cpu(bp->dev, addr, bp->size, DMA_BIDIRECTIONAL);
}

static void ipsec_release_sg_payload_fd(const struct qm_sg_entry *sg)
{
	struct qm_fd fd;

	if (!sg || !qm_sg_addr(sg) || qm_sg_entry_get_ext(sg))
		return;
	if (qm_sg_entry_get_bpid(sg) == 0xff)
		return;

	memset(&fd, 0, sizeof(fd));
	qm_fd_addr_set64(&fd, qm_sg_addr(sg));
	fd.format = qm_fd_contig;
	fd.length20 = qm_sg_entry_get_len(sg);
	fd.offset = qm_sg_entry_get_offset(sg);
	fd.bpid = qm_sg_entry_get_bpid(sg);

	ipsec_bp_release_fd(&fd, &ipsec_bp_release_sg_payload_cnt);
}

static int ipsec_copy_sg_list_to_contig_fd(const struct qm_sg_entry *sgt,
		struct qm_fd *compat_fd, bool release_payload)
{
	struct bm_buffer bmb;
	struct dpa_bp *bp;
	uint8_t *dst_base;
	uint32_t payload_len = 0;
	uint32_t dst_off;
	uint16_t new_off = 64;
	int i;
	bool final = false;

	if (!sgt || !compat_fd)
		return -EINVAL;

	for (i = 0; i < IPSEC_COMPAT_MAX_SG_ENTRIES; i++) {
		const struct qm_sg_entry *sg = &sgt[i];

		if (qm_sg_entry_get_ext(sg))
			return -EOPNOTSUPP;

		if (qm_sg_addr(sg) && qm_sg_entry_get_len(sg))
			payload_len += qm_sg_entry_get_len(sg);

		if (qm_sg_entry_get_final(sg)) {
			final = true;
			break;
		}
	}

	if (!final || !payload_len)
		return -EINVAL;

	bp = ipsecinfo.ipsec_bp;
	if (!bp || !bp->pool || !bp->size)
		return -ENODEV;
	if ((uint32_t)new_off + payload_len > bp->size)
		return -ENOSPC;

	if (bman_acquire(bp->pool, &bmb, 1, 0) != 1) {
		ipsec_bp_note_alloc(IPSEC_BP_SGLIST, -ENOBUFS);
		ipsec_log_bp_snapshot("sglist-enobufs");
		return -ENOBUFS;
	}
	ipsec_bp_note_alloc(IPSEC_BP_SGLIST, 0);

	dst_base = (uint8_t *)phys_to_virt((uint64_t)bmb.addr);
	if (!dst_base) {
		while (bman_release(bp->pool, &bmb, 1, 0))
			cpu_relax();
		return -EFAULT;
	}

	ipsec_compat_dma_sync_buf_for_cpu(bp, bmb.addr);

	dst_off = new_off;
	for (i = 0; i < IPSEC_COMPAT_MAX_SG_ENTRIES; i++) {
		const struct qm_sg_entry *sg = &sgt[i];
		uint8_t *src_base;
		uint32_t len;

		len = qm_sg_entry_get_len(sg);
		if (qm_sg_addr(sg) && len) {
			ipsec_compat_dma_sync_sg_for_cpu(sg);
			src_base = (uint8_t *)phys_to_virt((uint64_t)qm_sg_addr(sg));
			if (!src_base) {
				while (bman_release(bp->pool, &bmb, 1, 0))
					cpu_relax();
				return -EFAULT;
			}
			memcpy(dst_base + dst_off,
			       src_base + qm_sg_entry_get_offset(sg), len);
			dst_off += len;
		}

		if (qm_sg_entry_get_final(sg))
			break;
	}

	memset(compat_fd, 0, sizeof(*compat_fd));
	qm_fd_addr_set64(compat_fd, bmb.addr);
	compat_fd->format = qm_fd_contig;
	compat_fd->bpid = bp->bpid;
	compat_fd->offset = new_off;
	compat_fd->length20 = payload_len;

	if (release_payload) {
		for (i = 0; i < IPSEC_COMPAT_MAX_SG_ENTRIES; i++) {
			const struct qm_sg_entry *sg = &sgt[i];

			if (qm_sg_addr(sg) && qm_sg_entry_get_len(sg))
				ipsec_release_sg_payload_fd(sg);

			if (qm_sg_entry_get_final(sg))
				break;
		}
	}

	return 0;
}

static int ipsec_copy_sg_payload_to_contig_fd(const struct qm_sg_entry *sg,
		struct qm_fd *compat_fd, bool release_payload)
{
	struct bm_buffer bmb;
	struct dpa_bp *bp;
	uint8_t *src_base;
	uint8_t *dst_base;
	uint16_t new_off = 64;
	uint32_t payload_len;
	uint16_t payload_off;

	if (!sg || !compat_fd)
		return -EINVAL;
	if (qm_sg_entry_get_ext(sg) || !qm_sg_addr(sg) ||
	    !qm_sg_entry_get_len(sg))
		return -EINVAL;

	bp = ipsecinfo.ipsec_bp;
	if (!bp || !bp->pool || !bp->size)
		return -ENODEV;

	payload_len = qm_sg_entry_get_len(sg);
	payload_off = qm_sg_entry_get_offset(sg);
	if ((uint32_t)new_off + payload_len > bp->size)
		return -ENOSPC;

	if (bman_acquire(bp->pool, &bmb, 1, 0) != 1) {
		ipsec_bp_note_alloc(IPSEC_BP_PAYLOAD, -ENOBUFS);
		ipsec_log_bp_snapshot("payload-enobufs");
		return -ENOBUFS;
	}
	ipsec_bp_note_alloc(IPSEC_BP_PAYLOAD, 0);

	src_base = (uint8_t *)phys_to_virt((uint64_t)qm_sg_addr(sg));
	dst_base = (uint8_t *)phys_to_virt((uint64_t)bmb.addr);
	if (!src_base || !dst_base) {
		while (bman_release(bp->pool, &bmb, 1, 0))
			cpu_relax();
		return -EFAULT;
	}

	ipsec_compat_dma_sync_sg_for_cpu(sg);
	ipsec_compat_dma_sync_buf_for_cpu(bp, bmb.addr);

	memcpy(dst_base + new_off, src_base + payload_off, payload_len);

	memset(compat_fd, 0, sizeof(*compat_fd));
	qm_fd_addr_set64(compat_fd, bmb.addr);
	compat_fd->format = qm_fd_contig;
	compat_fd->bpid = bp->bpid;
	compat_fd->offset = new_off;
	compat_fd->length20 = payload_len;

	if (release_payload)
		ipsec_release_sg_payload_fd(sg);

	return 0;
}

static int ipsec_build_compat_fd(const struct qm_fd *fd,
		struct qm_fd *compat_fd)
{
	struct qm_sg_entry *sgt;
	struct qm_sg_entry *sg;

	if (!fd || !compat_fd)
		return -EINVAL;

	/* Direct contiguous return path. */
	if (fd->format == qm_fd_contig) {
		if (!qm_fd_addr(fd) || !fd->length20)
			return -EINVAL;
		memcpy(compat_fd, fd, sizeof(*compat_fd));
		compat_fd->status = 0;
		compat_fd->cmd = 0;
		return 0;
	}

	/* SG returns need normalization because LAN compat delivery depends on
	 * direct frame access for neighbor lookup and L2 prepend. */
	if (fd->format == qm_fd_sg) {
		int err;

		sgt = (struct qm_sg_entry *)phys_to_virt((uint64_t)qm_fd_addr(fd));
		if (!sgt)
			return -EINVAL;
		err = ipsec_copy_sg_list_to_contig_fd(sgt, compat_fd, false);
		if (!err)
			dpa_fd_release(NULL, fd);
		return err;
	}

	if (fd->format != qm_fd_compound || !qm_fd_addr(fd))
		return -EINVAL;

	sgt = (struct qm_sg_entry *)phys_to_virt((uint64_t)qm_fd_addr(fd));
	if (!sgt)
		return -EINVAL;

	/* SEC can return a valid payload in SG[1] while SG[0] is empty.
	 * Accept either slot and normalize into a direct compat FD. */
	sg = &sgt[0];
	if (!qm_sg_addr(sg) || !qm_sg_entry_get_len(sg)) {
		sg = &sgt[1];
		if (!qm_sg_addr(sg) || !qm_sg_entry_get_len(sg))
			return -EINVAL;
	}

	if (qm_sg_entry_get_ext(sg)) {
		struct qm_sg_entry *ext_sgt;
		int err;

		ext_sgt = (struct qm_sg_entry *)phys_to_virt(
				(uint64_t)qm_sg_addr(sg));
		if (!ext_sgt)
			return -EINVAL;

		err = ipsec_copy_sg_list_to_contig_fd(ext_sgt, compat_fd, true);
		if (err) {
			printk_ratelimited(KERN_INFO
				"IPSEC_COMPAT: compound-ext-copy-failed st=0x%08x err=%d\n",
				fd->status, err);
			ipsec_log_bp_snapshot("compound-ext-copy-failed");
			return err;
		}
		return 0;
	}

	return ipsec_copy_sg_payload_to_contig_fd(sg, compat_fd, true);
}

/* Return inner ETH(+VLAN) header length if payload starts with L2 then IP. */
static uint16_t ipsec_compat_inner_l2_len(const struct qm_fd *fd)
{
	const uint8_t *pkt;
	uint8_t *base;
	uint32_t len, l3;
	uint16_t eth;

	if (!fd || fd->format != qm_fd_contig || !qm_fd_addr(fd) || !fd->length20)
		return 0;

	base = (uint8_t *)phys_to_virt((uint64_t)qm_fd_addr(fd));
	if (!base)
		return 0;

	pkt = base + fd->offset;
	len = fd->length20;
	if (len < ETH_HLEN)
		return 0;

	l3 = ETH_HLEN;
	eth = ((uint16_t)pkt[12] << 8) | pkt[13];

	while ((eth == ETH_P_8021Q || eth == ETH_P_8021AD) && len >= (l3 + 4)) {
		eth = ((uint16_t)pkt[l3 + 2] << 8) | pkt[l3 + 3];
		l3 += 4;
	}

	if (eth == ETH_P_IP || eth == ETH_P_IPV6)
		return (uint16_t)l3;

	return 0;
}

static int ipsec_compat_rebuild_with_headroom(struct qm_fd *compat_fd,
		uint16_t min_headroom)
{
	struct bm_buffer bmb;
	struct dpa_bp *bp;
	uint8_t *src_base;
	uint8_t *dst_base;
	uint16_t new_off;
	uint16_t payload_len;

	if (!compat_fd || compat_fd->format != qm_fd_contig ||
	    !qm_fd_addr(compat_fd) || !compat_fd->length20)
		return -EINVAL;

	bp = ipsecinfo.ipsec_bp;
	if (!bp || !bp->pool || !bp->size)
		return -ENODEV;

	/* Keep room for FMAN private slot and L2 prepend. */
	new_off = min_headroom;
	if (new_off < 64)
		new_off = 64;
	if ((uint32_t)new_off + compat_fd->length20 > bp->size)
		return -ENOSPC;

	if (bman_acquire(bp->pool, &bmb, 1, 0) != 1) {
		ipsec_bp_note_alloc(IPSEC_BP_REBUILD, -ENOBUFS);
		ipsec_log_bp_snapshot("rebuild-enobufs");
		return -ENOBUFS;
	}
	ipsec_bp_note_alloc(IPSEC_BP_REBUILD, 0);

	src_base = (uint8_t *)phys_to_virt((uint64_t)qm_fd_addr(compat_fd));
	dst_base = (uint8_t *)phys_to_virt((uint64_t)bmb.addr);
	if (!src_base || !dst_base) {
		while (bman_release(bp->pool, &bmb, 1, 0))
			cpu_relax();
		return -EFAULT;
	}

	payload_len = compat_fd->length20;

	memcpy(dst_base + new_off,
		src_base + compat_fd->offset,
		payload_len);

	/* Drop the old frame backing this compat FD and switch to rebuilt one. */
	ipsec_bp_release_fd(compat_fd, &ipsec_bp_release_rebuild_old_cnt);

	memset(compat_fd, 0, sizeof(*compat_fd));
	qm_fd_addr_set64(compat_fd, bmb.addr);
	compat_fd->format = qm_fd_contig;
	compat_fd->bpid = bp->bpid;
	compat_fd->offset = new_off;
	compat_fd->length20 = payload_len;
	return 0;
}

/* Log the frame shape and first bytes right before compat enqueue. */

static enum qman_cb_dqrr_result ipsec_from_sec_compat_handler(
		struct qman_portal *qm, struct qman_fq *fq,
		const struct qm_dqrr_entry *dq)
{
	struct dpa_ipsec_sainfo *ipsecsa_info;
	struct dpa_ipsec_sainfo *tx_ipsecsa_info;
	struct qm_fd compat_fd;
	uint8_t *buf;
	uint16_t inner_l2_len, old_off, old_len;
	uint16_t pppoe_payload_len;
	uint16_t sa_hint = 0;
	uint16_t peer_sa_hint = 0;
	uint8_t remap_reason = IPSEC_NOFQ_REMAP_OK;
	struct net_device *runtime_tx_dev = NULL;
	bool runtime_tx_dev_is_preferred = false;
	int err;

	ipsecsa_info = container_of((struct dpa_fq *)fq,
			struct dpa_ipsec_sainfo, sec_fq[FQ_FROM_SEC]);
	atomic64_inc(&ipsec_from_sec_entry_cnt);
	if (dq->fd.format <= qm_fd_compound)
		atomic64_inc(&ipsec_from_sec_format_cnt[dq->fd.format]);
	else
		atomic64_inc(&ipsec_from_sec_format_cnt[3]);
	atomic64_inc(&ipsec_from_sec_status_bucket_cnt[
		ipsec_status_bucket(dq->fd.status)]);
	ipsec_track_fromsec_status(ipsecsa_info, fq, dq->fd.status);
	ipsec_log_from_sec_snapshot(ipsecsa_info, fq, dq);

	err = ipsec_build_compat_fd(&dq->fd, &compat_fd);
	if (err) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: unwrap-failed fqid=0x%x fmt=%u st=0x%08x err=%d\n",
			fq->fqid, dq->fd.format, dq->fd.status, err);
		ipsec_log_bp_snapshot("unwrap-failed");
		/* Keep traffic moving for known SEC return-shape failures. */
		if (dq->fd.status == 0x50000008) {
			static atomic64_t ipsec_bpderr_drop_cnt = ATOMIC64_INIT(0);
			u64 cnt = atomic64_inc_return(&ipsec_bpderr_drop_cnt);
			printk_ratelimited(KERN_ERR
				"IPSEC_COMPAT: bpderr-drop fqid=0x%x fmt=%u st=0x%08x cnt=%llu\n",
				fq->fqid, dq->fd.format, dq->fd.status,
				(unsigned long long)cnt);
		}
		goto release_reclaim;
	}

	if (!qm_fd_addr(&compat_fd) || !compat_fd.length20) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: malformed-built-fd from_sec=0x%x fmt=%u len=%u off=%u bpid=%u addr=0x%llx\n",
			fq->fqid, compat_fd.format, compat_fd.length20,
			compat_fd.offset, compat_fd.bpid,
			(unsigned long long)qm_fd_addr(&compat_fd));
		ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
		goto release_reclaim;
	}

	err = ipsec_compat_dma_sync_fd(&compat_fd, DMA_BIDIRECTIONAL,
			false);
	if (err) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: dma-sync-cpu-failed from_sec=0x%x fmt=%u len=%u off=%u bpid=%u err=%d\n",
			fq->fqid, compat_fd.format, compat_fd.length20,
			compat_fd.offset, compat_fd.bpid, err);
		ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
		goto release_reclaim;
	}
	if (compat_fd.format == qm_fd_contig && qm_fd_addr(&compat_fd)) {
		if (compat_fd.length20 >= 2)
			memcpy(&sa_hint,
				(uint8_t *)(phys_to_virt((uint64_t)qm_fd_addr(&compat_fd)) +
				compat_fd.offset + compat_fd.length20 - 2),
				sizeof(sa_hint));
		ipsec_dbg_log_hex_sample("compat-built",
				ipsecsa_info->compat_tx_fq.fqid,
				(uint8_t *)(phys_to_virt((uint64_t)qm_fd_addr(&compat_fd)) +
				compat_fd.offset), compat_fd.length20, 0);
	}
	ipsec_dbg_log_fq_runtime("compat-tx",
			&ipsecsa_info->compat_tx_fq.fq_base, sa_hint);
	ipsec_dbg_log_fq_runtime("compat-from_sec",
			&ipsecsa_info->sec_fq[FQ_FROM_SEC].fq_base, sa_hint);
#ifdef UNIQUE_IPSEC_CP_FQID
	ipsec_dbg_log_fq_runtime("compat-to_cp",
			&ipsecsa_info->sec_fq[FQ_TO_CP].fq_base, sa_hint);
#endif
	tx_ipsecsa_info = ipsecsa_info;

	if (!tx_ipsecsa_info->compat_tx_fq.fqid &&
	    dq->fd.format != qm_fd_compound) {
		tx_ipsecsa_info = ipsec_resolve_compat_remap_peer(ipsecsa_info,
				&compat_fd, sa_hint, &peer_sa_hint, &remap_reason);
		if (tx_ipsecsa_info && tx_ipsecsa_info->compat_tx_fq.fqid) {
			printk_ratelimited(KERN_INFO
				"IPSEC_COMPAT: nofq-remap-sa-consistent from_sec=0x%x sa=0x%x -> peer_sa=0x%x compat_tx=0x%x\n",
				fq->fqid, sa_hint, peer_sa_hint,
				tx_ipsecsa_info->compat_tx_fq.fqid);
		}
	}

	if (!tx_ipsecsa_info || !tx_ipsecsa_info->compat_tx_fq.fqid) {
		u64 nofq_cnt = atomic64_inc_return(&ipsec_nofq_fallback_cnt);
		ipsec_note_nofq_remap_reason(remap_reason);
		ipsec_log_nofq_snapshot(ipsecsa_info, tx_ipsecsa_info, fq, dq,
				&compat_fd, sa_hint, peer_sa_hint, remap_reason, nofq_cnt);

		if (ipsec_compat_reinject_mode) {
			atomic64_inc(&ipsec_reinject_attempt_cnt);

			if (!ipsecinfo.ofport_reinject_fq_valid) {
				atomic64_inc(&ipsec_reinject_skipped_missing_fq_cnt);
				ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
						"skip-missing-fq", -ENODEV);
			} else if (compat_fd.format != qm_fd_contig ||
					!qm_fd_addr(&compat_fd) ||
					!compat_fd.length20) {
				atomic64_inc(&ipsec_reinject_skipped_noncontig_cnt);
				ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
						"skip-noncontig", -EINVAL);
			} else {
				const uint8_t *pkt = (const uint8_t *)phys_to_virt(
						(uint64_t)qm_fd_addr(&compat_fd));
				if (pkt)
					ipsec_dbg_log_pkt_identity("reinject-attempt",
							fq->fqid, compat_fd.status,
							pkt + compat_fd.offset,
							compat_fd.length20, sa_hint);
				err = qman_enqueue(&ipsecinfo.ofport_reinject_fq.fq_base,
						&compat_fd, 0);
				if (!err) {
					atomic64_inc(&ipsec_reinject_ok_cnt);
					printk_ratelimited(KERN_INFO
						"IPSEC_REINJECT: stage=enqueue-ok from_sec=0x%x reinject_fq=0x%x len=%u off=%u fmt=%u st=0x%08x sa=0x%x remap_reason=%u\n",
						fq->fqid, ipsecinfo.ofport_reinject_fq.fqid,
						compat_fd.length20, compat_fd.offset,
						compat_fd.format, compat_fd.status,
						sa_hint, remap_reason);
					ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
							"enqueue-ok", 0);
					goto release_reclaim;
				}

				atomic64_inc(&ipsec_reinject_err_cnt);
				atomic64_inc(&ipsec_reinject_status_bucket_cnt[
					ipsec_status_bucket(compat_fd.status)]);
				printk_ratelimited(KERN_ERR
					"IPSEC_REINJECT: stage=enqueue-failed from_sec=0x%x reinject_fq=0x%x err=%d len=%u off=%u fmt=%u st=0x%08x sa=0x%x remap_reason=%u\n",
					fq->fqid, ipsecinfo.ofport_reinject_fq.fqid, err,
					compat_fd.length20, compat_fd.offset,
					compat_fd.format, compat_fd.status,
					sa_hint, remap_reason);
				ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
						"enqueue-failed", err);
			}
		}

		/* No compat TX mapping exists for this from-SEC queue (commonly
		 * inbound return). For direct frames, hand off to the standard
		 * exception path instead of dropping. */
		if (dq->fd.format != qm_fd_compound) {
			atomic64_inc(&ipsec_reinject_fallback_excp_cnt);
			ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
					"fallback-exception", 0);
			return ipsec_exception_pkt_handler(qm, fq, dq);
		}

		atomic64_inc(&ipsec_reinject_fallback_drop_cnt);
		ipsec_log_reinject_snapshot(fq, &compat_fd, sa_hint,
				"fallback-drop-compound", 0);
		ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
		goto release_reclaim;
	}

	/* CAAM output can still carry an inner ETH(+VLAN) header.
	 * Strip it so PPP payload starts at outer IP/ESP before prepend. */
	inner_l2_len = ipsec_compat_inner_l2_len(&compat_fd);
	if (inner_l2_len && compat_fd.length20 > inner_l2_len) {
		old_off = compat_fd.offset;
		old_len = compat_fd.length20;
		compat_fd.offset += inner_l2_len;
		compat_fd.length20 -= inner_l2_len;
	}

	if (tx_ipsecsa_info->compat_lan_dev &&
	    compat_fd.format == qm_fd_contig && qm_fd_addr(&compat_fd)) {
		buf = (uint8_t *)phys_to_virt(
				(uint64_t)qm_fd_addr(&compat_fd));
		if (buf && ipsec_compat_should_learning_punt(
		    buf + compat_fd.offset, compat_fd.length20)) {
			err = ipsec_compat_punt_inner_to_stack(ipsecsa_info,
					&compat_fd);
			if (!err)
				goto release_reclaim;

			atomic64_inc(&ipsec_learning_skip_cnt);
		}
	}

	/* For inbound LAN delivery: resolve destination MAC via ARP cache.
	 * The L2 template has src=LAN-MAC but dst=zeros from SA creation.
	 * Patch dst MAC from the neighbour table before prepend. */
	if (tx_ipsecsa_info->compat_lan_dev &&
	    compat_fd.format == qm_fd_contig &&
	    tx_ipsecsa_info->compat_l2_len >= 14) {
		const uint8_t *ip_hdr;
		__be32 inner_dst;
		struct neighbour *neigh;
		struct net_device *ndev;
		uint8_t resolved_ha[ETH_ALEN];
		bool have_resolved_ha = false;
		struct net_device *resolved_dev = NULL;

		ipsec_compat_mss_clamp(&compat_fd,
				tx_ipsecsa_info->compat_mtu);

		buf = (uint8_t *)phys_to_virt(
				(uint64_t)qm_fd_addr(&compat_fd));
		ip_hdr = buf + compat_fd.offset;
		if (compat_fd.length20 >= 20 &&
		    (ip_hdr[0] >> 4) == 4) {
			memcpy(&inner_dst, ip_hdr + 16, sizeof(inner_dst));
			rcu_read_lock();
			neigh = neigh_lookup(&arp_tbl, &inner_dst,
					tx_ipsecsa_info->compat_lan_dev);
			if (neigh && (neigh->nud_state & NUD_VALID)) {
				memcpy(resolved_ha, neigh->ha, ETH_ALEN);
				have_resolved_ha = true;
				memcpy(tx_ipsecsa_info->compat_l2_hdr,
					neigh->ha, ETH_ALEN);
				resolved_dev = tx_ipsecsa_info->compat_lan_dev;
				neigh_release(neigh);
			} else {
				if (neigh)
					neigh_release(neigh);

				/* The SA can be bound to a physical LAN port while
				 * neighbour entries live on bridge/VLAN devices.
				 * Probe other UP ethernet devices before fallback. */
				for_each_netdev_rcu(&init_net, ndev) {
					if (ndev == tx_ipsecsa_info->compat_lan_dev)
						continue;
					if (!(ndev->flags & IFF_UP))
						continue;
					if (ndev->type != ARPHRD_ETHER)
						continue;

					neigh = neigh_lookup(&arp_tbl, &inner_dst, ndev);
					if (!neigh)
						continue;
					if (!(neigh->nud_state & NUD_VALID)) {
						neigh_release(neigh);
						continue;
					}

					memcpy(resolved_ha, neigh->ha, ETH_ALEN);
					have_resolved_ha = true;
					memcpy(tx_ipsecsa_info->compat_l2_hdr,
						neigh->ha, ETH_ALEN);
					resolved_dev = ndev;
					neigh_release(neigh);
					break;
				}
			}
			rcu_read_unlock();

				if (resolved_dev && have_resolved_ha) {
					struct net_device *wire_dev = resolved_dev;
					struct net_device *tx_dev = resolved_dev;
					struct net_device *src_dev = resolved_dev;
					uint16_t vlan_id = 0;
					bool has_vlan = false;
					uint8_t l2_len;

					if (netif_is_bridge_master(resolved_dev)) {
						if (!rtnl_trylock()) {
							if (ipsec_lan_neigh_diag_enable)
								printk_ratelimited(KERN_INFO
									"IPSEC_COMPAT: lan-fdb-skip dst=%pI4 bridge=%s reason=rtnl-busy\n",
									&inner_dst, resolved_dev->name);
							return ipsec_exception_pkt_handler(qm, fq, dq);
						}
						wire_dev = br_fdb_find_port(resolved_dev,
								resolved_ha, 0);
						rtnl_unlock();
						if (!wire_dev) {
							if (ipsec_lan_neigh_diag_enable)
								printk_ratelimited(KERN_INFO
									"IPSEC_COMPAT: lan-fdb-miss dst=%pI4 bridge=%s\n",
									&inner_dst, resolved_dev->name);
							return ipsec_exception_pkt_handler(qm, fq, dq);
						}
						src_dev = resolved_dev;
					}

					if (is_vlan_dev(wire_dev)) {
						vlan_id = vlan_dev_vlan_id(wire_dev);
						tx_dev = vlan_dev_real_dev(wire_dev);
						has_vlan = true;
					} else {
						tx_dev = wire_dev;
					}

					if (!tx_dev || tx_dev->type != ARPHRD_ETHER) {
						if (ipsec_lan_neigh_diag_enable)
							printk_ratelimited(KERN_INFO
								"IPSEC_COMPAT: lan-txdev-invalid dst=%pI4 resolved=%s wire=%s tx=%s\n",
								&inner_dst, resolved_dev->name,
								wire_dev ? wire_dev->name : "-",
								tx_dev ? tx_dev->name : "-");
						return ipsec_exception_pkt_handler(qm, fq, dq);
					}

					memcpy(tx_ipsecsa_info->compat_l2_hdr,
						resolved_ha, ETH_ALEN);
					memcpy(tx_ipsecsa_info->compat_l2_hdr + ETH_ALEN,
						src_dev->dev_addr, ETH_ALEN);
					l2_len = 12;
					if (has_vlan) {
						tx_ipsecsa_info->compat_l2_hdr[l2_len++] = 0x81;
						tx_ipsecsa_info->compat_l2_hdr[l2_len++] = 0x00;
						tx_ipsecsa_info->compat_l2_hdr[l2_len++] = (vlan_id >> 8) & 0xff;
						tx_ipsecsa_info->compat_l2_hdr[l2_len++] = vlan_id & 0xff;
					}
					tx_ipsecsa_info->compat_l2_hdr[l2_len++] = 0x08;
					tx_ipsecsa_info->compat_l2_hdr[l2_len++] = 0x00;
					tx_ipsecsa_info->compat_l2_len = l2_len;
					tx_ipsecsa_info->compat_l2_pppoe_off = 0;
					runtime_tx_dev = tx_dev;

					if (ipsec_lan_neigh_diag_enable &&
					    resolved_dev != tx_ipsecsa_info->compat_lan_dev) {
						printk_ratelimited(KERN_INFO
							"IPSEC_COMPAT: lan-neigh-fallback dst=%pI4 bound=%s resolved=%s tx=%s\n",
							&inner_dst,
							tx_ipsecsa_info->compat_lan_dev->name,
							resolved_dev->name,
							runtime_tx_dev->name);
					}
				} else {
					if (ipsec_lan_neigh_diag_enable)
						printk_ratelimited(KERN_INFO
							"IPSEC_COMPAT: lan-neigh-miss dst=%pI4 dev=%s\n",
							&inner_dst,
							tx_ipsecsa_info->compat_lan_dev->name);
					return ipsec_exception_pkt_handler(qm, fq, dq);
				}
			} else {
				if (ipsec_lan_neigh_diag_enable)
					printk_ratelimited(KERN_INFO
						"IPSEC_COMPAT: lan-non-ipv4 len=%u\n",
						compat_fd.length20);
				return ipsec_exception_pkt_handler(qm, fq, dq);
			}
		}

	/* Prepend L2 header (ETH + optional VLAN + optional PPPoE) to the
	 * CAAM output.  CAAM returns bare IP/ESP; FMAN TX needs a complete
	 * Ethernet frame.  The template was stored at SA creation time. */
	if (tx_ipsecsa_info->compat_l2_len &&
	    compat_fd.format == qm_fd_contig &&
	    compat_fd.offset < tx_ipsecsa_info->compat_l2_len) {
		err = ipsec_compat_rebuild_with_headroom(&compat_fd,
			tx_ipsecsa_info->compat_l2_len + 16);
		if (err) {
			printk_ratelimited(KERN_ERR
				"IPSEC_COMPAT: headroom-rebuild-failed from_sec=0x%x len=%u off=%u need=%u err=%d\n",
				fq->fqid, compat_fd.length20, compat_fd.offset,
				tx_ipsecsa_info->compat_l2_len, err);
			ipsec_log_bp_snapshot("headroom-rebuild-failed");
			goto release_reclaim;
		}
	}

	if (tx_ipsecsa_info->compat_l2_len &&
	    compat_fd.format == qm_fd_contig &&
	    compat_fd.offset >= tx_ipsecsa_info->compat_l2_len) {
		buf = (uint8_t *)phys_to_virt(
				(uint64_t)qm_fd_addr(&compat_fd));
		compat_fd.offset -= tx_ipsecsa_info->compat_l2_len;
		memcpy(buf + compat_fd.offset,
			tx_ipsecsa_info->compat_l2_hdr,
			tx_ipsecsa_info->compat_l2_len);
		compat_fd.length20 += tx_ipsecsa_info->compat_l2_len;

		/* Patch PPPoE payload length if present.
		 * PPPoE payload = PPP protocol (2) + IP/ESP data. */
		if (tx_ipsecsa_info->compat_l2_pppoe_off) {
			pppoe_payload_len = compat_fd.length20 -
				tx_ipsecsa_info->compat_l2_pppoe_off - 2;
			buf[compat_fd.offset +
				tx_ipsecsa_info->compat_l2_pppoe_off] =
				(pppoe_payload_len >> 8) & 0xff;
			buf[compat_fd.offset +
				tx_ipsecsa_info->compat_l2_pppoe_off + 1] =
				pppoe_payload_len & 0xff;
		}
	}

	/* Clear the skb-pointer slot at the start of the output buffer.
	 * FMAN TX confirmation (via the port's default tx-conf FQ) calls
	 * _dpa_cleanup_tx_fd() which reads *(phys_to_virt(fd_addr)) as
	 * an skb pointer.  The CAAM output buffer has stale data there,
	 * not a valid skb — writing NULL prevents the confirmation
	 * handler from dereferencing garbage. */
	if (compat_fd.format == qm_fd_contig &&
	    qm_fd_addr(&compat_fd) &&
	    compat_fd.offset >= sizeof(struct sk_buff *))
		*(struct sk_buff **)phys_to_virt(
				(uint64_t)qm_fd_addr(&compat_fd)) = NULL;

	/* Do not tag compat TX in fd->status/cmd.  For TX frames this word is
	 * the FMAN command field; bit 31 is FM_FD_CMD_FCO, not free debug
	 * state.  Setting it here overrides FQ context on the final egress
	 * handoff and can prevent the encrypted frame from reaching the wire.
	 */
	{
		u64 tag_cnt;

		tag_cnt = atomic64_inc_return(&ipsec_compat_posttx_tag_cnt);
		if (ipsec_compat_diag_enable &&
		    ipsec_compat_diag_should_log(tag_cnt))
			printk_ratelimited(KERN_INFO
				"IPSEC_COMPAT_DIAG: stage=posttx-ready from_sec=0x%x compat_tx=0x%x ch=0x%x wq=%u seq=%llu st=0x%08x len=%u off=%u fmt=%u\n",
				fq->fqid, tx_ipsecsa_info->compat_tx_fq.fqid,
				tx_ipsecsa_info->compat_tx_channel,
				tx_ipsecsa_info->compat_tx_wq,
				(unsigned long long)tag_cnt, compat_fd.status,
				compat_fd.length20, compat_fd.offset, compat_fd.format);
	}

	err = ipsec_compat_dma_sync_fd(&compat_fd, DMA_BIDIRECTIONAL, true);
	if (err) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: dma-sync-device-failed from_sec=0x%x compat_tx=0x%x len=%u off=%u fmt=%u bpid=%u err=%d\n",
			fq->fqid, tx_ipsecsa_info->compat_tx_fq.fqid,
			compat_fd.length20, compat_fd.offset, compat_fd.format,
			compat_fd.bpid, err);
		ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
		goto release_reclaim;
	}

	if (!runtime_tx_dev && ipsec_compat_txdev_prefer &&
	    tx_ipsecsa_info->compat_tx_dev) {
		runtime_tx_dev = tx_ipsecsa_info->compat_tx_dev;
		runtime_tx_dev_is_preferred = true;
	}

	if (runtime_tx_dev) {
		struct net_device *tx_dev = runtime_tx_dev;

		/* Runtime LAN resolution may select a bridge/VLAN backing device that
		 * was not known when CMM programmed the SA.  For WAN, prefer the
		 * SA's physical egress netdev when available so post-SEC frames use
		 * the same DPAA egress queueing path as normal TX. */
		err = dpaa_ipsec_xmit_compat_fd(tx_dev,
				&compat_fd);
		if (!err) {
			if (ipsec_fd_from_ipsec_bp(&compat_fd))
				atomic64_inc(&ipsec_bp_txdev_enqueue_ok_cnt);
			if (ipsec_compat_diag_enable &&
			    ipsec_compat_diag_should_log(
				    atomic64_read(&ipsec_compat_posttx_tag_cnt)))
				printk_ratelimited(KERN_INFO
					"IPSEC_COMPAT_DIAG: stage=txdev-enqueue-ok from_sec=0x%x dev=%s len=%u off=%u fmt=%u st=0x%08x\n",
					fq->fqid,
					tx_dev->name,
					compat_fd.length20, compat_fd.offset,
					compat_fd.format, compat_fd.status);
			goto release_reclaim;
		}
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: txdev-enqueue-failed from_sec=0x%x dev=%s err=%d fallback_compat_tx=0x%x len=%u off=%u fmt=%u st=0x%08x\n",
			fq->fqid, tx_dev->name, err, tx_ipsecsa_info->compat_tx_fq.fqid,
			compat_fd.length20, compat_fd.offset,
			compat_fd.format, compat_fd.status);
		if (!runtime_tx_dev_is_preferred) {
			ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
			goto release_reclaim;
		}
	}

	err = qman_enqueue(&tx_ipsecsa_info->compat_tx_fq.fq_base, &compat_fd, 0);
	if (err) {
		printk_ratelimited(KERN_ERR
			"IPSEC_COMPAT: tx-enqueue-failed from_sec=0x%x compat_tx=0x%x err=%d len=%u bpid=%u off=%u fmt=%u st=0x%08x\n",
			fq->fqid, tx_ipsecsa_info->compat_tx_fq.fqid, err,
			compat_fd.length20, compat_fd.bpid, compat_fd.offset,
			compat_fd.format, compat_fd.status);
		if (ipsec_fd_from_ipsec_bp(&compat_fd))
			atomic64_inc(&ipsec_bp_enqueue_fail_cnt);
		ipsec_log_bp_snapshot("compat-enqueue-failed");
		ipsec_bp_release_fd(&compat_fd, &ipsec_bp_release_compat_cnt);
		} else {
			if (ipsec_fd_from_ipsec_bp(&compat_fd))
				atomic64_inc(&ipsec_bp_compat_enqueue_ok_cnt);
			if (ipsec_compat_diag_enable && ipsec_compat_diag_should_log(
					atomic64_read(&ipsec_compat_posttx_tag_cnt)))
				printk_ratelimited(KERN_INFO
					"IPSEC_COMPAT_DIAG: stage=tx-enqueue-ok from_sec=0x%x compat_tx=0x%x len=%u off=%u fmt=%u st=0x%08x\n",
				fq->fqid, tx_ipsecsa_info->compat_tx_fq.fqid,
				compat_fd.length20, compat_fd.offset,
				compat_fd.format, compat_fd.status);
		ipsec_dbg_log_fq_runtime("compat-tx-post",
				&tx_ipsecsa_info->compat_tx_fq.fq_base,
				peer_sa_hint ? peer_sa_hint : sa_hint);
	}

release_reclaim:
	if (dq->fd.format == qm_fd_compound) {
		err = dpaa_ipsec_release_compound_reclaim_ctx(&dq->fd);
		if (err) {
			printk_ratelimited(KERN_ERR
				"IPSEC_COMPAT: reclaim-release-failed fqid=0x%x err=%d addr=0x%llx bpid=%u\n",
				fq->fqid, err, (unsigned long long)qm_fd_addr(&dq->fd),
				dq->fd.bpid);
		}
	}

	return qman_cb_dqrr_consume;
}

static uint32_t ipsec_exception_pkt_cnt;
void print_ipsec_exception_pkt_cnt(void)
{
	printk(KERN_INFO "ipsec_exception_pkt_cnt=%u\n", ipsec_exception_pkt_cnt);
	printk(KERN_INFO
		"ipsec_learning_punt=%llu seen=%llu skip=%llu enabled=%u\n",
		(unsigned long long)atomic64_read(&ipsec_learning_punt_cnt),
		(unsigned long long)atomic64_read(&ipsec_learning_seen_cnt),
		(unsigned long long)atomic64_read(&ipsec_learning_skip_cnt),
		ipsec_compat_learning_punt ? 1 : 0);
}


void *cdx_get_xfrm_state_of_sa(void *dev, uint16_t handle)
{
	struct xfrm_state *x;
	struct net_device *netdev = (struct net_device *)dev;

	if ((x = xfrm_state_lookup_byhandle(dev_net(netdev), handle)) == NULL)
	{
		DPAIPSEC_ERROR("(%s)xfrm_state not found for handle %x\n",
				__FUNCTION__, handle);
		return NULL;
	}
	return x;
}

void cdx_dpa_ipsec_xfrm_state_dec_ref_cnt(void *xfrm_state)
{
	if (xfrm_state)
	{
		xfrm_state_put((struct xfrm_state *)xfrm_state);
	}
	return;
}

#ifdef UNIQUE_IPSEC_CP_FQID
extern 	struct net_device *get_netdev_of_SA_by_fqid(uint32_t fqid,
		uint16_t *sagd_pkt);
#endif /* UNIQUE_IPSEC_CP_FQID */
static enum qman_cb_dqrr_result ipsec_exception_pkt_handler(struct qman_portal *qm,
		struct qman_fq *fq,
		const struct qm_dqrr_entry *dq)
{
	uint8_t *ptr;
	uint32_t len;
	struct sk_buff *skb = NULL;
	struct net_device *net_dev;
	struct dpa_bp *dpa_bp;
	struct dpa_priv_s               *priv;
	struct dpa_percpu_priv_s        *percpu_priv;
	unsigned short eth_type;
	unsigned short sagd_pkt;
	struct sec_path *sp;
	struct xfrm_state *x = NULL;
	struct timespec64 ktime;
#ifdef DPA_IPSEC_DEBUG1
	unsigned short sagd; 
#endif
	bool use_gro;
	int *percpu_bp_cnt;
	unsigned short protocol;
	int no_l2_itf_dev;
	gro_result_t gro_result;
	const struct qman_portal_config *pc;
	struct dpa_napi_portal *np;;
	bool fd_released = false;
	/* check SEC errors here */
#ifdef DPA_IPSEC_DEBUG1
	DPAIPSEC_INFO("%s::fqid %x(%d), bpid %d, len %d, \n offset %d sts %08x, cnt %d\n", __FUNCTION__,
			dq->fqid, dq->fqid, dq->fd.bpid, dq->fd.length20,
			dq->fd.offset,dq->fd.status, ipsec_exception_pkt_cnt);

	/* for debugging */
	ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr));
	printk("Dispalying parse result:\n");
	display_buff_data(ptr, 0x70);
#endif /* DPA_IPSEC_DEBUG1 */

	/* len = (dq->fd.length20 - 4); */
	len = dq->fd.length20;
	ptr = (uint8_t *)(phys_to_virt((uint64_t)dq->fd.addr) + dq->fd.offset);
	atomic64_inc(&ipsec_excp_status_total_cnt);
	atomic64_inc(&ipsec_excp_status_bucket_cnt[
		ipsec_status_bucket(dq->fd.status)]);
	if (dq->fd.status)
		atomic64_inc(&ipsec_excp_status_nonzero_cnt);
	ipsec_log_signal_snapshot("exception");
	ipsec_dbg_log_pkt_identity("excp-entry", dq->fqid, dq->fd.status, ptr,
			len, 0);
	ipsec_dbg_log_hex_sample("excp-entry", dq->fqid, ptr, len, 0);
#ifdef DPA_IPSEC_DEBUG1
	/* for debugging printing packet*/
	if (len >= 64)
	{
		display_buff_data(ptr, 64);
	}
	else
	{
		display_buff_data(ptr, len);
	} 
#endif /*DPA_IPSEC_DEBUG1 */
	/* 
	 * extract sagd from the end of packet. That sagd is used for two purpose.
	 * 1) After the Sec processes since a new buffer is used for decrypted input 
	 *    packets, the port information on which the orginal packet reached is lost.
	 *    When giving the packet to the stack this information is required. Earlier
	 *    we used a hardcoded logic of identifying one of the port as WAN port  by name
	 *    or adding ESP table to only one of the port in configuration file, and hard code 
	 *    that port as incoming ipsec packet before submitting the packet. With this change
	 *    now we store the incoing interface netdev structure in SA structure itself and 
	 *    extract incoming for by using the sagd copied into the end of packet.
	 *  2) We need dpa_priv pointer from the net_dev for calling dpaa_eth_napi_schedule ()
	 *     We do not want the complete pkt processing happen in irq context. 
	 *     dpaa_eth_napi_schedule () schdule a soft irq and ensure this function is called
	 *     again soft irq. 
	 *  3) We need to find xrfm state by using this sagd and put that into skb
	 *     beofe submitting into stack. If the there is a coresponding inbound 
	 *     ipsec policy only this packet will be allowed otherwise stack will
	 *     drop the packet.   
	 */
	dpa_bp = dpa_bpid2pool(dq->fd.bpid);
#ifdef UNIQUE_IPSEC_CP_FQID
	net_dev = get_netdev_of_SA_by_fqid(dq->fqid, &sagd_pkt);
#else
	memcpy(&sagd_pkt,(ptr+(len-2)),2);
	net_dev = (struct net_device *) M_ipsec_get_sa_netdev(sagd_pkt );
#endif /* UNIQUE_IPSEC_CP_FQID */
	ipsec_dbg_log_pkt_identity("excp-sa", dq->fqid, dq->fd.status, ptr, len,
			sagd_pkt);
	ipsec_dbg_log_status("excp", sagd_pkt, dq->fd.status);
	ipsec_dbg_log_fq_runtime("excp-fq", fq, sagd_pkt);

	if(!net_dev ){
		uint32_t spi = 0;
		U16 recovered_sagd = 0;
		int spi_parse_rc = -1;
		int sagd_lookup_rc = -1;

		/* Direct from-SEC return frames may carry invalid trailing SAGD.
		 * Recover inbound SAGD from outer ESP SPI as a bounded fallback. */
		spi_parse_rc = ipsec_extract_esp_spi_from_l2(ptr, len, &spi);
		if (spi_parse_rc == 0) {
			sagd_lookup_rc = cdx_ipsec_handle_get_inbound_sagd(spi,
					&recovered_sagd);
		}

		if (spi_parse_rc == 0 &&
		    sagd_lookup_rc == NO_ERR &&
		    recovered_sagd) {
			sagd_pkt = recovered_sagd;
			net_dev = (struct net_device *)M_ipsec_get_sa_netdev(sagd_pkt);
		} else {
			/* Some direct return frames are already post-decrypt inner
			 * IP (for example ICMP) and carry no outer ESP header.
			 * In that shape SPI parsing is impossible; fall back to
			 * inbound SA cache as a bounded recovery path. */
			if (spi_parse_rc && !recovered_sagd &&
			    cdx_ipsec_handle_get_inbound_sagd(0, &recovered_sagd) == NO_ERR &&
			    recovered_sagd) {
				sagd_pkt = recovered_sagd;
				net_dev = (struct net_device *)M_ipsec_get_sa_netdev(sagd_pkt);
			}
		}

	}

	if(!net_dev ){
		printk_ratelimited(KERN_INFO "IPSEC_EXCP: drop reason=no-netdev sagd=%u\n", sagd_pkt);
#ifdef DPA_IPSEC_DEBUG
		DPAIPSEC_INFO("%s:: Could not find or delete mark set in inbound SA, droping pkt \n",__func__);
#endif
		goto rel_fd;
	}

	use_gro = !!(net_dev->features & NETIF_F_GRO);
	if ((x = xfrm_state_lookup_byhandle(dev_net(net_dev), sagd_pkt )) == NULL)
	{
		printk_ratelimited(KERN_INFO "IPSEC_EXCP: drop reason=no-xfrm sagd=%u dev=%s\n",
			sagd_pkt, net_dev->name);
#ifdef DPA_IPSEC_DEBUG
		DPAIPSEC_INFO("%s(%d) xfrm_state not found. Dropping pkt\n", __func__,__LINE__);
#endif
		goto rel_fd;
	}

	priv = netdev_priv(net_dev); 
	DPA_BUG_ON(!priv);
	/* IRQ handler, non-migratable; safe to use raw_cpu_ptr here */
	percpu_priv = raw_cpu_ptr(priv->percpu_priv);
#ifndef CONFIG_FSL_ASK_QMAN_PORTAL_NAPI
	if (unlikely(dpaa_eth_napi_schedule(percpu_priv, qm)))
	{
		DPAIPSEC_ERROR("%s(%d) dpaa_eth_napi_schedule failed\n",
				__FUNCTION__,__LINE__);
		xfrm_state_put(x);
		return qman_cb_dqrr_stop;
	}
#endif /* CONFIG_FSL_ASK_QMAN_PORTAL_NAPI */

	no_l2_itf_dev = vwd_is_no_l2_itf_device(net_dev);
	ipsec_exception_pkt_cnt++;
	percpu_bp_cnt =	raw_cpu_ptr(priv->percpu_count);
	/*  When V6 SA is applied to v4 packet and vice versa, since ether header is
	 *  copied from input packet, it will be wrong. Below logic is added just
	 *  make the required correction in this case.
	 */
	memcpy(&eth_type,(ptr+12),2);
	if((eth_type == htons(ETHERTYPE_IPV4)) && ((ptr[14] & 0xF0) == 0x60))
	{
		ptr[12]= 0x86;
		ptr[13] = 0xDD;
	}
	if((eth_type == htons(ETHERTYPE_IPV6)) && ((ptr[14] & 0xF0) == 0x40))
	{
		ptr[12]= 0x08;
		ptr[13] = 0x00;
	}
	protocol =  *((unsigned short*) (ptr + 12));
#ifdef DPA_IPSEC_DEBUG1
	DPAIPSEC_INFO("%s::fqid %x(%d), bpid %d, len %d, offset %d netdev %p dev %s temp_dev =%s addr %llx sts %08x\n", __FUNCTION__,
			dq->fqid, dq->fqid, dq->fd.bpid, dq->fd.length20,
			dq->fd.offset, net_dev, net_dev->name,net_dev->name, (uint64_t)dq->fd.addr, dq->fd.status);
	DPAIPSEC_INFO(" sagd extracted from packet = %d \n",sagd_pkt);
	//display_buff_data(ptr, len);	
	//goto rel_fd;
#endif

	if (ipsec_excp_copy_skb && likely(dq->fd.format == qm_fd_contig)) {
		skb = netdev_alloc_skb_ip_align(net_dev, len);
		if (unlikely(!skb)) {
			printk_ratelimited(KERN_INFO
				"IPSEC_EXCP: drop reason=alloc-skb-fail len=%u dev=%s\n",
				len, net_dev->name);
			goto rel_fd;
		}
		skb_put_data(skb, ptr, len);
		dpa_fd_release(net_dev, &dq->fd);
		fd_released = true;
	} else if (likely(dq->fd.format == qm_fd_contig)) {
		skb = contig_fd_to_skb(priv, &dq->fd, &use_gro, false);
	} else {
		skb = sg_fd_to_skb(priv, &dq->fd, &use_gro, percpu_bp_cnt, false);
		percpu_priv->rx_sg++;
	}

	if (!fd_released) {
		(*percpu_bp_cnt)--;
		if (unlikely(dpaa_eth_refill_bpools(dpa_bp, percpu_bp_cnt,
				THRESHOLD_IPSEC_BPOOL_REFILL))) {
			/* if we cant refill give this up */
			goto pkt_drop;
		}
	}



	skb->dev = net_dev;
//	skb_reset_tail_pointer(skb);
	if (no_l2_itf_dev)
	{
#ifndef UNIT_TEST
		skb_pull(skb, ETH_HLEN);
		skb_reset_network_header(skb);
		skb->mac_len = 0;
		skb->protocol = protocol;
#else
		skb->protocol = eth_type_trans(skb, net_dev);
#endif
	}
	else
	{
		skb->protocol = eth_type_trans(skb, net_dev);
	}

	/* Exception-return packets can carry stale checksum offload metadata.
	 * Normalize before handing to stack to avoid path-specific checksum
	 * validation ambiguity on reinjected traffic. */
	skb->ip_summed = CHECKSUM_NONE;
	skb->csum_level = 0;
	skb->encapsulation = 0;

	sp = skb_ext_add(skb, SKB_EXT_SEC_PATH);

	if (!sp)
	{
		DPAIPSEC_ERROR("No sec_path. Dropping pkt\n");
		goto pkt_drop;
	}

	sp->xvec[0] = x;

	if (!x->curlft.use_time)
	{
		ktime_get_real_ts64(&ktime);
		x->curlft.use_time = (unsigned long)ktime.tv_sec;
	}
	sp->len = 1;
	x = NULL;

#ifdef DPA_IPSEC_DEBUG1
	DPAIPSEC_INFO("%s::len %d ipsec_exception_pkt_cnt %d\n", 
			__FUNCTION__, skb->len, ipsec_exception_pkt_cnt);
#endif
	/* netif_receive_skb(skb); */
	if (use_gro)
	{
		pc = qman_p_get_portal_config(qm);
		np = &percpu_priv->np[pc->index];

		np->p = qm;
		gro_result = napi_gro_receive(&np->napi, skb);
		(void)gro_result; /* Result no longer checked - GRO_DROP removed in kernel 6.12 */

	}
	else {
		int rx_rc = netif_receive_skb(skb);
		if (rx_rc == NET_RX_DROP) /* (netif_rx(skb) != NET_RX_SUCCESS) */
			DPAIPSEC_ERROR("%s::packet dropped\n", __FUNCTION__);
	}
	return qman_cb_dqrr_consume;
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
pkt_drop:
#endif
	if (skb) 
		dev_kfree_skb(skb);
rel_fd:
	if (x)
		xfrm_state_put(x);
	if (!fd_released)
		dpa_fd_release(net_dev, &dq->fd);
	return qman_cb_dqrr_consume;
}


#define PORTID_SHIFT_VAL 8

static int cdx_find_ipsec_pcd_fqinfo(int fqid, struct ipsec_info *info)
{
	struct dpa_fq *list = info->ipsec_exception_fq;
	while (list)
	{
		if (list->fqid == fqid)
			return 0;
		list = (struct dpa_fq *)list->list.next;
	}
	return -1;
}

static void ipsec_addfq_to_exceptionfq_list(struct dpa_fq *frameq,
		struct ipsec_info *info)
{
	frameq->list.next = (struct list_head *)info->ipsec_exception_fq;
	info->ipsec_exception_fq = frameq;
}

static void ipsec_delfq_from_exceptionfq_list(uint32_t fqid,
		struct ipsec_info *info)
{
	struct dpa_fq *prev, *list = info->ipsec_exception_fq;
	prev = list;
	while (list)
	{
		if (list->fqid == fqid)
		{
			if (prev == list)
			{
				info->ipsec_exception_fq = (struct dpa_fq *)list->list.next;
				return;
			}
			prev->list.next = list->list.next;
			return;
		}
		prev = list;
		list = (struct dpa_fq *)list->list.next;
	}
	return;
}

static int create_ipsec_pcd_fqs(struct ipsec_info *info, uint32_t schedule)
{
	struct dpa_fq *dpa_fq;
	uint32_t fqbase;
	uint32_t fqcount;
	uint32_t portid;
	uint32_t ii,jj;
	uint32_t portal_channel[NR_CPUS];
	uint32_t num_portals, max_dist = 0;
	uint32_t next_portal_ch_idx;
	const cpumask_t *affine_cpus;
	struct qman_fq *fq;
	struct qm_mcc_initfq opts;
	struct dpa_iface_info *oh_iface_info;

	//get cpu portal channel info
	num_portals = 0;
	next_portal_ch_idx = 0;
	affine_cpus = qman_affine_cpus();
	/* get channel used by portals affined to each cpu */
	for_each_cpu(ii, affine_cpus) {
		portal_channel[num_portals] = qman_affine_channel(ii);
		num_portals++;
	}
	if (!num_portals) {
		DPAIPSEC_ERROR("%s::unable to get affined portal info\n",
				__FUNCTION__);
		return -1;
	}

#ifdef DPA_IPSEC_DEBUG
	DPAIPSEC_INFO("%s::num_portals %d ::", __FUNCTION__, num_portals);
	for (ii = 0; ii < num_portals; ii++)
		DPAIPSEC_INFO("%d ", portal_channel[ii]);
	DPAIPSEC_INFO("\n");
#endif

	if (get_ofport_max_dist(IPSEC_FMAN_IDX, info->ofport_handle, &max_dist) < 0)
	{
		DPAIPSEC_ERROR("%s::unable to get distributions for oh port\n", __FUNCTION__);
		return -1;
	}

	DPAIPSEC_INFO("%s::max_dist : %d\n", __FUNCTION__, max_dist) ;

	/* create all FQs */
	info->expt_fq_count = 0;
	/* get port id required for FQ creation */
	if (get_ofport_portid(IPSEC_FMAN_IDX, info->ofport_handle, &portid)) {
		DPAIPSEC_ERROR("%s::err getting of port id\n", __FUNCTION__) ;
		return -1;
	}

	if ((oh_iface_info = dpa_get_ohifinfo_by_portid(portid)) == NULL) {
		DPAIPSEC_ERROR("%s::err getting oh iface info of port id %u\n", __FUNCTION__, portid) ;
		return -1;
	}
	if (oh_iface_info->pcd_proc_entry == NULL)
	{
		DPAIPSEC_ERROR("%s()::%d OH iface pcd proc entry is invalid:\n", __func__, __LINE__);
		return -1;
	}

	for (jj = 0; jj < max_dist; jj++)
	{
		/* get FQbase and count used for each distribution
			 with scheme sharing this is the only distribution that will be used */

		if (get_oh_port_pcd_fqinfo(IPSEC_FMAN_IDX, info->ofport_handle,
					jj , &fqbase, &fqcount)) {
			DPAIPSEC_ERROR("%s::err getting pcd fqinfo for dist %d\n",
					__FUNCTION__,jj) ;
			return FAILURE;
		}

		/* add port id into FQID */
		fqbase |= (portid << PORTID_SHIFT_VAL);

		DPAIPSEC_INFO("%s::pcd FQ base for portid %d and  distribution id(%d): %x(%d), count %d\n",
				__FUNCTION__, portid, jj, fqbase, fqbase, fqcount);

		for (ii = 0; ii < fqcount; ii++)
		{
			DPAIPSEC_INFO("%s(%d) calling cdx_find_ipsec_pcd_fqinfo (%x)\n",
					__FUNCTION__,__LINE__, fqbase);
			if (!cdx_find_ipsec_pcd_fqinfo(fqbase, info))
			{
				fqbase++;
				continue;
			}

			/* create FQ for exception packets from ipsec ofline  port */
			dpa_fq = kzalloc((sizeof(struct dpa_fq)), GFP_KERNEL);
			if (!dpa_fq) {
				DPAIPSEC_ERROR("%s::unable to alloc mem for dpa_fq\n", __FUNCTION__) ;
				return FAILURE;
			}

			/* set FQ parameters */
			/* use wan port as the device for this FQ */
			//dpa_fq->net_dev = net_dev;
			dpa_fq->fq_type = FQ_TYPE_RX_PCD;
			dpa_fq->fqid = fqbase;
			/* set call back function pointer */
			fq = &dpa_fq->fq_base;
			fq->cb.dqrr = ipsec_exception_pkt_handler;
			/* round robin channel like ethernet driver does */
			dpa_fq->channel = portal_channel[next_portal_ch_idx];
			if (next_portal_ch_idx == (num_portals - 1))
				next_portal_ch_idx = 0;
			else
				next_portal_ch_idx++;
			dpa_fq->wq = DEFA_WQ_ID;
			ipsec_addfq_to_exceptionfq_list(dpa_fq,info);
			/* set options similar to ethernet driver */
			memset(&opts, 0, sizeof(struct qm_mcc_initfq));
			opts.fqd.fq_ctrl = (QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_HOLDACTIVE);
			opts.fqd.context_a.stashing.exclusive =
				(QM_STASHING_EXCL_DATA | QM_STASHING_EXCL_ANNOTATION);
			opts.fqd.context_a.stashing.data_cl = NUM_PKT_DATA_LINES_IN_CACHE;
			opts.fqd.context_a.stashing.annotation_cl = NUM_ANN_LINES_IN_CACHE;
			/* create FQ */
			if (qman_create_fq(dpa_fq->fqid, 0, fq)) {
				DPAIPSEC_ERROR("%s::qman_create_fq failed for fqid %d\n",
						__FUNCTION__, dpa_fq->fqid);
				goto err_ret;
			}
			opts.fqid = dpa_fq->fqid;
			opts.count = 1;
			opts.fqd.dest.channel = dpa_fq->channel;
			opts.fqd.dest.wq = dpa_fq->wq;
			opts.we_mask = (QM_INITFQ_WE_DESTWQ | QM_INITFQ_WE_FQCTRL |
					QM_INITFQ_WE_CONTEXTB | QM_INITFQ_WE_CONTEXTA);
			if (schedule)
				schedule = QMAN_INITFQ_FLAG_SCHED;

			/* init FQ */
			if (qman_init_fq(fq, schedule, &opts)) {
				DPAIPSEC_ERROR("%s::qman_init_fq failed for fqid %d\n",
						__FUNCTION__, dpa_fq->fqid);
				qman_destroy_fq(fq, 0);
				goto err_ret;
			}
			cdx_create_type_fqid_info_in_procfs(fq, PCD_DIR, oh_iface_info->pcd_proc_entry, NULL);
#ifdef DPA_IPSEC_DEBUG
			DPAIPSEC_INFO("%s::created pcd fq %x(%d) for wlan packets "
					"channel 0x%x\n", __FUNCTION__,
					dpa_fq->fqid, dpa_fq->fqid, dpa_fq->channel);
#endif
			/* next FQ */
			fqbase++;
			info->expt_fq_count++;
		}
	}
	return SUCCESS;
err_ret:
	/* release FQs allocated so far and mem */
	return FAILURE;
}

static int create_ipsec_fqs(struct dpa_ipsec_sainfo *ipsecsa_info, uint32_t schedule, uint32_t handle)
{
	int32_t ii;
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	struct qm_mcc_initfq opts;
	int errno;
	uint32_t flags = 0;
	uint64_t addr;
#ifdef UNIQUE_IPSEC_CP_FQID
	uint32_t portal_channel[NR_CPUS];
	uint32_t num_portals;
	uint32_t next_portal_ch_idx;
	const cpumask_t *affine_cpus;
	uint32_t fqids_base;
#endif /* UNIQUE_IPSEC_CP_FQID */
	int to_sec_fq = 0;
	uint8_t sa_id_name[8]="";

	//get cpu portal channel info
#ifdef UNIQUE_IPSEC_CP_FQID
	num_portals = 0;
	next_portal_ch_idx = 0;
	affine_cpus = qman_affine_cpus();
	/* get channel used by portals affined to each cpu */
	for_each_cpu(ii, affine_cpus) {
		portal_channel[num_portals] = qman_affine_channel(ii);
		num_portals++;
		/* need only one channel for one frame queue */
		break;
	}
	if (!num_portals) {
		DPAIPSEC_ERROR("%s::unable to get affined portal info\n",
				__FUNCTION__);
		return -1;
	}

#ifdef DPA_IPSEC_DEBUG1
	DPAIPSEC_INFO("%s::num_portals %d ::", __FUNCTION__, num_portals);
	for (ii = 0; ii < num_portals; ii++)
		DPAIPSEC_INFO("%d ", portal_channel[ii]);
	DPAIPSEC_INFO("\n");
#endif

#endif /* UNIQUE_IPSEC_CP_FQID */


	ipsecsa_info->shdesc_mem = 
		kzalloc((sizeof(struct sec_descriptor) + PRE_HDR_ALIGN), GFP_KERNEL);
	if (!ipsecsa_info->shdesc_mem)
	{
		DPAIPSEC_ERROR("%s::kzalloc failed for SEC descriptor\n",
				__FUNCTION__);
		goto err_ret0;
	}
	memset(ipsecsa_info->shdesc_mem, 0, (sizeof(struct sec_descriptor)+PRE_HDR_ALIGN));
	ipsecsa_info->shared_desc = (struct sec_descriptor *)
		PTR_ALIGN(ipsecsa_info->shdesc_mem, PRE_HDR_ALIGN);
	ipsecsa_info->shared_desc_dma = dma_map_single(jrdev_g,
			ipsecsa_info->shared_desc,
			sizeof(struct sec_descriptor),
			DMA_BIDIRECTIONAL);
	if (dma_mapping_error(jrdev_g, ipsecsa_info->shared_desc_dma))
	{
		DPAIPSEC_ERROR("%s::DMA map failed for SEC descriptor\n",
				__FUNCTION__);
		goto err_ret1;
	}

#ifdef UNIQUE_IPSEC_CP_FQID
	errno = qman_alloc_fqid_range(&fqids_base, NUM_FQS_PER_SA, 0, 0);
	if (errno < NUM_FQS_PER_SA)
	{
		DPAIPSEC_ERROR("%s::qman_alloc_fqid_range failed for allocating frame queues\n",
				__FUNCTION__);
		goto err_ret1;
	}
#endif /* UNIQUE_IPSEC_CP_FQID */

	sprintf(sa_id_name, "0x%x", handle);
	if (cdx_create_dir_in_procfs(&ipsecsa_info->sa_proc_entry, sa_id_name, SA_DIR)) {
		DPAIPSEC_ERROR("%s:: create pcd proc entry failed %s\n", 
				__FUNCTION__, sa_id_name);
		goto err_ret2;
	}

	for (ii = 0; ii < NUM_FQS_PER_SA; ii++) {

		dpa_fq = &ipsecsa_info->sec_fq[ii];
		memset(dpa_fq, 0, sizeof(struct dpa_fq));
		memset(&opts, 0, sizeof(struct qm_mcc_initfq));
		fq = &dpa_fq->fq_base;
		to_sec_fq = 0;
		switch (ii) {
			case FQ_FROM_SEC:
				{
#ifdef DPA_IPSEC_DEBUG
					printk("%s::handle %x\n", __FUNCTION__, handle);
#endif
#ifdef UNIQUE_IPSEC_CP_FQID
					flags = 0;
#else
					flags = (QMAN_FQ_FLAG_TO_DCPORTAL |
						 QMAN_FQ_FLAG_DYNAMIC_FQID);
#endif /* UNIQUE_IPSEC_CP_FQID */
					dpa_fq->fq_type = FQ_TYPE_RX_PCD;
					dpa_fq->fq_base.cb.dqrr = ipsec_from_sec_compat_handler;
#ifdef UNIQUE_IPSEC_CP_FQID
					dpa_fq->channel = portal_channel[next_portal_ch_idx];
#else
					dpa_fq->channel = ipsecinfo.ofport_channel;
#endif /* UNIQUE_IPSEC_CP_FQID */
					/* setting A1 value to 2 and setting a  bit to copy A1 value in  context A field  */
					/* setting override frame queue option */
					opts.fqd.context_a.hi =
						(((
#ifdef UNIQUE_IPSEC_CP_FQID
							 CDX_FQD_CTX_A_OVERRIDE_FQ |
							 /*CDX_FQD_CTX_A_B0_FIELD_VALID | */
#endif /* UNIQUE_IPSEC_CP_FQID */
							 CDX_FQD_CTX_A_A1_FIELD_VALID) <<
							CDX_FQD_CTX_A_SHIFT_BITS) |
						 CDX_FQD_CTX_A_A1_VAL_TO_CHECK_SECERR );
#ifdef UNIQUE_IPSEC_CP_FQID
					opts.fqd.context_b = fqids_base + FQ_TO_CP;
#endif /* UNIQUE_IPSEC_CP_FQID */
					break;
				}
			case FQ_TO_SEC:
				{
#ifdef UNIQUE_IPSEC_CP_FQID
					flags = (QMAN_FQ_FLAG_TO_DCPORTAL |
						 QMAN_FQ_FLAG_LOCKED);
#else
					flags = (QMAN_FQ_FLAG_TO_DCPORTAL | QMAN_FQ_FLAG_DYNAMIC_FQID |
						 QMAN_FQ_FLAG_LOCKED);
#endif /* UNIQUE_IPSEC_CP_FQID */
					addr = ipsecsa_info->shared_desc_dma;
					dpa_fq->channel = ipsecinfo.crypto_channel_id;
					dpa_fq->fq_base.cb.ern = dpa_ipsec_ern_cb;
					opts.fqd.context_b = ipsecsa_info->sec_fq[FQ_FROM_SEC].fqid;
					opts.fqd.context_a.hi = (uint32_t) (addr >> 32);
					opts.fqd.context_a.lo = (uint32_t) (addr);
					to_sec_fq = 1;
					break;
				}
#ifdef UNIQUE_IPSEC_CP_FQID
			case FQ_TO_CP:
				{
					flags = 0;
					/* set FQ parameters */
					/* dpa_fq->net_dev = net_dev; */
					/* No net_dev is attached to FQ as its being fetched from sagd */
					dpa_fq->fq_type = FQ_TYPE_RX_PCD;
					/* creating CP fqid as the fqid value of FROM_SEC FQID +1 */
					/* set call back function pointer */
					dpa_fq->fq_base.cb.dqrr = ipsec_exception_pkt_handler;
					/* round robin channel like ethernet driver does */
					dpa_fq->channel = portal_channel[next_portal_ch_idx];
					break;
				}
#endif /* UNIQUE_IPSEC_CP_FQID */

		}
		dpa_fq->wq = IPSEC_WQ_ID;
#ifdef UNIQUE_IPSEC_CP_FQID
		if (qman_create_fq(fqids_base+ii, flags, fq)) 
#else
		if (qman_create_fq(dpa_fq->fqid, flags, fq)) 
#endif /* UNIQUE_IPSEC_CP_FQID */
		{
			DPAIPSEC_ERROR("%s::qman_create_fq failed for fqid %d\n",
					__FUNCTION__, dpa_fq->fqid);
			goto err_ret3;
		}
		dpa_fq->fqid = fq->fqid;
		opts.fqid = dpa_fq->fqid;
		opts.count = 1;
		opts.fqd.dest.channel = dpa_fq->channel;
		opts.fqd.dest.wq = dpa_fq->wq;
#ifndef UNIQUE_IPSEC_CP_FQID
		opts.fqd.fq_ctrl = QM_FQCTRL_CPCSTASH;
		if (ii == FQ_FROM_SEC)
		{
			opts.fqd.fq_ctrl = (QM_FQCTRL_PREFERINCACHE |
					QM_FQCTRL_HOLDACTIVE);
			opts.fqd.context_a.stashing.exclusive =
				(QM_STASHING_EXCL_DATA | QM_STASHING_EXCL_ANNOTATION);
			opts.fqd.context_a.stashing.data_cl = NUM_PKT_DATA_LINES_IN_CACHE;
			opts.fqd.context_a.stashing.annotation_cl = NUM_ANN_LINES_IN_CACHE;
		}
#else
		if (ii == FQ_TO_SEC)
		{
			opts.fqd.fq_ctrl = QM_FQCTRL_CPCSTASH;
		}
		else
		{
			opts.fqd.fq_ctrl = (QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_HOLDACTIVE);
			opts.fqd.context_a.stashing.exclusive =
				(QM_STASHING_EXCL_DATA | QM_STASHING_EXCL_ANNOTATION);
			opts.fqd.context_a.stashing.data_cl = NUM_PKT_DATA_LINES_IN_CACHE;
			opts.fqd.context_a.stashing.annotation_cl = NUM_ANN_LINES_IN_CACHE;
		}
#endif /* UNIQUE_IPSEC_CP_FQID */
		if (to_sec_fq == 1)
		{
#ifdef FQ_TAIL_DROP
			/* Enabling the FQ tail drop threshold */
			opts.we_mask = QM_INITFQ_WE_TDTHRESH;
			/* Setting the frame queue tail drop threshold value. */
			qm_fqd_taildrop_set(&opts.fqd.td, DPA_FQ_TD_BYTES, 1);
			/* Enabling the FQ tail drop support. */
			opts.fqd.fq_ctrl |= QM_FQCTRL_TDE;
#endif
#ifdef CS_TAIL_DROP
			opts.we_mask |= QM_INITFQ_WE_CGID;
			/* Match CAAM QI request-FQ setup: always attach to a CGR. */
			opts.fqd.fq_ctrl |= QM_FQCTRL_CGE;
			opts.fqd.cgid = (u8)ipsecinfo.cgr.ingress_cgr.cgrid;
#endif
		}
		opts.we_mask |= (QM_INITFQ_WE_DESTWQ | QM_INITFQ_WE_FQCTRL |
				QM_INITFQ_WE_CONTEXTB | QM_INITFQ_WE_CONTEXTA);
		if(schedule)
			schedule = QMAN_INITFQ_FLAG_SCHED;
		if((errno=qman_init_fq(fq, schedule, &opts)))
		{
			DPAIPSEC_ERROR("%s::qman_init_fq failed for fqid %d errno= %d\n",
					__FUNCTION__, dpa_fq->fqid,errno);
			qman_destroy_fq(fq, 0);
			goto err_ret4;
			return FAILURE;
		}
		ipsec_addfq_to_exceptionfq_list(dpa_fq, &ipsecinfo);

		if (ii == FQ_FROM_SEC)
		{
			cdx_create_type_fqid_info_in_procfs(fq, SA_DIR, ipsecsa_info->sa_proc_entry, "from_sec");
		}
		else if (ii == FQ_TO_SEC)
		{
			cdx_create_type_fqid_info_in_procfs(fq, SA_DIR, ipsecsa_info->sa_proc_entry, "to_sec");
		}
#ifdef UNIQUE_IPSEC_CP_FQID
		else if (ii == FQ_TO_CP)
		{
			cdx_create_type_fqid_info_in_procfs(fq, SA_DIR, ipsecsa_info->sa_proc_entry, "to_cp");
		}
#endif

#ifdef DPA_IPSEC_DEBUG
		DPAIPSEC_INFO("%s::created fq %x(%d) for ipsec - type %d "
				"channel 0x%x\n", __FUNCTION__,
				dpa_fq->fqid, dpa_fq->fqid, ii, dpa_fq->channel);
#endif
	}
	return SUCCESS;

err_ret4:
err_ret3:
	for (; ii>0 ; ii--)
	{
		fq = &(ipsecsa_info->sec_fq[ii-1].fq_base);
		ipsec_delfq_from_exceptionfq_list(fq->fqid,&ipsecinfo);
		if (qman_retire_fq(fq, NULL)) {
			DPAIPSEC_ERROR("%s::Failed to retire FQ %x(%d)\n", 
					__FUNCTION__, fq->fqid, fq->fqid);
			return FAILURE;
		}
		if (qman_oos_fq(fq)) {
			DPAIPSEC_ERROR("%s::Failed to retire FQ %x(%d)\n", 
					__FUNCTION__, fq->fqid, fq->fqid);
			return FAILURE;
		}
		cdx_remove_fqid_info_in_procfs(fq->fqid);
		qman_destroy_fq(fq, 0);
	}
	if (ipsecsa_info->sa_proc_entry)
		proc_remove(((cdx_proc_dir_entry_t *)(ipsecsa_info->sa_proc_entry))->proc_dir);
err_ret2:
#ifdef UNIQUE_IPSEC_CP_FQID
	/*TODO : qman_release_fqid_range */
err_ret1:
#endif
	if (ipsecsa_info->shared_desc_dma)
		dma_unmap_single(jrdev_g, ipsecsa_info->shared_desc_dma,
				sizeof(struct sec_descriptor), DMA_BIDIRECTIONAL);
	kfree(ipsecsa_info->shdesc_mem);
err_ret0:
	return FAILURE;
}

void display_fq_info(void *handle)
{
	struct dpa_ipsec_sainfo *ipsecsa_info;
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	struct qm_mcr_queryfq_np *np;
	struct qm_fqd *fqd;
	uint32_t ii;

	ipsecsa_info = (struct dpa_ipsec_sainfo *)handle;
	np = kzalloc(sizeof(struct qm_mcr_queryfq_np), GFP_KERNEL);
	if (!np) {
		printk("%s::error allocating fqnp\n", __FUNCTION__);
		return;
	}
	fqd = kzalloc(sizeof(struct qm_fqd), GFP_KERNEL);
	if (!fqd) {
		printk("%s::error allocating fqd\n", __FUNCTION__);
		kfree(np);
		return;
	}

	for (ii = 0; ii < NUM_FQS_PER_SA; ii++) {
		dpa_fq = &ipsecsa_info->sec_fq[ii];
		fq = &dpa_fq->fq_base;
		printk("===========================================\n%s::fqid %x(%d\n", __FUNCTION__, fq->fqid, fq->fqid);
		if (qman_query_fq(fq, fqd)) {
			printk("%s::error getting fq fields\n", __FUNCTION__);
			break;
		}
		printk("fqctrl\t%x\n", fqd->fq_ctrl);
		printk("channel\t%x\n", fqd->dest.channel);
		printk("Wq\t%d\n", fqd->dest.wq);
		printk("contextb\t%x\n", fqd->context_b);
		printk("contexta\t%p\n", (void *)fqd->context_a.opaque);
		if (qman_query_fq_np(fq, np)) {
			printk("%s::error getting fqnp fields\n", __FUNCTION__);
			break;
		}
		printk("state\t%d\n", np->state);
		printk("byte count\t%d\n", np->byte_cnt);
		printk("frame count\t%d\n", np->frm_cnt);
	}
	kfree(np);
	kfree(fqd);
}


static int ipsec_init_ohport(struct ipsec_info *info)
{

	/* Get OH port for this driver */
	info->ofport_handle = alloc_offline_port(IPSEC_FMAN_IDX, PORT_TYPE_IPSEC, 
			NULL, NULL);
	if (info->ofport_handle < 0)
	{
		DPAIPSEC_ERROR("%s: Error in allocating OH port Channel\n", __FUNCTION__);
		return FAILURE;
	}
#ifdef DPA_IPSEC_DEBUG
	DPAIPSEC_INFO("%s: allocated oh port %d\n", __FUNCTION__, info->ofport_handle);
#endif
	if (get_ofport_info(IPSEC_FMAN_IDX, info->ofport_handle, &info->ofport_channel, 
				&info->ofport_td[0])) {
		DPAIPSEC_ERROR("%s: Error in getting OH port info\n", __FUNCTION__);
		return FAILURE;
	}
	if (get_ofport_portid(IPSEC_FMAN_IDX, info->ofport_handle, &info->ofport_portid)) {
		DPAIPSEC_ERROR("%s: Error in getting OH port id\n", __FUNCTION__);
		return FAILURE;
	}
	/* Route offline-port ingress into parser/PCD instead of the default RX sink. */
	if (ohport_set_ofne(info->ofport_handle, 0x440000) == -1) {
		DPAIPSEC_ERROR("%s: Error in setting OH port OFNE to parser\n",
				__FUNCTION__);
		return FAILURE;
	}
	printk("%s:: ipsec of port id = %d\n ", __func__, info->ofport_portid);
	return SUCCESS;
}

void *  dpa_get_ipsec_instance(void)
{
	return &ipsecinfo; 
}

int dpa_ipsec_ofport_td(struct ipsec_info *info, uint32_t table_type, void **td, 
		uint32_t* portid)
{
	if (table_type >= MAX_MATCH_TABLES) {
		DPAIPSEC_ERROR("%s::invalid table type %d\n", __FUNCTION__, table_type);
		return FAILURE;
	}
	*td = info->ofport_td[table_type];
	*portid = info->ofport_portid;
	return SUCCESS;
}

extern int dpaa_bp_alloc_n_add_buffs(const struct dpa_bp *dpa_bp, 
		uint32_t nbuffs, bool act_skb);
#define CDX_MAX_SG_BUFF_SIZE 1024
#define CDX_MAX_SG_BUFF_COUNT 512
extern struct dpa_bp *sg_bpool_g; // buffer reqd to frame SG list for skb fraglist
extern struct dpa_bp *skb_2bfreed_bpool_g; //if no recyclable skbs exist in skb fraglist, those should be freed back, SEC engine will add to this bman pool

int cdx_init_skb_2bfreed_bpool(void)
{
	struct dpa_bp *bp, *bp_parent;
	struct port_bman_pool_info parent_pool_info;

	// allocate memory for bpool
	bp = kzalloc(sizeof(struct dpa_bp), GFP_KERNEL);
	if (unlikely(bp == NULL)) {
		DPAIPSEC_ERROR("%s(%d)::failed to mem for non_recyclable SKB free bman pool\n",
				__FUNCTION__,__LINE__);
		return -1;
	}
	bp->size = CDX_MAX_SG_BUFF_SIZE;
	bp->config_count = CDX_MAX_SG_BUFF_COUNT;

	//find pools used by ethernet devices
	if (get_phys_port_poolinfo_bysize(bp->size, &parent_pool_info)) {
		DPAIPSEC_ERROR("%s::failed to locate eth bman pool for ipsec\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	bp_parent = dpa_bpid2pool(parent_pool_info.pool_id);
	bp->dev = bp_parent->dev;
	if (dpa_bp_alloc(bp, bp->dev)) {
		DPAIPSEC_ERROR("%s::dpa_bp_alloc failed for bufpool of freeing skbs\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	DPAIPSEC_INFO("%s::bp->size :%zu, bpid %d\n", 
			__FUNCTION__, bp->size, bp->bpid);
	skb_2bfreed_bpool_g = bp;
	return 0;
}

int cdx_init_scatter_gather_bpool(void)
{
	struct dpa_bp *bp,*bp_parent;
	struct port_bman_pool_info parent_pool_info;
	int ret =0;

	bp = kzalloc(sizeof(struct dpa_bp), GFP_KERNEL);
	if (unlikely(bp == NULL)) {
		DPAIPSEC_ERROR("%s::failed to allocate mem for SG bman pool\n", 
				__FUNCTION__);
		return -1;
	}
	bp->size = CDX_MAX_SG_BUFF_SIZE;
	bp->config_count = CDX_MAX_SG_BUFF_COUNT;

	//find pools used by ethernet devices and borrow buffers from it
	if (get_phys_port_poolinfo_bysize(bp->size, &parent_pool_info)) {
		DPAIPSEC_ERROR("%s::failed to locate eth bman pool for ipsec\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	bp_parent = dpa_bpid2pool(parent_pool_info.pool_id);
#ifdef DPA_IPSEC_DEBUG
	DPAIPSEC_INFO("%s::parent bman pool for SG - bp %p, bpid %d paddr %lx vaddr %p dev %p\n", 
			__FUNCTION__, bp, parent_pool_info.pool_id,
			(unsigned long)bp->paddr, bp->vaddr, bp->dev);
#endif
	bp->dev = bp_parent->dev;
	if (dpa_bp_alloc(bp, bp->dev)) {
		DPAIPSEC_ERROR("%s::dpa_bp_alloc failed for ipsec\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	DPAIPSEC_INFO("%s::bp->size :%zu, bpid %d\n", 
			__FUNCTION__, bp->size, bp->bpid);
	sg_bpool_g = bp;

	ret = dpaa_bp_alloc_n_add_buffs(bp, CDX_MAX_SG_BUFF_COUNT, 0);
	DPAIPSEC_INFO("%s(%d) buffers added to ipsec pool %d info size %zu \n", 
			__FUNCTION__,__LINE__,sg_bpool_g->bpid,
			sg_bpool_g->size);
	return 0;
}

static int add_ipsec_bpool(struct ipsec_info *info)
{
	struct dpa_bp *bp,*bp_parent;
	int ret = 0;
	printk (KERN_INFO"\n ################## %s", 
			__FUNCTION__);

	bp = kzalloc(sizeof(struct dpa_bp), GFP_KERNEL);
	if (unlikely(bp == NULL)) {
		DPAIPSEC_ERROR("%s::failed to allocate mem for bman pool for ipsec\n", 
				__FUNCTION__);
		return -1;
	}

	//find pools used by ethernet devices and borrow buffers from it
	if (get_phys_port_poolinfo_bysize(1700, &info->parent_pool_info)) {
		DPAIPSEC_ERROR("%s::failed to locate eth bman pool for ipsec\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	bp_parent = dpa_bpid2pool(info->parent_pool_info.pool_id);
#ifdef DPA_IPSEC_DEBUG
	DPAIPSEC_INFO("%s::parent bman pool for ipsec - bp %p, bpid %d paddr %lx vaddr %p dev %p\n", 
			__FUNCTION__, bp, info->parent_pool_info.pool_id,
			(unsigned long)bp->paddr, bp->vaddr, bp->dev);
#endif
	bp->dev = bp_parent->dev;
	bp->size = IPSEC_BUFSIZE;
	bp->config_count = IPSEC_BUFCOUNT;
	bp->free_buf_cb = _dpa_bp_free_pf;
	if (dpa_bp_alloc(bp, bp->dev)) {
		DPAIPSEC_ERROR("%s::dpa_bp_alloc failed for ipsec\n", 
				__FUNCTION__);
		kfree(bp);
		return -1;
	}
	DPAIPSEC_INFO("%s::bp->size :%zu, bpid %d\n", 
			__FUNCTION__, bp->size, bp->bpid);
	printk (KERN_INFO"\n ################## %s::bp->size :%zu, bpid %d\n", 
			__FUNCTION__, bp->size, bp->bpid);
	info->ipsec_bp = bp;

	ret = dpaa_bp_alloc_n_add_buffs(bp, IPSEC_BUFCOUNT, 1);
	if (ret < 0) {
		DPAIPSEC_ERROR("%s::failed to seed ipsec bpool %d\n",
				__FUNCTION__, bp->bpid);
	}
	printk(KERN_INFO "%s: %d buffers added to ipsec bpool %d (size %zu)\n",
			__FUNCTION__, IPSEC_BUFCOUNT, bp->bpid, bp->size);
	return 0;
}
static int release_ipsec_bpool(struct ipsec_info *info)
{
	struct dpa_bp *bp =  info->ipsec_bp ;
	bman_free_pool(bp->pool);
	kfree(bp);
	info->ipsec_bp = NULL; 
	return 0;
}

int cdx_dpa_get_ipsec_pool_info(uint32_t *bpid, uint32_t *buf_size)
{
	if (!ipsecinfo.ipsec_bp) 	
		return -1;
	*bpid = ipsecinfo.ipsec_bp->bpid;
	//*buf_size =ipsecinfo.parent_pool_info.buf_size;
	*buf_size = ipsecinfo.ipsec_bp->size;
	return 0;

}

void *cdx_dpa_ipsecsa_alloc(struct ipsec_info *info, uint32_t handle) 
{
	struct dpa_ipsec_sainfo *sainfo;

	sainfo = (struct dpa_ipsec_sainfo *)
		kzalloc(sizeof(struct dpa_ipsec_sainfo), GFP_KERNEL);
	if (!sainfo) {
		DPAIPSEC_ERROR("%s::Error in allocating sainfo\n", 
				__FUNCTION__);
		return NULL;
	}	
	memset(sainfo, 0, sizeof(struct dpa_ipsec_sainfo));
	atomic64_set(&sainfo->fromsec_status_transition_cnt, 0);
	atomic64_set(&sainfo->fromsec_bad_50000008_cnt, 0);
	atomic64_set(&sainfo->fromsec_bad_50000008_runlen, 0);
	sainfo->fromsec_last_status = 0;
	//create fqs in scheduled state
	if (create_ipsec_fqs(sainfo, 1, handle)) {
		kfree(sainfo);
		return NULL;
	}
	return sainfo; 	
}

/* change the state of frame queues */
int cdx_dpa_ipsec_retire_fq(void *handle, int fq_num)
{
	struct dpa_ipsec_sainfo *sainfo;
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	int32_t flags, ret;

	sainfo = (struct dpa_ipsec_sainfo *)handle;
	dpa_fq = &sainfo->sec_fq[fq_num];
	fq = &dpa_fq->fq_base; 
	ret = qman_retire_fq(fq, &flags);
	if (ret < 0) {
		DPAIPSEC_ERROR("%s::Failed to retire FQ %x(%d)\n", 
				__FUNCTION__, fq->fqid, fq->fqid);
	}
	return ret;
}

int cdx_dpa_ipsecsa_release(void *handle) 
{
	struct dpa_ipsec_sainfo *sainfo;
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	uint32_t ii;
	//	uint32_t flags;

	if (!handle)
		return FAILURE;
	sainfo = (struct dpa_ipsec_sainfo *)handle;

	for (ii = 0; ii < NUM_FQS_PER_SA; ii++) {
		dpa_fq = &sainfo->sec_fq[ii];
		fq = &dpa_fq->fq_base; 
		ipsec_delfq_from_exceptionfq_list(fq->fqid,&ipsecinfo);
#if 0 /* calling retire before timer start */
		//drain fq TODO
		//take fqs out of service
		if (qman_retire_fq(fq, &flags)) {
			DPAIPSEC_ERROR("%s::Failed to retire FQ %x(%d)\n", 
					__FUNCTION__, fq->fqid, fq->fqid);
			return FAILURE;
		}
#endif /* 0 */
		if (qman_oos_fq(fq)) {
			DPAIPSEC_ERROR("%s::Failed to retire FQ %x(%d)\n", 
					__FUNCTION__, fq->fqid, fq->fqid);
			return FAILURE;
		}
		cdx_remove_fqid_info_in_procfs(fq->fqid);
		qman_destroy_fq(fq, 0);
	}
	ipsec_destroy_compat_tx_fq(sainfo);
	if (sainfo->sa_proc_entry)
	{
		proc_remove(((cdx_proc_dir_entry_t *)(sainfo->sa_proc_entry))->proc_dir);
	}
#ifdef  UNIQUE_IPSEC_CP_FQID
	qman_release_fqid_range(sainfo->sec_fq[FQ_FROM_SEC].fqid, NUM_FQS_PER_SA);
#endif /* UNIQUE_IPSEC_CP_FQID */
	if (sainfo->shared_desc_dma)
		dma_unmap_single(jrdev_g, sainfo->shared_desc_dma,
				sizeof(struct sec_descriptor), DMA_BIDIRECTIONAL);
	kfree(sainfo->shdesc_mem);
	kfree(sainfo);
	return SUCCESS;
}

int cdx_ipsec_sa_fq_check_if_retired_state(void *dpa_ipsecsa_handle, int fq_num)
{
	struct dpa_ipsec_sainfo *sainfo;
	struct dpa_fq *dpa_fq;
	struct qman_fq *fq;
	sainfo = (struct dpa_ipsec_sainfo *)dpa_ipsecsa_handle;
	dpa_fq = &sainfo->sec_fq[fq_num];
	fq = &dpa_fq->fq_base; 
	/* if fq is not in retired state, restart timer */
	return (fq->state != qman_fq_state_retired);
}
#ifdef DPA_IPSEC_TEST_ENABLE
void dpa_ipsec_test(struct ipsec_info *info)
{
	void *handle;	
	struct sec_descriptor *sh_desc;
	uint32_t tosec_fqid;
	uint32_t fromsec_fqid;
	uint32_t portid;
	void *td;

	if (cdx_dpa_ipsec_wanport_td(info, ESP_IPV4_TABLE, &td)) {
		return;
	}	
	DPAIPSEC_INFO("%s::WAN ESP_IPV4_TABLE %p\n", __FUNCTION__, td);

	if (cdx_dpa_ipsec_wanport_td(info, ESP_IPV6_TABLE, &td)) {
		return;
	}	
	DPAIPSEC_INFO("%s::WAN ESP_IPV6_TABLE %p\n", __FUNCTION__, td);

	if (dpa_ipsec_ofport_td(info, IPV4_UDP_TABLE, &td, &portid)) {
		return;
	}	
	DPAIPSEC_INFO("%s::OF IPV4_TCPUDP_TABLE %p\n", __FUNCTION__, td);

	if (dpa_ipsec_ofport_td(info, IPV6_UDP_TABLE, &td, &portid )) {
		return;
	}	
	DPAIPSEC_INFO("%s::OF IPV6_TCPUDP_TABLE %p\n", __FUNCTION__, td);

	if (dpa_ipsec_ofport_td(info, ESP_IPV4_TABLE, &td, &portid)) {
		return;
	}	
	DPAIPSEC_INFO("%s::OF ESP_IPV4_TABLE %p, portif = %d\n", __FUNCTION__, td, portid);

	if (dpa_ipsec_ofport_td(info, ESP_IPV6_TABLE, &td, &portid)) {
		return;
	}	
	DPAIPSEC_INFO("%s::OF ESP_IPV6_TABLE %p\n", __FUNCTION__, td);

	handle = cdx_dpa_ipsecsa_alloc(info, 0xaa55);
	if (handle) {
		sh_desc = get_shared_desc(handle);
		tosec_fqid = get_fqid_to_sec(handle);	
		fromsec_fqid = get_fqid_from_sec(handle);	
		DPAIPSEC_INFO("%s::sh desc %p, tosec fqid %x(%d) from sec fqid %x(%d)\n",
				__FUNCTION__, sh_desc, tosec_fqid, tosec_fqid,
				fromsec_fqid, fromsec_fqid); 
		if (cdx_dpa_ipsecsa_release(handle)) {
			DPAIPSEC_ERROR("%s::Failed to release sa %p\n", 
					__FUNCTION__, handle);
			return;
		}		
	} else {
		DPAIPSEC_ERROR("%s::Failed to alloc sa\n", __FUNCTION__);
		return;
	}
}
#else
#define dpa_ipsec_test(x)
#endif

#ifdef CS_TAIL_DROP
static void cgr_cb(struct qman_portal *qm, struct qman_cgr *cgr, int congested)
{
	u64 enter_cnt = 0;
	u64 exit_cnt = 0;
	u64 trans_cnt;

	if (congested) {
		enter_cnt = atomic64_inc_return(&ipsec_cgr_enter_cnt);
		exit_cnt = atomic64_read(&ipsec_cgr_exit_cnt);
	} else {
		exit_cnt = atomic64_inc_return(&ipsec_cgr_exit_cnt);
		enter_cnt = atomic64_read(&ipsec_cgr_enter_cnt);
	}

	if (!ipsec_cgr_snapshot_enable || !ipsec_cgr_snapshot_period)
		return;

	trans_cnt = enter_cnt + exit_cnt;
	if (trans_cnt != 1 && (trans_cnt % ipsec_cgr_snapshot_period))
		return;

	printk_ratelimited(KERN_INFO
		"IPSEC_CGR: cgrid=%u congested=%u enter=%llu exit=%llu transitions=%llu sec_congestion=%u\n",
		cgr ? cgr->cgrid : 0, congested ? 1 : 0,
		(unsigned long long)enter_cnt,
		(unsigned long long)exit_cnt,
		(unsigned long long)trans_cnt,
		sec_congestion);
	return;
}

static int cdx_dpaa_ingress_cgr_init(struct cgr_priv *cgr)
{
	struct qm_mcc_initcgr initcgr;
	u32 cs_th;
	int err;

	memset(&initcgr, 0, sizeof(struct qm_mcc_initcgr));
	memset(cgr, 0, sizeof(struct cgr_priv));
	err = qman_alloc_cgrid(&cgr->ingress_cgr.cgrid);
	if (err < 0) {
		pr_err("Error %d allocating CGR ID\n", err);
		goto out_error;
	}

	cgr->ingress_cgr.cb = cgr_cb;
	/* Enable CS TD, Congestion State Change Notifications. */
	initcgr.we_mask = QM_CGR_WE_CSCN_EN | QM_CGR_WE_CS_THRES | QM_CGR_WE_MODE;
	initcgr.cgr.cscn_en = QM_CGR_EN;
	initcgr.cgr.mode = QMAN_CGR_MODE_FRAME;
	cs_th = sec_congestion ? sec_congestion : CDX_DPAA_INGRESS_CS_TD;

	qm_cgr_cs_thres_set64(&initcgr.cgr.cs_thres, cs_th, 1);
	printk("%s()::%d cs_th: %u mant %d exp %d\n", __func__, __LINE__,cs_th,
			initcgr.cgr.cs_thres.TA, initcgr.cgr.cs_thres.Tn);

	initcgr.we_mask |= QM_CGR_WE_CSTD_EN;
	initcgr.cgr.cstd_en = QM_CGR_EN;

	err = qman_create_cgr(&cgr->ingress_cgr, QMAN_CGR_FLAG_USE_INIT,
			&initcgr);
	if (err < 0) {
		pr_err("Error %d creating ingress CGR with ID %d\n", err,
				cgr->ingress_cgr.cgrid);
		qman_release_cgrid(cgr->ingress_cgr.cgrid);
		goto out_error;
	}
	pr_debug("Created ingress CGR %d\n", cgr->ingress_cgr.cgrid);

	/* cgr->use_ingress_cgr = true;*/

out_error:
	return err;
}

static void cdx_dpaa_ingress_cgr_exit(struct cgr_priv *cgr)
{
	int iRet = 0;

	if ((iRet = qman_delete_cgr(&cgr->ingress_cgr)))
		printk("Deletion of CGR failed: %d\n", iRet);
	else
		qman_release_cgrid(cgr->ingress_cgr.cgrid);

	return;
}
#endif


int cdx_dpa_ipsec_init(void)
{
	int i;

	DPAIPSEC_INFO("%s::\n", __FUNCTION__);
	ipsecinfo.crypto_channel_id = qm_channel_caam;
	ipsecinfo.ipsec_exception_fq = NULL;
	ipsecinfo.ofport_reinject_fq_valid = 0;
	atomic64_set(&ipsec_reinject_attempt_cnt, 0);
	atomic64_set(&ipsec_reinject_ok_cnt, 0);
	atomic64_set(&ipsec_reinject_err_cnt, 0);
	atomic64_set(&ipsec_reinject_ern_cnt, 0);
	atomic64_set(&ipsec_reinject_skipped_noncontig_cnt, 0);
	atomic64_set(&ipsec_reinject_skipped_missing_fq_cnt, 0);
	atomic64_set(&ipsec_reinject_fallback_excp_cnt, 0);
	atomic64_set(&ipsec_reinject_fallback_drop_cnt, 0);
	atomic64_set(&ipsec_signal_snapshot_seq, 0);
	atomic64_set(&ipsec_excp_status_total_cnt, 0);
	atomic64_set(&ipsec_excp_status_nonzero_cnt, 0);
	atomic64_set(&ipsec_from_sec_entry_cnt, 0);
	for (i = 0; i < 16; i++) {
		atomic64_set(&ipsec_excp_status_bucket_cnt[i], 0);
		atomic64_set(&ipsec_reinject_status_bucket_cnt[i], 0);
		atomic64_set(&ipsec_from_sec_status_bucket_cnt[i], 0);
	}
	for (i = 0; i < 4; i++)
		atomic64_set(&ipsec_from_sec_format_cnt[i], 0);
	for (i = 0; i < IPSEC_BP_SITE_MAX; i++) {
		atomic64_set(&ipsec_bp_alloc_ok_cnt[i], 0);
		atomic64_set(&ipsec_bp_alloc_enobufs_cnt[i], 0);
		atomic64_set(&ipsec_bp_alloc_fail_cnt[i], 0);
	}
	atomic64_set(&ipsec_bp_release_sg_payload_cnt, 0);
	atomic64_set(&ipsec_bp_release_rebuild_old_cnt, 0);
	atomic64_set(&ipsec_bp_release_compat_cnt, 0);
	atomic64_set(&ipsec_bp_release_ern_cnt, 0);
	atomic64_set(&ipsec_bp_txdev_enqueue_ok_cnt, 0);
	atomic64_set(&ipsec_bp_compat_enqueue_ok_cnt, 0);
	atomic64_set(&ipsec_bp_enqueue_fail_cnt, 0);
	for (i = 0; i < IPSEC_NOFQ_REMAP_MAX; i++)
		atomic64_set(&ipsec_nofq_remap_reason_cnt[i], 0);
#ifdef CS_TAIL_DROP
	atomic64_set(&ipsec_cgr_enter_cnt, 0);
	atomic64_set(&ipsec_cgr_exit_cnt, 0);
#endif
	if (ipsec_init_ohport(&ipsecinfo)) {
		return FAILURE;
	}
	if (add_ipsec_bpool(&ipsecinfo)) {
		return FAILURE;
	}
#ifdef CS_TAIL_DROP
	if (cdx_dpaa_ingress_cgr_init(&ipsecinfo.cgr)) {
		return FAILURE;
	}
	printk_ratelimited(KERN_INFO
		"IPSEC_CGR: stage=init cgrid=%u sec_congestion=%u snapshot=%u period=%u\n",
		ipsecinfo.cgr.ingress_cgr.cgrid, sec_congestion,
		ipsec_cgr_snapshot_enable ? 1 : 0,
		ipsec_cgr_snapshot_period);
#endif
	if (create_ipsec_pcd_fqs(&ipsecinfo, 1)) {
		goto ipsec_pcd_fq_failure;
	}
	if (ipsec_create_ofport_reinject_fq(&ipsecinfo)) {
		goto ipsec_reinject_fq_failure;
	}
	printk_ratelimited(KERN_INFO
		"IPSEC_REINJECT: stage=init mode=%u snapshot=%u period=%u\n",
		ipsec_compat_reinject_mode,
		ipsec_reinject_snapshot_enable ? 1 : 0,
		ipsec_reinject_snapshot_period);
	printk_ratelimited(KERN_INFO
		"IPSEC_SIGNAL: stage=init snapshot=%u period=%u\n",
		ipsec_signal_snapshot_enable ? 1 : 0,
		ipsec_signal_snapshot_period);
	printk_ratelimited(KERN_INFO
		"IPSEC_FROMSEC: stage=init snapshot=%u period=%u\n",
		ipsec_fromsec_snapshot_enable ? 1 : 0,
		ipsec_fromsec_snapshot_period);
	dpa_ipsec_test(&ipsecinfo);
	register_cdx_deinit_func(cdx_dpa_ipsec_exit);
	return SUCCESS;

ipsec_reinject_fq_failure:
ipsec_pcd_fq_failure:
#ifdef CS_TAIL_DROP
	cdx_dpaa_ingress_cgr_exit(&ipsecinfo.cgr);
#endif
	return FAILURE;
}

void cdx_dpa_ipsec_exit(void)
{
	DPAIPSEC_INFO("%s::\n", __FUNCTION__);
	ipsec_log_signal_snapshot("exit");
	printk_ratelimited(KERN_INFO
		"IPSEC_REINJECT: stage=exit attempts=%llu ok=%llu err=%llu ern=%llu skip_noncontig=%llu skip_nofq=%llu fb_excp=%llu fb_drop=%llu\n",
		(unsigned long long)atomic64_read(&ipsec_reinject_attempt_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_ok_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_err_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_ern_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_skipped_noncontig_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_skipped_missing_fq_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_fallback_excp_cnt),
		(unsigned long long)atomic64_read(&ipsec_reinject_fallback_drop_cnt));
	printk_ratelimited(KERN_INFO
		"IPSEC_SIGNAL: stage=exit remap[ok=%llu in=%llu unm=%llu cache=%llu esp=%llu peer=%llu sec=%llu l2=%llu] excp[total=%llu nonzero=%llu]\n",
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_OK]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_INPUT_INVALID]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PKT_UNMAPPED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_CACHED_PEER_INVALID]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_ESP_SAGD_UNRESOLVED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PEER_CHECK_FAILED]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_SECONDARY_NO_PEER]),
		(unsigned long long)atomic64_read(&ipsec_nofq_remap_reason_cnt[IPSEC_NOFQ_REMAP_PEER_L2_UNSUITABLE]),
		(unsigned long long)atomic64_read(&ipsec_excp_status_total_cnt),
		(unsigned long long)atomic64_read(&ipsec_excp_status_nonzero_cnt));
	printk_ratelimited(KERN_INFO
		"IPSEC_FROMSEC: stage=exit entries=%llu fmt[c=%llu sg=%llu cmp=%llu other=%llu]\n",
		(unsigned long long)atomic64_read(&ipsec_from_sec_entry_cnt),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[0]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[1]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[2]),
		(unsigned long long)atomic64_read(&ipsec_from_sec_format_cnt[3]));
	ipsec_log_bp_snapshot("exit");
	ipsec_destroy_ofport_reinject_fq(&ipsecinfo);
#ifdef CS_TAIL_DROP
	printk_ratelimited(KERN_INFO
		"IPSEC_CGR: stage=exit enter=%llu exit=%llu transitions=%llu cgrid=%u\n",
		(unsigned long long)atomic64_read(&ipsec_cgr_enter_cnt),
		(unsigned long long)atomic64_read(&ipsec_cgr_exit_cnt),
		(unsigned long long)(atomic64_read(&ipsec_cgr_enter_cnt) +
			atomic64_read(&ipsec_cgr_exit_cnt)),
		ipsecinfo.cgr.ingress_cgr.cgrid);
	cdx_dpaa_ingress_cgr_exit(&ipsecinfo.cgr);
#endif
	release_ipsec_bpool(&ipsecinfo);
	return;
}
#else
#define cdx_dpa_ipsec_init()
struct dpa_bp* get_ipsec_bp(void)
{
	return NULL;
}
#endif
