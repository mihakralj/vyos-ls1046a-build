// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - debugfs subsystem.
 *
 * Exposes /sys/kernel/debug/ask/ with the M1 coarse dataplane mode-switch
 * trigger.  This is the manual operator seam for engaging/disengaging
 * hardware offload on a single FMan RX port before the M7 VyOS op-mode
 * (`set system offload ask`) is wired - both drive the same
 * ask_hw_offload_engage()/_disengage() path.
 *
 * /sys/kernel/debug/ask/offload  (write-only, single-writer)
 *     echo "engage <port>"     > offload   # S0 (RSS) -> S1 (AC_CC)
 *     echo "disengage <port>"  > offload   # S1 (AC_CC) -> S0 (RSS)
 *   <port> is the FMan-side hardware RX port id (decimal or 0x-hex;
 *   eth3 = 0x10).  M1 ships dormant: the engage carries no classification
 *   semantics and is not on a traffic path (control-plane plumbing only).
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/debugfs.h>
#include <linux/uaccess.h>
#include <linux/string.h>

#include "include/ask_internal.h"

static struct dentry *ask_debugfs_root;

static ssize_t ask_offload_write(struct file *file, const char __user *ubuf,
				 size_t len, loff_t *ppos)
{
	char buf[64];
	char verb[16];
	unsigned int port;
	int rc;

	if (len == 0 || len >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, len))
		return -EFAULT;
	buf[len] = '\0';

	/* "engage <port>" | "disengage <port>"; %i accepts 0x-hex or decimal. */
	if (sscanf(buf, "%15s %i", verb, &port) != 2)
		return -EINVAL;
	if (port > 0xff)
		return -EINVAL;

	if (!strcmp(verb, "engage")) {
		rc = ask_hw_offload_engage((u8)port);
		if (rc)
			return rc;
	} else if (!strcmp(verb, "disengage")) {
		ask_hw_offload_disengage((u8)port);
	} else {
		return -EINVAL;
	}

	return len;
}

static const struct file_operations ask_offload_fops = {
	.owner	= THIS_MODULE,
	.open	= simple_open,
	.write	= ask_offload_write,
	.llseek	= noop_llseek,
};

int ask_debugfs_init(void)
{
	ask_debugfs_root = debugfs_create_dir(ASK_DRV_NAME, NULL);
	if (IS_ERR(ask_debugfs_root)) {
		/* debugfs disabled or unavailable - non-fatal, ship without it. */
		ask_pr_dbg("debugfs: unavailable (%ld); offload trigger absent\n",
			   PTR_ERR(ask_debugfs_root));
		ask_debugfs_root = NULL;
		return 0;
	}

	debugfs_create_file("offload", 0200, ask_debugfs_root, NULL,
			    &ask_offload_fops);

	ask_pr_dbg("debugfs: /sys/kernel/debug/%s/offload ready\n",
		   ASK_DRV_NAME);
	return 0;
}

void ask_debugfs_exit(void)
{
	debugfs_remove_recursive(ask_debugfs_root);
	ask_debugfs_root = NULL;
	ask_pr_dbg("debugfs: exit\n");
}
