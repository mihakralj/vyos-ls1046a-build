// SPDX-License-Identifier: GPL-2.0
/* Host fault model for the extracted DPAA offload-FQ and remove paths. */
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int netdevice_tracker;

struct list_head { struct list_head *next, *prev; };
struct device { struct device *parent; void *drvdata; };
struct platform_device { struct device dev; };
struct qman_fq { u32 fqid, idx; bool late_scheduled; };
struct net_device_ops { int unused; };
struct dpaa_priv;
struct dpaa_pcd_ops;
struct net_device {
	struct device dev;
	const struct net_device_ops *netdev_ops;
	struct dpaa_priv *priv;
	int reg_state, refs;
};
struct cgr { u32 cgrid; };
struct dpaa_cgr_context { struct cgr cgr; };
struct mac_device { void *port[2]; void *phylink; };
struct dpaa_priv {
	struct net_device *net_dev;
	struct mac_device *mac_dev;
	struct list_head dpaa_fq_list;
#ifdef CGR_CONTAINMENT
	struct dpaa_cgr_context egress_ctx, ingress_ctx;
	struct { struct dpaa_cgr_context *ctx; } cgr_data;
	struct dpaa_cgr_context *ingress_cgr;
#else
	struct { struct cgr cgr; } cgr_data;
	struct cgr ingress_cgr;
#endif
	struct qman_fq **conf_fqs;
	unsigned int tx_headroom;
	bool use_ingress_cgr;
	const struct dpaa_pcd_ops *pcd_ops;
};
struct dpaa_fq {
	struct qman_fq fq_base;
	struct list_head list;
	struct net_device *net_dev;
	bool init;
	u32 fqid, flags;
	u16 channel;
	u8 wq;
	int fq_type, xdp_rxq;
};
struct dpaa_pcd_ops { void (*teardown)(struct dpaa_priv *); };
struct dpaa_fq_cbs { struct qman_fq egress_ern; };
struct sk_buff { int unused; };
struct qm_mcc_initfq {
	u16 we_mask;
	struct {
		u16 fq_ctrl;
		u8 cgid;
		struct { struct { u8 exclusive; } stashing; } context_a;
	} fqd;
};

enum {
	FQ_TYPE_RX_DEFAULT = 1, FQ_TYPE_RX_ERROR, FQ_TYPE_RX_PCD, FQ_TYPE_TX,
	FQ_TYPE_TX_CONFIRM, FQ_TYPE_TX_CONF_MQ, FQ_TYPE_TX_ERROR,
	FQ_TYPE_TX_NO_CONFIRM
};
enum { NETREG_REGISTERED, NETREG_UNREGISTERING, NETREG_UNREGISTERED };
#define TX 1
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define QMAN_FQ_FLAG_DYNAMIC_FQID 1
#define QMAN_FQ_FLAG_NO_MODIFY 2
#define QMAN_FQ_FLAG_NO_ENQUEUE 4
#define QMAN_FQ_FLAG_TO_DCPORTAL 8
#define QMAN_INITFQ_FLAG_SCHED 1
#define QM_INITFQ_WE_FQCTRL 1
#define QM_INITFQ_WE_DESTWQ 2
#define QM_INITFQ_WE_CGID 4
#define QM_INITFQ_WE_OAC 8
#define QM_INITFQ_WE_TDTHRESH 16
#define QM_INITFQ_WE_CONTEXTA 32
#define QM_FQCTRL_PREFERINCACHE 1
#define QM_FQCTRL_AVOIDBLOCK 2
#define QM_FQCTRL_CGE 4
#define QM_FQCTRL_TDE 8
#define QM_FQCTRL_HOLDACTIVE 16
#define QM_FQCTRL_CTXASTASHING 32
#define QM_OAC_CG 1
#define QM_STASHING_EXCL_DATA 1
#define QM_STASHING_EXCL_CTX 2
#define QM_STASHING_EXCL_ANNOTATION 4
#define DPAA_FQ_TD 0x400000
#define FSL_QMAN_MAX_OAL 127
#define MEM_TYPE_PAGE_ORDER0 1
#define cpu_to_be16(x) (x)
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define rcu_dereference_protected(p, c) (p)
#define lockdep_is_held(p) true
#define ERR_PTR(err) ((void *)(intptr_t)(err))
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

static const struct net_device_ops dpaa_ops;
static const struct net_device_ops other_ops;
static const struct dpaa_fq_cbs dpaa_fq_cbs;
static struct qman_fq *fq_table[32];
static int create_error, init_error, creates, inits, destroys, frees;
static int managed_allocations, holds, errors, cleanup_calls, pcd_teardowns;
static bool rtnl_held, expect_rtnl = true, expect_taildrop = true;
static jmp_buf unregister_wait;

static void init_list(struct list_head *head) { head->next = head->prev = head; }
static void list_add_tail(struct list_head *item, struct list_head *head)
{
	item->prev = head->prev;
	item->next = head;
	head->prev->next = item;
	head->prev = item;
}
static void list_del(struct list_head *item)
{
	item->prev->next = item->next;
	item->next->prev = item->prev;
}
static void rtnl_lock(void) { assert(!rtnl_held); rtnl_held = true; }
static void rtnl_unlock(void) { assert(rtnl_held); rtnl_held = false; }
static struct dpaa_priv *netdev_priv(struct net_device *dev) { return dev->priv; }
static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{
	managed_allocations++;
	return calloc(1, size);
}
static void devm_kfree(struct device *dev, void *ptr) { frees++; free(ptr); }
static void netdev_hold(struct net_device *dev, netdevice_tracker *tracker, int flags)
{
	assert(rtnl_held);
	assert(tracker);
	dev->refs++;
	holds++;
}
static void dev_err(struct device *dev, const char *format, ...) { errors++; }
static void netdev_err(struct net_device *dev, const char *format, ...) { errors++; }
static void netdev_info(struct net_device *dev, const char *format, ...) {}
static int qman_create_fq(u32 fqid, u32 flags, struct qman_fq *fq)
{
	assert(rtnl_held || !expect_rtnl);
	creates++;
	if (create_error)
		return create_error;
	fq->fqid = 7;
	assert((flags & QMAN_FQ_FLAG_TO_DCPORTAL) &&
	       (flags & QMAN_FQ_FLAG_DYNAMIC_FQID));
	fq->idx = fq->fqid * 2;
	assert(!fq_table[fq->idx]);
	fq_table[fq->idx] = fq;
	return 0;
}
static int qman_init_fq(struct qman_fq *fq, u32 flags, struct qm_mcc_initfq *opts)
{
	assert(rtnl_held || !expect_rtnl);
	inits++;
	assert(fq_table[fq->idx] == fq);
	if (expect_taildrop) {
		assert(opts->we_mask & QM_INITFQ_WE_TDTHRESH);
		assert(!(opts->we_mask & QM_INITFQ_WE_OAC));
	} else {
		assert(!(opts->we_mask & QM_INITFQ_WE_TDTHRESH));
		assert(opts->we_mask & QM_INITFQ_WE_OAC);
	}
	return init_error;
}
static void qman_destroy_fq(struct qman_fq *fq)
{
	destroys++;
	fq_table[fq->idx] = NULL;
}
static void complete_init_late(u32 fqid)
{
	struct qman_fq *fq = fq_table[fqid * 2];

	assert(fq && fq->fqid == fqid);
	fq->late_scheduled = true;
}
static u32 qman_fq_fqid(struct qman_fq *fq) { return fq->fqid; }
static void dpaa_setup_egress(struct dpaa_priv *priv, struct dpaa_fq *fq,
			      void *port, const struct qman_fq *cb)
{
	assert(port);
	fq->net_dev = priv->net_dev;
	fq->flags = QMAN_FQ_FLAG_TO_DCPORTAL;
	fq->channel = 0x801;
}
static int dpaa_tx_fq_to_id(const struct dpaa_priv *priv, struct qman_fq *fq)
{
	return -1;
}
static void qm_fqd_set_destwq(void *fqd, u16 channel, u8 wq) {}
static void qm_fqd_set_oac(void *fqd, int value) {}
static void qm_fqd_set_oal(void *fqd, size_t value) {}
static void qm_fqd_set_taildrop(void *fqd, u32 threshold, int rounded) {}
static void qm_fqd_context_a_set64(void *fqd, uint64_t value) {}
static void qm_fqd_set_stashing(void *fqd, int a, int b, int c) {}
static int xdp_rxq_info_reg(int *info, struct net_device *dev, int queue, int napi)
{
	return 0;
}
static int xdp_rxq_info_reg_mem_model(int *info, int model, void *allocator)
{
	return 0;
}
static void xdp_rxq_info_unreg(int *info) {}

static void *dev_get_drvdata(struct device *dev) { return dev->drvdata; }
static void dev_set_drvdata(struct device *dev, void *data) { dev->drvdata = data; }
static void dpaa_eth_sysfs_remove(struct device *dev) {}
static void unregister_netdev(struct net_device *dev)
{
	rtnl_lock();
	dev->reg_state = NETREG_UNREGISTERED;
	rtnl_unlock();
	if (dev->refs > 1)
		longjmp(unregister_wait, 1);
}
static void phylink_destroy(void *link) { cleanup_calls++; }
static void dpaa_cls_flush(struct net_device *dev) { cleanup_calls++; }
static int dpaa_fq_free(struct device *dev, struct list_head *list)
{
	cleanup_calls++;
	return 0;
}
static void qman_delete_cgr_safe(struct cgr *cgr) { cleanup_calls++; }
static void qman_release_cgrid(u32 cgrid) { cleanup_calls++; }
static void dpaa_cgr_deactivate(struct dpaa_cgr_context *ctx) {}
static void dpaa_cgr_release(struct device *dev, struct dpaa_cgr_context **ctx)
{
	cleanup_calls++;
}
static void dpaa_napi_del(struct net_device *dev) { cleanup_calls++; }
static void dpaa_priv_detach_flavor_ops(struct dpaa_priv *priv) { cleanup_calls++; }
static void dpaa_bps_free(struct dpaa_priv *priv) { cleanup_calls++; }
static void free_netdev(struct net_device *dev) { cleanup_calls++; }
static void pcd_teardown(struct dpaa_priv *priv) { pcd_teardowns++; }
static const struct dpaa_pcd_ops pcd_ops = { .teardown = pcd_teardown };

#include "dpaa_offload_fq_body.h"

static void setup(struct net_device *dev, struct dpaa_priv *priv,
		  struct mac_device *mac, struct device *parent)
{
	memset(dev, 0, sizeof(*dev));
	memset(priv, 0, sizeof(*priv));
	memset(mac, 0, sizeof(*mac));
	mac->port[TX] = mac;
	dev->dev.parent = parent;
	dev->netdev_ops = &dpaa_ops;
	dev->priv = priv;
	dev->reg_state = NETREG_REGISTERED;
	dev->refs = 1;
	priv->net_dev = dev;
	priv->mac_dev = mac;
#ifdef CGR_CONTAINMENT
	priv->cgr_data.ctx = &priv->egress_ctx;
	priv->ingress_cgr = &priv->ingress_ctx;
#endif
	init_list(&priv->dpaa_fq_list);
}

int main(int argc, char **argv)
{
	struct device parent = { 0 };
	struct platform_device child = { 0 };
	struct net_device dev, other;
	struct dpaa_priv priv, other_priv;
	struct mac_device mac, other_mac;
	struct dpaa_fq *fq;
	u32 fqid = 0xdeadbeef;
	int rc;

	assert(argc == 2);
	setup(&dev, &priv, &mac, &parent);
	setup(&other, &other_priv, &other_mac, &parent);

	if (!strcmp(argv[1], "success")) {
		assert(dpaa_alloc_offload_tx_fq(&dev, &fqid) == 0);
		assert(fqid == 7 && creates == 1 && inits == 1 && !destroys);
		fq = container_of(fq_table[14], struct dpaa_fq, fq_base);
		assert(fq->net_dev == &dev && fq->fq_type == FQ_TYPE_TX_NO_CONFIRM);
		assert(fq->init && fq->wq == 6 && fq->channel == 0x801 && dev.refs == 1);
		assert(!holds && !frees && managed_allocations == 1);
		puts("successful offload FQ remains usable");
		return 0;
	}

	if (!strcmp(argv[1], "create-error")) {
		create_error = -ENOSPC;
		assert(dpaa_alloc_offload_tx_fq(&dev, &fqid) == -ENOSPC);
		assert(fqid == 0xdeadbeef && frees == 1 && !fq_table[14]);
		assert(!destroys && !inits && !holds && dev.refs == 1);
		create_error = 0;
		assert(dpaa_alloc_offload_tx_fq(&other, &fqid) == 0 && fqid == 7);
		puts("ENOSPC create failure is cleaned up and retryable");
		return 0;
	}

	if (!strcmp(argv[1], "unregistered")) {
		assert(dpaa_alloc_offload_tx_fq(NULL, &fqid) == -EINVAL);
		assert(dpaa_alloc_offload_tx_fq(&dev, NULL) == -EINVAL);
		dev.netdev_ops = &other_ops;
		assert(dpaa_alloc_offload_tx_fq(&dev, &fqid) == -ENODEV);
		dev.netdev_ops = &dpaa_ops;
		dev.reg_state = NETREG_UNREGISTERING;
		assert(dpaa_alloc_offload_tx_fq(&dev, &fqid) == -ENODEV);
		dev.reg_state = NETREG_UNREGISTERED;
		assert(dpaa_alloc_offload_tx_fq(&dev, &fqid) == -ENODEV);
		assert(!creates && !inits && !managed_allocations && !holds);
		puts("invalid and unregistering netdevs rejected");
		return 0;
	}

	if (!strcmp(argv[1], "probe-error")) {
		struct dpaa_fq probe_fq = { .fq_type = FQ_TYPE_TX };

		dpaa_setup_egress(&priv, &probe_fq, mac.port[TX],
				  &dpaa_fq_cbs.egress_ern);
		expect_rtnl = false;
		expect_taildrop = false;
		init_error = -EIO;
		rc = dpaa_fq_init(&probe_fq, false, NULL);
		assert(rc == -EIO && creates == 1 && inits == 1 && destroys == 1);
		assert(!fq_table[14] && !holds && dev.refs == 1);
		puts("generic probe error handling unchanged; its safety remains open");
		return 0;
	}

	init_error = !strcmp(argv[1], "timeout") ? -ETIMEDOUT : -EIO;
	assert(!strcmp(argv[1], "timeout") || !strcmp(argv[1], "init-error"));
	rc = dpaa_alloc_offload_tx_fq(&dev, &fqid);
	assert(rc == init_error && fqid == 0xdeadbeef);
	assert(creates == 1 && inits == 1 && !destroys && !frees);
	assert(fq_table[14] && dev.refs == 2 && holds == 1 && errors);
	fq = container_of(fq_table[14], struct dpaa_fq, fq_base);
	assert(fq->net_dev == &dev && fq->list.next == &priv.dpaa_fq_list);

	/* A late INITFQ completion must still find its original owner. */
	complete_init_late(7);
	assert(fq->fq_base.late_scheduled && fq_table[14] == &fq->fq_base);
	assert(dpaa_alloc_offload_tx_fq(&other, &fqid) == -ESHUTDOWN);
	assert(creates == 1 && inits == 1 && !destroys && !frees);

	/* Model the net core waiting on the retained reference during remove. */
	priv.pcd_ops = &pcd_ops;
	child.dev.drvdata = &dev;
	if (!setjmp(unregister_wait)) {
		dpaa_remove(&child);
		assert(!"unregister should wait for the retained netdev reference");
	}
	assert(!cleanup_calls && !frees && fq_table[14] == &fq->fq_base);
	assert(pcd_teardowns == 1 && !child.dev.drvdata);
	assert(dev.refs == 2 && fq->net_dev->priv == &priv);
	puts("failed INITFQ retains its FQ and blocks further allocations");
	return 0;
}
