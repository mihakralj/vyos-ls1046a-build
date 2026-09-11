/* SPDX-License-Identifier: GPL-2.0 */
/*
 * include/linux/fsl/fman_pcd.h - Public API of the FMan PCD subsystem.
 *
 * Authoritative reference: LS1046A Reference Manual chapter 8 (RM 8.7-8.10).
 *
 * Consumers call into this API to install exact-match flow rules at line
 * rate.  All function calls are process-context (use a mutex inside) and
 * not atomic-safe.  The fast path (per-packet) does not go through this
 * API - hardware does the lookup directly against the MURAM-resident
 * tables programmed by these calls.
 *
 * What this header exposes
 * ------------------------
 * Only the orchestration surface: subsystem init/release and MURAM
 * accounting.  The KG/CC/manip/plcr/parser/replicator APIs land as the
 * corresponding .c files do.  Forward declarations are placed here so
 * that #include <linux/fsl/fman_pcd.h> from consumers remains stable
 * across the per-engine series.
 */

#ifndef __LINUX_FSL_FMAN_PCD_H
#define __LINUX_FSL_FMAN_PCD_H

#include <linux/types.h>

struct fman;

/*
 * ABI versioning macro.  Bumped when the public surface changes in an
 * incompatible way.  Consumers can #if-guard against this.
 */
#define FMAN_PCD_API_VERSION	1

/*
 * Opaque handles.  All defined in their respective .c files.
 */
struct fman_pcd;
struct fman_pcd_kg_scheme;
struct fman_pcd_cc_tree;
struct fman_pcd_cc_node;
struct fman_pcd_manip;
struct fman_pcd_plcr_profile;
struct fman_pcd_replic_group;

/**
 * struct fman_pcd_muram_budget - MURAM accounting snapshot.
 * @reserved_bytes:   total bytes reserved at fman_pcd_init() time
 * @used_bytes:       bytes currently allocated to schemes/trees/profiles
 * @free_bytes:       reserved - used (recomputed under lock for atomicity)
 * @high_water_bytes: peak value of used_bytes since init
 *
 * Small, cheap-to-copy struct returned by value from
 * fman_pcd_get_muram_budget().  Consumed by debugfs muram_budget and by
 * cli op-mode.
 */
struct fman_pcd_muram_budget {
	size_t reserved_bytes;
	size_t used_bytes;
	size_t free_bytes;
	size_t high_water_bytes;
};

/*
 * Orchestration API.
 *
 * fman_pcd_init() is called by fman_probe() after fman_muram_init().
 * fman_pcd_release() is called via devm_add_action_or_reset() on
 * unbind/probe-failure.  Consumers should retrieve the per-FMan handle
 * with fman_get_pcd() (declared in the fman driver's fman.h via the
 * integration patch on fman.c).
 */
struct fman_pcd *fman_pcd_init(struct fman *fman);
void fman_pcd_release(struct fman_pcd *pcd);
struct fman_pcd_muram_budget fman_pcd_get_muram_budget(struct fman_pcd *pcd);

#endif /* __LINUX_FSL_FMAN_PCD_H */
