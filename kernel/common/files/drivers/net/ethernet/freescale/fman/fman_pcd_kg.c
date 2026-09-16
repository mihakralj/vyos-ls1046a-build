// SPDX-License-Identifier: GPL-2.0
/*
 * FMan PCD KeyGen (KG) - silicon-programming layer.
 *
 * Allocates a free KG scheme, populates keygen->schemes[scheme_id] from
 * the caller-supplied struct fman_pcd_kg_scheme_params, and delegates the
 * actual KGSE_* register programming to the existing in-tree helper
 * keygen_scheme_setup() (made EXPORT_SYMBOL_GPL by this same patch).
 *
 * Authoritative reference
 * -----------------------
 * - LS1046A Reference Manual sec 8.7.4 (KGSE_MV/MODE/EKDV/EKFC/CCBS/
 *   HC/FQB scheme registers).
 * - LS1046A Reference Manual sec 8.7.5 (KGSE extract record format,
 *   EKDV slot layout, hash configuration).
 * - LS1046A Reference Manual sec 8.4 (FMan port numbering: 1-8 1G,
 *   9-10 10G; port 0 is the offline-parsing port and is never a
 *   valid KG match-vector target).
 * - drivers/net/ethernet/freescale/fman/fman_keygen.c in this same
 *   build tree provides the silicon-fact register names + bit
 *   layouts (KG_SCH_*, KG_SCH_KN_*, KG_SCH_HASH_CONFIG_SHIFT_SHIFT)
 *   and the existing helpers we delegate to.
 *
 * Locking model
 * -------------
 * pcd->lock (acquired via fman_pcd_get_lock()) serialises scheme-id
 * allocation, list mutation, and the keygen_scheme_setup() write
 * path.  The fast path does NOT enter this layer per packet - the
 * hardware does the lookup directly against the MURAM-resident table
 * programmed by the slow-path callers.
 *
 * Coarse Classification chaining
 * ------------------------------
 * fman_pcd_kg_attach_cc() flips a scheme into CC next-engine mode by
 * rewriting KGSE_MODE (NIA_FM_CTL_AC_CC + CCOBASE) and KGSE_CCBS via
 * keygen_scheme_setup().  The CC tree-root MURAM base is programmed
 * separately at the BMI RX port (FMBM_RCCB) by fman_port_set_cc_base().
 */

#include <linux/err.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/types.h>

#include <linux/fsl/fman_pcd.h>
#include "fman.h"
#include "fman_keygen_internal.h"
#include "fman_pcd_internal.h"

/*
 * KeyGen scheme match-vector silicon bits (RM §8.7.4, KGSE_MV register).
 * These are private to fman_keygen.c upstream; replicated here as silicon
 * constants for the public PCD KG API. Do NOT change values — they are
 * hardware-fixed.
 */
#define KG_SCH_KN_PTYPE1	0x00040000
#define KG_SCH_KN_IPSRC1	0x00100000
#define KG_SCH_KN_IPDST1	0x00080000
#define KG_SCH_KN_L4PSRC	0x00000004
#define KG_SCH_KN_L4PDST	0x00000002

/*
 * KG extract-source enumeration that mirrors the silicon-side
 * KGSE_EKDV slot encoding documented in RM sec 8.7.5 Table 8-105.
 * Our public enum is layered on top: consumers supply values from
 * enum fman_pcd_kg_extract_from in <linux/fsl/fman_pcd.h>; we
 * translate them here.
 */
static int kg_extract_src_valid(enum fman_pcd_kg_extract_from src)
{
	switch (src) {
	case FMAN_PCD_KG_EXTRACT_FROM_FRAME:
	case FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT:
	case FMAN_PCD_KG_EXTRACT_FROM_DEFAULT:
		return 0;
	}
	return -EINVAL;
}

/*
 * Per-scheme bookkeeping kept on the pcd->kg_schemes list.  Adds the
 * parent pcd back-pointer (needed by scheme_destroy to splice ourselves
 * out), the silicon-allocated scheme id, and a small cache of the
 * extract spec the caller supplied.  The handle remains opaque to
 * consumers - <linux/fsl/fman_pcd.h> only forward-declares this struct.
 */
struct fman_pcd_kg_scheme {
	struct list_head node;
	struct fman_pcd *pcd;
	u8 id;
	u8 hw_port_id;		/* 0 = unbound; set by bind_port */
	bool bound;
	u8 num_extracts;
	struct fman_pcd_kg_extract_spec extracts[8];
	u32 default_fqid;
	bool use_hash;
	u8 next_engine;		/* 0 = BMI enqueue, 1 = policer, 2 = CC */
	u8 policer_profile_id;	/* valid when next_engine == 1 */
};

/*
 * Allocate the next free scheme id.  Caller must hold pcd->lock.
 *
 * Semantics:
 *   - @params->id >= 0: caller wants a specific slot; succeed only
 *     if it is currently free.
 *   - @params->id < 0:  first-fit allocation in [0, FM_KG_MAX_NUM_OF_SCHEMES).
 *
 * Returns the allocated id in [0, FM_KG_MAX_NUM_OF_SCHEMES) on
 * success or a negative errno on failure.  Does NOT mark the slot
 * used - keygen_scheme_setup() sets keygen->schemes[id].used itself.
 */
static int kg_alloc_scheme_id(struct fman_keygen *keygen,
			      const struct fman_pcd_kg_scheme_params *params)
{
	int i;

	if (params->id >= 0) {
		if (params->id >= FM_KG_MAX_NUM_OF_SCHEMES)
			return -EINVAL;
		if (keygen->schemes[params->id].used)
			return -EBUSY;
		return params->id;
	}

	for (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++)
		if (!keygen->schemes[i].used)
			return i;

	return -ENOSPC;
}

/*
 * Translate the caller's extract spec to a silicon match-vector.
 * RM sec 8.7.5 documents the KGSE_KN (Key Number) bits; the
 * KG_SCH_KN_* constants above hold the silicon bit positions.  We
 * support the IPv4 5-tuple shape (SIP, DIP, proto, SPORT, DPORT).
 * Wider shapes (IPv6, MPLS, IPSEC) extend this function in follow-ups
 * - they all map to bits that already exist in fman_keygen.c.
 *
 * Returns 0 + writes to *out on success, or a negative errno.
 */
static int kg_build_match_vector(const struct fman_pcd_kg_scheme_params *p,
				 u32 *out)
{
	u32 mv = 0;
	u8 i;

	for (i = 0; i < p->num_extracts; i++) {
		const struct fman_pcd_kg_extract_spec *e = &p->extracts[i];

		if (kg_extract_src_valid(e->src))
			return -EINVAL;
		if (e->size == 0 || e->size > 16)
			return -EINVAL;
		if (e->src == FMAN_PCD_KG_EXTRACT_FROM_PARSE_RESULT &&
		    e->offset > 63)
			return -EINVAL;
		if (e->src == FMAN_PCD_KG_EXTRACT_FROM_FRAME &&
		    e->offset > 127)
			return -EINVAL;

		/*
		 * Synthetic mapping: we only honour the five canonical
		 * IPv4 5-tuple slots.  This is sufficient for an IPv4-only
		 * first cut; the full opcode table can be wired up in a
		 * follow-up once we have a real test harness.
		 */
		switch (e->offset) {
		case 12:  /* parse_result.IPv4 SIP */
			mv |= KG_SCH_KN_IPSRC1;
			break;
		case 16:  /* parse_result.IPv4 DIP */
			mv |= KG_SCH_KN_IPDST1;
			break;
		case 9:   /* parse_result.IPv4 protocol */
			mv |= KG_SCH_KN_PTYPE1;
			break;
		case 20:  /* parse_result.L4 SPORT */
			mv |= KG_SCH_KN_L4PSRC;
			break;
		case 22:  /* parse_result.L4 DPORT */
			mv |= KG_SCH_KN_L4PDST;
			break;
		default:
			/* Unrecognised offset is surfaced loudly rather
			 * than silently producing an unexpected lookup.
			 */
			return -EOPNOTSUPP;
		}
	}

	if (mv == 0)
		return -EINVAL;

	*out = mv;
	return 0;
}

struct fman_pcd_kg_scheme *
fman_pcd_kg_scheme_create(struct fman_pcd *pcd,
			  const struct fman_pcd_kg_scheme_params *params)
{
	struct fman_pcd_kg_scheme *scheme;
	struct fman_keygen *keygen;
	struct keygen_scheme *slot;
	struct fman *fman;
	struct mutex *lock;
	u32 match_vector;
	int id, err;

	if (!pcd || !params)
		return ERR_PTR(-EINVAL);
	if (params->num_extracts > 8)
		return ERR_PTR(-EINVAL);
	/*
	 * num_extracts == 0 is only valid for a policer-pass-through
	 * "catch-all" scheme: no key is extracted, match_vector is left 0 so
	 * every frame on the bound port selects this scheme, and the frame is
	 * handed straight to the policer (next_engine == PLCR). An ENQUEUE
	 * scheme with no extracts would have nothing to match on and is
	 * rejected.
	 */
	if (params->num_extracts == 0 &&
	    params->next_engine != FMAN_PCD_KG_NEXT_ENGINE_PLCR)
		return ERR_PTR(-EINVAL);
	/*
	 * default_fqid is dispatched-to only when use_hash == false.  When
	 * the scheme is in hash mode the silicon walks the hash-FQID base
	 * register and the per-bucket index, leaving slot->base_fqid as a
	 * write-but-never-read field.  Reject default_fqid == 0 only in the
	 * non-hash case so an IPv4 5-tuple scheme (use_hash = true,
	 * default_fqid = 0) is accepted.
	 */
	if (!params->use_hash && params->default_fqid == 0)
		return ERR_PTR(-EINVAL);
	if (params->default_fqid >= (1U << 24))
		return ERR_PTR(-EINVAL);

	fman = fman_pcd_get_fman(pcd);
	if (!fman || !fman->keygen)
		return ERR_PTR(-ENXIO);
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(pcd);

	if (params->num_extracts == 0) {
		/*
		 * Catch-all policer-pass-through scheme: no key extraction.
		 * match_vector = 0 means the scheme imposes no parser-result
		 * match requirement, so every frame on the bound port selects
		 * it (same "match everything" semantics the RSS-hashing path
		 * relies on).
		 */
		match_vector = 0;
	} else {
		err = kg_build_match_vector(params, &match_vector);
		if (err)
			return ERR_PTR(err);
	}

	scheme = kzalloc(sizeof(*scheme), GFP_KERNEL);
	if (!scheme)
		return ERR_PTR(-ENOMEM);

	mutex_lock(lock);
	id = kg_alloc_scheme_id(keygen, params);
	if (id < 0) {
		err = id;
		goto err_unlock;
	}

	/*
	 * Populate the silicon-side scheme slot.  The existing
	 * keygen_scheme_setup() helper reads from here.  hw_port_id
	 * stays 0 until bind_port wires us up; setup() does not
	 * dispatch frames into a scheme until match_vector AND the
	 * port-binding ar register both reference it.
	 */
	slot = &keygen->schemes[id];
	memset(slot, 0, sizeof(*slot));
	slot->match_vector = match_vector;
	slot->base_fqid = params->default_fqid;
	slot->use_hashing = params->use_hash;
	slot->next_engine =
		(params->next_engine == FMAN_PCD_KG_NEXT_ENGINE_PLCR) ? 1 : 0;
	slot->policer_profile_id = params->policer_profile_id;
	if (params->use_hash) {
		/* Conservative defaults for the first cut.  Field-level
		 * tuning (hash_fqid_count, symmetric_hash, hashShift)
		 * becomes a public API parameter if/when a consumer needs
		 * more than one FQID per scheme.
		 */
		slot->hash_fqid_count = 1;
		slot->symmetric_hash = true;
		slot->hashShift = 0;
	}

	err = keygen_scheme_setup(keygen, (u8)id, true);
	if (err) {
		memset(slot, 0, sizeof(*slot));
		goto err_unlock;
	}

	scheme->pcd          = pcd;
	scheme->id           = (u8)id;
	scheme->hw_port_id   = 0;
	scheme->bound        = false;
	scheme->num_extracts = params->num_extracts;
	memcpy(scheme->extracts, params->extracts,
	       params->num_extracts * sizeof(scheme->extracts[0]));
	scheme->default_fqid = params->default_fqid;
	scheme->use_hash     = params->use_hash;
	scheme->next_engine  =
		(params->next_engine == FMAN_PCD_KG_NEXT_ENGINE_PLCR) ? 1 : 0;
	scheme->policer_profile_id = params->policer_profile_id;
	INIT_LIST_HEAD(&scheme->node);
	list_add_tail(&scheme->node, fman_pcd_get_kg_list(pcd));

	mutex_unlock(lock);
	return scheme;

err_unlock:
	mutex_unlock(lock);
	kfree(scheme);
	return ERR_PTR(err);
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_scheme_create);

int fman_pcd_kg_bind_port(struct fman_pcd_kg_scheme *scheme, u8 port_id)
{
	struct fman_keygen *keygen;
	struct keygen_scheme *slot;
	struct fman *fman;
	struct mutex *lock;
	int err;

	if (!scheme)
		return -EINVAL;
	if (port_id < 0x08 || port_id >= 0x28)
		return -EINVAL;

	fman = fman_pcd_get_fman(scheme->pcd);
	if (!fman || !fman->keygen)
		return -ENXIO;
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(scheme->pcd);

	mutex_lock(lock);
	if (scheme->bound) {
		mutex_unlock(lock);
		return -EBUSY;
	}

	slot = &keygen->schemes[scheme->id];
	slot->hw_port_id = port_id;

	err = keygen_bind_port_to_schemes(keygen, scheme->id, true);
	if (err) {
		slot->hw_port_id = 0;
		mutex_unlock(lock);
		return err;
	}

	scheme->hw_port_id = port_id;
	scheme->bound      = true;
	mutex_unlock(lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_bind_port);

int fman_pcd_kg_attach_cc(struct fman_pcd_kg_scheme *scheme,
			  struct fman_pcd_cc_tree *cc)
{
	struct keygen_scheme *slot;
	struct fman *fman;
	struct fman_keygen *keygen;
	struct mutex *lock;
	int err;

	if (!scheme || !cc)
		return -EINVAL;

	fman = fman_pcd_get_fman(scheme->pcd);
	if (!fman || !fman->keygen)
		return -ENXIO;
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(scheme->pcd);

	/*
	 * Chain this KeyGen scheme into the installed Coarse Classification
	 * tree.  The scheme's hash output indexes the CC group table; the
	 * matched AD result then carries the destination RX FQID.  For the
	 * static single-group/single-entry tree (board patch 0098) both the
	 * CCOBASE group index and the CCBS hash-bit selection are 0.
	 *
	 * The CC tree-root MURAM base is programmed separately at the BMI RX
	 * port (FMBM_RCCB) via fman_port_set_cc_base(); this routine only
	 * flips the KeyGen scheme into CC next-engine mode.
	 *
	 * Same pattern as the policer (next_engine == 1) path: mutate the
	 * internal slot then re-run keygen_scheme_setup(enable = true) under
	 * pcd->lock to rewrite KGSE_MODE/KGSE_CCBS.  Permissive: the scheme
	 * need not be port-bound first.
	 */
	mutex_lock(lock);
	slot = &keygen->schemes[scheme->id];
	slot->next_engine    = 2;
	slot->cc_base_offset = 0;
	slot->cc_bits_sel    = 0;

	err = keygen_scheme_setup(keygen, (u8)scheme->id, true);
	if (err) {
		/* Roll back to the BMI-enqueue default on failure. */
		slot->next_engine    = 0;
		slot->cc_base_offset = 0;
		slot->cc_bits_sel    = 0;
		(void)keygen_scheme_setup(keygen, (u8)scheme->id, true);
		mutex_unlock(lock);
		return err;
	}

	scheme->next_engine = 2;
	mutex_unlock(lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_attach_cc);

void fman_pcd_kg_scheme_destroy(struct fman_pcd_kg_scheme *scheme)
{
	struct fman_keygen *keygen;
	struct fman *fman;
	struct mutex *lock;

	if (!scheme)
		return;

	fman = fman_pcd_get_fman(scheme->pcd);
	if (!fman || !fman->keygen) {
		/*
		 * Probe-failure tear-down can land here if the FMan
		 * keygen has already been freed.  Drop the wrapper
		 * without touching silicon - the parent FMan unbind
		 * path will reset KGSE_* anyway.
		 */
		kfree(scheme);
		return;
	}
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(scheme->pcd);

	mutex_lock(lock);
	if (scheme->bound)
		(void)keygen_bind_port_to_schemes(keygen, scheme->id, false);
	(void)keygen_scheme_setup(keygen, scheme->id, false);
	list_del(&scheme->node);
	mutex_unlock(lock);

	kfree(scheme);
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_scheme_destroy);

/*
 * Locate the lowest-id KG scheme currently bound to @hw_port_id.  Caller
 * must hold pcd->lock.  Every RX port is initialised with exactly one
 * hard-coded RSS-hashing scheme (keygen_port_hashing_init()), and the
 * silicon's scheme-select dispatches each frame to the lowest-id scheme
 * whose port-binding matches - so this returns the slot the datapath
 * actually uses.  Returns NULL (and leaves *id_out untouched) if the port
 * has no bound scheme.
 */
static struct keygen_scheme *
kg_find_port_scheme(struct fman_keygen *keygen, u8 hw_port_id, u8 *id_out)
{
	int i;

	for (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {
		if (keygen->schemes[i].used &&
		    keygen->schemes[i].hw_port_id == hw_port_id) {
			*id_out = (u8)i;
			return &keygen->schemes[i];
		}
	}
	return NULL;
}

int fman_pcd_kg_port_attach_policer(struct fman_pcd *pcd, u8 hw_port_id,
				    u8 profile_id)
{
	struct fman_keygen *keygen;
	struct keygen_scheme *slot;
	struct fman *fman;
	struct mutex *lock;
	u8 saved_engine, saved_profile, id;
	int err;

	if (!pcd)
		return -EINVAL;
	if (hw_port_id < 0x08 || hw_port_id >= 0x28)
		return -EINVAL;

	fman = fman_pcd_get_fman(pcd);
	if (!fman || !fman->keygen)
		return -ENXIO;
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(pcd);

	mutex_lock(lock);

	slot = kg_find_port_scheme(keygen, hw_port_id, &id);
	if (!slot) {
		mutex_unlock(lock);
		return -ENODEV;
	}

	saved_engine  = slot->next_engine;
	saved_profile = slot->policer_profile_id;

	slot->next_engine        = 1;
	slot->policer_profile_id = profile_id;

	/*
	 * keygen_scheme_setup() rejects enable on an already-used scheme
	 * (-EINVAL), so clear ->used for the in-place reprogram; setup() sets
	 * it back to true on success.  use_hashing / base_fqid / match_vector
	 * are untouched, so the scheme keeps spreading conforming frames over
	 * the same hashed RX FQs while the next-engine now points at the
	 * policer profile.
	 */
	slot->used = false;
	err = keygen_scheme_setup(keygen, id, true);
	if (err) {
		/* Restore the previous next-engine on failure. */
		slot->next_engine        = saved_engine;
		slot->policer_profile_id = saved_profile;
		slot->used = false;
		(void)keygen_scheme_setup(keygen, id, true);
		mutex_unlock(lock);
		return err;
	}

	mutex_unlock(lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_policer);

int fman_pcd_kg_port_detach_policer(struct fman_pcd *pcd, u8 hw_port_id)
{
	struct fman_keygen *keygen;
	struct keygen_scheme *slot;
	struct fman *fman;
	struct mutex *lock;
	u8 id;
	int err;

	if (!pcd)
		return -EINVAL;
	if (hw_port_id < 0x08 || hw_port_id >= 0x28)
		return -EINVAL;

	fman = fman_pcd_get_fman(pcd);
	if (!fman || !fman->keygen)
		return -ENXIO;
	keygen = fman->keygen;
	lock = fman_pcd_get_lock(pcd);

	mutex_lock(lock);

	slot = kg_find_port_scheme(keygen, hw_port_id, &id);
	if (!slot) {
		/* Nothing bound: detach is a no-op (idempotent teardown). */
		mutex_unlock(lock);
		return 0;
	}

	slot->next_engine        = 0;
	slot->policer_profile_id = 0;

	slot->used = false;
	err = keygen_scheme_setup(keygen, id, true);
	mutex_unlock(lock);
	return err;
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_port_detach_policer);
