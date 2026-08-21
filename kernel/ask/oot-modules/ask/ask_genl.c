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
#include <linux/fsl/fman_pcd.h>
#include "include/ask_internal.h"

/*
 * fman_get_pcd() is EXPORT_SYMBOL_GPL but declared only in the private
 * fsl_dpaa_fman driver header (fman/fman.h). ask.ko consumes the exported
 * symbol, so declare the prototype here (matching fman.h).
 */
extern struct fman_pcd *fman_get_pcd(struct fman *fman);

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
static int ask_genl_get_muram_doit(struct sk_buff *skb, struct genl_info *info);
static int ask_genl_engage_doit(struct sk_buff *skb, struct genl_info *info);
static int ask_genl_disengage_doit(struct sk_buff *skb, struct genl_info *info);
static int ask_genl_set_policer_doit(struct sk_buff *skb, struct genl_info *info);
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
u32            portid;  /* NETLINK_CB portid for the per-flow genlmsg */
u32            seq;     /* dump request nlmsg_seq for the per-flow genlmsg */
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
.doit     = ask_genl_get_muram_doit,
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
.doit     = ask_genl_set_policer_doit,
},
{
.cmd      = ASK_CMD_ENGAGE,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_engage_doit,
},
{
.cmd      = ASK_CMD_DISENGAGE,
.validate = GENL_DONT_VALIDATE_STRICT | GENL_DONT_VALIDATE_DUMP,
.flags    = GENL_UNS_ADMIN_PERM,
.doit     = ask_genl_disengage_doit,
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
.resv_start_op = ASK_CMD_DISENGAGE + 1,
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

/*
 * Production capability/status telemetry. Do not advertise planned features:
 * the shipping hardware path is plain routed IPv4 TCP/UDP only; IPv6, NAT,
 * VLAN, bridge and ESP deliberately fall back to software. ASK_CAP_IPV4 means
 * that specific path is compiled and exposed, not that a flow is live now.
 */
if (nla_put_u64_64bit(skb, ASK_INFO_ATTR_CAPABILITIES, ASK_CAP_IPV4,
      ASK_INFO_ATTR_UNSPEC))
goto nla_put_failure;

{
struct ask_flow_table *t = ask_flow_default_table();
u32 num_fman = ask_hw_get_fman() ? 1 : 0;
u32 num_flows = t ? (u32)atomic_read(&t->num_flows) : 0;

if (nla_put_u32(skb, ASK_INFO_ATTR_NUM_FMAN, num_fman))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_INFO_ATTR_NUM_FLOWS, num_flows))
goto nla_put_failure;
}
/* No fixed per-flow ceiling is exported yet: the external-hash table uses
 * collision chains and coherent DDR records. Zero means "not a fixed limit",
 * not "zero capacity". Resource failures are reported by the A4 preflight. */
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
/* ASK_CMD_GET_MURAM handler                                                   */
/*                                                                            */
/* Returns the FMan PCD MURAM budget as a nested ASK_ATTR_MURAM set.           */
/* Production equivalent of the fman_pcd debugfs muram_budget node: the        */
/* "no debugfs in production" plan (plans/ASK2-PRODUCTION-ARCHITECTURE.md)     */
/* requires this info be reachable over genl, not /sys/kernel/debug.           */
/*                                                                            */
/* The kernel-side data already exists (fman_pcd_get_muram_budget() +          */
/* fman_get_pcd() are exported); this handler only wires it onto the wire.     */
/* No FMan handle (module not brought up / not bound) -> empty budget fields   */
/* and rc=0, matching the "no microcode info" contract userspace already       */
/* understands for GET_INFO.                                                   */
/* ------------------------------------------------------------------------- */
static int ask_genl_fill_muram(struct sk_buff *skb)
{
	struct fman *fman;
	struct fman_pcd *pcd;
	struct fman_pcd_muram_budget b;
	struct nlattr *nest;
	bool have;

	memset(&b, 0, sizeof(b));

	fman = ask_hw_get_fman();
	pcd = fman ? fman_get_pcd(fman) : NULL;
	have = pcd != NULL;
	if (have)
		b = fman_pcd_get_muram_budget(pcd);

	nest = nla_nest_start(skb, ASK_ATTR_MURAM);
	if (!nest)
		return -EMSGSIZE;

	if (!have) {
		/* No PCD: report zeros (no MURAM budget info). */
		nla_nest_end(skb, nest);
		return 0;
	}

	if (nla_put_u32(skb, ASK_MURAM_ATTR_TOTAL_BYTES,
			(u32)b.reserved_bytes) ||
	    nla_put_u32(skb, ASK_MURAM_ATTR_FREE_BYTES,
			(u32)b.free_bytes) ||
	    nla_put_u32(skb, ASK_MURAM_ATTR_FLOW_TABLE_BYTES,
			(u32)b.used_bytes)) {
		nla_nest_cancel(skb, nest);
		return -EMSGSIZE;
	}

	nla_nest_end(skb, nest);
	return 0;
}

static int ask_genl_get_muram_doit(struct sk_buff *skb, struct genl_info *info)
{
	struct sk_buff *rep;
	void *hdr;
	int rc;

	rep = genlmsg_new(NLMSG_DEFAULT_SIZE, GFP_KERNEL);
	if (!rep)
		return -ENOMEM;

	hdr = genlmsg_put_reply(rep, info, &ask_genl_family, 0,
				ASK_CMD_GET_MURAM);
	if (!hdr) {
		rc = -EMSGSIZE;
		goto err;
	}

	rc = ask_genl_fill_muram(rep);
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
/* ASK_CMD_SET_POLICER handler                                                */
/*                                                                            */
/* Programs one srTCM (RFC 2697) policer profile on a port via the exported   */
/* fman_pcd_plcr_install(). srTCM = single-rate two-color: committed rate +   */
/* committed burst. The genl request carries a nested ASK_ATTR_POLICER set    */
/* (port-id, rate-bps, burst-bytes) per kernel/ask/uapi/ask.yaml.             */
/*                                                                            */
/* Phase 1 (plans/ASK2-PRODUCTION-ARCHITECTURE.md): genl is the production    */
/* control surface; this replaces any future debugfs policer knob. The        */
/* implementation is a thin wire of the already-exported kernel API — no      */
/* silicon encode logic lives in ask.ko.                                      */
/* ------------------------------------------------------------------------- */
static int ask_genl_set_policer_doit(struct sk_buff *skb, struct genl_info *info)
{
	struct fman_pcd_plcr_hw_profile hw;
	struct fman *fman;
	struct fman_pcd *pcd;
	struct nlattr *nest;
	struct nlattr *tb[ASK_POLICER_ATTR_MAX + 1];
	u8 port_id;
	u32 rate_bps;
	u32 burst_bytes;
	int rc;

	nest = info->attrs[ASK_ATTR_POLICER];
	if (!nest)
		return -EINVAL;

	rc = nla_parse_nested(tb, ASK_POLICER_ATTR_MAX, nest, ask_policer_policy,
			      info->extack);
	if (rc)
		return rc;

	if (!tb[ASK_POLICER_ATTR_PORT_ID] ||
	    !tb[ASK_POLICER_ATTR_RATE_BPS] ||
	    !tb[ASK_POLICER_ATTR_BURST_BYTES])
		return -EINVAL;

	port_id   = nla_get_u8(tb[ASK_POLICER_ATTR_PORT_ID]);
	rate_bps  = nla_get_u32(tb[ASK_POLICER_ATTR_RATE_BPS]);
	burst_bytes = nla_get_u32(tb[ASK_POLICER_ATTR_BURST_BYTES]);

	fman = ask_hw_get_fman();
	pcd = fman ? fman_get_pcd(fman) : NULL;
	if (!pcd)
		return -ENODEV;

	memset(&hw, 0, sizeof(hw));
	hw.trtcm       = false;	       /* srTCM: single-rate two-color */
	hw.color_aware = false;	       /* colour-blind */
	hw.cir_bps     = rate_bps;
	hw.cbs_bytes   = burst_bytes;
	hw.pir_bps     = 0;	       /* N/A for srTCM */
	hw.pbs_bytes   = 0;	       /* N/A for srTCM */

	/* profile_id == port_id (one srTCM policer slot per port). */
	rc = fman_pcd_plcr_install(pcd, port_id, port_id, &hw);
	if (rc) {
		ask_pr_err("genl: set_policer port 0x%02x failed: %d\n",
			   port_id, rc);
		return rc;
	}

	ask_pr_info("genl: set_policer port 0x%02x rate=%u bps burst=%u\n",
		    port_id, rate_bps, burst_bytes);
	return 0;
}
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
/* rhashtable + RCU table created in ask_flow.c. The wire format is FLAT      */
/* (T-M7-5): each dumped flow is its own NLM_F_MULTI message and GET_FLOW     */
/* replies with a single message, both carrying the ASK_FLOW_ATTR_* set at    */
/* the genl message top level — matching the flat ask.yaml `flow` set and the */
/* idiomatic mainline one-object-per-message dump. Per-flow content:          */
/*   ASK_FLOW_ATTR_COOKIE     (u64)                                           */
/*   ASK_FLOW_ATTR_OFFLOADED  (u8)   — 1 = in silicon, 0 = SW path only      */
/*   ASK_FLOW_ATTR_HW_FLOW_ID (u32)  — opaque; emitted only when offloaded   */
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
u64 packets, bytes, last_seen_ns;
unsigned int seq;
u16 l3proto;
int iplen;

/*
 * T-M7-5: emit the flow's attributes FLAT (no ASK_ATTR_FLOW wrapper).
 * Each dumped flow is its own NLM_F_MULTI message (dump_one_cb frames one
 * genlmsg per flow) and get-flow returns a single message, so these
 * attributes sit directly at the genl message top level — matching the
 * flat genetlink-legacy layout ask.yaml declares and the idiomatic
 * mainline one-object-per-message dump shape. Callers own the genlmsg
 * header/cancel; this helper only appends attributes.
 */
if (nla_put_u64_64bit(skb, ASK_FLOW_ATTR_ID, f->cookie,
      ASK_FLOW_ATTR_UNSPEC))
goto nla_put_failure;
/*
 * Only a HW-backed flow has a meaningful id. A software-fallback flow
 * carries a counter value drawn from a space that overlaps real xarray
 * cookies, so emitting it let two different flows report the same
 * hw-flow-id and gave operators no way to tell an offloaded flow from
 * one the hardware refused. Report the state explicitly and omit the
 * id when it would be meaningless.
 */
if (nla_put_u8(skb, ASK_FLOW_ATTR_OFFLOADED, f->hw_backed ? 1 : 0))
goto nla_put_failure;
if (f->hw_backed &&
    nla_put_u32(skb, ASK_FLOW_ATTR_HW_FLOW_ID, f->hw_flow_id))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_FLOW_ATTR_OIF, f->oif))
goto nla_put_failure;

/*
 * T-M7-2: emit the 5-tuple + ingress ifindex so op-mode
 * `show interfaces ethernet eth<n> offload ask flows` can render each
 * flow and filter by interface (iif/oif) without a second lookup.
 * key.l3_proto is the internal ASK_FLOW_L3_* enum; the UAPI attr carries
 * the EtherType (ETH_P_IP / ETH_P_IPV6) the ask.yaml spec documents.
 * src_ip/dst_ip are u8[16] with the v4 address packed into the first 4.
 * sport/dport are already __be16, so nla_put_be16() keeps them on the
 * wire in the big-endian order the spec declares.
 */
l3proto = (f->key.l3_proto == ASK_FLOW_L3_IPV6) ? ETH_P_IPV6 : ETH_P_IP;
iplen   = (f->key.l3_proto == ASK_FLOW_L3_IPV6) ? 16 : 4;
if (nla_put_u16(skb, ASK_FLOW_ATTR_L3_PROTO, l3proto))
goto nla_put_failure;
if (nla_put_u8(skb, ASK_FLOW_ATTR_L4_PROTO, f->key.l4_proto))
goto nla_put_failure;
if (nla_put(skb, ASK_FLOW_ATTR_SRC_IP, iplen, f->key.src_ip))
goto nla_put_failure;
if (nla_put(skb, ASK_FLOW_ATTR_DST_IP, iplen, f->key.dst_ip))
goto nla_put_failure;
if (nla_put_be16(skb, ASK_FLOW_ATTR_SPORT, f->key.sport))
goto nla_put_failure;
if (nla_put_be16(skb, ASK_FLOW_ATTR_DPORT, f->key.dport))
goto nla_put_failure;
if (nla_put_u32(skb, ASK_FLOW_ATTR_IIF, f->key.iif))
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

return 0;

nla_put_failure:
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
void *hdr;
int rc;

if (ctx->seen < ctx->start) {
ctx->seen++;
return 0;
}

/*
 * One flow == one NLM_F_MULTI genl message, attributes emitted FLAT.
 * This is the idiomatic mainline dump shape and matches the flat
 * ask.yaml `flow` attribute-set (ynl decodes each message's top-level
 * attributes against that set). A failed genlmsg_put / fill means the
 * dump skb is full: stop the walk with -EMSGSIZE so the dumpit re-runs
 * from ctx->seen on the next netlink invocation.
 */
hdr = genlmsg_put(ctx->skb, ctx->portid, ctx->seq, &ask_genl_family,
  NLM_F_MULTI, ASK_CMD_DUMP_FLOWS);
if (!hdr) {
ctx->err = -EMSGSIZE;
return -EMSGSIZE;
}

rc = ask_genl_fill_one_flow(ctx->skb, f);
if (rc) {
genlmsg_cancel(ctx->skb, hdr);
ctx->err = rc;
return rc;
}

genlmsg_end(ctx->skb, hdr);
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

if (!t)
return 0; /* table not initialised → empty dump */

ctx.skb    = skb;
ctx.start  = cb->args[0];
ctx.portid = NETLINK_CB(cb->skb).portid;
ctx.seq    = cb->nlh->nlmsg_seq;

/*
 * Emit as many flows as fit into this skb, one genl message each, then
 * let netlink call us again (cb->args[0] advanced past what we emitted).
 * When the walker emits nothing this round the table is exhausted (or a
 * later call has skipped every entry): returning 0 ends the dump and
 * netlink appends NLMSG_DONE. This naturally terminates on an empty
 * table — no header is written when count == 0 — so there is no infinite
 * dump the way a bare genlmsg_put per call would produce.
 */
ask_flow_walk(t, ask_genl_dump_one_cb, &ctx);

if (ctx.count == 0)
return ctx.err ? ctx.err : 0;

cb->args[0] = ctx.seen;
return skb->len;
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

/* ------------------------------------------------------------------------- */
/* ASK_CMD_ENGAGE / ASK_CMD_DISENGAGE handlers                                */
/*                                                                            */
/* These commands engage/disengage the ASK2 hardware offload on a specific    */
/* FMan port. The port_id parameter is the hardware port ID (e.g., 0x10 for   */
/* eth3, 0x11 for eth4 on LS1046A).                                           */
/*                                                                            */
/* Unlike the debugfs bridge (vyos-offload-ask), these handlers use the       */
/* proper fman_pcd_offload_engage()/disengage() API which correctly manages   */
/* BMI port state and doesn't corrupt the RX path.                            */
/* ------------------------------------------------------------------------- */
static int ask_genl_engage_doit(struct sk_buff *skb, struct genl_info *info)
{
	struct nlattr *port_attr;
	struct nlattr *fam_attr;
	u8 port_id;
	u8 fam_mask;
	int rc;

	port_attr = info->attrs[ASK_ATTR_PORT_ID];
	if (!port_attr)
		return -EINVAL;

	port_id = nla_get_u8(port_attr);

	/*
	 * ASK_ATTR_FAMILY_MASK selects which L3 families this port offloads
	 * (CLI `offload ipv4` / `offload ipv6`). Absent => both, so callers
	 * that predate the split get the historical behaviour. Set the mask
	 * BEFORE engage so admission is correct from the first frame.
	 */
	fam_attr = info->attrs[ASK_ATTR_FAMILY_MASK];
	fam_mask = fam_attr ? nla_get_u8(fam_attr) : (ASK_FAM_V4 | ASK_FAM_V6);
	if (!fam_mask || (fam_mask & ~(ASK_FAM_V4 | ASK_FAM_V6)))
		return -EINVAL;	/* engage with no/unknown family is nonsensical */

	ask_hw_offload_set_family(port_id, fam_mask);

	rc = ask_hw_offload_engage(port_id);
	if (rc) {
		ask_pr_err("genl: engage port 0x%02x failed: %d\n", port_id, rc);
		return rc;
	}

	ask_pr_info("genl: engaged port 0x%02x family_mask=0x%x\n",
		    port_id, fam_mask);
	return 0;
}

static int ask_genl_disengage_doit(struct sk_buff *skb, struct genl_info *info)
{
struct nlattr *port_attr;
u8 port_id;

port_attr = info->attrs[ASK_ATTR_PORT_ID];
if (!port_attr)
return -EINVAL;

port_id = nla_get_u8(port_attr);

ask_hw_offload_disengage(port_id);

ask_pr_info("genl: disengaged port 0x%02x\n", port_id);
return 0;
}

