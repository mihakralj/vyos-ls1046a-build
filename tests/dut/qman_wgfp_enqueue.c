// SPDX-License-Identifier: GPL-2.0
/* Host harness for the actual patched enqueue and WGFP egress functions. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int atomic64_t;

struct qm_fd {
	u8 bpid;
	u64 addr;
	u32 off, len;
};

struct qman_portal;
struct qman_fq;
union qm_mr_entry {
	struct {
		struct qm_fd fd;
	} ern;
};

struct qman_fq {
	struct {
		void (*ern)(struct qman_portal *, struct qman_fq *,
			    const union qm_mr_entry *);
	} cb;
	u32 fqid, idx;
};

struct qm_eqcr_entry {
	u32 fqid, tag;
	struct qm_fd fd;
};

struct qm_portal {
	struct qm_eqcr_entry entry;
};

struct qman_portal {
	bool use_eqcr_ci_stashing;
	struct qm_portal p;
};

struct device { int unused; };
struct net_device {
	int ifindex;
	const char *name;
	bool dpaa;
};

struct dpaa_wgfp_frame {
	const struct qm_fd *fd;
	struct device *dev;
	u64 addr;
};

#define WGFP_MAX_EGRESS 8
#define QMAN_FQ_FLAG_NO_MODIFY 2
#define QMAN_FQ_FLAG_DYNAMIC_FQID 0x20
#define QM_EQCR_VERB_CMD_ENQUEUE 1
#define DMA_TO_DEVICE 1
#define EXPORT_SYMBOL(x)
#define unlikely(x) (x)
#define atomic64_inc(p) (++*(p))
#define dpaa_wgfp_stat_inc(p) atomic64_inc(p)
#define cpu_to_be32(x) htonl(x)
#define local_irq_save(x) do { (x) = 123; irq_depth++; } while (0)
#define local_irq_restore(x) do { assert((x) == 123); irq_depth--; } while (0)
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p, v) (*(p) = (v))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)

static struct qman_portal portal;
static struct qman_fq wgfp_ern_fq;
static bool wgfp_ern_fq_ready;
static u32 wgfp_eg_fqid[WGFP_MAX_EGRESS];
static int wgfp_eg_ifindex[WGFP_MAX_EGRESS], wgfp_neg, wgfp_lock;
static atomic64_t wgfp_xmit_ok, wgfp_xmit_fail, wgfp_xmit_ern;
static atomic64_t wgfp_rx_ern __attribute__((unused));
static int portal_refs, irq_depth, full_attempts, starts, commits;
static int ci_updates, relaxes, dma_syncs, releases, allocations, registrations;
static int create_error;
static struct qm_fd released_fd;

static struct qman_portal *get_affine_portal(void)
{
	portal_refs++;
	return &portal;
}

static void put_affine_portal(void)
{
	portal_refs--;
}

static struct qm_eqcr_entry *start_entry(struct qm_portal *p)
{
	starts++;
	if (full_attempts) {
		full_attempts--;
		return NULL;
	}
	return &p->entry;
}

static struct qm_eqcr_entry *qm_eqcr_start_stash(struct qm_portal *p)
{
	return start_entry(p);
}

static struct qm_eqcr_entry *qm_eqcr_start_no_stash(struct qm_portal *p)
{
	return start_entry(p);
}

static u8 qm_eqcr_get_avail(struct qm_portal *p)
{
	return full_attempts ? 0 : 7;
}

static void update_eqcr_ci(struct qman_portal *p, u8 avail)
{
	ci_updates++;
}

static void qm_fqid_set(struct qm_eqcr_entry *entry, u32 fqid)
{
	entry->fqid = fqid;
}

static u32 fq_to_tag(struct qman_fq *fq)
{
	return fq->idx;
}

static void qm_eqcr_pvb_commit(struct qm_portal *p, int verb)
{
	assert(verb == QM_EQCR_VERB_CMD_ENQUEUE);
	commits++;
}

static void *dpaa_get_rx_fman_port(struct net_device *dev)
{
	return dev->dpaa ? dev : NULL;
}

static int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)
{
	*fqid = 100 + ++allocations;
	return 0;
}

static int qman_create_fq(u32 fqid, u32 flags, struct qman_fq *fq)
{
	assert(fqid == 0);
	assert(flags == (QMAN_FQ_FLAG_NO_MODIFY | QMAN_FQ_FLAG_DYNAMIC_FQID));
	assert(fq->cb.ern);
	registrations++;
	if (create_error)
		return create_error;
	fq->fqid = 500;
	fq->idx = 1001;
	return 0;
}

static void dpaa_wgfp_fd_release(const struct qm_fd *fd)
{
	released_fd = *fd;
	releases++;
}

static void qm_fd_clear_fd(struct qm_fd *fd)
{
	memset(fd, 0, sizeof(*fd));
}

static void qm_fd_addr_set64(struct qm_fd *fd, u64 addr)
{
	fd->addr = addr;
}

static void qm_fd_set_contig(struct qm_fd *fd, u32 off, u32 len)
{
	fd->off = off;
	fd->len = len;
}

static void dma_sync_single_for_device(struct device *dev, u64 addr,
				       u32 len, int direction)
{
	assert(direction == DMA_TO_DEVICE);
	dma_syncs++;
}

static void cpu_relax(void)
{
	relaxes++;
}

#include "qman_wgfp_body.h"

static void reset(void)
{
	memset(&portal, 0, sizeof(portal));
	memset(&wgfp_ern_fq, 0, sizeof(wgfp_ern_fq));
	memset(wgfp_eg_fqid, 0, sizeof(wgfp_eg_fqid));
	wgfp_ern_fq_ready = false;
	memset(wgfp_eg_ifindex, 0, sizeof(wgfp_eg_ifindex));
	memset(&released_fd, 0, sizeof(released_fd));
	wgfp_neg = wgfp_xmit_ok = wgfp_xmit_fail = wgfp_xmit_ern = 0;
	portal_refs = irq_depth = full_attempts = starts = commits = 0;
	ci_updates = relaxes = dma_syncs = releases = allocations = registrations = 0;
	create_error = 0;
}

static void test_ring(bool stash)
{
	struct qman_fq fq = { .fqid = 37, .idx = 75 };
	struct qm_fd fd = { .bpid = 3, .addr = 0x1234, .off = 64, .len = 99 };

	reset();
	portal.use_eqcr_ci_stashing = stash;
	full_attempts = 1;
	assert(qman_enqueue_fqid(&fq, fq.fqid, &fd) == -EBUSY);
	assert(starts == 1 && commits == 0);
	assert(portal_refs == 0 && irq_depth == 0);
	assert(ci_updates == !stash);
	assert(qman_enqueue_fqid(&fq, fq.fqid, &fd) == 0);
	assert(starts == 2 && commits == 1);
	assert(portal.p.entry.fqid == fq.fqid);
	assert(ntohl(portal.p.entry.tag) == fq.idx);
	assert(!memcmp(&portal.p.entry.fd, &fd, sizeof(fd)));
	assert(portal_refs == 0 && irq_depth == 0);
}

static void test_egress_and_xmit(bool stash)
{
	struct net_device netdev = { .ifindex = 3, .name = "eth3", .dpaa = true };
	struct net_device netdev2 = { .ifindex = 4, .name = "eth4", .dpaa = true };
	struct device dev = { 0 };
	struct qm_fd fd = { .bpid = 4, .addr = 0x4000 };
	struct dpaa_wgfp_frame frame = { .fd = &fd, .dev = &dev, .addr = fd.addr };
	union qm_mr_entry ern;
	struct qman_fq *fq;

	reset();
	portal.use_eqcr_ci_stashing = stash;
	assert(dpaa_wgfp_egress(&netdev) == 0);
	assert(dpaa_wgfp_egress(&netdev) == 0 && allocations == 1);
	assert(dpaa_wgfp_egress(&netdev2) == 1 && allocations == 2);
	assert(registrations == 1);
	fq = &wgfp_ern_fq;
	assert(wgfp_eg_fqid[0] == 101);
	assert(fq->fqid == 500 && fq->idx == 1001 && fq->cb.ern);

	full_attempts = 2;
	dpaa_wgfp_xmit(&frame, 0, 64, 1200);
	assert(starts == 3 && commits == 1 && relaxes == 2);
	assert(wgfp_xmit_ok == 1 && wgfp_xmit_fail == 0 && releases == 0);
	assert(dma_syncs == 1 && ntohl(portal.p.entry.tag) == fq->idx);
	assert(portal.p.entry.fqid == wgfp_eg_fqid[0]);
	assert(portal.p.entry.fd.bpid == fd.bpid);
	assert(portal.p.entry.fd.addr == fd.addr);
	assert(portal.p.entry.fd.off == 64 && portal.p.entry.fd.len == 1200);

	ern.ern.fd = portal.p.entry.fd;
	fq->cb.ern(&portal, fq, &ern);
	assert(wgfp_xmit_ok == 1 && wgfp_xmit_fail == 1 && wgfp_xmit_ern == 1);
	assert(releases == 1 && released_fd.bpid == fd.bpid);
	assert(released_fd.addr == fd.addr);

	dpaa_wgfp_xmit(&frame, 1, 128, 1000);
	assert(ntohl(portal.p.entry.tag) == fq->idx);
	assert(portal.p.entry.fqid == wgfp_eg_fqid[1] && wgfp_eg_fqid[1] == 102);
	assert(wgfp_xmit_ok == 2 && wgfp_xmit_fail == 1 && releases == 1);

	reset();
	portal.use_eqcr_ci_stashing = stash;
	assert(dpaa_wgfp_egress(&netdev) == 0);
	full_attempts = 8;
	dpaa_wgfp_xmit(&frame, 0, 64, 1200);
	assert(starts == 8 && commits == 0 && relaxes == 8);
	assert(wgfp_xmit_ok == 0 && wgfp_xmit_fail == 1 && wgfp_xmit_ern == 0);
	assert(releases == 1 && released_fd.addr == fd.addr);
	assert(portal_refs == 0 && irq_depth == 0);

	reset();
	dpaa_wgfp_xmit(&frame, 0, 64, 1200);
	assert(starts == 0 && dma_syncs == 0 && releases == 1);
	assert(wgfp_xmit_fail == 1 && wgfp_xmit_ok == 0);

	reset();
	create_error = -EINVAL;
	assert(dpaa_wgfp_egress(&netdev) == -EINVAL);
	assert(wgfp_neg == 0 && allocations == 0);
	create_error = 0;
	assert(dpaa_wgfp_egress(&netdev) == 0);
	assert(wgfp_neg == 1 && allocations == 1 && registrations == 2);
}

int main(void)
{
	test_ring(false);
	test_ring(true);
	test_egress_and_xmit(false);
	test_egress_and_xmit(true);
	puts("QMan backpressure and WGFP egress ownership: OK");
	return 0;
}
