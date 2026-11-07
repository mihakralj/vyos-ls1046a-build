// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted CAAM QI queue and DMA paths. */
#include <assert.h>
#include <endian.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t dma_addr_t;

#define CAAM_QI_ENQUEUE_RETRIES 3
#define DMA_BIDIRECTIONAL 0
#define likely(x) (x)
#define unlikely(x) (x)
#define READ_ONCE(x) (x)
#define be32_to_cpu(x) be32toh(x)
#define qm_mcr_frm_cnt_mask 0x00ffffffU
#define qm_mcr_np_get(np, field) ((np)->field & qm_mcr_##field##_mask)
#define dev_err(dev, ...) ((void)(dev))
#define dev_warn_once(dev, ...) ((void)(dev))
#define WARN_ON(condition) ((void)(condition))
#define IS_ERR_OR_NULL(ptr) (!(ptr))
#define QMAN_FQ_STATE_NE 0x01
#define QMAN_FQ_STATE_ORL 0x02
#define QMAN_FQ_STATE_BLOCKOOS 0x04

struct device { struct device *parent; };
enum qman_fq_state { qman_fq_state_sched, qman_fq_state_retired };
struct qman_fq {
	u32 fqid;
	u32 idx;
	u32 flags;
	enum qman_fq_state state;
};
struct qm_fd { dma_addr_t addr; };
struct qm_sg_entry { u32 len; };
struct qm_mcr_queryfq_np { u32 frm_cnt; };
struct caam_drv_ctx {
	struct qman_fq *req_fq;
	struct device *qidev;
	dma_addr_t context_a;
	u32 prehdr[2], sh_desc[64];
	int refcnt;
};
struct caam_drv_req {
	struct qm_sg_entry fd_sgt[2];
	struct caam_drv_ctx *drv_ctx;
};

static struct device device;
static struct qman_fq fq;
static struct caam_drv_ctx ctx = { .req_fq = &fq, .qidev = &device };
static struct caam_drv_req request = { .drv_ctx = &ctx };
static bool map_error;
static int map_calls, unmap_calls, enqueue_calls, succeed_at, enqueue_error;
static bool watch_retry_idle, drain_saw_idle;
static int query_calls, query_error, sleep_calls;
static int retire_result, retire_calls, oos_result, oos_calls;
static int empty_result, empty_calls, destroy_calls, fq_free_calls;
static int ctx_free_calls, descriptor_unmap_calls;
static bool complete_retirement;
static u32 query_counts[4];
static unsigned int query_count;
static const dma_addr_t mapped_addr = 0xabcdefU;
static const dma_addr_t descriptor_addr = 0x123456U;

#ifdef TEST_CAAM_RELEASE
enum { ENCRYPT, DECRYPT, NUM_OP };
struct caam_ctx {
	struct device *jrdev;
	unsigned char key[64];
	dma_addr_t key_dma;
	int dir;
	struct caam_drv_ctx *drv_ctx[NUM_OP];
};
static struct device jrdev = { .parent = &device };
static struct caam_ctx tfm_ctx = {
	.jrdev = &jrdev,
	.drv_ctx = { &ctx, NULL },
};
static const dma_addr_t key_addr = 0x654321U;
static int key_unmap_calls, jr_free_calls, leaf_control_calls;
#endif

static void qm_fd_clear_fd(struct qm_fd *fd) { memset(fd, 0, sizeof(*fd)); }
static void qm_fd_set_compound(struct qm_fd *fd, u32 len) {}
static u32 qm_sg_entry_get_len(struct qm_sg_entry *sg) { return sg->len; }
static void qm_fd_addr_set64(struct qm_fd *fd, dma_addr_t addr) { fd->addr = addr; }

static dma_addr_t dma_map_single(struct device *dev, void *ptr, size_t size,
				 int direction)
{
	assert(dev == &device && ptr == request.fd_sgt);
	assert(size == sizeof(request.fd_sgt) && direction == DMA_BIDIRECTIONAL);
	map_calls++;
	return map_error ? UINT64_MAX : mapped_addr;
}

static bool dma_mapping_error(struct device *dev, dma_addr_t addr)
{
	return addr == UINT64_MAX;
}

void dma_unmap_single(struct device *dev, dma_addr_t addr, size_t size,
		      int direction)
{
	assert(dev == &device && direction == DMA_BIDIRECTIONAL);
	if (addr == mapped_addr) {
		assert(size == sizeof(request.fd_sgt));
		unmap_calls++;
	} else if (addr == descriptor_addr) {
		assert(size == sizeof(ctx.sh_desc) + sizeof(ctx.prehdr));
		descriptor_unmap_calls++;
#ifdef TEST_CAAM_RELEASE
	} else if (addr == key_addr) {
		assert(size == sizeof(tfm_ctx.key));
		key_unmap_calls++;
#endif
	} else {
		assert(false && "unexpected DMA address");
	}
}

static void refcount_inc(int *ref) { ++*ref; }
static void refcount_dec(int *ref)
{
	--*ref;
	if (watch_retry_idle && enqueue_calls < CAAM_QI_ENQUEUE_RETRIES &&
	    *ref == 1)
		drain_saw_idle = true;
}
static int refcount_read(int *ref) { return *ref; }

#ifdef TEST_CAAM_REAL_QMAN
#define QM_EQCR_VERB_CMD_ENQUEUE 1
#define cpu_to_be32(x) htobe32(x)
#define local_irq_save(flags) do { (flags) = 123; irq_depth++; } while (0)
#define local_irq_restore(flags) do { assert((flags) == 123); irq_depth--; } while (0)

struct qm_eqcr_entry {
	u32 fqid, tag;
	struct qm_fd fd;
};
struct qm_portal { struct qm_eqcr_entry entry; };
struct qman_portal {
	bool use_eqcr_ci_stashing;
	struct qm_portal p;
};

static struct qman_portal portal;
static int portal_refs, irq_depth, full_attempts, commits, ci_updates;

static struct qman_portal *get_affine_portal(void)
{
	portal_refs++;
	return &portal;
}

static void put_affine_portal(void) { portal_refs--; }

static struct qm_eqcr_entry *start_entry(struct qm_portal *p)
{
	enqueue_calls++;
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
	assert(p == &portal);
	ci_updates++;
}

static void qm_fqid_set(struct qm_eqcr_entry *entry, u32 fqid)
{
	entry->fqid = fqid;
}

static u32 fq_to_tag(struct qman_fq *queue) { return queue->idx; }

static void qm_eqcr_pvb_commit(struct qm_portal *p, int verb)
{
	assert(p == &portal.p && verb == QM_EQCR_VERB_CMD_ENQUEUE);
	commits++;
}

#include "caam_qi_qman_body.h"
#else
#ifdef TEST_CAAM_FQID
static int qman_enqueue_fqid(struct qman_fq *queue, u32 fqid,
			     const struct qm_fd *fd)
#else
static int qman_enqueue(struct qman_fq *queue, const struct qm_fd *fd)
#endif
{
	assert(queue == &fq && fd->addr == mapped_addr);
#ifdef TEST_CAAM_FQID
	assert(fqid == queue->fqid);
#endif
	enqueue_calls++;
	return enqueue_calls == succeed_at ? 0 : enqueue_error;
}
#endif

static int qman_query_fq_np(struct qman_fq *queue, struct qm_mcr_queryfq_np *np)
{
	assert(queue == &fq);
	query_calls++;
	if (query_error)
		return query_error;
	assert(query_calls <= (int)query_count);
	np->frm_cnt = htobe32(query_counts[query_calls - 1]);
	return 0;
}

static void msleep(unsigned int milliseconds)
{
	assert(milliseconds == 20);
	sleep_calls++;
	if (complete_retirement)
		fq.state = qman_fq_state_retired;
}

static int qman_retire_fq(struct qman_fq *queue, u32 *flags)
{
	assert(queue == &fq);
	retire_calls++;
	*flags = fq.flags;
	return retire_result;
}

static int empty_retired_fq(struct device *dev, struct qman_fq *queue)
{
	assert(dev == &device && queue == &fq);
	empty_calls++;
	return empty_result;
}

static int qman_oos_fq(struct qman_fq *queue)
{
	assert(queue == &fq);
	oos_calls++;
	return oos_result;
}

static void qman_destroy_fq(struct qman_fq *queue)
{
	assert(queue == &fq);
	destroy_calls++;
}

static void kfree(void *ptr)
{
	if (ptr == &fq)
		fq_free_calls++;
	else {
		assert(ptr == &ctx);
		ctx_free_calls++;
	}
}

#include "caam_qi_ownership_body.h"

#ifdef TEST_CAAM_RELEASE
static void caam_jr_free(struct device *dev)
{
	assert(dev == &jrdev);
	jr_free_calls++;
}

static void leaf_control_drv_ctx_rel(struct caam_drv_ctx *owner)
{
	if (owner) {
		assert(owner == &ctx);
		leaf_control_calls++;
	}
}

#include "caam_qi_exit_body.h"
#endif

static void reset(void)
{
	map_error = false;
	map_calls = unmap_calls = enqueue_calls = 0;
	watch_retry_idle = drain_saw_idle = false;
	succeed_at = 0;
	enqueue_error = -EBUSY;
	query_calls = query_error = sleep_calls = 0;
	retire_result = retire_calls = oos_result = oos_calls = 0;
	empty_result = empty_calls = destroy_calls = fq_free_calls = 0;
	ctx_free_calls = descriptor_unmap_calls = 0;
	complete_retirement = false;
	query_count = 0;
	memset(query_counts, 0, sizeof(query_counts));
	fq.flags = 0;
	fq.fqid = 37;
	fq.idx = 75;
	fq.state = qman_fq_state_sched;
	ctx.context_a = descriptor_addr;
	ctx.refcnt = 1;
#ifdef TEST_CAAM_REAL_QMAN
	memset(&portal, 0, sizeof(portal));
	portal_refs = irq_depth = full_attempts = commits = ci_updates = 0;
#endif
#ifdef TEST_CAAM_RELEASE
	tfm_ctx.key_dma = key_addr;
	tfm_ctx.dir = DMA_BIDIRECTIONAL;
	key_unmap_calls = jr_free_calls = leaf_control_calls = 0;
#endif
}

static int check(bool ok, const char *name)
{
	printf("CAAM QI %s: %s\n", name, ok ? "PASS" : "FAIL");
	return !ok;
}

#ifdef TEST_CAAM_REAL_QMAN
static int check_eqcr(void)
{
	int failures = 0;
	int rc;
	bool stash;

	for (int mode = 0; mode < 2; mode++) {
		stash = mode != 0;
		reset();
		portal.use_eqcr_ci_stashing = stash;
		full_attempts = CAAM_QI_ENQUEUE_RETRIES;
		watch_retry_idle = true;
		rc = caam_qi_enqueue(&device, &request);
		failures += check(rc == -EAGAIN && map_calls == 1 &&
				  unmap_calls == 1 && ctx.refcnt == 1 &&
				  !drain_saw_idle &&
				  enqueue_calls == CAAM_QI_ENQUEUE_RETRIES &&
				  !commits && !portal_refs && !irq_depth &&
				  (!!ci_updates == !stash),
				  stash ? "full stashed EQCR" : "full EQCR");

		reset();
		portal.use_eqcr_ci_stashing = stash;
		full_attempts = 2;
		watch_retry_idle = true;
		rc = caam_qi_enqueue(&device, &request);
		failures += check(!rc && map_calls == 1 && !unmap_calls &&
				  ctx.refcnt == 2 && enqueue_calls == 3 &&
				  !drain_saw_idle &&
				  commits == 1 && !portal_refs && !irq_depth &&
				  portal.p.entry.fqid == fq.fqid &&
				  be32toh(portal.p.entry.tag) == fq.idx &&
				  portal.p.entry.fd.addr == mapped_addr,
				  stash ? "stashed EQCR retry" : "EQCR retry");
	}

	printf("CAAM QI full-ring gate: %d uncontained case(s)\n", failures);
	return failures ? 1 : 0;
}
#endif

#ifdef TEST_CAAM_RELEASE
static int check_release(void)
{
	int failures = 0;

	reset();
	retire_result = -ETIMEDOUT;
	caam_drv_ctx_rel(&ctx);
	failures += check(retire_calls == 1 && !oos_calls && !destroy_calls &&
			  !fq_free_calls && !descriptor_unmap_calls &&
			  !ctx_free_calls, "retire failure retains owner");

	reset();
	oos_result = -ETIMEDOUT;
	caam_drv_ctx_rel(&ctx);
	failures += check(retire_calls == 1 && oos_calls == 1 && !destroy_calls &&
			  !fq_free_calls && !descriptor_unmap_calls &&
			  !ctx_free_calls, "OOS failure retains owner");

	reset();
	fq.flags = QMAN_FQ_STATE_NE;
	empty_result = -EIO;
	caam_drv_ctx_rel(&ctx);
	failures += check(empty_calls == 1 && !oos_calls && !destroy_calls &&
			  !fq_free_calls && !descriptor_unmap_calls &&
			  !ctx_free_calls, "drain failure retains owner");

	reset();
	retire_result = 1;
	complete_retirement = true;
	fq.flags = QMAN_FQ_STATE_ORL;
	oos_result = -EBUSY;
	caam_drv_ctx_rel(&ctx);
	failures += check(sleep_calls == 1 && oos_calls == 1 && !destroy_calls &&
			  !fq_free_calls && !descriptor_unmap_calls &&
			  !ctx_free_calls, "pending ORL retains owner");

	reset();
	caam_drv_ctx_rel(&ctx);
	failures += check(retire_calls == 1 && oos_calls == 1 &&
			  destroy_calls == 1 && fq_free_calls == 1 &&
			  descriptor_unmap_calls == 1 && ctx_free_calls == 1,
			  "successful release frees owner");

	reset();
	retire_result = -ETIMEDOUT;
	caam_exit_common(&tfm_ctx);
	failures += check(!key_unmap_calls && !jr_free_calls,
			  "failed release retains crypto parent");

	reset();
	caam_exit_common_leaf_control(&tfm_ctx);
	assert(leaf_control_calls == 1 && !fq_free_calls && !ctx_free_calls);
	failures += check(!key_unmap_calls && !jr_free_calls,
			  "simulated leaf retention protects crypto parent");

	printf("CAAM QI release gate: %d uncontained case(s)\n", failures);
	return failures ? 1 : 0;
}
#endif

int main(int argc, char **argv)
{
	int failures = 0;
	int rc;

	if (argc == 2 && !strcmp(argv[1], "--check-pending")) {
		reset();
		ctx.refcnt = 2;
		query_count = 1;
		rc = empty_caam_fq(&fq, &ctx);
		failures = check(rc != 0, "drain with simulated old job pending");
		return failures ? 1 : 0;
	}
#ifdef TEST_CAAM_REAL_QMAN
	if (argc == 2 && !strcmp(argv[1], "--check-eqcr"))
		return check_eqcr();
#endif
#ifdef TEST_CAAM_RELEASE
	if (argc == 2 && !strcmp(argv[1], "--check-release"))
		return check_release();
#endif
	assert(argc == 1);

	reset();
	map_error = true;
	rc = caam_qi_enqueue(&device, &request);
	failures += check(rc == -EIO && map_calls == 1 && !unmap_calls &&
			  !enqueue_calls && ctx.refcnt == 1, "DMA map failure");

	reset();
	succeed_at = 2;
	rc = caam_qi_enqueue(&device, &request);
	failures += check(!rc && map_calls == 1 && !unmap_calls &&
			  enqueue_calls == 2 && ctx.refcnt == 2,
			  "retry then submission");

	reset();
	rc = caam_qi_enqueue(&device, &request);
#ifdef TEST_CAAM_FQID
	failures += check(rc == -EAGAIN && map_calls == 1 && unmap_calls == 1 &&
#else
	failures += check(rc == -EBUSY && map_calls == 1 && unmap_calls == 1 &&
#endif
			  enqueue_calls == CAAM_QI_ENQUEUE_RETRIES &&
			  ctx.refcnt == 1, "failed retries unmap");

	reset();
	enqueue_error = -EIO;
	rc = caam_qi_enqueue(&device, &request);
	failures += check(rc == -EIO && map_calls == 1 && unmap_calls == 1 &&
			  enqueue_calls == 1 && ctx.refcnt == 1,
			  "non-retryable enqueue failure");

	reset();
	query_counts[0] = 1;
	query_counts[1] = 0;
	query_count = 2;
	rc = empty_caam_fq(&fq, &ctx);
	failures += check(!rc && query_calls == 2 && sleep_calls == 1,
			  "one queued frame is not empty");

	reset();
	query_counts[0] = 256;
	query_counts[1] = 0;
	query_count = 2;
	rc = empty_caam_fq(&fq, &ctx);
	failures += check(!rc && query_calls == 2 && sleep_calls == 1,
			  "256 queued frames are not empty");

	reset();
	query_counts[0] = 0x01000000U;
	query_counts[1] = 0;
	query_count = 2;
	rc = empty_caam_fq(&fq, &ctx);
	failures += check(!rc && query_calls == 1 && !sleep_calls,
			  "reserved bits are not frame count");

	reset();
	query_error = -ETIMEDOUT;
	rc = empty_caam_fq(&fq, &ctx);
	failures += check(rc == -ETIMEDOUT && query_calls == 1 && !sleep_calls,
			  "queue query failure");

	printf("CAAM QI targeted fixes: %d failing case(s)\n", failures);
	return failures ? 1 : 0;
}
