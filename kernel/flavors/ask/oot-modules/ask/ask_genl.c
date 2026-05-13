// SPDX-License-Identifier: GPL-2.0
/*
 * ask_genl.c — generic-netlink family for ASK2
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
 * See specs/ask2-rewrite-spec.md §7 for the protocol design.
 */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <net/genetlink.h>
#include <net/sock.h>

#include <uapi/linux/ask/ask.h>
#include "include/ask_internal.h"

/* ------------------------------------------------------------------------- */
/* PR7 (M1.3) flow command handlers — wire DUMP_FLOWS / GET_FLOW /            */
/* FLUSH_FLOWS to the rhashtable + RCU table in ask_flow.c.                   */
/* ------------------------------------------------------------------------- */
static int ask_genl_dump_flows_dumpit(struct sk_buff *skb,
      struct netlink_callback *cb);
static int ask_genl_get_flow_doit(struct sk_buff *skb,
  struct genl_info *info);
static int ask_genl_flush_flows_doit(struct sk_buff *skb,
     struct genl_info *info);

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
/*
 * The eopnotsupp stubs and the per-flow fill / dump-walker helpers below
 * are non-static so the kunit suite (PR9 / M1.5) can call them directly
 * without going through the genl_family small_ops dispatch table. The
 * production code path always reaches them via the dispatch table; the
 * test-only direct-call entry is a coverage convenience, not an API.
 * See tests/ask_test_genl.c.
 */
int ask_genl_eopnotsupp_doit(struct sk_buff *skb, struct genl_info *info);
int ask_genl_eopnotsupp_dumpit(struct sk_buff *skb,
       struct netlink_callback *cb);
int ask_genl_get_info_fill(struct sk_buff *skb);
int ask_genl_fill_one_flow(struct sk_buff *skb, struct ask_flow *f);

/*
 * Dump context: cb->args[0] is the index of the next flow to emit (we
 * advance it as we walk). cb->args[1] is a sentinel that becomes 1 once
 * the walk exhausts so subsequent dumpit calls return 0 immediately.
 */
struct ask_genl_dump_ctx {
struct sk_buff *skb;
int            start;   /* skip first N entries */
int            count;   /* how many emitted so far this call */
int            seen;    /* total walked (start + count + skipped tail) */
int            err;
};

int ask_genl_dump_one_cb(struct ask_flow *f, void *arg);

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
.dumpit   = ask_genl_dump_flows_dumpit,
},
{
.cmd      = ASK_CMD_GET_FLOW,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_get_flow_doit,
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
.doit     = ask_genl_flush_flows_doit,
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
int ask_genl_get_info_fill(struct sk_buff *skb)
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

/*
 * PR13 (M2.4): populate the ucode-version fields from the live QEF
 * blob loaded by U-Boot into FMan IRAM and republished via DT
 * /soc/fman@<addr>/fman-firmware/fsl,firmware. ask_hw_ucode_get_version()
 * is cheap (cached after first call) and never sleeps in the steady
 * state, so calling it from the get-info doit hot-path is fine.
 *
 * If the probe fails for any reason (no FMan in DT, missing firmware
 * property, malformed QEF blob) we fall back to all-zeros — userspace
 * already knows zero means "no microcode info" from the PR1 contract.
 * The first failure is logged via ask_pr_warn() in ask_hw.c, so the
 * cause appears in dmesg without spamming on every get-info call.
 */
{
        struct ask_hw_ucode_version v = {0};

        (void)ask_hw_ucode_get_version(&v);

        if (nla_put_u16(skb, ASK_INFO_ATTR_UCODE_FAMILY, v.family))
                goto nla_put_failure;
        if (nla_put_u8(skb,  ASK_INFO_ATTR_UCODE_MAJOR,  v.major))
                goto nla_put_failure;
        if (nla_put_u8(skb,  ASK_INFO_ATTR_UCODE_MINOR,  v.minor))
                goto nla_put_failure;
        if (nla_put_u16(skb, ASK_INFO_ATTR_UCODE_PATCH,  v.patch))
                goto nla_put_failure;
}

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
EXPORT_SYMBOL_GPL(ask_genl_get_info_fill);

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
int ask_genl_eopnotsupp_doit(struct sk_buff *skb,
    struct genl_info *info)
{
printk_ratelimited(KERN_INFO ASK_DRV_NAME
   ": ASK_CMD_%u not yet supported in this build\n",
   info->genlhdr->cmd);
return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_genl_eopnotsupp_doit);

int ask_genl_eopnotsupp_dumpit(struct sk_buff *skb,
       struct netlink_callback *cb)
{
u8 cmd = cb->nlh ?
((struct genlmsghdr *)nlmsg_data(cb->nlh))->cmd : 0;

printk_ratelimited(KERN_INFO ASK_DRV_NAME
   ": ASK_CMD_%u (dump) not yet supported in this build\n",
   cmd);
return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_genl_eopnotsupp_dumpit);

/* ------------------------------------------------------------------------- */
/* PR7 (M1.3) flow command handlers.                                          */
/*                                                                            */
/* These wire ASK_CMD_DUMP_FLOWS / GET_FLOW / FLUSH_FLOWS to the              */
/* rhashtable + RCU table created in ask_flow.c. The wire format is the       */
/* nested ASK_ATTR_FLOW container described in spec §7. Per-flow content:     */
/*   ASK_FLOW_ATTR_COOKIE     (u64)                                           */
/*   ASK_FLOW_ATTR_HW_FLOW_ID (u32)  — fake counter for PR7, real in PR14     */
/*   ASK_FLOW_ATTR_OIF        (u32)                                           */
/*   ASK_FLOW_ATTR_ACTION_FLAGS (u32)                                         */
/*   ASK_FLOW_ATTR_PACKETS    (u64)                                           */
/*   ASK_FLOW_ATTR_BYTES      (u64)                                           */
/*   ASK_FLOW_ATTR_LAST_SEEN_NS (u64)                                         */
/*                                                                            */
/* Stats are read under u64_stats_fetch_begin / retry inside ask_flow.c so    */
/* 32-bit readers cannot see torn 64-bit values.                              */
/* ------------------------------------------------------------------------- */

int ask_genl_fill_one_flow(struct sk_buff *skb, struct ask_flow *f)
{
struct nlattr *nest;
u64 packets, bytes, last_seen_ns;
unsigned int seq;

nest = nla_nest_start(skb, ASK_ATTR_FLOW);
if (!nest)
return -EMSGSIZE;

if (nla_put_u64_64bit(skb, ASK_FLOW_ATTR_ID, f->cookie,
      ASK_FLOW_ATTR_UNSPEC))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_FLOW_ATTR_HW_FLOW_ID, f->hw_flow_id))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_FLOW_ATTR_OIF, f->oif))
goto nla_put_failure;

do {
seq = u64_stats_fetch_begin(&f->stats.syncp);
packets      = f->stats.packets;
bytes        = f->stats.bytes;
last_seen_ns = f->stats.last_seen_ns;
} while (u64_stats_fetch_retry(&f->stats.syncp, seq));

if (nla_put_u64_64bit(skb, ASK_FLOW_ATTR_PACKETS, packets,
      ASK_FLOW_ATTR_UNSPEC))
goto nla_put_failure;
if (nla_put_u64_64bit(skb, ASK_FLOW_ATTR_BYTES, bytes,
      ASK_FLOW_ATTR_UNSPEC))
goto nla_put_failure;
if (nla_put_u64_64bit(skb, ASK_FLOW_ATTR_LAST_SEEN_NS, last_seen_ns,
      ASK_FLOW_ATTR_UNSPEC))
goto nla_put_failure;

nla_nest_end(skb, nest);
return 0;

nla_put_failure:
nla_nest_cancel(skb, nest);
return -EMSGSIZE;
}
EXPORT_SYMBOL_GPL(ask_genl_fill_one_flow);

/*
 * The struct ask_genl_dump_ctx layout (and its narrative on snapshot vs.
 * iter-threaded walks) is forward-declared up top alongside the kunit
 * helper prototypes so tests/ask_test_genl.c can construct one without
 * needing a private copy of the type.
 */
int ask_genl_dump_one_cb(struct ask_flow *f, void *arg)
{
struct ask_genl_dump_ctx *ctx = arg;
int rc;

if (ctx->seen < ctx->start) {
ctx->seen++;
return 0;
}

rc = ask_genl_fill_one_flow(ctx->skb, f);
if (rc) {
ctx->err = rc;
/* Stop walk; netlink core will resume from ctx->seen next call. */
return rc;
}

ctx->count++;
ctx->seen++;
return 0;
}
EXPORT_SYMBOL_GPL(ask_genl_dump_one_cb);

static int ask_genl_dump_flows_dumpit(struct sk_buff *skb,
      struct netlink_callback *cb)
{
struct ask_flow_table *t = ask_flow_default_table();
struct ask_genl_dump_ctx ctx = { 0 };
void *hdr;
int rc;

if (!t)
return 0; /* table not initialised → empty dump */

ctx.skb   = skb;
ctx.start = cb->args[0];

hdr = genlmsg_put(skb, NETLINK_CB(cb->skb).portid, cb->nlh->nlmsg_seq,
  &ask_genl_family, NLM_F_MULTI, ASK_CMD_DUMP_FLOWS);
if (!hdr)
return -EMSGSIZE;

rc = ask_flow_walk(t, ask_genl_dump_one_cb, &ctx);

if (ctx.count == 0 && rc == -EMSGSIZE) {
/* First fill on this call already overflowed → real error. */
genlmsg_cancel(skb, hdr);
return -EMSGSIZE;
}

genlmsg_end(skb, hdr);

cb->args[0] = ctx.seen;

/* Returning 0 ends the dump; returning >0 keeps it going. We end
 * when the walker exhausted the table (rc == 0 AND ctx.count fit
 * everything still owed). On EMSGSIZE we report the bytes written
 * so far and let netlink call us again with cb->args[0] advanced.
 */
if (rc == 0 || rc == -EMSGSIZE)
return skb->len;

return rc;
}

static int ask_genl_get_flow_doit(struct sk_buff *skb, struct genl_info *info)
{
struct ask_flow_table *t = ask_flow_default_table();
struct ask_flow *f;
struct sk_buff *rep;
struct nlattr *flow_attr, *cookie_attr;
struct nlattr *flow_tb[ASK_FLOW_ATTR_MAX + 1];
u64 cookie;
void *hdr;
int rc;

if (!t)
return -ENOENT;

flow_attr = info->attrs[ASK_ATTR_FLOW];
if (!flow_attr)
return -EINVAL;

rc = nla_parse_nested(flow_tb, ASK_FLOW_ATTR_MAX, flow_attr,
      ask_flow_policy, info->extack);
if (rc)
return rc;

cookie_attr = flow_tb[ASK_FLOW_ATTR_ID];
if (!cookie_attr)
return -EINVAL;
cookie = nla_get_u64(cookie_attr);

rep = genlmsg_new(NLMSG_DEFAULT_SIZE, GFP_KERNEL);
if (!rep)
return -ENOMEM;

hdr = genlmsg_put_reply(rep, info, &ask_genl_family, 0,
ASK_CMD_GET_FLOW);
if (!hdr) {
rc = -EMSGSIZE;
goto err;
}

rcu_read_lock();
f = ask_flow_lookup(t, cookie);
if (!f) {
rcu_read_unlock();
rc = -ENOENT;
goto err_cancel;
}
rc = ask_genl_fill_one_flow(rep, f);
rcu_read_unlock();
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

static int ask_genl_flush_flows_doit(struct sk_buff *skb,
     struct genl_info *info)
{
struct ask_flow_table *t = ask_flow_default_table();

if (!t)
return -ENOENT;

ask_flow_flush(t);
return 0;
}

