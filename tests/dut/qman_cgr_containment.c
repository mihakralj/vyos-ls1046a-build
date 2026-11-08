// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted QMan and DPAA CGR paths. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint16_t u16;
typedef uint32_t u32;
typedef unsigned int uint;

#define CGR_NUM 256
#define GFP_KERNEL 0
#define QMAN_CGR_FLAG_USE_INIT 1
#define QM_CGR_WE_CSCN_TARG 1
#define QM_CGR_WE_CSCN_EN 2
#define QM_CGR_WE_CS_THRES 4
#define QM_CGR_WE_CSTD_EN 8
#define QM_CGR_WE_MODE 16
#define QM_CGR_EN 1
#define QMAN_CGR_MODE_FRAME 1
#define MAC_10000FD 1
#define DPAA_CS_THRESHOLD_10G 100
#define DPAA_CS_THRESHOLD_1G 10
#define cpu_to_be16(x) (x)
#define be32_to_cpu(x) (x)
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define clamp_t(type, v, lo, hi) \
	((type)(v) < (type)(lo) ? (type)(lo) : \
	 ((type)(v) > (type)(hi) ? (type)(hi) : (type)(v)))
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define pr_debug(...) ((void)0)
#define pr_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define pr_err_ratelimited(...) (error_logs++)
#define netif_msg_drv(priv) false

struct list_head {
	struct list_head *next, *prev;
};

struct device {
	struct device *parent;
};

struct net_device {
	struct device dev;
	int stops, wakes;
};

struct qman_portal;
struct qman_cgr {
	u32 cgrid;
	void (*cb)(struct qman_portal *, struct qman_cgr *, int);
	u16 chan;
	int portal_cpu;
	struct list_head node;
};

struct dpaa_priv;
struct dpaa_cgr_context {
	struct qman_cgr cgr;
	struct dpaa_priv *priv;
};

struct mac_device {
	struct { unsigned int mac_capabilities; } phylink_config;
	unsigned char addr[6];
};

struct dpaa_bp {
	u32 config_count;
};

struct dpaa_priv {
	struct net_device *net_dev;
	struct mac_device *mac_dev;
	struct dpaa_bp *dpaa_bp;
	struct {
		struct dpaa_cgr_context *ctx;
		u32 congestion_start_jiffies, cgr_congested_count;
		u32 congested_jiffies;
	} cgr_data;
	struct dpaa_cgr_context *ingress_cgr;
	bool use_ingress_cgr;
};

struct cgr_data {
	u32 cscn_targ, cs_thres;
	bool cscn_en;
	unsigned int mode, cstd_en;
};
struct qm_mcr_querycgr { struct cgr_data cgr; };
struct qm_mcc_initcgr { u16 we_mask; struct cgr_data cgr; };
struct qman_cgrs { bool congested; };
struct portal_config { u16 channel; struct device *dev; };
struct qman_portal {
	struct list_head cgr_cbs;
	pthread_mutex_t cgr_lock;
	struct qman_cgrs cgrs[2];
	struct portal_config *config;
};

static struct qman_portal qman_affine_portal[2];
static struct portal_config configs[2];
static int current_cpu, references, preempt_count;
static int query_calls, modify_calls, query_fail_at, modify_fail_at;
static int alloc_error, release_error, release_calls, error_logs;
static int ctx_allocs, ctx_frees;
static bool ipi_fails, allocation_fails, allocated_ids[CGR_NUM];
static u32 jiffies = 100;
static uint dpaa_ingress_quarters = 2;
static pthread_mutex_t gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gate_cond = PTHREAD_COND_INITIALIZER;
static bool block_callback, callback_entered, release_callback;
static bool monitor_disable, disable_attempted, disable_done;

#define PORTAL_IDX(p) ((p)->config->channel)
#define per_cpu_ptr(ptr, cpu) (&(*(ptr))[(cpu)])

static void init_list(struct list_head *head)
{
	head->next = head->prev = head;
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

static void lock_irqsave(pthread_mutex_t *lock, unsigned long *flags)
{
	*flags = 0;
	assert(!pthread_mutex_lock(&gate_lock));
	if (monitor_disable) {
		disable_attempted = true;
		assert(!pthread_cond_broadcast(&gate_cond));
	}
	assert(!pthread_mutex_unlock(&gate_lock));
	assert(!pthread_mutex_lock(lock));
}

#define raw_spin_lock_irq(lock) assert(!pthread_mutex_lock(lock))
#define raw_spin_unlock_irq(lock) assert(!pthread_mutex_unlock(lock))
#define raw_spin_lock_irqsave(lock, flags) lock_irqsave(lock, &(flags))
#define raw_spin_unlock_irqrestore(lock, flags) \
	do { (void)(flags); assert(!pthread_mutex_unlock(lock)); } while (0)

static void preempt_disable(void) { preempt_count++; }
static void preempt_enable(void) { assert(preempt_count > 0); preempt_count--; }
static int smp_processor_id(void) { return current_cpu; }
static int num_possible_cpus(void) { return 2; }

static struct qman_portal *get_affine_portal(void)
{
	references++;
	return &qman_affine_portal[current_cpu];
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

	assert(wait && preempt_count);
	if (ipi_fails)
		return -ENXIO;
	current_cpu = cpu;
	fn(arg);
	current_cpu = saved;
	return 0;
}

static int qman_query_cgr(struct qman_cgr *cgr, struct qm_mcr_querycgr *result)
{
	query_calls++;
	if (query_calls == query_fail_at)
		return -ETIMEDOUT;
	memset(result, 0, sizeof(*result));
	return 0;
}

static int qm_modify_cgr(struct qman_cgr *cgr, u32 flags,
			 struct qm_mcc_initcgr *opts)
{
	modify_calls++;
	return modify_calls == modify_fail_at ? -ETIMEDOUT : 0;
}

static void qm_cgr_cscn_targ_set(struct cgr_data *data, int portal_id, u32 old) {}
static void qm_cgr_cscn_targ_clear(struct cgr_data *data, int portal_id, u32 old) {}
static bool qman_cgrs_get(struct qman_cgrs *cgrs, unsigned int id)
{
	return cgrs->congested;
}
static void qm_cgr_cs_thres_set64(u32 *threshold, uint64_t value, int round)
{
	*threshold = value;
}

static int qman_alloc_cgrid(u32 *id)
{
	if (alloc_error)
		return alloc_error;
	for (u32 i = 7; i < CGR_NUM; i++) {
		if (!allocated_ids[i]) {
			allocated_ids[i] = true;
			*id = i;
			return 0;
		}
	}
	return -ENOSPC;
}

static int qman_release_cgrid(u32 id)
{
	release_calls++;
	if (release_error)
		return release_error;
	assert(id < CGR_NUM && allocated_ids[id]);
	allocated_ids[id] = false;
	return 0;
}

static void *kzalloc(size_t size, int flags)
{
	if (allocation_fails)
		return NULL;
	ctx_allocs++;
	return calloc(1, size);
}

static void kfree(void *ptr)
{
	assert(ptr);
	ctx_frees++;
	free(ptr);
}

static void dev_err(struct device *dev, const char *format, ...)
{
	error_logs++;
}

static void dev_warn(struct device *dev, const char *format, ...)
{
	error_logs++;
}

static void netif_tx_stop_all_queues(struct net_device *dev)
{
	dev->stops++;
	assert(!pthread_mutex_lock(&gate_lock));
	if (block_callback) {
		callback_entered = true;
		assert(!pthread_cond_broadcast(&gate_cond));
		while (!release_callback)
			assert(!pthread_cond_wait(&gate_cond, &gate_lock));
	}
	assert(!pthread_mutex_unlock(&gate_lock));
}

static void netif_tx_wake_all_queues(struct net_device *dev)
{
	dev->wakes++;
}

#include "qman_cgr_containment_body.h"

struct fixture {
	struct device parent;
	struct net_device netdev;
	struct dpaa_priv priv;
	struct mac_device mac;
	struct dpaa_bp bp;
};

static void reset(void)
{
	for (int cpu = 0; cpu < 2; cpu++) {
		assert(!pthread_mutex_trylock(&qman_affine_portal[cpu].cgr_lock));
		assert(!pthread_mutex_unlock(&qman_affine_portal[cpu].cgr_lock));
		init_list(&qman_affine_portal[cpu].cgr_cbs);
		memset(qman_affine_portal[cpu].cgrs, 0,
		       sizeof(qman_affine_portal[cpu].cgrs));
		configs[cpu].channel = 3 + cpu;
		qman_affine_portal[cpu].config = &configs[cpu];
	}
	current_cpu = references = preempt_count = 0;
	query_calls = modify_calls = query_fail_at = modify_fail_at = 0;
	alloc_error = release_error = release_calls = error_logs = 0;
	ctx_allocs = ctx_frees = 0;
	ipi_fails = allocation_fails = false;
	memset(allocated_ids, 0, sizeof(allocated_ids));
	block_callback = callback_entered = release_callback = false;
	monitor_disable = disable_attempted = disable_done = false;
}

static void setup(struct fixture *f)
{
	memset(f, 0, sizeof(*f));
	f->netdev.dev.parent = &f->parent;
	f->priv.net_dev = &f->netdev;
	f->priv.mac_dev = &f->mac;
	f->priv.dpaa_bp = &f->bp;
	f->bp.config_count = 100;
}

static bool linked(int cpu, struct qman_cgr *cgr)
{
	struct list_head *head = &qman_affine_portal[cpu].cgr_cbs;
	struct list_head *entry;

	for (entry = head->next; entry != head; entry = entry->next)
		if (entry == &cgr->node)
			return true;
	return false;
}

static void dispatch(struct qman_portal *portal, int congested)
{
	struct qman_cgr *cgr;

	raw_spin_lock_irq(&portal->cgr_lock);
	list_for_each_entry(cgr, &portal->cgr_cbs, node)
		if (cgr->cb)
			cgr->cb(portal, cgr, congested);
	raw_spin_unlock_irq(&portal->cgr_lock);
}

static void cleanup_quarantine(int cpu, struct dpaa_cgr_context *ctx)
{
	u32 id = ctx->cgr.cgrid;

	assert(linked(cpu, &ctx->cgr));
	list_del(&ctx->cgr.node);
	kfree(ctx);
	assert(allocated_ids[id]);
	allocated_ids[id] = false;
}

static void successful_cleanup(void)
{
	struct fixture f;
	struct dpaa_cgr_context *egress, *ingress;
	u32 egress_id, ingress_id, other;

	reset();
	setup(&f);
	assert(!dpaa_eth_cgr_init(&f.priv));
	assert(!dpaa_ingress_cgr_init(&f.priv));
	egress = f.priv.cgr_data.ctx;
	ingress = f.priv.ingress_cgr;
	egress_id = egress->cgr.cgrid;
	ingress_id = ingress->cgr.cgrid;
	assert(f.priv.use_ingress_cgr && egress_id != ingress_id);
	dispatch(&qman_affine_portal[0], 1);
	dispatch(&qman_affine_portal[0], 0);
	assert(f.netdev.stops == 1 && f.netdev.wakes == 1);
	assert(f.priv.cgr_data.cgr_congested_count == 1);
	dpaa_cgr_deactivate(egress);
	assert(!egress->priv && !egress->cgr.cb);
	dispatch(&qman_affine_portal[0], 1);
	assert(f.netdev.stops == 1);
	dpaa_cgr_release(&f.parent, &f.priv.cgr_data.ctx);
	dpaa_cgr_release(&f.parent, &f.priv.ingress_cgr);
	assert(!f.priv.cgr_data.ctx && !f.priv.ingress_cgr);
	assert(allocated_ids[egress_id] && allocated_ids[ingress_id]);
	assert(ctx_allocs == 2 && ctx_frees == 2 && !release_calls);
	assert(!qman_alloc_cgrid(&other));
	assert(other != egress_id && other != ingress_id);
	assert(!qman_release_cgrid(other));
	assert(!references && !preempt_count);
}

static void failed_creation(bool ingress, bool modify)
{
	struct fixture f;
	u32 other;
	int result;

	reset();
	setup(&f);
	if (modify)
		modify_fail_at = 1;
	else
		query_fail_at = 1;
	result = ingress ? dpaa_ingress_cgr_init(&f.priv)
			 : dpaa_eth_cgr_init(&f.priv);
	assert(result == -ETIMEDOUT);
	assert(!f.priv.cgr_data.ctx && !f.priv.ingress_cgr);
	assert(allocated_ids[7] && !release_calls && ctx_frees == 1);
	assert(qman_affine_portal[0].cgr_cbs.next ==
	       &qman_affine_portal[0].cgr_cbs);
	assert(!qman_alloc_cgrid(&other) && other != 7);
	assert(!qman_release_cgrid(other));
	assert(error_logs && !references && !preempt_count);
}

static void failed_allocations(void)
{
	struct fixture f;

	reset();
	setup(&f);
	allocation_fails = true;
	assert(dpaa_eth_cgr_init(&f.priv) == -ENOMEM);
	assert(dpaa_ingress_cgr_init(&f.priv) == -ENOMEM);
	assert(!ctx_allocs && !ctx_frees && !allocated_ids[7]);
	allocation_fails = false;
	alloc_error = -ENOSPC;
	assert(dpaa_eth_cgr_init(&f.priv) == -ENOSPC);
	assert(dpaa_ingress_cgr_init(&f.priv) == -ENOSPC);
	assert(ctx_allocs == 2 && ctx_frees == 2 && !allocated_ids[7]);
	assert(!f.priv.cgr_data.ctx && !f.priv.ingress_cgr);
}

enum delete_failure { QUERY_FAILURE, MODIFY_FAILURE, OFFLINE_FAILURE };

static void inject_delete_failure(enum delete_failure failure)
{
	if (failure == QUERY_FAILURE)
		query_fail_at = query_calls + 1;
	else if (failure == MODIFY_FAILURE)
		modify_fail_at = modify_calls + 1;
	else
		ipi_fails = true;
}

static void failed_deletion(int owner_cpu, enum delete_failure failure)
{
	struct fixture f;
	struct dpaa_cgr_context *ctx;
	u32 id, other;
	int expected = failure == OFFLINE_FAILURE ? -ENXIO : -ETIMEDOUT;

	reset();
	setup(&f);
	current_cpu = owner_cpu;
	assert(!dpaa_eth_cgr_init(&f.priv));
	ctx = f.priv.cgr_data.ctx;
	id = ctx->cgr.cgrid;
	current_cpu = 0;
	inject_delete_failure(failure);
	assert(qman_delete_cgr_safe(&ctx->cgr) == expected);
	assert(linked(owner_cpu, &ctx->cgr));
	inject_delete_failure(failure);
	dpaa_cgr_release(&f.parent, &f.priv.cgr_data.ctx);
	assert(!f.priv.cgr_data.ctx && !ctx->priv && !ctx->cgr.cb);
	assert(linked(owner_cpu, &ctx->cgr) && allocated_ids[id]);
	assert(!release_calls && error_logs);
	dispatch(&qman_affine_portal[owner_cpu], 1);
	assert(!f.netdev.stops);
	assert(!qman_alloc_cgrid(&other) && other != id);
	assert(!qman_release_cgrid(other));
	cleanup_quarantine(owner_cpu, ctx);
	assert(ctx_allocs == ctx_frees && !references && !preempt_count);
}

static void partial_creation_and_id_retention(void)
{
	struct fixture f;
	struct dpaa_cgr_context *ctx;
	u32 id;

	reset();
	setup(&f);
	query_fail_at = 2;
	assert(!dpaa_eth_cgr_init(&f.priv));
	ctx = f.priv.cgr_data.ctx;
	assert(linked(0, &ctx->cgr));
	query_fail_at = query_calls + 1;
	dpaa_cgr_release(&f.parent, &f.priv.cgr_data.ctx);
	assert(!f.priv.cgr_data.ctx && !ctx->cgr.cb && !ctx->priv);
	assert(allocated_ids[ctx->cgr.cgrid] && !release_calls);
	cleanup_quarantine(0, ctx);

	reset();
	setup(&f);
	assert(!dpaa_ingress_cgr_init(&f.priv));
	id = f.priv.ingress_cgr->cgr.cgrid;
	dpaa_cgr_release(&f.parent, &f.priv.ingress_cgr);
	assert(!f.priv.ingress_cgr && allocated_ids[id] && !release_calls);
	assert(ctx_allocs == ctx_frees && error_logs);
	assert(qman_affine_portal[0].cgr_cbs.next ==
	       &qman_affine_portal[0].cgr_cbs);
}

static void *dispatch_thread(void *arg)
{
	dispatch(arg, 1);
	return NULL;
}

static void *disable_thread(void *arg)
{
	dpaa_cgr_deactivate(arg);
	assert(!pthread_mutex_lock(&gate_lock));
	disable_done = true;
	assert(!pthread_cond_broadcast(&gate_cond));
	assert(!pthread_mutex_unlock(&gate_lock));
	return NULL;
}

static void concurrent_callback(void)
{
	struct fixture *f;
	struct dpaa_cgr_context *ctx, *owner;
	struct device dev = {0};
	pthread_t callback, disable;

	reset();
	f = calloc(1, sizeof(*f));
	assert(f);
	setup(f);
	assert(!dpaa_eth_cgr_init(&f->priv));
	ctx = f->priv.cgr_data.ctx;
	block_callback = true;
	assert(!pthread_create(&callback, NULL, dispatch_thread,
			       &qman_affine_portal[0]));
	assert(!pthread_mutex_lock(&gate_lock));
	while (!callback_entered)
		assert(!pthread_cond_wait(&gate_cond, &gate_lock));
	monitor_disable = true;
	assert(!pthread_create(&disable, NULL, disable_thread, ctx));
	while (!disable_attempted)
		assert(!pthread_cond_wait(&gate_cond, &gate_lock));
	assert(!disable_done && ctx->priv == &f->priv);
	release_callback = true;
	assert(!pthread_cond_broadcast(&gate_cond));
	assert(!pthread_mutex_unlock(&gate_lock));
	assert(!pthread_join(callback, NULL));
	assert(!pthread_join(disable, NULL));
	assert(disable_done && !ctx->priv && !ctx->cgr.cb);
	monitor_disable = false;

	owner = ctx;
	free(f);
	dispatch(&qman_affine_portal[0], 1);
	query_fail_at = query_calls + 1;
	dpaa_cgr_release(&dev, &owner);
	assert(!owner && !ctx->priv && !ctx->cgr.cb);
	dispatch(&qman_affine_portal[0], 1);
	cleanup_quarantine(0, ctx);
	assert(ctx_allocs == ctx_frees && !references && !preempt_count);
}

int main(void)
{
	for (int cpu = 0; cpu < 2; cpu++)
		assert(!pthread_mutex_init(&qman_affine_portal[cpu].cgr_lock, NULL));
	successful_cleanup();
	failed_allocations();
	failed_creation(false, false);
	failed_creation(false, true);
	failed_creation(true, false);
	failed_creation(true, true);
	failed_deletion(0, QUERY_FAILURE);
	failed_deletion(0, MODIFY_FAILURE);
	failed_deletion(1, QUERY_FAILURE);
	failed_deletion(1, MODIFY_FAILURE);
	failed_deletion(1, OFFLINE_FAILURE);
	partial_creation_and_id_retention();
	concurrent_callback();
	for (int cpu = 0; cpu < 2; cpu++)
		assert(!pthread_mutex_destroy(&qman_affine_portal[cpu].cgr_lock));
	puts("CGR containment: cleanup, faults, offline dispatch and callback synchronization passed");
	return 0;
}
