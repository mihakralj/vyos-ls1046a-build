// SPDX-License-Identifier: GPL-2.0
/*
 * ask_genl.c — generic-netlink family for ASK 2.0
 *
 * PR1 wires only ASK_CMD_GET_INFO. Every other command in the UAPI
 * (DUMP_FLOWS / GET_FLOW / DUMP_SAS / FLUSH_FLOWS / FLUSH_SAS /
 *  SET_POLICER / GET_MURAM) is intentionally omitted from the small_ops
 * table — the genl core will reply -EOPNOTSUPP for any unknown command,
 * which is exactly the M0 contract: the family exists, version 1 is
 * advertised, info reports a populated struct, everything else is
 * unsupported until the relevant PR lands.
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

/* ------------------------------------------------------------------------- */
/* small_ops table                                                            */
/* PR1 only registers GET_INFO. Future PRs append entries here.               */
/* ------------------------------------------------------------------------- */
static const struct genl_small_ops ask_genl_small_ops[] = {
{
.cmd      = ASK_CMD_GET_INFO,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_get_info_doit,
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