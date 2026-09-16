/* SPDX-License-Identifier: GPL-2.0 */
/*
 * NXP FMan PCD subsystem - private intra-module API.
 *
 * Declarations for helpers exported from fman_pcd.c to its per-engine
 * sibling translation units (fman_pcd_kg.c, fman_pcd_cc.c,
 * fman_pcd_manip.c, fman_pcd_plcr.c, fman_pcd_replic.c). All link
 * together into fsl_dpaa_fman.ko, so no EXPORT_SYMBOL is needed and
 * these symbols are NOT visible to out-of-tree modules. The public ABI
 * lives in <linux/fsl/fman_pcd.h>.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#ifndef __FMAN_PCD_INTERNAL_H
#define __FMAN_PCD_INTERNAL_H

#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/types.h>

struct fman;
struct fman_pcd;

/* MURAM accounting wrappers.  Best-effort accounting; gen_pool is
 * authoritative.  Return value follows fman_muram_alloc() convention:
 * an unsigned long offset, or IS_ERR_VALUE() negative errno cast.
 */
unsigned long fman_pcd_muram_alloc(struct fman_pcd *pcd, size_t size);
void fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long offset,
			 size_t size);

/* Container accessors.  Sibling files use these instead of dereferencing
 * struct fman_pcd directly (the struct definition is intentionally kept
 * private to fman_pcd.c).
 */
struct fman *fman_pcd_get_fman(struct fman_pcd *pcd);
struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd);
struct list_head *fman_pcd_get_kg_list(struct fman_pcd *pcd);
struct list_head *fman_pcd_get_cc_list(struct fman_pcd *pcd);
struct list_head *fman_pcd_get_manip_list(struct fman_pcd *pcd);
struct list_head *fman_pcd_get_plcr_list(struct fman_pcd *pcd);
struct list_head *fman_pcd_get_replic_list(struct fman_pcd *pcd);

#endif /* __FMAN_PCD_INTERNAL_H */
