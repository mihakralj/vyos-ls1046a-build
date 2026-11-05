// SPDX-License-Identifier: GPL-2.0
/* Host harness for the generated HC barrier and confirmation paths. */
#include <assert.h>
#include <endian.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t __be32;
typedef u64 dma_addr_t;

#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define smp_load_acquire(ptr) (*(ptr))
#define ARRAY_SIZE(a) ((int)(sizeof(a) / sizeof((a)[0])))
#define cpu_to_be32(x) htobe32(x)
#define be32_to_cpu(x) be32toh(x)
#define dma_wmb() ((void)0)
#define dma_rmb() ((void)0)
#define pr_err(...) ((void)0)
#define pr_err_ratelimited(...) fprintf(stderr, __VA_ARGS__)
#define HC_FRAME_LEN 140
#define HC_HCOR_GBL 0x20000000U
#define HC_HCOR_OPCODE_SYNC 2U
#define HC_TIMEOUT_NS 1000U
#define HC_BUSY_POLL_NS 300U

#define module_param_named(name, value, type, perm) \
	_Static_assert((perm) == 0400, "HC barrier must not be writable at runtime")

struct mutex { int unused; };
typedef int spinlock_t;
static void unlock_hook(void);
#define mutex_lock(lock) ((void)(lock))
#define mutex_unlock(lock) ((void)(lock))
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
	(void)(lock); (void)(flags); unlock_hook(); \
} while (0)

struct fman_pcd { int unused; };
struct fman_pcd_hc;
struct qman_fq { struct fman_pcd_hc *hc; };
struct qm_fd { u64 addr; u32 status, len; };
struct qm_dqrr_entry { struct qm_fd fd; };
struct qm_mcr_queryfq_np { int unused; };

enum qman_cb_dqrr_result { qman_cb_dqrr_consume };
enum hc_src { HC_SRC_NONE, HC_SRC_CONF, HC_SRC_ERR };

struct fman_pcd_hc_fq {
	struct qman_fq fq;
	struct fman_pcd_hc *hc;
};

struct fman_pcd_hc {
	struct mutex lock;
	spinlock_t cb_lock;
	struct fman_pcd *pcd;
	struct fman_pcd_hc_fq tx, conf, err;
	__be32 *buf;
	dma_addr_t dma;
	bool enabled, failed, in_flight, done;
	u32 seq, in_flight_seq, last_status, last_words[4];
	enum hc_src last_src;
	u64 sent, confirmed, err_frames, late, stray, ern, enq_err, timeouts;
	u64 bad_seq, lat_min_ns, lat_max_ns, lat_sum_ns;
	int np_rc;
	struct qm_mcr_queryfq_np np;
	bool np_valid;
};

static struct fman_pcd_hc *hc_state;
static struct fman_pcd_hc hc;
static struct fman_pcd pcd, other_pcd;
static __be32 frame[HC_FRAME_LEN / sizeof(__be32)];
static u64 now;
static unsigned int enqueues, disables, teardowns;
static bool pending_bad_sequence;
static struct qm_dqrr_entry pending_dq;

enum delivery { CONFIRM, ERROR_FQ, BAD_SEQUENCE, BAD_ERROR_SEQUENCE,
		DELAYED_BAD_SEQUENCE, CONF_ERROR_STATUS, STRAY_THEN_CONFIRM,
		REJECT, TIMEOUT };
static enum delivery delivery;

static u64 qm_fd_addr(const struct qm_fd *fd) { return fd->addr; }
static void qm_fd_addr_set64(struct qm_fd *fd, u64 addr) { fd->addr = addr; }
static void qm_fd_set_contig(struct qm_fd *fd, int offset, int len)
{
	(void)offset;
	fd->len = len;
}
static u64 ktime_get_ns(void) { return now += 200; }
static void udelay(unsigned int us) { (void)us; }
static void usleep_range(unsigned int min, unsigned int max)
{
	(void)min;
	(void)max;
}
static int qman_query_fq_np(struct qman_fq *fq, struct qm_mcr_queryfq_np *np)
{
	(void)fq;
	(void)np;
	return 0;
}
static int hc_port_disable(struct fman_pcd_hc *state)
{
	disables++;
	state->enabled = false;
	return 0;
}
static bool hc_fqs_teardown(struct fman_pcd_hc *state)
{
	(void)state;
	teardowns++;
	return true;
}
static int qman_enqueue(struct qman_fq *fq, const struct qm_fd *fd);

#include "hc_barrier_body.h"

static void unlock_hook(void)
{
	if (!pending_bad_sequence)
		return;
	pending_bad_sequence = false;
	hc.buf[3] = cpu_to_be32(hc.in_flight_seq ^ 0x80000000U);
	hc_dqrr(&hc.conf, &pending_dq, HC_SRC_CONF);
}

static int qman_enqueue(struct qman_fq *fq, const struct qm_fd *fd)
{
	struct fman_pcd_hc *state = fq->hc;
	struct qm_dqrr_entry dq = { .fd = *fd };

	enqueues++;
	if (delivery == REJECT)
		return -EBUSY;
	if (delivery == TIMEOUT)
		return 0;
	if (delivery == DELAYED_BAD_SEQUENCE) {
		pending_dq = dq;
		pending_bad_sequence = true;
		return 0;
	}
	if (delivery == STRAY_THEN_CONFIRM) {
		dq.fd.addr++;
		hc_dqrr(&state->conf, &dq, HC_SRC_CONF);
		dq.fd.addr--;
	}
	if (delivery == BAD_SEQUENCE || delivery == BAD_ERROR_SEQUENCE)
		state->buf[3] = cpu_to_be32(state->seq ^ 0x80000000U);
	if (delivery == CONF_ERROR_STATUS)
		dq.fd.status = cpu_to_be32(0x01000000U); /* FM_FD_ERR_DMA */
	hc_dqrr(delivery == ERROR_FQ || delivery == BAD_ERROR_SEQUENCE ?
		&state->err : &state->conf, &dq,
		delivery == ERROR_FQ || delivery == BAD_ERROR_SEQUENCE ?
		HC_SRC_ERR : HC_SRC_CONF);
	return 0;
}

static void fresh_boot(bool enable)
{
	memset(&hc, 0, sizeof(hc));
	memset(frame, 0, sizeof(frame));
	ehash_hc_sync_enable = enable;
	hc_state = &hc;
	hc.pcd = &pcd;
	hc.enabled = true;
	hc.buf = frame;
	hc.dma = 0x1000;
	hc.tx.fq.hc = &hc;
	hc.conf.hc = &hc;
	hc.err.hc = &hc;
	now = enqueues = disables = teardowns = 0;
	pending_bad_sequence = false;
	delivery = CONFIRM;
}

static void test_boot_barrier(void)
{
	fresh_boot(false);
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0 && enqueues == 0);
	fresh_boot(true);
	delivery = REJECT;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EBUSY);
	assert(hc.enq_err == 1 && !hc.in_flight && !hc.failed);
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EBUSY);
	assert(fman_pcd_hc_ehash_sync(&other_pcd) == -ENODEV);
	assert(hc_fini(&hc) == -EBUSY && disables == 0 && teardowns == 0);
	delivery = CONFIRM;
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0);
	assert(hc.confirmed == 1);
}

static void test_bad_confirmation_sequence(void)
{
	for (int error_fq = 0; error_fq < 2; error_fq++) {
		fresh_boot(true);
		hc.seq = 37;
		delivery = error_fq ? BAD_ERROR_SEQUENCE : BAD_SEQUENCE;
		assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO);
		assert(hc.failed && hc.bad_seq == 1 && hc.confirmed == 0);
		assert(hc.last_words[3] == (37U ^ 0x80000000U));
		delivery = CONFIRM;
		assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO && enqueues == 1);
		assert(hc_fini(&hc) == -EBUSY && disables == 0);
	}

	fresh_boot(true);
	hc.seq = 91;
	delivery = DELAYED_BAD_SEQUENCE;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO);
	assert(hc.failed && hc.bad_seq == 1 && hc.timeouts == 0);
}

static void test_transport_outcomes(void)
{
	struct qm_dqrr_entry late = { .fd.addr = 0x1000 };

	fresh_boot(true);
	hc.seq = 7;
	delivery = STRAY_THEN_CONFIRM;
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0);
	assert(hc.stray == 1 && hc.confirmed == 1 && hc.seq == 8);
	delivery = ERROR_FQ;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO);
	assert(hc.err_frames == 1 && !hc.failed);
	delivery = CONF_ERROR_STATUS;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO);
	assert(hc.err_frames == 2 && hc.confirmed == 1 && !hc.failed);
	delivery = CONFIRM;
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0 && hc.confirmed == 2);
	hc.seq = UINT32_MAX;
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0 && hc.seq == 0);
	assert(fman_pcd_hc_ehash_sync(&pcd) == 0 && hc.seq == 1);

	fresh_boot(true);
	delivery = TIMEOUT;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -ETIMEDOUT);
	assert(hc.failed && hc.timeouts == 1 && !hc.in_flight);
	hc_dqrr(&hc.conf, &late, HC_SRC_CONF);
	assert(hc.late == 1 && hc.failed);
	assert(fman_pcd_hc_ehash_sync(&pcd) == -EIO);
	assert(hc_fini(&hc) == -EBUSY && disables == 0);

	fresh_boot(true);
	hc_state = NULL;
	assert(fman_pcd_hc_ehash_sync(&pcd) == -ENODEV);

	fresh_boot(false);
	assert(hc_fini(&hc) == 0 && disables == 1 && teardowns == 1);
}

int main(int argc, char **argv)
{
	assert(argc == 2);
	if (!strcmp(argv[1], "boot"))
		test_boot_barrier();
	else if (!strcmp(argv[1], "sequence"))
		test_bad_confirmation_sequence();
	else if (!strcmp(argv[1], "transport"))
		test_transport_outcomes();
	else
		return 2;
	puts("OK: HC barrier host scenario");
	return 0;
}
