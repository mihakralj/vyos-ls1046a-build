// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * fsl_usdpaa_mainline.c - Userspace DPAA driver for DPDK on mainline kernel
 *
 * Provides /dev/fsl-usdpaa and /dev/fsl-usdpaa-irq character devices
 * implementing the NXP USDPAA ioctl ABI for DPDK's DPAA1 PMD.
 * Maps DPDK ioctls to mainline BMan/QMan APIs instead of requiring
 * the NXP SDK kernel.
 *
 * Copyright (C) 2026 Mono Gateway Project
 * Author: Beast (agentic rewrite of NXP fsl_usdpaa.c + fsl_usdpaa_irq.c)
 *
 * Based on ioctl ABI from NXP fsl_usdpaa.h:
 *   Copyright (C) 2008-2012 Freescale Semiconductor, Inc.
 *
 * Portal mapping strategy (mainline-safe):
 *   PORTAL_MAP ioctl returns physical addresses in addr.{cena, cinh}.
 *   DPDK then calls mmap() on the usdpaa fd with pgoff = phys >> PAGE_SHIFT.
 *   usdpaa_mmap() handler identifies CE vs CI windows by physical address
 *   and applies correct page protection attributes:
 *     CE: Write-Back Non-Shareable (for QBMan stashing protocol)
 *     CI: Strongly-Ordered / Device-nGnRnE (for portal doorbells)
 */

#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/genalloc.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/eventfd.h>
#include <linux/interrupt.h>
#include <linux/mutex.h>
#include <linux/list.h>
#include <linux/io.h>
#include <linux/netdevice.h>
#include <linux/ethtool.h>
#include <linux/rtnetlink.h>
#include <linux/poll.h>
#include <linux/wait.h>
#include <linux/atomic.h>
#include <net/net_namespace.h>

#include <soc/fsl/bman.h>
#include <soc/fsl/qman.h>

/* Internal headers for portal reservation + BPID allocator */
#include "bman_priv.h"
#include "qman_priv.h"

/* DPAA_GENALLOC_OFF already defined via bman_priv.h -> dpaa_sys.h */

/* ====================================================================
 * USDPAA ioctl ABI definitions
 * Must be binary-compatible with DPDK 24.11 process.h / process.c
 *
 * CRITICAL: Struct layouts must match DPDK exactly - the _IOW/_IOWR
 * macros encode sizeof(struct) into the ioctl number. Different sizes
 * produce different ioctl command numbers = ENOTTY.
 * ==================================================================== */

#define USDPAA_IOCTL_MAGIC 'u'

/* Resource ID types - matches DPDK enum dpaa_id_type */
enum usdpaa_id_type {
	usdpaa_id_fqid,
	usdpaa_id_bpid,
	usdpaa_id_qpool,
	usdpaa_id_cgrid,
	usdpaa_id_ceetm0_lfqid,
	usdpaa_id_ceetm1_lfqid,
	usdpaa_id_ceetm0_channelid,
	usdpaa_id_ceetm1_channelid,
	usdpaa_id_max,
};

/* Portal types - matches DPDK enum dpaa_portal_type */
enum usdpaa_portal_type {
	usdpaa_portal_qman,
	usdpaa_portal_bman,
};

/* ioctl structures - ID allocation */
struct usdpaa_ioctl_id_alloc {
	uint32_t base;              /* Return value */
	enum usdpaa_id_type id_type;
	uint32_t num;
	uint32_t align;
	int partial;                /* DPDK has this field */
};

struct usdpaa_ioctl_id_release {
	enum usdpaa_id_type id_type;
	uint32_t base;
	uint32_t num;
};

struct usdpaa_ioctl_id_reserve {
	enum usdpaa_id_type id_type;
	uint32_t base;
	uint32_t num;
};

/*
 * Portal map structures - MUST match DPDK process.h exactly
 *
 * DPDK defines:
 *   struct dpaa_portal_map { void *cinh; void *cena; };
 *   struct dpaa_ioctl_portal_map {
 *       enum dpaa_portal_type type;
 *       uint32_t index;
 *       struct dpaa_portal_map addr;
 *       u16 channel;
 *       uint32_t pools;
 *   };
 *
 * The void* fields carry PHYSICAL addresses from kernel to DPDK.
 * DPDK then calls mmap() on the usdpaa fd to get virtual addresses.
 */
struct usdpaa_portal_map {
	void *cinh;
	void *cena;
};

struct usdpaa_ioctl_portal_map {
	enum usdpaa_portal_type type;
	uint32_t index;
	struct usdpaa_portal_map addr;
	uint16_t channel;
	uint32_t pools;
};

/*
 * IRQ map structure - MUST match DPDK process.h
 *
 * DPDK defines:
 *   struct dpaa_ioctl_irq_map {
 *       enum dpaa_portal_type type;
 *       int fd;
 *       void *portal_cinh;
 *   };
 *
 * portal_cinh carries the PHYSICAL CI address (since PORTAL_MAP
 * returns physical addresses on mainline).
 */
struct usdpaa_ioctl_irq_map {
	enum usdpaa_portal_type type;
	int fd;
	void *portal_cinh;
};

struct usdpaa_ioctl_dma_map {
	uint64_t phys_addr; /* output */
	uint64_t len;
	unsigned int flags;
	uint64_t has_locking;
	uint64_t did_alloc;
};

struct usdpaa_ioctl_dma_used {
	uint64_t free_bytes;
	uint64_t total_bytes;
};

struct usdpaa_ioctl_raw_portal {
	/* inputs */
	enum usdpaa_portal_type type;
	uint8_t enable_stash;
	uint32_t cpu;
	uint32_t cache;
	uint32_t window;
	uint8_t sdest;
	uint32_t index;
	/* outputs */
	uint64_t cinh;
	uint64_t cena;
};

struct usdpaa_ioctl_link_status {
	char if_name[16];
};

struct usdpaa_ioctl_link_status_args {
	char if_name[16];
	int link_status;
	int link_speed;
	int link_duplex;
	int link_autoneg;
};

struct usdpaa_ioctl_update_link_status {
	char if_name[16];
	int link_status;
};

struct usdpaa_ioctl_update_link_speed {
	char if_name[16];
	int link_speed;
	int link_duplex;
};

/* ioctl numbers - must match DPDK process.c exactly */
#define USDPAA_IOCTL_ID_ALLOC \
	_IOWR(USDPAA_IOCTL_MAGIC, 0x01, struct usdpaa_ioctl_id_alloc)
#define USDPAA_IOCTL_ID_RELEASE \
	_IOW(USDPAA_IOCTL_MAGIC, 0x02, struct usdpaa_ioctl_id_release)
#define USDPAA_IOCTL_DMA_MAP \
	_IOWR(USDPAA_IOCTL_MAGIC, 0x03, struct usdpaa_ioctl_dma_map)
#define USDPAA_IOCTL_DMA_UNMAP \
	_IOW(USDPAA_IOCTL_MAGIC, 0x04, uint64_t)
#define USDPAA_IOCTL_DMA_LOCK \
	_IOW(USDPAA_IOCTL_MAGIC, 0x05, uint8_t)
#define USDPAA_IOCTL_DMA_UNLOCK \
	_IOW(USDPAA_IOCTL_MAGIC, 0x06, uint8_t)
#define USDPAA_IOCTL_PORTAL_MAP \
	_IOWR(USDPAA_IOCTL_MAGIC, 0x07, struct usdpaa_ioctl_portal_map)
#define USDPAA_IOCTL_PORTAL_UNMAP \
	_IOW(USDPAA_IOCTL_MAGIC, 0x08, struct usdpaa_portal_map)
#define USDPAA_IOCTL_PORTAL_IRQ_MAP \
	_IOW(USDPAA_IOCTL_MAGIC, 0x09, struct usdpaa_ioctl_irq_map)
#define USDPAA_IOCTL_ID_RESERVE \
	_IOW(USDPAA_IOCTL_MAGIC, 0x0A, struct usdpaa_ioctl_id_reserve)
#define USDPAA_IOCTL_DMA_USED \
	_IOR(USDPAA_IOCTL_MAGIC, 0x0B, struct usdpaa_ioctl_dma_used)
#define USDPAA_IOCTL_ALLOC_RAW_PORTAL \
	_IOWR(USDPAA_IOCTL_MAGIC, 0x0C, struct usdpaa_ioctl_raw_portal)
#define USDPAA_IOCTL_FREE_RAW_PORTAL \
	_IOR(USDPAA_IOCTL_MAGIC, 0x0D, struct usdpaa_ioctl_raw_portal)
#define USDPAA_IOCTL_ENABLE_LINK_STATUS_INTERRUPT \
	_IOW(USDPAA_IOCTL_MAGIC, 0x0E, struct usdpaa_ioctl_link_status)
#define USDPAA_IOCTL_DISABLE_LINK_STATUS_INTERRUPT \
	_IOW(USDPAA_IOCTL_MAGIC, 0x0F, char[16])
#define USDPAA_IOCTL_GET_LINK_STATUS \
	_IOWR(USDPAA_IOCTL_MAGIC, 0x10, struct usdpaa_ioctl_link_status_args)
#define USDPAA_IOCTL_UPDATE_LINK_STATUS \
	_IOW(USDPAA_IOCTL_MAGIC, 0x11, struct usdpaa_ioctl_update_link_status)
#define USDPAA_IOCTL_UPDATE_LINK_SPEED \
	_IOW(USDPAA_IOCTL_MAGIC, 0x12, struct usdpaa_ioctl_update_link_speed)
#define USDPAA_IOCTL_RESTART_LINK_AUTONEG \
	_IOW(USDPAA_IOCTL_MAGIC, 0x13, char[16])
#define USDPAA_IOCTL_GET_IOCTL_VERSION \
	_IOR(USDPAA_IOCTL_MAGIC, 0x14, int)

#define USDPAA_IOCTL_VERSION 2

/* Portal Interrupt Inhibit Register offset from CI base */
#define USDPAA_PORTAL_IIR_OFFSET 0xE0C

/* ====================================================================
 * Per-FD tracking structures
 * ==================================================================== */

struct resource_range {
	struct list_head node;
	enum usdpaa_id_type type;
	u32 base;
	u32 count;
	bool reserved;  /* true = tracking-only (ID_RESERVE), no gen_pool free */
};

/*
 * Portal reservation - tracks reserved portals per FD.
 * phys_ce/ci are physical addresses for mmap matching.
 * No userspace VAs stored - DPDK does its own mmap().
 */
struct portal_reservation {
	struct list_head node;        /* per-FD list */
	struct list_head global_node; /* global list for IRQ lookup */
	enum usdpaa_portal_type type;
	union {
		struct bm_portal_config *bpcfg;
		struct qm_portal_config *qpcfg;
	};
	resource_size_t phys_ce, phys_ci;
	size_t size_ce, size_ci;
};

struct dma_mapping {
	struct list_head node;
	phys_addr_t phys;
	size_t len;
};

struct usdpaa_ctx {
	struct list_head alloc_resources;
	struct list_head portal_maps;
	struct list_head dma_maps;
	struct mutex lock;
};

/* ====================================================================
 * Global portal tracking list — shared between usdpaa + usdpaa-irq
 * The IRQ device looks up portal configs by physical CI address.
 * ==================================================================== */

static LIST_HEAD(global_portal_list);
static DEFINE_MUTEX(global_portal_lock);

/* ====================================================================
 * DMA memory pool (from reserved-memory DT node)
 * ==================================================================== */

static struct gen_pool *usdpaa_mem_pool;
static phys_addr_t usdpaa_mem_phys;
static size_t usdpaa_mem_size;

/* ====================================================================
 * /dev/fsl-usdpaa: File operations
 * ==================================================================== */

static int usdpaa_open(struct inode *inode, struct file *filp)
{
	struct usdpaa_ctx *ctx;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	INIT_LIST_HEAD(&ctx->alloc_resources);
	INIT_LIST_HEAD(&ctx->portal_maps);
	INIT_LIST_HEAD(&ctx->dma_maps);
	mutex_init(&ctx->lock);
	filp->private_data = ctx;
	return 0;
}

/* ====================================================================
 * ioctl: ID_ALLOC (0x01) - Resource range allocation
 * ==================================================================== */

/* Forward declaration — used in ioctl_id_alloc error path */
static void release_id_range(enum usdpaa_id_type type, u32 base, u32 count);

static int ioctl_id_alloc(struct usdpaa_ctx *ctx,
			  struct usdpaa_ioctl_id_alloc __user *arg)
{
	struct usdpaa_ioctl_id_alloc alloc;
	struct resource_range *rr;
	u32 base;
	int ret;

	if (copy_from_user(&alloc, arg, sizeof(alloc)))
		return -EFAULT;

	switch (alloc.id_type) {
	case usdpaa_id_fqid:
		ret = qman_alloc_fqid_range(&base, alloc.num);
		break;
	case usdpaa_id_bpid:
		ret = bm_alloc_bpid_range(&base, alloc.num);
		break;
	case usdpaa_id_qpool:
		ret = qman_alloc_pool_range(&base, alloc.num);
		break;
	case usdpaa_id_cgrid:
		ret = qman_alloc_cgrid_range(&base, alloc.num);
		break;
	default:
		/* CEETM types - not supported */
		return -ENOSYS;
	}

	if (ret)
		return ret;

	/* Track allocation for cleanup */
	rr = kmalloc(sizeof(*rr), GFP_KERNEL);
	if (!rr) {
		/* Best-effort release on alloc tracking failure.
			 * Use allocator-only frees — no hardware portal access. */
			release_id_range(alloc.id_type, base, alloc.num);
		return -ENOMEM;
	}
	rr->type = alloc.id_type;
	rr->base = base;
	rr->count = alloc.num;
	rr->reserved = false;

	mutex_lock(&ctx->lock);
	list_add(&rr->node, &ctx->alloc_resources);
	mutex_unlock(&ctx->lock);

	alloc.base = base;
	if (copy_to_user(arg, &alloc, sizeof(alloc)))
		return -EFAULT;

	return 0;
}

/* ====================================================================
 * ioctl: ID_RESERVE (0x0A) - Reserve specific resource ID range
 *
 * Unlike ID_ALLOC (which picks any free range), ID_RESERVE marks a
 * *specific* base+count as taken.  DPDK's DPAA PMD uses this to claim
 * the FQIDs pre-configured in the device-tree by FMan firmware
 * (fsl,qman-frame-queues-rx/tx properties).
 *
 * Implementation: tracking-only.  The hardware FQIDs/BPIDs are assigned
 * by FMan firmware and configured in the DT — they are NOT allocated
 * from gen_pool.  The mainline kernel gen_pool may or may not have
 * these IDs depending on whether dpaa_eth has them allocated or has
 * been unbound.  Instead of fighting gen_pool state, we simply record
 * the reservation for per-FD accounting and return success.  DPDK
 * programs the hardware queues directly via its own QMan portals.
 *
 * This matches the NXP SDK pattern where backend->reserve() operates
 * on a separate allocator independent of the kernel's gen_pool.
 * ==================================================================== */

static int ioctl_id_reserve(struct usdpaa_ctx *ctx,
 		    struct usdpaa_ioctl_id_reserve __user *arg)
{
 struct usdpaa_ioctl_id_reserve rsv;
 struct resource_range *rr;

 if (copy_from_user(&rsv, arg, sizeof(rsv)))
 	return -EFAULT;

 /* Validate type — CGRID has no reserve in NXP ABI */
 switch (rsv.id_type) {
 case usdpaa_id_fqid:
 case usdpaa_id_bpid:
 case usdpaa_id_qpool:
 	break;
 case usdpaa_id_cgrid:
 	return -EINVAL;
 default:
 	return -ENOSYS;
 }

 /* Track reservation for cleanup on fd close */
 rr = kmalloc(sizeof(*rr), GFP_KERNEL);
 if (!rr)
 	return -ENOMEM;
 rr->type = rsv.id_type;
 rr->base = rsv.base;
 rr->count = rsv.num;
 rr->reserved = true;

 mutex_lock(&ctx->lock);
 list_add(&rr->node, &ctx->alloc_resources);
 mutex_unlock(&ctx->lock);

 return 0;
}

/* ====================================================================
 * ioctl: ID_RELEASE (0x02) - Resource range release
 * ==================================================================== */

static void release_id_range(enum usdpaa_id_type type, u32 base, u32 count)
{
	/*
	 * USDPAA allocator-only release: free IDs back to genalloc pools
	 * WITHOUT any hardware cleanup (no portal access).
	 *
	 * The standard kernel release functions (qman_release_fqid,
	 * bm_release_bpid, qman_release_pool, qman_release_cgrid) all
	 * perform hardware operations via QBMan portals. When DPDK has
	 * reserved portals for userspace, those kernel portal mappings
	 * are invalid — accessing them causes a level 3 translation fault.
	 *
	 * DPDK is responsible for draining/retiring its own resources
	 * before closing the FD. We just reclaim the ID ranges.
	 */
	switch (type) {
	case usdpaa_id_fqid:
		qman_free_fqid_range(base, count);
		break;
	case usdpaa_id_bpid:
		bm_free_bpid_range(base, count);
		break;
	case usdpaa_id_qpool:
		qman_free_pool_range(base, count);
		break;
	case usdpaa_id_cgrid:
		qman_free_cgrid_range(base, count);
		break;
	default:
		break;
	}
}

static int ioctl_id_release(struct usdpaa_ctx *ctx,
			    struct usdpaa_ioctl_id_release __user *arg)
{
	struct usdpaa_ioctl_id_release rel;
	struct resource_range *rr, *tmp;

	if (copy_from_user(&rel, arg, sizeof(rel)))
		return -EFAULT;

	/* Find and remove from tracking list */
	mutex_lock(&ctx->lock);
	list_for_each_entry_safe(rr, tmp, &ctx->alloc_resources, node) {
		if (rr->type == rel.id_type && rr->base == rel.base &&
		    rr->count == rel.num) {
			list_del(&rr->node);
			mutex_unlock(&ctx->lock);
			if (!rr->reserved)
				release_id_range(rr->type, rr->base, rr->count);
			kfree(rr);
			return 0;
		}
	}
	mutex_unlock(&ctx->lock);

	/* Not tracked - release anyway (DPDK may release without tracking) */
	release_id_range(rel.id_type, rel.base, rel.num);
	return 0;
}

/* ====================================================================
 * ioctl: DMA_MAP (0x03) - DMA memory allocation from reserved-memory
 * ==================================================================== */

static int ioctl_dma_map(struct usdpaa_ctx *ctx,
			 struct usdpaa_ioctl_dma_map __user *arg)
{
	struct usdpaa_ioctl_dma_map map;
	struct dma_mapping *dm;
	unsigned long addr;

	if (!usdpaa_mem_pool)
		return -ENOMEM;

	if (copy_from_user(&map, arg, sizeof(map)))
		return -EFAULT;

	if (map.len == 0 || !IS_ALIGNED(map.len, PAGE_SIZE))
		return -EINVAL;

	addr = gen_pool_alloc(usdpaa_mem_pool, map.len);
	if (!addr)
		return -ENOMEM;

	dm = kmalloc(sizeof(*dm), GFP_KERNEL);
	if (!dm) {
		gen_pool_free(usdpaa_mem_pool, addr, map.len);
		return -ENOMEM;
	}

	dm->phys = (phys_addr_t)addr;
	dm->len = map.len;

	mutex_lock(&ctx->lock);
	list_add(&dm->node, &ctx->dma_maps);
	mutex_unlock(&ctx->lock);

	map.phys_addr = dm->phys;
	map.did_alloc = 1;
	if (copy_to_user(arg, &map, sizeof(map)))
		return -EFAULT;

	return 0;
}

/* ====================================================================
 * ioctl: DMA_UNMAP (0x04) - DMA memory release
 * ==================================================================== */

static int ioctl_dma_unmap(struct usdpaa_ctx *ctx, uint64_t __user *arg)
{
	uint64_t phys;
	struct dma_mapping *dm, *tmp;

	if (copy_from_user(&phys, arg, sizeof(phys)))
		return -EFAULT;

	mutex_lock(&ctx->lock);
	list_for_each_entry_safe(dm, tmp, &ctx->dma_maps, node) {
		if (dm->phys == (phys_addr_t)phys) {
			list_del(&dm->node);
			mutex_unlock(&ctx->lock);
			gen_pool_free(usdpaa_mem_pool, (unsigned long)dm->phys,
				      dm->len);
			kfree(dm);
			return 0;
		}
	}
	mutex_unlock(&ctx->lock);
	return -ENOENT;
}

/* ====================================================================
 * ioctl: DMA_USED (0x0B) - DMA memory statistics
 * ==================================================================== */

static int ioctl_dma_used(struct usdpaa_ioctl_dma_used __user *arg)
{
	struct usdpaa_ioctl_dma_used stats;

	if (!usdpaa_mem_pool) {
		stats.free_bytes = 0;
		stats.total_bytes = 0;
	} else {
		stats.total_bytes = usdpaa_mem_size;
		stats.free_bytes = gen_pool_avail(usdpaa_mem_pool);
	}

	if (copy_to_user(arg, &stats, sizeof(stats)))
		return -EFAULT;
	return 0;
}

/* ====================================================================
 * Helper: release a portal and remove from global list
 * ==================================================================== */

static void portal_release_and_untrack(struct portal_reservation *pr)
{
	mutex_lock(&global_portal_lock);
	list_del(&pr->global_node);
	mutex_unlock(&global_portal_lock);

	if (pr->type == usdpaa_portal_qman)
		qman_portal_release_reserved(pr->qpcfg);
	else
		bman_portal_release_reserved(pr->bpcfg);

	kfree(pr);
}

/* ====================================================================
 * Portal pgprot helpers for mmap
 *
 * CE (cache-enabled) window: Normal Write-Back, Non-Shareable
 *   QBMan stashing protocol requires WB cacheability but
 *   Non-Shareable (SH=00) since the portal hardware manages
 *   its own coherency.
 *
 * NOTE: upstream kernel has a latent bug where addr_virt_ce is
 *   actually mapped to CI physical (platform_get_resource overwrites
 *   the CE resource ptr with CI before memremap). The kernel never
 *   maps CE physical at all — portal ops work through CI window.
 *
 *   We do NOT memunmap the kernel's stale mapping because doing so
 *   destabilizes vmalloc bookkeeping and triggers translation faults
 *   on adjacent affine portal mappings. Since reserved portals are
 *   never accessed by kernel code, the stale mapping is harmless.
 *   Our remap_pfn_range uses the CORRECT CE physical from addr_phys_ce.
 *
 * CI (cache-inhibited) window: Strongly-Ordered / Device-nGnRnE
 *   Required for portal doorbell/command registers.
 *   Both kernel ioremap and our pgprot_noncached use Device-nGnRnE,
 *   so no attribute conflict exists for CI windows.
 *
 * On ARM64, PTE_SHARED = bits [9:8] = 0b11 (inner-shareable).
 * Clearing them gives 0b00 (non-shareable).
 * ==================================================================== */

#define PTE_SHARED_MASK (_AT(pteval_t, 3) << 8)
static inline pgprot_t pgprot_cached_nonshared(pgprot_t prot)
{
	return __pgprot(pgprot_val(prot) & ~PTE_SHARED_MASK);
}

/* ====================================================================
 * ioctl: ALLOC_RAW_PORTAL (0x0C) - Portal allocation (phys addrs)
 *
 * Used by DPDK for qman_allocate_raw_portal/bman_allocate_raw_portal.
 * Returns physical addresses — DPDK mmaps these via /dev/mem.
 * ==================================================================== */

static int ioctl_alloc_raw_portal(struct usdpaa_ctx *ctx,
				  struct usdpaa_ioctl_raw_portal __user *arg)
{
	struct usdpaa_ioctl_raw_portal rp;
	struct portal_reservation *pr;
	int ret;

	if (copy_from_user(&rp, arg, sizeof(rp)))
		return -EFAULT;

	pr = kzalloc(sizeof(*pr), GFP_KERNEL);
	if (!pr)
		return -ENOMEM;

	pr->type = rp.type;

	if (rp.type == usdpaa_portal_qman) {
		struct qm_portal_config *qpcfg;

		ret = qman_portal_reserve(&qpcfg);
		if (ret) {
			kfree(pr);
			return ret;
		}

		/* Configure stash destination if requested */
		if (rp.enable_stash)
			qman_set_sdest(qpcfg->channel, rp.sdest);

		rp.cinh = qpcfg->addr_phys_ci;
		rp.cena = qpcfg->addr_phys_ce;
		rp.index = qpcfg->channel - QM_CHANNEL_SWPORTAL0;
		pr->qpcfg = qpcfg;
		pr->phys_ce = qpcfg->addr_phys_ce;
		pr->size_ce = qpcfg->size_ce;
		pr->phys_ci = qpcfg->addr_phys_ci;
		pr->size_ci = qpcfg->size_ci;
	} else if (rp.type == usdpaa_portal_bman) {
		struct bm_portal_config *bpcfg;

		ret = bman_portal_reserve(&bpcfg);
		if (ret) {
			kfree(pr);
			return ret;
		}

		rp.cinh = bpcfg->addr_phys_ci;
		rp.cena = bpcfg->addr_phys_ce;
		rp.index = 0;
		pr->bpcfg = bpcfg;
		pr->phys_ce = bpcfg->addr_phys_ce;
		pr->size_ce = bpcfg->size_ce;
		pr->phys_ci = bpcfg->addr_phys_ci;
		pr->size_ci = bpcfg->size_ci;
	} else {
		kfree(pr);
		return -EINVAL;
	}

	/* Track in per-FD list */
	mutex_lock(&ctx->lock);
	list_add(&pr->node, &ctx->portal_maps);
	mutex_unlock(&ctx->lock);

	/* Track in global list for IRQ device lookup */
	mutex_lock(&global_portal_lock);
	list_add(&pr->global_node, &global_portal_list);
	mutex_unlock(&global_portal_lock);

	if (copy_to_user(arg, &rp, sizeof(rp)))
		return -EFAULT;

	return 0;
}

/* ====================================================================
 * ioctl: FREE_RAW_PORTAL (0x0D) - Release portal
 * ==================================================================== */

static int ioctl_free_raw_portal(struct usdpaa_ctx *ctx,
				 struct usdpaa_ioctl_raw_portal __user *arg)
{
	struct usdpaa_ioctl_raw_portal rp;
	struct portal_reservation *pr, *tmp;

	if (copy_from_user(&rp, arg, sizeof(rp)))
		return -EFAULT;

	mutex_lock(&ctx->lock);
	list_for_each_entry_safe(pr, tmp, &ctx->portal_maps, node) {
		bool match = false;

		if (pr->type != rp.type)
			continue;

		if (rp.type == usdpaa_portal_qman &&
		    pr->qpcfg->addr_phys_ci == rp.cinh)
			match = true;
		else if (rp.type == usdpaa_portal_bman &&
			 pr->bpcfg->addr_phys_ci == rp.cinh)
			match = true;

		if (match) {
			list_del(&pr->node);
			mutex_unlock(&ctx->lock);
			portal_release_and_untrack(pr);
			return 0;
		}
	}
	mutex_unlock(&ctx->lock);
	return -ENOENT;
}

/* ====================================================================
 * ioctl: PORTAL_MAP (0x07) - Portal reservation + phys address return
 *
 * This is the ioctl DPDK's bman_driver.c and qman_driver.c use:
 *   1. Reserve a portal from the idle pool
 *   2. Return physical addresses in addr.{cena, cinh}
 *   3. DPDK then calls mmap(usdpaa_fd, ..., phys>>PAGE_SHIFT) to
 *      get virtual addresses with correct page protection attributes
 *
 * IMPORTANT: We do NOT call do_mmap() from ioctl context — that
 * causes kernel panics on mainline 6.6. Instead, DPDK does the
 * mmap() call from userspace, which safely invokes our usdpaa_mmap()
 * handler.
 * ==================================================================== */

static int ioctl_portal_map(struct usdpaa_ctx *ctx,
			    struct usdpaa_ioctl_portal_map __user *arg)
{
	struct usdpaa_ioctl_portal_map pm;
	struct portal_reservation *pr;
	int ret;

	if (copy_from_user(&pm, arg, sizeof(pm)))
		return -EFAULT;

	pr = kzalloc(sizeof(*pr), GFP_KERNEL);
	if (!pr)
		return -ENOMEM;

	pr->type = pm.type;

	if (pm.type == usdpaa_portal_qman) {
		struct qm_portal_config *qpcfg;

		ret = qman_portal_reserve(&qpcfg);
		if (ret) {
			kfree(pr);
			return ret;
		}
		pm.index = qpcfg->channel - QM_CHANNEL_SWPORTAL0;
		pm.channel = qpcfg->channel;
		pm.pools = 0xFF; /* All pool channels available */
		pr->qpcfg = qpcfg;
		pr->phys_ce = qpcfg->addr_phys_ce;
		pr->size_ce = qpcfg->size_ce;
		pr->phys_ci = qpcfg->addr_phys_ci;
		pr->size_ci = qpcfg->size_ci;
	} else if (pm.type == usdpaa_portal_bman) {
		struct bm_portal_config *bpcfg;

		ret = bman_portal_reserve(&bpcfg);
		if (ret) {
			kfree(pr);
			return ret;
		}
		pm.index = 0;
		pm.channel = 0;
		pm.pools = 0;
		pr->bpcfg = bpcfg;
		pr->phys_ce = bpcfg->addr_phys_ce;
		pr->size_ce = bpcfg->size_ce;
		pr->phys_ci = bpcfg->addr_phys_ci;
		pr->size_ci = bpcfg->size_ci;
	} else {
		kfree(pr);
		return -EINVAL;
	}

	/* Track in per-FD list (needed for mmap handler to find portals) */
	mutex_lock(&ctx->lock);
	list_add(&pr->node, &ctx->portal_maps);
	mutex_unlock(&ctx->lock);

	/* Track in global list for IRQ device lookup */
	mutex_lock(&global_portal_lock);
	list_add(&pr->global_node, &global_portal_list);
	mutex_unlock(&global_portal_lock);

	/*
	 * Return physical addresses in the void* fields.
	 * DPDK will call mmap() on this fd to map them with correct pgprot.
	 */
	pm.addr.cena = (void *)(uintptr_t)pr->phys_ce;
	pm.addr.cinh = (void *)(uintptr_t)pr->phys_ci;

	if (copy_to_user(arg, &pm, sizeof(pm)))
		return -EFAULT;

	pr_debug("usdpaa: PORTAL_MAP %s idx=%d phys_ce=0x%llx phys_ci=0x%llx "
		 "size_ce=0x%zx size_ci=0x%zx\n",
		 pm.type == usdpaa_portal_qman ? "QMan" : "BMan",
		 pm.index, (u64)pr->phys_ce, (u64)pr->phys_ci,
		 pr->size_ce, pr->size_ci);

	return 0;
}

/* ====================================================================
 * ioctl: PORTAL_UNMAP (0x08) - Release portal by CI address
 *
 * DPDK sends struct usdpaa_portal_map {cinh, cena} (16 bytes).
 * On mainline, cinh carries the physical CI address (same value
 * that PORTAL_MAP returned). We match by physical address.
 * ==================================================================== */

static int ioctl_portal_unmap(struct usdpaa_ctx *ctx,
			      struct usdpaa_portal_map __user *arg)
{
	struct usdpaa_portal_map pm;
	struct portal_reservation *pr, *tmp;
	resource_size_t target_cinh;

	if (copy_from_user(&pm, arg, sizeof(pm)))
		return -EFAULT;

	target_cinh = (resource_size_t)(uintptr_t)pm.cinh;

	mutex_lock(&ctx->lock);
	list_for_each_entry_safe(pr, tmp, &ctx->portal_maps, node) {
		if (pr->phys_ci == target_cinh) {
			list_del(&pr->node);
			mutex_unlock(&ctx->lock);
			portal_release_and_untrack(pr);
			return 0;
		}
	}
	mutex_unlock(&ctx->lock);
	return -ENOENT;
}

/* ====================================================================
 * ioctl: GET_LINK_STATUS (0x10) - Query interface link state
 * ==================================================================== */

static int ioctl_get_link_status(
	struct usdpaa_ioctl_link_status_args __user *arg)
{
	struct usdpaa_ioctl_link_status_args ls;
	struct net_device *netdev;
	struct ethtool_link_ksettings ks;
	int ret;

	if (copy_from_user(&ls, arg, sizeof(ls)))
		return -EFAULT;

	ls.if_name[sizeof(ls.if_name) - 1] = '\0';

	rtnl_lock();
	netdev = dev_get_by_name(&init_net, ls.if_name);
	if (!netdev) {
		rtnl_unlock();
		return -ENODEV;
	}

	ls.link_status = netif_carrier_ok(netdev) ? 1 : 0;

	/* Get speed/duplex/autoneg via ethtool kernel API */
	memset(&ks, 0, sizeof(ks));
	if (netdev->ethtool_ops && netdev->ethtool_ops->get_link_ksettings) {
		ret = netdev->ethtool_ops->get_link_ksettings(netdev, &ks);
		if (!ret) {
			ls.link_speed = ks.base.speed;
			ls.link_duplex = ks.base.duplex;
			ls.link_autoneg = ks.base.autoneg;
		} else {
			ls.link_speed = 0;
			ls.link_duplex = 0;
			ls.link_autoneg = 0;
		}
	} else {
		ls.link_speed = 0;
		ls.link_duplex = 0;
		ls.link_autoneg = 0;
	}

	dev_put(netdev);
	rtnl_unlock();

	if (copy_to_user(arg, &ls, sizeof(ls)))
		return -EFAULT;

	return 0;
}

/* ====================================================================
 * mmap handler - Portal and DMA memory mappings
 *
 * Called when userspace does mmap(fd, ..., phys >> PAGE_SHIFT).
 * DPDK calls this AFTER PORTAL_MAP ioctl to map portal windows.
 *
 * We match the requested pgoff against registered portals:
 *   - CE window match → Write-Back Non-Shareable (stashing protocol)
 *   - CI window match → Strongly-Ordered/Device (doorbell registers)
 *   - DMA pool match  → Write-Combining (non-cacheable coherent)
 *
 * Size matching is relaxed - we only check that the requested range
 * falls within the portal window. This allows DPDK to mmap with
 * sizes it determines from device-tree.
 * ==================================================================== */

static int usdpaa_mmap(struct file *filp, struct vm_area_struct *vma)
{
	struct usdpaa_ctx *ctx = filp->private_data;
	unsigned long pfn = vma->vm_pgoff;
	size_t len = vma->vm_end - vma->vm_start;
	struct portal_reservation *pr;
	unsigned long offset;

	/*
	 * Check portal mappings — DPDK does:
	 *   mmap(NULL, size, PROT_READ|PROT_WRITE, MAP_SHARED,
	 *        usdpaa_fd, phys_addr)
	 * where phys_addr is page-aligned and becomes vm_pgoff.
	 */
	mutex_lock(&ctx->lock);
	list_for_each_entry(pr, &ctx->portal_maps, node) {
		/* Check CE (cache-enabled) window */
		if (pfn == (pr->phys_ce >> PAGE_SHIFT) &&
		    len <= pr->size_ce) {
			mutex_unlock(&ctx->lock);
			/*
			 * CE window: Normal Write-Back, Non-Shareable
			 * QBMan stashing protocol requires WB cacheability
			 * but Non-Shareable (SH=00) since the portal
			 * hardware manages its own coherency.
			 */
			vma->vm_page_prot =
				pgprot_cached_nonshared(vma->vm_page_prot);
			vm_flags_set(vma, VM_IO | VM_PFNMAP |
					  VM_DONTEXPAND | VM_DONTDUMP);
			return remap_pfn_range(vma, vma->vm_start, pfn,
					       len, vma->vm_page_prot);
		}
		/* Check CI (cache-inhibited) window */
		if (pfn == (pr->phys_ci >> PAGE_SHIFT) &&
		    len <= pr->size_ci) {
			mutex_unlock(&ctx->lock);
			/*
			 * CI window: Strongly-Ordered (Device-nGnRnE)
			 * Required for portal doorbell/command registers.
			 */
			vma->vm_page_prot =
				pgprot_noncached(vma->vm_page_prot);
			vm_flags_set(vma, VM_IO | VM_PFNMAP |
					  VM_DONTEXPAND | VM_DONTDUMP);
			return remap_pfn_range(vma, vma->vm_start, pfn,
					       len, vma->vm_page_prot);
		}
	}
	mutex_unlock(&ctx->lock);

	/* Check DMA pool */
	if (usdpaa_mem_pool) {
		offset = pfn << PAGE_SHIFT;
		if (offset >= usdpaa_mem_phys &&
		    offset + len <= usdpaa_mem_phys + usdpaa_mem_size) {
			/* DMA memory: Write-Combining (non-cacheable) */
			vma->vm_page_prot =
				pgprot_writecombine(vma->vm_page_prot);
			vm_flags_set(vma, VM_IO | VM_PFNMAP |
					  VM_DONTEXPAND | VM_DONTDUMP);
			return remap_pfn_range(vma, vma->vm_start, pfn,
					       len, vma->vm_page_prot);
		}
	}

	pr_warn("usdpaa: mmap failed - no match for pgoff=0x%lx len=0x%zx\n",
		pfn, len);
	return -EINVAL;
}

/* ====================================================================
 * Cleanup on close - release all resources held by this FD
 *
 * DPDK's userspace mmaps are automatically cleaned up by the kernel
 * when the process exits or the VMA is munmapped. We only need to
 * release the HW resources (portals, IDs, DMA memory).
 * ==================================================================== */

static int usdpaa_release(struct inode *inode, struct file *filp)
{
	struct usdpaa_ctx *ctx = filp->private_data;
	struct dma_mapping *dm, *tmp_dm;
	struct portal_reservation *pr, *tmp_pr;
	struct resource_range *rr, *tmp_rr;

	if (!ctx)
		return 0;

	/* Release all DMA mappings */
	list_for_each_entry_safe(dm, tmp_dm, &ctx->dma_maps, node) {
		if (usdpaa_mem_pool)
			gen_pool_free(usdpaa_mem_pool,
				      (unsigned long)dm->phys, dm->len);
		list_del(&dm->node);
		kfree(dm);
	}

	/* Release all portals (userspace mmaps auto-cleaned by kernel) */
	list_for_each_entry_safe(pr, tmp_pr, &ctx->portal_maps, node) {
		list_del(&pr->node);
		portal_release_and_untrack(pr);
	}

	/* Release all resource IDs (only gen_pool-allocated, not reserved) */
	list_for_each_entry_safe(rr, tmp_rr, &ctx->alloc_resources, node) {
		if (!rr->reserved)
			release_id_range(rr->type, rr->base, rr->count);
		list_del(&rr->node);
		kfree(rr);
	}

	mutex_destroy(&ctx->lock);
	kfree(ctx);
	return 0;
}

/* ====================================================================
 * ioctl dispatch - /dev/fsl-usdpaa
 * ==================================================================== */

static long usdpaa_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	struct usdpaa_ctx *ctx = filp->private_data;
	void __user *argp = (void __user *)arg;

	switch (cmd) {
	/* Resource allocation */
	case USDPAA_IOCTL_ID_ALLOC:
		return ioctl_id_alloc(ctx, argp);
	case USDPAA_IOCTL_ID_RELEASE:
		return ioctl_id_release(ctx, argp);
	case USDPAA_IOCTL_ID_RESERVE:
		return ioctl_id_reserve(ctx, argp);

	/* DMA memory */
	case USDPAA_IOCTL_DMA_MAP:
		return ioctl_dma_map(ctx, argp);
	case USDPAA_IOCTL_DMA_UNMAP:
		return ioctl_dma_unmap(ctx, argp);
	case USDPAA_IOCTL_DMA_LOCK:
		return 0; /* No-op - single-process DPDK */
	case USDPAA_IOCTL_DMA_UNLOCK:
		return 0; /* No-op */
	case USDPAA_IOCTL_DMA_USED:
		return ioctl_dma_used(argp);

	/* Portal management */
	case USDPAA_IOCTL_PORTAL_MAP:
		return ioctl_portal_map(ctx, argp);
	case USDPAA_IOCTL_PORTAL_UNMAP:
		return ioctl_portal_unmap(ctx, argp);
	case USDPAA_IOCTL_ALLOC_RAW_PORTAL:
		return ioctl_alloc_raw_portal(ctx, argp);
	case USDPAA_IOCTL_FREE_RAW_PORTAL:
		return ioctl_free_raw_portal(ctx, argp);

	/* Portal IRQ - should go to /dev/fsl-usdpaa-irq, not here */
	case USDPAA_IOCTL_PORTAL_IRQ_MAP:
		pr_debug("usdpaa: PORTAL_IRQ_MAP sent to wrong device "
			 "(use /dev/fsl-usdpaa-irq)\n");
		return 0;

	/* Link status */
	case USDPAA_IOCTL_ENABLE_LINK_STATUS_INTERRUPT:
		return 0; /* Stub - DPDK uses poll-mode */
	case USDPAA_IOCTL_DISABLE_LINK_STATUS_INTERRUPT:
		return 0;
	case USDPAA_IOCTL_GET_LINK_STATUS:
		return ioctl_get_link_status(argp);
	case USDPAA_IOCTL_UPDATE_LINK_STATUS:
		return -ENOSYS; /* Managed by kernel PHY driver */
	case USDPAA_IOCTL_UPDATE_LINK_SPEED:
		return -ENOSYS;
	case USDPAA_IOCTL_RESTART_LINK_AUTONEG:
		return -ENOSYS;

	/* Version */
	case USDPAA_IOCTL_GET_IOCTL_VERSION: {
		int ver = USDPAA_IOCTL_VERSION;

		if (copy_to_user(argp, &ver, sizeof(ver)))
			return -EFAULT;
		return 0;
	}

	default:
		pr_debug("usdpaa: unknown ioctl 0x%x (nr=%u size=%u)\n",
			 cmd, _IOC_NR(cmd), _IOC_SIZE(cmd));
		return -ENOTTY;
	}
}

/* ====================================================================
 * /dev/fsl-usdpaa file operations and miscdevice
 * ==================================================================== */

static const struct file_operations usdpaa_fops = {
	.owner		= THIS_MODULE,
	.open		= usdpaa_open,
	.release	= usdpaa_release,
	.unlocked_ioctl	= usdpaa_ioctl,
	.mmap		= usdpaa_mmap,
};

static struct miscdevice usdpaa_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "fsl-usdpaa",
	.fops  = &usdpaa_fops,
};

/* ====================================================================
 * /dev/fsl-usdpaa-irq: Portal interrupt device
 *
 * Provides per-portal IRQ handling for DPDK. Flow:
 * 1. DPDK opens /dev/fsl-usdpaa-irq (one FD per portal)
 * 2. Sends PORTAL_IRQ_MAP ioctl with portal CI physical address
 * 3. Kernel requests the portal's HW IRQ
 * 4. IRQ handler inhibits portal + wakes waiters
 * 5. DPDK calls read() which blocks until IRQ fires
 * 6. DPDK processes the portal, then uninhibits via MMIO write
 * ==================================================================== */

struct usdpaa_irq_ctx {
	atomic_t count;          /* Incremented by IRQ handler */
	atomic_t last_read;      /* Last count seen by read() */
	wait_queue_head_t wait;  /* Woken by IRQ handler */
	int irq;                 /* Linux IRQ number, 0 = not mapped */
	void __iomem *inhibit_addr; /* Portal IIR register vaddr */
};

/* IRQ handler - inhibit portal + wake userspace */
static irqreturn_t usdpaa_portal_irq(int irq, void *data)
{
	struct usdpaa_irq_ctx *ctx = data;

	atomic_inc(&ctx->count);
	wake_up_all(&ctx->wait);

	/* Inhibit further portal interrupts until userspace processes */
	if (ctx->inhibit_addr)
		iowrite32be(0x1, ctx->inhibit_addr);

	return IRQ_HANDLED;
}

static int usdpaa_irq_open(struct inode *inode, struct file *filp)
{
	struct usdpaa_irq_ctx *ctx;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	atomic_set(&ctx->count, 0);
	atomic_set(&ctx->last_read, 0);
	init_waitqueue_head(&ctx->wait);
	ctx->irq = 0;
	ctx->inhibit_addr = NULL;
	filp->private_data = ctx;
	return 0;
}

/*
 * ioctl: PORTAL_IRQ_MAP - map a portal's IRQ to this FD
 *
 * DPDK sends: { type, fd, portal_cinh }
 *   - type: bman or qman
 *   - fd: the /dev/fsl-usdpaa file descriptor (ignored by us)
 *   - portal_cinh: physical address of CI window
 *                  (PORTAL_MAP returns physical addresses on mainline)
 *
 * We look up the portal in our global reservation list by matching
 * the physical CI address.
 */
static int usdpaa_irq_ioctl_map(struct usdpaa_irq_ctx *ctx,
				struct usdpaa_ioctl_irq_map __user *arg)
{
	struct usdpaa_ioctl_irq_map im;
	struct portal_reservation *pr;
	resource_size_t target;
	int irq_num = 0;
	void __iomem *ci_vaddr = NULL;
	int ret;

	if (copy_from_user(&im, arg, sizeof(im)))
		return -EFAULT;

	/* Already mapped? */
	if (ctx->irq)
		return -EBUSY;

	target = (resource_size_t)(uintptr_t)im.portal_cinh;

	/* Find the portal in the global list by physical CI address */
	mutex_lock(&global_portal_lock);
	list_for_each_entry(pr, &global_portal_list, global_node) {
		if (pr->type != (im.type == usdpaa_portal_bman ?
				 usdpaa_portal_bman : usdpaa_portal_qman))
			continue;

		if (pr->phys_ci == target) {
			if (pr->type == usdpaa_portal_bman) {
				irq_num = pr->bpcfg->irq;
				ci_vaddr = pr->bpcfg->addr_virt_ci;
			} else {
				irq_num = pr->qpcfg->irq;
				ci_vaddr = pr->qpcfg->addr_virt_ci;
			}
			break;
		}
	}
	mutex_unlock(&global_portal_lock);

	if (!irq_num) {
		pr_debug("usdpaa-irq: no portal found for phys 0x%llx\n",
			 (u64)target);
		return -ENOENT;
	}

	/* Point to the IIR (Interrupt Inhibit Register) */
	if (ci_vaddr)
		ctx->inhibit_addr = ci_vaddr + USDPAA_PORTAL_IIR_OFFSET;

	/* Request the hardware IRQ */
	ret = request_irq(irq_num, usdpaa_portal_irq, 0,
			  "usdpaa-portal", ctx);
	if (ret) {
		pr_err("usdpaa-irq: request_irq(%d) failed: %d\n",
		       irq_num, ret);
		ctx->inhibit_addr = NULL;
		return ret;
	}

	ctx->irq = irq_num;

	pr_debug("usdpaa-irq: mapped IRQ %d for %s portal phys 0x%llx\n",
		 irq_num,
		 im.type == usdpaa_portal_bman ? "BMan" : "QMan",
		 (u64)target);

	return 0;
}

static long usdpaa_irq_ioctl(struct file *filp, unsigned int cmd,
			     unsigned long arg)
{
	struct usdpaa_irq_ctx *ctx = filp->private_data;
	void __user *argp = (void __user *)arg;

	switch (cmd) {
	case USDPAA_IOCTL_PORTAL_IRQ_MAP:
		return usdpaa_irq_ioctl_map(ctx, argp);
	default:
		return -ENOTTY;
	}
}

/*
 * read() - block until portal IRQ fires
 *
 * DPDK calls read(fd, &irq_count, sizeof(irq_count)) to wait for an IRQ.
 * We return the current count when it changes from last_read.
 * The read buffer receives the count as a u32.
 */
static ssize_t usdpaa_irq_read(struct file *filp, char __user *buf,
				size_t count, loff_t *ppos)
{
	struct usdpaa_irq_ctx *ctx = filp->private_data;
	u32 cur_count;
	int ret;

	if (count < sizeof(u32))
		return -EINVAL;

	/* Wait until IRQ count changes from what we last reported */
	ret = wait_event_interruptible(ctx->wait,
		atomic_read(&ctx->count) != atomic_read(&ctx->last_read));
	if (ret)
		return ret;

	cur_count = atomic_read(&ctx->count);
	atomic_set(&ctx->last_read, cur_count);

	if (copy_to_user(buf, &cur_count, sizeof(cur_count)))
		return -EFAULT;

	return sizeof(cur_count);
}

/*
 * poll() - non-blocking IRQ check
 */
static __poll_t usdpaa_irq_poll(struct file *filp,
				struct poll_table_struct *wait)
{
	struct usdpaa_irq_ctx *ctx = filp->private_data;

	poll_wait(filp, &ctx->wait, wait);

	if (atomic_read(&ctx->count) != atomic_read(&ctx->last_read))
		return EPOLLIN | EPOLLRDNORM;

	return 0;
}

/*
 * release() - free IRQ and cleanup
 */
static int usdpaa_irq_release(struct inode *inode, struct file *filp)
{
	struct usdpaa_irq_ctx *ctx = filp->private_data;

	if (!ctx)
		return 0;

	if (ctx->irq) {
		free_irq(ctx->irq, ctx);
		/* Inhibit the portal one last time to prevent stale IRQs */
		if (ctx->inhibit_addr)
			iowrite32be(0x1, ctx->inhibit_addr);
		pr_debug("usdpaa-irq: freed IRQ %d\n", ctx->irq);
	}

	kfree(ctx);
	return 0;
}

static const struct file_operations usdpaa_irq_fops = {
	.owner		= THIS_MODULE,
	.open		= usdpaa_irq_open,
	.release	= usdpaa_irq_release,
	.unlocked_ioctl	= usdpaa_irq_ioctl,
	.read		= usdpaa_irq_read,
	.poll		= usdpaa_irq_poll,
};

static struct miscdevice usdpaa_irq_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "fsl-usdpaa-irq",
	.fops  = &usdpaa_irq_fops,
};

/* ====================================================================
 * Module init/exit
 * ==================================================================== */

static int __init usdpaa_init(void)
{
	struct device_node *mem_node;
	struct resource res;
	int ret;

	/* Find reserved-memory node for DMA pool */
	mem_node = of_find_compatible_node(NULL, NULL, "fsl,usdpaa-mem");
	if (!mem_node) {
		pr_info("fsl-usdpaa: no reserved memory node found, "
			"DMA_MAP will be unavailable\n");
	} else {
		ret = of_address_to_resource(mem_node, 0, &res);
		of_node_put(mem_node);
		if (ret) {
			pr_err("fsl-usdpaa: cannot parse reserved memory: %d\n",
			       ret);
			return ret;
		}

		usdpaa_mem_phys = res.start;
		usdpaa_mem_size = resource_size(&res);

		usdpaa_mem_pool = gen_pool_create(PAGE_SHIFT, -1);
		if (!usdpaa_mem_pool) {
			pr_err("fsl-usdpaa: gen_pool_create failed\n");
			return -ENOMEM;
		}

		ret = gen_pool_add(usdpaa_mem_pool, usdpaa_mem_phys,
				   usdpaa_mem_size, -1);
		if (ret) {
			pr_err("fsl-usdpaa: gen_pool_add failed: %d\n", ret);
			gen_pool_destroy(usdpaa_mem_pool);
			usdpaa_mem_pool = NULL;
			return ret;
		}
	}

	/* Register /dev/fsl-usdpaa */
	ret = misc_register(&usdpaa_miscdev);
	if (ret) {
		pr_err("fsl-usdpaa: misc_register failed: %d\n", ret);
		goto err_cleanup_pool;
	}

	/* Register /dev/fsl-usdpaa-irq */
	ret = misc_register(&usdpaa_irq_miscdev);
	if (ret) {
		pr_err("fsl-usdpaa-irq: misc_register failed: %d\n", ret);
		goto err_dereg_usdpaa;
	}

	pr_info("fsl-usdpaa: registered (mainline, DMA pool: %zu MB @ 0x%llx)\n",
		usdpaa_mem_size >> 20, (u64)usdpaa_mem_phys);
	pr_info("fsl-usdpaa-irq: registered (portal IRQ handler)\n");
	return 0;

err_dereg_usdpaa:
	misc_deregister(&usdpaa_miscdev);
err_cleanup_pool:
	if (usdpaa_mem_pool) {
		gen_pool_destroy(usdpaa_mem_pool);
		usdpaa_mem_pool = NULL;
	}
	return ret;
}

static void __exit usdpaa_exit(void)
{
	misc_deregister(&usdpaa_irq_miscdev);
	misc_deregister(&usdpaa_miscdev);
	if (usdpaa_mem_pool) {
		gen_pool_destroy(usdpaa_mem_pool);
		usdpaa_mem_pool = NULL;
	}
	pr_info("fsl-usdpaa: unregistered\n");
}

module_init(usdpaa_init);
module_exit(usdpaa_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Mono Gateway Project");
MODULE_DESCRIPTION("Userspace DPAA driver for DPDK on mainline kernel");
MODULE_ALIAS("devname:fsl-usdpaa");
MODULE_ALIAS("devname:fsl-usdpaa-irq");
