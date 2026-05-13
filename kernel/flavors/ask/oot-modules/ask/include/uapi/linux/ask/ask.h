/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ASK2 generic-netlink UAPI.
 *
 * The kernel module exposes a single GENL family named "ask" (version 1)
 * with three multicast groups (events, flows, sas).
 *
 * Per spec §7: there are NO ASK_CMD_FLOW_ADD / ASK_CMD_SA_ADD operations.
 * Flow and SA insertion go through nf_flow_table + xfrmdev_ops which is
 * what mainline drivers do. Operators talk to mainline subsystems; the
 * kernel routes the work to ask.ko via callbacks. This is the inversion
 * that matters: the legacy stack made userspace push flows; the modern
 * stack lets the kernel pull them through standard infrastructure.
 *
 * See specs/ask2-rewrite-spec.md §7 for the full protocol.
 */
#ifndef _UAPI_LINUX_ASK_H
#define _UAPI_LINUX_ASK_H

#include <linux/types.h>

#define ASK_GENL_NAME           "ask"
#define ASK_GENL_VERSION        1

/* Multicast groups */
#define ASK_MCGRP_EVENTS_NAME   "events"
#define ASK_MCGRP_FLOWS_NAME    "flows"
#define ASK_MCGRP_SAS_NAME      "sas"

enum ask_genl_mcgrp {
    ASK_MCGRP_EVENTS,       /* lifecycle, MURAM, ucode errors */
    ASK_MCGRP_FLOWS,        /* flow add/del/evict events */
    ASK_MCGRP_SAS,          /* SA add/del/expire events */

    __ASK_MCGRP_MAX,
};
#define ASK_MCGRP_MAX (__ASK_MCGRP_MAX - 1)

/* Commands */
enum ask_genl_cmd {
    ASK_CMD_UNSPEC,

    /* Query operations (CAP_NET_ADMIN) */
    ASK_CMD_GET_INFO,       /* version, capabilities, ucode info */
    ASK_CMD_GET_MURAM,      /* MURAM allocation report */
    ASK_CMD_DUMP_FLOWS,     /* dump all installed flows */
    ASK_CMD_GET_FLOW,       /* query single flow by id */
    ASK_CMD_DUMP_SAS,       /* dump all installed SAs */

    /* Control operations (CAP_NET_ADMIN) */
    ASK_CMD_FLUSH_FLOWS,    /* admin: remove all flows */
    ASK_CMD_FLUSH_SAS,      /* admin: remove all SAs */
    ASK_CMD_SET_POLICER,    /* exception-rate-limit configuration */

    __ASK_CMD_MAX,
};
#define ASK_CMD_MAX (__ASK_CMD_MAX - 1)

/* Top-level attributes */
enum ask_genl_attr {
    ASK_ATTR_UNSPEC,

    ASK_ATTR_INFO,          /* nested ask_info_attr */
    ASK_ATTR_MURAM,         /* nested ask_muram_attr */
    ASK_ATTR_FLOW,          /* nested ask_flow_attr */
    ASK_ATTR_SA,            /* nested ask_sa_attr */
    ASK_ATTR_EVENT,         /* nested ask_event_attr */
    ASK_ATTR_POLICER,       /* nested ask_policer_attr */

    __ASK_ATTR_MAX,
};
#define ASK_ATTR_MAX (__ASK_ATTR_MAX - 1)

/* ASK_ATTR_INFO nested attributes */
enum ask_info_attr {
    ASK_INFO_ATTR_UNSPEC,

    ASK_INFO_ATTR_DRIVER_VERSION,   /* string, e.g. "ask2.0" */
    ASK_INFO_ATTR_GENL_VERSION,     /* u32 */
    ASK_INFO_ATTR_UCODE_FAMILY,     /* u16 (e.g. 0x0210) */
    ASK_INFO_ATTR_UCODE_MAJOR,      /* u8 */
    ASK_INFO_ATTR_UCODE_MINOR,      /* u8 */
    ASK_INFO_ATTR_UCODE_PATCH,      /* u16 */
    ASK_INFO_ATTR_CAPABILITIES,     /* u64 bitmap */
    ASK_INFO_ATTR_NUM_FMAN,         /* u32 */
    ASK_INFO_ATTR_NUM_FLOWS,        /* u32 currently installed */
    ASK_INFO_ATTR_MAX_FLOWS,        /* u32 budget */

    __ASK_INFO_ATTR_MAX,
};
#define ASK_INFO_ATTR_MAX (__ASK_INFO_ATTR_MAX - 1)

/* ASK_ATTR_MURAM nested attributes */
enum ask_muram_attr {
    ASK_MURAM_ATTR_UNSPEC,

    ASK_MURAM_ATTR_TOTAL_BYTES,     /* u32 */
    ASK_MURAM_ATTR_FREE_BYTES,      /* u32 */
    ASK_MURAM_ATTR_FLOW_TABLE_BYTES,/* u32 */

    __ASK_MURAM_ATTR_MAX,
};
#define ASK_MURAM_ATTR_MAX (__ASK_MURAM_ATTR_MAX - 1)

/* ASK_ATTR_FLOW nested attributes */
enum ask_flow_attr {
    ASK_FLOW_ATTR_UNSPEC,

    ASK_FLOW_ATTR_ID,           /* u64 cookie (matches flow_offload) */
    ASK_FLOW_ATTR_L3_PROTO,     /* u16: ETH_P_IP | ETH_P_IPV6 */
    ASK_FLOW_ATTR_L4_PROTO,     /* u8 */
    ASK_FLOW_ATTR_SRC_IP,       /* binary, 4 or 16 bytes */
    ASK_FLOW_ATTR_DST_IP,       /* binary, 4 or 16 bytes */
    ASK_FLOW_ATTR_SPORT,        /* u16 NBO */
    ASK_FLOW_ATTR_DPORT,        /* u16 NBO */
    ASK_FLOW_ATTR_IIF,          /* u32 ifindex */
    ASK_FLOW_ATTR_OIF,          /* u32 ifindex */
    ASK_FLOW_ATTR_PACKETS,      /* u64 */
    ASK_FLOW_ATTR_BYTES,        /* u64 */
    ASK_FLOW_ATTR_LAST_SEEN_NS, /* u64 */
    ASK_FLOW_ATTR_HW_FLOW_ID,   /* u32, opaque */

    __ASK_FLOW_ATTR_MAX,
};
#define ASK_FLOW_ATTR_MAX (__ASK_FLOW_ATTR_MAX - 1)

/* ASK_ATTR_SA nested attributes */
enum ask_sa_attr {
    ASK_SA_ATTR_UNSPEC,

    ASK_SA_ATTR_HW_SA_ID,       /* u32 */
    ASK_SA_ATTR_SPI,            /* u32 NBO */
    ASK_SA_ATTR_FAMILY,         /* u16: AF_INET | AF_INET6 */
    ASK_SA_ATTR_DST,            /* binary 4 or 16 bytes */
    ASK_SA_ATTR_REQID,          /* u32 */
    ASK_SA_ATTR_DIR,            /* u8: 0=in, 1=out */
    ASK_SA_ATTR_PACKETS,        /* u64 */
    ASK_SA_ATTR_BYTES,          /* u64 */

    __ASK_SA_ATTR_MAX,
};
#define ASK_SA_ATTR_MAX (__ASK_SA_ATTR_MAX - 1)

/* ASK_ATTR_EVENT nested attributes (multicast) */
enum ask_event_attr {
    ASK_EVENT_ATTR_UNSPEC,

    ASK_EVENT_ATTR_TYPE,        /* u32, see ask_event_type */
    ASK_EVENT_ATTR_TIMESTAMP_NS,/* u64 */
    ASK_EVENT_ATTR_FLOW_ID,     /* u64 (optional) */
    ASK_EVENT_ATTR_SA_ID,       /* u32 (optional) */
    ASK_EVENT_ATTR_REASON,      /* u32 (optional) */
    ASK_EVENT_ATTR_MESSAGE,     /* string (optional) */

    __ASK_EVENT_ATTR_MAX,
};
#define ASK_EVENT_ATTR_MAX (__ASK_EVENT_ATTR_MAX - 1)

enum ask_event_type {
    ASK_EVENT_READY = 1,
    ASK_EVENT_GOING_DOWN,
    ASK_EVENT_FLOW_ADDED,
    ASK_EVENT_FLOW_REMOVED,
    ASK_EVENT_FLOW_EVICTED,
    ASK_EVENT_TABLE_FULL,
    ASK_EVENT_UCODE_ERROR,
    ASK_EVENT_SA_ADDED,
    ASK_EVENT_SA_REMOVED,
    ASK_EVENT_SA_EXPIRED,
};

/* ASK_ATTR_POLICER nested attributes */
enum ask_policer_attr {
    ASK_POLICER_ATTR_UNSPEC,

    ASK_POLICER_ATTR_PORT_ID,       /* u8 */
    ASK_POLICER_ATTR_RATE_BPS,      /* u32 */
    ASK_POLICER_ATTR_BURST_BYTES,   /* u32 */

    __ASK_POLICER_ATTR_MAX,
};
#define ASK_POLICER_ATTR_MAX (__ASK_POLICER_ATTR_MAX - 1)

/* Capability bits (ASK_INFO_ATTR_CAPABILITIES) */
#define ASK_CAP_IPV4            (1ULL << 0)
#define ASK_CAP_IPV6            (1ULL << 1)
#define ASK_CAP_NAT             (1ULL << 2)
#define ASK_CAP_PAT             (1ULL << 3)
#define ASK_CAP_BRIDGE          (1ULL << 4)
#define ASK_CAP_MULTICAST       (1ULL << 5)
#define ASK_CAP_VLAN            (1ULL << 6)
#define ASK_CAP_ESP_OFFLOAD     (1ULL << 7)
#define ASK_CAP_POLICER         (1ULL << 8)

#endif /* _UAPI_LINUX_ASK_H */