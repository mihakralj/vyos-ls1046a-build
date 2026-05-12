// SPDX-License-Identifier: GPL-2.0
/*
 * ask_genl.c — generic-netlink family for ASK 2.0
 *
 * PR1 (M0.1) wired ASK_CMD_GET_INFO with a real handler.
 * PR5 (M1.1) wires the remaining 7 UAPI commands as enumerated stubs
 * that log + return -EOPNOTSUPP via printk_ratelimited, so that
 * `genl ctrl-list -f ask` enumerates all 8 ops and userspace tooling
 * (askd, ask-cli) can introspect what this build supports without
 * trial-and-error round-trips. Each later PR replaces the eopnotsupp
 * wiring with the real handler when its subsystem lands (PR7: flows,
 * PR16a: SAs, plus dedicated PRs for SET_POLICER / GET_MURAM).
 *
 * See specs/ask-2.0-rewrite-spec.md §7 for the protocol design.
 */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <net/genetlink.h>
#include <net/sock.h>

#include <uapi/linux/ask/ask.h>
#include "ask_internal.h"

/* ------------------------------------------------------------------------- */
/* Multicast groups                                                           */
/* The order MUST match enum ask_genl_mcgrp in the UAPI header so that        */
/* userspace genl_ctrl_resolve_grp() returns the right id.                    */
/* ------------------------------------------------------------------------- */
static const struct genl_multicast_group ask_mcgrps[] = {
[ASK_MCGRP_EVENTS] = { .name = ASK_MCGRP_EVENTS_NAME },
[ASK_MCGRP_FLOWS]  = { .name = ASK_MCGRP_FLOWS_NAME  },
[ASK_MCGRP_SAS]    = { .name = ASK_MCGRP_SAS_NAME    },
};

/* Forward */
static int ask_genl_get_info_doit(struct sk_buff *skb, struct genl_info *info);
static int ask_genl_eopnotsupp_doit(struct sk_buff *skb,
    struct genl_info *info);
static int ask_genl_eopnotsupp_dumpit(struct sk_buff *skb,
      struct netlink_callback *cb);

/* ------------------------------------------------------------------------- */
/* small_ops table                                                            */
/*                                                                            */
/* PR5 (M1.1) wires every UAPI command. ASK_CMD_GET_INFO is the only one     */
/* with a real handler; the other seven dispatch to ask_genl_eopnotsupp_*    */
/* which logs "command N not yet supported" once per cmd via printk          */
/* ratelimit and returns -EOPNOTSUPP. Benefit over leaving them out of       */
/* small_ops (and letting genl core return -EOPNOTSUPP silently) is twofold: */
/*   1. `genl ctrl-list -f ask` enumerates all 8 ops, so userspace tooling   */
/*      (askd, ask-cli) can introspect what this driver build claims to      */
/*      support without trial-and-error round-trips.                         */
/*   2. dmesg gets a one-shot breadcrumb the first time askd asks for an     */
/*      unsupported command — the diagnostic surface a developer wants when  */
/*      bringing up a partial build.                                         */
/*                                                                          */
/* Each later PR replaces the eopnotsupp doit/dumpit pointers with real      */
/* handlers as the corresponding subsystem lands (PR7: DUMP_FLOWS /          */
/* GET_FLOW / FLUSH_FLOWS, PR16a: DUMP_SAS / FLUSH_SAS, the policer PR:      */
/* SET_POLICER, the muram PR: GET_MURAM).                                   */
/* ------------------------------------------------------------------------- */
static const struct genl_small_ops ask_genl_small_ops[] = {
{
.cmd      = ASK_CMD_GET_INFO,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_get_info_doit,
},
{
.cmd      = ASK_CMD_GET_MURAM,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_eopnotsupp_doit,
},
{
.cmd      = ASK_CMD_DUMP_FLOWS,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.dumpit   = ask_genl_eopnotsupp_dumpit,
},
{
.cmd      = ASK_CMD_GET_FLOW,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_eopnotsupp_doit,
},
{
.cmd      = ASK_CMD_DUMP_SAS,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.dumpit   = ask_genl_eopnotsupp_dumpit,
},
{
.cmd      = ASK_CMD_FLUSH_FLOWS,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_eopnotsupp_doit,
},
{
.cmd      = ASK_CMD_FLUSH_SAS,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_eopnotsupp_doit,
},
{
.cmd      = ASK_CMD_SET_POLICER,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_eopnotsupp_doit,
},
};

/* ------------------------------------------------------------------------- */
/* genl_family                                                                */
/* ------------------------------------------------------------------------- */
static struct genl_family ask_genl_family __ro_after_init = {
.hdrsize       = 0,
.name          = ASK_GENL_NAME,
.version       = ASK_GENL_VERSION,
.maxattr       = ASK_ATTR_MAX,
.policy        = ask_top_policy,
.netnsok       = true,
.parallel_ops  = false,
.module        = THIS_MODULE,
.small_ops     = ask_genl_small_ops,
.n_small_ops   = ARRAY_SIZE(ask_genl_small_ops),
.resv_start_op = ASK_CMD_SET_POLICER + 1,
.mcgrps        = ask_mcgrps,
.n_mcgrps      = ARRAY_SIZE(ask_mcgrps),
};

/* ------------------------------------------------------------------------- */
/* ASK_CMD_GET_INFO handler                                                   */
/*                                                                            */
/* Reply layout (top-level): single ASK_ATTR_INFO nested attribute            */
/* containing every ASK_INFO_ATTR_* defined in the UAPI header.               */
/*                                                                            */
/* For PR1, the ucode fields are zero (we don't talk to silicon yet) and the  */
/* capability bitmap is empty (no offload features are wired). PR13 fills     */
/* the ucode fields from a real OP_GET_UCODE_VERSION; M3/M4 OR in the         */
/* capability bits as each feature lands.                                     */
/* ------------------------------------------------------------------------- */
static int ask_genl_get_info_fill(struct sk_buff *skb)
{
struct nlattr *nest;

nest = nla_nest_start(skb, ASK_ATTR_INFO);
if (!nest)
return -EMSGSIZE;

if (nla_put_string(skb, ASK_INFO_ATTR_DRIVER_VERSION,
   "ask " ASK_DRV_VERSION_STR))
goto nla_put_failure;

if (nla_put_u32(skb, ASK_INFO_ATTR_GENL_VERSION, ASK_GENL_VERSION))
goto nla_put_failure;

if (nla_put_u16(skb, ASK_INFO_ATTR_UCODE_FAMILY, 0))
goto nla_put_failure;
if (nla_put_u8(skb,  ASK_INFO_ATTR_UCODE_MAJOR,  0))
goto nla_put_failure;
if (nla_put_u8(skb,  ASK_INFO_ATTR_UCODE_MINOR,  0))
goto nla_put_failure;
if (nla_put_u16(skb, ASK_INFO_ATTR_UCODE_PATCH,  0))
goto nla_put_failure;

if (nla_put_u64_64bit(skb, ASK_INFO_ATTR_CAPABILITIES, 0,
      ASK_INFO_ATTR_UNSPEC))
goto nla_put_failure;

if (nla_put_u32(skb, ASK_INFO_ATTR_NUM_FMAN,  0))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_INFO_ATTR_NUM_FLOWS, 0))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_INFO_ATTR_MAX_FLOWS, 0))
goto nla_put_failure;

nla_nest_end(skb, nest);
return 0;

nla_put_failure:
nla_nest_cancel(skb, nest);
return -EMSGSIZE;
}

static int ask_genl_get_info_doit(struct sk_buff *skb, struct genl_info *info)
{
struct sk_buff *rep;
void *hdr;
int rc;

rep = genlmsg_new(NLMSG_DEFAULT_SIZE, GFP_KERNEL);
if (!rep)
return -ENOMEM;

hdr = genlmsg_put_reply(rep, info, &ask_genl_family, 0,
ASK_CMD_GET_INFO);
if (!hdr) {
rc = -EMSGSIZE;
goto err;
}

rc = ask_genl_get_info_fill(rep);
if (rc)
goto err_cancel;

genlmsg_end(rep, hdr);
return genlmsg_reply(rep, info);

err_cancel:
genlmsg_cancel(rep, hdr);
err:
nlmsg_free(rep);
return rc;
}

/* ------------------------------------------------------------------------- */
/* Lifecycle                                                                  */
/* ------------------------------------------------------------------------- */
int ask_genl_register(void)
{
int rc = genl_register_family(&ask_genl_family);

if (rc) {
ask_pr_err("genl family registration failed: %d\n", rc);
return rc;
}
ask_pr_info("genl family '%s' registered, version %u, family id %d\n",
    ask_genl_family.name, ask_genl_family.version,
    ask_genl_family.id);
return 0;
}

void ask_genl_unregister(void)
{
genl_unregister_family(&ask_genl_family);
ask_pr_info("genl family unregistered\n");
}

/* ------------------------------------------------------------------------- */
/* PR5 stub doit/dumpit shared by the seven not-yet-implemented commands.    */
/*                                                                            */
/* The ratelimit (printk_ratelimited default: 10 messages per 5s) is          */
/* deliberately generous so a misconfigured askd hammering an unsupported    */
/* command does not flood dmesg, but the FIRST hit is always visible. PRs    */
/* that implement a command MUST replace its eopnotsupp doit/dumpit wiring   */
/* with the real handler — the ratelimit hides duplicates but never the      */
/* first occurrence.                                                          */
/* ------------------------------------------------------------------------- */
static int ask_genl_eopnotsupp_doit(struct sk_buff *skb,
    struct genl_info *info)
{
printk_ratelimited(KERN_INFO ASK_DRV_NAME
   ": ASK_CMD_%u not yet supported in this build\n",
   info->genlhdr->cmd);
return -EOPNOTSUPP;
}

static int ask_genl_eopnotsupp_dumpit(struct sk_buff *skb,
      struct netlink_callback *cb)
{
u8 cmd = cb->nlh ?
((struct genlmsghdr *)nlmsg_data(cb->nlh))->cmd : 0;

printk_ratelimited(KERN_INFO ASK_DRV_NAME
   ": ASK_CMD_%u (dump) not yet supported in this build\n",
   cmd);
return -EOPNOTSUPP;
}
