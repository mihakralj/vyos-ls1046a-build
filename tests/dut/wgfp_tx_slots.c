// SPDX-License-Identifier: GPL-2.0
/*
 * Host unit test harness for WGFP-v2 dedicated TX slots, confirmation DQRR,
 * ERN/TX-error claim guards, batched BMan release, ORP submission, and
 * NETDEV_GOING_DOWN / NETDEV_UNREGISTER drain & quarantine in dpaa_wgfp.c.
 */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
typedef uint64_t dma_addr_t;
typedef int64_t ktime_t;

#define PAGE_SHIFT 12
#define PAGE_SIZE (1U << PAGE_SHIFT)
#define PAGE_MASK (~((dma_addr_t)PAGE_SIZE - 1))
#define ETH_HLEN 14
#define DPAA_WGFP_TX_HEADROOM 64
#define DPAA_WGFP_TX_BUF_SIZE (PAGE_SIZE - DPAA_WGFP_TX_HEADROOM)
#define WGFP_FD_MAX_OFF 511U
#define WGFP_MAX_SINKS 4
#define WGFP_MAX_EGRESS 8
#define WGFP_BPID_INV 0xff
#define WGFP_TX_SLOTS_PER_CPU 16	/* small pool for exhaustive exhaustion tests */
#define WGFP_TX_DMA_HT_BITS 6
#define WGFP_TX_META_MAGIC 0x57474650U
#define WGFP_DRAIN_TIMEOUT_MS 5
#define WGFP_TXB_MAX 64
#define WGFP_ORP_WAIT_NS 10
#define WGFP_XMIT_WAIT_NS 10
#define WGFP_TX_POLL_WAIT_NS 10
#define FM_FD_CMD_FCO 0x80000000U
#define FM_FD_ERR_UNSUPPORTED_FORMAT 0x04000000U
#define FM_FD_ERR_LENGTH 0x02000000U
#define FM_FD_ERR_DMA 0x01000000U
#define WGFP_FD_STAT_TX_ERRORS (FM_FD_ERR_UNSUPPORTED_FORMAT | FM_FD_ERR_LENGTH | FM_FD_ERR_DMA)
#define QM_MR_RC_MASK 0xf0
#define QM_MR_RC_CGR_TAILDROP 0x00
#define QM_MR_RC_ORPWINDOW_LATE 0x40
#define QM_MR_RC_ORPWINDOW_EARLY 0x50
#define DMA_TO_DEVICE 1
#define DMA_FROM_DEVICE 2
#define GFP_KERNEL 0
#define NETDEV_GOING_DOWN 0x0009
#define NETDEV_UNREGISTER 0x0006
#define NOTIFY_DONE 0
#define NOTIFY_OK 1

#define likely(x) (x)
#define unlikely(x) (x)
#define __rcu
#define ____cacheline_aligned_in_smp
#define EXPORT_SYMBOL_GPL(x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p, v) (*(p) = (v))
#define smp_mb__before_atomic() do { } while (0)
#define smp_mb__after_atomic() do { } while (0)
#define IS_ENABLED(x) 0
#define ASSERT_RTNL() do { assert(rtnl_held); } while (0)
#define cpu_relax() do { } while (0)
#define rcu_read_lock() do { } while (0)
#define rcu_read_unlock() do { } while (0)
#define rcu_dereference(p) (p)
#define rcu_access_pointer(p) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
#define local_bh_disable() do { bh_depth++; } while (0)
#define local_bh_enable() do { assert(bh_depth > 0); bh_depth--; } while (0)
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define cpu_to_be32(x) __builtin_bswap32(x)
#define be32_to_cpu(x) __builtin_bswap32(x)
#define pr_info(...) do { } while (0)
#define pr_err(...) do { } while (0)
#define this_cpu_ptr(p) (p)
#define this_cpu_read(x) (x)
#define prefetchw(p) ((void)(p))
#define time_before(a, b) ((long)((a) - (b)) < 0)
#define msecs_to_jiffies(ms) (ms)

typedef struct { int counter; } atomic_t;
typedef struct { int64_t counter; } atomic64_t;
#define ATOMIC_INIT(v) { (v) }
#define ATOMIC64_INIT(v) { (v) }
static inline int atomic_read(const atomic_t *a) { return a->counter; }
static inline void atomic_set(atomic_t *a, int v) { a->counter = v; }
static inline void atomic_inc(atomic_t *a) { a->counter++; }
static inline void atomic_dec(atomic_t *a) { a->counter--; }
static inline void atomic_add(int v, atomic_t *a) { a->counter += v; }
static inline int atomic_cmpxchg(atomic_t *a, int o, int n)
{
	int cur = a->counter;
	if (cur == o)
		a->counter = n;
	return cur;
}
#define atomic_cmpxchg_relaxed(a, o, n) atomic_cmpxchg((a), (o), (n))
#define WGFP_MAX_BPIDS 64
static struct device *wgfp_bpid_dev[WGFP_MAX_BPIDS];
static inline int64_t atomic64_read(const atomic64_t *a) { return a->counter; }
#define atomic64_read_acquire(a) atomic64_read(a)
static inline void atomic64_set(atomic64_t *a, int64_t v) { a->counter = v; }
static inline void atomic64_inc(atomic64_t *a) { a->counter++; }
#define dpaa_wgfp_stat_inc(p) atomic64_inc(p)

typedef struct { int locked; } spinlock_t;
static int bh_depth;
static int remote_lock_takes;
static inline void spin_lock_init(spinlock_t *l) { l->locked = 0; }
static inline void spin_lock_bh(spinlock_t *l) { assert(!l->locked); l->locked = 1; bh_depth++; remote_lock_takes++; }
static inline void spin_unlock_bh(spinlock_t *l) { assert(l->locked && bh_depth > 0); l->locked = 0; bh_depth--; }

static int rtnl_held, wgfp_lock_held;
static void rtnl_lock(void) { assert(!rtnl_held && !wgfp_lock_held); rtnl_held = 1; }
static void rtnl_unlock(void) { assert(rtnl_held && !wgfp_lock_held); rtnl_held = 0; }
static void mutex_lock(int *m) { assert(!*m); *m = 1; }
static void mutex_unlock(int *m) { assert(*m); *m = 0; }
static int wgfp_lock;

/* Minimal Linux hlist */
struct hlist_node { struct hlist_node *next, **pprev; };
struct hlist_head { struct hlist_node *first; };
#define DEFINE_HASHTABLE(name, bits) struct hlist_head name[1U << (bits)]
static inline void hlist_add_head_rcu(struct hlist_node *n, struct hlist_head *h)
{
	struct hlist_node *first = h->first;
	n->next = first;
	if (first)
		first->pprev = &n->next;
	h->first = n;
	n->pprev = &h->first;
}
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - __builtin_offsetof(type, member)))
#define hlist_for_each_entry_rcu(pos, head, member) \
	for (struct hlist_node *__n = (head)->first; \
	     __n && ((pos) = container_of(__n, __typeof__(*(pos)), member), 1); \
	     __n = __n->next)

static inline u32 hash_64(u64 val, unsigned int bits)
{
	return (u32)((val * 0x61C8864680B583EBULL) >> (64 - bits));
}

struct device { int id; };
struct dpaa_priv { struct device *tx_dma_dev; };
struct net_device {
	char name[16];
	int ifindex;
	bool running;
	struct dpaa_priv priv;
};
static inline void *netdev_priv(struct net_device *dev) { return &dev->priv; }
static inline bool netif_running(const struct net_device *dev) { return dev->running; }
struct notifier_block { int (*notifier_call)(struct notifier_block *, unsigned long, void *); };
static inline struct net_device *netdev_notifier_info_to_dev(void *ptr) { return ptr; }

struct qm_fd {
	u8 bpid;
	u64 addr;
	u32 off, len;
	u32 cmd;
	u32 status;
};
struct qm_dqrr_entry { struct qm_fd fd; };
union qm_mr_entry {
	struct {
		u8 rc;
		struct qm_fd fd;
	} ern;
};
struct qman_portal { int id; };
struct qman_fq { u32 fqid; };
enum qman_cb_dqrr_result { qman_cb_dqrr_consume = 0, qman_cb_dqrr_stop = 1 };

static inline void qm_fd_clear_fd(struct qm_fd *fd) { memset(fd, 0, sizeof(*fd)); }
static inline void qm_fd_addr_set64(struct qm_fd *fd, u64 a) { fd->addr = a; }
static inline u64 qm_fd_addr(const struct qm_fd *fd) { return fd->addr; }
static inline void qm_fd_set_contig(struct qm_fd *fd, u32 off, u32 len) { fd->off = off; fd->len = len; }
static inline u32 qman_fq_fqid(const struct qman_fq *fq) { return fq->fqid; }

struct dpaa_wgfp_frame {
	u8 *buf;
	dma_addr_t addr;
	struct device *dev;
	u32 off, len, buf_size;
	int ingress_ifindex;
	void *fd;
};
struct dpaa_wgfp_tx_buf {
	void *buf;
	u32 buf_size;
	int ifindex;
	void *priv;
};

/* Core structs matching dpaa_wgfp.c */
struct wgfp_egress;
enum wgfp_slot_state {
	WGFP_SLOT_FREE = 0,
	WGFP_SLOT_ALLOC = 1,
	WGFP_SLOT_BATCHED = 2,
	WGFP_SLOT_INFLIGHT = 3,
};
struct wgfp_tx_slot {
	dma_addr_t dma;
	struct wgfp_egress *eg;
	u8 *buf;
	atomic_t state;
	u16 owner_cpu;
	u16 local_idx;
	struct hlist_node dma_node;
} ____cacheline_aligned_in_smp;
struct wgfp_tx_meta {
	u32 magic;
	struct wgfp_tx_slot *slot;
};
struct wgfp_egress_pc {
	u64 alloc_cnt;
	u64 local_done;
	u16 free_top;
	u16 free_stack[WGFP_TX_SLOTS_PER_CPU];
	atomic64_t remote_done;
	spinlock_t remote_lock;
	u16 remote_top;
	u16 remote_stack[WGFP_TX_SLOTS_PER_CPU];
};
struct wgfp_egress {
	struct net_device *dev;
	struct device *tx_dma_dev;
	int ifindex;
	u32 tx_fqid;
	unsigned int ncpus;
	struct wgfp_tx_slot *slots;
	struct wgfp_egress_pc *pc;
	bool active;
	bool retired;
	bool quarantined;
};
struct wgfp_sink { int dummy; };
struct wgfp_orp_ctx {
	struct wgfp_sink *sink;
	u32 orp_fqid;
	u16 seq;
	bool owed;
};
struct wgfp_bman_rel {
	bool active;
	u8 bpid;
	int n;
	dma_addr_t addr[8];
};
struct wgfp_txb {
	bool active, threaded;
	unsigned int n, lim;
	ktime_t end;
	struct wgfp_tx_slot *slot[WGFP_TXB_MAX];
	struct qm_fd fd[WGFP_TXB_MAX];
};

static struct wgfp_orp_ctx wgfp_orp_ctx;
static struct wgfp_bman_rel wgfp_bman_rel;
static struct wgfp_txb wgfp_txb;
static struct qman_fq wgfp_conf_fqs[WGFP_MAX_SINKS];
static struct qman_fq wgfp_tx_ern_fq_inst;
static struct qman_fq *wgfp_tx_ern_fq = &wgfp_tx_ern_fq_inst;
static struct qman_fq wgfp_hole_fq;
static struct wgfp_egress wgfp_egresses[WGFP_MAX_EGRESS];
static struct net_device *wgfp_eg_dev[WGFP_MAX_EGRESS];
static int wgfp_neg;
static DEFINE_HASHTABLE(wgfp_tx_dma_ht, WGFP_TX_DMA_HT_BITS);
static atomic_t wgfp_tx_slots_installed = ATOMIC_INIT(0);

static atomic64_t wgfp_xmit_ok, wgfp_xmit_conf_ok, wgfp_xmit_fail, wgfp_xmit_ern;
static atomic64_t wgfp_xmit_ern_cgr_td, wgfp_xmit_ern_orp_late, wgfp_xmit_ern_orp_early, wgfp_xmit_ern_other;
static atomic64_t wgfp_xmit_frame_wait_fail, wgfp_xmit_poll_wait_fail;
static atomic64_t wgfp_xmit_invalid_egress, wgfp_xmit_orp_wait_fail;
static atomic64_t wgfp_xmit_no_slot, wgfp_xmit_tx_err, wgfp_xmit_dup_comp;
static atomic64_t wgfp_orp_holes, wgfp_orp_wait_fail;

/* Mock page allocator (4 KiB aligned so PAGE_MASK checks work; static struct page
 * array avoids LSan false positives on boot-lifetime pages). */
struct page { u8 *addr; bool in_use; };
static struct page mock_pages[256];
static int cur_cpu;
static int alloc_pages_live, dma_maps_live;
static int fail_fq_alloc;
static int enq_fail_mode, orp_enq_fail_mode;
static int enq_calls, orp_enq_calls, orp_hole_calls;
static struct qm_fd last_enq_fd;
static u32 last_enq_fqid;
static int bman_batch_calls, bman_batch_total, bman_direct_calls;
static u8 bman_last_bpid;
static int bman_last_n;
static int rcu_syncs, napi_syncs;
static unsigned long jiffies = 100;
static ktime_t now_ns = 1000;
static void (*msleep_hook)(void);

static int smp_processor_id(void) { return cur_cpu; }
static unsigned int num_possible_cpus(void) { return 2; }
static ktime_t ktime_get(void) { return now_ns += 5; }
static ktime_t ktime_add_ns(ktime_t t, u64 ns) { return t + (ktime_t)ns; }
static bool ktime_before(ktime_t a, ktime_t b) { return a < b; }
static void *kcalloc(size_t n, size_t sz, int gfp) { (void)gfp; return calloc(n, sz); }
static void kfree(void *p) { free(p); }
static struct page *alloc_page(int gfp)
{
	void *p = NULL;
	(void)gfp;
	for (size_t i = 0; i < sizeof(mock_pages) / sizeof(mock_pages[0]); i++) {
		if (!mock_pages[i].in_use) {
			assert(posix_memalign(&p, PAGE_SIZE, PAGE_SIZE) == 0);
			memset(p, 0, PAGE_SIZE);
			mock_pages[i].addr = p;
			mock_pages[i].in_use = true;
			alloc_pages_live++;
			return &mock_pages[i];
		}
	}
	return NULL;
}
static void __free_page(struct page *pg)
{
	assert(pg && pg->in_use && pg->addr);
	free(pg->addr);
	pg->addr = NULL;
	pg->in_use = false;
	alloc_pages_live--;
}
static u8 *page_address(struct page *pg) { return pg->addr; }
static struct page *virt_to_page(void *p)
{
	for (size_t i = 0; i < sizeof(mock_pages) / sizeof(mock_pages[0]); i++) {
		if (mock_pages[i].in_use && mock_pages[i].addr == p)
			return &mock_pages[i];
	}
	assert(0 && "virt_to_page: unknown page");
	return NULL;
}
static dma_addr_t dma_map_page(struct device *d, struct page *pg, size_t off, size_t len, int dir)
{
	(void)d; (void)off; (void)len; (void)dir;
	dma_maps_live++;
	return (dma_addr_t)(uintptr_t)pg->addr;
}
static bool dma_mapping_error(struct device *d, dma_addr_t a) { (void)d; return a == 0; }
static void dma_unmap_page(struct device *d, dma_addr_t a, size_t len, int dir)
{
	(void)d; (void)a; (void)len; (void)dir;
	dma_maps_live--;
}
static void dma_sync_single_for_device(struct device *d, dma_addr_t a, u32 len, int dir)
{
	(void)d; (void)a; (void)len; (void)dir;
}
static void *phys_to_virt(dma_addr_t a) { return (void *)(uintptr_t)a; }
static void *dpaa_get_rx_fman_port(struct net_device *d) { return d; }
static int wgfp_tx_infra_ensure_locked(void)
{
	for (int c = 0; c < WGFP_MAX_SINKS; c++)
		wgfp_conf_fqs[c].fqid = 0x500 + c;
	return 0;
}
static int dpaa_alloc_wgfp_tx_fq(struct net_device *dev, u32 *fqid, struct device **dma_dev)
{
	assert(rtnl_held && wgfp_lock);
	if (fail_fq_alloc)
		return fail_fq_alloc;
	*fqid = 0x700 + dev->ifindex;
	*dma_dev = dev->priv.tx_dma_dev;
	return 0;
}
static void dpaa_wgfp_bman_release_batch(u8 bpid, const dma_addr_t *addrs, int n)
{
	(void)addrs;
	bman_batch_calls++;
	bman_batch_total += n;
	bman_last_bpid = bpid;
	bman_last_n = n;
}
static void dpaa_wgfp_fd_release(const struct qm_fd *fd)
{
	(void)fd;
	bman_direct_calls++;
}
static enum qman_cb_dqrr_result wgfp_sched_portal(struct qman_portal *p, struct wgfp_sink *s)
{
	(void)p; (void)s;
	return qman_cb_dqrr_stop;
}
static int qman_enqueue_fqid(struct qman_fq *tag, u32 fqid, const struct qm_fd *fd)
{
	(void)tag;
	enq_calls++;
	if (enq_fail_mode)
		return -EBUSY;
	last_enq_fqid = fqid;
	last_enq_fd = *fd;
	return 0;
}
static int qman_enqueue_orp_fqid(struct qman_fq *tag, u32 fqid, u32 orp_fqid,
				 u16 seq, bool hole, const struct qm_fd *fd)
{
	(void)tag; (void)orp_fqid; (void)seq;
	if (hole) {
		orp_hole_calls++;
		return 0;
	}
	orp_enq_calls++;
	if (orp_enq_fail_mode)
		return -EBUSY;
	last_enq_fqid = fqid;
	last_enq_fd = *fd;
	return 0;
}
static void synchronize_rcu(void) { rcu_syncs++; }
static void wgfp_sync_all_napis_locked(void) { napi_syncs++; }
static void msleep(unsigned int ms)
{
	jiffies += ms;
	if (msleep_hook)
		msleep_hook();
}

#include "wgfp_tx_slots_body.h"

static struct qm_fd drain_fd;
static void complete_during_drain(void)
{
	struct qm_dqrr_entry dq = { .fd = drain_fd };
	if (drain_fd.addr) {
		drain_fd.addr = 0;
		wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false);
	}
}

int main(void)
{
	struct device dma_dev = { .id = 1 };
	struct net_device eth3 = { .name = "eth3", .ifindex = 3, .running = true,
				   .priv = { .tx_dma_dev = &dma_dev } };
	struct net_device eth4 = { .name = "eth4", .ifindex = 4, .running = true,
				   .priv = { .tx_dma_dev = &dma_dev } };
	struct dpaa_wgfp_tx_buf tx, tx2;
	struct qm_dqrr_entry dq;
	union qm_mr_entry mr;
	struct qm_fd fd;
	int eg3, eg4;

	/* 1. FQ alloc failure unwinds unpublished pages and leaves HT empty */
	fail_fq_alloc = -EBUSY;
	assert(dpaa_wgfp_egress(&eth3) == -EBUSY);
	assert(alloc_pages_live == 0 && dma_maps_live == 0);
	assert(!dpaa_wgfp_has_tx_slots() && wgfp_neg == 0);
	fail_fq_alloc = 0;

	/* 2. Normal egress creation */
	eg3 = dpaa_wgfp_egress(&eth3);
	assert(eg3 == 0 && dpaa_wgfp_has_tx_slots() && wgfp_neg == 1);
	assert(dpaa_wgfp_egress_find(&eth3) == 0);
	assert(alloc_pages_live == 2 * WGFP_TX_SLOTS_PER_CPU);

	/* 3. Bounds check on tx_alloc and tx_submit */
	assert(!dpaa_wgfp_tx_alloc(eg3, 3, DPAA_WGFP_TX_BUF_SIZE + 1, &tx));
	assert(!dpaa_wgfp_tx_alloc(eg3, 99, 128, &tx));
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 1);
	/* Invalid off < 64 or off > 511 (9-bit QMan contig offset) cancels slot cleanly */
	dpaa_wgfp_tx_submit(NULL, &tx, 32, 64);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0 && tx.priv == NULL);
	assert(wgfp_xmit_fail.counter == 1);
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, 512, 64);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0 && tx.priv == NULL);
	assert(wgfp_xmit_fail.counter == 2);

	/* Missing confirm FQ on current CPU refuses tx_alloc cleanly */
	u32 saved_cfq = wgfp_conf_fqs[0].fqid;
	wgfp_conf_fqs[0].fqid = 0;
	assert(!dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	wgfp_conf_fqs[0].fqid = saved_cfq;

	/* Zero DMA address confirm does NOT dereference phys_to_virt(0) */
	memset(&dq, 0, sizeof(dq));
	assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false) == qman_cb_dqrr_consume);
	assert(wgfp_xmit_dup_comp.counter == 1);
	wgfp_xmit_dup_comp.counter = 0;

	/* 4. Normal alloc -> submit -> confirm fast backpointer */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 200, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 200);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 1);
	assert(last_enq_fqid == 0x703);
	assert(last_enq_fd.bpid == WGFP_BPID_INV);
	assert(last_enq_fd.cmd == cpu_to_be32(FM_FD_CMD_FCO | 0x500));
	dq.fd = last_enq_fd;
	assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false) == qman_cb_dqrr_consume);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(wgfp_xmit_conf_ok.counter == 1 && wgfp_xmit_dup_comp.counter == 0);

	/* 5. Duplicate completion (confirm + racing ERN) -> dup_comp, inflight stays 0 */
	assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false) == qman_cb_dqrr_consume);
	mr.ern.rc = QM_MR_RC_CGR_TAILDROP;
	mr.ern.fd = dq.fd;
	wgfp_tx_ern_cb(NULL, wgfp_tx_ern_fq, &mr);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(wgfp_xmit_dup_comp.counter == 2);
	wgfp_xmit_ern_cgr_td.counter = 0;

	/* 5b. Sync enqueue failure unwinds INFLIGHT slot cleanly */
	enq_fail_mode = 1;
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	enq_fail_mode = 0;

	/* 5c. Cross-CPU completion (CPU 1 completes CPU 0's slot -> remote_stack) */
	int prev_rem = remote_lock_takes;
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	cur_cpu = 1;
	dq.fd = last_enq_fd;
	assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[1], &dq, false) == qman_cb_dqrr_consume);
	cur_cpu = 0;
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(remote_lock_takes == prev_rem + 1);

	/* 6. Confirm with FMan TX error status -> xmit_tx_err, HT fallback when magic cleared */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	((struct wgfp_tx_meta *)tx.buf)->magic = 0;	/* force HT fallback */
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	dq.fd = last_enq_fd;
	dq.fd.status = cpu_to_be32(FM_FD_ERR_DMA);
	assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false) == qman_cb_dqrr_consume);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(wgfp_xmit_tx_err.counter == 1);
	((struct wgfp_tx_meta *)phys_to_virt(dq.fd.addr))->magic = WGFP_TX_META_MAGIC;

	/* 7. ERN callback (BH-enabled -> remote_lock + remote_stack) & reason counters */
	u8 rcs[4] = { QM_MR_RC_CGR_TAILDROP, QM_MR_RC_ORPWINDOW_LATE,
		      QM_MR_RC_ORPWINDOW_EARLY, 0x90 };
	int prev_remote = remote_lock_takes;
	for (int i = 0; i < 4; i++) {
		assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
		dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
		mr.ern.rc = rcs[i];
		mr.ern.fd = last_enq_fd;
		wgfp_tx_ern_cb(NULL, wgfp_tx_ern_fq, &mr);
	}
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(remote_lock_takes == prev_remote + 4);
	assert(wgfp_xmit_ern_cgr_td.counter == 1);
	assert(wgfp_xmit_ern_orp_late.counter == 1);
	assert(wgfp_xmit_ern_orp_early.counter == 1);
	assert(wgfp_xmit_ern_other.counter == 1);
	/* Exhaust local free_stack to verify remote_stack refill works cleanly */
	struct dpaa_wgfp_tx_buf all[WGFP_TX_SLOTS_PER_CPU];
	for (int i = 0; i < WGFP_TX_SLOTS_PER_CPU; i++)
		assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &all[i]));
	assert(!dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	assert(wgfp_xmit_no_slot.counter == 1);
	for (int i = 0; i < WGFP_TX_SLOTS_PER_CPU; i++)
		dpaa_wgfp_tx_cancel(&all[i]);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);

	/* 8. dpaa_wgfp_tx_error_claim() guards */
	memset(&fd, 0, sizeof(fd));
	fd.bpid = 7;
	assert(!dpaa_wgfp_tx_error_claim(&fd));		/* non-0xff bpid ignored */
	fd.bpid = WGFP_BPID_INV;
	fd.addr = 0x12345;
	assert(!dpaa_wgfp_tx_error_claim(&fd));		/* unaligned ignored */
	fd.addr = 0x100000;
	assert(!dpaa_wgfp_tx_error_claim(&fd));		/* foreign page ignored */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	assert(dpaa_wgfp_tx_error_claim(&last_enq_fd));	/* WGFP FD claimed */
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);

	/* 9. wgfp_txb batch flush and cancellation when eg->active turns false */
	wgfp_txb.active = true;
	wgfp_txb.lim = 4;
	wgfp_txb.n = 0;
	int prev_enq = enq_calls;
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx2));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	dpaa_wgfp_tx_submit(NULL, &tx2, DPAA_WGFP_TX_HEADROOM, 128);
	assert(wgfp_txb.n == 2 && enq_calls == prev_enq);
	wgfp_egresses[eg3].active = false;
	wgfp_txb_flush(&wgfp_txb);
	wgfp_txb.active = false;
	assert(enq_calls == prev_enq && wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	wgfp_egresses[eg3].active = true;

	/* 10. ORP submission + ORP enqueue failure unwind + hole settle */
	wgfp_orp_ctx.owed = true;
	wgfp_orp_ctx.orp_fqid = 0x900;
	wgfp_orp_ctx.seq = 42;
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	assert(!wgfp_orp_ctx.owed && orp_enq_calls == 1);
	dq.fd = last_enq_fd;
	wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false);

	wgfp_orp_ctx.owed = true;
	orp_enq_fail_mode = 1;
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	assert(wgfp_orp_ctx.owed && wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	dpaa_wgfp_orp_settle();
	assert(!wgfp_orp_ctx.owed && orp_hole_calls == 1);
	orp_enq_fail_mode = 0;

	/* 11. NETDEV_GOING_DOWN drain -> down/up re-activation without realloc */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	drain_fd = last_enq_fd;
	msleep_hook = complete_during_drain;
	assert(wgfp_netdev_event(NULL, NETDEV_GOING_DOWN, &eth3) == NOTIFY_OK);
	msleep_hook = NULL;
	assert(!wgfp_egresses[eg3].active && !wgfp_egresses[eg3].quarantined);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(dpaa_wgfp_egress_find(&eth3) == -ENOENT);
	int pages_before = alloc_pages_live;
	assert(dpaa_wgfp_egress(&eth3) == eg3);
	assert(wgfp_egresses[eg3].active && alloc_pages_live == pages_before);

	/* 11b. CEETM attach drains & deactivates WGFP egress cleanly under RTNL */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	drain_fd = last_enq_fd;
	msleep_hook = complete_during_drain;
	rtnl_lock();
	dpaa_wgfp_egress_deactivate(&eth3);
	rtnl_unlock();
	msleep_hook = NULL;
	assert(!wgfp_egresses[eg3].active && !wgfp_egresses[eg3].quarantined);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);
	assert(dpaa_wgfp_egress(&eth3) == eg3);

	/* 12. Drain timeout -> quarantine; late confirm still safely drains inflight */
	assert(dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 128);
	struct qm_fd late_fd = last_enq_fd;
	assert(wgfp_netdev_event(NULL, NETDEV_GOING_DOWN, &eth3) == NOTIFY_OK);
	assert(wgfp_egresses[eg3].quarantined);
	assert(!dpaa_wgfp_tx_alloc(eg3, 3, 128, &tx));
	assert(dpaa_wgfp_egress(&eth3) == -EBUSY);
	dq.fd = late_fd;
	wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false);
	assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg3]) == 0);

	/* 13. NETDEV_UNREGISTER permanently retires slot and clears BPID cache */
	eg4 = dpaa_wgfp_egress(&eth4);
	assert(eg4 == 1);
	wgfp_bpid_dev[7] = &dma_dev;
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &eth4) == NOTIFY_OK);
	assert(wgfp_egresses[eg4].retired && !wgfp_egresses[eg4].active);
	assert(wgfp_bpid_dev[7] == NULL);
	assert(dpaa_wgfp_egress(&eth4) == -EBUSY);

	/* 14. Batched BMan RX release (8 per RCR + BPID change flush) */
	struct qm_fd rxfd7 = { .bpid = 7 }, rxfd8 = { .bpid = 8 };
	struct dpaa_wgfp_frame rxf = { .buf_size = 3584, .dev = &dma_dev };
	wgfp_bman_rel.active = true;
	for (int i = 0; i < 9; i++) {
		rxf.addr = 0x200000 + (u64)i * 4096;
		rxf.fd = &rxfd7;
		wgfp_frame_release_fast(&rxf);
	}
	assert(bman_batch_calls == 1 && bman_last_n == 8 && bman_last_bpid == 7);
	rxf.addr = 0x300000;
	rxf.fd = &rxfd8;
	wgfp_frame_release_fast(&rxf);
	assert(bman_batch_calls == 2 && bman_last_n == 1 && bman_last_bpid == 7);
	wgfp_bman_rel_flush(&wgfp_bman_rel);
	assert(bman_batch_calls == 3 && bman_last_n == 1 && bman_last_bpid == 8);
	assert(bman_batch_total == 10 && bman_direct_calls == 0);

	/* 15. In-NAPI BH-elision path (wgfp_bman_rel.active = true) + lazy b->end */
	{
		struct net_device eth5 = { .name = "eth5", .ifindex = 5, .running = true,
					   .priv = { .tx_dma_dev = &dma_dev } };
		int eg5 = dpaa_wgfp_egress(&eth5);
		assert(eg5 == 2);
		wgfp_bman_rel.active = true;
		wgfp_txb.active = true;
		wgfp_txb.threaded = true;
		wgfp_txb.end = 0;
		wgfp_txb.lim = 4;
		wgfp_txb.n = 0;
		assert(bh_depth == 0);
		assert(dpaa_wgfp_tx_alloc(eg5, 5, 200, &tx));
		assert(bh_depth == 0);
		dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 200);
		assert(bh_depth == 0 && wgfp_txb.n == 1 && wgfp_txb.end == 0);
		wgfp_txb_flush(&wgfp_txb);
		/* No -EBUSY -> lazy b->end stayed 0 (zero ktime_get calls) */
		assert(wgfp_txb.end == 0 && wgfp_eg_inflight_sum(&wgfp_egresses[eg5]) == 1);
		dq.fd = last_enq_fd;
		assert(wgfp_conf_dqrr(NULL, &wgfp_conf_fqs[0], &dq, false) == qman_cb_dqrr_consume);
		assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg5]) == 0);
		assert(wgfp_egresses[eg5].pc[0].alloc_cnt == 1);
		assert(wgfp_egresses[eg5].pc[0].local_done == 1);
		assert(atomic64_read(&wgfp_egresses[eg5].pc[0].remote_done) == 0);
		/* -EBUSY populates lazy b->end once and unwinds cleanly */
		enq_fail_mode = 1;
		assert(dpaa_wgfp_tx_alloc(eg5, 5, 200, &tx));
		dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 200);
		wgfp_txb_flush(&wgfp_txb);
		enq_fail_mode = 0;
		assert(wgfp_txb.end != 0 && wgfp_eg_inflight_sum(&wgfp_egresses[eg5]) == 0);
		wgfp_txb.active = false;
		wgfp_bman_rel.active = false;

		/* 16. In-NAPI threaded=1 with tx_batch <= 1 (b->active == false)
		 * must still use the shared poll deadline (&b->end) rather than
		 * falling back to the short per-frame wait path. */
		u64 poll_fail_before = (u64)atomic64_read(&wgfp_xmit_poll_wait_fail);
		u64 frame_fail_before = (u64)atomic64_read(&wgfp_xmit_frame_wait_fail);
		wgfp_bman_rel.active = true;
		wgfp_txb.active = false;
		wgfp_txb.threaded = true;
		wgfp_txb.end = 0;
		enq_fail_mode = 1;
		assert(dpaa_wgfp_tx_alloc(eg5, 5, 200, &tx));
		dpaa_wgfp_tx_submit(NULL, &tx, DPAA_WGFP_TX_HEADROOM, 200);
		enq_fail_mode = 0;
		assert(wgfp_txb.end != 0);
		assert((u64)atomic64_read(&wgfp_xmit_poll_wait_fail) == poll_fail_before + 1);
		assert((u64)atomic64_read(&wgfp_xmit_frame_wait_fail) == frame_fail_before);
		assert(wgfp_eg_inflight_sum(&wgfp_egresses[eg5]) == 0);
		wgfp_bman_rel.active = false;
	}

	assert(bh_depth == 0);
	puts("wgfp_tx_slots: ok");
	return 0;
}
