 /*
  *  module_tunnel.c: Tunnel module
  *
  *  Copyright (C) 2007 Mindspeed Technologies, Inc.
  *  Copyright 2014-2016 Freescale Semiconductor, Inc.
  *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
  */
#include <sys/ioctl.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <net/if_arp.h>

#include "cmm.h"
#include "fpp.h"
#include "cmmd.h"
#include "module_tunnel.h"
#include <linux/if_tun.h>


extern  void __itf_update_connection(FCI_CLIENT *fci_handle, int ifindex);

/************************************************************
 *
 * tunnel_print_usage
 * Role : Get tunnel info from kernel
 ************************************************************/
static int tunnel_print_usage()
{
	cmm_print(DEBUG_STDERR, 
			  "Usage: tunnel <name> add {gre6} [ipsec {0|1}]\n"
		  "       tunnel <name> del\n"
                  "       tunnel <name> show \n"
                  "\n"
                  "\n"
		  "       Ex:  set tunnel tnl0 add gre6 ipsec 1\n"
		  "            tunnel tnl0 del\n"
	          );
	return 0;
}

/************************************************************
 *
 * tunnel_print_info
 *
 ************************************************************/
static int tunnel_print_info(struct tunnel_info *pInfo)
{

	char remote[INET6_ADDRSTRLEN];
	char local[INET6_ADDRSTRLEN];
	char ifname[IFNAMSIZ];

	cmm_print(DEBUG_STDOUT, "Tunnel name        : %s\n", pInfo->ifname);
	cmm_print(DEBUG_STDOUT, "tunnel_ family        : %d\n", pInfo->tunnel_family);

	if (pInfo->tunnel_family == AF_INET6)
	{

		inet_ntop(AF_INET6, &pInfo->remote, remote, INET6_ADDRSTRLEN);
		inet_ntop(AF_INET6, &pInfo->local, local, INET6_ADDRSTRLEN);

		if( pInfo->tunnel_proto == IPPROTO_IPIP)
			cmm_print(DEBUG_STDOUT, "Protocol           : 4-o-6 (%d)\n", pInfo->tunnel_proto);
		else if (pInfo->tunnel_proto == IPPROTO_GRE)
			cmm_print(DEBUG_STDOUT, "Protocol           : GRE over IPv6 (%d)\n", pInfo->tunnel_proto);
		else
			cmm_print(DEBUG_STDOUT, "Protocol           : Unknown (%d)\n", pInfo->tunnel_proto);


	}
	else if (pInfo->tunnel_family == AF_INET)
	{

		inet_ntop(AF_INET, &pInfo->remote, remote, INET_ADDRSTRLEN);
		inet_ntop(AF_INET, &pInfo->local, local, INET_ADDRSTRLEN);

		if(pInfo->tunnel_proto == IPPROTO_IPV6)
			cmm_print(DEBUG_STDOUT, "Protocol           : 6-o-4 (%d)\n", pInfo->tunnel_proto);
		else
			cmm_print(DEBUG_STDOUT, "Protocol           : Unknown (%d)\n", pInfo->tunnel_proto);


	}

	cmm_print(DEBUG_STDOUT, "Local address      : %s\n", local);
	cmm_print(DEBUG_STDOUT, "Remote address     : %s\n", remote);
	cmm_print(DEBUG_STDOUT, "Output device      : %s\n", if_indextoname(pInfo->phys_ifindex, ifname));
	if(pInfo->ipsec)
		cmm_print(DEBUG_STDOUT, "Secure          : yes\n");
	else
		cmm_print(DEBUG_STDOUT, "Secure          : no\n");

	if (pInfo->itf_programmed)
		cmm_print(DEBUG_STDOUT, "Status             : running\n");
	else
	{
		cmm_print(DEBUG_STDOUT, "Status             : not complete\n");
		if (!pInfo->neigh_programmed)
			cmm_print(DEBUG_STDOUT, "                -> Waiting for neigh info\n");

		if((!pInfo->sa_programmed) && (pInfo->ipsec))
			cmm_print(DEBUG_STDOUT, "                -> Waiting for ipsec info\n");
	}


	return 0;
}

/************************************************************
 *
 * tunnel_parse_cmd
 *
 ************************************************************/
static int tunnel_parse_cmd(int argc, char ** keywords, daemon_handle_t daemon_handle)
{	
	char *tnl_name;
	char tnl_type;
	char ipsec = 0;
	cmmd_tunnel_t cmmtd_cmd; /* CMM to Deamon command */
       	char rcvBuffer[1024];
	union u_rxbuf1024 rxbuf;
	
	int rc;
	
	if (argc < 2)
		return tunnel_print_usage();
	
	memset(&cmmtd_cmd,0,sizeof cmmtd_cmd);
	tnl_name = *keywords++;

	if (strncmp(*keywords, "add", strlen(*keywords)) == 0)
	{	
		keywords++;	

		if((argc != 3) && (argc != 5))
			return tunnel_print_usage();

		if (strcmp(*keywords, "gre6") == 0)
			tnl_type = TNL_GRE_IPV6;
		else
		{
			return tunnel_print_usage();
		}

		if (argc == 5)
		{
			keywords++;

			if ((strncmp(*keywords, "ipsec", strlen(*keywords)) == 0))
			{
				keywords++;

				if ((strncmp(*keywords, "0", strlen(*keywords)) == 0)
				|| (strncmp(*keywords, "1", strlen(*keywords)) == 0))
				{
					ipsec = atoi(*keywords);
				}
				else
				{
					return tunnel_print_usage();
				}
			}
			else
			{
				return tunnel_print_usage();
			}
		}
		strncpy(cmmtd_cmd.name, tnl_name, sizeof(cmmtd_cmd.name));
		STR_TRUNC_END(cmmtd_cmd.name, sizeof(cmmtd_cmd.name));
		cmmtd_cmd.ipsec = ipsec;
		cmmtd_cmd.tunnel_type = tnl_type;

		/* Send CMD_CMMTD_TUNNEL_ADD to Deamon !*/
		rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_TUNNEL_ADD, &cmmtd_cmd, sizeof(cmmtd_cmd), &rxbuf.rcvBuffer);
		if (rc != 2) /* we expect 2 bytes in response */
		{
			if (rc >= 0)
				cmm_print(DEBUG_STDERR, "CMD_TUNNEL_ADD unexpected response length %d\n", rc);
			return -1;
		}
		else if (rxbuf.result != CMMD_ERR_OK) 
		{
			showErrorMsg("CMD_TUNNEL_ADD", ERRMSG_SOURCE_CMMD, rcvBuffer);
			return -1;
		}

		return 0;
	}
	else if(strncmp(*keywords, "show", strlen(*keywords)) == 0)
	{
		if(argc != 2)
			return tunnel_print_usage();
		else
		{
			struct tunnel_info *pInfo;

 			cmm_print(DEBUG_STDOUT, "Details for tunnel %s\n", tnl_name);
			strncpy(cmmtd_cmd.name, tnl_name, sizeof(cmmtd_cmd.name));
			STR_TRUNC_END(cmmtd_cmd.name, sizeof(cmmtd_cmd.name));

			/* Send CMD_CMMTD_TUNNEL_SHOW to Deamon !*/
			rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_TUNNEL_SHOW, &cmmtd_cmd, sizeof(cmmtd_cmd), rxbuf.rcvBuffer);
			if (rc != (sizeof(struct tunnel_info) + 4))
			{
				if(rc >= 0)
					cmm_print(DEBUG_STDERR, "ERROR: CMD_TUNNEL_SHOW Unexpected result returned from FPP rc:%04x - received %d - expected %zu\n",
						  (rc < sizeof(unsigned short) ) ? 0 : rxbuf.result,
						  rc,
 						  sizeof(struct tunnel_info) + 4
			  			  );
				return -1;
			}
			else if (rxbuf.result != CMMD_ERR_OK)
			{
				showErrorMsg("CMD_TUNNEL_SHOW", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
				return -1;
			}

 			pInfo = (struct tunnel_info *)(rxbuf.rcvBuffer + 4);
			tunnel_print_info(pInfo);
		}
	}
	else if(strncmp(*keywords, "del", strlen(*keywords)) == 0)
	{
		if(argc != 2)
			return tunnel_print_usage();
		else
		{
			strncpy(cmmtd_cmd.name, tnl_name, sizeof(cmmtd_cmd.name));
			STR_TRUNC_END(cmmtd_cmd.name, sizeof(cmmtd_cmd.name));

			/* Send CMD_CMMTD_TUNNEL_DEL to Deamon !*/
			rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_TUNNEL_DEL, &cmmtd_cmd, sizeof(cmmtd_cmd), &rxbuf.rcvBuffer);
			if (rc != 2)
			{
				if (rc >= 0)
					cmm_print(DEBUG_STDERR, "CMD_TUNNEL_DEL unexpected response length %d\n", rc);
				return -1;
			}
			else if (rxbuf.result != CMMD_ERR_OK)
			{
				showErrorMsg("CMD_TUNNEL_DEL", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
				return -1;
			}
		}
	}
	else
		return tunnel_print_usage();

	return 0;
}

/************************************************************
 *
 * cmm_tunnel_parse_cmd
 *
 ************************************************************/
int cmm_tunnel_parse_cmd(int argc, char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	if (tabStart < argc)
		return tunnel_parse_cmd(argc - tabStart, &keywords[tabStart], daemon_handle);
	else
		return tunnel_print_usage();
}

/************************************************************
 *
 * tunnel_send_cmd
 * Role: CMM to FPP commands in deamon context
 ************************************************************/
int tunnel_send_cmd(FCI_CLIENT *fci_handle, int request, struct interface *itf)
{
	cmmd_tunnel_create_cmd_t cmd;
	int action;
	int ret = CMMD_ERR_OK;

	switch (request)
	{
	case (ADD | UPDATE):
		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED)
			goto out;

		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == (FPP_PROGRAMMED | FPP_NEEDS_UPDATE))
			action = FPP_ACTION_UPDATE;
		else
			action = FPP_ACTION_REGISTER;

		break;

	case UPDATE:
		if (!((itf->flags & FPP_PROGRAMMED) && (itf->flags & FPP_NEEDS_UPDATE)))
			goto out;

		action = FPP_ACTION_UPDATE;
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: Command not supported\n", __func__);
		ret = CMMD_ERR_UNKNOWN_COMMAND;
		goto out;
		break;
	}

	memset(&cmd, 0, sizeof(cmd));

	if (itf->tunnel_family == AF_INET6)
	{
		if (itf->tunnel_parm6.proto == IPPROTO_IPIP)
			cmd.mode = TNL_4O6;
		else if (itf->tunnel_parm6.proto == IPPROTO_GRE)
			cmd.mode = TNL_GRE_IPV6;
		else
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel proto %d not supported\n", __func__,itf->tunnel_parm6.proto);
			ret = CMMD_ERR_UNKNOWN_COMMAND;
			goto out;
		}

		memcpy(cmd.local, itf->tunnel_parm6.laddr.s6_addr, 16);
		memcpy(cmd.remote, itf->tunnel_parm6.raddr.s6_addr, 16);

		if (itf->tunnel_parm6.flags & IP6_TNL_F_IGN_ENCAP_LIMIT)
			cmd.encap_limit = 0;
		else
			cmd.encap_limit = itf->tunnel_parm6.encap_limit;

		cmd.hop_limit = itf->tunnel_parm6.hop_limit;

		/* Flowinfo : flowclass / traffic class will need to be detailed */
		cmd.flow_info = itf->tunnel_parm6.flowinfo;

		if (itf->tunnel_parm6.flags & IP6_TNL_F_USE_ORIG_TCLASS) {
			cmd.tunnel_flags |= INHERIT_TC;
			cmm_print(DEBUG_INFO, "tc inherit set\n");
		}

		if (itf->tunnel_parm6.flags & IP6_TNL_F_RCV_DSCP_COPY) {
			cmd.tunnel_flags |= DSCP_COPY;
			cmm_print(DEBUG_INFO, "dscp inherit set\n");
		}

		if (itf->tunnel_flags & TNL_IPSEC)
			cmd.secure = 1;
		else
			cmd.secure = 0;

	}
	else
	{
		if (itf->tunnel_parm4.iph.protocol == IPPROTO_IPV6)
			cmd.mode = TNL_6O4;
		else
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel proto %d not supported\n", __func__,itf->tunnel_parm4.iph.protocol);
			ret = CMMD_ERR_UNKNOWN_COMMAND;
			goto out;
		}

		if(! itf->tunnel_parm4.iph.saddr)
		{
			if(itf->rt.route)
				memcpy(cmd.local, &itf->rt.route->sAddr[0], 4);
		}
		else
			memcpy(cmd.local, &itf->tunnel_parm4.iph.saddr, 4);

		memcpy(cmd.remote, &itf->tunnel_parm4.iph.daddr, 4);

		cmd.hop_limit = itf->tunnel_parm4.iph.ttl;
		cmd.flow_info = itf->tunnel_parm4.iph.tos;
		cmd.frag_off = itf->tunnel_parm4.iph.frag_off;

		/* For now not supported */
		if (itf->tunnel_flags & TNL_IPSEC)
			cmd.secure = 1;
		else
			cmd.secure = 0;
	}

	cmd.route_id = itf->rt.fpp_route_id;

	cmd.enabled = itf->tunnel_enabled;

	cmd.mtu	= itf->mtu;

	if (____itf_get_name(itf, cmd.name, sizeof(cmd.name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		ret = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto out;
	}

#if 0
	if (__itf_get_name(itf->phys_ifindex, cmd.output_device, sizeof(cmd.output_device)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, itf->phys_ifindex);
		goto err;
	}
#endif

	if (action == FPP_ACTION_REGISTER)
	{
		//Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_TUNNEL_ADD\n");

		ret = fci_write(fci_handle, FPP_CMD_TUNNEL_ADD, sizeof(fpp_tunnel_create_cmd_t), (unsigned short *)&cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_TNL_ALREADY_CREATED))
		{
			itf->flags |= FPP_PROGRAMMED;
			itf->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_TUNNEL_ADD\n", __func__, ret);
			goto out;
		}
	}
	else
	{
		//Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_TUNNEL_UPDATE\n");

		ret = fci_write(fci_handle, FPP_CMD_TUNNEL_UPDATE, sizeof(fpp_tunnel_create_cmd_t), (unsigned short *)&cmd);
		if (ret == FPP_ERR_OK)
		{
			itf->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_TUNNEL_UPDATE\n", __func__, ret);
			goto out;
		}
	}

out:
	return ret;
}


static int tunnel_send_del(FCI_CLIENT *fci_handle, struct interface *itf)
{
	fpp_tunnel_del_cmd_t cmd;
	int ret = 0;

	if (!(itf->flags & FPP_PROGRAMMED))
		return CMMD_ERR_OK;

	memset(&cmd, 0, sizeof(cmd));

	if (____itf_get_name(itf, cmd.name, sizeof(cmd.name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		return CMMD_ERR_WRONG_COMMAND_PARAM;
	}

	//Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send CMD_TUNNEL_DEL\n");

	ret = fci_write(fci_handle, FPP_CMD_TUNNEL_DEL, sizeof(fpp_tunnel_del_cmd_t), (unsigned short *) &cmd);
	if (ret == FPP_ERR_TNL_ENTRY_NOT_FOUND || ret == FPP_ERR_OK)
		itf->flags &= ~FPP_PROGRAMMED;
	else
	{
		if(ret > 0)
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_TUNNEL_DEL\n", __func__, ret);
		else
			cmm_print(DEBUG_ERROR, "%s: Error '%s' while sending CMD_TUNNEL_DEL\n", __func__, strerror(errno));
	}

	return ret;
}

/************************************************************
 *
 * __tunnel_remove_flow
 *
 ************************************************************/
#ifdef IPSEC_FLOW_CACHE
void __tunnel_remove_flow(FCI_CLIENT *fci_key_handle, struct interface *itf)
{
	if (itf->flow_orig)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, itf->flow_orig))
		{
			__cmmFlowPut(itf->flow_orig);

			itf->flow_orig = NULL;
		}

	if (itf->flow_rep)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, itf->flow_rep))
		{
			__cmmFlowPut(itf->flow_rep);

			itf->flow_rep = NULL;
		}
}
#else
void __tunnel_remove_flow(FCI_CLIENT *fci_key_handle, struct interface *itf)
{
		/* TODO  will be taken when supporting IPSEC for local in packets*/
}

#endif /* IPSEC_FLOW_CACHE */


/************************************************************
 *
 * tunnel_update_sa
 * Role : Update FPP tunnel SA and local cmm copy
 ************************************************************/
static int tunnel_update_sa(FCI_CLIENT *fci_handle, struct interface *itf, unsigned char orig)
{
	fpp_tunnel_sec_cmd_t cmd;
	int ret;

	if (!(itf->flags & FPP_PROGRAMMED))
		goto out;

	memset(&cmd, 0, sizeof(cmd));

	if (____itf_get_name(itf, cmd.name, sizeof(cmd.name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		goto err;
	}

#ifdef IPSEC_FLOW_CACHE
	if (orig)
	{
		cmd.sa_nr = itf->flow_orig->sa_nr;
		memcpy(cmd.sa_handle, itf->flow_orig->sa_handle, itf->flow_orig->sa_nr * sizeof(unsigned short));
	}
	else
	{
		cmd.sa_reply_nr = itf->flow_rep->sa_nr;
		memcpy(cmd.sa_reply_handle, itf->flow_rep->sa_handle, itf->flow_rep->sa_nr * sizeof(unsigned short));
	}
#else
	/* TODO  will be taken when supporting IPSEC for local in packets*/
#endif /* IPSEC_FLOW_CACHE */

	//Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send CMD_TUNNEL_SEC\n");

	if ((ret = fci_write(fci_handle, FPP_CMD_TUNNEL_SEC, sizeof(fpp_tunnel_sec_cmd_t), (unsigned short *) &cmd)))
	{
		cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_TUNNEL_SEC\n", __func__, ret);
		goto err;
	}

out:
	return 0;

err:
	return -1;
}


/************************************************************
 *
 * __tunnel_add
 * 
 ************************************************************/
int __tunnel_add(FCI_CLIENT *fci_handle, struct interface *itf)
{
	unsigned int *sAddr, *dAddr;
	unsigned char proto;
	int enabled = itf->tunnel_enabled;
	int rc = CMMD_ERR_NOT_CONFIGURED;

	cmm_print(DEBUG_INFO, "%s: tunnel %s\n", __func__, itf->ifname);
	if (!__itf_is_up(itf))
		goto err;

	if (!(itf->flags & USER_ADDED))
		goto err;

	if (itf->tunnel_family == AF_INET6)
	{
		dAddr = itf->tunnel_parm6.raddr.s6_addr32;
		proto = itf->tunnel_parm6.proto;

		sAddr = itf->tunnel_parm6.laddr.s6_addr32;
	}
	else
	{
		sAddr = &itf->tunnel_parm4.iph.saddr;
		dAddr = &itf->tunnel_parm4.iph.daddr;
		proto = itf->tunnel_parm4.iph.protocol;
	}

	if (((itf->type != ARPHRD_SIT) && (itf->tunnel_parm6.proto != IPPROTO_IPIP)) || dAddr[0])
	{
		struct flow flow;

		if (itf->tunnel_flags & TNL_IPSEC)
		{
#ifdef IPSEC_FLOW_CACHE
/* 		If TNL_IPSEC flag is enabled and flows are null, then we need to update PFE with the  new flows,
 *		and update the tunnel in PFE, with secure flag enabled */
			if (!itf->flow_orig)
			{
				itf->flow_orig = __cmmFlowGet(itf->tunnel_family, sAddr, dAddr, 0, 0, proto, FLOW_DIR_OUT);
				itf->flags |= FPP_NEEDS_UPDATE;
			}

			if (!itf->flow_rep)
			{
				itf->flow_rep = __cmmFlowGet(itf->tunnel_family, dAddr, sAddr, 0, 0, proto, FLOW_DIR_IN);
				itf->flags |= FPP_NEEDS_UPDATE;
			}
#else
			/* TODO  will be taken when supporting IPSEC for local in packets*/
#endif /* IPSEC_FLOW_CACHE */
		}
		else
		{
#ifdef IPSEC_FLOW_CACHE
/* 		If TNL_IPSEC flag is disabled and flows are not null, then we need to update PFE with the secure flag disabled
 *		This will in turn reset the secure flows in PFE), and remove the flows from the ITF (done later in tunnel_add)
 */
			if(itf->flow_orig || itf->flow_rep )
				itf->flags |= FPP_NEEDS_UPDATE;
#else
					/* TODO  will be taken when supporting IPSEC for local in packets*/
#endif /* IPSEC_FLOW_CACHE */
		}

		flow.family = itf->tunnel_family;
		flow.sAddr = sAddr;
		flow.dAddr = dAddr;
		flow.fwmark = 0;
		flow.iifindex = itf->ifindex;
		flow.proto = proto;
		flow.flow_flags = FLOWFLAG_LOCAL;

		rc = __cmmRouteRegister(&itf->rt, &flow, "tunnel");

		if (itf->rt.route)
			itf->phys_ifindex = itf->rt.route->oifindex;

		if (rc < 0)
		{
			enabled = 0;
			goto program;
		}

		enabled = 1;

		cmmFeRouteUpdate(fci_handle, ADD | UPDATE, itf->rt.fpp_route);
	}
	else
		enabled = 1;

program:
	if (itf->tunnel_enabled != enabled)
	{
		itf->flags |= FPP_NEEDS_UPDATE;
		itf->tunnel_enabled = enabled;
	}

	__cmmCheckFPPRouteIdUpdate(&itf->rt, &itf->flags);

	rc = tunnel_send_cmd(fci_handle, ADD | UPDATE, itf);
	cmm_print(DEBUG_INFO, "%s: tunnel_send_cmd returned %d\n", __func__, rc);

	if (rc != CMMD_ERR_OK)
		goto err;

	if (itf->tunnel_flags & TNL_IPSEC)
	{
		if (itf->flow_orig)
			tunnel_update_sa(fci_handle, itf, 1);

		if (itf->flow_rep)
			tunnel_update_sa(fci_handle, itf, 0);
	}
err:
	return rc;
}


/************************************************************
 *
 * tunnel_add
 *
 ************************************************************/
static int tunnel_add(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, char *name, unsigned char ipsec, char tnl_type, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ifindex;
	struct interface *itf;
	int rc = 0;
	int update_connections = 0, update_tnl_flows = 0;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	

	ifindex = if_nametoindex(name);

	itf = __itf_get(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s: interface %s not found\n", __func__, name);
		res_buf[0] = CMMD_ERR_NOT_FOUND;
		goto err0;
	}

	if (!__itf_is_tunnel(itf))
	{
		cmm_print(DEBUG_ERROR, "%s: interface %s is not a tunnel\n", __func__, name);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err1;
	}

	switch (tnl_type)
	{
	case TNL_GRE_IPV6:
		if (itf->tunnel_family != AF_INET6)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have family %d\n", __func__, tnl_type, itf->tunnel_family);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

		if (itf->tunnel_parm6.proto != IPPROTO_GRE)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have proto %d\n", __func__, tnl_type, itf->tunnel_parm6.proto);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

		if (ipsec)
		{
			itf->tunnel_flags |= TNL_IPSEC;
		}
		else
		{
			if(itf->tunnel_flags & TNL_IPSEC)
				itf->flags |= FPP_NEEDS_UPDATE;
			itf->tunnel_flags &= ~TNL_IPSEC;
			update_tnl_flows = 1;
		}
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported tunnel type %x\n", __func__, tnl_type);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err1;
	}
	if(itf->phys_ifindex)// Bound to an interface
	{
		if(!__itf_is_programmed(itf->phys_ifindex))
		{
			cmm_print(DEBUG_ERROR, "%s: Fast forward tunneling only supported on offloaded interface\n", __func__);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}
	}

	itf->flags |= USER_ADDED;

	rc  = __tunnel_add(fci_handle, itf);
	if (rc >= 0)
	{
		res_buf[0] = rc;
		rc = 0;
	}

	if(update_tnl_flows)
		__tunnel_remove_flow(fci_key_handle, itf);

        if(update_connections)
               __itf_update_connection(fci_handle, itf->ifindex);

err1:
	__itf_put(itf);

err0:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	*res_len = 2;
	return rc;
}


/************************************************************
 *
 * __tunnel_del
 *
 ************************************************************/
int __tunnel_del(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct interface *itf)
{
	int rc = tunnel_send_del(fci_handle, itf);


	__cmmRouteDeregister(fci_handle, &itf->rt, "tunnel");

	__tunnel_remove_flow(fci_key_handle, itf);

	return rc;
}


/************************************************************
 *
 * tunnel_del
 *
 ************************************************************/
static int tunnel_del(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, char *name, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ifindex;
	struct interface *itf;
	int rc = 0;

	*res_len = 2;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	

	ifindex = if_nametoindex(name);

	itf = __itf_find(ifindex);
	if (!itf)
	{
		res_buf[0] = CMMD_ERR_NOT_FOUND;
		goto err;
	}

	if (!__itf_is_tunnel(itf))
	{
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err;
	}

	rc = __tunnel_del(fci_handle, fci_key_handle, itf);
	if (rc >= 0)
	{
		res_buf[0] = rc;
		rc = 0;
	}
err:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return rc;
}

/************************************************************
 *
 * __tunnel_update
 *
 ************************************************************/
int __tunnel_update(FCI_CLIENT *fci_handle, struct interface *itf)
{
	int rc = 0;
	itf->flags |= FPP_NEEDS_UPDATE;
	itf->tunnel_enabled = 0;
	rc = tunnel_send_cmd(fci_handle, UPDATE, itf);
	return rc;
}


/************************************************************
 *
 * tunnel_show
 *
 ************************************************************/
static int tunnel_show(FCI_CLIENT *fci_handle, char *name, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ifindex;
	struct interface *itf;
	struct tunnel_info *pInfo;

	__pthread_mutex_lock(&itf_table.lock);

	ifindex = if_nametoindex(name);

	itf = __itf_find(ifindex);
	if (!itf)
	{
		res_buf[0] = CMMD_ERR_NOT_FOUND;
		*res_len = 2;
		goto err;
	}

	if (!__itf_is_tunnel(itf))
	{
		res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
		*res_len = 2;
		goto err;
	}

	/* +4 is for making the structure 4-byte aligned in memory
	 * to boost access performance. It's not +0 because we need to put response code
	 * in there.
	 * TODO: this should be refactored (a response structure should be introduced)
	 */
	if (sizeof(struct tunnel_info) < *res_len)
	{
		res_buf[0] = CMMD_ERR_OK;
		pInfo = (struct tunnel_info*)((uint8_t *)res_buf + 4);	
		pInfo->tunnel_family = itf->tunnel_family;
		{
			size_t len = strlen(itf->ifname);
			if (len >= IFNAMSIZ)
				len = IFNAMSIZ - 1;
			memcpy(pInfo->ifname, itf->ifname, len);
			pInfo->ifname[len] = '\0';
		}
		pInfo->phys_ifindex = itf->phys_ifindex;
		pInfo->ipsec = (itf->tunnel_flags & TNL_IPSEC);
		pInfo->itf_programmed = (itf->flags & FPP_PROGRAMMED) ? 1 : 0;
		pInfo->neigh_programmed = (itf->rt.route)? 1 : 0; 
		pInfo->sa_programmed = (itf->flow_rep && itf->flow_orig);
		if(itf->tunnel_family == AF_INET6)
		{
			memcpy(&pInfo->remote, &itf->tunnel_parm6.raddr.s6_addr, 16);
			memcpy(&pInfo->local, &itf->tunnel_parm6.laddr.s6_addr, 16);
			pInfo->tunnel_proto = itf->tunnel_parm6.proto;
		}
		else
		{
			memcpy(&pInfo->remote, &itf->tunnel_parm4.iph.daddr, 4);
			memcpy(&pInfo->local, &itf->tunnel_parm4.iph.saddr, 4);
			pInfo->tunnel_proto = itf->tunnel_parm4.iph.protocol;
		}
		
		*res_len = sizeof(struct tunnel_info) + 4;
	}


err:
	__pthread_mutex_unlock(&itf_table.lock);
	return 0;
}

/************************************************************
 * 
 * tunnel_daemon_msg_recv
 * Role: Parse CMM to deamon messages
 ************************************************************/
int tunnel_daemon_msg_recv(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_tunnel_t *tnl = (cmmd_tunnel_t *) cmd_buf;

	switch (function_code)
	{
	case CMMD_CMD_TUNNEL_ADD:
		return tunnel_add(fci_handle, fci_key_handle, tnl->name, tnl->ipsec, tnl->tunnel_type, res_buf, res_len);

	case CMMD_CMD_TUNNEL_DEL:
		return tunnel_del(fci_handle, fci_key_handle, tnl->name, res_buf, res_len);

	case CMMD_CMD_TUNNEL_SHOW:
		return tunnel_show(fci_handle, tnl->name, res_buf, res_len);

	default:
		res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
		*res_len = 2;
	}

	return 0;
}

unsigned int tunnel_get_ipv4_dst(struct RtEntry *route, struct interface *itf)
{
	unsigned int dst = 0;

	/* Check for 6to4 address (2002::/16) - first 16 bits of the IPv6
	 * address are in the high half of dAddr[0] (network byte order). */
	if ((route->dAddr[0] & htonl(0xFFFF0000)) == htonl(0x20020000)) {
		/* 6to4 v6 addr: 16-bit prefix | 32-bit v4addr | 16-bit SLA | ...
		 * The embedded IPv4 address spans the low 16 bits of dAddr[0]
		 * and the high 16 bits of dAddr[1]. */
		memcpy(&dst, (char *)route->dAddr + 2, 4);
	}

	if (!dst)
	{
		/* ipv6 addr compatible v4 */
		if ((route->gwAddr[0] == 0) && (route->gwAddr[1] == 0) && (route->gwAddr[2] == 0) &&
			route->gwAddr[3] && (route->gwAddr[3] != htonl(0x00000001)))
			dst = route->gwAddr[3];
	}

	return dst;
}

/************************************************************
 *
 * __cmmGetTunnel
 * Role : Check if interface is a tunnel and retrieve info from kernel
 ************************************************************/
int __cmmGetTunnel(int fd, struct interface *itf, struct rtattr *tb[])
{
	struct ifreq ifr;
	int rc;

	itf->itf_flags &= ~ITF_TUNNEL;

	if (__cmmGetTunnel_gre6(fd, itf, tb))
		goto out;

	memset(&itf->tunnel_parm6, 0, sizeof(struct ip6_tnl_parm));
	memset(&itf->tunnel_parm4, 0, sizeof(struct ip_tunnel_parm));
	strcpy(ifr.ifr_name, itf->ifname);

	switch (itf->type)
	{
	case ARPHRD_ETHER:

		if (itf->phys_ifindex == itf->ifindex)
			goto out;

		if (!__itf_is_pointopoint(itf))
			goto out;
		/* tunnel_parm4 and tunnel_parm6 start at the same address, so tunnel_parm4 can be used
		 to avoid some code duplication
		*/
		ifr.ifr_ifru.ifru_data = (void *)&itf->tunnel_parm4; //tunnel_parm4 and tunnel_parm6 start at the same address, so tunnel_parm4

		rc = ioctl(fd, SIOCGETTUNNEL, &ifr);
		if (rc < 0)
			goto out;
 /*

   struct ip6_tnl_parm {			struct ip_tunnel_parm {
           char name[IFNAMSIZ];				char   name[IFNAMSIZ];
           int link;					int    link;
           __u8 proto;					__be16 i_flags;
           __u8 encap_limit;				__be16 o_flags;
           __u8 hop_limit;				__be32 i_key;
           __be32 flowinfo;				__be32 o_key;
           __u32 flags;					struct iphdr iph;
           struct in6_addr laddr;		};
           struct in6_addr raddr;
   };
 */
		/* The ip6_tnl_parm and ip_tunnel_parm converge upto link, so it is safe to
                   just check this once */
		/* In case this is not a real tunnel interface these should not match */
		if (itf->phys_ifindex != itf->tunnel_parm4.link)
			goto out;

		break;

	case ARPHRD_TUNNEL:
		cmm_print(DEBUG_ERROR, "%s: itf(%d) unsupported tunnel type (%x)\n", __func__, itf->ifindex, itf->type);
		goto out;


	case ARPHRD_TUNNEL6:
		cmm_print(DEBUG_INFO, "%s: itf(%d) supported tunnel type (%x)\n", __func__, itf->ifindex, itf->type);

/*		 if (!__itf_is_pointopoint(itf))
		 {
			cmm_print(DEBUG_ERROR, "%s: itf(%d) is not point to point and tunnel's remote address is not configured\n", __func__, itf->ifindex);
                        goto out;
		 }*/

                ifr.ifr_ifru.ifru_data = (void *)&itf->tunnel_parm6;

                rc = ioctl(fd, SIOCGETTUNNEL, &ifr);
                if (rc < 0)
                        goto out;

		cmm_print(DEBUG_INFO, "%s: itf(%d) tunnel flag is set (%x)\n", __func__, itf->ifindex, itf->flags);
		itf->itf_flags |= ITF_TUNNEL;
		itf->tunnel_family = AF_INET6;

		/* Add this type of tunnel automatically */
		itf->flags |= USER_ADDED;
		break;

	case ARPHRD_SIT:
		ifr.ifr_ifru.ifru_data = (void *)&itf->tunnel_parm4;

		rc = ioctl(fd, SIOCGETTUNNEL, &ifr);
		if (rc < 0)
			goto out;

		itf->itf_flags |= ITF_TUNNEL;
		itf->tunnel_family = AF_INET;

		/* Add this type of tunnel automatically */
		itf->flags |= USER_ADDED;

		break;

	case ARPHRD_IPGRE:
		cmm_print(DEBUG_ERROR, "%s: itf(%d) unsupported tunnel type (%x)\n", __func__, itf->ifindex, itf->type);
		goto out;


#ifndef ARPHRD_NONE
#define ARPHRD_NONE    0xFFFE
#endif
	case ARPHRD_NONE: /* As is the case for tun/tap interfaces */
		cmm_print(DEBUG_ERROR, "%s: itf(%d) supported tunnel type (%x)\n", __func__, itf->ifindex, itf->type);
		/* if 4rd support for SAM interfaces is not enabled ,
		 * just return
		 */
		if (!globalConf.enable_sam_itfs)
			goto out;
                ifr.ifr_ifru.ifru_data = (void *)&itf->tunnel_parm6;

                 rc = ioctl(fd, SIOCGIFFLAGS, &ifr);
                cmm_print(DEBUG_ERROR, "%s: itf(%d) rc is %d \n", __func__, itf->ifindex, rc);
                 if (rc < 0)
                        goto out;
                if(!(ifr.ifr_flags & IFF_TUN))
               {
                       cmm_print(DEBUG_ERROR, "%s: itf(%d) is not a TUN interface \n", __func__, itf->ifindex);
                       goto out;
               }

                itf->itf_flags |= ITF_TUNNEL;
                itf->tunnel_family = globalConf.tun_family;
                itf->tunnel_parm6.proto = globalConf.tun_proto;
		cmm_print(DEBUG_INFO,"%s: tun family is %d,tun proto is %d\n",__func__, globalConf.tun_family,globalConf.tun_proto);
                /* Add this type of tunnel automatically */
                itf->flags |= USER_ADDED;
                break;


	default:
		break;
	}

out:
	return 0;
}


/************************************************************
 *
 * __cmmGetTunnel_gre6
 *
 ************************************************************/

// NOTE: The following definitions must match the corresponding definitions in
//	the linux kernel file include/linux/if_tunnel.h.

enum {
	CMM_IFLA_GRE_UNSPEC,
	CMM_IFLA_GRE_LINK,
	CMM_IFLA_GRE_IFLAGS,
	CMM_IFLA_GRE_OFLAGS,
	CMM_IFLA_GRE_IKEY,
	CMM_IFLA_GRE_OKEY,
	CMM_IFLA_GRE_LOCAL,
	CMM_IFLA_GRE_REMOTE,
	CMM_IFLA_GRE_TTL,
	CMM_IFLA_GRE_TOS,
	CMM_IFLA_GRE_PMTUDISC,
	CMM_IFLA_GRE_ENCAP_LIMIT,
	CMM_IFLA_GRE_FLOWINFO,
	CMM_IFLA_GRE_FLAGS,
	__CMM_IFLA_GRE_MAX,
};
#define CMM_IFLA_GRE_MAX	(__CMM_IFLA_GRE_MAX - 1)

int __cmmGetTunnel_gre6(int fd, struct interface *itf, struct rtattr *tb[])
{
	struct rtattr *linkinfo[IFLA_INFO_MAX + 1];
	struct rtattr *greinfo[CMM_IFLA_GRE_MAX + 1];
	char local_buf[INET6_ADDRSTRLEN];
	char remote_buf[INET6_ADDRSTRLEN];

	if (strcmp(itf->link_kind, LINK_KIND_GRE6) != 0)
		return 0;

	itf->tunnel_parm6.proto = IPPROTO_GRE;
	itf->itf_flags |= ITF_TUNNEL;
	itf->tunnel_family = AF_INET6;
	if (!tb[IFLA_LINKINFO])
		goto gre6_error;
	cmm_parse_rtattr(linkinfo, IFLA_INFO_MAX, RTA_DATA(tb[IFLA_LINKINFO]), RTA_PAYLOAD(tb[IFLA_LINKINFO]));
	if (!linkinfo[IFLA_INFO_DATA])
		goto gre6_error;
	cmm_parse_rtattr(greinfo, CMM_IFLA_GRE_MAX, RTA_DATA(linkinfo[IFLA_INFO_DATA]), RTA_PAYLOAD(linkinfo[IFLA_INFO_DATA]));
	if (!greinfo[CMM_IFLA_GRE_LOCAL] || !greinfo[CMM_IFLA_GRE_REMOTE] || !greinfo[CMM_IFLA_GRE_LINK])
		goto gre6_error;
	strcpy(itf->tunnel_parm6.name, itf->ifname);
	memcpy(&itf->tunnel_parm6.laddr, RTA_DATA(greinfo[CMM_IFLA_GRE_LOCAL]), sizeof(itf->tunnel_parm6.laddr));
	memcpy(&itf->tunnel_parm6.raddr, RTA_DATA(greinfo[CMM_IFLA_GRE_REMOTE]), sizeof(itf->tunnel_parm6.raddr));
	itf->tunnel_parm6.link = *(__u32 *)RTA_DATA(greinfo[CMM_IFLA_GRE_LINK]);
	if (greinfo[CMM_IFLA_GRE_TTL])
		itf->tunnel_parm6.hop_limit = *(__u8 *)RTA_DATA(greinfo[CMM_IFLA_GRE_TTL]);
	if (greinfo[CMM_IFLA_GRE_ENCAP_LIMIT])
		itf->tunnel_parm6.encap_limit = *(__u8 *)RTA_DATA(greinfo[CMM_IFLA_GRE_ENCAP_LIMIT]);
	if (greinfo[CMM_IFLA_GRE_FLOWINFO])
		itf->tunnel_parm6.flowinfo = *(__u32 *)RTA_DATA(greinfo[CMM_IFLA_GRE_FLOWINFO]);
	if (greinfo[CMM_IFLA_GRE_FLAGS])
		itf->tunnel_parm6.flags = *(__u32 *)RTA_DATA(greinfo[CMM_IFLA_GRE_FLAGS]);
	itf->flags |= USER_ADDED;
	cmm_print(DEBUG_INFO, "%s: GRE tunnel, index=%d, name=%s, proto=%d, link=%d, local=%s, remote=%s\n",
				__func__, itf->ifindex, itf->ifname, itf->tunnel_parm6.proto, itf->tunnel_parm6.link,
				inet_ntop(AF_INET6, &itf->tunnel_parm6.laddr, local_buf, sizeof(local_buf)),
				inet_ntop(AF_INET6, &itf->tunnel_parm6.raddr, remote_buf, sizeof(remote_buf)));

gre6_error:
	return 1;
}

/************************************************************
 *
 * __cmmTunnelUpdateWithRoute
 *
 ************************************************************/
void __cmmTunnelUpdateWithRoute(FCI_CLIENT *fci_handle, struct RtEntry *route)
{
	struct interface *itf;
	struct list_head *entry;
	struct fpp_rt *fpp_route;
	int i;

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_tunnel(itf))
				continue;

			if (itf->rt.route == route)
			{
				fpp_route = itf->rt.fpp_route;
				itf->rt.fpp_route = NULL;

				__tunnel_add(fci_handle, itf);

				__cmmFPPRouteDeregister(fci_handle, fpp_route, "tunnel");
			}
		}
	}
}

/************************************************************
 *
 * __cmmTunnelFindFromFlow
 * Role : Finds tunnel entry that matches flow
 ************************************************************/
struct interface *__cmmTunnelFindFromFlow(int family, unsigned int *saddr, unsigned int *daddr, unsigned char proto, char *orig)
{
	struct interface *itf;
	struct list_head *entry;
	int i;

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_tunnel(itf))
				continue;

			if (itf->tunnel_family != family)
				continue;

			if (!(itf->tunnel_flags & TNL_IPSEC))
				continue;

			if (family == AF_INET6)
			{
				if (!memcmp(saddr, itf->tunnel_parm6.laddr.s6_addr, 16)
				    && !memcmp(daddr, itf->tunnel_parm6.raddr.s6_addr, 16)
				    && (proto == itf->tunnel_parm6.proto))
				{
					*orig = 1;
					goto found;
				}

				if (!memcmp(daddr, itf->tunnel_parm6.laddr.s6_addr, 16)
				    && !memcmp(saddr, itf->tunnel_parm6.raddr.s6_addr, 16)
				    && (proto == itf->tunnel_parm6.proto))
				{
					*orig = 0;
					goto found;
				}
			}
			else
			{
				if((saddr[0] == itf->tunnel_parm4.iph.saddr) &&
					(daddr[0] == itf->tunnel_parm4.iph.daddr) &&
					(proto == itf->tunnel_parm4.iph.protocol))
				{
					*orig = 1;
					goto found;
				}

				if((daddr[0] == itf->tunnel_parm4.iph.saddr) &&
					(saddr[0] == itf->tunnel_parm4.iph.daddr) &&
					(proto == itf->tunnel_parm4.iph.protocol))
				{
					*orig = 0;
					goto found;
				}

			}
		}
	}

	itf = NULL;

found:
	return itf;
}


int cmmTnlQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        short rc;
        int count = 0;
	int family = AF_INET6;
        char local[INET6_ADDRSTRLEN], remote[INET6_ADDRSTRLEN];
        cmmd_tunnel_query_cmd_t* pTnlCmd = (cmmd_tunnel_query_cmd_t*) rxbuf.rcvBuffer;

        rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_TUNNEL_QUERY, pTnlCmd,
                                  sizeof(cmmd_tunnel_query_cmd_t) , rxbuf.rcvBuffer);

        if (rcvBytes != sizeof(cmmd_tunnel_query_cmd_t) ) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == FPP_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR,
                         "ERROR: FPP Tunnel does not support ACTION_QUERY\n");
                } else if (rc == FPP_ERR_TNL_ENTRY_NOT_FOUND) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP Tunnel table empty\n");
                } else {
                    cmm_print(DEBUG_STDERR,
                            "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }

            cmm_print(DEBUG_STDOUT, "Tunnel interfaces:\n");
            do {
			char *mode;
                        if (pTnlCmd->mode == TNL_4O6)
				mode = "4o6";
                        else if (pTnlCmd->mode == TNL_6O4)
			{
				mode = "6o4";
				family = AF_INET;
			}
                        else if (pTnlCmd->mode == TNL_GRE_IPV6)
				mode = "GRE_IPV6";
			else
				mode = "Unknown";

			cmm_print(DEBUG_STDOUT, "%d: mode=%s, name=%s, local=%s, remote=%s, enabled=%d, secure=%d, flow_info=0x%x, encap_limit=%d, hop_limit=0x%x, mtu=%d\n",
				count, mode, pTnlCmd->name,
				inet_ntop(family , &pTnlCmd->local , local, (family == AF_INET6)? INET6_ADDRSTRLEN: INET_ADDRSTRLEN),
				inet_ntop(family , &pTnlCmd->remote , remote, (family == AF_INET6)? INET6_ADDRSTRLEN: INET_ADDRSTRLEN),
				pTnlCmd->enabled, pTnlCmd->secure, ntohl(pTnlCmd->flow_info),
				pTnlCmd->encap_limit, pTnlCmd->hop_limit, pTnlCmd->mtu);
                        count++;
                        rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_TUNNEL_QUERY_CONT, pTnlCmd, sizeof(cmmd_tunnel_query_cmd_t) , rxbuf.rcvBuffer);
           }while (rcvBytes == sizeof(cmmd_tunnel_query_cmd_t) );
           cmm_print(DEBUG_STDOUT, "Total Tunnel Entries:%d\n", count);

        return CLI_OK;
}
