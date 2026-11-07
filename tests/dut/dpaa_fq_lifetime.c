// SPDX-License-Identifier: GPL-2.0
/* Model QMan outcomes around extracted DPAA teardown and QMan destruction. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum fq_state { qman_fq_state_parked, qman_fq_state_sched,
		qman_fq_state_retired, qman_fq_state_oos };
enum retire_result { RETIRE_PENDING, RETIRE_NONEMPTY, RETIRE_ERROR, RETIRE_EMPTY };
struct device { int unused; };
struct dpaa_priv { bool log; };
struct net_device { struct dpaa_priv priv; };
struct qman_fq {
	enum fq_state state;
	unsigned int flags, fqid, idx;
};
struct list_head { bool removed; };
struct dpaa_fq {
	struct qman_fq fq_base;
	struct net_device *net_dev;
	struct list_head list;
	int fq_type, xdp_rxq;
	bool init;
};

#define QMAN_FQ_FLAG_DYNAMIC_FQID 1
#define QMAN_FQ_STATE_CHANGING 0x80000000
#define QMAN_FQ_STATE_NE 0x40000000
#define QMAN_FQ_STATE_ORL 0x20000000
#define QMAN_FQ_STATE_BLOCKOOS 0xe0000000
#define DPAA_GENALLOC_OFF 0
#define FQ_TYPE_RX_DEFAULT 1
#define FQ_TYPE_RX_PCD 2
#define DPAA_ASSERT(x) ((void)0)
#define pr_debug(...) ((void)0)
#define smp_wmb() ((void)0)
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

static struct qman_fq *fq_table[16];
static int qm_fqalloc;
static enum retire_result next_retire;

static bool fq_isset(struct qman_fq *fq, unsigned int flag) { return fq->flags & flag; }
static void dev_err(struct device *dev, const char *format, ...) {}
static int qman_shutdown_fq(unsigned int fqid) { return 0; }
static void gen_pool_free(int pool, unsigned int fqid, int n) {}
static struct dpaa_priv *netdev_priv(struct net_device *dev) { return &dev->priv; }
static bool netif_msg_drv(const struct dpaa_priv *priv) { return priv->log; }
static unsigned int qman_fq_fqid(struct qman_fq *fq) { return fq->fqid; }
static bool xdp_rxq_info_is_reg(int *info) { return false; }
static void xdp_rxq_info_unreg(int *info) {}
static void list_del(struct list_head *list) { list->removed = true; }

static int qman_retire_fq(struct qman_fq *fq, unsigned int *flags)
{
	switch (next_retire) {
	case RETIRE_PENDING:
		fq->flags |= QMAN_FQ_STATE_CHANGING;
		return 1;
	case RETIRE_NONEMPTY:
		fq->state = qman_fq_state_retired;
		fq->flags |= QMAN_FQ_STATE_NE;
		return 0;
	case RETIRE_ERROR:
		return -ETIMEDOUT;
	case RETIRE_EMPTY:
		fq->state = qman_fq_state_retired;
		return 0;
	}
	return -EINVAL;
}

static int qman_oos_fq(struct qman_fq *fq)
{
	if (fq->state != qman_fq_state_retired)
		return -EINVAL;
	if (fq->flags & QMAN_FQ_STATE_BLOCKOOS)
		return -EBUSY;
	fq->state = qman_fq_state_oos;
	return 0;
}

#include "qman_destroy_body.h"
#include "dpaa_fq_free_body.h"

static int check(enum retire_result result, bool log, const char *label)
{
	struct device dev = { 0 };
	struct net_device netdev = { .priv = { .log = log } };
	struct dpaa_fq fq = {
		.fq_base = { .state = qman_fq_state_sched, .fqid = 3, .idx = 3 },
		.net_dev = &netdev,
		.fq_type = 3,
		.init = true,
	};
	int rc;

	memset(fq_table, 0, sizeof(fq_table));
	fq_table[3] = &fq.fq_base;
	next_retire = result;
#ifdef LIFETIME_ORACLE_CONTROLS
	/* Model retention on an unsettled retirement, not a production fix. */
	rc = result == RETIRE_EMPTY ?
		dpaa_fq_free_entry(&dev, &fq.fq_base) : -EINPROGRESS;
#else
	rc = dpaa_fq_free_entry(&dev, &fq.fq_base);
#endif
	if (result == RETIRE_EMPTY) {
		if (!rc && fq.list.removed && !fq_table[3]) {
			puts("QMan immediate empty retirement: removed");
			return 0;
		}
		puts("QMan immediate empty retirement control: FAILED");
		return 1;
	}
	if (rc < 0 && !fq.list.removed && fq_table[3]) {
		printf("QMan %s: owner retained\n", label);
		return 0;
	}
	printf("QMan %s: NOT CONTAINED (rc=%d, owner unlinked=%d, lookup retained=%d)\n",
	       label, rc, fq.list.removed, !!fq_table[3]);
	return 1;
}

int main(void)
{
	int failures = 0;

	failures += check(RETIRE_EMPTY, true, "empty");
	failures += check(RETIRE_PENDING, true, "pending retirement");
	failures += check(RETIRE_PENDING, false, "pending retirement without driver logging");
	failures += check(RETIRE_NONEMPTY, true, "nonempty retired queue");
	failures += check(RETIRE_ERROR, true, "retire timeout");
	printf("QMan teardown characterization: %d uncontained case(s)\n", failures);
	return failures ? 1 : 0;
}
