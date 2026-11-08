// SPDX-License-Identifier: GPL-2.0
/* Exercise ASK bringup without creating an unused global TX queue. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
struct device { int refs; };
struct device_node { int unused; };
struct platform_device { struct device dev; };
struct fman { int unused; };
struct mutex { int unused; };
struct xarray { int unused; };
struct kmem_cache { int unused; };
struct ask_hw_flow_cookie { int unused; };
struct qman_fq { u32 fqid; };
struct qm_mcc_initfq {
	unsigned int we_mask;
	struct { unsigned int fq_ctrl; } fqd;
};
struct ask_hw_pcd {
	struct mutex lock;
	struct fman *fman;
	struct xarray flow_cookies;
	struct qman_fq dedicated_fq;
	bool dedicated_fq_ready;
};

#define GFP_KERNEL 0
#define XA_FLAGS_ALLOC1 1
#define QMAN_FQ_FLAG_TO_DCPORTAL 1
#define QMAN_INITFQ_FLAG_SCHED 1
#define QM_INITFQ_WE_FQCTRL 1
#define QM_INITFQ_WE_DESTWQ 2
#define QM_FQCTRL_PREFERINCACHE 1
#define cpu_to_be16(x) (x)
#define ask_pr_dbg(...) ((void)0)
#define ask_pr_info(...) ((void)0)
#define ask_pr_warn(...) ((void)0)

static struct ask_hw_pcd *ask_hw_pcd_inst;
static struct kmem_cache *ask_hw_cookie_cache;
static struct device_node node;
static struct platform_device pdev;
static struct fman fm;
static struct kmem_cache cache;
static bool node_present = true;
static int qman_calls;

static struct device_node *of_find_compatible_node(void *from, void *type,
						   const char *compatible)
{
	return node_present ? &node : NULL;
}

static struct platform_device *of_find_device_by_node(struct device_node *np)
{
	assert(np == &node);
	pdev.dev.refs++;
	return &pdev;
}

static void of_node_put(struct device_node *np) { assert(np == &node); }
static void put_device(struct device *dev) { dev->refs--; }
static struct fman *fman_bind(struct device *dev) { dev->refs++; return &fm; }
static void *kzalloc(size_t size, int flags) { return calloc(1, size); }
static void mutex_init(struct mutex *lock) {}
static void xa_init_flags(struct xarray *xa, int flags) {}
static struct kmem_cache *kmem_cache_create(const char *name, size_t size,
					    size_t align, int flags, void *ctor)
{
	return &cache;
}

static int qman_alloc_fqid(u32 *fqid) { qman_calls++; return -ENOSPC; }
static int qman_create_fq(u32 fqid, u32 flags, struct qman_fq *fq) { return 0; }
static int qman_init_fq(struct qman_fq *fq, u32 flags, struct qm_mcc_initfq *opts)
{
	return 0;
}
static void qman_destroy_fq(struct qman_fq *fq) {}
static int qman_release_fqid(u32 fqid) { return 0; }
static void qm_fqd_set_destwq(void *fqd, int channel, int wq) {}

#include "ask_hw_bringup_body.h"

int main(void)
{
	assert(ask_hw_pcd_bringup() == 0);
	assert(ask_hw_pcd_inst && ask_hw_pcd_inst->fman == &fm);
	assert(pdev.dev.refs == 1 && ask_hw_cookie_cache == &cache);
	assert(qman_calls == 0);
	assert(ask_hw_pcd_bringup() == 0);
	assert(pdev.dev.refs == 1 && qman_calls == 0);
	free(ask_hw_pcd_inst);
	ask_hw_pcd_inst = NULL;
	pdev.dev.refs = 0;
	node_present = false;
	assert(ask_hw_pcd_bringup() == 0);
	assert(!ask_hw_pcd_inst && !pdev.dev.refs && qman_calls == 0);
	return 0;
}
