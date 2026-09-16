// SPDX-License-Identifier: GPL-2.0
/*
 * FMan Parse / Classify / Distribute (PCD) subsystem - orchestration layer.
 *
 * Authoritative reference: LS1046A Reference Manual chapter 8 (RM 8.7-8.10).
 *
 * Architectural role
 * ------------------
 * Top-level entry point for the FMan PCD hardware-programming layer that
 * lets a consumer install exact-match flow rules into the FMan
 * KeyGen/Classifier/Manipulator/Policer pipeline at line rate. Mainline
 * 6.18 ships only the parser+RSS subset of PCD; this subsystem resurrects
 * the exact-match path that the legacy NXP SDK provided through its
 * fm_pcd_*.c tree, but does so written from RM 8.7-8.10 and the existing
 * in-tree fman_keygen.c/fman_port.c register layouts only. No SDK code
 * was read while authoring.
 *
 * What this file delivers
 * -----------------------
 * Orchestration scaffolding only: per-FMan PCD state struct, lifecycle
 * (fman_pcd_init / fman_pcd_release), MURAM-budget tracking + accessor,
 * internal list-head registries that the KG/CC/manip/plcr/parser/replic
 * modules attach to, plus a debugfs muram_budget node. No silicon-side
 * state machine is touched here.
 */

#include <linux/debugfs.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/seq_file.h>
#include <linux/slab.h>

#include <linux/fsl/fman_pcd.h>
#include "fman_pcd_internal.h"

#include "fman.h"
#include "fman_muram.h"

/*
 * PCD scratch MURAM partition size.
 *
 * RM 8.7.1 worst-case per-component MURAM footprint:
 *   32 KG schemes @ 64 B            =  2048 B (~  2 KiB)
 *   256 policer profiles @ 32 B     =  8192 B (~  8 KiB)
 *   64 CC trees @ ~256 B            = 16384 B (~ 16 KiB)
 *   CC key tables (design budget)   ~ 64 KiB
 *   parser HXS records              ~  4 KiB
 *   replicator group records        ~  8 KiB
 *
 * Total worst-case ~100 KiB. The full 96 KiB partition does not fit the
 * post-CAM/FIFO free MURAM window on LS1046A (6x1G + 2x10G ports) and is
 * rejected with -ENOMEM at probe (PR14b regression). We reserve a 64 KiB
 * partition; fman_muram_alloc rounds up to 256-byte (gen_pool
 * min_alloc_order=8) so 65536 is an exact multiple.
 */
#define FMAN_PCD_MURAM_RESERVED_BYTES	(64U * 1024U)

/*
 * Per-FMan PCD state. Allocated and freed by fman_pcd_init/release.
 * Pinned to the owning struct fman via the new `pcd` member added by the
 * integration patch on fman.c.
 *
 * Locking model
 * -------------
 *  - lock: serialises list mutations + MURAM budget accounting.  Held
 *    across KG scheme create/destroy, CC tree create/destroy, manip/plcr
 *    create/destroy.  Not held on the fast path - the consumer reaches
 *    into per-CC-node spinlocks for flow insert/delete.
 *  - muram_used / muram_high_water: protected by lock.  Accounting only;
 *    the underlying MURAM gen_pool has its own internal lock.
 */
struct fman_pcd {
	struct fman *fman;

	struct mutex lock;

	unsigned long muram_offset;
	size_t muram_reserved;
	size_t muram_used;
	size_t muram_high_water;

	/*
	 * Per-engine registries.  Each list_head is consumed by the
	 * corresponding per-engine module which appends one entry per live
	 * scheme/tree/profile so fman_pcd_release() can tear them down in
	 * dependency order.  At skeleton stage the lists are initialised
	 * but always empty.
	 */
	struct list_head kg_schemes;
	struct list_head cc_trees;
	struct list_head manip_profiles;
	struct list_head plcr_profiles;
	struct list_head replic_groups;

	struct dentry *debugfs_dir;
};

/*
 * Globally-rooted debugfs parent.  Created on the first fman_pcd_init()
 * and cleaned up on the last fman_pcd_release().  Each FMan instance
 * gets a subdirectory named by its FMan id (typically "0" on LS1046A).
 */
static struct dentry *fman_pcd_debugfs_root;
static DEFINE_MUTEX(fman_pcd_debugfs_lock);
static int fman_pcd_debugfs_refcount;

static int fman_pcd_debugfs_root_get(void)
{
	mutex_lock(&fman_pcd_debugfs_lock);
	if (fman_pcd_debugfs_refcount++ == 0) {
		fman_pcd_debugfs_root = debugfs_create_dir("fman_pcd", NULL);
		if (IS_ERR(fman_pcd_debugfs_root)) {
			int err = PTR_ERR(fman_pcd_debugfs_root);

			fman_pcd_debugfs_root = NULL;
			fman_pcd_debugfs_refcount = 0;
			mutex_unlock(&fman_pcd_debugfs_lock);
			return err;
		}
	}
	mutex_unlock(&fman_pcd_debugfs_lock);
	return 0;
}

static void fman_pcd_debugfs_root_put(void)
{
	mutex_lock(&fman_pcd_debugfs_lock);
	if (WARN_ON(fman_pcd_debugfs_refcount == 0)) {
		mutex_unlock(&fman_pcd_debugfs_lock);
		return;
	}
	if (--fman_pcd_debugfs_refcount == 0) {
		debugfs_remove_recursive(fman_pcd_debugfs_root);
		fman_pcd_debugfs_root = NULL;
	}
	mutex_unlock(&fman_pcd_debugfs_lock);
}

static int fman_pcd_muram_budget_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;
	size_t used, hw, free_bytes;

	mutex_lock(&pcd->lock);
	used = pcd->muram_used;
	hw = pcd->muram_high_water;
	free_bytes = pcd->muram_reserved - used;
	mutex_unlock(&pcd->lock);

	seq_printf(s, "reserved   %10zu bytes\n", pcd->muram_reserved);
	seq_printf(s, "used       %10zu bytes\n", used);
	seq_printf(s, "free       %10zu bytes\n", free_bytes);
	seq_printf(s, "high-water %10zu bytes\n", hw);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(fman_pcd_muram_budget);

/*
 * Internal MURAM allocator wrappers consumed by per-engine siblings (NOT
 * EXPORT_SYMBOL - same module, fsl_dpaa_fman.ko).  Budget accounting is
 * best-effort: the underlying gen_pool keeps the authoritative
 * accounting; ours is for visibility (debugfs + cli).
 */
unsigned long fman_pcd_muram_alloc(struct fman_pcd *pcd, size_t size)
{
	struct muram_info *muram;
	unsigned long offset;

	if (WARN_ON(!pcd) || size == 0)
		return (unsigned long)-EINVAL;

	muram = fman_get_muram(pcd->fman);
	if (!muram)
		return (unsigned long)-ENXIO;

	offset = fman_muram_alloc(muram, size);
	if (IS_ERR_VALUE(offset))
		return offset;

	mutex_lock(&pcd->lock);
	pcd->muram_used += size;
	if (pcd->muram_used > pcd->muram_high_water)
		pcd->muram_high_water = pcd->muram_used;
	mutex_unlock(&pcd->lock);

	return offset;
}

void fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long offset,
			 size_t size)
{
	struct muram_info *muram;

	if (WARN_ON(!pcd) || size == 0)
		return;

	muram = fman_get_muram(pcd->fman);
	if (WARN_ON(!muram))
		return;

	fman_muram_free_mem(muram, offset, size);

	mutex_lock(&pcd->lock);
	if (WARN_ON(pcd->muram_used < size))
		pcd->muram_used = 0;
	else
		pcd->muram_used -= size;
	mutex_unlock(&pcd->lock);
}

/**
 * fman_pcd_init() - initialise the PCD subsystem for an FMan instance.
 * @fman: parent FMan controller.  Must have a live MURAM pool (mainline
 *        fman_config() calls fman_muram_init() before fman_init(); the
 *        integration patch on fman.c calls fman_pcd_init() AFTER that).
 *
 * Allocates per-FMan PCD state, reserves the scratch MURAM partition,
 * initialises the per-engine list registries, creates the debugfs node.
 *
 * Return: valid handle on success; ERR_PTR(-errno) otherwise.  The
 * caller (fman_probe) is responsible for storing the returned pointer
 * on struct fman and arranging teardown via fman_pcd_release().
 */
struct fman_pcd *fman_pcd_init(struct fman *fman)
{
	struct fman_pcd *pcd;
	struct muram_info *muram;
	int err;

	if (!fman)
		return ERR_PTR(-EINVAL);

	muram = fman_get_muram(fman);
	if (!muram)
		return ERR_PTR(-ENXIO);

	pcd = kzalloc(sizeof(*pcd), GFP_KERNEL);
	if (!pcd)
		return ERR_PTR(-ENOMEM);

	pcd->fman = fman;
	pcd->muram_reserved = FMAN_PCD_MURAM_RESERVED_BYTES;

	mutex_init(&pcd->lock);
	INIT_LIST_HEAD(&pcd->kg_schemes);
	INIT_LIST_HEAD(&pcd->cc_trees);
	INIT_LIST_HEAD(&pcd->manip_profiles);
	INIT_LIST_HEAD(&pcd->plcr_profiles);
	INIT_LIST_HEAD(&pcd->replic_groups);

	pcd->muram_offset = fman_muram_alloc(muram,
					     FMAN_PCD_MURAM_RESERVED_BYTES);
	if (IS_ERR_VALUE(pcd->muram_offset)) {
		err = (int)pcd->muram_offset;
		dev_err(fman_get_dev(fman),
			"fman_pcd: cannot reserve %u bytes MURAM (err %d)\n",
			FMAN_PCD_MURAM_RESERVED_BYTES, err);
		goto err_free;
	}

	err = fman_pcd_debugfs_root_get();
	if (err) {
		dev_warn(fman_get_dev(fman),
			 "fman_pcd: debugfs root unavailable (err %d) - continuing\n",
			 err);
		/* non-fatal: PCD works without debugfs */
	} else {
		char name[16];

		snprintf(name, sizeof(name), "%d", fman_get_id(fman));
		pcd->debugfs_dir = debugfs_create_dir(name,
						      fman_pcd_debugfs_root);
		if (!IS_ERR_OR_NULL(pcd->debugfs_dir))
			debugfs_create_file("muram_budget", 0444,
					    pcd->debugfs_dir, pcd,
					    &fman_pcd_muram_budget_fops);
	}

	dev_info(fman_get_dev(fman),
		 "fman_pcd: ready (%u KiB MURAM reserved at offset 0x%lx)\n",
		 FMAN_PCD_MURAM_RESERVED_BYTES / 1024U,
		 pcd->muram_offset);

	return pcd;

err_free:
	mutex_destroy(&pcd->lock);
	kfree(pcd);
	return ERR_PTR(err);
}
EXPORT_SYMBOL_GPL(fman_pcd_init);

/**
 * fman_pcd_release() - tear down the PCD subsystem.
 * @pcd: handle previously returned by fman_pcd_init(). NULL-safe.
 *
 * Frees the MURAM partition, removes debugfs, frees the handle.  WARN
 * on any non-empty list - a consumer leaking a handle is a bug.  At
 * skeleton stage the lists are always empty so the warnings are
 * dormant; they go live as per-engine modules attach entries.
 */
void fman_pcd_release(struct fman_pcd *pcd)
{
	if (!pcd)
		return;

	if (!IS_ERR_OR_NULL(pcd->debugfs_dir))
		debugfs_remove_recursive(pcd->debugfs_dir);
	fman_pcd_debugfs_root_put();

	WARN_ON(!list_empty(&pcd->kg_schemes));
	WARN_ON(!list_empty(&pcd->cc_trees));
	WARN_ON(!list_empty(&pcd->manip_profiles));
	WARN_ON(!list_empty(&pcd->plcr_profiles));
	WARN_ON(!list_empty(&pcd->replic_groups));

	if (!IS_ERR_VALUE(pcd->muram_offset))
		fman_muram_free_mem(fman_get_muram(pcd->fman),
				    pcd->muram_offset,
				    pcd->muram_reserved);

	mutex_destroy(&pcd->lock);
	kfree(pcd);
}
EXPORT_SYMBOL_GPL(fman_pcd_release);

/**
 * fman_pcd_get_muram_budget() - return MURAM accounting snapshot.
 * @pcd: handle.  NULL-safe (returns zeroed struct).
 *
 * Consumed by the debugfs file and by cli op-mode.  Takes the PCD mutex
 * - not safe from atomic context.
 */
struct fman_pcd_muram_budget fman_pcd_get_muram_budget(struct fman_pcd *pcd)
{
	struct fman_pcd_muram_budget out = { 0 };

	if (!pcd)
		return out;

	mutex_lock(&pcd->lock);
	out.reserved_bytes   = pcd->muram_reserved;
	out.used_bytes       = pcd->muram_used;
	out.free_bytes       = pcd->muram_reserved - pcd->muram_used;
	out.high_water_bytes = pcd->muram_high_water;
	mutex_unlock(&pcd->lock);

	return out;
}
EXPORT_SYMBOL_GPL(fman_pcd_get_muram_budget);

/*
 * Internal accessors for per-engine siblings.  Not exported - same module.
 */
struct fman *fman_pcd_get_fman(struct fman_pcd *pcd)
{
	return pcd ? pcd->fman : NULL;
}

struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd)
{
	return pcd ? &pcd->lock : NULL;
}

struct list_head *fman_pcd_get_kg_list(struct fman_pcd *pcd)
{
	return pcd ? &pcd->kg_schemes : NULL;
}

struct list_head *fman_pcd_get_cc_list(struct fman_pcd *pcd)
{
	return pcd ? &pcd->cc_trees : NULL;
}

struct list_head *fman_pcd_get_manip_list(struct fman_pcd *pcd)
{
	return pcd ? &pcd->manip_profiles : NULL;
}

struct list_head *fman_pcd_get_plcr_list(struct fman_pcd *pcd)
{
	return pcd ? &pcd->plcr_profiles : NULL;
}

struct list_head *fman_pcd_get_replic_list(struct fman_pcd *pcd)
{
	return pcd ? &pcd->replic_groups : NULL;
}
