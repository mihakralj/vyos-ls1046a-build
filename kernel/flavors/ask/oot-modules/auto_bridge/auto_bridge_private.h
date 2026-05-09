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

#ifndef _AUTO_BRIDGE_PRIVATE_H
#define _AUTO_BRIDGE_PRIVATE_H

#include <linux/version.h>

#define L2FLOW_HASH_TABLE_SIZE		1024
#define L2FLOW_HASH_BY_MAC_TABLE_SIZE 	128

#define ABM_DEFAULT_MAX_ENTRIES		5000

/* Internal flags */
#define L2FLOW_FL_NEEDS_UPDATE	0x1
#define L2FLOW_FL_DEAD			0x2
#define L2FLOW_FL_WAIT_ACK		0x4
#define L2FLOW_FL_PENDING_MSG		0x8

enum l2flow_state{
	L2FLOW_STATE_SEEN,
	L2FLOW_STATE_CONFIRMED,
	L2FLOW_STATE_LINUX,
	L2FLOW_STATE_FF,
	L2FLOW_STATE_DYING, // Intermediate state before effective deletion to give some time to CMM
	L2FLOW_STATE_MAX,
};

#ifdef VLAN_FILTER
/* VLAN flags */
#define VLAN_FILTERED	0x1	/* Flag to check if vlan filtering is enabled on bridge */
#define VLAN_UNTAGGED   0x2	/* Flag to check if egress is configured as untagged */
#endif

/* L2flow definition*/
struct l2flow
{
	u8 saddr[ETH_ALEN];
	u8 daddr[ETH_ALEN];
	u16 ethertype; 
	u16 session_id;
	u16 svlan_tag; /* S TCI only */ 
	u16 cvlan_tag; /* C TCI only */
#ifdef VLAN_FILTER
	u16 vid;
	u8 vlan_flags;
#endif
	/* L3 info optional */
	struct{
		union {
			u32 all[4];
			u32 ip;
			u32 ip6[4];
		}saddr;
		union {
			u32 all[4];
			u32 ip;
			u32 ip6[4];
		}daddr;
		u8 proto;
	}l3;
	struct{
		/* L4 info optional */
		u16 sport;
		u16 dport;
	}l4;
};


/* L2flow table entry definition*/
struct l2flowTable
{
	struct list_head list;
	struct list_head list_by_src_mac;
	struct list_head list_by_dst_mac;
	struct list_head list_wait_for_ack;
	struct list_head list_msg_to_send;

	unsigned char state;
	unsigned long time_sent;
	unsigned int flags;
	struct timer_list timeout;
	u32 idev_ifi;
	u32 odev_ifi;
	u16 packet_mark;
	struct l2flow l2flow;
};

struct br_event_table
{
	struct list_head list_rtevent;
	struct net_device *brdev;
};

#if LINUX_VERSION_CODE >= KERNEL_VERSION(3,6,0)
#define NLA_PUT(skb, attrtype, attrlen, data) \
        do { \
                if (nla_put(skb, attrtype, attrlen, data)) \
                        goto nla_put_failure; \
        } while(0)

#define NLA_PUT_U8(skb, attrtype, data) \
        do { \
                if (nla_put_u8(skb, attrtype, data)) \
                        goto nla_put_failure; \
        } while(0)

#define NLA_PUT_U16(skb, attrtype, data) \
        do { \
                if (nla_put_u16(skb, attrtype, data)) \
                        goto nla_put_failure; \
        } while(0)

#define NLA_PUT_U32(skb, attrtype, data) \
        do { \
                if (nla_put_u32(skb, attrtype, data)) \
                        goto nla_put_failure; \
        } while(0)
#endif


#define ABM_PRINT(type, info, args...) do {printk(type "ABM :" info, ## args);} while(0)

static inline void print_l2flow(struct l2flow *l2flowtmp)
{
	ABM_PRINT(KERN_DEBUG, "  Saddr : %02x:%02x:%02x:%02x:%02x:%02x\n", l2flowtmp->saddr[0], l2flowtmp->saddr[1], l2flowtmp->saddr[2],
															l2flowtmp->saddr[3], l2flowtmp->saddr[4], l2flowtmp->saddr[5]);
	ABM_PRINT(KERN_DEBUG, "  Daddr : %02x:%02x:%02x:%02x:%02x:%02x\n", l2flowtmp->daddr[0], l2flowtmp->daddr[1], l2flowtmp->daddr[2],
															l2flowtmp->daddr[3], l2flowtmp->daddr[4], l2flowtmp->daddr[5]);
	ABM_PRINT(KERN_DEBUG, "  Ethertype : %04x\n", htons(l2flowtmp->ethertype));
	ABM_PRINT(KERN_DEBUG, "  PPPoE Session id : %d\n", l2flowtmp->session_id);
}

#if 0
static inline unsigned int abm_l2_flow_hash(u8 *saddr,  u8 *daddr, u16 ethertype, 
	u32 session_id, u32 *ipsaddr, u32 *ipdaddr, u8 proto, u16 sport, u16 dport)
{
	u32 a, b, c, d , e;
	
	a = jhash((void *) saddr, 6, ethertype);
	b = jhash((void *) daddr, 6, session_id);
	c = 0;
	d = 0;

	if (ethertype == htons(ETH_P_IP))
	{
		c = jhash_2words(*ipsaddr, *ipdaddr, sport | (dport << 16));
	}
	else if (ethertype == htons(ETH_P_IPV6))
	{
		c = jhash2((void *) ipsaddr, 4, sport);
		d =jhash2((void *) ipdaddr, 4, dport);
	}
	
	return jhash_3words(a, b, c, d);
}
#endif
static inline unsigned int abm_l2flow_hash(struct l2flow *l2flowtmp)
{	
	return (jhash(l2flowtmp, sizeof(struct l2flow), 0x12345678) & (L2FLOW_HASH_TABLE_SIZE - 1));
}
static inline unsigned int abm_l2flow_hash_mac(char *src_mac)
{	
	return (jhash(src_mac, ETH_ALEN, 0x12345678) & (L2FLOW_HASH_BY_MAC_TABLE_SIZE - 1));
}

static inline int abm_l2flow_cmp(struct l2flow *flow_a, struct l2flow *flow_b);
static struct l2flowTable * abm_l2flow_find(struct l2flow *l2flowtmp);
static int abm_l2flow_msg_handle(char action, int flags, struct l2flow *l2flowtmp);
static struct l2flowTable *  abm_l2flow_add(struct l2flow *l2flowtmp);
static void abm_l2flow_del(struct l2flowTable *l2flow_entry);
static void abm_l2flow_update(int flags, struct l2flowTable *table_entry);
static  void abm_l2flow_table_flush(void);
extern void br_fdb_register_can_expire_cb(int(*cb)(unsigned char *mac_addr, struct net_device *dev));
extern void br_fdb_deregister_can_expire_cb(void);
static void abm_do_work_send_msg(struct work_struct *work);
static void abm_do_work_retransmit(struct work_struct *work);
static int abm_nl_send_l2flow_msg(struct sock *s, char action, int flags, struct l2flowTable *table_entry);
static void __abm_go_dying(struct l2flowTable *table_entry);


#endif
