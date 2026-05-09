/*
 *
 *  Copyright (C) 2007 Mindspeed Technologies, Inc.
 *  Copyright 2015-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */
#ifndef _AUTO_BRIDGE_H
#define _AUTO_BRIDGE_H

#ifdef __KERNEL__
#include <linux/if_ether.h>
#include <linux/types.h>
#else
/* Userspace: avoid musl struct ethhdr redefinition by not including
 * <linux/if_ether.h>. Only ETH_ALEN is needed from that header. */
#include <sys/types.h>
#ifndef ETH_ALEN
#define ETH_ALEN 6
#endif
#endif

#ifndef NETLINK_L2FLOW
#define NETLINK_L2FLOW             33
#endif

#define	L2FLOW_NL_GRP			1

/* Msg type */
enum l2flow_msg_types{
	L2FLOW_MSG_BASE = 16, //msg_types < 0x10 are reserved control msg types
	L2FLOW_MSG_ENTRY,
	L2FLOW_MSG_RESET,
	L2FLOW_MSG_MAX
};

/* Flags used for CMM answers */
#define L2FLOW_OFFLOADED			0x1
#define L2FLOW_DENIED				0x2
#define L2FLOW_ACK				0x4

/* Actions */
enum l2flow_msg_actions{
	L2FLOW_ENTRY_NEW,
	L2FLOW_ENTRY_UPDATE,
	L2FLOW_ENTRY_DEL,
	L2FLOW_ENTRY_MAX,
};


/* L2flow netlink message header base */
struct l2flow_msg
{
	/* Minimal parameters */
	u_int8_t action;
	u_int32_t flags;
	u_int8_t saddr[ETH_ALEN];
	u_int8_t daddr[ETH_ALEN];
	u_int16_t ethertype;
};

enum{
	L2FLOWA_UNSPEC,
	L2FLOWA_SVLAN_TAG,
	L2FLOWA_CVLAN_TAG,
	L2FLOWA_PPP_S_ID,
	L2FLOWA_IIF_IDX,
	L2FLOWA_OIF_IDX,
	L2FLOWA_IP_SRC,
	L2FLOWA_IP_DST,
	L2FLOWA_IP_PROTO,
	L2FLOWA_SPORT,
	L2FLOWA_DPORT,
	L2FLOWA_MARK,
#ifdef VLAN_FILTER
	L2FLOWA_VID,
	L2FLOWA_VLAN_FLAGS,
#endif
	__L2FLOWA_MAX,
};

#define L2FLOWA_MAX (__L2FLOWA_MAX - 1)

/* RtNetlink style helper macros for CMM */
#define L2FLOWA_PAYLOAD(n) NLMSG_PAYLOAD(n,sizeof(struct l2flow_msg))
#define L2FLOWA_RTA(r)  ((struct rtattr*)(((char*)(r)) + NLMSG_ALIGN(sizeof(struct l2flow_msg))))


#endif
