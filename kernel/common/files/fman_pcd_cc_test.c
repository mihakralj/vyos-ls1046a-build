// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_test.c — B1 CC static-tree test debugfs interface.
 *
 * Provides /sys/kernel/debug/fman_pcd/<N>/cc_test for validating the
 * productive fman_pcd_cc_static_install() path (patch 0098) without a
 * kernel consumer.
 *
 * Write: install <port> <qband> <proto> <src_ip> <dst_ip> <dport>
 *        install_l2 <port> <dst_mac> <target_fqid>
 *        clear <port>
 * Read:  summary of installed CC trees
 *
 * Spec sec 5.4; B1 acceptance gate.
 *
 * install_l2 (T-M6-2 B2, plans/ASK2-BRIDGE-OFFLOAD-PLAN.md): arms a single
 * DA-only bridge FDB CC leaf (cc_pack_key_l2(), the 15-byte PORT_ID|DA|SA|
 * ETYPE layout with only DA present) on @port, enqueuing a DA match
 * directly to @target_fqid (a real hardware TX FQ -- e.g. read from
 * `ask-check`'s "DPAA FQ map" line for the intended egress port, or
 * `bin/fman-full-capture.py`). miss_fe_off is auto-filled from
 * fman_pcd_fe_root_get_offset() -- 0 (legacy RSS miss) if FE_ENTER isn't
 * currently engaged on this FMan, non-zero (CC-miss routes through the
 * production ehash path) if it is; this is exactly the B2 coexistence
 * proof's precondition, not something the caller supplies. Debugfs-only:
 * no KeyGen scheme-attach happens here, so nothing reaches this leaf
 * until the port's KG scheme is separately armed for L2 extraction
 * (operator's job on the sacrificial port, not this harness's).
 */

#include <linux/debugfs.h>
#include <linux/err.h>
#include <linux/errno.h>
#include <linux/etherdevice.h>     /* ETH_ALEN, ether_addr_copy, T-M6-2 B2 */
#include <linux/inet.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>

#include <linux/fsl/fman_pcd.h>
#include "fman.h"
#include "fman_muram.h"
#include "fman_pcd_internal.h"
#include "fman_port.h"          /* fman_port_lookup_rx/_set_cc_base, T-M6-2 B2 */

/*
 * Forward references: fman_pcd_cc_static_install/destroy live in
 * fman_pcd_cc.c and link into fsl_dpaa_fman.ko — no EXPORT_SYMBOL needed.
 */
extern int fman_pcd_cc_static_install(struct fman_pcd *pcd, u8 port_id,
				      const struct fman_pcd_cc_hw_spec *hw);
extern void fman_pcd_cc_static_destroy(struct fman_pcd *pcd, u8 port_id);

/*
 * The private struct fman_pcd_cc_tree is defined in fman_pcd_cc.c.  We
 * need its port_id/num_keys/match_off/ad_off/group_off fields for the read
 * handler.  Define a minimal shadow struct matching the layout so we can
 * walk pcd->cc_trees without pulling the full header.
 */
struct fman_pcd_cc_tree_shadow {
	struct list_head node;
	struct fman_pcd *pcd;
	u8 port_id;
	u16 num_keys;
	unsigned long group_off;
	unsigned long match_off;
	unsigned long ad_off;
	/* padding fields from the full struct are harmless — list_for_each
	 * only traverses node pointers and we only read the first fields */
};

/* ---- debugfs read handler ---- */

static int cc_test_show(struct seq_file *m, void *v)
{
	struct fman_pcd *pcd = m->private;
	struct list_head *head;
	struct fman_pcd_cc_tree_shadow *t;

	if (!pcd)
		return 0;

	head = fman_pcd_get_cc_list(pcd);
	if (!head)
		return 0;

	mutex_lock(fman_pcd_get_lock(pcd));
	if (list_empty(head))
		seq_puts(m, "(no CC trees installed)\n");
	else
		list_for_each_entry(t, head, node)
			seq_printf(m,
				"port %u: %u keys, match=0x%lx ad=0x%lx group=0x%lx\n",
				t->port_id, t->num_keys,
				t->match_off, t->ad_off, t->group_off);
	mutex_unlock(fman_pcd_get_lock(pcd));
	return 0;
}

static int cc_test_open(struct inode *inode, struct file *file)
{
	return single_open(file, cc_test_show, inode->i_private);
}

/* ---- debugfs write handler ---- */

static int cc_test_install(struct fman_pcd *pcd, const char *args)
{
	struct fman_pcd_cc_hw_spec spec;
	u8 port_id, proto;
	u16 qband, dport;
	char src_str[40], dst_str[40];
	u32 src_ip, dst_ip;
	int n;

	n = sscanf(args, "install %hhu %hu %hhu %39s %39s %hu",
		  &port_id, &qband, &proto, src_str, dst_str, &dport);
	if (n != 6)
		return -EINVAL;

	if (qband > 3 || !(proto == 6 || proto == 17))
		return -EINVAL;

	if (!in4_pton(src_str, -1, (u8 *)&src_ip, -1, NULL) ||
	    !in4_pton(dst_str, -1, (u8 *)&dst_ip, -1, NULL))
		return -EINVAL;

	memset(&spec, 0, sizeof(spec));
	spec.num_keys = 1;
	spec.miss_qband = 0;
	spec.keys[0].present = FMAN_PCD_CC_HW_F_ETHERTYPE |
			       FMAN_PCD_CC_HW_F_PROTO |
			       FMAN_PCD_CC_HW_F_SRC_IP |
			       FMAN_PCD_CC_HW_F_DST_IP |
			       FMAN_PCD_CC_HW_F_DST_PORT;
	spec.keys[0].ethertype_be = cpu_to_be16(0x0800);
	spec.keys[0].proto = proto;
	spec.keys[0].src_ip_be = cpu_to_be32(src_ip);
	spec.keys[0].dst_ip_be = cpu_to_be32(dst_ip);
	spec.keys[0].dst_port_be = cpu_to_be16(dport);
	spec.keys[0].target_qband = qband;

	return fman_pcd_cc_static_install(pcd, port_id, &spec);
}

/* T-M6-2 B2: see the file-header comment above for the full contract. */
static int cc_test_install_l2(struct fman_pcd *pcd, const char *args)
{
	struct fman_pcd_cc_hw_spec spec;
	u8 port_id;
	u32 target_fqid;
	char mac_str[32];
	u8 dst_mac[ETH_ALEN];
	struct fman *fm;
	int n;

	n = sscanf(args, "install_l2 %hhu %31s %u",
		  &port_id, mac_str, &target_fqid);
	if (n != 3)
		return -EINVAL;

	if (!mac_pton(mac_str, dst_mac))
		return -EINVAL;

	fm = fman_pcd_get_fman(pcd);
	if (!fm)
		return -ENODEV;

	memset(&spec, 0, sizeof(spec));
	spec.bridge_l2 = true;
	spec.num_keys = 1;
	spec.miss_qband = 0;
	/*
	 * 0 if FE_ENTER isn't engaged on this FMan yet -- a valid state (a
	 * pure-L2 leaf with legacy RSS miss), not an error. Non-zero is the
	 * B2 coexistence proof's actual precondition: engage ASK ehash
	 * routing on the port first, then install_l2, so CC-miss lands on
	 * the live routed/NAT path instead of falling through to RSS.
	 */
	spec.miss_fe_off = fman_pcd_fe_root_get_offset(fm);
	spec.keys[0].present = FMAN_PCD_CC_HW_F_MAC_DST;
	ether_addr_copy(spec.keys[0].dst_mac, dst_mac);
	spec.keys[0].target_fqid = target_fqid;

	{
		struct fman_port *rxport;
		u32 cc_base;
		int err;

		err = fman_pcd_cc_static_install(pcd, port_id, &spec);
		if (err)
			return err;

		/*
		 * Beyond this point the tree exists in MURAM but nothing
		 * points at it yet -- publish the datapath wiring (SDK
		 * FM_PORT_SetPCD order, matching every other install_*
		 * variant in this file: params page, then FMBM_RCCB, then
		 * the KG scheme's KGSE_MODE/CCBS/EKFC via attach_cc_l2()).
		 * Any failure here tears the tree back down so a partial,
		 * dangling-but-unreachable CC tree is never left behind.
		 */
		err = fman_pcd_cc_static_get_base(pcd, port_id, &cc_base);
		if (!err) {
			rxport = fman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);
			if (!rxport)
				err = -ENODEV;
		}
		if (!err)
			err = fman_pcd_port_ensure_params_page(pcd, rxport);
		if (!err)
			err = fman_port_set_cc_base(rxport, cc_base);
		if (!err) {
			err = fman_pcd_kg_port_attach_cc_l2(pcd, port_id, cc_base);
			if (err)
				(void)fman_port_set_cc_base(rxport, 0);
		}
		if (err)
			fman_pcd_cc_static_destroy(pcd, port_id);
		return err;
	}
}

static ssize_t cc_test_write(struct file *file, const char __user *buf,
			     size_t count, loff_t *ppos)
{
	struct seq_file *m = file->private_data;
	struct fman_pcd *pcd = m->private;
	char *kbuf, *cmd;
	u8 port_id;
	int ret;

	if (!pcd || count == 0 || count > 256)
		return -EINVAL;

	kbuf = memdup_user_nul(buf, count);
	if (IS_ERR(kbuf))
		return PTR_ERR(kbuf);

	kbuf[strcspn(kbuf, "\n")] = 0;
	cmd = kbuf;

	if (sscanf(cmd, "clear %hhu", &port_id) == 1) {
		fman_pcd_cc_static_destroy(pcd, port_id);
		ret = count;
	} else if (strncmp(cmd, "install_l2 ", 11) == 0) {
		ret = cc_test_install_l2(pcd, cmd);
		if (ret == 0)
			ret = count;
	} else if (strncmp(cmd, "install ", 8) == 0) {
		ret = cc_test_install(pcd, cmd);
		if (ret == 0)
			ret = count;
	} else {
		ret = -EINVAL;
	}

	kfree(kbuf);
	return ret;
}

static const struct file_operations fman_pcd_cc_test_fops = {
	.owner	 = THIS_MODULE,
	.open	 = cc_test_open,
	.read	 = seq_read,
	.write	 = cc_test_write,
	.llseek	 = seq_lseek,
	.release = single_release,
};

/*
 * Public init called from fman_pcd_init().  Creates the cc_test debugfs
 * node under the per-FMan subdirectory.
 */
void fman_pcd_cc_test_debugfs_init(struct dentry *parent, struct fman_pcd *pcd)
{
	if (!parent || !pcd)
		return;
	debugfs_create_file("cc_test", 0644, parent, pcd,
			    &fman_pcd_cc_test_fops);
}
