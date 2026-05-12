/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ASK 2.0 internal API.
 *
 * Forward declarations and module-private function signatures shared
 * between the .c files inside ask.ko. Anything exposed to userspace
 * lives in include/uapi/linux/ask/ask.h instead.
 *
 * See specs/ask-2.0-rewrite-spec.md for the full architecture.
 */
#ifndef _ASK_INTERNAL_H
#define _ASK_INTERNAL_H

#include <linux/types.h>
#include <linux/printk.h>
#include <net/netlink.h>

#define ASK_DRV_NAME            "ask"
#define ASK_DRV_VERSION_STR     "2.0.0"
#define ASK_DRV_VERSION_MAJOR   2
#define ASK_DRV_VERSION_MINOR   0
#define ASK_DRV_VERSION_PATCH   0

#define ask_pr_info(fmt, ...)   pr_info(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_warn(fmt, ...)   pr_warn(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_err(fmt, ...)    pr_err(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)
#define ask_pr_dbg(fmt, ...)    pr_debug(ASK_DRV_NAME ": " fmt, ##__VA_ARGS__)

/* ------------------------------------------------------------------------- */
/* ask_genl.c — generic-netlink family lifecycle and dispatch                 */
/* ------------------------------------------------------------------------- */
int  ask_genl_register(void);
void ask_genl_unregister(void);

/* ------------------------------------------------------------------------- */
/* ask_genl_attr.c — nla_policy tables shared across nested attribute sets    */
/* ------------------------------------------------------------------------- */
extern const struct nla_policy ask_top_policy[];
extern const struct nla_policy ask_info_policy[];
extern const struct nla_policy ask_muram_policy[];
extern const struct nla_policy ask_flow_policy[];
extern const struct nla_policy ask_sa_policy[];
extern const struct nla_policy ask_event_policy[];
extern const struct nla_policy ask_policer_policy[];

/* ------------------------------------------------------------------------- */
/* ask_flow.c — software flow table (rhashtable + RCU)                        */
/* PR7 fills these in; for PR1 they are all stubs returning -EOPNOTSUPP.      */
/* ------------------------------------------------------------------------- */
int  ask_flow_init(void);
void ask_flow_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_flow_offload.c — flow_block_cb registration on dpaa netdevs            */
/* PR8 fills these in.                                                       */
/* ------------------------------------------------------------------------- */
int  ask_flow_offload_init(void);
void ask_flow_offload_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_xfrm.c — xfrmdev_ops packet-mode IPsec offload                         */
/* PR16a fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_xfrm_init(void);
void ask_xfrm_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_caam.c — CAAM QI descriptor sharing                                    */
/* PR16b fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_caam_init(void);
void ask_caam_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_bridge.c — switchdev notifier driving bridge fast-path                 */
/* PR15e fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_bridge_init(void);
void ask_bridge_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_neigh.c — netevent notifier for L2 nexthop updates                     */
/* M2 onwards fills these in.                                                */
/* ------------------------------------------------------------------------- */
int  ask_neigh_init(void);
void ask_neigh_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_op.c — offline-port re-injection plumbing                              */
/* PR15f fills these in.                                                     */
/* ------------------------------------------------------------------------- */
int  ask_op_init(void);
void ask_op_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_hostcmd.c — wire-format encoders/decoders for FMan host commands       */
/* PR6 fills these in.                                                       */
/* ------------------------------------------------------------------------- */
int  ask_hostcmd_init(void);
void ask_hostcmd_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_stats.c — u64_stats_sync wrappers                                      */
/* PR7 fills these in.                                                       */
/* ------------------------------------------------------------------------- */
int  ask_stats_init(void);
void ask_stats_exit(void);

/* ------------------------------------------------------------------------- */
/* ask_debugfs.c - /sys/kernel/debug/ask (gated on CONFIG_DEBUG_FS)           */
/* ------------------------------------------------------------------------- */
int  ask_debugfs_init(void);
void ask_debugfs_exit(void);

#endif /* _ASK_INTERNAL_H */