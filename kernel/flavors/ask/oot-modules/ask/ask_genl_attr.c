// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - Generic netlink attribute policies
 *
 * Defines the nla_policy tables referenced by ask_genl.c. Per spec v0.7 §7,
 * all top-level attributes are NLA_NESTED containers; the inner policies
 * validate the contents of each nested block.
 *
 * Only ask_top_policy is consumed by the genl core in PR1 (since only
 * ASK_CMD_GET_INFO is wired). The other policies are defined now so that
 * later PRs can reference them when wiring additional commands without
 * having to touch this file again.
 */

#include <linux/module.h>
#include <net/netlink.h>
#include <uapi/linux/ask/ask.h>
#include "include/ask_internal.h"

/* Top-level: every command's payload is a single nested container. */
const struct nla_policy ask_top_policy[ASK_ATTR_MAX + 1] = {
[ASK_ATTR_INFO]    = { .type = NLA_NESTED },
[ASK_ATTR_MURAM]   = { .type = NLA_NESTED },
[ASK_ATTR_FLOW]    = { .type = NLA_NESTED },
[ASK_ATTR_SA]      = { .type = NLA_NESTED },
[ASK_ATTR_EVENT]   = { .type = NLA_NESTED },
[ASK_ATTR_POLICER] = { .type = NLA_NESTED },
};

const struct nla_policy ask_info_policy[ASK_INFO_ATTR_MAX + 1] = {
[ASK_INFO_ATTR_DRIVER_VERSION] = { .type = NLA_NUL_STRING, .len = 31 },
[ASK_INFO_ATTR_GENL_VERSION]   = { .type = NLA_U32 },
[ASK_INFO_ATTR_UCODE_FAMILY]   = { .type = NLA_U16 },
[ASK_INFO_ATTR_UCODE_MAJOR]    = { .type = NLA_U8 },
[ASK_INFO_ATTR_UCODE_MINOR]    = { .type = NLA_U8 },
[ASK_INFO_ATTR_UCODE_PATCH]    = { .type = NLA_U16 },
[ASK_INFO_ATTR_CAPABILITIES]   = { .type = NLA_U64 },
[ASK_INFO_ATTR_NUM_FMAN]       = { .type = NLA_U32 },
[ASK_INFO_ATTR_NUM_FLOWS]      = { .type = NLA_U32 },
[ASK_INFO_ATTR_MAX_FLOWS]      = { .type = NLA_U32 },
};
EXPORT_SYMBOL_GPL(ask_info_policy);

/*
 * The ask_top_policy and ask_flow_policy tables are referenced by the kunit
 * suite (PR9 / M1.5) — the genl_attr coverage tests build hand-rolled nlattr
 * streams and validate them through nla_validate_nested(). Other policy
 * tables (muram, sa, event, policer) become exportable when their respective
 * subsystem PRs need them; for now they live only inside ask.ko's address
 * space.
 */
EXPORT_SYMBOL_GPL(ask_top_policy);
EXPORT_SYMBOL_GPL(ask_flow_policy);

const struct nla_policy ask_muram_policy[ASK_MURAM_ATTR_MAX + 1] = {
[ASK_MURAM_ATTR_TOTAL_BYTES]      = { .type = NLA_U32 },
[ASK_MURAM_ATTR_FREE_BYTES]       = { .type = NLA_U32 },
[ASK_MURAM_ATTR_FLOW_TABLE_BYTES] = { .type = NLA_U32 },
};

const struct nla_policy ask_flow_policy[ASK_FLOW_ATTR_MAX + 1] = {
[ASK_FLOW_ATTR_ID]           = { .type = NLA_U64 },
[ASK_FLOW_ATTR_L3_PROTO]     = { .type = NLA_U16 },
[ASK_FLOW_ATTR_L4_PROTO]     = { .type = NLA_U8 },
[ASK_FLOW_ATTR_SRC_IP]       = { .type = NLA_BINARY, .len = 16 },
[ASK_FLOW_ATTR_DST_IP]       = { .type = NLA_BINARY, .len = 16 },
[ASK_FLOW_ATTR_SPORT]        = { .type = NLA_U16 },
[ASK_FLOW_ATTR_DPORT]        = { .type = NLA_U16 },
[ASK_FLOW_ATTR_IIF]          = { .type = NLA_U32 },
[ASK_FLOW_ATTR_OIF]          = { .type = NLA_U32 },
[ASK_FLOW_ATTR_PACKETS]      = { .type = NLA_U64 },
[ASK_FLOW_ATTR_BYTES]        = { .type = NLA_U64 },
[ASK_FLOW_ATTR_LAST_SEEN_NS] = { .type = NLA_U64 },
[ASK_FLOW_ATTR_HW_FLOW_ID]   = { .type = NLA_U32 },
};

const struct nla_policy ask_sa_policy[ASK_SA_ATTR_MAX + 1] = {
[ASK_SA_ATTR_HW_SA_ID] = { .type = NLA_U32 },
[ASK_SA_ATTR_SPI]      = { .type = NLA_U32 },
[ASK_SA_ATTR_FAMILY]   = { .type = NLA_U16 },
[ASK_SA_ATTR_DST]      = { .type = NLA_BINARY, .len = 16 },
[ASK_SA_ATTR_REQID]    = { .type = NLA_U32 },
[ASK_SA_ATTR_DIR]      = { .type = NLA_U8 },
[ASK_SA_ATTR_PACKETS]  = { .type = NLA_U64 },
[ASK_SA_ATTR_BYTES]    = { .type = NLA_U64 },
};

const struct nla_policy ask_event_policy[ASK_EVENT_ATTR_MAX + 1] = {
[ASK_EVENT_ATTR_TYPE]         = { .type = NLA_U32 },
[ASK_EVENT_ATTR_TIMESTAMP_NS] = { .type = NLA_U64 },
[ASK_EVENT_ATTR_FLOW_ID]      = { .type = NLA_U64 },
[ASK_EVENT_ATTR_SA_ID]        = { .type = NLA_U32 },
[ASK_EVENT_ATTR_REASON]       = { .type = NLA_U32 },
[ASK_EVENT_ATTR_MESSAGE]      = { .type = NLA_NUL_STRING },
};

const struct nla_policy ask_policer_policy[ASK_POLICER_ATTR_MAX + 1] = {
[ASK_POLICER_ATTR_PORT_ID]     = { .type = NLA_U8 },
[ASK_POLICER_ATTR_RATE_BPS]    = { .type = NLA_U32 },
[ASK_POLICER_ATTR_BURST_BYTES] = { .type = NLA_U32 },
};