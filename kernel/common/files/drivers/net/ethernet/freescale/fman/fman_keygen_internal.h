/* SPDX-License-Identifier: GPL-2.0 */
/*
 * FMan KeyGen — intra-module internal header.
 *
 * Promotes the previously file-local types `struct keygen_scheme` and
 * `struct fman_keygen` to module-internal scope so that sibling
 * translation units within fsl_dpaa_fman.ko (specifically fman_pcd_kg.c
 * added in the PCD KeyGen patch) can populate a scheme slot before
 * calling the now-non-static keygen_scheme_setup() /
 * keygen_bind_port_to_schemes() helpers.
 *
 * Not exported via include/linux/fsl/ — this header is fsl_dpaa_fman.ko-
 * internal and must NOT be consumed from outside the module. Out-of-tree
 * consumers go through the public fman_pcd_kg_* surface in
 * include/linux/fsl/fman_pcd.h.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#ifndef __FMAN_KEYGEN_INTERNAL_H
#define __FMAN_KEYGEN_INTERNAL_H

#include <linux/io.h>
#include <linux/types.h>

#include "fman_keygen.h"

/* Forward decl; full body lives in fman_keygen.c. */
struct fman_kg_regs;

/* Maximum number of KeyGen Schemes (FMan v3 silicon, RM §8.7.4).
 * Promoted here from fman_keygen.c so the struct fman_keygen array
 * dimension below is visible to sibling translation units.
 */
#ifndef FM_KG_MAX_NUM_OF_SCHEMES
#define FM_KG_MAX_NUM_OF_SCHEMES	32
#endif

/* Per-scheme bookkeeping. Mirrors the file-static definition that used
 * to live in fman_keygen.c — moved here verbatim so fman_pcd_kg.c can
 * populate match_vector ≠ 0 for exact-match flow lookup (RM §8.7.3),
 * not just the match_vector = 0 RSS-hashing case that
 * keygen_port_hashing_init() programs.
 */
struct keygen_scheme {
	bool used;	/* Specifies if this scheme is used */
	u8 hw_port_id;
		/* Hardware port ID
		 * schemes sharing between multiple ports is not
		 * currently supported
		 * so we have only one port id bound to a scheme
		 */
	u32 base_fqid;
		/* Base FQID:
		 * Must be between 1 and 2^24-1
		 * If hash is used and an even distribution is
		 * expected according to hash_fqid_count,
		 * base_fqid must be aligned to hash_fqid_count
		 */
	u32 hash_fqid_count;
		/* FQ range for hash distribution:
		 * Must be a power of 2
		 * Represents the range of queues for spreading
		 */
	bool use_hashing;	/* Usage of Hashing and spreading over FQ */
	bool symmetric_hash;	/* Symmetric Hash option usage */
	u8 hashShift;
		/* Hash result right shift.
		 * Select the 24 bits out of the 64 hash result.
		 * 0 means using the 24 LSB's, otherwise
		 * use the 24 LSB's after shifting right
		 */
	u32 match_vector;	/* Match Vector */
	u8 next_engine;
		/* Post-KeyGen next-action engine for this scheme:
		 *   0 = BMI enqueue (ENQUEUE_KG_DFLT_NIA, the default)
		 *   1 = Policer    (NIA_ENG_PLCR, KGSE_PPC = policer_profile_id)
		 *   2 = Coarse Classification (NIA_FM_CTL_AC_CC,
		 *       KGSE_MODE CCOBASE = cc_base_offset,
		 *       KGSE_CCBS = cc_bits_sel)
		 * Mirrors enum fman_pcd_kg_next_engine in the public header;
		 * kept as a plain u8 here so this module-internal header does
		 * not depend on <linux/fsl/fman_pcd.h>.
		 */
	u8 policer_profile_id;	/* Absolute policer profile id, valid when
				 * next_engine == 1 (programmed into KGSE_PPC).
				 */
	u32 cc_base_offset;	/* CC group-table base index (CCOBASE),
				 * valid when next_engine == 2. 0 for the
				 * static single-group tree.
				 */
	u32 cc_bits_sel;	/* CC hash-bit selection (KGSE_CCBS), valid
				 * when next_engine == 2. 0 for the static
				 * single-entry tree.
				 */
};

/* KeyGen driver data. Mirrors the file-static definition formerly in
 * fman_keygen.c.
 */
struct fman_keygen {
	struct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES];
	struct fman_kg_regs __iomem *keygen_regs;
};

/* Demoted from file-static in fman_keygen.c. Both EXPORT_SYMBOL_GPL.
 * fman_pcd_kg.c calls these after populating a scheme slot with
 * match_vector ≠ 0.
 *
 *   keygen_scheme_setup(keygen, scheme_id, enable):
 *     Writes the populated keygen->schemes[scheme_id] state out to
 *     silicon via the FMan KeyGen Action Register protocol. @enable
 *     toggles the scheme's valid bit.
 *
 *   keygen_bind_port_to_schemes(keygen, scheme_id, bind):
 *     Programs the FMan KG schemes-bind vector so that the hardware
 *     port saved in keygen->schemes[scheme_id].hw_port_id dispatches
 *     into this scheme. @bind=true to bind, false to unbind.
 */
int keygen_scheme_setup(struct fman_keygen *keygen, u8 scheme_id,
			bool enable);
int keygen_bind_port_to_schemes(struct fman_keygen *keygen, u8 scheme_id,
				bool bind);

#endif /* __FMAN_KEYGEN_INTERNAL_H */
