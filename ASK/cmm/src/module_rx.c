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
#include "cmm.h"
#include "itf.h"
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define UNKNOWN_CMD	0
#define ICC_CMD		1
#define	BRIDGE_CMD	2

#ifdef WIFI_ENABLE
extern struct wifi_ff_entry glbl_wifi_ff_ifs[MAX_WIFI_FF_IFS];
#endif

struct list_head l2flow_table[L2FLOW_HASH_TABLE_SIZE];
pthread_mutex_t brMutex = PTHREAD_MUTEX_INITIALIZER;		/*mutex to prevent race condition on the route table*/

#ifdef AUTO_BRIDGE

/* This function allocates and add an entry into flow_table */
static int __cmm_l2flow_del(struct l2flowTable * l2flow_entry)
{
	list_del(&l2flow_entry->list);
	free(l2flow_entry);
	return 0;  
}

/* This function allocates and add an entry into flow_table */
static struct l2flowTable * __cmm_l2flow_add(struct l2flow *l2flowtmp)
{
	unsigned int key;
	struct l2flowTable* l2flow_entry;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	key = l2flow_hash(l2flowtmp);
	l2flow_entry = malloc(sizeof(*l2flow_entry)); 
	if(!l2flow_entry){
		cmm_print(DEBUG_ERROR, "Out of memory\n");
		goto out;
	}
	memset(l2flow_entry, 0, sizeof(*l2flow_entry));
	memcpy(&l2flow_entry->l2flow, l2flowtmp, sizeof(*l2flowtmp));
	
	list_add(&l2flow_table[key], &l2flow_entry->list);
	
	cmm_print(DEBUG_INFO, "Entry added, hash = %d \n", key);
out:
	return l2flow_entry;
}


static struct l2flowTable * __cmm_l2flow_find(struct l2flow *l2flowtmp)
{
	int key;
	struct list_head *entry;
	struct l2flowTable *table_entry;
	
	key = l2flow_hash(l2flowtmp);
	
	entry = list_first(&l2flow_table[key]);

	while (entry != &l2flow_table[key])
	{
		table_entry = container_of(entry, struct l2flowTable, list);
		if(!cmm_l2flow_cmp(&table_entry->l2flow, l2flowtmp))
			return table_entry; // Found

		entry = list_next(entry);
	}
	return NULL; // Not found 

}
void cmm_l2flow_print(int level, struct l2flow *l2flow_tmp, char nl)
{

	cmm_print(level, "Saddr=%02x:%02x:%02x:%02x:%02x:%02x ", l2flow_tmp->saddr[0], l2flow_tmp->saddr[1],
						l2flow_tmp->saddr[2], l2flow_tmp->saddr[3], l2flow_tmp->saddr[4], l2flow_tmp->saddr[5]);
	cmm_print(level, "Daddr=%02x:%02x:%02x:%02x:%02x:%02x ", l2flow_tmp->daddr[0], l2flow_tmp->daddr[1],
						l2flow_tmp->daddr[2], l2flow_tmp->daddr[3], l2flow_tmp->daddr[4], l2flow_tmp->daddr[5]);
	cmm_print(level, "Ethertype=%04x ", ntohs(l2flow_tmp->ethertype));
	cmm_print(level, "PPPoE session id=%d ", ntohs(l2flow_tmp->session_id));
	cmm_print(level, "SVlan tag=%d ", ntohs(l2flow_tmp->svlan_tag));
	cmm_print(level, "CVlan tag=%d ", ntohs(l2flow_tmp->cvlan_tag));
#ifdef VLAN_FILTER
	cmm_print(level, "Vlan filter=%d ",(l2flow_tmp->vlan_flags & VLAN_FILTERED) ? 1 : 0);
	if (l2flow_tmp->vlan_flags & VLAN_FILTERED) {
		cmm_print(level, "Vid=%d ", l2flow_tmp->vid);
		cmm_print(level, "Egress untagged=%d ", (l2flow_tmp->vlan_flags & VLAN_UNTAGGED) ? 1 : 0);
	}
#endif
	cmm_print(level, "Proto=%d ", l2flow_tmp->l3.proto);

	if(l2flow_tmp->l3.proto)
	{
		int family = 0;
		char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN];

		if(l2flow_tmp->ethertype == htons(ETH_P_IP))
			family = AF_INET;
		else if (l2flow_tmp->ethertype == htons(ETH_P_IPV6))
			family = AF_INET6;

		if(family)
			cmm_print(level, "   L3 info src=%s dst=%s sport=%d dport=%d ", 
				inet_ntop(family, l2flow_tmp->l3.saddr.all, saddr_buf, INET6_ADDRSTRLEN),
				inet_ntop(family, l2flow_tmp->l3.daddr.all, daddr_buf, INET6_ADDRSTRLEN),
				ntohs(l2flow_tmp->l4.sport), ntohs(l2flow_tmp->l4.dport));
	}
	if(nl)
		cmm_print(level, "\n");
}

int __cmm_l2flow_deregister(FCI_CLIENT* fci_handler, struct l2flow *l2flow_tmp)
{
	struct l2flowTable *l2flow_entry = NULL;

	cmm_print(DEBUG_INFO, "%s\n", __func__);
	cmm_l2flow_print(DEBUG_INFO, l2flow_tmp, 1);
	
	l2flow_entry = __cmm_l2flow_find(l2flow_tmp);
	if(!l2flow_entry){
		cmm_print(DEBUG_INFO, "%s Can't find l2flow entry\n", __func__);
	}
	else{
		if(!cmmFeL2FlowUpdate(fci_handler, REMOVE, l2flow_entry))
			__cmm_l2flow_del(l2flow_entry);
		
		cmm_print(DEBUG_INFO, "%s L2 entry successfully deleted\n", __func__);
	}
	/* In all case it is needed to ack ABM whatever */
	cmm_l2flow_abm_notify(L2FLOW_ENTRY_DEL, L2FLOW_ACK, l2flow_tmp);

	return 0;
}

int __cmm_l2flow_register(FCI_CLIENT* fci_handler, char action, struct l2flow *l2flow_tmp, int iifi_idx, int oifi_idx, int flags, short mark)
{
	struct l2flowTable * entry = NULL;
	char allowed = 0;

	cmm_print(DEBUG_INFO, "%s\n", __func__);
	cmm_l2flow_print(DEBUG_INFO, l2flow_tmp, 0);

	cmm_print(DEBUG_INFO, "Input itf idx = %d ", iifi_idx);
	cmm_print(DEBUG_INFO, "Output itf idx = %d ", oifi_idx);
	cmm_print(DEBUG_INFO, "Mark = %0x4\n", mark);

	
	entry = __cmm_l2flow_find(l2flow_tmp);
	if(!entry){
		if((entry = __cmm_l2flow_add(l2flow_tmp)) == NULL){
			cmm_print(DEBUG_INFO, "%s error l2flow add failed\n", __func__);
			goto fail;
		}
		entry->flags |= FPP_NEEDS_UPDATE;
	}
	
	if(__itf_is_programmed(iifi_idx) && __itf_is_programmed(oifi_idx)){
		cmm_print(DEBUG_INFO, "L2Flow Entry allowed for input itf index %d to output itf index %d\n",
					iifi_idx, oifi_idx);
		allowed = 1;
	}
	else{
		cmm_print(DEBUG_INFO, "L2Flow Entry not allowed for input itf index %d to output itf index %d\n",
					iifi_idx, oifi_idx);
		entry->flags &= ~FPP_NEEDS_UPDATE;
	}
	
	if((entry->idev_ifi != iifi_idx) || (entry->odev_ifi  != oifi_idx))
		entry->flags |= FPP_NEEDS_UPDATE;

	entry->idev_ifi = iifi_idx;
	entry->odev_ifi = oifi_idx;
	entry->mark = mark;

	if(allowed){
		if(!cmmFeL2FlowUpdate(fci_handler, ADD|UPDATE, entry))
			cmm_l2flow_abm_notify(L2FLOW_ENTRY_UPDATE, L2FLOW_OFFLOADED | L2FLOW_ACK, &entry->l2flow);
		else
			cmm_l2flow_abm_notify(L2FLOW_ENTRY_UPDATE, L2FLOW_DENIED | L2FLOW_ACK, &entry->l2flow);
	}
	else{
		cmmFeL2FlowUpdate(fci_handler, REMOVE, entry);
		cmm_l2flow_abm_notify(L2FLOW_ENTRY_UPDATE, L2FLOW_DENIED, &entry->l2flow);
	}
		
	return 0;
fail:
	return -1;
}
int __cmm_l2flow_reset(FCI_CLIENT* fci_handler)
{
	int i;
	struct l2flowTable * table_entry = NULL;
	struct list_head *entry;

	if (fci_write(fci_handler, FPP_CMD_RX_L2BRIDGE_FLOW_RESET, 0, NULL))
	{
		cmm_print(DEBUG_ERROR, "Error while trying to reset bridge module\n");
		goto out;
	}
	cmm_print(DEBUG_COMMAND, "Send CMD_RX_L2BRIDGE_FLOW_RESET\n");
	
	for(i= 0 ; i < L2FLOW_HASH_TABLE_SIZE; i++)
		while (!list_empty(&l2flow_table[i]))
		{
			entry = list_first(&l2flow_table[i]);
			table_entry = container_of(entry, struct l2flowTable, list);
			__cmm_l2flow_del(table_entry);
		}
out:
	return 0;
}

int cmm_l2flow_abm_notify(char action, int flags, struct l2flow * l2flow)
{
	struct rtnl_handle rth;
	char buf[256] __attribute__ ((aligned (4)));
	struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
	struct l2flow_msg *l2m;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (cmm_nl_open(&rth, 0, NETLINK_L2FLOW) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: cmm_rtnl_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err0;
	}

	cmm_nlh_init(nlh, sizeof(struct l2flow_msg), L2FLOW_MSG_ENTRY, NLM_F_REQUEST);

	l2m = NLMSG_DATA(nlh);
	memset(l2m, 0, sizeof(struct l2flow_msg));

	l2m->action = action;
	l2m->flags = flags;
	memcpy(l2m->saddr, l2flow->saddr, ETH_ALEN);
	memcpy(l2m->daddr, l2flow->daddr, ETH_ALEN);
	l2m->ethertype = l2flow->ethertype;


	if(l2flow->svlan_tag)
		cmm_addattr16(nlh, sizeof(buf), L2FLOWA_SVLAN_TAG, l2flow->svlan_tag);

	if(l2flow->cvlan_tag)
		cmm_addattr16(nlh, sizeof(buf), L2FLOWA_CVLAN_TAG, l2flow->cvlan_tag);

#ifdef VLAN_FILTER
	if(l2flow->vid)
		cmm_addattr16(nlh, sizeof(buf), L2FLOWA_VID, l2flow->vid);

	if(l2flow->vlan_flags)
		cmm_addattr8(nlh, sizeof(buf), L2FLOWA_VLAN_FLAGS, l2flow->vlan_flags);
#endif
	if(l2flow->session_id)
		cmm_addattr16(nlh, sizeof(buf), L2FLOWA_PPP_S_ID, l2flow->session_id);

	if(l2flow->l3.proto){
		cmm_addattr_l(nlh, sizeof(buf), L2FLOWA_IP_PROTO, &l2flow->l3.proto, sizeof(l2flow->l3.proto));

		if(l2flow->ethertype == htons(ETH_P_IP)){
			cmm_addattr32(nlh, sizeof(buf), L2FLOWA_IP_SRC, l2flow->l3.saddr.ip);
			cmm_addattr32(nlh, sizeof(buf), L2FLOWA_IP_DST, l2flow->l3.daddr.ip);
		}
		else if(l2flow->ethertype == htons(ETH_P_IPV6)){
			cmm_addattr_l(nlh, sizeof(buf), L2FLOWA_IP_SRC, l2flow->l3.saddr.ip6, 16);
			cmm_addattr_l(nlh, sizeof(buf), L2FLOWA_IP_DST, l2flow->l3.daddr.ip6, 16);
		}
		if((l2flow->l3.proto == IPPROTO_UDP) 
		|| (l2flow->l3.proto == IPPROTO_TCP)){
			cmm_addattr16(nlh, sizeof(buf), L2FLOWA_SPORT, l2flow->l4.sport);
			cmm_addattr16(nlh, sizeof(buf), L2FLOWA_DPORT, l2flow->l4.dport);
		}
	}
	if (cmm_rtnl_send(&rth, nlh) < 0)
		goto err1;

	cmm_rtnl_close(&rth);

	return 0;

err1:
	cmm_rtnl_close(&rth);

err0:
	return -1;
}

int cmm_l2flow_netlink_rcv(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct rtattr *tb[L2FLOWA_MAX + 1];
	struct cmm_ct *ctx = arg;


	switch (nlh->nlmsg_type) {
	case L2FLOW_MSG_ENTRY:
	case L2FLOW_MSG_RESET:
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported ABM netlink message %x\n", __func__, nlh->nlmsg_type);
		goto out;
		break;
	}

	if (nlh->nlmsg_type == L2FLOW_MSG_ENTRY)
	{
		struct l2flow l2flow_tmp;
		struct l2flow_msg * l2msg;
		int oifi_idx = 0;
		int iifi_idx = 0;
		short mark = 0;

		char action;
		

		memset(&l2flow_tmp, 0, sizeof(l2flow_tmp));
		l2msg = NLMSG_DATA(nlh);

		memcpy(l2flow_tmp.saddr, l2msg->saddr, ETH_ALEN);
		memcpy(l2flow_tmp.daddr, l2msg->daddr, ETH_ALEN);
		l2flow_tmp.ethertype = l2msg->ethertype;
		action = l2msg->action;

		cmm_parse_rtattr(tb, L2FLOWA_MAX, L2FLOWA_RTA(l2msg),L2FLOWA_PAYLOAD(nlh));

		if(tb[L2FLOWA_IIF_IDX])
			iifi_idx = *(unsigned int *)RTA_DATA((tb[L2FLOWA_IIF_IDX]));

		if(tb[L2FLOWA_OIF_IDX])
			oifi_idx = *(unsigned int *)RTA_DATA((tb[L2FLOWA_OIF_IDX]));

		if(tb[L2FLOWA_SVLAN_TAG])
			l2flow_tmp.svlan_tag = *(unsigned short *)RTA_DATA((tb[L2FLOWA_SVLAN_TAG]));

		if(tb[L2FLOWA_CVLAN_TAG])
			l2flow_tmp.cvlan_tag = *(unsigned short *)RTA_DATA((tb[L2FLOWA_CVLAN_TAG]));
		
#ifdef VLAN_FILTER
		if(tb[L2FLOWA_VID])
			l2flow_tmp.vid = *(unsigned short *)RTA_DATA((tb[L2FLOWA_VID]));

		if(tb[L2FLOWA_VLAN_FLAGS])
			l2flow_tmp.vlan_flags=  *(unsigned char *)RTA_DATA(tb[L2FLOWA_VLAN_FLAGS]);
#endif
		if(tb[L2FLOWA_PPP_S_ID])
			l2flow_tmp.session_id = *(unsigned short *)RTA_DATA((tb[L2FLOWA_PPP_S_ID]));

		if(tb[L2FLOWA_MARK])
			mark = *(unsigned short *)RTA_DATA((tb[L2FLOWA_MARK]));

		if(tb[L2FLOWA_IP_SRC])
			memcpy(&l2flow_tmp.l3.saddr.all, RTA_DATA(tb[L2FLOWA_IP_SRC]), RTA_PAYLOAD(tb[L2FLOWA_IP_SRC]));

		if(tb[L2FLOWA_IP_DST])
			memcpy(&l2flow_tmp.l3.daddr.all, RTA_DATA(tb[L2FLOWA_IP_DST]), RTA_PAYLOAD(tb[L2FLOWA_IP_DST]));

		if(tb[L2FLOWA_IP_PROTO])
			l2flow_tmp.l3.proto=  *(unsigned char *)RTA_DATA(tb[L2FLOWA_IP_PROTO]);
		
		if(tb[L2FLOWA_SPORT])
			l2flow_tmp.l4.sport=  *(unsigned short *)RTA_DATA(tb[L2FLOWA_SPORT]);

		if(tb[L2FLOWA_DPORT])
			l2flow_tmp.l4.dport=  *(unsigned short *)RTA_DATA(tb[L2FLOWA_DPORT]);	

		if((action == L2FLOW_ENTRY_NEW) || (action == L2FLOW_ENTRY_UPDATE))
		{
			__pthread_mutex_lock(&brMutex);
			__cmm_l2flow_register(ctx->fci_handle, action, &l2flow_tmp, iifi_idx, oifi_idx, l2msg->flags, mark);
			__pthread_mutex_unlock(&brMutex);
		}
		else if (action == L2FLOW_ENTRY_DEL)
		{
			__pthread_mutex_lock(&brMutex);
			__cmm_l2flow_deregister(ctx->fci_handle, &l2flow_tmp); 
			__pthread_mutex_unlock(&brMutex);
		}
	}
	else if (nlh->nlmsg_type == L2FLOW_MSG_RESET)
	{
		__pthread_mutex_lock(&brMutex);
		__cmm_l2flow_reset(ctx->fci_handle);
		__pthread_mutex_unlock(&brMutex);
	}
out:
	return RTNL_CB_CONTINUE;

}
#else
int cmm_l2flow_netlink_rcv(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	return RTNL_CB_CONTINUE;
}
#endif

#if defined(LS1043)
int cmmFeBridgedIfUpdate(FCI_CLIENT *fci_handle, int fd, struct interface *itf)
{
        struct fpp_bridged_itf_cmd br_cmd;

	if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED)
		return 0;

	{
		size_t len = strlen(itf->ifname);
		if (len >= IFNAMSIZ)
			len = IFNAMSIZ - 1;
		memcpy(br_cmd.ifname, itf->ifname, len);
		br_cmd.ifname[len] = '\0';
	}

	if (__itf_is_bridged_port(itf))
	{
		br_cmd.is_bridged = 1;
		memcpy(br_cmd.br_macaddr, itf->br_macaddr, 6);
	}
	else
		br_cmd.is_bridged = 0;

	cmm_print(DEBUG_INFO,"%s:: sending command FPP_CMD_BRIDGED_ITF_UPDATE : %d\n", __func__, br_cmd.is_bridged);
	cmm_print(DEBUG_INFO,"%s:: br mac: is_bridged: %d - %x:%x:%x:%x:%x:%x\n", __func__, br_cmd.is_bridged, br_cmd.br_macaddr[0], br_cmd.br_macaddr[1], br_cmd.br_macaddr[2],br_cmd.br_macaddr[3], br_cmd.br_macaddr[4], br_cmd.br_macaddr[5]);
	if (fci_write(fci_handle, FPP_CMD_BRIDGED_ITF_UPDATE, sizeof(br_cmd), (unsigned short *)&br_cmd)){
		cmm_print(DEBUG_ERROR, "Error while trying to set  bridge mac to interface \n");
		return -1;
	}
	
	itf->flags &= ~FPP_NEEDS_UPDATE;
	itf->flags |= FPP_PROGRAMMED;
		
	return 0;

}

/* When an interface event is recieved, this function
checks if its part of any bridge group, if so sets the
bridged  flag */
int cmmGetBridgedItf(struct interface *br_itf)
{
	struct interface *itf;

#ifdef VLAN_FILTER
	if(br_itf->master)
	{
		itf = __itf_find(br_itf->master);
		if(itf && __itf_is_up(br_itf))
		{
			memcpy(br_itf->br_macaddr, itf->macaddr, ETH_ALEN);
			br_itf->itf_flags |= ITF_BRIDGED_PORT;
			cmm_print(DEBUG_INFO,"%s::Interface : %s is set as bridged and its master interface is %s\n", __func__, br_itf->ifname,itf->ifname);
		}
	}
#else
	struct list_head *entry;
	int i, j;

	/* Search through interface table if there are any
	 * bridge interface and it has given interface
	 * as one of its port and they are part of bridge then
	 * update the interfce in FPP with bridge MAC
	 * MAC address.
	 */

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_bridge(itf->ifindex))
				continue;
			for( j = 0; j < MAX_PORTS; j++ )
			{
				if(itf->ifindices[j] == br_itf->ifindex )
					break;
			}

			if( (j < MAX_PORTS) &&  __itf_is_up(br_itf) )
			{
				memcpy(br_itf->br_macaddr, itf->macaddr, 6 );
				br_itf->itf_flags |= ITF_BRIDGED_PORT;
				cmm_print(DEBUG_INFO,"%s::Interface : %s is set as bridged\n", __func__, br_itf->ifname);

			}

                }
        }
#endif

        return 0;
}


/* This function is called when there is an event for bridge interface and 
update all the interfaces part of bridge with bridge mac address
and is_bridged flag to be set and when bridge interface is down, all the interfaces 
part of bridge group will reset the is_bridged flag
*/
int cmmFeUpdateAllBridgedIfs( FCI_CLIENT *fci_handle, int fd, struct interface *bitf)
{
	struct list_head *entry;
	struct interface *itf;
	short ret = 0;
	int i;
#ifndef VLAN_FILTER
	int j;
#endif
	/* Search through interface table if there are any
	 * interface and they are part of bridge then
	 * update the interfce in FPP with bridge MAC
	 * MAC address.
	 */

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (unsupported_bridged_interface(itf))
				continue;

#ifdef VLAN_FILTER
			if (bitf->ifindex != itf->master)
				continue;
#else
			for( j = 0; j < MAX_PORTS; j++ )
			{
				if(bitf->ifindices[j] == itf->ifindex )
					break;
			}

			/*if( (j < MAX_PORTS) && (itf->flags & FPP_PROGRAMMED) && __itf_is_up(itf) ) */ 
			//if( (j < MAX_PORTS) && (____itf_is_programmed(itf)) && __itf_is_up(itf) )
			if(j < MAX_PORTS)
			{
#endif
				if ((____itf_is_programmed(itf)) && __itf_is_up(itf) )
				{
					cmm_print(DEBUG_INFO, "%s: itf mac: %x:%x:%x:%x:%x:%x  bridge mac: %x:%x:%x:%x:%x:%x\n ", __func__,
							itf->macaddr[0], itf->macaddr[1], itf->macaddr[2],
							itf->macaddr[3],itf->macaddr[4],itf->macaddr[5],
							bitf->macaddr[0], bitf->macaddr[1], bitf->macaddr[2],
							bitf->macaddr[3],bitf->macaddr[4],bitf->macaddr[5]
						 );

					if (__itf_is_up(bitf))
					{
						memcpy( itf->br_macaddr, bitf->macaddr, 6 );
						itf->itf_flags |= ITF_BRIDGED_PORT ;
						itf->flags |= FPP_NEEDS_UPDATE;
						cmm_print(DEBUG_INFO, "%s:: Setting interface %s as bridged interface \n",__func__, itf->ifname);

					}
					else
					{
						if (__itf_is_bridged_port(itf))
						{
							itf->itf_flags &= ~ITF_BRIDGED_PORT;
							itf->flags |= FPP_NEEDS_UPDATE;
							cmm_print(DEBUG_INFO, "%s:: unsetting interface %s as bridged interface \n",__func__, itf->ifname);
						}
					}

					ret = cmmFeBridgedIfUpdate(fci_handle, fd, itf);
				}
#ifndef VLAN_FILTER
			}
#endif
		}
	}
	return ret;
}
#endif
#ifdef AUTO_BRIDGE
int cmmBridgeInit(struct cmm_ct *ctx)
{
	fpp_l2_bridge_control_cmd_t br_cmd;

	if (cmm_nl_open(&ctx->rth_abm, L2FLOW_NL_GRP, NETLINK_L2FLOW) < 0)
	{
		cmm_print(DEBUG_CRIT, "%s: Bridge is started in manual mode\n", __func__);
		br_cmd.mode_timeout = FPP_L2_BRIDGE_MODE_MANUAL;
		globalConf.auto_bridge = 0;
		
	}
	else{
		br_cmd.mode_timeout = FPP_L2_BRIDGE_MODE_AUTO;
		cmm_print(DEBUG_CRIT, "%s: Bridge is started in auto mode\n", __func__);
		globalConf.auto_bridge = 1;
	}

	/* Set bridge mode in FPP */
	cmm_print(DEBUG_COMMAND, "Send FPP_CMD_RX_L2BRIDGE_MODE\n");
	if (fci_write(ctx->fci_handle, FPP_CMD_RX_L2BRIDGE_MODE, sizeof(br_cmd), (unsigned short *)&br_cmd)){
		cmm_print(DEBUG_ERROR, "Error while trying to set  bridge mode\n");
		return -1;
	}
	return 0;
}
#else
int cmmBridgeInit(struct cmm_ct *ctx)
{
	globalConf.auto_bridge = 0;
	cmm_print(DEBUG_CRIT, "%s: Bridge is started in manual mode\n", __func__);
	return 0;
}

#endif
int cmmBridgeControlProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	fpp_l2_bridge_control_cmd_t cmd;
	union u_rxbuf rxbuf;
	char *endptr;
	unsigned int  timeout;
	int rc;

	if(!keywords[cpt])
		goto usage;

	if (strcasecmp(keywords[cpt], "timeout") != 0)
		goto usage;

	if(!keywords[++cpt])
		goto usage;

	/*Get an integer from the string*/
	endptr = NULL;
	timeout = strtoul(keywords[cpt], &endptr, 0);
	if ((keywords[cpt] == endptr) ||  (timeout > UINT16_MAX) || (timeout == 0)){
		cmm_print(DEBUG_ERROR, "%s Timeout must be > 0 and < %d s \n",__func__, UINT16_MAX);
		goto usage;
	}

	cmd.mode_timeout = timeout;
	
	// Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send FPP_CMD_RX_L2BRIDGE_FLOW_TIMEOUT to FPP\n");
	rc = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_FLOW_TIMEOUT, (unsigned short *) &cmd, sizeof(fpp_l2_bridge_control_cmd_t), rxbuf.rcvBuffer);
	if (rc != 2) /* we expect 2 bytes in response */
	{
		cmm_print(DEBUG_STDERR, "FPP_CMD_RX_L2BRIDGE_FLOW_TIMEOUT unexpected response length %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != FPP_ERR_OK)
	{
		showErrorMsg("CMD_RX_L2BRIDGE_FLOW_TIMEOUT", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		return -1;
	}

	return 0;
usage:
	cmm_print(DEBUG_STDOUT, "Usage: set bridge timeout {timeout value in seconds}\n");
	return -1;
}

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmRxShowPrintHelp()
{
	char buf[128];


	print_all_gemac_ports(buf, 128);
  //	cmm_print(DEBUG_STDOUT, "show rx not yet supported\n");
	cmm_print(DEBUG_STDOUT, "Usage: show rx interface {%s} icc\n"
				"       query rx bridge\n", buf);
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmRxQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int cmdToSend = 0; /* bits field*/
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
 	char null_mac[6] = {0,0,0,0,0,0};

	//goto help
	if(!keywords[cpt])
		goto help;
	if(strcasecmp(keywords[cpt], "bridge") == 0)
	{
		cmdToSend |= CMD_BIT(FPP_CMD_RX_L2BRIDGE_QUERY_STATUS);
	}
	else
		goto keyword_error;

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_L2BRIDGE_QUERY_STATUS))
	{
		int count = 0;
		char input_interface[IFNAMSIZ];
		char output_interface[IFNAMSIZ];
		char pkt_priority[16];
		char svlan_priority[16];
		char cvlan_priority[16];
		char queue_modifier[16];
		fpp_l2_bridge_query_status_response_t *pStatusResponse = (fpp_l2_bridge_query_status_response_t *)rxbuf.rcvBuffer;
		fpp_l2_bridge_query_entry_response_t *pEntryResponse = (fpp_l2_bridge_query_entry_response_t *)rxbuf.rcvBuffer;

		while( 1 )
		{
			// Send CMD_RX_L2BRIDGE_QUERY_STATUS command
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_QUERY_STATUS, NULL, 0, rxbuf.rcvBuffer);
			if ( rcvBytes != sizeof(fpp_l2_bridge_query_status_response_t) )
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
					  (rcvBytes < sizeof(unsigned short) ) ? 
								0 : rxbuf.result
				  );
				goto exit;
			}
			
			if (pStatusResponse->eof)
			    	break;

			cmm_print(DEBUG_STDOUT, "Interface %s Status: %s\n", 
					pStatusResponse->ifname, pStatusResponse->status ? "ON":"OFF");
		}
#if 0
		// Send CMD_RX_L2BRIDGE_QUERY_STATUS command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_QUERY_STATUS, NULL, 0, rxbuf.rcvBuffer);
		if (rcvBytes != sizeof(fpp_l2_bridge_query_status_response_t) + sizeof(unsigned short))
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : *((unsigned short *) rxbuf.rcvBuffer)
			  );
			goto exit;
		}
#ifdef WIFI_ENABLE
		cmm_print(DEBUG_STDOUT, "Interface %s (WAN) Status: %s\nInterface %s (LAN) Status: %s\nInterface %s (WIFI) Status: %s\nInterface %s (WIFI) Status: %s\n",
						WAN_INTERFACE_NAME,
						( pStatusResponse->status & (1 << WAN_PORT_ID))? "ON" : "OFF",
						LAN_INTERFACE_NAME,
						( pStatusResponse->status & (1 << LAN_PORT_ID))? "ON" : "OFF",
						WIFI_INTERFACE_NAME0,
						( pStatusResponse->status & (1 << WIFI_PORT0)) ? "ON" : "OFF",
						WIFI_INTERFACE_NAME1,
						( pStatusResponse->status & (1 << WIFI_PORT1)) ? "ON" : "OFF");
#else
		cmm_print(DEBUG_STDOUT, "Interface %s (WAN) Status: %s\nInterface %s (LAN) Status: %s\n",
						WAN_INTERFACE_NAME,
						( pStatusResponse->status & (1 << WAN_PORT_ID))? "ON" : "OFF",
						LAN_INTERFACE_NAME,
						( pStatusResponse->status & (1 << LAN_PORT_ID))? "ON" : "OFF");
#endif
#endif
		while (1)
		{
			char sessionid_buf[32];
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_QUERY_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_l2_bridge_query_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: CMD_RX_L2BRIDGE_QUERY_ENTRY Unexpected result returned from FPP rc:%04x - received %d - expected %zu\n",
				  	(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result,
					rcvBytes,
					sizeof(fpp_l2_bridge_query_entry_response_t)
			  	);
				goto exit;
			}
			if (pEntryResponse->eof)
			    	break;
			if (pEntryResponse->input_interface >= GEM_PORTS)
				strcpy(input_interface, pEntryResponse->input_name);	
			else	
				get_port_name(pEntryResponse->input_interface, input_interface, IFNAMSIZ);
			
			if ((pEntryResponse->input_svlan != 0xFFFF) && (pEntryResponse->input_cvlan != 0xFFFF))
				sprintf(input_interface + strlen(input_interface), ".%d.%d", pEntryResponse->input_svlan, pEntryResponse->input_cvlan);
			else if (pEntryResponse->input_svlan != 0xFFFF)
				sprintf(input_interface + strlen(input_interface), ".%d", pEntryResponse->input_svlan);

			if (pEntryResponse->output_interface >= GEM_PORTS)
				strcpy(output_interface, pEntryResponse->output_name);	
			else	
				get_port_name(pEntryResponse->output_interface, output_interface, IFNAMSIZ);
			
			if ((pEntryResponse->output_svlan != 0xFFFF) && (pEntryResponse->output_cvlan != 0xFFFF))
				sprintf(output_interface + strlen(output_interface), ".%d.%d", pEntryResponse->output_svlan, pEntryResponse->output_cvlan);
			else if (pEntryResponse->output_svlan != 0xFFFF)
				sprintf(output_interface + strlen(output_interface), ".%d", pEntryResponse->output_svlan);

			if (pEntryResponse->pkt_priority == 0x8000)
				strcpy(pkt_priority, "vlan");
			else
				sprintf(pkt_priority, "%d", pEntryResponse->pkt_priority);

			if (pEntryResponse->svlan_priority == 0x8000)
				strcpy(svlan_priority, "copy");
			else
				sprintf(svlan_priority, "%d", pEntryResponse->svlan_priority);
			
			if (pEntryResponse->cvlan_priority == 0x8000)
				strcpy(cvlan_priority, "copy");
			else
				sprintf(cvlan_priority, "%d", pEntryResponse->cvlan_priority);
			
			if (pEntryResponse->session_id != 0)
				sprintf(sessionid_buf, "SessionId=%d ", pEntryResponse->session_id);
			else
				sessionid_buf[0] = '\0';

			if(pEntryResponse->queue_modifier == FPP_BRIDGE_QMOD_DSCP)
				strcpy(queue_modifier, "dscp");
			else
				strcpy(queue_modifier, "none");	


			if ((!memcmp(pEntryResponse->srcaddr, null_mac, 6)) && (pEntryResponse->ethertype == 0))
				cmm_print(DEBUG_STDOUT, "Input=%-6s "
				                "DA=%02X:%02X:%02X:%02X:%02X:%02X "
				                "SA=       *          "
						  "Type=  *  "
						  "Queue=%-s "
						  "Qmod=%-s "
						  "SVLANPrio=%-s "
						  "CVLANPrio=%-s "
						  "%s"
						  "Output=%s\n",
						    input_interface,
						    pEntryResponse->destaddr[0], pEntryResponse->destaddr[1], pEntryResponse->destaddr[2],
						    pEntryResponse->destaddr[3], pEntryResponse->destaddr[4], pEntryResponse->destaddr[5],
						    pkt_priority, queue_modifier, svlan_priority, cvlan_priority,sessionid_buf,
						    output_interface);
			else if (!memcmp(pEntryResponse->srcaddr, null_mac, 6))
				cmm_print(DEBUG_STDOUT, "Input=%-6s "
				                "DA=%02X:%02X:%02X:%02X:%02X:%02X "
				                "SA=       *          "
						  "Type=%04X "
						  "Queue=%-s "
						  "Qmod=%-s "
						  "SVLANPrio=%-s "
						  "CVLANPrio=%-s "
						  "%s"
						  "Output=%s\n",
						    input_interface,
						    pEntryResponse->destaddr[0], pEntryResponse->destaddr[1], pEntryResponse->destaddr[2],
						    pEntryResponse->destaddr[3], pEntryResponse->destaddr[4], pEntryResponse->destaddr[5],
						    pEntryResponse->ethertype, pkt_priority, queue_modifier, svlan_priority, cvlan_priority,
						    sessionid_buf, output_interface);
			else if (pEntryResponse->ethertype == 0)
				cmm_print(DEBUG_STDOUT, "Input=%-6s "
				                "DA=%02X:%02X:%02X:%02X:%02X:%02X "
				                "SA=%02X:%02X:%02X:%02X:%02X:%02X "
						  "Type=  *  "
						  "Queue=%-s "
						  "Qmod=%-s "
						  "SVLANPrio=%-s "
						  "CVLANPrio=%-s "
						  "%s"
						  "Output=%s\n",
						    input_interface,
						    pEntryResponse->destaddr[0], pEntryResponse->destaddr[1], pEntryResponse->destaddr[2],
						    pEntryResponse->destaddr[3], pEntryResponse->destaddr[4], pEntryResponse->destaddr[5],
						    pEntryResponse->srcaddr[0], pEntryResponse->srcaddr[1], pEntryResponse->srcaddr[2],
						    pEntryResponse->srcaddr[3], pEntryResponse->srcaddr[4], pEntryResponse->srcaddr[5],
						    pkt_priority, queue_modifier, svlan_priority, cvlan_priority,sessionid_buf,
						    output_interface);

			else	
			cmm_print(DEBUG_STDOUT, "Input=%-6s "
				                "DA=%02X:%02X:%02X:%02X:%02X:%02X "
				                "SA=%02X:%02X:%02X:%02X:%02X:%02X "
						  "Type=%04X "
						  "Queue=%-s "
						  "Qmod=%-s "
						  "SVLANPrio=%-s "
						  "CVLANPrio=%-s "
						  "%s"
						  "Output=%s\n",
						    input_interface,
						    pEntryResponse->destaddr[0], pEntryResponse->destaddr[1], pEntryResponse->destaddr[2],
						    pEntryResponse->destaddr[3], pEntryResponse->destaddr[4], pEntryResponse->destaddr[5],
						    pEntryResponse->srcaddr[0], pEntryResponse->srcaddr[1], pEntryResponse->srcaddr[2],
						    pEntryResponse->srcaddr[3], pEntryResponse->srcaddr[4], pEntryResponse->srcaddr[5],
						    pEntryResponse->ethertype, pkt_priority, queue_modifier, svlan_priority, cvlan_priority,
						    sessionid_buf, output_interface);
			count++;
		}
		cmm_print(DEBUG_STDOUT, "\n%d Bridge Table Entries found\n", count);
	}

        return 0;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmRxShowPrintHelp();

exit:
	return -1;
}

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmRxShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int cmdToSend = 0; /* bits field*/
	int rcvBytes = 0;
	union u_rxbuf rxbuf;

	fpp_rx_icc_disable_cmd_t showCmd;  // same structure used for show
	fpp_rx_icc_show_return_cmd_t *iccStatus;

	//goto help
	if(!keywords[cpt])
		goto help;
	if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		
		if ((short)(showCmd.interface = get_port_id(keywords[cpt])) < 0)
			goto keyword_error;

		if(!keywords[++cpt])
			goto help;
		if(strcasecmp(keywords[cpt], "icc") == 0)
			cmdToSend |= CMD_BIT(FPP_CMD_RX_CNG_SHOW);
		else
			goto keyword_error;
	}
	else
		goto keyword_error;


	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_CNG_SHOW))
	{
		// Send CMD_RX_CNG_SHOW command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_CNG_SHOW, & showCmd, sizeof(showCmd), rxbuf.rcvBuffer);

		if ( rcvBytes < (sizeof(fpp_rx_icc_show_return_cmd_t) - sizeof(unsigned short)) )
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected returned result from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  );
			goto exit;
		}
		else
		{
			iccStatus = (fpp_rx_icc_show_return_cmd_t *)(rxbuf.rcvBuffer);
			cmm_print(DEBUG_STDOUT, "State: %s\n" "Acc: %d\n" "onThr: %d\n" "offThr: %d\n",
					(iccStatus->state & 1 )? "Enabled":"Disabled\n", iccStatus->acc_value, iccStatus->on_thr, iccStatus->off_thr);
			if (iccStatus->state & 0xfe)
			    cmm_print(DEBUG_STDOUT, "Flags: 0x%02x\n", iccStatus->state & 0xfe);
			
		}
	}

	return 0;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmRxShowPrintHelp();

exit:
	return -1;

}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmRxSetPrintHelp(int cmd_type)
{
	char buf[128];


	print_all_gemac_ports(buf, 128);

	if (cmd_type == UNKNOWN_CMD || cmd_type == ICC_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set rx interface {%s} [icc {on|off}]\n"
                  "                             [acc {acc_value}]\n"
                  "                             [on_thr {on_thr value}]\n"
                  "                             [off_thr {off_thr value}]\n"
                  "                             [flag {flag value}]\n"
                  "                             [val1 {val1 value}]\n"
                  "                             [val2 {val2 value}]\n", buf);
	}
	if (cmd_type == UNKNOWN_CMD)
	{
	    cmm_print(DEBUG_STDOUT, "\n");
	}
	if (cmd_type == UNKNOWN_CMD || cmd_type == BRIDGE_CMD)
	{
#ifdef WIFI_ENABLE
	    //FIXME : Now interface names are hardcoded to ath0/ath1. Need to 
	    //        find solution to get interface names from config file.	
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set rx interface {%s|<wi-fi interface>} [bridge {on|off|add|remove}]\n"
                  "                add / remove options:\n"
                  "                                      [da {dest_addr value}]\n"
                  "                                      [sa {src_addr value}]\n"
                  "                                      [type {ethertype value}]\n"
                  "                add options:\n"
                  "                                      [queue {output queue base value 0-31|vlan}]\n"
                  "                                      [svlanprio {priority value|copy}]\n"
                  "                                      [cvlanprio {priority value|copy}]\n"
                  "                                      [sessionid {session id value}]\n"
                  "                                      [output {interface}]\n"
		  "                                      [qmod {output queue modifier dscp|none}]\n", buf);
#else
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set rx interface {%s} [bridge {on|off|add|remove}]\n"
                  "                add / remove options:\n"
                  "                                      [da {dest_addr value}]\n"
                  "                                      [sa {src_addr value}]\n"
                  "                                      [type {ethertype value}]\n"
                  "                add options:\n"
                  "                                      [queue {output queue base value 0-31|vlan}]\n"
                  "                                      [svlanprio {priority value|copy}]\n"
                  "                                      [cvlanprio {priority value|copy}]\n"
                  "                                      [sessionid {session id value}]\n"
                  "                                      [output {interface}]\n"
		  "                                      [qmod {output queue modifier dscp|none}]\n", buf);
#endif
	}
}


/************************************************************
 *
 *
 *
 ************************************************************/
static int parse_interface(char *pstring, unsigned short *pinterface_number );
static int parse_interface_qinq(char *pstring, unsigned short *pinterface_number, unsigned short *svlan_id, unsigned short *cvlan_id);

int cmmRxSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cmd_type = UNKNOWN_CMD;
	int cpt = tabStart;
	unsigned int cmdToSend = 0; /* bits field*/
	char * endptr;
	unsigned int tmp;
        int rcvBytes = 0;
	char *pinterface;
	unsigned short interface;

	fpp_rx_icc_enable_cmd_t enableCmd;
	fpp_rx_icc_disable_cmd_t disableCmd;

	fpp_l2_bridge_enable_cmd_t bridgeEnableCmd;
	fpp_l2_bridge_add_entry_cmd_t bridgeAddCmd;
	fpp_l2_bridge_remove_entry_cmd_t bridgeRemoveCmd;

	union u_rxbuf rxbuf;

	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		pinterface = keywords[cpt];
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;

   if(strcasecmp(keywords[cpt], "icc") == 0)
   {		
       		cmd_type = ICC_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "on") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_RX_CNG_ENABLE);
			memset(&enableCmd, 0, sizeof(enableCmd));
			if (parse_icc_interface(pinterface, &interface, 2) < 0)
				goto help;
			enableCmd.interface = interface;

			cpt++;

			while (keywords[cpt] != NULL)
			{
				if(strcasecmp(keywords[cpt], "acc") == 0)
				{
					if(!keywords[++cpt])
						goto help;

				    /*Get an integer from the string*/
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    if ((keywords[cpt] == endptr) || (tmp > USHRT_MAX))
				    {
				        cmm_print(DEBUG_CRIT, "icc ERROR: acc must be a number between 1 and %d\n", USHRT_MAX);
				        goto help;
				    }

				    enableCmd.acc_value = tmp;
				}
				else if(strcasecmp(keywords[cpt], "on_thr") == 0)
				{
				    if(!keywords[++cpt])
				        goto help;

				    /*Get an integer from the string*/
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    if ((keywords[cpt] == endptr) || (tmp > USHRT_MAX))
				    {
				        cmm_print(DEBUG_CRIT, "icc ERROR: on_thr must be a number between 1 and %d\n", USHRT_MAX);
				        goto help;
				    }

				    enableCmd.on_thr = tmp;
				}
				else if(strcasecmp(keywords[cpt], "off_thr") == 0)
				{
				    if(!keywords[++cpt])
				        goto help;

				    /*Get an integer from the string*/
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    if ((keywords[cpt] == endptr) || (tmp > USHRT_MAX))
				    {
				        cmm_print(DEBUG_CRIT, "icc ERROR: off_thr must be a number between 1 and %d\n", USHRT_MAX);
				        goto help;
				    }

				    enableCmd.off_thr = tmp;
				}
				else if (strncasecmp(keywords[cpt],"flag",1) == 0) {
				    if(!keywords[++cpt])
				        goto help;
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    enableCmd.flag = tmp;
				}
				else if (strcasecmp(keywords[cpt],"val1") == 0) {
				    if(!keywords[++cpt])
				        goto help;
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    enableCmd.val1 = tmp;
				}
				else if (strcasecmp(keywords[cpt],"val2") == 0) {
				    if(!keywords[++cpt])
				        goto help;
				    endptr = NULL;
				    tmp = strtoul(keywords[cpt], &endptr, 0);
				    enableCmd.val1 = tmp;
				}
				else
				    goto help;
			
				cpt ++;
			}
		}
		else if(strcasecmp(keywords[cpt], "off") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_RX_CNG_DISABLE);
			memset(&disableCmd, 0, sizeof(disableCmd));
			if (parse_icc_interface(pinterface, &interface, 2) < 0)
				goto help;
			disableCmd.interface = interface;
		}
		else
			goto keyword_error;
   }
   else if(strcasecmp(keywords[cpt], "bridge") == 0)
   {		
       		cmd_type = BRIDGE_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "on") == 0)
		{
			if (keywords[++cpt])
				goto help;
			cmdToSend |= CMD_BIT(FPP_CMD_RX_L2BRIDGE_ENABLE);
			memset(&bridgeEnableCmd, 0, sizeof(bridgeEnableCmd));
			bridgeEnableCmd.enable_flag = 1;
			bridgeEnableCmd.interface = 0xffff;
		
			strncpy (&bridgeEnableCmd.input_name[0], pinterface, sizeof(bridgeEnableCmd.input_name));
			STR_TRUNC_END(bridgeEnableCmd.input_name, sizeof(bridgeEnableCmd.input_name));
		}
		else if(strcasecmp(keywords[cpt], "off") == 0)
		{
			if (keywords[++cpt])
				goto help;
			cmdToSend |= CMD_BIT(FPP_CMD_RX_L2BRIDGE_ENABLE);
			memset(&bridgeEnableCmd, 0, sizeof(bridgeEnableCmd));
			bridgeEnableCmd.enable_flag = 0;
			bridgeEnableCmd.interface = 0xffff;
			strncpy (&bridgeEnableCmd.input_name[0], pinterface, sizeof(bridgeEnableCmd.input_name));
			STR_TRUNC_END(bridgeEnableCmd.input_name, sizeof(bridgeEnableCmd.input_name));
		}
		else if(strcasecmp(keywords[cpt], "add") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_RX_L2BRIDGE_ADD);
			memset(&bridgeAddCmd, 0, sizeof(bridgeAddCmd));
			strncpy(bridgeAddCmd.input_name, pinterface, sizeof(bridgeAddCmd.input_name));
			STR_TRUNC_END(bridgeAddCmd.input_name, sizeof(bridgeAddCmd.input_name));
			bridgeAddCmd.input_interface = 0xffff;
			bridgeAddCmd.input_svlan = 0xffff;
			bridgeAddCmd.input_cvlan = 0xffff;
			bridgeAddCmd.output_interface = 0xffff;
			bridgeAddCmd.output_svlan = 0xFFFF;
			bridgeAddCmd.output_cvlan = 0xFFFF;
			while (keywords[++cpt] != NULL)
			{
				if(strcasecmp(keywords[cpt], "da") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (!parse_macaddr(keywords[cpt], bridgeAddCmd.destaddr))
					{
						cmm_print(DEBUG_CRIT, "bridge ERROR: bad MAC address: %s\n", keywords[cpt]);
						goto help;
					}
				}
				else if(strcasecmp(keywords[cpt], "sa") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (!parse_macaddr(keywords[cpt], bridgeAddCmd.srcaddr))
					{
						cmm_print(DEBUG_CRIT, "bridge ERROR: bad MAC address: %s\n", keywords[cpt]);
						goto help;
					}
				}
				else if(strcasecmp(keywords[cpt], "type") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > 0xFFFF))
					{
					    cmm_print(DEBUG_CRIT, "bridge ERROR: bad ETHERTYPE value: %s\n", keywords[cpt]);
					    goto help;
					}
					bridgeAddCmd.ethertype = (unsigned short)tmp;
				}
				else if(strcasecmp(keywords[cpt], "prio") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (strcasecmp(keywords[cpt], "vlan") == 0)
						bridgeAddCmd.pkt_priority = 0x8000;
					else
					{
						tmp = strtoul(keywords[cpt], &endptr, 0);
						if ((keywords[cpt] == endptr) || (tmp > 7))
						{
					    		cmm_print(DEBUG_CRIT, "bridge ERROR: bad PRIORITY value: %s\n", keywords[cpt]);
					    		goto help;
						}
						bridgeAddCmd.pkt_priority = (unsigned short)tmp;
					}
				}
				else if(strcasecmp(keywords[cpt], "queue") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (strcasecmp(keywords[cpt], "vlan") == 0)
						bridgeAddCmd.pkt_priority = 0x8000;
					else
					{
						tmp = strtoul(keywords[cpt], &endptr, 0);
						if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
						{
					    		cmm_print(DEBUG_CRIT, "bridge ERROR: bad QUEUE value: %s\n", keywords[cpt]);
					    		goto help;
						}
						bridgeAddCmd.pkt_priority = (unsigned short)tmp;
					}
				}
				else if(strcasecmp(keywords[cpt], "svlanprio") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (strcasecmp(keywords[cpt], "copy") == 0)
						bridgeAddCmd.svlan_priority = 0x8000;
					else
					{
						tmp = strtoul(keywords[cpt], &endptr, 0);
						if ((keywords[cpt] == endptr) || (tmp > 7))
						{
					    		cmm_print(DEBUG_CRIT, "bridge ERROR: bad SVLAN PRIORITY value: %s\n", keywords[cpt]);
					    		goto help;
						}
						bridgeAddCmd.svlan_priority = (unsigned short)tmp;
					}
				}
				else if(strcasecmp(keywords[cpt], "cvlanprio") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (strcasecmp(keywords[cpt], "copy") == 0)
						bridgeAddCmd.cvlan_priority = 0x8000;
					else
					{
						tmp = strtoul(keywords[cpt], &endptr, 0);
						if ((keywords[cpt] == endptr) || (tmp > 7))
						{
					    		cmm_print(DEBUG_CRIT, "bridge ERROR: bad CVLAN PRIORITY value: %s\n", keywords[cpt]);
					    		goto help;
						}
						bridgeAddCmd.cvlan_priority = (unsigned short)tmp;
					}
				}
				else if(strcasecmp(keywords[cpt], "sessionid") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > 0xFFFF))
					{
					    cmm_print(DEBUG_CRIT, "bridge ERROR: bad SESSION ID value: %s\n", keywords[cpt]);
					    goto help;
					}
					bridgeAddCmd.session_id = (unsigned short)tmp;
				}
				else if(strcasecmp(keywords[cpt], "output") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					bridgeAddCmd.output_interface = 0xFFFF;
					bridgeAddCmd.input_interface  = 0xFFFF;
					bridgeAddCmd.output_svlan = 0xFFFF;
					bridgeAddCmd.output_cvlan = 0xFFFF;
					strncpy(bridgeAddCmd.output_name, keywords[cpt], sizeof(bridgeAddCmd.output_name));
					STR_TRUNC_END(bridgeAddCmd.output_name,sizeof(bridgeAddCmd.output_name));
					strncpy(bridgeAddCmd.input_name, pinterface, sizeof(bridgeAddCmd.input_name));
					STR_TRUNC_END(bridgeAddCmd.input_name,sizeof(bridgeAddCmd.input_name));
				}
				else if(strcasecmp(keywords[cpt], "qmod") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (strcasecmp(keywords[cpt], "dscp") == 0)
						bridgeAddCmd.queue_modifier = FPP_BRIDGE_QMOD_DSCP;
					else if(strcasecmp(keywords[cpt], "none") == 0)
						bridgeAddCmd.queue_modifier = FPP_BRIDGE_QMOD_NONE;
					else {
						bridgeAddCmd.queue_modifier = FPP_BRIDGE_QMOD_NONE;
						cmm_print(DEBUG_CRIT, "bridge ERROR: bad QUEUE MODIFIER  value: %s\n", keywords[cpt]);
					    	goto help;
					}
				}
				else
				    goto help;
			}
		}
		else if(strcasecmp(keywords[cpt], "remove") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_RX_L2BRIDGE_REMOVE);
			memset(&bridgeRemoveCmd, 0, sizeof(bridgeRemoveCmd));
			strncpy(bridgeRemoveCmd.input_name, pinterface, sizeof(bridgeRemoveCmd.input_name));
			STR_TRUNC_END(bridgeRemoveCmd.input_name,sizeof(bridgeRemoveCmd.input_name));
			bridgeRemoveCmd.input_interface = 0xffff;
			bridgeRemoveCmd.input_svlan = 0xffff;
			bridgeRemoveCmd.input_cvlan = 0xffff;
			while (keywords[++cpt] != NULL)
			{
				if(strcasecmp(keywords[cpt], "da") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (!parse_macaddr(keywords[cpt], bridgeRemoveCmd.destaddr))
					{
						cmm_print(DEBUG_CRIT, "bridge ERROR: bad MAC address: %s\n", keywords[cpt]);
						goto help;
					}
				}
				else if(strcasecmp(keywords[cpt], "sa") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					if (!parse_macaddr(keywords[cpt], bridgeRemoveCmd.srcaddr))
					{
						cmm_print(DEBUG_CRIT, "bridge ERROR: bad MAC address: %s\n", keywords[cpt]);
						goto help;
					}
				}
				else if(strcasecmp(keywords[cpt], "type") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > 0xFFFF))
					{
					    cmm_print(DEBUG_CRIT, "bridge ERROR: bad ETHERTYPE value: %s\n", keywords[cpt]);
					    goto help;
					}
					bridgeRemoveCmd.ethertype = (unsigned short)tmp;
				}
				else if(strcasecmp(keywords[cpt], "sessionid") == 0)
				{
					if(!keywords[++cpt])
						goto help;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > 0xFFFF))
					{
					    cmm_print(DEBUG_CRIT, "bridge ERROR: bad SESSION ID value: %s\n", keywords[cpt]);
					    goto help;
					}
					bridgeRemoveCmd.session_id = (unsigned short)tmp;
				}
				else
				    goto help;
			}
		}
		else
			goto keyword_error;
   }
   else
       goto keyword_error;

	/*
	 * Parsing have been performed
	 * Now send the right commands
	 */
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_L2BRIDGE_ENABLE))
	{
		// Send CMD_RX_L2BRIDGE_ENABLE command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_ENABLE, & bridgeEnableCmd, sizeof(bridgeEnableCmd), rxbuf.rcvBuffer);
	}
        
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_L2BRIDGE_ADD))
	{
		// Send CMD_RX_L2BRIDGE_ADD command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_ADD, & bridgeAddCmd, sizeof(bridgeAddCmd), rxbuf.rcvBuffer);
	}
        
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_L2BRIDGE_REMOVE))
	{
		// Send CMD_RX_L2BRIDGE_REMOVE command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2BRIDGE_REMOVE, & bridgeRemoveCmd, sizeof(bridgeRemoveCmd), rxbuf.rcvBuffer);
	}
        
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_CNG_DISABLE))
	{
		// Send CMD_RX_CNG_DISABLE command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_CNG_DISABLE, & disableCmd, sizeof(disableCmd), rxbuf.rcvBuffer);
	}
	
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_RX_CNG_ENABLE))
	{
		// Send CMD_RX_CNG_ENABLE command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_CNG_ENABLE, & enableCmd, sizeof(enableCmd), rxbuf.rcvBuffer);
	}
        
	if ((rcvBytes != 2) || rxbuf.result) {
	  cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
		    (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
		    );
          return -1;
	}

	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmRxSetPrintHelp(cmd_type);
	return -1;
}

/*********************************************************************************
 *
 *
 *
 ********************************************************************************/
int cmmL2BridgeProcessClientCmd(FCI_CLIENT* fci_handle, int fc, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	fpp_l2_bridge_enable_cmd_t *bridgeEnableCmd;
	fpp_l2_bridge_add_entry_cmd_t *bridgeAddCmd;
	fpp_l2_bridge_remove_entry_cmd_t *bridgeRemoveCmd;
	unsigned short svlan_id, cvlan_id, interface;
	
	cmm_print(DEBUG_INFO, "%s\n", __func__);

	res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;

	switch(fc)
	{
		case FPP_CMD_RX_L2BRIDGE_ENABLE:
			cmm_print(DEBUG_INFO, "2 %s\n", __func__);
			bridgeEnableCmd = (fpp_l2_bridge_enable_cmd_t *)cmd_buf;
			*res_len = 2;
			if( cmd_len < sizeof(fpp_l2_bridge_enable_cmd_t) )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge enable command size too small(%d, %zu)\n", 
						__func__, cmd_len, sizeof(fpp_l2_bridge_enable_cmd_t));
				return 0;
			}
			
			if( parse_interface(bridgeEnableCmd->input_name, &interface) < 0 )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge enable command with unknown interface: %s\n",
						__func__, bridgeEnableCmd->input_name);
				res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
				return 0; 
			}
			bridgeEnableCmd->interface = interface;
			
			goto FCI_CMD; 

		case FPP_CMD_RX_L2BRIDGE_ADD:
			bridgeAddCmd = (fpp_l2_bridge_add_entry_cmd_t *)cmd_buf;
			*res_len = 2;
			if( cmd_len < sizeof(fpp_l2_bridge_add_entry_cmd_t) )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge add command size too small(%d, %zu)\n", 
						__func__, cmd_len, sizeof(fpp_l2_bridge_add_entry_cmd_t));
				return 0;
			}
			
			if( parse_interface_qinq(bridgeAddCmd->input_name, &interface, &svlan_id, &cvlan_id) < 0 )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge add command with unknown interface: %s\n",
						__func__, bridgeAddCmd->input_name);
				res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
				return 0; 
			}
			bridgeAddCmd->input_interface = interface;
			bridgeAddCmd->input_svlan = svlan_id;
			bridgeAddCmd->input_cvlan = cvlan_id;

		/* FIXME : output interface should be provided by the user */
		//	bridgeAddCmd->output_interface = interface == GEMAC0_PORT ? GEMAC1_PORT : GEMAC0_PORT;
		//	bridgeAddCmd->output_vlan = 0xFFFF;

		//	if (!strlen(bridgeAddCmd->output_name))
		//		goto FCI_CMD;

			if( parse_interface_qinq(bridgeAddCmd->output_name, &interface, &svlan_id, &cvlan_id) == 0 )
			{
				bridgeAddCmd->output_interface = interface;
				bridgeAddCmd->output_svlan = svlan_id;
				bridgeAddCmd->output_cvlan = cvlan_id;
			}
			else
			{
				bridgeAddCmd->output_interface = 0xFFFF;
				bridgeAddCmd->input_interface  = 0xFFFF;
				bridgeAddCmd->output_svlan = 0xFFFF;
				bridgeAddCmd->output_cvlan = 0xFFFF;
			}	
			goto FCI_CMD;
 
		case FPP_CMD_RX_L2BRIDGE_REMOVE:
			bridgeRemoveCmd = (fpp_l2_bridge_remove_entry_cmd_t *)cmd_buf;
			*res_len = 2;
			if( cmd_len < sizeof(fpp_l2_bridge_remove_entry_cmd_t) )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge remove command size too small(%d, %zu)\n", 
						__func__, cmd_len, sizeof(fpp_l2_bridge_remove_entry_cmd_t));
				return 0;
			}
			
			if( parse_interface_qinq(bridgeRemoveCmd->input_name, &interface, &svlan_id, &cvlan_id) < 0 )
			{
				cmm_print(DEBUG_ERROR, "%s: Bridge remove command with unknown interface: %s\n",
						__func__, bridgeRemoveCmd->input_name);
				res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
				return 0; 
			}
			
			bridgeRemoveCmd->input_interface = interface;
			bridgeRemoveCmd->input_svlan = svlan_id;
			bridgeRemoveCmd->input_cvlan = cvlan_id;
					
			goto FCI_CMD;

		case FPP_CMD_RX_L2BRIDGE_QUERY_STATUS:
		case FPP_CMD_RX_L2BRIDGE_QUERY_ENTRY:
			goto FCI_CMD;
	}
FCI_CMD:
	return fci_cmd(fci_handle, fc, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
	
}
  
int parse_icc_interface(char *pstring, unsigned short *pinterface_number, int num_interfaces)
{
	u_int32_t interface;

	if ((short)(*pinterface_number = get_port_id(pstring)) < 0)
	{
		if (parse_value(pstring, &interface, num_interfaces - 1) == 0)
		{
			*pinterface_number = interface;
		}
		else
		{
			return -1;
		}
	}

	return 0;	
}

static int parse_interface(char *pstring, unsigned short *pinterface_number)
{
	if ((short)(*pinterface_number = get_port_id(pstring)) < 0)
	{
#ifdef WIFI_ENABLE
		int i, ret;

		__pthread_mutex_lock(&itf_table.lock);
		ret = __itf_is_programmed(if_nametoindex(pstring));
		__pthread_mutex_unlock(&itf_table.lock);

		if( ret <= 0 )
			return -1;

		for (i = 0; i < MAX_WIFI_FF_IFS; i++)	
		{
			if( !strcmp(pstring, glbl_wifi_ff_ifs[i].ifname) && glbl_wifi_ff_ifs[i].used )
			{
				*pinterface_number = WIFI_PORT0 + i;
				break;
			}
		}
	
		if( i >= MAX_WIFI_FF_IFS )
#endif
			return -1;
	}
	return 0;
}

static int parse_interface_qinq(char *pstring, unsigned short *pinterface_number, unsigned short *psvlan_id, unsigned short *pcvlan_id)
{
	char interface[16];
	char *pperiod;
	char *peos;
	unsigned long vlan_id;

	*psvlan_id = 0xFFFF;
	*pcvlan_id = 0xFFFF;
	strncpy(interface, pstring, sizeof(interface) - 1);
	STR_TRUNC_END(interface, sizeof(interface));

	pperiod = strchr(interface, '.');
	if (pperiod)
	    	*pperiod++ = '\0';
	if ((short)(*pinterface_number = get_port_id(interface)) < 0)
	{
#ifdef WIFI_ENABLE
		int i, ret;
		
		__pthread_mutex_lock(&itf_table.lock);
		ret = __itf_is_programmed(if_nametoindex(interface));
		__pthread_mutex_unlock(&itf_table.lock);

		if( ret <= 0 )
			return -1;

		for (i = 0; i < MAX_WIFI_FF_IFS; i++)	
		{
			if( !strcmp(interface, glbl_wifi_ff_ifs[i].ifname) &&  glbl_wifi_ff_ifs[i].used )
			{
				*pinterface_number = WIFI_PORT0 + 1;
				break;
			}
		}
		
		if( i >= MAX_WIFI_FF_IFS )
#endif
			return -1;
	}

	if (pperiod)
	{
		vlan_id = strtoul(pperiod, &peos, 10);
		if (peos == pperiod || vlan_id > 4094)
		{
			cmm_print(DEBUG_CRIT, "ERROR: Invalid SVLAN specification: %s\n", pstring);
			return -1;
		}
		*psvlan_id = (unsigned short)vlan_id;
		if (*peos == '\0')
		{
			return 0;
		}

		pperiod = peos + 1;
		vlan_id = strtoul(pperiod, &peos, 10);
		if (peos == pperiod || *peos != '\0' || vlan_id > 4094)
		{
			cmm_print(DEBUG_CRIT, "ERROR: Invalid CVLAN specification: %s\n", pstring);
			return -1;
		}
		*pcvlan_id = (unsigned short)vlan_id;
	}

	return 0;
}

int parse_macaddr(char *pstring, unsigned char *pmacaddr)
{
    int i, n;
    unsigned long nextbyte;
    char *endp;
    for (i = 0; i < 6; i++)
    {
	nextbyte = strtoul(pstring, &endp, 16);
	n = endp - pstring;
	if (n < 1 || n > 2)
	    return 0;
	if ((i < 5 && *endp != ':') || (i == 5 && *endp != '\0'))
	    return 0;
	*pmacaddr++ = (unsigned char)nextbyte;
	pstring = endp + 1;
    }
    return 1;
}

