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
#include <signal.h>
#include <libnetfilter_conntrack/libnetfilter_conntrack.h>
#include <libnetfilter_conntrack/libnetfilter_conntrack_tcp.h>
#include <net/if.h>

#include "cmm.h"
#include "itf.h"
#include "ffbridge.h"
#include "fpp.h"
#include "cmmd.h"
#include "fpp_private.h"

#define CTCMD_FLAGS_ORIG_DISABLED	(1 << 0)
#define CTCMD_FLAGS_REP_DISABLED	(1 << 1)
#define CTCMD_FLAGS_PERMANENT		(1 << 2)

/* CtExCommand  FORMAT bitfield DEFINES*/
#define CT_SECURE               (1 << 0)
#define CT_ORIG_TUNNEL      (1 << 1)
#define CT_REPL_TUNNEL      (1 << 2)


/*****************************************************************
* cmmFeReset
*
*
******************************************************************/
void cmmFeReset(FCI_CLIENT *fci_handle)
{
	int i;
	struct ctTable *ctEntry;
#ifdef IPSEC_FLOW_CACHE
	struct FlowEntry *FlowEntry;
#endif /* IPSEC_FLOW_CACHE */
	struct RtEntry *rtEntry;
	struct NeighborEntry *neigh;
	struct socket *socket;
	struct list_head *entry;

	cmm_print(DEBUG_ERROR, "%s: start\n", __func__);

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&socket_lock);
	__pthread_mutex_lock(&brMutex);

	// Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_RESET\n");
	if (fci_write(fci_handle, FPP_CMD_IPV4_RESET, 0, NULL))
	{
		cmm_print(DEBUG_ERROR, "Error while trying to reset IPv4 forward Engine\n");
		goto unlock;
	}

	cmm_print(DEBUG_COMMAND, "Send CMD_IPV6_RESET\n");
	if (fci_write(fci_handle, FPP_CMD_IPV6_RESET, 0, NULL))
	{
		cmm_print(DEBUG_ERROR, "Error while trying to reset IPv6 forward Engine\n");
		goto unlock;
	}

	for (i = 0; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
		while(!list_empty(&ct_table[i]))
		{
			entry = list_first(&ct_table[i]);
			ctEntry = container_of(entry, struct ctTable, list);

			__cmmCtRemove(ctEntry);
		}
	}

#ifdef IPSEC_FLOW_CACHE
	for (i = 0; i < FLOW_HASH_TABLE_SIZE; i++)
	{
		while (!list_empty(&flow_table[i]))
		{
			entry = list_first(&flow_table[i]);
			FlowEntry = container_of(entry, struct FlowEntry, list);
			__cmmFlowRemove(FlowEntry);
		}
	}
#endif /* IPSEC_FLOW_CACHE */

	for (i = 0; i < 2 * ROUTE_HASH_TABLE_SIZE; i++)
	{
		while (!list_empty(&rt_table[i]))
		{
			entry = list_first(&rt_table[i]);
			rtEntry = container_of(entry, struct RtEntry, list);
			__cmmRouteRemove(rtEntry);
		}
	}

	for (i = 0; i < 2 * NEIGHBOR_HASH_TABLE_SIZE; i++)
	{
		while (!list_empty(&neigh_table[i]))
		{
			entry = list_first(&neigh_table[i]);
			neigh = container_of(entry, struct NeighborEntry, list);
			__cmmNeighRemove(neigh);
		}
	}

	for (i = 0; i < HASH_SOCKET_SIZE; i++)
	{
		while (!list_empty(&socket_table[i]))
		{
			entry = list_first(&socket_table[i]);
			socket = container_of(entry, struct socket, list);
			socket_remove(socket);
		}
	}
#ifdef AUTO_BRIDGE
	 __cmm_l2flow_reset(fci_handle);
#endif

unlock:
	__pthread_mutex_unlock(&brMutex);
	__pthread_mutex_unlock(&socket_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	cmm_print(DEBUG_ERROR, "%s: end\n", __func__);
}
#ifdef AUTO_BRIDGE
static void cmmFeL2FlowChange(struct cmm_ct *ctx, fpp_l2_bridge_flow_entry_cmd_t* cmd, int len)
{
	struct l2flow l2flow_tmp;

	memset(&l2flow_tmp, 0 , sizeof(l2flow_tmp));
	memcpy(l2flow_tmp.saddr, cmd->srcaddr, ETH_ALEN);
	memcpy(l2flow_tmp.daddr, cmd->destaddr, ETH_ALEN);
	l2flow_tmp.ethertype = cmd->ethertype;
	l2flow_tmp.svlan_tag =cmd->svlan_tag;
	l2flow_tmp.cvlan_tag =cmd->cvlan_tag;
#ifdef VLAN_FILTER
	l2flow_tmp.vid =cmd->vid;
	l2flow_tmp.vlan_flags =cmd->vlan_flags;
#endif
	l2flow_tmp.session_id = cmd->session_id;
	l2flow_tmp.l3.proto = cmd->proto;
	memcpy(l2flow_tmp.l3.saddr.all, cmd->saddr, 16);
	memcpy(l2flow_tmp.l3.daddr.all, cmd->daddr, 16);
	l2flow_tmp.l4.sport = cmd->sport;
	l2flow_tmp.l4.dport = cmd->dport;
		
	__pthread_mutex_lock(&brMutex);
	__cmm_l2flow_deregister(ctx->fci_handle, &l2flow_tmp);
	__pthread_mutex_unlock(&brMutex);

}

int cmmFeL2FlowUpdate(FCI_CLIENT* fci_handler, int request, struct l2flowTable *l2flow_entry)
{
	int action;
	short ret;
	fpp_l2_bridge_flow_entry_cmd_t cmd;
	memset(&cmd, 0, sizeof(cmd));

	switch (request)
	{
	default:
	case (ADD | UPDATE):
		if ((l2flow_entry->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED)
			goto out;

		if ((l2flow_entry->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == (FPP_PROGRAMMED | FPP_NEEDS_UPDATE))
		{
			action = FPP_ACTION_UPDATE;
		}
		else
		{
			action = FPP_ACTION_REGISTER;
		}

		break;

	case UPDATE:
		if (!((l2flow_entry->flags & FPP_PROGRAMMED) && (l2flow_entry->flags & FPP_NEEDS_UPDATE)))
			goto out;

		action = FPP_ACTION_UPDATE;

		break;

	case REMOVE:
		if (!(l2flow_entry->flags & FPP_PROGRAMMED))
			goto out;

		action = FPP_ACTION_DEREGISTER;

		break;
	}
	
	if (__itf_get_name(l2flow_entry->idev_ifi, cmd.input_name, sizeof(cmd.input_name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, l2flow_entry->idev_ifi);

		goto err;
	}
	if (action != FPP_ACTION_DEREGISTER)
	{
		if (__itf_get_name(l2flow_entry->odev_ifi, cmd.output_name, sizeof(cmd.output_name)) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, l2flow_entry->odev_ifi);

			goto err;
		}
	}
	cmd.action = action;
	memcpy(cmd.destaddr, l2flow_entry->l2flow.daddr, ETH_ALEN);
	memcpy(cmd.srcaddr, l2flow_entry->l2flow.saddr, ETH_ALEN);
	cmd.ethertype = l2flow_entry->l2flow.ethertype;
	cmd.svlan_tag = l2flow_entry->l2flow.svlan_tag;
	cmd.cvlan_tag = l2flow_entry->l2flow.cvlan_tag;
#ifdef VLAN_FILTER
	cmd.vid = l2flow_entry->l2flow.vid;
	cmd.vlan_flags = l2flow_entry->l2flow.vlan_flags;
#endif
	cmd.session_id = l2flow_entry->l2flow.session_id;
	cmd.proto = l2flow_entry->l2flow.l3.proto;
	memcpy(cmd.saddr, l2flow_entry->l2flow.l3.saddr.all, 16);
	memcpy(cmd.daddr, l2flow_entry->l2flow.l3.daddr.all, 16);
	cmd.sport = l2flow_entry->l2flow.l4.sport;
	cmd.dport = l2flow_entry->l2flow.l4.dport;
	cmd.mark = l2flow_entry->mark;

	cmm_print(DEBUG_INFO, "PROTO %d\n", cmd.proto);

	switch (action)
	{
	case FPP_ACTION_REGISTER:
		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send FPP_CMD_RX_L2FLOW_ENTRY, ACTION_REGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_RX_L2FLOW_ENTRY, sizeof(fpp_l2_bridge_flow_entry_cmd_t), (unsigned short *) &cmd);

		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_BRIDGE_ENTRY_ALREADY_EXISTS)){
			l2flow_entry->flags |= FPP_PROGRAMMED;
			l2flow_entry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else	{
			cmm_print(DEBUG_ERROR, "Error %d while sending FPP_CMD_RX_L2FLOW_ENTRY, ACTION_REGISTER\n", ret);
			goto err;
		}
		break;

	case FPP_ACTION_UPDATE:
		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send FPP_CMD_RX_L2FLOW_ENTRY, ACTION_UPDATE\n");

		ret = fci_write(fci_handler, FPP_CMD_RX_L2FLOW_ENTRY, sizeof(fpp_l2_bridge_flow_entry_cmd_t), (unsigned short *) &cmd);

		if (ret == FPP_ERR_OK){
			l2flow_entry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else	{
			cmm_print(DEBUG_ERROR, "Error %d while sending FPP_CMD_RX_L2FLOW_ENTRY, ACTION_UPDATE\n", ret);
			goto err;
		}
		break;

	case FPP_ACTION_DEREGISTER:
		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send FPP_CMD_RX_L2FLOW_ENTRY, ACTION_DEREGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_RX_L2FLOW_ENTRY, sizeof(fpp_l2_bridge_flow_entry_cmd_t), (unsigned short *) &cmd);

		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_BRIDGE_ENTRY_NOT_FOUND)){
			l2flow_entry->flags &= ~FPP_PROGRAMMED;
		}
		else	{
			cmm_print(DEBUG_ERROR, "Error %d while sending FPP_CMD_RX_L2FLOW_ENTRY, ACTION_DEREGISTER\n", ret);
			goto err;
		}
		break;
	default:
		break;
	}
out:
	return 0;
err:
	return -1;
}

#endif
/*****************************************************************
* cmmFeCtUpdate4
*
*
******************************************************************/
int cmmFeCtUpdate4(FCI_CLIENT *fci_handler, int action, struct ctTable *ctEntry)
{
	fpp_ct_ex_cmd_t cmd;
	int cmd_size = sizeof(fpp_ct_cmd_t);
	short ret;
	char saddr_buf[INET_ADDRSTRLEN], daddr_buf[INET_ADDRSTRLEN];


	memset(&cmd, 0, sizeof(cmd));
	cmd.action = action;
	cmd.protocol = nfct_get_attr_u8(ctEntry->ct, ATTR_ORIG_L4PROTO);

	cmd.saddr = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_IPV4_SRC);
	cmd.daddr = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_IPV4_DST);
	cmd.saddr_reply = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_IPV4_SRC);
	cmd.daddr_reply = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_IPV4_DST);

	cmd.sport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_SRC);
	cmd.dport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_DST);
	cmd.sport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_SRC);
	cmd.dport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_DST);
	cmd.qosconnmark = cmmQosmarkGet(ctEntry->ct);

	if (!(ctEntry->fpp_dir & ORIGINATOR))
		cmd.flags |= CTCMD_FLAGS_ORIG_DISABLED;

	if (!(ctEntry->fpp_dir & REPLIER))
		cmd.flags |= CTCMD_FLAGS_REP_DISABLED;

	cmm_print(DEBUG_INFO, "%s: protocol=%d, qosconnmark=0x%" PRIx64 "\n", __func__, cmd.protocol, cmd.qosconnmark);
	cmm_print(DEBUG_INFO, "  Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
		  inet_ntop(AF_INET, &cmd.saddr, saddr_buf, sizeof(saddr_buf)),
			    inet_ntop(AF_INET, &cmd.daddr, daddr_buf, sizeof(daddr_buf)),
				      ntohs(cmd.sport), ntohs(cmd.dport));

	cmm_print(DEBUG_INFO, "  SaddrReply=%s, DaddrReply=%s, SportReply=%d, DportReply=%d\n",
		  inet_ntop(AF_INET, &cmd.saddr_reply, saddr_buf, sizeof(saddr_buf)),
			    inet_ntop(AF_INET, &cmd.daddr_reply, daddr_buf, sizeof(daddr_buf)),
				      ntohs(cmd.sport_reply), ntohs(cmd.dport_reply));

	if (action != FPP_ACTION_DEREGISTER)
	{
		/**
		 * We must check to see if the queue and DSCP marking parameters were applied to the
		 * correct direction.  When the connection was established, we assume that the orig
		 * direction is upstream (output port = WAN).  If this is not true, then we must swap
		 * the queue, DSCP marking parameters, and VLAN p-bit settings with the twin connection.
		 */

#if !defined(LS1043)
		{
			qosconnmark_t qmark;
			qmark.x = cmd.qosconnmark;
			if ( (qmark.qosmark_ds.ds_flag) &&
				(((ctEntry->fpp_dir & ORIGINATOR) &&
				(is_wan_port_ifindex(nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_COMCERTO_FP_IIF)) &&
				!is_wan_port_ifindex(ctEntry->orig.route->oifindex))) ||
				((ctEntry->fpp_dir & REPLIER) &&
				(!is_wan_port_ifindex(nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_COMCERTO_FP_IIF)) &&
				is_wan_port_ifindex(ctEntry->rep.route->oifindex))))
				)
			{
				qosmark_t qmark_lo, qmark_hi;
				qmark_lo.x = qmark.x_us;
				qmark_hi.x = qmark.x_ds;
				qmark.x_us = qmark_hi.x; qmark.qosmark_us.ds_flag = 0;
				qmark.x_ds = qmark_lo.x; qmark.qosmark_ds.ds_flag = 1;
				cmd.qosconnmark = qmark.x;
			}
		}
#endif

#ifdef IPSEC_FLOW_CACHE
		if (ctEntry->fEntryOrigOut || ctEntry->fEntryOrigFwd)
		{
			unsigned char sa_nr = 0;

			if (ctEntry->fEntryOrigOut)			
			{
				cmd.sa_dir |= FLOW_DIR_OUT_BITVAL;
				sa_nr += ctEntry->fEntryOrigOut->sa_nr;
			}

			if (ctEntry->fEntryOrigFwd)			
			{
				cmd.sa_dir |= FLOW_DIR_FWD_BITVAL;
				sa_nr += ctEntry->fEntryOrigFwd->sa_nr;
			}

			if (sa_nr > MAX_SA_PER_FLOW)
			{
				cmm_print(DEBUG_ERROR, "Number of SAs attached to the flow is more than MAX_SA_PER_FLOW: %d\n", sa_nr);
				goto err;
			}

			cmd.sa_nr = sa_nr;
			
			if ( (cmd.sa_dir & ( FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL)) == ( FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL) )
			{
				cmd.sa_handle[0] = ctEntry->fEntryOrigFwd->sa_handle[0];
				cmd.sa_handle[1] = ctEntry->fEntryOrigOut->sa_handle[0];
				cmm_print(DEBUG_INFO, "Both decrypt and encrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
			}
			else if (cmd.sa_dir &  FLOW_DIR_OUT_BITVAL )
			{
				memcpy(cmd.sa_handle, ctEntry->fEntryOrigOut->sa_handle, ctEntry->fEntryOrigOut->sa_nr * sizeof(unsigned short));
				cmm_print(DEBUG_INFO, "Only encrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
			}
			else
			{
				memcpy(cmd.sa_handle, ctEntry->fEntryOrigFwd->sa_handle, ctEntry->fEntryOrigFwd->sa_nr * sizeof(unsigned short));
				cmm_print(DEBUG_INFO, "Only decrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
			}
			cmd.format |= CT_SECURE;
		}

		if (ctEntry->fEntryRepOut || ctEntry->fEntryRepFwd)
		{
			unsigned char sa_reply_nr = 0;

			if (ctEntry->fEntryRepFwd)
			{
				cmd.sa_reply_dir |= FLOW_DIR_FWD_BITVAL;
				sa_reply_nr += ctEntry->fEntryRepFwd->sa_nr;
			}
			if (ctEntry->fEntryRepOut)
			{
				cmd.sa_reply_dir |= FLOW_DIR_OUT_BITVAL;
				sa_reply_nr += ctEntry->fEntryRepOut->sa_nr;
			}

			if (sa_reply_nr > MAX_SA_PER_FLOW)
			{
				cmm_print(DEBUG_ERROR, "sa_reply nr ERRRRROR: %d\n", sa_reply_nr);
				goto err;
			}
			cmd.sa_reply_nr = sa_reply_nr;
			if ((cmd.sa_reply_dir & (FLOW_DIR_FWD_BITVAL | FLOW_DIR_OUT_BITVAL)) == (FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL))
			{
				cmd.sa_reply_handle[0] = ctEntry->fEntryRepFwd->sa_handle[0];
				cmd.sa_reply_handle[1] = ctEntry->fEntryRepOut->sa_handle[0];
				cmm_print(DEBUG_INFO, "Reply: Both decrypt and encrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
			}
			else if (cmd.sa_reply_dir & FLOW_DIR_OUT_BITVAL)
			{
				memcpy(cmd.sa_reply_handle, ctEntry->fEntryRepOut->sa_handle, ctEntry->fEntryRepOut->sa_nr * sizeof(unsigned short));
				cmm_print(DEBUG_INFO, "Reply: Only encrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
			}
			else
			{
				memcpy(cmd.sa_reply_handle, ctEntry->fEntryRepFwd->sa_handle, ctEntry->fEntryRepFwd->sa_nr * sizeof(unsigned short));
				cmm_print(DEBUG_INFO, "Reply: Only decrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
			}

			cmd.format |= CT_SECURE;
		}
#else
		if (ctEntry->flags & FLOW_NO_ORIG_SA)
		{
			cmm_print(DEBUG_INFO, "%s(%d) NO FLOW SA , not setting SA related info\n",
					__FUNCTION__,__LINE__);
			cmd.format |= CT_SECURE;
			goto proceedv4_if_flow_no_orig_sa;
		}
		if (ctEntry->fEntryOrigFwdSA)
		{
			cmd.sa_dir |= FLOW_DIR_FWD_BITVAL;
			cmd.sa_handle[cmd.sa_nr] = ctEntry->fEntryOrigFwdSA->SAInfo.sagd;
			cmd.sa_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Only decrypt[0]: %x\n",  cmd.sa_handle[0]);
		}
		if (ctEntry->fEntryOrigOutSA)
		{
			cmd.sa_dir |= FLOW_DIR_OUT_BITVAL;
			cmd.sa_handle[cmd.sa_nr] = ctEntry->fEntryOrigOutSA->SAInfo.sagd;
			cmd.sa_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Only encrypt[0]: %x\n", cmd.sa_handle[0]);
		}

proceedv4_if_flow_no_orig_sa:
		if (ctEntry->flags & FLOW_NO_REPL_SA)
		{
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "%s(%d) NO FLOW SA , not setting SA related info\n",
					__FUNCTION__,__LINE__);
			goto proceedv4_if_flow_no_repl_sa;
		}

		if (ctEntry->fEntryRepFwdSA)
		{
			cmd.sa_reply_dir |= FLOW_DIR_FWD_BITVAL;
			cmd.sa_reply_handle[cmd.sa_reply_nr] = ctEntry->fEntryRepFwdSA->SAInfo.sagd;
			cmd.sa_reply_nr++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Reply: Only decrypt[0]: %x\n", cmd.sa_reply_handle[0]);
		}
		if (ctEntry->fEntryRepOutSA)
		{
			cmd.sa_reply_dir |= FLOW_DIR_OUT_BITVAL;
			cmd.sa_reply_handle[cmd.sa_reply_nr] = ctEntry->fEntryRepOutSA->SAInfo.sagd;
			cmd.sa_reply_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Reply: Only encrypt[0]: %x\n", cmd.sa_reply_handle[0]);
		}

proceedv4_if_flow_no_repl_sa:
#endif /* IPSEC_FLOW_CACHE */

		if (cmd.format & CT_SECURE)
		{
			cmm_print(DEBUG_INFO, "IPv4 conntrack secure Orig dir:%x SAh(%d):%x: %x Repl dir:%x SAh(%d): %x :%x\n",
					cmd.sa_dir, cmd.sa_nr, cmd.sa_handle[0], cmd.sa_handle[1],cmd.sa_reply_dir,
					cmd.sa_reply_nr, cmd.sa_reply_handle[0], cmd.sa_reply_handle[1]);
			cmd_size = sizeof(fpp_ct_ex_cmd_t);
		}

		if (ctEntry->orig_tunnel.fpp_route)
		{
			cmd.format |= CT_ORIG_TUNNEL;
			cmd.tunnel_route_id = ctEntry->orig_tunnel.fpp_route_id;
		}

		if (ctEntry->rep_tunnel.fpp_route)
		{
			cmd.format |= CT_REPL_TUNNEL;
			cmd.tunnel_route_id_reply = ctEntry->rep_tunnel.fpp_route_id;

		}

		if (cmd.format & (CT_ORIG_TUNNEL | CT_REPL_TUNNEL)) 
			cmd_size = sizeof(fpp_ct_ex_cmd_t);


		cmd.route_id = ctEntry->orig.fpp_route_id;
		cmd.route_id_reply = ctEntry->rep.fpp_route_id;
	}

	switch (action)
	{
	case FPP_ACTION_REGISTER:

		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_CONNTRACK ACTION_REGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_IPV4_CONNTRACK, cmd_size, (unsigned short *) &cmd);

		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_CT_ENTRY_ALREADY_REGISTERED))
		{
#ifdef	IPSEC_FLOW_CACHE
			if (ctEntry->fEntryOrigFwd)
			{
				ctEntry->fEntryOrigFwd->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryOrigFwd->flags &= ~FPP_NEEDS_UPDATE;
			}
			if (ctEntry->fEntryOrigOut)
			{
				ctEntry->fEntryOrigOut->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryOrigOut->flags &= ~FPP_NEEDS_UPDATE;
			}

			if (ctEntry->fEntryRepFwd)
			{
				ctEntry->fEntryRepFwd->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryRepFwd->flags &= ~FPP_NEEDS_UPDATE;
			}

			if (ctEntry->fEntryRepOut)
			{
				ctEntry->fEntryRepOut->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryRepOut->flags &= ~FPP_NEEDS_UPDATE;
			}
#endif /* IPSEC_FLOW_CACHE */
			ctEntry->flags |= FPP_PROGRAMMED;
			ctEntry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV4_CONNTRACK, ACTION_REGISTER\n", ret);
			goto err;
		}

		break;

	case FPP_ACTION_UPDATE:
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_CONNTRACK ACTION_UPDATE\n");

		ret = fci_write(fci_handler, FPP_CMD_IPV4_CONNTRACK, cmd_size, (unsigned short *) &cmd);
		if (ret == FPP_ERR_OK)
		{
#ifdef	IPSEC_FLOW_CACHE
			if (ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd->flags &= ~FPP_NEEDS_UPDATE;
			if (ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut->flags &= ~FPP_NEEDS_UPDATE;

			if (ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd->flags &= ~FPP_NEEDS_UPDATE;
			if (ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut->flags &= ~FPP_NEEDS_UPDATE;
#endif /* IPSEC_FLOW_CACHE */
			ctEntry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV4_CONNTRACK, ACTION_UPDATE\n", ret);
			goto err;
		}

		break;

	case FPP_ACTION_DEREGISTER:
	default:
		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_CONNTRACK ACTION_DEREGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_IPV4_CONNTRACK, sizeof(fpp_ct_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_CT_ENTRY_NOT_FOUND))
		{
#ifdef	IPSEC_FLOW_CACHE
			if (ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd->flags &= ~FPP_PROGRAMMED;
			if (ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut->flags &= ~FPP_PROGRAMMED;

			if (ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd->flags &= ~FPP_PROGRAMMED;
			if (ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut->flags &= ~FPP_PROGRAMMED;
#endif /* IPSEC_FLOW_CACHE */

			ctEntry->flags &= ~FPP_PROGRAMMED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV4_CONNTRACK, ACTION_DEREGISTER\n", ret);
			goto err;
		}

		break;
	}

	return 0;

err:
	return -1;
}

/*****************************************************************
* cmmFeCtUpdate6
*
*
******************************************************************/
int cmmFeCtUpdate6(FCI_CLIENT *fci_handler, int action, struct ctTable *ctEntry)
{
	fpp_ct6_ex_cmd_t cmd;
	int cmd_size = sizeof(fpp_ct6_cmd_t);
	short ret;
	char buf[INET6_ADDRSTRLEN], buf1[INET6_ADDRSTRLEN];

	memset(&cmd, 0, sizeof(cmd));
	cmd.action = action;
	cmd.protocol = nfct_get_attr_u8(ctEntry->ct, ATTR_ORIG_L4PROTO);

	memcpy(cmd.saddr, nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV6_SRC), 16);
	memcpy(cmd.daddr, nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV6_DST), 16);
	memcpy(cmd.saddr_reply, nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV6_SRC), 16);
	memcpy(cmd.daddr_reply, nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV6_DST), 16);

	cmd.sport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_SRC);
	cmd.dport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_DST);
	cmd.sport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_SRC);
	cmd.dport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_DST);
	cmd.qosconnmark = cmmQosmarkGet(ctEntry->ct);

	cmd.route_id = ctEntry->orig.fpp_route_id;
	cmd.route_id_reply = ctEntry->rep.fpp_route_id;

	if (!(ctEntry->fpp_dir & ORIGINATOR))
		cmd.flags |= CTCMD_FLAGS_ORIG_DISABLED;

	if (!(ctEntry->fpp_dir & REPLIER))
		cmd.flags |= CTCMD_FLAGS_REP_DISABLED;

	cmm_print(DEBUG_INFO, "%s: protocol=%d, qosconnmark=0x%" PRIx64 "\n", __func__, cmd.protocol, cmd.qosconnmark);
	cmm_print(DEBUG_INFO, "  Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
		  inet_ntop(AF_INET6, &cmd.saddr, buf, sizeof(buf)),
			    inet_ntop(AF_INET6, &cmd.daddr, buf1, sizeof(buf1)),
				      ntohs(cmd.sport), ntohs(cmd.dport));
	cmm_print(DEBUG_INFO, "  SaddrReply=%s, DaddrReply=%s, SportReply=%d, DportReply=%d\n",
		  inet_ntop(AF_INET6, &cmd.saddr_reply, buf, sizeof(buf)),
			    inet_ntop(AF_INET6, &cmd.daddr_reply, buf1, sizeof(buf1)),
				      ntohs(cmd.sport_reply), ntohs(cmd.dport_reply));

	if (action != FPP_ACTION_DEREGISTER)
	{
		/**
		 * We must check to see if the queue and DSCP marking parameters were applied to the
		 * correct direction.  When the connection was established, we assume that the orig
		 * direction is upstream (output port = WAN).  If this is not true, then we must swap
		 * the queue, DSCP marking parameters, and VLAN p-bit settings with the twin connection.
		 */

#if !defined(LS1043)
		{
			qosconnmark_t qmark;
			qmark.x = cmd.qosconnmark;
			if ( (qmark.qosmark_ds.ds_flag) &&
				(((ctEntry->fpp_dir & ORIGINATOR) &&
				(is_wan_port_ifindex(nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_COMCERTO_FP_IIF)) &&
				!is_wan_port_ifindex(ctEntry->orig.route->oifindex))) ||
				((ctEntry->fpp_dir & REPLIER) &&
				(!is_wan_port_ifindex(nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_COMCERTO_FP_IIF)) &&
				is_wan_port_ifindex(ctEntry->rep.route->oifindex))))
				)
			{
				qosmark_t qmark_lo, qmark_hi;
				qmark_lo.x = qmark.x_us;
				qmark_hi.x = qmark.x_ds;
				qmark.x_us = qmark_hi.x; qmark.qosmark_us.ds_flag = 0;
				qmark.x_ds = qmark_lo.x; qmark.qosmark_ds.ds_flag = 1;
				cmd.qosconnmark = qmark.x;
			}
		}
#endif

#ifdef	IPSEC_FLOW_CACHE
		if (ctEntry->fEntryOrigFwd || ctEntry->fEntryOrigOut) {
			unsigned char sa_nr = 0;

                        if (ctEntry->fEntryOrigOut)
                        {
                                cmd.sa_dir |= FLOW_DIR_OUT_BITVAL;
                                sa_nr += ctEntry->fEntryOrigOut->sa_nr;
                        }

                        if (ctEntry->fEntryOrigFwd)
                        {
                                cmd.sa_dir |= FLOW_DIR_FWD_BITVAL;
                                sa_nr += ctEntry->fEntryOrigFwd->sa_nr;
                        }

                        if (sa_nr > MAX_SA_PER_FLOW)
                        {
                                cmm_print(DEBUG_ERROR, "Number of SAs attached to the flow is more than MAX_SA_PER_FLOW: %d\n", sa_nr);
                                goto err;
                        }

                        cmd.sa_nr = sa_nr;

                        if ( (cmd.sa_dir & ( FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL)) == ( FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL) )
                        {
                                cmd.sa_handle[0] = ctEntry->fEntryOrigFwd->sa_handle[0];
                                cmd.sa_handle[1] = ctEntry->fEntryOrigOut->sa_handle[0];
                                cmm_print(DEBUG_INFO, "Both decrypt and encrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
                        }
                        else if (cmd.sa_dir &  FLOW_DIR_OUT_BITVAL )
                        {
                                memcpy(cmd.sa_handle, ctEntry->fEntryOrigOut->sa_handle, ctEntry->fEntryOrigOut->sa_nr * sizeof(unsigned short));
                                cmm_print(DEBUG_INFO, "Only encrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
                        }
                        else
                        {
                                memcpy(cmd.sa_handle, ctEntry->fEntryOrigFwd->sa_handle, ctEntry->fEntryOrigFwd->sa_nr * sizeof(unsigned short));
                                cmm_print(DEBUG_INFO, "Only decrypt[%d]: %x-%x\n", sa_nr, cmd.sa_handle[0],cmd.sa_handle[1]);
                        }
                        cmd.format |= CT_SECURE;

		}

		if (ctEntry->fEntryRepOut || ctEntry->fEntryRepFwd)
                {
                        unsigned char sa_reply_nr = 0;

                        if (ctEntry->fEntryRepFwd)
                        {
                                cmd.sa_reply_dir |= FLOW_DIR_FWD_BITVAL;
                                sa_reply_nr += ctEntry->fEntryRepFwd->sa_nr;
                        }
                        if (ctEntry->fEntryRepOut)
                        {
                                cmd.sa_reply_dir |= FLOW_DIR_OUT_BITVAL;
                                sa_reply_nr += ctEntry->fEntryRepOut->sa_nr;
                        }

                        if (sa_reply_nr > MAX_SA_PER_FLOW)
                        {
                                cmm_print(DEBUG_ERROR, "sa_reply nr ERRRRROR: %d\n", sa_reply_nr);
                                goto err;
                        }
                        cmd.sa_reply_nr = sa_reply_nr;
                        if ((cmd.sa_reply_dir & (FLOW_DIR_FWD_BITVAL | FLOW_DIR_OUT_BITVAL)) == (FLOW_DIR_OUT_BITVAL | FLOW_DIR_FWD_BITVAL))
                        {
                                cmd.sa_reply_handle[0] = ctEntry->fEntryRepFwd->sa_handle[0];
                                cmd.sa_reply_handle[1] = ctEntry->fEntryRepOut->sa_handle[0];
                                cmm_print(DEBUG_INFO, "Reply: Both decrypt and encrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
                        }
                        else if (cmd.sa_reply_dir & FLOW_DIR_OUT_BITVAL)
                        {
                                memcpy(cmd.sa_reply_handle, ctEntry->fEntryRepOut->sa_handle, ctEntry->fEntryRepOut->sa_nr * sizeof(unsigned short));
                                cmm_print(DEBUG_INFO, "Reply: Only encrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
                        }
                        else
                        {
                                memcpy(cmd.sa_reply_handle, ctEntry->fEntryRepFwd->sa_handle, ctEntry->fEntryRepFwd->sa_nr * sizeof(unsigned short));
                                cmm_print(DEBUG_INFO, "Reply: Only decrypt[%d]: %x-%x\n", sa_reply_nr, cmd.sa_reply_handle[0],cmd.sa_reply_handle[1]);
                        }

                        cmd.format |= CT_SECURE;
                }
#else
		if (ctEntry->flags & FLOW_NO_ORIG_SA)
		{
			cmm_print(DEBUG_INFO, "%s(%d) NO FLOW SA , not setting SA related info\n",
					__FUNCTION__,__LINE__);
			cmd.format |= CT_SECURE;
			goto proceedv6_if_flow_no_orig_sa;
		}	
		if (ctEntry->fEntryOrigFwdSA)
		{
			cmd.sa_dir |= FLOW_DIR_FWD_BITVAL;
			cmd.sa_handle[cmd.sa_nr] = ctEntry->fEntryOrigFwdSA->SAInfo.sagd;
			cmd.sa_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Only decrypt[0]: %x\n",  cmd.sa_handle[0]);
		}
		if (ctEntry->fEntryOrigOutSA)
		{
			cmd.sa_dir |= FLOW_DIR_OUT_BITVAL;
			cmd.sa_handle[cmd.sa_nr] = ctEntry->fEntryOrigOutSA->SAInfo.sagd;
			cmd.sa_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Only encrypt[0]: %x\n", cmd.sa_handle[0]);
		}

proceedv6_if_flow_no_orig_sa:

		if (ctEntry->flags & FLOW_NO_REPL_SA)
		{
			cmm_print(DEBUG_INFO, "%s(%d) NO FLOW SA , not setting SA related info\n",
					__FUNCTION__,__LINE__);
			cmd.format |= CT_SECURE;
			goto proceedv6_if_flow_no_repl_sa;
		}
		if (ctEntry->fEntryRepFwdSA)
		{
			cmd.sa_reply_dir |= FLOW_DIR_FWD_BITVAL;
			cmd.sa_reply_handle[cmd.sa_reply_nr] = ctEntry->fEntryRepFwdSA->SAInfo.sagd;
			cmd.sa_reply_nr++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Reply: Only decrypt[0]: %x\n", cmd.sa_reply_handle[0]);
		}
		if (ctEntry->fEntryRepOutSA)
		{
			cmd.sa_reply_dir |= FLOW_DIR_OUT_BITVAL;
			cmd.sa_reply_handle[cmd.sa_reply_nr] = ctEntry->fEntryRepOutSA->SAInfo.sagd;
			cmd.sa_reply_nr ++;
			cmd.format |= CT_SECURE;
			cmm_print(DEBUG_INFO, "Reply: Only encrypt[0]: %x\n", cmd.sa_reply_handle[0]);
		}
		

proceedv6_if_flow_no_repl_sa:
#endif /* IPSEC_FLOW_CACHE */
		if (cmd.format & CT_SECURE) {
			cmm_print(DEBUG_INFO, "IPv6 conntrack secure Orig dir:%x SAh(%d): %x : %x Repl dir:%x SAh(%d): %x : %x\n",
					cmd.sa_dir, cmd.sa_nr, cmd.sa_handle[0], cmd.sa_handle[1], cmd.sa_reply_dir, cmd.sa_reply_nr, cmd.sa_reply_handle[0], cmd.sa_reply_handle[1]);

			cmd_size = sizeof(fpp_ct6_ex_cmd_t);
		}

		if (ctEntry->orig_tunnel.fpp_route)
		{
			cmd.format |= CT_ORIG_TUNNEL;
			cmd.tunnel_route_id = ctEntry->orig_tunnel.fpp_route_id;
		}

		if (ctEntry->rep_tunnel.fpp_route)
		{
			cmd.format |= CT_REPL_TUNNEL;
			cmd.tunnel_route_id_reply = ctEntry->rep_tunnel.fpp_route_id;
		}

		if (cmd.format & (CT_ORIG_TUNNEL | CT_REPL_TUNNEL)) {
			cmm_print(DEBUG_INFO, "IPv6 tunnel route id orig(%x) repl(%x)\n",
						cmd.tunnel_route_id,
						cmd.tunnel_route_id_reply);

			cmd_size = sizeof(fpp_ct6_ex_cmd_t);
		}
	}

	switch (action)
	{
	case FPP_ACTION_REGISTER:

		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV6_CONNTRACK ACTION_REGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_IPV6_CONNTRACK, cmd_size, (unsigned short *) &cmd);

		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_CT_ENTRY_ALREADY_REGISTERED))
		{
#ifdef  IPSEC_FLOW_CACHE 
			if (ctEntry->fEntryOrigOut)
			{
				ctEntry->fEntryOrigOut->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryOrigOut->flags &= ~FPP_NEEDS_UPDATE;
			}
			if (ctEntry->fEntryOrigFwd)
			{
				ctEntry->fEntryOrigFwd->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryOrigFwd->flags &= ~FPP_NEEDS_UPDATE;
			}

			if (ctEntry->fEntryRepOut)
			{
				ctEntry->fEntryRepOut->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryRepOut->flags &= ~FPP_NEEDS_UPDATE;
			}
			if (ctEntry->fEntryRepFwd)
			{
				ctEntry->fEntryRepFwd->flags |= FPP_PROGRAMMED;
				ctEntry->fEntryRepFwd->flags &= ~FPP_NEEDS_UPDATE;
			}
#endif /* IPSEC_FLOW_CACHE */

			ctEntry->flags |= FPP_PROGRAMMED;
			ctEntry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV6_CONNTRACK, ACTION_REGISTER\n", ret);
			goto err;
		}

		break;

	case FPP_ACTION_UPDATE:
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV6_CONNTRACK ACTION_UPDATE\n");

		ret = fci_write(fci_handler, FPP_CMD_IPV6_CONNTRACK, cmd_size, (unsigned short *) &cmd);
		if (ret == FPP_ERR_OK)
		{
#ifdef  IPSEC_FLOW_CACHE 
			if (ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut->flags &= ~FPP_NEEDS_UPDATE;
			if (ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd->flags &= ~FPP_NEEDS_UPDATE;

			if (ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut->flags &= ~FPP_NEEDS_UPDATE;
			if (ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd->flags &= ~FPP_NEEDS_UPDATE;
#endif /* IPSEC_FLOW_CACHE */

			ctEntry->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV6_CONNTRACK, ACTION_UPDATE\n", ret);
			goto err;
		}

		break;

	case FPP_ACTION_DEREGISTER:
	default:
		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_IPV6_CONNTRACK ACTION_DEREGISTER\n");
		ret = fci_write(fci_handler, FPP_CMD_IPV6_CONNTRACK, sizeof(fpp_ct6_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_CT_ENTRY_NOT_FOUND))
		{
#ifdef  IPSEC_FLOW_CACHE 
			if (ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut->flags &= ~FPP_PROGRAMMED;
			if (ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd->flags &= ~FPP_PROGRAMMED;

			if (ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut->flags &= ~FPP_PROGRAMMED;
			if (ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd->flags &= ~FPP_PROGRAMMED;
#endif /* IPSEC_FLOW_CACHE */

			ctEntry->flags &= ~FPP_PROGRAMMED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IPV6_CONNTRACK, ACTION_DEREGISTER\n", ret);
			goto err;
		}

		break;
	}

	return 0;

err:
	return -1;
}


/*****************************************************************
* cmmFeCtChange4
*
*
******************************************************************/
static void cmmFeCtChange4(struct cmm_ct *ctx, fpp_ct_cmd_t* cmd, int len)
{
	struct nf_conntrack *ctTemp;
	struct ctTable *ctEntry;

	if (len < sizeof(fpp_ct_cmd_t))
	{
		cmm_print(DEBUG_ERROR, "%s: wrong length(%d) CMD_IPV4_CONNTRACK_CHANGE\n", __func__, len);
		return;
	}

	ctTemp = nfct_new();
	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, cmd->protocol);
	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L3PROTO, AF_INET);
	nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_SRC, cmd->saddr);
	nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_DST, cmd->daddr);
	nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_SRC, cmd->saddr_reply);
	nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_DST, cmd->daddr_reply);

	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, cmd->sport);
	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, cmd->dport);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, cmd->sport_reply);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, cmd->dport_reply);

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);

	switch (cmd->action)
	{
	case FPP_ACTION_REMOVED:

		// We have to remove the conntrack in the conntrack table
		if (!(ctEntry = __cmmCtFind(ctTemp)))
		{
			cmm_print(DEBUG_WARNING, "%s: conntrack not found\n", __func__);
			goto end;
		}

		cmm_print(DEBUG_COMMAND, "%s: CMD_IPV4_CONNTRACK_CHANGE, ACTION_REMOVED\n", __func__);

		ctEntry->flags &= ~FPP_PROGRAMMED;

		cmmCtNetlinkRemove(ctx->handle, ctEntry->ct);
		ct_stats.removed++;
		____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry);

		break;

	case FPP_ACTION_TCP_FIN:

		// We have to remove the conntrack in the conntrack table
		if (!(ctEntry = __cmmCtFind(ctTemp)))
		{
			cmm_print(DEBUG_WARNING, "%s: conntrack not found\n", __func__);
			goto end;
		}

		cmm_print(DEBUG_COMMAND, "%s: CMD_IPV4_CONNTRACK_CHANGE, ACTION_TCP_FIN\n", __func__);

		ctEntry->flags &= ~FPP_PROGRAMMED;

		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported action(%d) for CMD_IPV4_CONNTRACK_CHANGE\n", __func__, cmd->action);
		break;
	}

end:
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	nfct_destroy(ctTemp);
}

/*****************************************************************
* cmmFeCtChange6
*
*
******************************************************************/
static void cmmFeCtChange6(struct cmm_ct *ctx, fpp_ct6_cmd_t* cmd, int len)
{
	struct nf_conntrack *ctTemp;
	struct ctTable *ctEntry;

	if (len < sizeof(fpp_ct6_cmd_t))
	{
		cmm_print(DEBUG_ERROR, "%s: wrong length(%d) CMD_IPV6_CONNTRACK_CHANGE\n", __func__, len);
		return;
	}

	ctTemp = nfct_new();
	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, cmd->protocol);
	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L3PROTO, AF_INET6);
	nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_SRC, &cmd->saddr[0]);
	nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_DST, &cmd->daddr[0]);

	nfct_set_attr(ctTemp, ATTR_REPL_IPV6_SRC, &cmd->saddr_reply[0]);
	nfct_set_attr(ctTemp, ATTR_REPL_IPV6_DST, &cmd->daddr_reply[0]);

	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, cmd->sport);
	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, cmd->dport);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, cmd->sport_reply);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, cmd->dport_reply);

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);

	switch(cmd->action)
	{
	case FPP_ACTION_REMOVED:
		// We have to remove the conntrack in the local table and also the one in the conntrack table

		if (!(ctEntry = __cmmCtFind(ctTemp)))
		{
			cmm_print(DEBUG_WARNING, "%s: conntrack not found\n", __func__);
			goto end;
		}

		cmm_print(DEBUG_COMMAND, "%s: CMD_IPV6_CONNTRACK_CHANGE, ACTION_REMOVED\n", __func__);

		ctEntry->flags &= ~FPP_PROGRAMMED;

		cmmCtNetlinkRemove(ctx->handle, ctEntry->ct);
		ct_stats.removed++;
		____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry);

		break;

	case FPP_ACTION_TCP_FIN:

		// We have to remove the conntrack in the conntrack table
		if (!(ctEntry = __cmmCtFind(ctTemp)))
		{
			cmm_print(DEBUG_WARNING, "%s: conntrack not found\n", __func__);
			goto end;
		}

		cmm_print(DEBUG_COMMAND, "%s: CMD_IPV6_CONNTRACK_CHANGE, ACTION_TCP_FIN\n", __func__);

		ctEntry->flags &= ~FPP_PROGRAMMED;

		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported action(%d) for CMD_IPV6_CONNTRACK_CHANGE\n", __func__, cmd->action);
		break;
	}

end:
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	nfct_destroy(ctTemp);
}


/*****************************************************************
* __cmmFeRouteUpdate
*
*
******************************************************************/
int __cmmFeRouteUpdate(FCI_CLIENT* fci_handler, int action, struct fpp_rt *fpp_route)
{
	fpp_rt_cmd_t cmd;
	short ret;

	memset(&cmd, 0, sizeof(cmd));

	cmd.action = action;
	cmd.id = fpp_route->id;

	if (action != FPP_ACTION_DEREGISTER)
	{
		memcpy(&cmd.dst_mac, fpp_route->dst_mac, 6);

		if (fpp_route->dst_addr_len) {
			if(fpp_route->dst_addr_len == 4 ) /* IPv4 Address Size */
				cmd.flags |= FPP_IP_ROUTE_6o4;
			else if(fpp_route->dst_addr_len == 16) /* IPv6 Address Size */
				cmd.flags |= FPP_IP_ROUTE_4o6;
			memcpy(&cmd.dst_addr, fpp_route->dst_addr, fpp_route->dst_addr_len);
		}

		if (__itf_get_name(fpp_route->oifindex, cmd.output_device, sizeof(cmd.output_device)) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, fpp_route->oifindex);
			goto err;
		}
#ifdef LS1043
		if (__itf_get_name(fpp_route->iifindex, cmd.input_device, sizeof(cmd.input_device)) < 0 &&
						!(fpp_route->flags & IS_LOCAL))
		{
			cmm_print(DEBUG_INFO, "%s: __itf_get_name(%d) failed (input device)\n", __func__, fpp_route->iifindex);
			goto err;
		}

		if (__itf_get_name(fpp_route->underlying_iifindex, cmd.underlying_input_device, sizeof(cmd.underlying_input_device)) < 0 &&
						!(fpp_route->flags & IS_LOCAL))
		{
			cmm_print(DEBUG_INFO, "%s: __itf_get_name(%d) failed (underlying input device)\n", __func__, fpp_route->underlying_iifindex);
			goto err;
		}
#endif

#ifdef VLAN_FILTER
		if (fpp_route->vlan_filter_flags & VLAN_FILTER_EN)
		{
			cmd.flags |= FPP_VLAN_FILTER_EN;
			if (fpp_route->vlan_filter_flags & EGRESS_UNTAG)
				cmd.flags |= FPP_VLAN_EGRESS_UNTAG;
			cmd.egress_vid = fpp_route->egress_vid;
		}
		if (fpp_route->vlan_filter_flags & VLAN_FILTER_INGRESS_EN)
		{
			cmd.flags |= FPP_VLAN_FILTER_INGRESS_EN;
			if (fpp_route->vlan_filter_flags & INGRESS_PVID)
				cmd.flags |= FPP_VLAN_INGRESS_PVID;
			cmd.underlying_vlan_id = fpp_route->underlying_vlan_id;
		}
#endif
		cmd.mtu = fpp_route->mtu;
	}

	switch (action)
	{
	case FPP_ACTION_REGISTER:
		cmm_print(DEBUG_COMMAND, "Send CMD_IP_ROUTE ACTION_REGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_IP_ROUTE, sizeof(fpp_rt_cmd_t), (unsigned short *)&cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_RT_ENTRY_ALREADY_REGISTERED))
		{
			fpp_route->flags |= FPP_PROGRAMMED;
			fpp_route->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IP_ROUTE, ACTION_REGISTER\n", ret);
			goto err;
		}

		break;

	case FPP_ACTION_UPDATE:
		cmm_print(DEBUG_COMMAND, "Send CMD_IP_ROUTE ACTION_UPDATE\n");

		ret = fci_write(fci_handler, FPP_CMD_IP_ROUTE, sizeof(fpp_rt_cmd_t), (unsigned short *)&cmd);
		if (ret == FPP_ERR_OK)
		{
			fpp_route->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IP_ROUTE, ACTION_UPDATE\n", ret);
		
			goto err;
		}

		break;

	case FPP_ACTION_DEREGISTER:
	default:
		cmm_print(DEBUG_COMMAND, "Send CMD_IP_ROUTE ACTION_DEREGISTER\n");

		ret = fci_write(fci_handler, FPP_CMD_IP_ROUTE, sizeof(fpp_rt_cmd_t), (unsigned short *)&cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_RT_ENTRY_NOT_FOUND))
		{
			fpp_route->flags &= ~FPP_PROGRAMMED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Error %d while sending CMD_IP_ROUTE, ACTION_DEREGISTER\n", ret);
			goto err;
		}

		break;
	}

	return 0;

err:
	return -1;
}



/*****************************************************************
* cmmRtQueryProcess
*
*
******************************************************************/
int cmmRtQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int rcvBytes;
	union u_rxbuf rxbuf;
	short rc;
	int count = 0;
	char dmac[MAC_ADDRSTRLEN];
	fpp_rt_cmd_t *rtCmd = (fpp_rt_cmd_t *)rxbuf.rcvBuffer;
	int is_ipv4 , is_ipv6 = 0;
	char daddr_buf[INET6_ADDRSTRLEN];


	rtCmd->action = FPP_ACTION_QUERY;
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_IP_ROUTE, rtCmd, sizeof(fpp_rt_cmd_t), rxbuf.rcvBuffer);
	if (rcvBytes < sizeof(fpp_rt_cmd_t) + sizeof(unsigned short)) {

		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;

		if (rc == FPP_ERR_UNKNOWN_ACTION) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP_CMD_IP_ROUTE does not support ACTION_QUERY\n");
		} else if (rc == FPP_ERR_RT_ENTRY_NOT_FOUND) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP IP ROUTE table empty\n");
		} else {
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
		}

		return CLI_OK;
        }

	cmm_print(DEBUG_STDOUT, "IP ROUTE:\n");

	do {
		is_ipv4 = 0;
		is_ipv6 = 0;
		rtCmd->output_device[sizeof(rtCmd->output_device) - 1] = '\0';

		if (rtCmd->dst_addr[0] != 0)
			is_ipv4 = 1;

		if (rtCmd->dst_addr[1] != 0 || rtCmd->dst_addr[2] != 0 || rtCmd->dst_addr[3] != 0)
			is_ipv6 = 1;

#ifdef LS1043
		if (is_ipv6)
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Output Iface: %s, Input Iface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device, rtCmd->input_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,inet_ntop(AF_INET6, &rtCmd->dst_addr, daddr_buf, sizeof(daddr_buf)));
		}
		else if (is_ipv4)
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Output Iface: %s, Input Iface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device, rtCmd->input_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,inet_ntop(AF_INET, &rtCmd->dst_addr, daddr_buf, sizeof(daddr_buf)));
		}
		else
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Output Iface: %s, Input Iface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device, rtCmd->input_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,"NULL");
		}
#else
		if (is_ipv6)
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Interface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,inet_ntop(AF_INET6, &rtCmd->dst_addr, daddr_buf, sizeof(daddr_buf)));
		}
		else if (is_ipv4)
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Interface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,inet_ntop(AF_INET, &rtCmd->dst_addr, daddr_buf, sizeof(daddr_buf)));
		}
		else
		{
		cmm_print(DEBUG_STDOUT, "%04d: Id: %x, Interface: %s, DST Mac: %s, Mtu: %d Daddr: %s\n",
			count,
			rtCmd->id,
			rtCmd->output_device,
			mac_ntop(rtCmd->dst_mac, dmac, sizeof(dmac)),
			rtCmd->mtu,"NULL");
		}
#endif

		count++;

		rtCmd->action = FPP_ACTION_QUERY_CONT;

		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_IP_ROUTE, rtCmd, sizeof(fpp_rt_cmd_t), rxbuf.rcvBuffer);

	} while (rcvBytes >= sizeof(fpp_rt_cmd_t) + sizeof(unsigned short));

	cmm_print(DEBUG_STDOUT, "Total FPP Route Entries: %d\n", count);
        
	return CLI_OK;
}

/*****************************************************************
* cmmCtQueryProcess
*
*
******************************************************************/
int cmmCtQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
	char output_buf[256];
        short rc;
        int count = 0,i,len =0;
        cmmd_ct_ex_cmd_t *ctCmd = (cmmd_ct_ex_cmd_t *)rxbuf.rcvBuffer;
        struct nf_conntrack *ctTemp;
	char saddr_buf[INET_ADDRSTRLEN], daddr_buf[INET_ADDRSTRLEN];
       
        ctCmd->action = CMMD_ACTION_QUERY;
	ctCmd->format = 0;
	ctCmd->sa_nr  = 0;
  	ctCmd->sa_reply_nr = 0;
        rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV4_CONNTRACK, ctCmd, sizeof(cmmd_ct_ex_cmd_t), rxbuf.rcvBuffer);
        if (rcvBytes < sizeof(cmmd_ct_ex_cmd_t) + sizeof(unsigned short)) {
            rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
            if (rc == FPP_ERR_UNKNOWN_ACTION) {
                cmm_print(DEBUG_STDERR, "ERROR: FPP CMM_IPV4_CONNTRACK does not support ACTION_QUERY\n");
            } else if (rc == FPP_ERR_CT_ENTRY_NOT_FOUND) {
                cmm_print(DEBUG_STDERR, "ERROR: FPP IPV4 CONNTRACK table empty\n");
            } else {
                cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
            }
            return CLI_OK;
        }
        ctTemp = nfct_new();
        cmm_print(DEBUG_STDOUT, "IPv4 Connections:\n");
        do {
            nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, ctCmd->protocol);

            nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_SRC, ctCmd->saddr);
            nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_DST, ctCmd->daddr);

            nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_SRC, ctCmd->saddr_reply);
            nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_DST, ctCmd->daddr_reply);

            nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, ctCmd->sport);
            nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, ctCmd->dport);
            nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, ctCmd->sport_reply);
            nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, ctCmd->dport_reply);
            cmmQosmarkSet(ctTemp, ctCmd->qosconnmark);

            //nfct_snprintf(buf, 500, ctTemp, NFCT_T_UNKNOWN, NFCT_O_PLAIN, NFCT_OF_SHOW_LAYER3);
            //cmm_print(DEBUG_STDOUT, "%04d: %s\n", count, buf);
            cmm_print(DEBUG_STDOUT, "%04d: protocol=%d, qosconnmark=0x%" PRIx64 "\n", count, ctCmd->protocol, ctCmd->qosconnmark);
            cmm_print(DEBUG_STDOUT, "  Init:  Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
                      inet_ntop(AF_INET, &ctCmd->saddr, saddr_buf, sizeof(saddr_buf)),
                      inet_ntop(AF_INET, &ctCmd->daddr, daddr_buf, sizeof(daddr_buf)),
                      ntohs(ctCmd->sport), ntohs(ctCmd->dport));
            cmm_print(DEBUG_STDOUT, "  Reply: Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
                      inet_ntop(AF_INET, &ctCmd->saddr_reply, saddr_buf, sizeof(saddr_buf)),
                      inet_ntop(AF_INET, &ctCmd->daddr_reply, daddr_buf, sizeof(daddr_buf)),
                      ntohs(ctCmd->sport_reply), ntohs(ctCmd->dport_reply));
            
            if (ctCmd->format)
	    {
		len += snprintf(output_buf+len,256-len, "IPSEC(Init:sa_nr=%d HO:",
							ctCmd->sa_nr);
                for (i =0; i< ctCmd->sa_nr;i++)
		    len += snprintf(output_buf+len ,256-len, "%x:",  ctCmd->sa_handle[i]);
		len +=  snprintf(output_buf+len,256-len,") ");
		len += snprintf(output_buf+len, 256-len,"(Reply:sa_nr=%d HO:",
						ctCmd->sa_reply_nr);
                for (i =0; i< ctCmd->sa_reply_nr;i++)
		    len += snprintf(output_buf+len ,256-len,"%x:", 
						ctCmd->sa_reply_handle[i]);
		len +=  snprintf(output_buf+len,256-len,") ");
	        cmm_print(DEBUG_STDOUT,"%s\n",output_buf);
	        len = 0;	
	    }

            count++;
            ctCmd->action = CMMD_ACTION_QUERY_CONT;
	    ctCmd->format = 0;
	    ctCmd->sa_nr  = 0;
  	    ctCmd->sa_reply_nr = 0;
            rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV4_CONNTRACK, ctCmd, sizeof(cmmd_ct_ex_cmd_t), rxbuf.rcvBuffer);
        } while (rcvBytes >= sizeof(cmmd_ct_ex_cmd_t) + sizeof(unsigned short));
        cmm_print(DEBUG_STDOUT, "Total Connection Entries: %d\n", count);

        nfct_destroy(ctTemp);
        return CLI_OK;
}

/*****************************************************************
 * * cmmCt6QueryProcess
 * *
 * *
 * ******************************************************************/
int cmmCt6QueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        char output_buf[256];
        short rc;
        int count = 0,len=0;
        cmmd_ct6_ex_cmd_t *ctCmd = (cmmd_ct6_ex_cmd_t *)rxbuf.rcvBuffer;
        struct nf_conntrack *ctTemp;
        char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN];

        ctCmd->action = CMMD_ACTION_QUERY;
	ctCmd->format = 0;
        ctCmd->sa_nr  = 0;
        ctCmd->sa_reply_nr = 0;

        rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV6_CONNTRACK, ctCmd, 
                                           sizeof(cmmd_ct6_ex_cmd_t), rxbuf.rcvBuffer);
        if (rcvBytes < sizeof(cmmd_ct6_ex_cmd_t) + sizeof(unsigned short)) {
            rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : 
                                                rxbuf.result ;
            if (rc == CMMD_ERR_UNKNOWN_ACTION) {
                cmm_print(DEBUG_STDERR, 
                  "ERROR: FPP CMM_IPV6_CONNTRACK does not support ACTION_QUERY\n");
            } else if (rc == FPP_ERR_CT_ENTRY_NOT_FOUND) {
                cmm_print(DEBUG_STDERR, "ERROR: FPP IPV6 CONNTRACK table empty\n");
            } else {
                cmm_print(DEBUG_STDERR, 
                         "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
            }
            return CLI_OK;
        }
        ctTemp = nfct_new();
        cmm_print(DEBUG_STDOUT, "IPv6 Connections:\n");
        do {
            nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, ctCmd->protocol);

            nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_SRC, &ctCmd->saddr[0]);
            nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_DST, &ctCmd->daddr[0]);

            nfct_set_attr(ctTemp, ATTR_REPL_IPV6_SRC, &ctCmd->saddr_reply[0]);
            nfct_set_attr(ctTemp, ATTR_REPL_IPV6_DST, &ctCmd->daddr_reply[0]);

            nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, ctCmd->sport);
            nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, ctCmd->dport);
            nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, ctCmd->sport_reply);
            nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, ctCmd->dport_reply);
            cmmQosmarkSet(ctTemp, ctCmd->qosconnmark);

            
            cmm_print(DEBUG_STDOUT, "%04d: protocol=%d, qosconnmark=0x%" PRIx64 "\n", 
                                            count, ctCmd->protocol, ctCmd->qosconnmark);
            cmm_print(DEBUG_STDOUT, "  Init:  Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
                      inet_ntop(AF_INET6, &ctCmd->saddr, saddr_buf, sizeof(saddr_buf)),
                      inet_ntop(AF_INET6, &ctCmd->daddr, daddr_buf, sizeof(daddr_buf)),
                      ntohs(ctCmd->sport), ntohs(ctCmd->dport));
            cmm_print(DEBUG_STDOUT, "  Reply: Saddr=%s, Daddr=%s, Sport=%d, Dport=%d\n",
                     inet_ntop(AF_INET6, &ctCmd->saddr_reply, saddr_buf, sizeof(saddr_buf)),
                     inet_ntop(AF_INET6, &ctCmd->daddr_reply, daddr_buf, sizeof(daddr_buf)),
                     ntohs(ctCmd->sport_reply), ntohs(ctCmd->dport_reply));

	    if (ctCmd->format & CT_SECURE)
            {
		int i;
                len += snprintf(output_buf+len, 256-len,"IPSEC(Init:sa_nr=%d HO:",ctCmd->sa_nr);
                for (i =0; i< ctCmd->sa_nr;i++)
                    len += snprintf(output_buf+len ,256-len,"%x:", ctCmd->sa_handle[i]);
		len +=  snprintf(output_buf+len,256-len,") ");
                len += snprintf(output_buf+len,256-len," (Reply: sa_nr=%d HO:",
					ctCmd->sa_reply_nr);
                for (i =0; i< ctCmd->sa_reply_nr;i++)
                    len += snprintf(output_buf+len,256-len,"%x:", 
						ctCmd->sa_reply_handle[i]);
		len +=  snprintf(output_buf+len,256-len,") ");
	        cmm_print(DEBUG_STDOUT,output_buf);
	        len = 0;	
            }
            count++;
            ctCmd->action = CMMD_ACTION_QUERY_CONT;
	    ctCmd->format = 0;
            ctCmd->sa_nr  = 0;
            ctCmd->sa_reply_nr = 0;
            rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV6_CONNTRACK, ctCmd, 
                                                 sizeof(cmmd_ct6_ex_cmd_t), rxbuf.rcvBuffer);
	 } while (rcvBytes >= sizeof(cmmd_ct6_ex_cmd_t) + sizeof(unsigned short));
        cmm_print(DEBUG_STDOUT, "Total Connection Entries: %d\n", count);

        nfct_destroy(ctTemp);
        return CLI_OK;
}
#ifdef AUTO_BRIDGE
/*****************************************************************
* cmmCtQueryProcess
*
*
******************************************************************/
int cmmL2FlowQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
	short rc;
	int count = 0;
	fpp_l2_bridge_flow_entry_cmd_t*cmd = (fpp_l2_bridge_flow_entry_cmd_t *)rxbuf.rcvBuffer;
       
	cmd->action = FPP_ACTION_QUERY;
	
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2FLOW_ENTRY, cmd, sizeof(fpp_l2_bridge_flow_entry_cmd_t), rxbuf.rcvBuffer);
	if (rcvBytes < sizeof(fpp_l2_bridge_flow_entry_cmd_t) + sizeof(unsigned short)) {
		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
		if (rc == FPP_ERR_UNKNOWN_ACTION) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP_CMD_RX_L2FLOW_ENTRY does not support ACTION_QUERY\n");
		} else if (rc == FPP_ERR_BRIDGE_ENTRY_NOT_FOUND) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP L2flow table empty\n");
		} else {
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
		}
		return CLI_OK;
	}

        cmm_print(DEBUG_STDOUT, "L2 flows:\n");
        do {
		struct l2flow l2flow_tmp;
		
		memset(&l2flow_tmp, 0 , sizeof(l2flow_tmp));
		memcpy(l2flow_tmp.saddr, cmd->srcaddr, ETH_ALEN);
		memcpy(l2flow_tmp.daddr, cmd->destaddr, ETH_ALEN);
		l2flow_tmp.ethertype = cmd->ethertype;
		l2flow_tmp.svlan_tag =cmd->svlan_tag;
		l2flow_tmp.cvlan_tag =cmd->cvlan_tag;
#ifdef VLAN_FILTER
		l2flow_tmp.vid =cmd->vid;
		l2flow_tmp.vlan_flags =cmd->vlan_flags;
#endif
		l2flow_tmp.session_id = cmd->session_id;
		l2flow_tmp.l3.proto = cmd->proto;
		memcpy(l2flow_tmp.l3.saddr.all, cmd->saddr, 16);
		memcpy(l2flow_tmp.l3.daddr.all, cmd->daddr, 16);
		l2flow_tmp.l4.sport = cmd->sport;
		l2flow_tmp.l4.dport = cmd->dport;
		
		cmm_l2flow_print(DEBUG_STDOUT, &l2flow_tmp, 0);
		cmm_print(DEBUG_STDOUT, "Input itf=%s ", cmd->input_name);
		cmm_print(DEBUG_STDOUT, "Output itf=%s ", cmd->output_name);
		cmm_print(DEBUG_STDOUT, "Timeout=%d s \n", cmd->timeout);

		count++;
		cmd->action = FPP_ACTION_QUERY_CONT;

		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RX_L2FLOW_ENTRY, cmd, sizeof(fpp_l2_bridge_flow_entry_cmd_t), rxbuf.rcvBuffer);
	} while (rcvBytes >= sizeof(fpp_l2_bridge_flow_entry_cmd_t) + sizeof(unsigned short));
	cmm_print(DEBUG_STDOUT, "Total Flow Entries: %d\n", count);

	return CLI_OK;
}
#endif
int cmmFeFFControl(FCI_CLIENT* fci_handler, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	fpp_ff_ctrl_cmd_t * cmd = (fpp_ff_ctrl_cmd_t *) cmd_buf;
	int ret = 0;

	res_buf[0] = CMMD_ERR_OK;
	*res_len = 2;

	if(cmd->enable)
	{
		if(!globalConf.ff_enable)
		{
			cmd->enable = 1;

			// Send message to forward engine
			cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_FF_CONTROL cmd len=%zu\n",sizeof(fpp_ff_ctrl_cmd_t));
			ret = fci_cmd(fci_handler, FPP_CMD_IPV4_FF_CONTROL, (unsigned short *) cmd, sizeof(fpp_ff_ctrl_cmd_t), res_buf, res_len);
			if (ret !=0 || res_buf[0] != FPP_ERR_OK)
			{
				if (ret != 0)
					cmm_print(DEBUG_ERROR, "Error '%s' while trying to enable fast-forward\n", strerror(errno));
				else
					cmm_print(DEBUG_ERROR, "Error %d while trying to enable fast-forward\n", res_buf[0]);
			}
			else
			{
				globalConf.ff_enable = 1;
				cmm_print(DEBUG_ERROR, "Fast-forward is enabled\n");
			}
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Fast-forward is already enabled\n");
		}
	}
	else
	{
		if(globalConf.ff_enable)
		{
			cmd->enable = 0;

			// Send message to forward engine
			cmm_print(DEBUG_COMMAND, "Send CMD_IPV4_FF_CONTROL cmd len=%zu\n",sizeof(fpp_ff_ctrl_cmd_t));
			ret = fci_cmd(fci_handler, FPP_CMD_IPV4_FF_CONTROL, (unsigned short *) cmd, sizeof(fpp_ff_ctrl_cmd_t), res_buf, res_len);
			if (ret !=0 || res_buf[0] != FPP_ERR_OK)
			{
				if (ret != 0)
					cmm_print(DEBUG_ERROR, "Error '%s' while trying to enable fast-forward\n", strerror(errno));
				else
					cmm_print(DEBUG_ERROR, "Error %d while trying to enable fast-forward\n", res_buf[0]);
			}
			else
			{
				globalConf.ff_enable = 0;
				cmm_print(DEBUG_ERROR, "Fast-forward is disabled\n");
			}
		}
		else
		{
			cmm_print(DEBUG_ERROR, "Fast-forward is already disabled\n");
		}
	}

	return ret;
}
/*****************************************************************
* cmmFeCatch
*
*
******************************************************************/
int cmmFeCatch(unsigned short fcode, unsigned short len, unsigned short *payload)
{
	switch (fcode)
	{
	case FPP_CMD_IPV4_CONNTRACK_CHANGE:
		cmmFeCtChange4(&globalConf.ct, (fpp_ct_cmd_t*)payload, len);
		break;

	case FPP_CMD_IPV6_CONNTRACK_CHANGE:
		cmmFeCtChange6(&globalConf.ct, (fpp_ct6_cmd_t*)payload, len);
		break;

	case FPP_CMD_IPSEC_SA_NOTIFY:
		cmm_print(DEBUG_COMMAND, "CMD_IPSEC_SA_NOTIFY\n");
		cmmIPSectoKeyEngine(globalConf.ct.fci_key_handle, fcode, len, payload);
		break;
#ifdef AUTO_BRIDGE
	case FPP_CMD_RX_L2FLOW_ENTRY:
		cmmFeL2FlowChange(&globalConf.ct, (fpp_l2_bridge_flow_entry_cmd_t*)payload, len);
		break;
#endif
	default:
		cmm_print(DEBUG_ERROR, "%s: Unknow command(%x) received\n", __func__, fcode);
		break;
	}

	return FCI_CB_CONTINUE;
}

