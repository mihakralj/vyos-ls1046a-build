// SPDX-License-Identifier: GPL-2.0
/* Host model for source-extracted CGR registration and portal routing. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint16_t u16;
typedef uint32_t u32;

#define CGR_NUM 256
#define QMAN_CGR_FLAG_USE_INIT 1
#define QM_CGR_WE_CSCN_TARG 1
#define cpu_to_be16(x) (x)
#define be32_to_cpu(x) (x)
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define pr_err_ratelimited(...) ((void)0)

struct list_head {
	struct list_head *next, *prev;
};

static void init_list(struct list_head *head)
{
	head->next = head;
	head->prev = head;
}

static void list_add(struct list_head *node, struct list_head *head)
{
	node->next = head->next;
	node->prev = head;
	head->next->prev = node;
	head->next = node;
}

static void list_del(struct list_head *node)
{
	node->prev->next = node->next;
	node->next->prev = node->prev;
	node->next = node->prev = NULL;
}

#define list_for_each_entry(pos, head, member) \
	for (struct list_head *entry = (head)->next; \
	     entry != (head) && \
	     ((pos) = (void *)((char *)entry - \
			      offsetof(struct qman_cgr, member)), true); \
	     entry = entry->next)

struct cgr_data {
	u32 cscn_targ;
	bool cscn_en;
};

struct qm_mcr_querycgr {
	struct cgr_data cgr;
};

struct qm_mcc_initcgr {
	u16 we_mask;
	struct cgr_data cgr;
};

struct qman_cgrs {
	bool congested;
};

struct qman_portal;

struct qman_cgr {
	unsigned int cgrid;
	void (*cb)(struct qman_portal *, struct qman_cgr *, int);
	u16 chan;
	int portal_cpu;
	struct list_head node;
};

struct portal_config {
	u16 channel;
};

struct qman_portal {
	struct list_head cgr_cbs;
	int cgr_lock;
	struct qman_cgrs cgrs[2];
	struct portal_config *config;
};

static struct qman_portal portals[2];
static struct portal_config configs[2];
static unsigned char qman_cgr_cpus[CGR_NUM];
static int current_cpu, references, preempt_count;
static int modify_calls, modified_cpu;

#define PORTAL_IDX(p) ((p)->config->channel)

static void lock(int *value) { assert(!*value); *value = 1; }
static void unlock(int *value) { assert(*value); *value = 0; }
#define raw_spin_lock_irq(lockp) lock(lockp)
#define raw_spin_unlock_irq(lockp) unlock(lockp)
#define raw_spin_lock_irqsave(lockp, flags) do { (flags) = 0; lock(lockp); } while (0)
#define raw_spin_unlock_irqrestore(lockp, flags) do { (void)(flags); unlock(lockp); } while (0)

static void preempt_disable(void) { preempt_count++; }
static void preempt_enable(void) { assert(preempt_count > 0); preempt_count--; }
static int smp_processor_id(void) { return current_cpu; }

static struct qman_portal *get_affine_portal(void)
{
	references++;
	return &portals[current_cpu];
}

static void put_affine_portal(void)
{
	assert(references > 0);
	references--;
}

static int smp_call_function_single(int cpu, void (*fn)(void *),
				    void *arg, bool wait)
{
	int saved = current_cpu;

	assert(preempt_count && wait);
	current_cpu = cpu;
	fn(arg);
	current_cpu = saved;
	return 0;
}

static int qman_query_cgr(struct qman_cgr *cgr, struct qm_mcr_querycgr *result)
{
	memset(result, 0, sizeof(*result));
	return 0;
}

static int qm_modify_cgr(struct qman_cgr *cgr, unsigned int flags,
			 struct qm_mcc_initcgr *opts)
{
	modify_calls++;
	modified_cpu = current_cpu;
	return 0;
}

static void qm_cgr_cscn_targ_set(struct cgr_data *data, int portal_id, u32 old) {}
static void qm_cgr_cscn_targ_clear(struct cgr_data *data, int portal_id, u32 old) {}
static bool qman_cgrs_get(struct qman_cgrs *cgrs, unsigned int id)
{
	return cgrs->congested;
}

#include "qman_cgr_owner_body.h"

static void callback(struct qman_portal *p, struct qman_cgr *cgr, int value) {}

static void reset(void)
{
	memset(portals, 0, sizeof(portals));
	memset(configs, 0, sizeof(configs));
	memset(qman_cgr_cpus, 0, sizeof(qman_cgr_cpus));
	for (int cpu = 0; cpu < 2; cpu++) {
		configs[cpu].channel = 3 + cpu;
		portals[cpu].config = &configs[cpu];
		init_list(&portals[cpu].cgr_cbs);
	}
	current_cpu = references = preempt_count = 0;
	modify_calls = 0;
	modified_cpu = -1;
}

static bool linked(int cpu, struct qman_cgr *cgr)
{
	struct list_head *entry;
	struct list_head *head = &portals[cpu].cgr_cbs;

	for (entry = head->next; entry != head; entry = entry->next)
		if (entry == &cgr->node)
			return true;
	return false;
}

static void finished(void)
{
	assert(!references && !preempt_count && !current_cpu);
	assert(!portals[0].cgr_lock && !portals[1].cgr_lock);
}

static int distinct_portals(void)
{
	struct qman_cgr first = { .cgrid = 7, .cb = callback };
	struct qman_cgr second = { .cgrid = 7, .cb = callback };
	struct qm_mcc_initcgr opts = {0};
	int failures = 0, ret;

	reset();
	assert(qman_create_cgr(&first, 0, &opts) == 0);
	current_cpu = 1;
	assert(qman_create_cgr(&second, 0, &opts) == 0);
	current_cpu = 0;
	assert(linked(0, &first) && linked(1, &second));

	modify_calls = 0;
	ret = qman_update_cgr_safe(&first, &opts);
	if (ret || modify_calls != 1 || modified_cpu != 0) {
		fprintf(stderr, "shared CGRID update missed first portal: %d\n", ret);
		failures++;
	}
	modify_calls = 0;
	ret = qman_update_cgr_safe(&second, &opts);
	if (ret || modify_calls != 1 || modified_cpu != 1) {
		fprintf(stderr, "shared CGRID update missed second portal: %d\n", ret);
		failures++;
	}

	qman_delete_cgr_safe(&first);
	if (linked(0, &first) || !linked(1, &second)) {
		fprintf(stderr, "shared CGRID delete left the first callback linked\n");
		failures++;
	}
	qman_delete_cgr_safe(&second);
	if (linked(1, &second)) {
		fprintf(stderr, "shared CGRID delete left the second callback linked\n");
		failures++;
	}
	finished();
	return failures;
}

static void shared_portal(void)
{
	struct qman_cgr first = { .cgrid = 9, .cb = callback };
	struct qman_cgr second = { .cgrid = 9, .cb = callback };
	struct qm_mcc_initcgr opts = {0};

	reset();
	assert(qman_create_cgr(&first, 0, &opts) == 0);
	assert(qman_create_cgr(&second, 0, &opts) == 0);
	modify_calls = 0;
	qman_delete_cgr_safe(&first);
	assert(!linked(0, &first) && linked(0, &second));
	assert(!modify_calls);
	qman_delete_cgr_safe(&second);
	assert(!linked(0, &second) && modify_calls == 1);
	finished();
}

int main(void)
{
	int failures = distinct_portals();

	shared_portal();
	if (failures)
		return 1;
	puts("QMan CGR ownership: cross-portal and same-portal sharing passed");
	return 0;
}
