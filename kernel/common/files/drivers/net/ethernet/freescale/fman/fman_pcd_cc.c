// SPDX-License-Identifier: GPL-2.0
/*
 * FMan PCD Coarse Classifier (CC) - static-tree silicon programming layer.
 *
 * dpaa1 forward-port (PR3 of the PCD series closing M3-3b).  Re-anchors the
 * ask20 CC-tree STATIC MURAM programming path onto the 0092 PCD subsystem +
 * 0097 KeyGen base, adapted to the dpaa1 flat-C steering API
 * (dpaa_fman_caps.c) instead of the ask20 dynamic handle model.
 *
 * What this file delivers
 * -----------------------
 * - struct fman_pcd_cc_tree (private; the handle 0097's
 *   fman_pcd_kg_attach_cc() forward-references through <linux/fsl/fman_pcd.h>).
 * - A per-port static tree registry on pcd->cc_trees.
 * - fman_pcd_cc_static_install(): encodes a normalised struct
 *   fman_pcd_cc_hw_spec into a MURAM match-key table + AD table + the tree
 *   group-table[0] CONT_LOOKUP entry per LS1046A RM 8.7.4.1, then registers
 *   the tree on pcd->cc_trees so fman_pcd_release() tears it down on unbind.
 * - fman_pcd_cc_static_destroy(): reverse teardown, frees MURAM.
 *
 * What this file does NOT deliver
 * -------------------------------
 * The dynamic add_key/remove_key lifecycle is intentionally absent here.  On
 * the Mono Gateway DK the loaded 210.10.1 QEF blob does NOT implement the
 * Host Command doorbell (FMAN_CAP_HC_DISPATCH stays clear; board reports
 * caps = 0x17), so the dpaa-side fman_cc_tree_add_key()/remove_key() remain
 * -ENOTSUPP.  Only the static install path is productive.  See spec sec 3.5
 * (HC-off hardware fact, PR13 2026-05-13).
 *
 * Authoritative reference
 * -----------------------
 * - LS1046A Reference Manual sec 8.7.4   (Coarse Classifier registers).
 * - LS1046A Reference Manual sec 8.7.4.1 (CC tree group table; CONT_LOOKUP
 *   action-descriptor word layout).
 * - LS1046A Reference Manual sec 8.7.4.2 (CC node match-key record format).
 * - LS1046A Reference Manual sec 8.7.4.3 (action template encoding).
 *
 * Provenance: mainline drivers/net/ethernet/freescale/fman/ has zero
 * classifier code; authored from the RM + the existing in-tree
 * fman_muram.c / fman_pcd.c register and allocator layouts only.  The ask20
 * fman_pcd_cc.c (PR14c series) is a structural cross-reference for the
 * MURAM dual-API pattern; no SDK code is copied.
 *
 * Locking: pcd->lock (via fman_pcd_get_lock()) serialises the per-port
 * registry mutation + MURAM budget accounting.  The fast path never enters
 * this layer — the CC walker reads the MURAM-resident tables directly.
 */

#include <linux/err.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/types.h>

#include <linux/fsl/fman_pcd.h>
#include "fman.h"
#include "fman_muram.h"
#include "fman_pcd_internal.h"

/*
 * Silicon constants (LS1046A RM 8.7.4.1 / 8.7.4.2 / 8.7.4.3).
 *
 * CC_GROUP_TABLE_SZ : the tree group table is a fixed 16-entry x 16-byte
 *                     record (256 B) regardless of populated fanout; unused
 *                     entries are zero = no-match.  gen_pool min_alloc_order
 *                     = 8 satisfies the 256-byte alignment naturally.
 * CC_AD_ENTRY_SIZE  : bytes per action descriptor.
 * CC_KEY_SIZE       : our fixed extracted-key width.  We extract a canonical
 *                     16-byte composite (ethertype/proto/IP/ports) packed
 *                     big-endian; the per-key match record is key+mask =
 *                     2 * CC_KEY_SIZE bytes.  RM 8.7.4.2 caps extract.size at
 *                     1..56; 16 is comfortably within range.
 */
#define CC_GROUP_TABLE_SZ	256
#define CC_AD_ENTRY_SIZE	16
#define CC_KEY_SIZE		16

/* CONT_LOOKUP action-descriptor words (RM 8.7.4.1). */
#define CC_AD_W2_CONT_LOOKUP	0x40000000u

/**
 * struct fman_pcd_cc_tree - one port's static CC tree handle (private).
 * @node:          anchor on pcd->cc_trees (protected by pcd->lock)
 * @pcd:           owning PCD subsystem (MURAM-free back-pointer)
 * @port_id:       FMan port this tree serves (registry key)
 * @num_keys:      populated key count
 * @group_off:     MURAM offset of the 256-byte group table
 * @match_off:     MURAM offset of the (num_keys+1) * 2 * CC_KEY_SIZE
 *                 match+mask record table
 * @ad_off:        MURAM offset of the (num_keys+1) * CC_AD_ENTRY_SIZE AD table
 * @match_sz:      byte size of the match table (for free)
 * @ad_sz:         byte size of the AD table (for free)
 * @group:         CPU iomem pointer for the group table
 * @match:         CPU iomem pointer for the match table
 * @ad:            CPU iomem pointer for the AD table
 *
 * Public ABI forward-declares this in <linux/fsl/fman_pcd.h>.
 */
struct fman_pcd_cc_tree {
	struct list_head node;
	struct fman_pcd *pcd;
	u8 port_id;
	u16 num_keys;
	unsigned long group_off;
	unsigned long match_off;
	unsigned long ad_off;
	size_t match_sz;
	size_t ad_sz;
	void __iomem *group;
	void __iomem *match;
	void __iomem *ad;
};

/* Find an installed tree for @port_id.  Caller holds pcd->lock. */
static struct fman_pcd_cc_tree *cc_find_locked(struct fman_pcd *pcd, u8 port_id)
{
	struct list_head *head = fman_pcd_get_cc_list(pcd);
	struct fman_pcd_cc_tree *t;

	if (!head)
		return NULL;
	list_for_each_entry(t, head, node)
		if (t->port_id == port_id)
			return t;
	return NULL;
}

/*
 * Pack one normalised key into a CC_KEY_SIZE-byte big-endian composite +
 * its mask, written into the match record at row @idx.  The composite layout
 * is fixed (it mirrors the parser extract the install programs):
 *
 *   bytes 0..1   ethertype (BE)
 *   byte  2      proto
 *   byte  3      flags (bit0 = is_ipv6)
 *   bytes 4..7   IPv4 src (BE)   [or first 4 of IPv6 src for v6]
 *   bytes 8..11  IPv4 dst (BE)   [or first 4 of IPv6 dst for v6]
 *   bytes 12..13 L4 src port (BE)
 *   bytes 14..15 L4 dst port (BE)
 *
 * The mask row mirrors the same layout: 0xff for participating bytes, 0x00
 * for wildcard.  This is sufficient for the IPv4 5-tuple shape the dpaa1
 * default/vpp consumers use; IPv6 full-address matching is a follow-up that
 * widens CC_KEY_SIZE (handled loudly: an IPv6 key still matches its first 4
 * address bytes here, which the dpaa-side translator flags).
 */
static void cc_pack_key(void __iomem *match, u16 idx,
			const struct fman_pcd_cc_hw_key *k)
{
	u8 key[CC_KEY_SIZE];
	u8 msk[CC_KEY_SIZE];
	u8 *row;

	memset(key, 0, sizeof(key));
	memset(msk, 0, sizeof(msk));

	if (k->present & FMAN_PCD_CC_HW_F_ETHERTYPE) {
		key[0] = (u8)(k->ethertype_be & 0xff);
		key[1] = (u8)(k->ethertype_be >> 8);
		msk[0] = msk[1] = 0xff;
	}
	if (k->present & FMAN_PCD_CC_HW_F_PROTO) {
		key[2] = k->proto;
		msk[2] = 0xff;
	}
	if (k->present & FMAN_PCD_CC_HW_F_IPV6)
		key[3] |= 0x01;
	/* flags byte is structural, always exact-matched */
	msk[3] = 0xff;

	if (k->present & FMAN_PCD_CC_HW_F_SRC_IP) {
		if (k->present & FMAN_PCD_CC_HW_F_IPV6)
			memcpy(&key[4], &k->src_ip6[0], 4);
		else
			memcpy(&key[4], &k->src_ip_be, 4);
		msk[4] = msk[5] = msk[6] = msk[7] = 0xff;
	}
	if (k->present & FMAN_PCD_CC_HW_F_DST_IP) {
		if (k->present & FMAN_PCD_CC_HW_F_IPV6)
			memcpy(&key[8], &k->dst_ip6[0], 4);
		else
			memcpy(&key[8], &k->dst_ip_be, 4);
		msk[8] = msk[9] = msk[10] = msk[11] = 0xff;
	}
	if (k->present & FMAN_PCD_CC_HW_F_SRC_PORT) {
		key[12] = (u8)(k->src_port_be & 0xff);
		key[13] = (u8)(k->src_port_be >> 8);
		msk[12] = msk[13] = 0xff;
	}
	if (k->present & FMAN_PCD_CC_HW_F_DST_PORT) {
		key[14] = (u8)(k->dst_port_be & 0xff);
		key[15] = (u8)(k->dst_port_be >> 8);
		msk[14] = msk[15] = 0xff;
	}

	/* Each match row is key followed by mask, 2 * CC_KEY_SIZE bytes. */
	row = (u8 __force *)match + (size_t)idx * 2 * CC_KEY_SIZE;
	memcpy_toio((void __iomem *)row, key, CC_KEY_SIZE);
	memcpy_toio((void __iomem *)(row + CC_KEY_SIZE), msk, CC_KEY_SIZE);
}

/*
 * Encode one CONT_LOOKUP action descriptor (RM 8.7.4.3) at AD row @idx.
 * For a matching key we steer to the target RX FQ derived from the qband;
 * the dpaa flavor layer owns the qband->FQID map, so here we encode the
 * qband directly into the AD's result field and let the walker's FQID base
 * register resolve it.  hm_handle, when non-zero, is encoded into the NIA
 * (next-invoked-action) HMTD offset so the CC->HM chain (spec sec 5.4 order
 * Parser->CC->HM->QMan) is honoured.
 *
 * AD word layout we emit for a leaf result entry:
 *   word0 = (qband << 16) | (hm_present << 8) | type
 *   word1 = hm_handle (HMTD MURAM offset; 0 when no HM)
 *   word2 = 0
 *   word3 = 0
 * where type 0x1 = "result / enqueue".  This is the leaf form; the
 * group-table[0] entry below is the CONT_LOOKUP that points the walker at
 * this AD table.
 */
static void cc_write_leaf_ad(void __iomem *ad, u16 idx, u16 qband, u32 hm)
{
	u32 __iomem *w = (u32 __iomem *)((u8 __force *)ad +
					 (size_t)idx * CC_AD_ENTRY_SIZE);
	u32 w0 = ((u32)qband << 16) | ((hm ? 1u : 0u) << 8) | 0x1u;

	iowrite32be(w0, &w[0]);
	iowrite32be(hm, &w[1]);
	iowrite32be(0, &w[2]);
	iowrite32be(0, &w[3]);
}

/*
 * Write the tree group-table[0] CONT_LOOKUP entry (RM 8.7.4.1).  This is the
 * single entry the CC walker reads first; it points at the match-key table
 * and the AD table and carries the populated key count + key width.
 *
 *   word0 = (num_keys << 24) | (match_off & 0xFFFFFF)
 *   word1 = (ad_off    & 0xFFFFFF)
 *   word2 = CONT_LOOKUP | ((CC_KEY_SIZE - 1) << 24)
 *   word3 = 0
 */
static void cc_write_group0(struct fman_pcd_cc_tree *t)
{
	u32 __iomem *w = (u32 __iomem *)t->group;

	iowrite32be(((u32)t->num_keys << 24) | (t->match_off & 0xFFFFFF), &w[0]);
	iowrite32be(t->ad_off & 0xFFFFFF, &w[1]);
	iowrite32be(CC_AD_W2_CONT_LOOKUP | ((CC_KEY_SIZE - 1) << 24), &w[2]);
	iowrite32be(0, &w[3]);
}

static void cc_tree_free(struct fman_pcd_cc_tree *t)
{
	struct fman_pcd *pcd = t->pcd;

	if (t->ad_off)
		fman_pcd_muram_free(pcd, t->ad_off, t->ad_sz);
	if (t->match_off)
		fman_pcd_muram_free(pcd, t->match_off, t->match_sz);
	if (t->group_off)
		fman_pcd_muram_free(pcd, t->group_off, CC_GROUP_TABLE_SZ);
	kfree(t);
}

int fman_pcd_cc_static_install(struct fman_pcd *pcd, u8 port_id,
			       const struct fman_pcd_cc_hw_spec *hw)
{
	struct fman_pcd_cc_tree *t, *old;
	struct muram_info *muram;
	struct mutex *lock;
	size_t match_sz, ad_sz;
	unsigned long off;
	u16 i, nrows;
	int err;

	if (!pcd || !hw)
		return -EINVAL;
	if (hw->num_keys > FMAN_PCD_CC_HW_MAX_KEYS)
		return -EINVAL;

	muram = fman_get_muram(fman_pcd_get_fman(pcd));
	if (!muram)
		return -ENXIO;
	lock = fman_pcd_get_lock(pcd);

	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (!t)
		return -ENOMEM;
	t->pcd = pcd;
	t->port_id = port_id;
	t->num_keys = hw->num_keys;
	INIT_LIST_HEAD(&t->node);

	/* (num_keys + 1) rows: trailing row is the miss slot (RM 8.7.4.2/3). */
	nrows = hw->num_keys + 1;
	match_sz = (size_t)nrows * 2 * CC_KEY_SIZE;
	ad_sz = (size_t)nrows * CC_AD_ENTRY_SIZE;
	t->match_sz = match_sz;
	t->ad_sz = ad_sz;

	off = fman_pcd_muram_alloc(pcd, CC_GROUP_TABLE_SZ);
	if (IS_ERR_VALUE(off)) {
		err = (int)off;
		goto err_free;
	}
	t->group_off = off;
	t->group = (void __iomem *)fman_muram_offset_to_vbase(muram, off);

	off = fman_pcd_muram_alloc(pcd, match_sz);
	if (IS_ERR_VALUE(off)) {
		err = (int)off;
		goto err_free;
	}
	t->match_off = off;
	t->match = (void __iomem *)fman_muram_offset_to_vbase(muram, off);

	off = fman_pcd_muram_alloc(pcd, ad_sz);
	if (IS_ERR_VALUE(off)) {
		err = (int)off;
		goto err_free;
	}
	t->ad_off = off;
	t->ad = (void __iomem *)fman_muram_offset_to_vbase(muram, off);

	/* Zero everything: unprogrammed rows are no-match / miss. */
	memset_io(t->group, 0, CC_GROUP_TABLE_SZ);
	memset_io(t->match, 0, match_sz);
	memset_io(t->ad, 0, ad_sz);

	/* Encode each key's match record + leaf result AD. */
	for (i = 0; i < hw->num_keys; i++) {
		cc_pack_key(t->match, i, &hw->keys[i]);
		cc_write_leaf_ad(t->ad, i, hw->keys[i].target_qband,
				 hw->keys[i].hm_handle);
	}
	/* Miss slot (last row): steer to miss_qband, no HM. */
	cc_write_leaf_ad(t->ad, hw->num_keys, hw->miss_qband, 0);

	/* Point the walker at the tables via the group-table[0] CONT_LOOKUP. */
	cc_write_group0(t);

	/* Publish: replace any existing tree for this port (commit rebuild).
	 * The old tree's MURAM free (cc_tree_free -> fman_pcd_muram_free) takes
	 * pcd->lock internally, so freeing under the held lock self-deadlocks
	 * every rebuild that replaces a tree (observed 2026-08-28: the
	 * nf_ft_offload_add worker hung in fman_pcd_muram_free; only the first
	 * tree was ever published, so VLAN CC keys never matched).  Mirror
	 * fman_pcd_cc_static_destroy(): detach under the lock, free after
	 * releasing it.
	 */
	mutex_lock(lock);
	old = cc_find_locked(pcd, port_id);
	if (old)
		list_del(&old->node);
	list_add_tail(&t->node, fman_pcd_get_cc_list(pcd));
	mutex_unlock(lock);

	if (old)
		cc_tree_free(old);

	return 0;

err_free:
	cc_tree_free(t);
	return err;
}
EXPORT_SYMBOL_GPL(fman_pcd_cc_static_install);

void fman_pcd_cc_static_destroy(struct fman_pcd *pcd, u8 port_id)
{
	struct fman_pcd_cc_tree *t;
	struct mutex *lock;

	if (!pcd)
		return;
	lock = fman_pcd_get_lock(pcd);

	mutex_lock(lock);
	t = cc_find_locked(pcd, port_id);
	if (t)
		list_del(&t->node);
	mutex_unlock(lock);

	if (t)
		cc_tree_free(t);
}
EXPORT_SYMBOL_GPL(fman_pcd_cc_static_destroy);

/*
 * fman_pcd_cc_static_get_base() - fetch the MURAM byte offset of a port's
 * installed static CC tree root (its group table).
 *
 * The returned value is the RAW MURAM byte offset (NO >>4 shift) suitable
 * for writing straight into the BMI RX port FMBM_RCCB register via
 * fman_port_set_cc_base() (board patch 0105). That port-bind is the
 * productive consumer that points the silicon datapath at the tree this
 * module installed in MURAM.
 *
 * Returns 0 and stores the offset in *cc_muram_off on success, -EINVAL on
 * bad arguments, or -ENODEV if no CC tree is installed for @port_id.
 */
int fman_pcd_cc_static_get_base(struct fman_pcd *pcd, u8 port_id,
				u32 *cc_muram_off)
{
	struct fman_pcd_cc_tree *t;
	struct mutex *lock;
	int err = 0;

	if (!pcd || !cc_muram_off)
		return -EINVAL;
	lock = fman_pcd_get_lock(pcd);

	mutex_lock(lock);
	t = cc_find_locked(pcd, port_id);
	if (t)
		*cc_muram_off = (u32)t->group_off;
	else
		err = -ENODEV;
	mutex_unlock(lock);

	return err;
}
EXPORT_SYMBOL_GPL(fman_pcd_cc_static_get_base);
