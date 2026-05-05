/*
 *
 *  Copyright (C) 2007 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#ifndef __MODULE_RX_H__
#define __MODULE_RX_H__

#define L2FLOW_HASH_TABLE_SIZE 1024

#ifdef VLAN_FILTER
/* VLAN flags */
#define VLAN_FILTERED   0x1	/* Flag to check if vlan filtering is enabled on bridge */
#define VLAN_UNTAGGED   0x2	/* Flag to check if egress is configured as untagged */
#endif

/* L2flow definition*/
struct l2flow
{
	unsigned char saddr[ETH_ALEN];
	unsigned char daddr[ETH_ALEN];
	unsigned short ethertype;
	unsigned short session_id;
	unsigned short svlan_tag; /* S TCI */
	unsigned short cvlan_tag; /* C TCI */
#ifdef VLAN_FILTER
	unsigned short vid;
	unsigned char vlan_flags;
#endif
	/* L3 info optional */
	struct{
		union {
			unsigned int all[4];
			unsigned int ip;
			unsigned int ip6[4];
		}saddr;
		union {
			unsigned int all[4];
			unsigned int ip;
			unsigned int ip6[4];
		}daddr;
		unsigned char proto;
	}l3;
	struct{
		/* L4 info optional */
		unsigned short sport;
		unsigned short dport;
	}l4;
};


/* L2flow table entry definition*/
struct l2flowTable
{
	struct list_head list;
	int flags;
	char status;
	unsigned int idev_ifi;
	unsigned int odev_ifi;
	unsigned short mark;
	struct l2flow l2flow;
};

int parse_icc_interface(char *pstring, unsigned short *pinterface_number, int num_interfaces);

int cmmL2BridgeProcessClientCmd(FCI_CLIENT* fci_handle, int fc, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
int cmmRxSetProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmRxShowProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmRxQueryProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int parse_macaddr(char *pstring, unsigned char *pmacaddr);

extern struct list_head l2flow_table[L2FLOW_HASH_TABLE_SIZE];
extern pthread_mutex_t brMutex;

static inline unsigned int l2flow_hash(struct l2flow *l2flowtmp)
{	
	return (jhash(l2flowtmp, sizeof(struct l2flow), 0x12345678) & (L2FLOW_HASH_TABLE_SIZE - 1));
}
static inline int cmm_l2flow_cmp(struct l2flow *flow_a, struct l2flow *flow_b)
{
	return memcmp(flow_a, flow_b, sizeof(struct l2flow));
}
int cmm_l2flow_netlink_rcv(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg);
int __cmm_l2flow_deregister(FCI_CLIENT* fci_handler, struct l2flow *l2flow_tmp);
int __cmm_l2flow_register(FCI_CLIENT* fci_handler, char action, struct l2flow *l2flow_tmp, int iifi_idx, int oifi_idx, int flags, short mark);
int __cmm_l2flow_reset(FCI_CLIENT* fci_handler);
int cmm_l2flow_abm_notify(char action, int flags, struct l2flow *l2flow);
void cmm_l2flow_print(int level, struct l2flow *l2flow_tmp, char nl);
int cmmBridgeInit(struct cmm_ct *ctx);
int cmmBridgeControlProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
#endif

