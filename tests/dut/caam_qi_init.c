// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted CAAM QI probe and devres cleanup. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define NR_CPUS 2
#define GFP_KERNEL 0
#define CAAM_QI_MEMCACHE_SIZE 256
#define CAAM_NAPI_WEIGHT 63
#define dev_err(dev, ...) ((void)(dev))
#define dev_info(dev, ...) ((void)(dev))

typedef uint32_t u32;
typedef struct cpumask { unsigned int bits; } cpumask_t;
typedef cpumask_t *cpumask_var_t;
struct device { void *drvdata; };
struct platform_device { struct device dev; };
struct caam_drv_private { int unused; };
struct qman_cgr { u32 cgrid; };
struct caam_qi_priv { struct qman_cgr cgr; };
struct qman_fq { bool released; };
struct napi_struct { bool added, enabled; };
struct caam_napi { struct napi_struct irqtask; };
struct net_device { struct device dev; bool released; };
struct kmem_cache { bool released; };
struct caam_qi_pcpu_priv {
	struct caam_napi caam_napi;
	struct qman_fq *rsp_fq;
	struct net_device *net_dev;
};

static struct caam_qi_priv qipriv;
static struct caam_qi_pcpu_priv pcpu_qipriv, pcpu_data[NR_CPUS];
static struct kmem_cache cache, *qi_cache;
static struct net_device netdevs[NR_CPUS];
static struct qman_fq queues[NR_CPUS];
static struct caam_drv_private controller;
static struct platform_device pdev = { .dev = { .drvdata = &controller } };
static const cpumask_t affine_cpus = { .bits = (1U << NR_CPUS) - 1 };
static cpumask_t clean_cpus;
static bool cgr_deleted, cgrid_released, fail_registration;
static unsigned int allocated_netdevs, duplicate_releases, mask_frees;
static void (*registered_action)(void *);
static void *registered_data;

#define per_cpu_ptr(ptr, cpu) \
	((__typeof__(ptr))((char *)&pcpu_data[(cpu)] + \
	 ((char *)(ptr) - (char *)&pcpu_qipriv)))
#define per_cpu(value, cpu) (*per_cpu_ptr(&(value), (cpu)))
#define for_each_cpu(cpu, mask) \
	for ((cpu) = 0; (cpu) < NR_CPUS; (cpu)++) \
		if ((mask)->bits & (1U << (cpu)))
#define devm_add_action_or_reset(dev, action, data) \
	__devm_add_action_or_reset(dev, action, data, #action)

static void release_once(bool *released)
{
	if (*released)
		duplicate_releases++;
	*released = true;
}

static const cpumask_t *qman_affine_cpus(void) { return &affine_cpus; }
static bool zalloc_cpumask_var(cpumask_var_t *mask, int flags)
{
	clean_cpus.bits = 0;
	*mask = &clean_cpus;
	return true;
}
static void free_cpumask_var(cpumask_var_t mask)
{
	assert(mask == &clean_cpus);
	mask_frees++;
}
static void cpumask_set_cpu(int cpu, cpumask_t *mask) { mask->bits |= 1U << cpu; }
static void *dev_get_drvdata(struct device *dev) { return dev->drvdata; }
static int init_cgr(struct device *dev) { qipriv.cgr.cgrid = 7; return 0; }

static int alloc_rsp_fqs(struct device *dev)
{
	for (int i = 0; i < NR_CPUS; i++)
		pcpu_data[i].rsp_fq = &queues[i];
	return 0;
}

static void kfree(void *ptr)
{
	struct qman_fq *fq = ptr;

	assert(fq == &queues[0] || fq == &queues[1]);
	release_once(&fq->released);
}

static int kill_fq(struct device *dev, struct qman_fq *fq)
{
	assert(dev == &pdev.dev);
	kfree(fq);
	return 0;
}

static struct net_device *alloc_netdev_dummy(int size)
{
	assert(!size && allocated_netdevs < NR_CPUS);
	return &netdevs[allocated_netdevs++];
}

static void free_netdev(struct net_device *dev)
{
	assert(dev == &netdevs[0] || dev == &netdevs[1]);
	release_once(&dev->released);
}

static int caam_qi_poll(struct napi_struct *napi, int budget) { return 0; }
static void netif_napi_add_tx_weight(struct net_device *dev,
				     struct napi_struct *napi,
				     int (*poll)(struct napi_struct *, int),
				     int weight)
{
	assert(poll == caam_qi_poll && weight == CAAM_NAPI_WEIGHT);
	napi->added = true;
}
static void napi_enable(struct napi_struct *napi)
{
	assert(napi->added && !napi->enabled);
	napi->enabled = true;
}
static void napi_disable(struct napi_struct *napi)
{
	assert(napi->added && napi->enabled);
	napi->enabled = false;
}
static void netif_napi_del(struct napi_struct *napi)
{
	assert(napi->added && !napi->enabled);
	napi->added = false;
}

static unsigned int dma_get_cache_alignment(void) { return 64; }
static struct kmem_cache *kmem_cache_create(const char *name, size_t size,
					    unsigned int align, unsigned int flags,
					    void (*ctor)(void *))
{
	assert(!strcmp(name, "caamqicache") && size == CAAM_QI_MEMCACHE_SIZE);
	return &cache;
}
static void kmem_cache_destroy(struct kmem_cache *value)
{
	assert(value == &cache);
	release_once(&value->released);
}
static void caam_debugfs_qi_init(struct caam_drv_private *priv)
{
	assert(priv == &controller);
}
static int qman_delete_cgr_safe(struct qman_cgr *cgr)
{
	assert(cgr == &qipriv.cgr);
	release_once(&cgr_deleted);
	return 0;
}
static int qman_release_cgrid(u32 cgrid)
{
	assert(cgrid == qipriv.cgr.cgrid);
	release_once(&cgrid_released);
	return 0;
}

static int __devm_add_action(struct device *dev, void (*action)(void *),
			     void *data, const char *name)
{
	assert(dev == &pdev.dev && data == dev && !strcmp(name, "caam_qi_shutdown"));
	if (fail_registration)
		return -ENOMEM;
	registered_action = action;
	registered_data = data;
	return 0;
}

#include "caam_qi_init_body.h"

static void reset(bool fail)
{
	memset(&qipriv, 0, sizeof(qipriv));
	memset(pcpu_data, 0, sizeof(pcpu_data));
	memset(netdevs, 0, sizeof(netdevs));
	memset(queues, 0, sizeof(queues));
	memset(&cache, 0, sizeof(cache));
	qi_cache = NULL;
	cgr_deleted = cgrid_released = false;
	fail_registration = fail;
	allocated_netdevs = duplicate_releases = mask_frees = 0;
	registered_action = NULL;
	registered_data = NULL;
}

static bool fully_released(void)
{
	for (int i = 0; i < NR_CPUS; i++)
		if (!queues[i].released || !netdevs[i].released ||
		    pcpu_data[i].caam_napi.irqtask.added ||
		    pcpu_data[i].caam_napi.irqtask.enabled)
			return false;
	return cache.released && cgr_deleted && cgrid_released && mask_frees == 1;
}

int main(void)
{
	int failures = 0, ret;

	reset(false);
	ret = caam_qi_init(&pdev);
	assert(!ret && registered_action && mask_frees == 1);
	registered_action(registered_data);
	if (!fully_released() || duplicate_releases)
		failures++;

	reset(true);
	ret = caam_qi_init(&pdev);
	if (ret != -ENOMEM || registered_action || !fully_released() ||
	    duplicate_releases)
		failures++;

	printf("CAAM QI devres init cleanup: %d uncontained case(s)\n", failures);
	return failures ? 1 : 0;
}
