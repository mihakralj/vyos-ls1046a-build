// SPDX-License-Identifier: GPL-2.0
/*
 * ask_main.c — module init/exit for ASK2
 *
 * PR1 (M0.1) skeleton: registers the generic-netlink family and the
 * subordinate subsystems (which are all stubs at this point). Any
 * subsystem that fails on init causes a clean teardown of the previous
 * ones — strict reverse-order unwind via goto labels.
 *
 * See specs/ask2-rewrite-spec.md §4.2 for the full module layout
 * and plans/ASK2-IMPLEMENTATION.md for the PR breakdown.
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <net/genetlink.h>

#include "include/ask_internal.h"

static int __init ask_init(void)
{
	int rc;

	ask_pr_info("loading ASK %s (skeleton — no offload functionality yet)\n",
		    ASK_DRV_VERSION_STR);

	rc = ask_hw_init();
	if (rc)
		goto err_hw;

rc = ask_stats_init();
if (rc)
goto err_stats;

rc = ask_flow_init();
if (rc)
goto err_flow;

	rc = ask_neigh_init();
	if (rc)
		goto err_neigh;

	rc = ask_bridge_init();
	if (rc)
		goto err_bridge;

	rc = ask_op_init();
	if (rc)
		goto err_op;

	rc = ask_caam_init();
	if (rc)
		goto err_caam;

	rc = ask_xfrm_init();
	if (rc)
		goto err_xfrm;

	rc = ask_flow_offload_init();
	if (rc)
		goto err_offload;

	rc = ask_debugfs_init();
	if (rc)
		goto err_debugfs;

	rc = ask_genl_register();
	if (rc)
		goto err_genl;

	ask_pr_info("ready (genl family registered, all subsystems are stubs)\n");
	return 0;

err_genl:
	ask_debugfs_exit();
err_debugfs:
	ask_flow_offload_exit();
err_offload:
	ask_xfrm_exit();
err_xfrm:
	ask_caam_exit();
err_caam:
	ask_op_exit();
err_op:
	ask_bridge_exit();
err_bridge:
	ask_neigh_exit();
err_neigh:
	ask_flow_exit();
err_flow:
ask_stats_exit();
err_stats:
	ask_hw_exit();
err_hw:
	ask_pr_err("init failed: %d\n", rc);
	return rc;
}

static void __exit ask_exit(void)
{
	ask_genl_unregister();
	ask_debugfs_exit();
	ask_flow_offload_exit();
	ask_xfrm_exit();
	ask_caam_exit();
	ask_op_exit();
	ask_bridge_exit();
	ask_neigh_exit();
ask_flow_exit();
ask_stats_exit();
ask_hw_exit();
ask_pr_info("unloaded\n");
}

module_init(ask_init);
module_exit(ask_exit);

MODULE_AUTHOR("VyOS LS1046A maintainers");
MODULE_DESCRIPTION("ASK2 — NXP LS1046A FMan/210 hardware offload (skeleton)");
MODULE_LICENSE("GPL");
MODULE_VERSION(ASK_DRV_VERSION_STR);
MODULE_ALIAS_GENL_FAMILY("ask");