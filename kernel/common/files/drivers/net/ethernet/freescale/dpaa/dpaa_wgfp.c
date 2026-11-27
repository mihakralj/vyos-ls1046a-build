// SPDX-License-Identifier: GPL-2.0
/*
 * WireGuard fast path for DPAA1 (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
 *
 * WGFP-v2 architecture:
 *  - Per-ingress-port QMan sink FQs (per-CPU workers + optional pool-channel
 *    ORP queue in spread=1 mode) drain FMan-steered frames without sk_buffs.
 *  - RX BMan buffers remain read-only DMA_FROM_DEVICE and are released back to
 *    BMan in batches of up to 8 per RCR command inside wgfp_cpu_poll().
 *  - TX uses boot-lifetime, per-egress, per-CPU pre-mapped 4 KiB DMA_TO_DEVICE
 *    slots submitted directly to a dedicated confirmed FQ_TYPE_TX_WGFP (WQ6,
 *    F-316 offload_cgr) with bpid=0xff and FM_FD_CMD_FCO | wgfp_conf_fq[cpu].
 *  - Per-CPU WGFP confirmation FQs (WQ1, sharing wgfp_cpu_napi weight=64 with
 *    2 * budget DQRR poll) complete slots locklessly on owner_cpu; QMan ERNs
 *    and DPAA TX-error/conf fallback guards claim slots atomically via an
 *    append-only DMA hash table.
 *  - Teardown (NETDEV_GOING_DOWN / NETDEV_UNREGISTER / CEETM attach) unpublishes
 *    the egress slot, synchronizes RCU (freezing per-CPU alloc_cnt), waits for
 *    per-CPU inflight sums to reach 0, and quarantines on timeout.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/gfp.h>
#include <linux/hashtable.h>
#include <linux/if_ether.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/mm.h>
#include <linux/netdevice.h>
#include <linux/prefetch.h>
#include <linux/rcupdate.h>
#include <linux/rtnetlink.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/fsl/dpaa_flow_offload.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/fsl/fman_pcd.h>
#include <soc/fsl/qman.h>

#include "dpaa_eth.h"
#include "dpaa_fman_caps.h"
#include "fman.h"
#include "fman_port.h"
#include "mac.h"

/* Helpers living in dpaa_eth.c (file-local state there). */
struct device *dpaa_wgfp_dma_dev(u8 bpid);
void dpaa_wgfp_fd_release(const struct qm_fd *fd);
bool dpaa_buf_stale_check(dma_addr_t addr, u8 bpid, const char *where);

/* Defined here, called from F-322 fallback guards in dpaa_eth.c and dpaa_ceetm.c. */
bool dpaa_wgfp_has_tx_slots(void);
bool dpaa_wgfp_tx_error_claim(const struct qm_fd *fd);
void dpaa_wgfp_egress_deactivate(struct net_device *dev);

#define WGFP_MAX_SINKS		4	/* worker queues per ingress port */
#define WGFP_MAX_PORTS		8
/* Usable bytes of a DPAA RX buffer (dpaa_bp_size of the 4096-byte raw size). */
#define DPAA_WGFP_BUF_SIZE	3584
/* Same work queue as kernel RX FQs (RX_DEFAULT/RX_PCD = 6); never starve eth0. */
#define WGFP_WQ			6
/* Confirmation FQs match mainline FQ_TYPE_TX_CONF_MQ (WQ 1) so completions
 * always drain before new RX work and replenish the per-CPU TX slot pool. */
#define WGFP_CONF_WQ		1
/* QMan contiguous FD offset field is 9 bits (0..511). */
#define WGFP_FD_MAX_OFF		511U
#define WGFP_SINK_TD		(1024 * 1024)
#define WGFP_MAX_EGRESS		8
#define WGFP_BPID_INV		0xff
#define WGFP_TX_SLOTS_PER_CPU	512
#define WGFP_TX_DMA_HT_BITS	12	/* 4096 buckets */
#define WGFP_TX_META_MAGIC	0x57474650U	/* "WGFP" */
#define WGFP_DRAIN_TIMEOUT_MS	500
#define WGFP_FD_STAT_RX_ERRORS	(FM_FD_ERR_DMA | FM_FD_ERR_PHYSICAL | \
				 FM_FD_ERR_SIZE | FM_FD_ERR_CLS_DISCARD | \
				 FM_FD_ERR_EXTRACTION | FM_FD_ERR_NO_SCHEME | \
				 FM_FD_ERR_PRS_TIMEOUT | \
				 FM_FD_ERR_PRS_ILL_INSTRUCT | \
				 FM_FD_ERR_PRS_HDR_ERR)
#define WGFP_FD_STAT_TX_ERRORS	(FM_FD_ERR_UNSUPPORTED_FORMAT | \
				 FM_FD_ERR_LENGTH | FM_FD_ERR_DMA)
#define WGFP_MAX_BPIDS		64

static struct device *wgfp_bpid_dev[WGFP_MAX_BPIDS];
static u8 wgfp_bpid_need_sync[WGFP_MAX_BPIDS] = {
	[0 ... WGFP_MAX_BPIDS - 1] = 1,
};

static inline struct device *wgfp_dma_dev_fast(u8 bpid, bool *need_sync)
{
	struct device *dev;

	if (unlikely(bpid >= WGFP_MAX_BPIDS)) {
		dev = dpaa_wgfp_dma_dev(bpid);
		*need_sync = dev && dma_dev_need_sync(dev);
		return dev;
	}
	dev = READ_ONCE(wgfp_bpid_dev[bpid]);
	if (unlikely(!dev)) {
		dev = dpaa_wgfp_dma_dev(bpid);
		if (dev) {
			WRITE_ONCE(wgfp_bpid_need_sync[bpid],
				   dma_dev_need_sync(dev) ? 1 : 0);
			smp_wmb();
			WRITE_ONCE(wgfp_bpid_dev[bpid], dev);
		}
	} else {
		/* Pair with smp_wmb() above before reading cached need_sync. */
		smp_rmb();
	}
	*need_sync = dev && READ_ONCE(wgfp_bpid_need_sync[bpid]);
	return dev;
}

struct wgfp_port;
struct wgfp_egress;

enum wgfp_slot_state {
	WGFP_SLOT_FREE = 0,
	WGFP_SLOT_ALLOC = 1,
	WGFP_SLOT_BATCHED = 2,
	WGFP_SLOT_INFLIGHT = 3,
};

struct wgfp_tx_slot {
	/* Hot fields packed into the first 32 bytes (single cacheline). */
	dma_addr_t dma;			/* page-aligned DMA_TO_DEVICE address */
	struct wgfp_egress *eg;
	u8 *buf;			/* order-0 page virtual address */
	atomic_t state;			/* enum wgfp_slot_state */
	u16 owner_cpu;
	u16 local_idx;			/* 0..WGFP_TX_SLOTS_PER_CPU-1 */
	struct hlist_node dma_node;	/* append-only boot-lifetime wgfp_tx_dma_ht */
} ____cacheline_aligned_in_smp;

/* Stored once at offset 0 of each WGFP TX slot page (inside the 64B headroom). */
struct wgfp_tx_meta {
	u32 magic;
	struct wgfp_tx_slot *slot;
};

struct wgfp_egress_pc {
	/* Local BH-disabled single-writer counters (zero atomics on hot path). */
	u64 alloc_cnt;
	u64 local_done;
	u16 free_top;
	u16 free_stack[WGFP_TX_SLOTS_PER_CPU];
	/* Cross-CPU / ERN / TX-error completion path. */
	atomic64_t remote_done;
	spinlock_t remote_lock;
	u16 remote_top;
	u16 remote_stack[WGFP_TX_SLOTS_PER_CPU];
} ____cacheline_aligned_in_smp;

struct wgfp_egress {
	struct net_device *dev;		/* permanently bound netdev (never reassigned) */
	struct device *tx_dma_dev;
	bool tx_dma_need_sync;
	int ifindex;
	u32 tx_fqid;			/* FQ_TYPE_TX_WGFP */
	unsigned int ncpus;		/* number of per-CPU pools populated */
	struct wgfp_tx_slot *slots;	/* [ncpus * WGFP_TX_SLOTS_PER_CPU] */
	struct wgfp_egress_pc *pc;	/* [ncpus], cacheline-aligned per CPU */
	bool active;
	bool retired;			/* NETDEV_UNREGISTER: permanent */
	bool quarantined;		/* drain timeout: refuse reuse, retain pages/FQ */
};

/* Frame/byte counts of a sink every CPU dequeues (the ORP queue): kept per
 * CPU so the hot path does not bounce one cache line between four cores. */
struct wgfp_sink_pc {
	u64 frames;
	u64 bytes;
};

struct wgfp_sink {
	struct qman_fq fq;
	struct wgfp_port *port;	/* NULL for the legacy global set */
	int cpu;
	bool created;
	bool dist;		/* fan-out distributor, not a worker */
	bool orp;		/* ordered pool-channel queue (spread mode) */
	struct wgfp_sink_pc __percpu *pc;	/* ORP sink only */
	u64 frames;
	u64 bytes;
	u64 errs;
	u64 sg;
	u64 irqs;
	u64 sum;	/* defeats dead-code elimination of the touch loop */
};

struct wgfp_port {
	int ifindex;
	unsigned int nsinks;
	bool dist;
	atomic_t rr;		/* round-robin distributor cursor */
	struct wgfp_sink dist_sink;
	bool orp;
	struct wgfp_sink *orp_sink;
	struct wgfp_sink sink[WGFP_MAX_SINKS];
};

/* ORP window: orprws 5 = 1024 frames in flight; OA=1; OLWS=3. */
#define WGFP_ORP_RWS		5
#define WGFP_ORP_OA		1
#define WGFP_ORP_OLWS		3
#define WGFP_ORP_WAIT_NS	(5 * NSEC_PER_MSEC)
#define WGFP_XMIT_WAIT_NS	(1 * NSEC_PER_MSEC)
#define WGFP_TX_POLL_WAIT_NS	(5 * NSEC_PER_MSEC)

/* The frame the running callback owes the ORP. Per CPU, BH-disabled. */
struct wgfp_orp_ctx {
	struct wgfp_sink *sink;
	u32 orp_fqid;
	u16 seq;
	bool owed;
};
static DEFINE_PER_CPU(struct wgfp_orp_ctx, wgfp_orp_ctx);

/* Per-CPU batched RX BMan buffer releases inside wgfp_cpu_poll(). */
struct wgfp_bman_rel {
	bool active;
	u8 bpid;
	int n;
	dma_addr_t addr[8];
};
static DEFINE_PER_CPU(struct wgfp_bman_rel, wgfp_bman_rel);

/* One WGFP NAPI per CPU serving all RX sinks and this CPU's wgfp_conf_fq. */
struct wgfp_cpu_napi {
	struct napi_struct napi;
	struct qman_portal *portal;
};
static DEFINE_PER_CPU(struct wgfp_cpu_napi, wgfp_cpu_napi);
static bool wgfp_cpu_napi_ready;
static bool wgfp_threaded = true;

/* Boot-lifetime sink arrays, confirmation FQs, and egress slots. */
static struct wgfp_sink *wgfp_global;		/* [WGFP_MAX_SINKS] */
static unsigned int wgfp_nglobal;
static struct wgfp_port *wgfp_ports;		/* [WGFP_MAX_PORTS] */
static unsigned int wgfp_nports;
static struct net_device *wgfp_ndev;
static struct qman_fq *wgfp_conf_fqs;		/* [WGFP_MAX_SINKS], boot-lifetime */
static struct qman_fq *wgfp_tx_ern_fq;		/* tag FQ for TX/ORP enqueues */
static bool wgfp_tx_infra_ready;
static struct wgfp_egress *wgfp_egresses;	/* [WGFP_MAX_EGRESS] */
static DEFINE_HASHTABLE(wgfp_tx_dma_ht, WGFP_TX_DMA_HT_BITS);
static atomic_t wgfp_tx_slots_installed = ATOMIC_INIT(0);

static void wgfp_napi_add(struct napi_struct *napi,
			  int (*poll)(struct napi_struct *, int), int cpu)
{
	netif_napi_add(wgfp_ndev, napi, poll);
	if (napi->thread && cpu >= 0)
		set_cpus_allowed_ptr(napi->thread, cpumask_of(cpu));
	napi_enable(napi);
}

static struct dentry *wgfp_dir;
static unsigned int wgfp_touch = 2;
static unsigned int wgfp_per_port;	/* worker queues per port */
static unsigned int wgfp_fanout;
#define WGFP_FANOUT_MAX		1024
static unsigned int wgfp_fanout_home;
#define WGFP_FANOUT_HOME_MAX	64
/* Spread mode (QMan ORP): enabled by default on WGFP-v2 direct QMan TX. */
static bool wgfp_spread = true;
#define WGFP_ORP_TD_DEF		(6 * 1024 * 1024)
#define WGFP_ORP_TD_MIN		(64 * 1024)
#define WGFP_ORP_TD_MAX		(16 * 1024 * 1024)
static u32 wgfp_orp_td = WGFP_ORP_TD_DEF;
static bool wgfp_orp_stash;
static bool wgfp_sink_stash;

static void wgfp_fqd_stash(struct qm_mcc_initfq *opts)
{
	opts->we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA);
	opts->fqd.fq_ctrl |= cpu_to_be16(QM_FQCTRL_CTXASTASHING);
	opts->fqd.context_a.stashing.exclusive =
		QM_STASHING_EXCL_DATA | QM_STASHING_EXCL_CTX |
		QM_STASHING_EXCL_ANNOTATION;
	qm_fqd_set_stashing(&opts->fqd, 1, 2,
			    DIV_ROUND_UP(sizeof(struct qman_fq), 64));
}

/* Prefetch for read (default 1): RX BMan buffers are never mutated. */
static unsigned int wgfp_prefetch = 1;
static unsigned int wgfp_prefetch_max;

static void wgfp_prefetch_frame(const u8 *p, u32 len)
{
	unsigned int mode = READ_ONCE(wgfp_prefetch);
	unsigned int max = READ_ONCE(wgfp_prefetch_max);
	u32 o;

	if (!mode)
		return;
	if (max && max < len)
		len = max;
	for (o = 0; o < len; o += L1_CACHE_BYTES) {
		if (mode == 2)
			prefetchw(p + o);
		else
			prefetch(p + o);
	}
}

static struct qman_fq wgfp_hole_fq;
static bool wgfp_hole_ready;
static DEFINE_PER_CPU(atomic64_t, wgfp_orp_frames);
static atomic64_t wgfp_orp_holes = ATOMIC64_INIT(0);
static atomic64_t wgfp_orp_wait_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_orp_hole_ern = ATOMIC64_INIT(0);
static void wgfp_hole_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			     const union qm_mr_entry *msg);
static bool wgfp_pool_failed;

static inline void wgfp_sink_count(struct wgfp_sink *s, u32 len)
{
	if (s->pc) {
		struct wgfp_sink_pc *pc = this_cpu_ptr(s->pc);

		pc->frames++;
		pc->bytes += len;
		return;
	}
	s->frames++;
	s->bytes += len;
}

static DEFINE_MUTEX(wgfp_lock);
/* Lock order:
 * wgfp_lab_lock -> rtnl_lock / ask_policy_revoke_mutex -> wgfp_ops_quiesce_lock
 * -> wgfp_lock -> pcd->fe_lock.
 */
static DEFINE_MUTEX(wgfp_ops_quiesce_lock);

static const struct dpaa_wgfp_ops __rcu *wgfp_ops;
static void *wgfp_ops_priv;
static struct qman_fq wgfp_rx_ern_fq;
static bool wgfp_rx_ern_ready;
static atomic64_t wgfp_rx_enq_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_rx_ern = ATOMIC64_INIT(0);
static void wgfp_rx_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg);

static struct net_device __rcu *wgfp_eg_dev[WGFP_MAX_EGRESS];
static int wgfp_neg;

static DEFINE_PER_CPU(atomic64_t, wgfp_xmit_ok);
static DEFINE_PER_CPU(atomic64_t, wgfp_xmit_conf_ok);
static atomic64_t wgfp_xmit_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern_cgr_td = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern_orp_late = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern_orp_early = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_ern_other = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_frame_wait_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_poll_wait_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_invalid_egress = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_orp_wait_fail = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_nomem = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_no_slot = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_qdisc_drop = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_cn = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_tx_err = ATOMIC64_INIT(0);
static atomic64_t wgfp_xmit_dup_comp = ATOMIC64_INIT(0);

/* Seal TX batching ("tx_batch <n>", default 32, 0 = off). */
#define WGFP_TXB_MAX		64
static unsigned int wgfp_tx_batch = 32;
struct wgfp_txb {
	bool active, threaded;
	unsigned int n, lim;
	ktime_t end;
	struct wgfp_tx_slot *slot[WGFP_TXB_MAX];
	struct qm_fd fd[WGFP_TXB_MAX];
};
static DEFINE_PER_CPU(struct wgfp_txb, wgfp_txb);

static inline u32 wgfp_dma_hash(dma_addr_t addr)
{
	return hash_64((u64)(addr >> PAGE_SHIFT), WGFP_TX_DMA_HT_BITS);
}

static struct wgfp_tx_slot *wgfp_tx_slot_by_dma(dma_addr_t addr)
{
	struct wgfp_tx_slot *s;

	if (unlikely(!addr || (addr & ~PAGE_MASK)))
		return NULL;
	rcu_read_lock();
	hlist_for_each_entry_rcu(s, &wgfp_tx_dma_ht[wgfp_dma_hash(addr)],
				 dma_node) {
		if (s->dma == addr) {
			rcu_read_unlock();
			return s;
		}
	}
	rcu_read_unlock();
	return NULL;
}

static void wgfp_slot_return_done(struct wgfp_tx_slot *slot, bool bh_disabled)
{
	struct wgfp_egress *eg = slot->eg;
	struct wgfp_egress_pc *pc = &eg->pc[slot->owner_cpu];
	bool quarantined = unlikely(READ_ONCE(eg->quarantined));

	if (bh_disabled && smp_processor_id() == slot->owner_cpu) {
		if (likely(!quarantined))
			pc->free_stack[pc->free_top++] = slot->local_idx;
		/* Inside wgfp_cpu_poll(), defer the release barrier to a single
		 * smp_wmb() at poll exit instead of per-packet STLR. */
		if (likely(this_cpu_read(wgfp_bman_rel.active)))
			WRITE_ONCE(pc->local_done, pc->local_done + 1);
		else
			smp_store_release(&pc->local_done, pc->local_done + 1);
		return;
	}
	if (likely(!quarantined)) {
		spin_lock_bh(&pc->remote_lock);
		pc->remote_stack[pc->remote_top++] = slot->local_idx;
		spin_unlock_bh(&pc->remote_lock);
	}
	/* Order slot state + remote_stack push before drain observes remote_done. */
	smp_mb__before_atomic();
	atomic64_inc(&pc->remote_done);
}

static struct wgfp_tx_slot *wgfp_slot_pop_bh(struct wgfp_egress *eg, int cpu)
{
	struct wgfp_egress_pc *pc = &eg->pc[cpu];
	struct wgfp_tx_slot *slot;
	u16 idx;

	if (unlikely(pc->free_top == 0)) {
		if (!READ_ONCE(pc->remote_top))
			return NULL;
		spin_lock_bh(&pc->remote_lock);
		if (pc->remote_top) {
			memcpy(pc->free_stack, pc->remote_stack,
			       pc->remote_top * sizeof(u16));
			pc->free_top = pc->remote_top;
			pc->remote_top = 0;
		}
		spin_unlock_bh(&pc->remote_lock);
		if (!pc->free_top)
			return NULL;
	}
	idx = pc->free_stack[--pc->free_top];
	slot = &eg->slots[cpu * WGFP_TX_SLOTS_PER_CPU + idx];
	prefetchw(slot->buf + DPAA_WGFP_TX_HEADROOM);
	prefetchw(slot->buf + DPAA_WGFP_TX_HEADROOM + 64);
	prefetchw(slot->buf + DPAA_WGFP_TX_HEADROOM + 128);
	prefetchw(slot->buf + DPAA_WGFP_TX_HEADROOM + 192);
	return slot;
}

/* Exactly-once completion for an INFLIGHT slot across confirm, ERN, and TX error. */
static void wgfp_slot_complete(struct wgfp_tx_slot *slot,
			       const struct qm_fd *fd, bool is_conf,
			       bool bh_disabled)
{
	if (unlikely(atomic_cmpxchg_relaxed(&slot->state, WGFP_SLOT_INFLIGHT,
					    WGFP_SLOT_FREE) != WGFP_SLOT_INFLIGHT)) {
		atomic64_inc(&wgfp_xmit_dup_comp);
		return;
	}
	if (is_conf) {
		if (unlikely(be32_to_cpu(fd->status) & WGFP_FD_STAT_TX_ERRORS))
			atomic64_inc(&wgfp_xmit_tx_err);
		else
			dpaa_wgfp_stat_inc(&wgfp_xmit_conf_ok);
	}
	wgfp_slot_return_done(slot, bh_disabled);
}

/* Sync-failure unwind when qman_enqueue_* rejected a slot before acceptance. */
static void wgfp_slot_unwind_failed_enq(struct wgfp_tx_slot *slot,
					bool bh_disabled)
{
	if (unlikely(atomic_cmpxchg_relaxed(&slot->state, WGFP_SLOT_INFLIGHT,
					    WGFP_SLOT_FREE) != WGFP_SLOT_INFLIGHT)) {
		atomic64_inc(&wgfp_xmit_dup_comp);
		return;
	}
	wgfp_slot_return_done(slot, bh_disabled);
}

static void wgfp_bman_rel_flush(struct wgfp_bman_rel *rel)
{
	if (!rel->n)
		return;
	dpaa_wgfp_bman_release_batch(rel->bpid, rel->addr, rel->n);
	rel->n = 0;
}

static void wgfp_frame_release_fast(struct dpaa_wgfp_frame *f)
{
	const struct qm_fd *fd = f->fd;
	struct wgfp_bman_rel *rel;

	if (unlikely(fd->bpid >= WGFP_MAX_BPIDS ||
		     READ_ONCE(wgfp_bpid_need_sync[fd->bpid])))
		dma_sync_single_for_device(f->dev, f->addr, f->buf_size,
					   DMA_FROM_DEVICE);
	rel = this_cpu_ptr(&wgfp_bman_rel);
	if (likely(rel->active)) {
		if (rel->n && (rel->bpid != fd->bpid || rel->n == 8))
			wgfp_bman_rel_flush(rel);
		rel->bpid = fd->bpid;
		rel->addr[rel->n++] = f->addr;
		if (rel->n == 8)
			wgfp_bman_rel_flush(rel);
		return;
	}
	dpaa_wgfp_fd_release(fd);
}

static void wgfp_touch_frame(struct wgfp_sink *s, u8 *p, u32 len)
{
	u64 sum = 0;
	u32 i;

	switch (wgfp_touch) {
	case 0:
		return;
	case 1:
		for (i = 0; i < 64 / 8 && i * 8 + 8 <= len; i++)
			sum += ((const u64 *)p)[i];
		if (len > 64)
			for (i = 0; i < 64 / 8; i++)
				sum += ((const u64 *)(p + len - 64))[i];
		break;
	case 2:
		for (i = 0; i + 8 <= len; i += 8)
			sum += *(const u64 *)(p + i);
		break;
	default:
		for (i = 0; i + 8 <= len; i += 8) {
			u64 *q = (u64 *)(p + i);

			*q ^= 0x5a5a5a5a5a5a5a5aULL;
			sum += *q;
		}
		break;
	}
	s->sum += sum;
}

static unsigned int wgfp_fanout_pick(unsigned int b, unsigned int n,
				     unsigned int home, unsigned int m)
{
	unsigned int j;

	if (n < 2 || m < 2 || home >= n)
		return b % n;
	if (b % m == 0)
		return home;
	j = (b - b / m - 1) % (n - 1);
	return j < home ? j : j + 1;
}

/* Same hand-off as dpaa_eth_napi_schedule(), to this CPU's WGFP NAPI. */
static enum qman_cb_dqrr_result wgfp_sched_portal(struct qman_portal *portal,
						  struct wgfp_sink *s)
{
	struct wgfp_cpu_napi *n = this_cpu_ptr(&wgfp_cpu_napi);

	qman_p_irqsource_remove(portal, QM_PIRQ_DQRI);
	n->portal = portal;
	if (s)
		s->irqs++;
	napi_schedule(&n->napi);
	return qman_cb_dqrr_stop;
}

static enum qman_cb_dqrr_result wgfp_conf_dqrr(struct qman_portal *portal,
					       struct qman_fq *fq,
					       const struct qm_dqrr_entry *dq,
					       bool sched_napi)
{
	const struct qm_fd *fd = &dq->fd;
	struct wgfp_tx_slot *slot = NULL;
	dma_addr_t addr;

	if (sched_napi)
		return wgfp_sched_portal(portal, NULL);

	addr = qm_fd_addr(fd);
	if (likely(addr && !(addr & ~PAGE_MASK))) {
		const struct wgfp_tx_meta *meta =
			(const struct wgfp_tx_meta *)phys_to_virt(addr);

		if (likely(meta->magic == WGFP_TX_META_MAGIC &&
			   meta->slot && meta->slot->dma == addr))
			slot = meta->slot;
	}
	if (unlikely(!slot))
		slot = wgfp_tx_slot_by_dma(addr);
	if (likely(slot))
		wgfp_slot_complete(slot, fd, true, true);
	else
		atomic64_inc(&wgfp_xmit_dup_comp);
	return qman_cb_dqrr_consume;
}

static void wgfp_tx_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg)
{
	const struct qm_fd *fd = &msg->ern.fd;
	struct wgfp_tx_slot *slot;
	u8 rc = msg->ern.rc & QM_MR_RC_MASK;

	atomic64_inc(&wgfp_xmit_ern);
	atomic64_inc(&wgfp_xmit_fail);
	switch (rc) {
	case QM_MR_RC_CGR_TAILDROP:
		atomic64_inc(&wgfp_xmit_ern_cgr_td);
		break;
	case QM_MR_RC_ORPWINDOW_LATE:
		atomic64_inc(&wgfp_xmit_ern_orp_late);
		break;
	case QM_MR_RC_ORPWINDOW_EARLY:
		atomic64_inc(&wgfp_xmit_ern_orp_early);
		break;
	default:
		atomic64_inc(&wgfp_xmit_ern_other);
		break;
	}
	/* qm_mr_process_task runs with BH enabled (B2): pass bh_disabled=false
	 * so return always takes remote_lock with spin_lock_bh(). */
	slot = wgfp_tx_slot_by_dma(qm_fd_addr(fd));
	if (likely(slot))
		wgfp_slot_complete(slot, fd, false, false);
	else
		atomic64_inc(&wgfp_xmit_dup_comp);
}

bool dpaa_wgfp_has_tx_slots(void)
{
	return atomic_read(&wgfp_tx_slots_installed) > 0;
}

bool dpaa_wgfp_tx_error_claim(const struct qm_fd *fd)
{
	struct wgfp_tx_slot *slot;
	dma_addr_t addr;

	if (likely(fd->bpid != WGFP_BPID_INV))
		return false;
	addr = qm_fd_addr(fd);
	if (!addr || (addr & ~PAGE_MASK))
		return false;
	slot = wgfp_tx_slot_by_dma(addr);
	if (!slot)
		return false;
	atomic64_inc(&wgfp_xmit_tx_err);
	wgfp_slot_complete(slot, fd, false, false);
	return true;
}

static enum qman_cb_dqrr_result wgfp_dqrr_frame(struct qman_portal *portal,
					       struct qman_fq *fq,
					       const struct qm_dqrr_entry *dq,
					       bool sched_napi)
{
	struct wgfp_sink *s = container_of(fq, struct wgfp_sink, fq);
	const struct qm_fd *fd = &dq->fd;
	const struct dpaa_wgfp_ops *ops;
	struct device *dev;
	bool need_sync;
	dma_addr_t addr;
	u32 off, len;
	u8 *p;

	if (sched_napi)
		return wgfp_sched_portal(portal, s);

	if (unlikely(be32_to_cpu(fd->status) & WGFP_FD_STAT_RX_ERRORS)) {
		s->errs++;
		dpaa_wgfp_fd_release(fd);
		return qman_cb_dqrr_consume;
	}
	if (unlikely(qm_fd_get_format(fd) != qm_fd_contig)) {
		s->sg++;
		dpaa_wgfp_fd_release(fd);
		return qman_cb_dqrr_consume;
	}

	addr = qm_fd_addr(fd);
	if (unlikely(dpaa_buf_stale_check(addr, fd->bpid, "wgfp"))) {
		s->errs++;
		return qman_cb_dqrr_consume;
	}
	off = qm_fd_get_offset(fd);
	len = qm_fd_get_length(fd);
	dev = wgfp_dma_dev_fast(fd->bpid, &need_sync);
	if (unlikely(!dev)) {
		s->errs++;
		return qman_cb_dqrr_consume;
	}

	if (s->dist) {
		struct wgfp_port *p = s->port;
		struct wgfp_sink *dst;
		struct qm_fd nfd;
		unsigned int i;

		if (unlikely(!p || !p->nsinks))
			goto release;
		i = wgfp_fanout_pick((unsigned int)atomic_inc_return(&p->rr) /
				     max_t(unsigned int, READ_ONCE(wgfp_fanout), 1),
				     p->nsinks, s->cpu,
				     READ_ONCE(wgfp_fanout_home));
		dst = &p->sink[i];
		qm_fd_clear_fd(&nfd);
		nfd.bpid = fd->bpid;
		qm_fd_addr_set64(&nfd, addr);
		qm_fd_set_contig(&nfd, off, len);
		if (!READ_ONCE(wgfp_rx_ern_ready) ||
		    qman_enqueue_fqid(&wgfp_rx_ern_fq,
				      qman_fq_fqid(&dst->fq), &nfd)) {
			atomic64_inc(&wgfp_rx_enq_fail);
			goto release;
		}
		wgfp_sink_count(s, len);
		return qman_cb_dqrr_consume;
	}

	p = (u8 *)phys_to_virt(addr) + off;
	if (unlikely(need_sync))
		dma_sync_single_for_cpu(dev, addr + off, len, DMA_FROM_DEVICE);
	wgfp_prefetch_frame(p, len);

	rcu_read_lock();
	ops = rcu_dereference(wgfp_ops);
	if (ops) {
		struct dpaa_wgfp_frame f = {
			.buf = (u8 *)phys_to_virt(addr),
			.addr = addr,
			.dev = dev,
			.off = off,
			.len = len,
			.buf_size = DPAA_WGFP_BUF_SIZE,
			.ingress_ifindex = s->port ? s->port->ifindex : 0,
			.fd = (void *)fd,
		};

		if (ops->frame(wgfp_ops_priv, &f)) {
			rcu_read_unlock();
			wgfp_sink_count(s, len);
			return qman_cb_dqrr_consume;
		}
	}
	rcu_read_unlock();

	wgfp_touch_frame(s, p, len);
	if (unlikely(need_sync))
		dma_sync_single_for_device(dev, addr + off, len,
					   DMA_FROM_DEVICE);

	wgfp_sink_count(s, len);
release:
	dpaa_wgfp_fd_release(fd);
	return qman_cb_dqrr_consume;
}

/* Give the ORP the sequence number of a frame that is going nowhere. */
static void wgfp_orp_hole(struct wgfp_orp_ctx *c)
{
	struct qm_fd fd;
	ktime_t end = ktime_add_ns(ktime_get(), WGFP_ORP_WAIT_NS);

	qm_fd_clear_fd(&fd);
	c->owed = false;
	do {
		if (!qman_enqueue_orp_fqid(&wgfp_hole_fq, 0, c->orp_fqid, c->seq,
					   true, &fd)) {
			atomic64_inc(&wgfp_orp_holes);
			return;
		}
		cpu_relax();
	} while (ktime_before(ktime_get(), end));
	atomic64_inc(&wgfp_orp_wait_fail);
}

void dpaa_wgfp_orp_settle(void)
{
	struct wgfp_orp_ctx *c = this_cpu_ptr(&wgfp_orp_ctx);

	if (unlikely(c->owed))
		wgfp_orp_hole(c);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_orp_settle);

static enum qman_cb_dqrr_result wgfp_dqrr(struct qman_portal *portal,
					  struct qman_fq *fq,
					  const struct qm_dqrr_entry *dq,
					  bool sched_napi)
{
	struct wgfp_sink *s = container_of(fq, struct wgfp_sink, fq);
	struct wgfp_orp_ctx *c;
	enum qman_cb_dqrr_result r;

	if (likely(!s->orp))
		return wgfp_dqrr_frame(portal, fq, dq, sched_napi);

	if (sched_napi)
		return wgfp_sched_portal(portal, s);
	if (unlikely(!(dq->stat & QM_DQRR_STAT_FD_VALID)))
		return qman_cb_dqrr_consume;

	c = this_cpu_ptr(&wgfp_orp_ctx);
	c->sink = s;
	c->orp_fqid = qman_fq_fqid(fq);
	c->seq = be16_to_cpu(dq->seqnum);
	c->owed = true;
	dpaa_wgfp_stat_inc(&wgfp_orp_frames);
	r = wgfp_dqrr_frame(portal, fq, dq, false);
	if (c->owed)
		wgfp_orp_hole(c);
	return r;
}

static void wgfp_tx_enqueue_slot(struct wgfp_tx_slot *slot,
				 const struct qm_fd *fd, ktime_t *shared_end,
				 bool bh_disabled)
{
	struct wgfp_egress *eg = slot->eg;
	ktime_t end;

	if (unlikely(!READ_ONCE(eg->active) || READ_ONCE(eg->quarantined))) {
		atomic_set(&slot->state, WGFP_SLOT_FREE);
		wgfp_slot_return_done(slot, bh_disabled);
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		return;
	}
	/* Publish INFLIGHT before enqueue so a fast completion can claim it. */
	smp_store_release(&slot->state.counter, WGFP_SLOT_INFLIGHT);
	if (likely(!qman_enqueue_fqid(wgfp_tx_ern_fq, eg->tx_fqid, fd))) {
		dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
		return;
	}
	if (shared_end) {
		if (!*shared_end)
			*shared_end = ktime_add_ns(ktime_get(),
						   WGFP_TX_POLL_WAIT_NS);
		end = *shared_end;
	} else {
		end = ktime_add_ns(ktime_get(), WGFP_XMIT_WAIT_NS);
	}
	do {
		cpu_relax();
		if (unlikely(!READ_ONCE(eg->active) ||
			     READ_ONCE(eg->quarantined)))
			break;
		if (!qman_enqueue_fqid(wgfp_tx_ern_fq, eg->tx_fqid, fd)) {
			dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
			return;
		}
	} while (ktime_before(ktime_get(), end));
	atomic64_inc(shared_end ? &wgfp_xmit_poll_wait_fail :
		     &wgfp_xmit_frame_wait_fail);
	atomic64_inc(&wgfp_xmit_fail);
	wgfp_slot_unwind_failed_enq(slot, bh_disabled);
}

static void wgfp_txb_flush(struct wgfp_txb *b)
{
	ktime_t *shared_end = b->threaded ? &b->end : NULL;
	unsigned int i;

	for (i = 0; i < b->n; i++)
		wgfp_tx_enqueue_slot(b->slot[i], &b->fd[i], shared_end, true);
	b->n = 0;
}

static int wgfp_cpu_poll(struct napi_struct *napi, int budget)
{
	struct wgfp_cpu_napi *n = container_of(napi, struct wgfp_cpu_napi, napi);
	struct wgfp_txb *b = this_cpu_ptr(&wgfp_txb);
	struct wgfp_bman_rel *rel = this_cpu_ptr(&wgfp_bman_rel);
	unsigned int lim = min_t(unsigned int, READ_ONCE(wgfp_tx_batch), WGFP_TXB_MAX);
	/* Each packet generates 2 DQRR entries (1 RX + 1 TX confirm); polling
	 * up to 2 * budget DQRR entries keeps a full 64-packet burst per poll
	 * while staying within NAPI_POLL_WEIGHT=64 (zero KERN_ERR warnings). */
	int dq_limit = budget * 2;
	int total;

	if (unlikely(!budget))
		return 0;

	b->threaded = READ_ONCE(wgfp_threaded);
	b->end = 0;		/* lazy ktime_get() only on -EBUSY */
	b->lim = lim;
	b->active = lim > 1;
	rel->active = true;
	total = qman_p_poll_dqrr(n->portal, dq_limit);
	if (b->active) {
		b->active = false;
		wgfp_txb_flush(b);
	}
	rel->active = false;
	wgfp_bman_rel_flush(rel);
	/* Pairs with smp_load_acquire() in wgfp_eg_inflight_sum() for batched
	 * local_done updates done during this poll. */
	smp_wmb();
	if (total < dq_limit) {
		int done = min(total, budget - 1);

		napi_complete_done(napi, done);
		qman_p_irqsource_add(n->portal, QM_PIRQ_DQRI);
		return done;
	}
	return budget;
}

/* Create the per-CPU NAPIs once with portal pre-initialized (B4). */
static void wgfp_cpu_napi_ensure_locked(void)
{
	unsigned int cpu;

	if (wgfp_cpu_napi_ready)
		return;
	for_each_cpu(cpu, qman_affine_cpus()) {
		struct wgfp_cpu_napi *n = &per_cpu(wgfp_cpu_napi, cpu);

		n->portal = qman_get_affine_portal(cpu);
		wgfp_napi_add(&n->napi, wgfp_cpu_poll, cpu);
	}
	wgfp_cpu_napi_ready = true;
}

static int wgfp_tx_infra_ensure_locked(void)
{
	const struct cpumask *affine = qman_affine_cpus();
	struct qm_mcc_initfq opts;
	unsigned int cpu;
	int err;

	if (wgfp_tx_infra_ready)
		return 0;
	if (wgfp_pool_failed)
		return -EIO;
	wgfp_cpu_napi_ensure_locked();

	wgfp_tx_ern_fq->cb.ern = wgfp_tx_ern_cb;
	err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
			     QMAN_FQ_FLAG_NO_MODIFY, wgfp_tx_ern_fq);
	if (err) {
		pr_err("dpaa_wgfp: allocate TX ERN tag queue: %d\n", err);
		return err;
	}
	for_each_cpu(cpu, affine) {
		struct qman_fq *cfq;

		if (cpu >= WGFP_MAX_SINKS)
			break;
		cfq = &wgfp_conf_fqs[cpu];
		memset(cfq, 0, sizeof(*cfq));
		cfq->cb.dqrr = wgfp_conf_dqrr;
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_ENQUEUE, cfq);
		if (err) {
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: create confirm FQ cpu %u: %d\n",
			       cpu, err);
			return err;
		}
		memset(&opts, 0, sizeof(opts));
		opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL |
					   QM_INITFQ_WE_DESTWQ);
		opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE |
					       QM_FQCTRL_AVOIDBLOCK);
		qm_fqd_set_destwq(&opts.fqd, qman_affine_channel(cpu),
				  WGFP_CONF_WQ);
		err = qman_init_fq(cfq, QMAN_INITFQ_FLAG_SCHED, &opts);
		if (err) {
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: init confirm FQ cpu %u (0x%x): %d\n",
			       cpu, qman_fq_fqid(cfq), err);
			return err;
		}
		pr_info("dpaa_wgfp: confirm FQ fqid=0x%x on cpu %u (wq %u)\n",
			qman_fq_fqid(cfq), cpu, WGFP_CONF_WQ);
	}
	wgfp_tx_infra_ready = true;
	return 0;
}

static void wgfp_hole_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			     const union qm_mr_entry *msg)
{
	atomic64_inc(&wgfp_orp_hole_ern);
	pr_warn_ratelimited("dpaa_wgfp: ORP hole %u of fqid 0x%x rejected (rc 0x%02x)\n",
			    be16_to_cpu(msg->ern.seqnum), (u32)qm_fqid_get(&msg->ern),
			    msg->ern.rc);
}

static int wgfp_orp_sink_create(struct wgfp_sink *s, struct wgfp_port *port,
				u16 channel)
{
	struct qm_mcc_initfq opts;
	int err;

	if (WARN_ON_ONCE(s->created))
		return -EBUSY;
	memset(s, 0, sizeof(*s));
	s->pc = alloc_percpu(struct wgfp_sink_pc);
	if (!s->pc)
		return -ENOMEM;
	s->cpu = -1;
	s->port = port;
	s->orp = true;
	s->fq.cb.dqrr = wgfp_dqrr;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ |
				   QM_INITFQ_WE_TDTHRESH | QM_INITFQ_WE_ORPC);
	opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_TDE |
				       QM_FQCTRL_ORP | QM_FQCTRL_AVOIDBLOCK);
	opts.fqd.orpc = QM_FQD_ORPC(WGFP_ORP_RWS, WGFP_ORP_OA, WGFP_ORP_OLWS);
	qm_fqd_set_destwq(&opts.fqd, channel, WGFP_WQ);
	if (READ_ONCE(wgfp_orp_stash))
		wgfp_fqd_stash(&opts);
	err = qm_fqd_set_taildrop(&opts.fqd, READ_ONCE(wgfp_orp_td), 1);
	if (err >= 0)
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_ENQUEUE, &s->fq);
	if (err) {
		free_percpu(s->pc);
		s->pc = NULL;
		return err < 0 ? err : -EINVAL;
	}
	s->created = true;
	err = qman_init_fq(&s->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err)
		pr_err("dpaa_wgfp: qman_init_fq(%u) ORP = %d\n",
		       qman_fq_fqid(&s->fq), err);
	return err;
}

static int wgfp_port_orp_locked(struct wgfp_port *p, struct net_device *in)
{
	struct dpaa_priv *priv = netdev_priv(in);
	int err;

	if (p->orp)
		return 0;
	if (!READ_ONCE(wgfp_hole_ready)) {
		wgfp_hole_fq.cb.ern = wgfp_hole_ern_cb;
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_MODIFY, &wgfp_hole_fq);
		if (err) {
			pr_err("dpaa_wgfp: allocate hole tag queue: %d\n", err);
			return err;
		}
		WRITE_ONCE(wgfp_hole_ready, true);
	}
	wgfp_cpu_napi_ensure_locked();
	if (!p->orp_sink) {
		p->orp_sink = kzalloc(sizeof(*p->orp_sink), GFP_KERNEL);
		if (!p->orp_sink)
			return -ENOMEM;
	}
	err = wgfp_orp_sink_create(p->orp_sink, p, priv->channel);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		pr_err("dpaa_wgfp: ORP queue setup failed; retaining allocated queues until reboot\n");
		return err;
	}
	p->orp = true;
	pr_info("dpaa_wgfp: port ifindex %d ORP queue fqid=0x%x channel 0x%x\n",
		p->ifindex, qman_fq_fqid(&p->orp_sink->fq), priv->channel);
	return 0;
}

static int wgfp_sink_create(struct wgfp_sink *s, struct wgfp_port *port,
			    int cpu, bool dist)
{
	struct qm_mcc_initfq opts;
	int err;

	if (WARN_ON_ONCE(s->created))
		return -EBUSY;
	memset(s, 0, sizeof(*s));
	s->cpu = cpu;
	s->port = port;
	s->dist = dist;
	s->fq.cb.dqrr = wgfp_dqrr;

	memset(&opts, 0, sizeof(opts));
	opts.we_mask = cpu_to_be16(QM_INITFQ_WE_FQCTRL | QM_INITFQ_WE_DESTWQ |
				   QM_INITFQ_WE_TDTHRESH);
	opts.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_PREFERINCACHE | QM_FQCTRL_TDE);
	qm_fqd_set_destwq(&opts.fqd, qman_affine_channel(cpu), WGFP_WQ);
	if (READ_ONCE(wgfp_sink_stash))
		wgfp_fqd_stash(&opts);
	err = qm_fqd_set_taildrop(&opts.fqd, WGFP_SINK_TD, 1);
	if (err < 0)
		return err;

	err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
			     (dist ? QMAN_FQ_FLAG_NO_ENQUEUE : 0), &s->fq);
	if (err)
		return err;
	s->created = true;

	wgfp_cpu_napi_ensure_locked();
	err = qman_init_fq(&s->fq, QMAN_INITFQ_FLAG_SCHED, &opts);
	if (err)
		pr_err("dpaa_wgfp: qman_init_fq(%u) = %d\n",
		       qman_fq_fqid(&s->fq), err);
	return err;
}

static int wgfp_global_create(unsigned int n)
{
	const struct cpumask *affine = qman_affine_cpus();
	unsigned int i;
	int err;

	if (wgfp_pool_failed)
		return -EIO;
	if (wgfp_nglobal)
		return -EBUSY;
	if (!n || n > WGFP_MAX_SINKS)
		return -EINVAL;
	for (i = 0; i < n; i++)
		if (!cpumask_test_cpu(i, affine) || !cpu_online(i))
			return -ENODEV;

	for (i = 0; i < n; i++) {
		err = wgfp_sink_create(&wgfp_global[i], NULL, i, false);
		if (err) {
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: sink setup failed; retaining allocated queues until reboot\n");
			return err;
		}
		pr_info("dpaa_wgfp: global sink fqid=0x%x on cpu %u\n",
			qman_fq_fqid(&wgfp_global[i].fq), i);
	}
	wgfp_nglobal = n;
	return 0;
}

static struct wgfp_port *wgfp_port_find_locked(int ifindex)
{
	unsigned int i;

	for (i = 0; i < wgfp_nports; i++)
		if (wgfp_ports[i].ifindex == ifindex)
			return &wgfp_ports[i];
	return NULL;
}

static int wgfp_port_create(struct wgfp_port *p, unsigned int n)
{
	const struct cpumask *affine = qman_affine_cpus();
	unsigned int i;
	int err;

	memset(p, 0, sizeof(*p));
	for (i = 0; i < n; i++)
		if (!cpumask_test_cpu(i, affine) || !cpu_online(i))
			return -ENODEV;
	for (i = 0; i < n; i++) {
		err = wgfp_sink_create(&p->sink[i], p, i, false);
		if (err) {
			WRITE_ONCE(wgfp_pool_failed, true);
			pr_err("dpaa_wgfp: port sink setup failed; retaining allocated queues until reboot\n");
			return err;
		}
	}
	atomic_set(&p->rr, 0);
	p->nsinks = n;
	return 0;
}

static struct wgfp_port *wgfp_port_get_locked(struct net_device *in)
{
	struct wgfp_port *p;
	unsigned int n;
	int err;

	p = wgfp_port_find_locked(in->ifindex);
	if (p)
		return p;
	if (wgfp_pool_failed)
		return ERR_PTR(-EIO);
	if (wgfp_nports == WGFP_MAX_PORTS)
		return ERR_PTR(-ENOSPC);
	n = wgfp_per_port ? min_t(unsigned int, wgfp_per_port, WGFP_MAX_SINKS) :
			    min_t(unsigned int, WGFP_MAX_SINKS, num_online_cpus());
	if (!n)
		return ERR_PTR(-ENODEV);
	p = &wgfp_ports[wgfp_nports];
	err = wgfp_port_create(p, n);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		return ERR_PTR(err);
	}
	p->ifindex = in->ifindex;
	wgfp_nports++;
	pr_info("dpaa_wgfp: %s: %u worker sinks\n", in->name, n);
	return p;
}

static int wgfp_port_dist_locked(struct wgfp_port *p)
{
	int err;

	if (p->dist)
		return 0;
	if (!READ_ONCE(wgfp_rx_ern_ready)) {
		wgfp_rx_ern_fq.cb.ern = wgfp_rx_ern_cb;
		err = qman_create_fq(0, QMAN_FQ_FLAG_DYNAMIC_FQID |
				     QMAN_FQ_FLAG_NO_MODIFY, &wgfp_rx_ern_fq);
		if (err) {
			pr_err("dpaa_wgfp: allocate RX tag queue: %d\n", err);
			return err;
		}
		WRITE_ONCE(wgfp_rx_ern_ready, true);
	}
	err = wgfp_sink_create(&p->dist_sink, p, 0, true);
	if (err) {
		WRITE_ONCE(wgfp_pool_failed, true);
		pr_err("dpaa_wgfp: RX distributor setup failed; retaining allocated queues until reboot\n");
		return err;
	}
	p->dist = true;
	pr_info("dpaa_wgfp: port ifindex %d distributor fqid=0x%x\n",
		p->ifindex, qman_fq_fqid(&p->dist_sink.fq));
	return 0;
}

static ssize_t wgfp_ctl_write(struct file *file, const char __user *ubuf,
			      size_t count, loff_t *ppos)
{
	unsigned int n, home;
	char buf[48];
	int err = 0;

	if (!count || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	mutex_lock(&wgfp_lock);
	if (sscanf(buf, "create %u", &n) == 1) {
		if (wgfp_nports || (n && wgfp_per_port && wgfp_per_port != n)) {
			err = -EBUSY;
		} else {
			err = n ? wgfp_global_create(n) : 0;
			if (!err && n)
				wgfp_per_port = n;
		}
	} else if (sscanf(buf, "fanout %u %u", &n, &home) == 2) {
		if (n > WGFP_FANOUT_MAX || home == 1 ||
		    home > WGFP_FANOUT_HOME_MAX) {
			err = -EINVAL;
		} else {
			WRITE_ONCE(wgfp_fanout, n);
			WRITE_ONCE(wgfp_fanout_home, home);
		}
	} else if (sscanf(buf, "fanout %u", &n) == 1) {
		if (n > WGFP_FANOUT_MAX)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_fanout, n);
	} else if (sscanf(buf, "spread %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_spread, n);
	} else if (sscanf(buf, "orp_td %u", &n) == 1) {
		if (n < WGFP_ORP_TD_MIN || n > WGFP_ORP_TD_MAX)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_orp_td, n);
	} else if (sscanf(buf, "threaded %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else if (wgfp_nglobal || wgfp_nports || wgfp_cpu_napi_ready)
			err = -EBUSY;
		else
			err = dev_set_threaded(wgfp_ndev, n ?
					       NETDEV_NAPI_THREADED_ENABLED :
					       NETDEV_NAPI_THREADED_DISABLED);
		if (!err)
			WRITE_ONCE(wgfp_threaded, n);
	} else if (sscanf(buf, "prefetch_max %u", &n) == 1) {
		if (n > 65536)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_prefetch_max, n);
	} else if (sscanf(buf, "prefetch %u", &n) == 1) {
		if (n > 2)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_prefetch, n);
	} else if (sscanf(buf, "orp_stash %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_orp_stash, n);
	} else if (sscanf(buf, "tx_batch %u", &n) == 1) {
		if (n > WGFP_TXB_MAX)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_tx_batch, n);
	} else if (sscanf(buf, "sink_stash %u", &n) == 1) {
		if (n > 1)
			err = -EINVAL;
		else
			WRITE_ONCE(wgfp_sink_stash, n);
	} else if (sscanf(buf, "touch %u", &n) == 1) {
		if (n > 3)
			err = -EINVAL;
		else
			wgfp_touch = n;
	} else if (!strncmp(buf, "reset", 5)) {
		unsigned int i, j;
		int cpu;

		for (i = 0; i < wgfp_nglobal; i++) {
			struct wgfp_sink *s = &wgfp_global[i];

			s->frames = s->bytes = s->errs = s->sg = 0;
			s->irqs = s->sum = 0;
		}
		for (i = 0; i < wgfp_nports; i++) {
			struct wgfp_port *p = &wgfp_ports[i];

			for (j = 0; j < p->nsinks; j++) {
				struct wgfp_sink *s = &p->sink[j];

				s->frames = s->bytes = s->errs = s->sg = 0;
				s->irqs = s->sum = 0;
			}
			if (p->dist) {
				struct wgfp_sink *s = &p->dist_sink;

				s->frames = s->bytes = s->errs = s->sg = 0;
				s->irqs = s->sum = 0;
			}
		}
		for (i = 0; i < wgfp_nports; i++) {
			struct wgfp_port *p = &wgfp_ports[i];
			struct wgfp_sink *o = p->orp_sink;

			if (!o || !o->pc)
				continue;
			for_each_possible_cpu(cpu)
				*per_cpu_ptr(o->pc, cpu) = (struct wgfp_sink_pc){};
			o->errs = o->sg = 0;
			o->irqs = 0;
		}
		for_each_possible_cpu(cpu)
			atomic64_set(per_cpu_ptr(&wgfp_orp_frames, cpu), 0);
		atomic64_set(&wgfp_orp_holes, 0);
		atomic64_set(&wgfp_orp_wait_fail, 0);
		atomic64_set(&wgfp_orp_hole_ern, 0);
	} else if (!strncmp(buf, "destroy", 7)) {
		pr_warn_ratelimited("dpaa_wgfp: sinks are boot-lifetime; reboot to release queues\n");
		err = -EOPNOTSUPP;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&wgfp_lock);
	if (err)
		return err;
	return count;
}

static const struct file_operations wgfp_ctl_fops = {
	.owner	= THIS_MODULE,
	.write	= wgfp_ctl_write,
	.llseek	= noop_llseek,
};

static void wgfp_sink_show(struct seq_file *m, unsigned int idx,
			   struct wgfp_sink *s, const char *kind, int ifindex,
			   bool query_fq)
{
	struct qm_mcr_queryfq_np np;
	u64 frames = s->frames, bytes = s->bytes;
	int err = 0, cpu;

	if (s->pc) {
		for_each_possible_cpu(cpu) {
			frames += per_cpu_ptr(s->pc, cpu)->frames;
			bytes += per_cpu_ptr(s->pc, cpu)->bytes;
		}
	}
	if (query_fq) {
		memset(&np, 0, sizeof(np));
		err = qman_query_fq_np(&s->fq, &np);
	}
	seq_printf(m,
		   "sink%u port=%d kind=%s cpu=%d fqid=0x%x frames=%llu bytes=%llu errs=%llu sg=%llu irqs=%llu",
		   idx, ifindex, kind, s->cpu, qman_fq_fqid(&s->fq), frames,
		   bytes, s->errs, s->sg, s->irqs);
	if (!query_fq)
		goto out;
	if (!err) {
		seq_printf(m, " state=0x%02x frm_cnt=%u byte_cnt=%u frm_cnt_raw=0x%08x byte_cnt_raw=0x%08x",
			   np.state & QM_MCR_NP_STATE_MASK,
			   be32_to_cpu(np.frm_cnt) & qm_mcr_frm_cnt_mask,
			   be32_to_cpu(np.byte_cnt),
			   np.frm_cnt, np.byte_cnt);
	} else {
		seq_printf(m, " query_err=%d", err);
	}
out:
	seq_putc(m, '\n');
}

static int wgfp_stats_emit(struct seq_file *m, bool query_fq)
{
	unsigned int i, j, idx = 0, total;

	mutex_lock(&wgfp_lock);
	total = wgfp_nglobal;
	for (i = 0; i < wgfp_nports; i++)
		total += wgfp_ports[i].nsinks + !!wgfp_ports[i].dist +
			 !!wgfp_ports[i].orp;
	seq_printf(m, "touch=%u sinks=%u per_port=%u fanout=%u fanout_home=%u ports=%u global=%u pool_failed=%d ops=%d egress=%d xmit_ok=%llu xmit_fail=%llu xmit_ern=%llu xmit_frame_wait_fail=%llu xmit_poll_wait_fail=%llu xmit_invalid_egress=%llu xmit_orp_wait_fail=%llu xmit_nomem=%llu xmit_qdisc_drop=%llu xmit_cn=%llu rx_enq_fail=%llu rx_ern=%llu spread=%d orp_td=%u orp_stash=%d sink_stash=%d prefetch=%u prefetch_max=%u threaded=%d tx_batch=%u orp_frames=%llu orp_holes=%llu orp_wait_fail=%llu orp_hole_ern=%llu xmit_conf_ok=%llu xmit_no_slot=%llu xmit_tx_err=%llu xmit_dup_comp=%llu xmit_ern_cgr_td=%llu xmit_ern_orp_late=%llu xmit_ern_orp_early=%llu xmit_ern_other=%llu frm_cnt_encoding=%s\n",
		   wgfp_touch, total, wgfp_per_port, READ_ONCE(wgfp_fanout),
		   READ_ONCE(wgfp_fanout_home),
		   wgfp_nports, wgfp_nglobal,
		   READ_ONCE(wgfp_pool_failed),
		   !!rcu_access_pointer(wgfp_ops),
		   READ_ONCE(wgfp_neg), dpaa_wgfp_stat_sum(&wgfp_xmit_ok),
		   (u64)atomic64_read(&wgfp_xmit_fail),
		   (u64)atomic64_read(&wgfp_xmit_ern),
		   (u64)atomic64_read(&wgfp_xmit_frame_wait_fail),
		   (u64)atomic64_read(&wgfp_xmit_poll_wait_fail),
		   (u64)atomic64_read(&wgfp_xmit_invalid_egress),
		   (u64)atomic64_read(&wgfp_xmit_orp_wait_fail),
		   (u64)atomic64_read(&wgfp_xmit_nomem),
		   (u64)atomic64_read(&wgfp_xmit_qdisc_drop),
		   (u64)atomic64_read(&wgfp_xmit_cn),
		   (u64)atomic64_read(&wgfp_rx_enq_fail),
		   (u64)atomic64_read(&wgfp_rx_ern),
		   READ_ONCE(wgfp_spread), READ_ONCE(wgfp_orp_td),
		   READ_ONCE(wgfp_orp_stash), READ_ONCE(wgfp_sink_stash),
		   READ_ONCE(wgfp_prefetch),
		   READ_ONCE(wgfp_prefetch_max), READ_ONCE(wgfp_threaded),
		   READ_ONCE(wgfp_tx_batch),
		   dpaa_wgfp_stat_sum(&wgfp_orp_frames),
		   (u64)atomic64_read(&wgfp_orp_holes),
		   (u64)atomic64_read(&wgfp_orp_wait_fail),
		   (u64)atomic64_read(&wgfp_orp_hole_ern),
		   dpaa_wgfp_stat_sum(&wgfp_xmit_conf_ok),
		   (u64)atomic64_read(&wgfp_xmit_no_slot),
		   (u64)atomic64_read(&wgfp_xmit_tx_err),
		   (u64)atomic64_read(&wgfp_xmit_dup_comp),
		   (u64)atomic64_read(&wgfp_xmit_ern_cgr_td),
		   (u64)atomic64_read(&wgfp_xmit_ern_orp_late),
		   (u64)atomic64_read(&wgfp_xmit_ern_orp_early),
		   (u64)atomic64_read(&wgfp_xmit_ern_other),
		   query_fq ? "host" : "none");
	for (i = 0; i < wgfp_nglobal; i++)
		wgfp_sink_show(m, idx++, &wgfp_global[i], "global", 0, query_fq);
	for (i = 0; i < wgfp_nports; i++) {
		struct wgfp_port *p = &wgfp_ports[i];

		for (j = 0; j < p->nsinks; j++)
			wgfp_sink_show(m, idx++, &p->sink[j], "worker",
				       p->ifindex, query_fq);
		if (p->dist)
			wgfp_sink_show(m, idx++, &p->dist_sink, "dist",
				       p->ifindex, query_fq);
		if (p->orp)
			wgfp_sink_show(m, idx++, p->orp_sink, "orp",
				       p->ifindex, query_fq);
	}
	mutex_unlock(&wgfp_lock);
	return 0;
}

static int wgfp_stats_show(struct seq_file *m, void *unused)
{
	return wgfp_stats_emit(m, true);
}

static int wgfp_stats_sw_show(struct seq_file *m, void *unused)
{
	return wgfp_stats_emit(m, false);
}
DEFINE_SHOW_ATTRIBUTE(wgfp_stats);
DEFINE_SHOW_ATTRIBUTE(wgfp_stats_sw);

int dpaa_wgfp_register(const struct dpaa_wgfp_ops *ops, void *priv)
{
	int ret = 0;

	mutex_lock(&wgfp_ops_quiesce_lock);
	mutex_lock(&wgfp_lock);
	if (rcu_access_pointer(wgfp_ops)) {
		ret = -EBUSY;
	} else {
		if (!wgfp_per_port)
			wgfp_per_port = min_t(unsigned int, WGFP_MAX_SINKS,
					      num_online_cpus());
		if (!wgfp_per_port)
			ret = -ENODEV;
		if (!ret) {
			wgfp_ops_priv = priv;
			rcu_assign_pointer(wgfp_ops, ops);
		}
	}
	mutex_unlock(&wgfp_lock);
	mutex_unlock(&wgfp_ops_quiesce_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_register);

void dpaa_wgfp_unregister(void)
{
	mutex_lock(&wgfp_ops_quiesce_lock);
	mutex_lock(&wgfp_lock);
	rcu_assign_pointer(wgfp_ops, NULL);
	synchronize_rcu();
	mutex_unlock(&wgfp_lock);
	mutex_unlock(&wgfp_ops_quiesce_lock);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_unregister);

int dpaa_wgfp_port_quiesce(struct net_device *dev)
{
	const struct dpaa_wgfp_ops *ops;
	void *priv;
	int ret = 0;

	if (!dev)
		return 0;
	mutex_lock(&wgfp_ops_quiesce_lock);
	ops = rcu_dereference_protected(wgfp_ops,
					lockdep_is_held(&wgfp_ops_quiesce_lock));
	priv = wgfp_ops_priv;
	if (ops && ops->port_quiesce)
		ret = ops->port_quiesce(priv, dev);
	mutex_unlock(&wgfp_ops_quiesce_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_port_quiesce);

int dpaa_wgfp_policy_revoke(void)
{
	const struct dpaa_wgfp_ops *ops;
	void *priv;
	int ret = 0;

	mutex_lock(&wgfp_ops_quiesce_lock);
	ops = rcu_dereference_protected(wgfp_ops,
					lockdep_is_held(&wgfp_ops_quiesce_lock));
	priv = wgfp_ops_priv;
	if (ops && ops->policy_revoke)
		ret = ops->policy_revoke(priv);
	mutex_unlock(&wgfp_ops_quiesce_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_policy_revoke);

static void wgfp_rx_ern_cb(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg)
{
	atomic64_inc(&wgfp_rx_ern);
	dpaa_wgfp_fd_release(&msg->ern.fd);
}

/* Caller must invoke only after eg->active=false + synchronize_rcu() (freezing
 * every CPU's alloc_cnt) so per-CPU alloc_cnt >= local_done + remote_done. */
static int wgfp_eg_inflight_sum(const struct wgfp_egress *eg)
{
	unsigned int cpu;
	s64 total = 0;

	for (cpu = 0; cpu < eg->ncpus; cpu++) {
		const struct wgfp_egress_pc *pc = &eg->pc[cpu];

		total += (s64)(READ_ONCE(pc->alloc_cnt) -
			       smp_load_acquire(&pc->local_done) -
			       (u64)atomic64_read_acquire(&pc->remote_done));
	}
	return (int)total;
}

static int wgfp_eg_alloc_slots(struct wgfp_egress *eg, struct device *dma_dev)
{
	unsigned int ncpus = min_t(unsigned int, WGFP_MAX_SINKS, num_possible_cpus());
	unsigned int total = ncpus * WGFP_TX_SLOTS_PER_CPU;
	unsigned int cpu, i, mapped = 0;
	struct wgfp_tx_slot *slots;
	struct wgfp_egress_pc *pc;

	slots = kcalloc(total, sizeof(*slots), GFP_KERNEL);
	pc = kcalloc(ncpus, sizeof(*pc), GFP_KERNEL);
	if (!slots || !pc) {
		kfree(slots);
		kfree(pc);
		return -ENOMEM;
	}
	for (cpu = 0; cpu < ncpus; cpu++) {
		pc[cpu].alloc_cnt = 0;
		pc[cpu].local_done = 0;
		atomic64_set(&pc[cpu].remote_done, 0);
		spin_lock_init(&pc[cpu].remote_lock);
		pc[cpu].free_top = 0;
		pc[cpu].remote_top = 0;
		for (i = 0; i < WGFP_TX_SLOTS_PER_CPU; i++) {
			struct wgfp_tx_slot *s = &slots[cpu * WGFP_TX_SLOTS_PER_CPU + i];
			struct wgfp_tx_meta *meta;
			struct page *page;
			dma_addr_t dma;

			page = alloc_page(GFP_KERNEL);
			if (!page)
				goto unwind;
			dma = dma_map_page(dma_dev, page, 0, PAGE_SIZE,
					   DMA_TO_DEVICE);
			if (dma_mapping_error(dma_dev, dma)) {
				__free_page(page);
				goto unwind;
			}
			s->eg = eg;
			s->buf = page_address(page);
			s->dma = dma;
			atomic_set(&s->state, WGFP_SLOT_FREE);
			s->owner_cpu = cpu;
			s->local_idx = i;
			meta = (struct wgfp_tx_meta *)s->buf;
			meta->magic = WGFP_TX_META_MAGIC;
			meta->slot = s;
			pc[cpu].free_stack[pc[cpu].free_top++] = i;
			mapped++;
		}
	}
	eg->ncpus = ncpus;
	eg->slots = slots;
	eg->pc = pc;
	return 0;

unwind:
	for (i = 0; i < mapped; i++) {
		dma_unmap_page(dma_dev, slots[i].dma, PAGE_SIZE, DMA_TO_DEVICE);
		__free_page(virt_to_page(slots[i].buf));
	}
	kfree(slots);
	kfree(pc);
	return -ENOMEM;
}

static void wgfp_eg_free_slots_unpublished(struct wgfp_egress *eg,
					   struct device *dma_dev)
{
	unsigned int i, total = eg->ncpus * WGFP_TX_SLOTS_PER_CPU;

	for (i = 0; i < total; i++) {
		dma_unmap_page(dma_dev, eg->slots[i].dma, PAGE_SIZE,
			       DMA_TO_DEVICE);
		__free_page(virt_to_page(eg->slots[i].buf));
	}
	kfree(eg->slots);
	kfree(eg->pc);
	eg->slots = NULL;
	eg->pc = NULL;
	eg->ncpus = 0;
}

static void wgfp_eg_publish_slots(struct wgfp_egress *eg)
{
	unsigned int i, total = eg->ncpus * WGFP_TX_SLOTS_PER_CPU;

	for (i = 0; i < total; i++)
		hlist_add_head_rcu(&eg->slots[i].dma_node,
				   &wgfp_tx_dma_ht[wgfp_dma_hash(eg->slots[i].dma)]);
	atomic_add(total, &wgfp_tx_slots_installed);
}

int dpaa_wgfp_egress(struct net_device *dev)
{
	struct wgfp_egress *eg;
	struct device *tx_dma_dev = NULL;
	struct dpaa_priv *priv;
	u32 fqid = 0;
	int i, n, ret;

	if (!dpaa_get_rx_fman_port(dev))
		return -ENODEV;

	/* Lock order B1: rtnl_lock() outside wgfp_lock (matches wgfp_netdev_event). */
	rtnl_lock();
	mutex_lock(&wgfp_lock);
	if (!netif_running(dev)) {
		ret = -ENETDOWN;
		goto out;
	}
	n = wgfp_neg;
	for (i = 0; i < n; i++) {
		eg = &wgfp_egresses[i];
		if (eg->dev == dev) {
			if (eg->retired || eg->quarantined) {
				ret = -EBUSY;
				goto out;
			}
#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)
			if (smp_load_acquire(&((struct dpaa_priv *)netdev_priv(dev))->ceetm)) {
				ret = -EBUSY;
				goto out;
			}
#endif
			if (!eg->active) {
				WRITE_ONCE(eg->active, true);
				rcu_assign_pointer(wgfp_eg_dev[i], dev);
			}
			ret = i;
			goto out;
		}
		if (eg->active && eg->ifindex == dev->ifindex) {
			ret = -EBUSY;
			goto out;
		}
	}
	if (n >= WGFP_MAX_EGRESS) {
		ret = -ENOSPC;
		goto out;
	}
	ret = wgfp_tx_infra_ensure_locked();
	if (ret)
		goto out;
	priv = netdev_priv(dev);
	eg = &wgfp_egresses[n];
	memset(eg, 0, sizeof(*eg));
	/* Allocate pages before allocating the QMan FQ; publish into
	 * wgfp_tx_dma_ht only after dpaa_alloc_wgfp_tx_fq() succeeds. */
	ret = wgfp_eg_alloc_slots(eg, priv->tx_dma_dev);
	if (ret)
		goto out;
	ret = dpaa_alloc_wgfp_tx_fq(dev, &fqid, &tx_dma_dev);
	if (ret) {
		wgfp_eg_free_slots_unpublished(eg, priv->tx_dma_dev);
		goto out;
	}
	eg->dev = dev;
	eg->tx_dma_dev = tx_dma_dev;
	eg->tx_dma_need_sync = tx_dma_dev && dma_dev_need_sync(tx_dma_dev);
	eg->ifindex = dev->ifindex;
	eg->tx_fqid = fqid;
	wgfp_eg_publish_slots(eg);
	WRITE_ONCE(eg->active, true);
	rcu_assign_pointer(wgfp_eg_dev[n], dev);
	smp_store_release(&wgfp_neg, n + 1);
	pr_info("dpaa_wgfp: egress %u = %s, confirmed tx fqid=0x%x (%u slots/cpu)\n",
		n, dev->name, fqid, WGFP_TX_SLOTS_PER_CPU);
	ret = n;
out:
	mutex_unlock(&wgfp_lock);
	rtnl_unlock();
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_egress);

int dpaa_wgfp_egress_find(const struct net_device *dev)
{
	int i, n = smp_load_acquire(&wgfp_neg);

	for (i = 0; i < n; i++)
		if (rcu_access_pointer(wgfp_eg_dev[i]) == dev)
			return i;
	return -ENOENT;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_egress_find);

/* Deactivate and drain any WGFP egress slots bound to @dev.
 * Caller holds RTNL (NETDEV_GOING_DOWN, NETDEV_UNREGISTER, or CEETM attach).
 * Does NOT call napi_synchronize() on the shared per-CPU wgfp_cpu_napi (which
 * stays NAPI_STATE_SCHED continuously under sustained traffic on other ports);
 * instead, dpaa_wgfp_tx_alloc() checks eg->active and increments alloc_cnt
 * inside rcu_read_lock(), and wgfp_cpu_poll() flushes wgfp_txb on every poll
 * iteration, so synchronize_rcu() followed by waiting for
 * wgfp_eg_inflight_sum(eg) == 0 is both exact and deadlock-free. */
static void wgfp_eg_deactivate_dev(struct net_device *dev, bool retire)
{
	unsigned int i, n;
	bool changed = false;

	mutex_lock(&wgfp_lock);
	n = smp_load_acquire(&wgfp_neg);
	for (i = 0; i < n; i++) {
		struct wgfp_egress *eg = &wgfp_egresses[i];

		if (eg->dev != dev)
			continue;
		if (retire)
			WRITE_ONCE(eg->retired, true);
		if (READ_ONCE(eg->active) ||
		    rcu_access_pointer(wgfp_eg_dev[i]) == dev) {
			WRITE_ONCE(eg->active, false);
			rcu_assign_pointer(wgfp_eg_dev[i], NULL);
			changed = true;
		}
	}
	if (changed) {
		unsigned long deadline;

		synchronize_rcu();
		deadline = jiffies + msecs_to_jiffies(WGFP_DRAIN_TIMEOUT_MS);
		for (i = 0; i < n; i++) {
			struct wgfp_egress *eg = &wgfp_egresses[i];
			int inflight;

			if (eg->dev != dev || !eg->pc)
				continue;
			while ((inflight = wgfp_eg_inflight_sum(eg)) > 0 &&
			       time_before(jiffies, deadline))
				msleep(1);
			if (inflight > 0) {
				WRITE_ONCE(eg->quarantined, true);
				pr_err("dpaa_wgfp: egress %u (%s) drain timed out with %d inflight; quarantining slot\n",
				       i, dev->name, inflight);
			}
		}
	}
	mutex_unlock(&wgfp_lock);
}

void dpaa_wgfp_egress_deactivate(struct net_device *dev)
{
	ASSERT_RTNL();
	if (!dev)
		return;
	wgfp_eg_deactivate_dev(dev, false);
}

static int wgfp_netdev_event(struct notifier_block *nb, unsigned long event,
			     void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);

	if (event != NETDEV_GOING_DOWN && event != NETDEV_UNREGISTER)
		return NOTIFY_DONE;

	wgfp_eg_deactivate_dev(dev, event == NETDEV_UNREGISTER);
	if (event == NETDEV_UNREGISTER) {
		unsigned int i;

		/* Reset need_sync to 1 (conservative sync) before clearing the
		 * cached dev pointer so in-flight frames never skip DMA sync. */
		for (i = 0; i < WGFP_MAX_BPIDS; i++)
			WRITE_ONCE(wgfp_bpid_need_sync[i], 1);
		smp_wmb();
		for (i = 0; i < WGFP_MAX_BPIDS; i++)
			WRITE_ONCE(wgfp_bpid_dev[i], NULL);
	}
	return NOTIFY_OK;
}

static struct notifier_block wgfp_netdev_notifier = {
	.notifier_call = wgfp_netdev_event,
};

static int wgfp_fm_port(struct net_device *dev, struct fman **fm, u8 *port_id,
			int *key_size)
{
	struct fman_port *port = dpaa_get_rx_fman_port(dev);
	struct dpaa_priv *priv;
	int ks;

	if (!port)
		return -ENODEV;
	priv = netdev_priv(dev);
	*fm = fman_bind(priv->mac_dev->fman_dev);
	if (!*fm)
		return -ENODEV;
	*port_id = fman_port_get_id(port);
	ks = fman_pcd_fe_ehash_keysize(*fm, *port_id);
	if (ks < 0)
		return ks;
	if (ks != FMAN_PCD_FE_ROUTED_KEY_SIZE)
		return -EOPNOTSUPP;
	*key_size = ks;
	return 0;
}

static void wgfp_key(const struct dpaa_wgfp_tuple *t, u8 *key)
{
	memset(key, 0, FMAN_PCD_FE_ROUTED_KEY_SIZE);
	if (t->family == AF_INET6) {
		key[0] = 0x60;
		memcpy(key + 9, &t->saddr6, 16);
		memcpy(key + 25, &t->daddr6, 16);
	} else {
		key[0] = 0x40;
		memcpy(key + 1, &t->saddr, 4);
		memcpy(key + 5, &t->daddr, 4);
	}
	key[41] = t->proto;
	memcpy(key + 42, &t->sport, 2);
	memcpy(key + 44, &t->dport, 2);
	memcpy(key + 46, &t->vlan_vid, 2);
	memcpy(key + 48, &t->pppoe_sid, 2);
}

static dpaa_wgfp_family_fn __rcu wgfp_family_fn;

void dpaa_wgfp_set_family_policy(dpaa_wgfp_family_fn fn)
{
	rcu_assign_pointer(wgfp_family_fn, fn);
	if (!fn)
		synchronize_rcu();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_set_family_policy);

bool dpaa_wgfp_family_ok(struct net_device *dev, u8 family)
{
	dpaa_wgfp_family_fn fn;
	u8 want, mask = 0;

	want = family == AF_INET ? DPAA_WGFP_FAM_V4 :
	       family == AF_INET6 ? DPAA_WGFP_FAM_V6 : 0;
	if (!dev || !want)
		return false;
	rcu_read_lock();
	fn = rcu_dereference(wgfp_family_fn);
	if (fn)
		mask = fn(dev);
	rcu_read_unlock();
	return mask & want;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_family_ok);

bool dpaa_wgfp_port_ok(struct net_device *dev)
{
	struct fman *fm;
	u8 port_id;
	int ks;

	return !wgfp_fm_port(dev, &fm, &port_id, &ks);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_port_ok);

int dpaa_wgfp_flow_add(struct net_device *in, const struct dpaa_wgfp_tuple *t,
		       u32 hash, bool rx)
{
	struct fman_pcd_fe_flow_action action;
	struct wgfp_sink *sink;
	struct wgfp_port *port;
	struct fman *fm;
	u8 port_id;
	int ks, err;

	if (!dpaa_wgfp_family_ok(in, t->family))
		return -EOPNOTSUPP;
	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	might_sleep();
	mutex_lock(&wgfp_lock);
	port = wgfp_port_get_locked(in);
	if (IS_ERR(port)) {
		mutex_unlock(&wgfp_lock);
		return PTR_ERR(port);
	}
	if (rx && READ_ONCE(wgfp_spread) && !wgfp_port_orp_locked(port, in))
		sink = port->orp_sink;
	else if (rx && READ_ONCE(wgfp_fanout) && !wgfp_port_dist_locked(port))
		sink = &port->dist_sink;
	else
		sink = &port->sink[hash % port->nsinks];
	memset(&action, 0, sizeof(action));
	wgfp_key(t, action.key);
	action.key_size = ks;
	action.table_idx = 0;
	action.rx_fqid = qman_fq_fqid(&sink->fq);
	action.flags = FMAN_PCD_FE_OWNER_WGFP;
	mutex_unlock(&wgfp_lock);
	err = fman_pcd_fe_flow_add(fm, port_id, &action);
	return err ? err : 0;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_add);

int dpaa_wgfp_flow_del(struct net_device *in, const struct dpaa_wgfp_tuple *t)
{
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];
	struct fman *fm;
	unsigned int wedged;
	u8 port_id;
	int ks, err;

	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	wgfp_key(t, key);
	wedged = fman_pcd_ehash_sync_wedged_count();
	err = fman_pcd_fe_flow_del_owner(fm, port_id, key, ks,
					 FMAN_PCD_FE_OWNER_WGFP);
	if (!err && fman_pcd_ehash_sync_wedged_count() != wedged)
		return -ETIMEDOUT;
	return err;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_del);

int dpaa_wgfp_flow_packets(struct net_device *in,
			   const struct dpaa_wgfp_tuple *t, u64 *packets)
{
	u8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];
	struct fman *fm;
	u64 bytes;
	u8 port_id;
	int ks, err;

	err = wgfp_fm_port(in, &fm, &port_id, &ks);
	if (err)
		return err;
	wgfp_key(t, key);
	return fman_pcd_fe_flow_get_stats(fm, port_id, key, ks, packets, &bytes,
					  NULL);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_flow_packets);

static void wgfp_frame_release(struct dpaa_wgfp_frame *f)
{
	wgfp_frame_release_fast(f);
}

void dpaa_wgfp_drop(struct dpaa_wgfp_frame *f)
{
	wgfp_frame_release(f);
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_drop);

bool dpaa_wgfp_tx_alloc(int egress, int expected_ifindex, u32 max_len,
			struct dpaa_wgfp_tx_buf *out)
{
	struct wgfp_egress *eg;
	struct wgfp_egress_pc *pc;
	struct wgfp_tx_slot *slot;
	struct net_device *dev;
	bool in_napi;
	int cpu;

	if (unlikely(!out || max_len > DPAA_WGFP_TX_BUF_SIZE))
		return false;

	/* Inside wgfp_cpu_poll() (99.9999% of calls), BH is already disabled
	 * so skip redundant local_bh_disable/enable (__local_bh_enable_ip).
	 * Always hold rcu_read_lock() so wgfp_eg_dev[] dereference and
	 * alloc_cnt ordering with synchronize_rcu() hold for every caller. */
	in_napi = this_cpu_read(wgfp_bman_rel.active);
	if (unlikely(!in_napi))
		local_bh_disable();
	rcu_read_lock();
	if (unlikely((unsigned int)egress >= WGFP_MAX_EGRESS)) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		goto fail;
	}
	eg = &wgfp_egresses[egress];
	dev = rcu_dereference(wgfp_eg_dev[egress]);
	if (unlikely(!dev || !netif_running(dev) ||
		     !READ_ONCE(eg->active) || READ_ONCE(eg->quarantined) ||
		     (expected_ifindex > 0 && eg->ifindex != expected_ifindex))) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		goto fail;
	}
	cpu = smp_processor_id();
	if (unlikely(cpu >= (int)eg->ncpus ||
		     !qman_fq_fqid(&wgfp_conf_fqs[cpu]))) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		goto fail;
	}
	slot = wgfp_slot_pop_bh(eg, cpu);
	if (unlikely(!slot)) {
		atomic64_inc(&wgfp_xmit_no_slot);
		goto fail;
	}
	/* Ordered with wgfp_eg_deactivate_dev() via synchronize_rcu() after
	 * eg->active = false: no extra smp_mb__() needed on hot path. */
	pc = &eg->pc[cpu];
	WRITE_ONCE(pc->alloc_cnt, pc->alloc_cnt + 1);
	atomic_set(&slot->state, WGFP_SLOT_ALLOC);
	out->buf = slot->buf;
	out->buf_size = PAGE_SIZE;
	out->ifindex = eg->ifindex;
	out->priv = slot;
	rcu_read_unlock();
	if (unlikely(!in_napi))
		local_bh_enable();
	return true;
fail:
	rcu_read_unlock();
	if (unlikely(!in_napi))
		local_bh_enable();
	return false;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_tx_alloc);

void dpaa_wgfp_tx_cancel(struct dpaa_wgfp_tx_buf *tx)
{
	struct wgfp_tx_slot *slot;
	bool in_napi;

	if (unlikely(!tx || !tx->priv))
		return;
	slot = tx->priv;
	tx->priv = NULL;
	in_napi = this_cpu_read(wgfp_bman_rel.active);
	if (unlikely(!in_napi))
		local_bh_disable();
	if (likely(atomic_cmpxchg_relaxed(&slot->state, WGFP_SLOT_ALLOC,
					  WGFP_SLOT_FREE) == WGFP_SLOT_ALLOC))
		wgfp_slot_return_done(slot, true);
	if (unlikely(!in_napi))
		local_bh_enable();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_tx_cancel);

static bool wgfp_orp_submit_slot(struct wgfp_tx_slot *slot,
				 const struct qm_fd *fd)
{
	struct wgfp_orp_ctx *c = this_cpu_ptr(&wgfp_orp_ctx);
	struct wgfp_egress *eg = slot->eg;
	ktime_t end = 0;

	if (likely(!c->owed))
		return false;
	if (unlikely(!READ_ONCE(eg->active) || READ_ONCE(eg->quarantined))) {
		atomic_set(&slot->state, WGFP_SLOT_FREE);
		wgfp_slot_return_done(slot, true);
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		return true;
	}
	smp_store_release(&slot->state.counter, WGFP_SLOT_INFLIGHT);
	for (;;) {
		if (!qman_enqueue_orp_fqid(wgfp_tx_ern_fq, eg->tx_fqid,
					   c->orp_fqid, c->seq, false, fd)) {
			c->owed = false;
			dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
			return true;
		}
		if (unlikely(!READ_ONCE(eg->active) ||
			     READ_ONCE(eg->quarantined)))
			break;
		if (!end)
			end = ktime_add_ns(ktime_get(), WGFP_ORP_WAIT_NS);
		else if (!ktime_before(ktime_get(), end))
			break;
		cpu_relax();
	}
	atomic64_inc(&wgfp_orp_wait_fail);
	atomic64_inc(&wgfp_xmit_orp_wait_fail);
	atomic64_inc(&wgfp_xmit_fail);
	wgfp_slot_unwind_failed_enq(slot, true);
	return true;
}

void dpaa_wgfp_tx_submit(struct dpaa_wgfp_frame *f, struct dpaa_wgfp_tx_buf *tx,
			 u32 off, u32 len)
{
	struct wgfp_tx_slot *slot;
	struct wgfp_egress *eg;
	struct wgfp_txb *b;
	struct qm_fd fd;
	bool in_napi;

	if (f)
		wgfp_frame_release_fast(f);
	if (unlikely(!tx || !tx->priv))
		return;
	if (unlikely(off < DPAA_WGFP_TX_HEADROOM || off > WGFP_FD_MAX_OFF ||
		     len < ETH_HLEN || len > PAGE_SIZE - off)) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		dpaa_wgfp_tx_cancel(tx);
		return;
	}
	slot = tx->priv;
	tx->priv = NULL;
	eg = slot->eg;

	if (unlikely(eg->tx_dma_need_sync))
		dma_sync_single_for_device(eg->tx_dma_dev, slot->dma + off, len,
					   DMA_TO_DEVICE);

	qm_fd_clear_fd(&fd);
	fd.bpid = WGFP_BPID_INV;
	qm_fd_addr_set64(&fd, slot->dma);
	qm_fd_set_contig(&fd, off, len);
	fd.cmd = cpu_to_be32(FM_FD_CMD_FCO |
			     qman_fq_fqid(&wgfp_conf_fqs[slot->owner_cpu]));

	in_napi = this_cpu_read(wgfp_bman_rel.active);
	if (unlikely(!in_napi))
		local_bh_disable();
	if (unlikely(wgfp_orp_submit_slot(slot, &fd))) {
		if (unlikely(!in_napi))
			local_bh_enable();
		return;
	}
	b = this_cpu_ptr(&wgfp_txb);
	if (b->active) {
		atomic_set(&slot->state, WGFP_SLOT_BATCHED);
		b->slot[b->n] = slot;
		b->fd[b->n] = fd;
		if (++b->n >= b->lim)
			wgfp_txb_flush(b);
		if (unlikely(!in_napi))
			local_bh_enable();
		return;
	}
	wgfp_tx_enqueue_slot(slot, &fd,
			     (in_napi && b->threaded) ? &b->end : NULL, true);
	if (unlikely(!in_napi))
		local_bh_enable();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_tx_submit);

static void wgfp_prepare_xmit_skb(struct sk_buff *skb)
{
	const struct ethhdr *eth;
	u16 protocol;
	u32 network_off, new_len = skb->len;

	skb_reset_mac_header(skb);
	eth = eth_hdr(skb);
	skb->protocol = eth->h_proto;
	skb->ip_summed = CHECKSUM_NONE;
	protocol = ntohs(skb->protocol);
	network_off = ETH_HLEN;
	if ((protocol == ETH_P_8021Q || protocol == ETH_P_8021AD) &&
	    new_len >= VLAN_ETH_HLEN) {
		const struct vlan_hdr *vlan =
			(const struct vlan_hdr *)(skb->data + ETH_HLEN);

		protocol = ntohs(vlan->h_vlan_encapsulated_proto);
		network_off = VLAN_ETH_HLEN;
	}
	if ((protocol == ETH_P_IP &&
	     new_len >= network_off + sizeof(struct iphdr)) ||
	    (protocol == ETH_P_IPV6 &&
	     new_len >= network_off + sizeof(struct ipv6hdr))) {
		u32 transport_off = network_off;

		if (protocol == ETH_P_IP) {
			const struct iphdr *iph =
				(const struct iphdr *)(skb->data + network_off);
			u32 ihl = iph->ihl * 4;

			transport_off += ihl >= sizeof(*iph) &&
					new_len >= network_off + ihl ?
					ihl : sizeof(*iph);
		} else {
			transport_off += sizeof(struct ipv6hdr);
		}
		skb_set_network_header(skb, network_off);
		skb_set_transport_header(skb, transport_off);
	} else {
		skb_set_network_header(skb, network_off);
		skb_set_transport_header(skb, network_off);
	}
}

static void wgfp_submit_skb(struct sk_buff *skb)
{
	int err = dev_queue_xmit(skb);

	if (likely(err == NET_XMIT_SUCCESS || err == NET_XMIT_CN)) {
		local_bh_disable();
		dpaa_wgfp_stat_inc(&wgfp_xmit_ok);
		local_bh_enable();
		if (unlikely(err == NET_XMIT_CN))
			atomic64_inc(&wgfp_xmit_cn);
	} else {
		atomic64_inc(&wgfp_xmit_fail);
		atomic64_inc(&wgfp_xmit_qdisc_drop);
	}
}

void dpaa_wgfp_xmit(struct dpaa_wgfp_frame *f, int egress, u32 new_off,
		    u32 new_len)
{
	struct dpaa_wgfp_tx_buf tx;

	if (unlikely(new_off > f->buf_size ||
		     new_len > f->buf_size - new_off ||
		     new_len < ETH_HLEN)) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		wgfp_frame_release(f);
		dpaa_wgfp_orp_settle();
		return;
	}
	if (likely(dpaa_wgfp_tx_alloc(egress, 0, new_len, &tx))) {
		memcpy(tx.buf + DPAA_WGFP_TX_HEADROOM, f->buf + new_off, new_len);
		dpaa_wgfp_tx_submit(f, &tx, DPAA_WGFP_TX_HEADROOM, new_len);
		return;
	}
	{
		struct sk_buff *skb = dpaa_wgfp_alloc_xmit_skb(egress, 0, new_len);

		if (unlikely(!skb)) {
			wgfp_frame_release(f);
			dpaa_wgfp_orp_settle();
			return;
		}
		skb_put_data(skb, f->buf + new_off, new_len);
		dpaa_wgfp_xmit_skb(f, egress, 0, skb);
	}
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_xmit);

struct sk_buff *dpaa_wgfp_alloc_xmit_skb(int egress, int expected_ifindex,
					 u32 total_len)
{
	struct net_device *dev;
	struct sk_buff *skb;

	rcu_read_lock();
	if (unlikely(egress < 0 || egress >= smp_load_acquire(&wgfp_neg))) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		rcu_read_unlock();
		return NULL;
	}
	dev = rcu_dereference(wgfp_eg_dev[egress]);
	if (unlikely(!dev || !netif_running(dev) ||
		     (expected_ifindex > 0 && dev->ifindex != expected_ifindex))) {
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		rcu_read_unlock();
		return NULL;
	}
	skb = netdev_alloc_skb(dev, total_len);
	if (unlikely(!skb)) {
		atomic64_inc(&wgfp_xmit_nomem);
		atomic64_inc(&wgfp_xmit_fail);
	}
	rcu_read_unlock();
	return skb;
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_alloc_xmit_skb);

void dpaa_wgfp_xmit_skb(struct dpaa_wgfp_frame *f, int egress,
			int expected_ifindex, struct sk_buff *skb)
{
	struct net_device *dev;

	if (unlikely(!skb || skb->len < ETH_HLEN)) {
		if (skb)
			kfree_skb(skb);
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		if (f)
			wgfp_frame_release(f);
		dpaa_wgfp_orp_settle();
		return;
	}

	rcu_read_lock();
	if (unlikely(egress < 0 || egress >= smp_load_acquire(&wgfp_neg))) {
		kfree_skb(skb);
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		if (f)
			wgfp_frame_release(f);
		dpaa_wgfp_orp_settle();
		rcu_read_unlock();
		return;
	}
	dev = rcu_dereference(wgfp_eg_dev[egress]);
	if (unlikely(!dev || !netif_running(dev) ||
		     (expected_ifindex > 0 && dev->ifindex != expected_ifindex))) {
		kfree_skb(skb);
		atomic64_inc(&wgfp_xmit_invalid_egress);
		atomic64_inc(&wgfp_xmit_fail);
		if (f)
			wgfp_frame_release(f);
		dpaa_wgfp_orp_settle();
		rcu_read_unlock();
		return;
	}
	skb->dev = dev;
	wgfp_prepare_xmit_skb(skb);
	if (f)
		wgfp_frame_release(f);
	dpaa_wgfp_orp_settle();
	wgfp_submit_skb(skb);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(dpaa_wgfp_xmit_skb);

static int __init dpaa_wgfp_init(void)
{
	wgfp_global = kcalloc(WGFP_MAX_SINKS, sizeof(*wgfp_global), GFP_KERNEL);
	wgfp_ports = kcalloc(WGFP_MAX_PORTS, sizeof(*wgfp_ports), GFP_KERNEL);
	wgfp_conf_fqs = kcalloc(WGFP_MAX_SINKS, sizeof(*wgfp_conf_fqs), GFP_KERNEL);
	wgfp_tx_ern_fq = kzalloc(sizeof(*wgfp_tx_ern_fq), GFP_KERNEL);
	wgfp_egresses = kcalloc(WGFP_MAX_EGRESS, sizeof(*wgfp_egresses), GFP_KERNEL);
	wgfp_ndev = alloc_netdev_dummy(0);
	if (!wgfp_global || !wgfp_ports || !wgfp_conf_fqs ||
	    !wgfp_tx_ern_fq || !wgfp_egresses || !wgfp_ndev)
		return -ENOMEM;
	if (wgfp_threaded) {
		if (dev_set_threaded(wgfp_ndev, NETDEV_NAPI_THREADED_ENABLED))
			wgfp_threaded = false;
	}
	if (register_netdevice_notifier(&wgfp_netdev_notifier))
		return -ENOMEM;
	wgfp_dir = debugfs_create_dir("dpaa_wgfp", NULL);
	debugfs_create_file("ctl", 0200, wgfp_dir, NULL, &wgfp_ctl_fops);
	debugfs_create_file("stats", 0444, wgfp_dir, NULL, &wgfp_stats_fops);
	debugfs_create_file("stats_sw", 0444, wgfp_dir, NULL, &wgfp_stats_sw_fops);
	return 0;
}
/* Part of the built-in fsl_dpa object; dpaa_eth.c owns module_init(). */
late_initcall(dpaa_wgfp_init);
