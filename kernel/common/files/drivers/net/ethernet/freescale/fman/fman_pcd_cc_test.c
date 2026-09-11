// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_test.c - debugfs CC steering test harness (M3-3b).
 *
 * The fman_cc_tree_install() consumer API (board patches 0098 + 0106) has
 * no in-tree caller yet, so the M3-3b acceptance gate (spec sec 5.4: tree
 * installs, KGSE_CCBS grafts, exact-match key steers, miss falls through,
 * detach restores) cannot be exercised on hardware.  This TU adds a
 * debugfs node per FMan instance:
 *
 *   /sys/kernel/debug/fman_pcd/<N>/cc_test
 *
 * Write commands (drives the EXACT 0106 productive sequence):
 *   install <hwport> <qband> <proto> <src_ip> <dst_ip> <dport>
 *       -> fman_pcd_cc_static_install() (one IPv4 exact-match key)
 *       -> fman_pcd_cc_static_get_base()
 *       -> fman_pcd_kg_port_attach_cc()   (the KGSE_CCBS graft)
 *   clear <hwport>
 *       -> fman_pcd_kg_port_detach_cc()
 *       -> fman_pcd_cc_static_destroy()
 *
 * Read: one line per installed tree -
 *   port 0x10: 1 keys, group=0x4ac40 match=0x4ac50 ad=0x4ad60
 * (group == the MURAM offset grafted into KGSE_CCBS; cross-check with
 *  ask-pcd-regdump.py scheme dump on the DUT.)
 *
 * Debug/bring-up only: gated by CONFIG_FSL_FMAN_PCD; the node only exists
 * when debugfs is mounted.  Zero datapath cost when unused.
 */

#include <linux/debugfs.h>
#include <linux/err.h>
#include <linux/errno.h>
#include <linux/inet.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>

#include <linux/fsl/fman_pcd.h>
#include "fman.h"
#include "fman_pcd_internal.h"

static int cc_test_show(struct seq_file *m, void *v)
{
	struct fman_pcd *pcd = m->private;

	if (!pcd)
		return 0;
	fman_pcd_cc_seq_dump(pcd, m);
	return 0;
}

static int cc_test_open(struct inode *inode, struct file *file)
{
	return single_open(file, cc_test_show, inode->i_private);
}

static int cc_test_install(struct fman_pcd *pcd, const char *args)
{
	struct fman_pcd_cc_hw_spec *spec;
	char src_str[40], dst_str[40];
	u32 src_ip, dst_ip, cc_base;
	u8 port_id, proto;
	u16 qband, dport;
	int n, err;

	n = sscanf(args, "install %hhi %hu %hhu %39s %39s %hu",
		   &port_id, &qband, &proto, src_str, dst_str, &dport);
	if (n != 6)
		return -EINVAL;

	if (qband > 3 || !(proto == 6 || proto == 17))
		return -EINVAL;

	if (!in4_pton(src_str, -1, (u8 *)&src_ip, -1, NULL) ||
	    !in4_pton(dst_str, -1, (u8 *)&dst_ip, -1, NULL))
		return -EINVAL;

	spec = kzalloc(sizeof(*spec), GFP_KERNEL);
	if (!spec)
		return -ENOMEM;

	spec->num_keys = 1;
	spec->miss_qband = 0;
	spec->keys[0].present = FMAN_PCD_CC_HW_F_ETHERTYPE |
				FMAN_PCD_CC_HW_F_PROTO |
				FMAN_PCD_CC_HW_F_SRC_IP |
				FMAN_PCD_CC_HW_F_DST_IP |
				FMAN_PCD_CC_HW_F_DST_PORT;
	spec->keys[0].ethertype_be = cpu_to_be16(ETH_P_IP);
	spec->keys[0].proto = proto;
	/* in4_pton() already stored network byte order into the u32 */
	spec->keys[0].src_ip_be = src_ip;
	spec->keys[0].dst_ip_be = dst_ip;
	spec->keys[0].dst_port_be = cpu_to_be16(dport);
	spec->keys[0].target_qband = qband;

	/* The exact 0106 productive sequence (fman_cc_tree_install()). */
	err = fman_pcd_cc_static_install(pcd, port_id, spec);
	kfree(spec);
	if (err)
		return err;

	err = fman_pcd_cc_static_get_base(pcd, port_id, &cc_base);
	if (!err)
		err = fman_pcd_kg_port_attach_cc(pcd, port_id, cc_base);
	if (err) {
		fman_pcd_cc_static_destroy(pcd, port_id);
		return err;
	}

	pr_info("fman_pcd cc_test: port 0x%02x tree installed, KGSE_CCBS grafted to 0x%x\n",
		port_id, cc_base);
	return 0;
}

static ssize_t cc_test_write(struct file *file, const char __user *buf,
			     size_t count, loff_t *ppos)
{
	struct seq_file *m = file->private_data;
	struct fman_pcd *pcd = m->private;
	char *kbuf;
	u8 port_id;
	int ret;

	if (!pcd || count == 0 || count > 256)
		return -EINVAL;

	kbuf = memdup_user_nul(buf, count);
	if (IS_ERR(kbuf))
		return PTR_ERR(kbuf);

	kbuf[strcspn(kbuf, "\n")] = 0;

	if (sscanf(kbuf, "clear %hhi", &port_id) == 1) {
		/* 0106 teardown order: detach the graft FIRST. */
		(void)fman_pcd_kg_port_detach_cc(pcd, port_id);
		fman_pcd_cc_static_destroy(pcd, port_id);
		pr_info("fman_pcd cc_test: port 0x%02x graft detached, tree destroyed\n",
			port_id);
		ret = count;
	} else if (strncmp(kbuf, "install ", 8) == 0) {
		ret = cc_test_install(pcd, kbuf);
		if (ret == 0)
			ret = count;
	} else {
		ret = -EINVAL;
	}

	kfree(kbuf);
	return ret;
}

static const struct file_operations fman_pcd_cc_test_fops = {
	.owner = THIS_MODULE,
	.open = cc_test_open,
	.read = seq_read,
	.write = cc_test_write,
	.llseek = seq_lseek,
	.release = single_release,
};

void fman_pcd_cc_test_debugfs_init(struct dentry *parent,
				   struct fman_pcd *pcd)
{
	if (IS_ERR_OR_NULL(parent) || !pcd)
		return;
	debugfs_create_file("cc_test", 0600, parent, pcd,
			    &fman_pcd_cc_test_fops);
}
