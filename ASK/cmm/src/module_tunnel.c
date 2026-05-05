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

#ifdef SAM_LEGACY
extern int rt_mw_sam_make_dst_ipv6( struct in_addr *sam_dst_ipv4_addr, ushort sam_port, struct in6_addr *sam_dst_ipv6_addr );
extern int rt_mw_sam_get_ipv6( struct in6_addr *sam_ipv6_addr );
extern int rt_mw_sam_get_portsetid( rt_mw_ipstack_sam_port_t *sam_psid );
unsigned short TunMtu = DEFAULT_SAM_FRAG_MTU;
#endif

extern  void __itf_update_connection(FCI_CLIENT *fci_handle, int ifindex);

/************************************************************
 *
 * tunnel_print_usage
 * Role : Get tunnel info from kernel
 ************************************************************/
static int tunnel_print_usage()
{
	cmm_print(DEBUG_STDERR, 
			  "Usage: tunnel <name> add {ethipoip6 | ethip | gre6} [ipsec {0|1}]\n"
		  "       tunnel <name> del\n"
                  "       tunnel <name> show \n"
                  "\n"
                  "\n"
		  "       Ex:  set tunnel tnl0 add ethipoip6 ipsec 1\n"
		  "            set tunnel tnl0 add ethip ipsec 1\n"
		  "            tunnel tnl0 del\n"
	          );
	#ifdef SAM_LEGACY
		cmm_print(DEBUG_STDERR,
		  "       tunnel <name> set <sam_enable/sam_disable> [sam-frag-mtu <mtu>] \n");
	#endif
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
	char prefix[INET6_ADDRSTRLEN];
	char relayprefix[INET_ADDRSTRLEN];

	cmm_print(DEBUG_STDOUT, "Tunnel name        : %s\n", pInfo->ifname);
	cmm_print(DEBUG_STDOUT, "tunnel_ family        : %d\n", pInfo->tunnel_family);

	if (pInfo->tunnel_family == AF_INET6)
	{

		inet_ntop(AF_INET6, &pInfo->remote, remote, INET6_ADDRSTRLEN);
		inet_ntop(AF_INET6, &pInfo->local, local, INET6_ADDRSTRLEN);

		if(pInfo->tunnel_proto == IPPROTO_ETHERIP)
			cmm_print(DEBUG_STDOUT, "Protocol           : Etherip-o-ipv6 (%d)\n", pInfo->tunnel_proto);
		else if( pInfo->tunnel_proto == IPPROTO_IPIP)
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

		if(pInfo->tunnel_proto == IPPROTO_ETHERIP)
			cmm_print(DEBUG_STDOUT, "Protocol           : Etherip-o-ipv4 (%d)\n", pInfo->tunnel_proto);
		else if(pInfo->tunnel_proto == IPPROTO_IPV6)
		{
			cmm_print(DEBUG_STDOUT, "Protocol           : 6-o-4 (%d)\n", pInfo->tunnel_proto);
			if (pInfo->conf_6rd)
			{
				cmm_print(DEBUG_STDOUT, "6rd-prefix         : %s\n", inet_ntop(AF_INET6, &pInfo->tunnel_parm6rd.prefix, prefix, INET6_ADDRSTRLEN));
				cmm_print(DEBUG_STDOUT, "6rd-prefixlen      : %d\n", pInfo->tunnel_parm6rd.prefixlen);
				cmm_print(DEBUG_STDOUT, "6rd-relayprefix    : %s\n", inet_ntop(AF_INET, &pInfo->tunnel_parm6rd.relay_prefix, relayprefix, INET_ADDRSTRLEN));
				cmm_print(DEBUG_STDOUT, "6rd-relayprefixlen : %d\n", pInfo->tunnel_parm6rd.relay_prefixlen);
			}

		}
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

		if (strcmp(*keywords, "ethipoip6") == 0)
			tnl_type = TNL_ETHIPOIP6;
		else if (strcmp(*keywords, "ethip") == 0)
			tnl_type = TNL_ETHIPOIP4;
		else if (strcmp(*keywords, "gre6") == 0)
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
#ifdef SAM_LEGACY
	else if (strncmp(*keywords, "set",  strlen(*keywords)) ==0)
	{
		unsigned int tmp;
		char * endptr = NULL;
		keywords++;
		
		if((argc < 3) ||((argc >3) && (argc <5)))
			return tunnel_print_usage();
		
		strcpy(cmmtd_cmd.name, tnl_name);
		cmmtd_cmd.tunnel_type = TNL_4O6;
		
		if(strncasecmp(*keywords,"sam_enable", strlen(*keywords)) == 0)
			cmmtd_cmd.sam_enable = 1;
		else if(strncasecmp(*keywords,"sam_disable", strlen(*keywords)) == 0)
			cmmtd_cmd.sam_enable = 0;
		else
			return tunnel_print_usage();
		
		cmmtd_cmd.tun_mtu = DEFAULT_SAM_FRAG_MTU;
		if( argc >3 )
		{
			keywords++;
			if(strncasecmp(*keywords,"sam-frag-mtu",strlen(*keywords))==0)
			{
				keywords++;
				tmp = strtoul(*keywords, &endptr, 0);
				cmmtd_cmd.tun_mtu= tmp;
			}
		}

		if(cmmSendToDaemon(daemon_handle, CMMD_CMD_TUNNEL_SAMREADY, &cmmtd_cmd, sizeof(cmmtd_cmd), &rxbuf.rcvBuffer) == 1)
		{
			if (rxbuf.result != 0)
			{
				showErrorMsg("CMD_TUNNEL_SAMREADY", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
				return -1;
			}
		}

	}
#endif
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
		if (itf->tunnel_parm6.proto == IPPROTO_ETHERIP)
			cmd.mode = TNL_ETHIPOIP6;
		else if (itf->tunnel_parm6.proto == IPPROTO_IPIP)
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
		if(!(itf->tunnel_flags & TNL_4RD))
		{
			memcpy(cmd.remote, itf->tunnel_parm6.raddr.s6_addr, 16);
		}

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
		if (itf->tunnel_parm4.iph.protocol == IPPROTO_ETHERIP)
			cmd.mode = TNL_ETHIPOIP4;
		else if (itf->tunnel_parm4.iph.protocol == IPPROTO_IPV6)
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
#ifdef SAM_LEGACY
		if(proto == IPPROTO_IPIP)
		{
			if ((!itf->sam_enable) && (!rt_mw_sam_get_ipv6(&itf->tunnel_parm6.laddr)) )
			{
				cmm_print(DEBUG_INFO,"Tunnel %s is up but SAM is not yet Ready", itf->ifname );
				goto err;
			}
		}
#endif

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
	case TNL_ETHIPOIP6:
	case TNL_GRE_IPV6:
		if (itf->tunnel_family != AF_INET6)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have family %d\n", __func__, tnl_type, itf->tunnel_family);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

		if (itf->tunnel_parm6.proto != IPPROTO_ETHERIP && itf->tunnel_parm6.proto != IPPROTO_GRE)
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

	case TNL_ETHIPOIP4:
		if (itf->tunnel_family != AF_INET)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have family %d\n", __func__, tnl_type, itf->tunnel_family);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

		if (itf->tunnel_parm4.iph.protocol != IPPROTO_ETHERIP)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have proto %d\n", __func__, tnl_type, itf->tunnel_parm4.iph.protocol);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

		if (ipsec)
			itf->tunnel_flags |= TNL_IPSEC;
		else
		{
			if(itf->tunnel_flags & TNL_IPSEC)
				itf->flags |= FPP_NEEDS_UPDATE;
			itf->tunnel_flags &= ~TNL_IPSEC;
			update_tnl_flows = 1;
		}
		break;

#ifdef SAM_LEGACY
	case TNL_4O6:
		if (itf->tunnel_family != AF_INET6)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have family %d\n", __func__, tnl_type, itf->tunnel_family);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}
		if (itf->tunnel_parm6.proto != IPPROTO_IPIP)
		{
			cmm_print(DEBUG_ERROR, "%s: tunnel type %x can't have proto %d\n", __func__, tnl_type, itf->tunnel_parm6.proto);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}

                if(tnl_type == TNL_4O6)
                {
                        if(! itf->sam_enable)
                        {
                               update_connections = 1;
                               itf->sam_enable = 1;
		  	       itf->tunnel_flags |= TNL_4RD;
                        }
                }
		break;
#endif

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported tunnel type %x\n", __func__, tnl_type);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err1;
	}
#ifndef SAM_LEGACY
	if(itf->phys_ifindex)// Bound to an interface
	{
		if(!__itf_is_programmed(itf->phys_ifindex))
		{
			cmm_print(DEBUG_ERROR, "%s: Fast forward tunneling only supported on offloaded interface\n", __func__);
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto err1;
		}
	}
#endif

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

	#ifdef SAM_LEGACY
	itf->tunnel_flags &= ~TNL_4RD;
	#endif
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
			pInfo->conf_6rd = (itf->tunnel_flags & TNL_6RD) ? 1: 0;
			if(itf->tunnel_flags & TNL_6RD)
				memcpy(&pInfo->tunnel_parm6rd, &itf->tunnel_parm6rd, sizeof(struct ip_tunnel_6rd));
		}
		
		*res_len = sizeof(struct tunnel_info) + 4;
	}


err:
	__pthread_mutex_unlock(&itf_table.lock);
	return 0;
}

#ifdef SAM_LEGACY

int tunnel_conv_id_set(FCI_CLIENT *fci_handle, char *name,  char *buffer, int buffer_size)
{
       int ifindex;
       struct interface *itf;
       int rc = 0;

       pthread_mutex_lock(&itf_table.lock);

       ifindex = if_nametoindex(name);

       itf = __itf_find(ifindex);
       if (!itf)
               goto err;

       if (!____itf_is_4o6_tunnel(itf))
               goto err;

       rc = fci_write(fci_handle, FPP_CMD_TUNNEL_4rd_ID_CONV_psid, sizeof(fpp_tunnel_id_conv_cmd_t),(unsigned short *)buffer);
       if(rc != 0)
               cmm_print(DEBUG_ERROR, "%s: Error %d while sending FPP_CMD_TUNNEL_4rd_ID_CONV\n", __func__, rc);

err:
       pthread_mutex_unlock(&itf_table.lock);
       return rc;

}

#endif

/************************************************************
 * 
 * tunnel_daemon_msg_recv
 * Role: Parse CMM to deamon messages
 ************************************************************/
int tunnel_daemon_msg_recv(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_tunnel_t *tnl = (cmmd_tunnel_t *) cmd_buf;
	#ifdef SAM_LEGACY
	fpp_tunnel_id_conv_cmd_t *tnl_IdConv = (fpp_tunnel_id_conv_cmd_t*) cmd_buf;
	#endif

	switch (function_code)
	{
	case CMMD_CMD_TUNNEL_ADD:
		return tunnel_add(fci_handle, fci_key_handle, tnl->name, tnl->ipsec, tnl->tunnel_type, res_buf, res_len);

	case CMMD_CMD_TUNNEL_DEL:
		return tunnel_del(fci_handle, fci_key_handle, tnl->name, res_buf, res_len);

	case CMMD_CMD_TUNNEL_SHOW:
		return tunnel_show(fci_handle, tnl->name, res_buf, res_len);
#ifdef SAM_LEGACY
	case CMMD_CMD_TUNNEL_SAMREADY:
	{
               if(tnl->tun_mtu > __itf_get_mtu(if_nametoindex(tnl->name)))
               {
                       cmm_print(DEBUG_STDERR,"\n ERROR : configured MTU cannot be greater than tunnel interface's MTU");
                       return 0;
               }
               TunMtu = tnl->tun_mtu;

	       if(tnl->sam_enable)
                     return tunnel_add(fci_handle, fci_key_handle, tnl->name, tnl->ipsec, tnl->tunnel_type , res_buf, res_len);
               else
                     return tunnel_del(fci_handle, fci_key_handle, tnl->name,res_buf, res_len);
	}
        case CMMD_CMD_TUNNEL_IDCONV_psid:
	{
		res_buf[0] = CMMD_ERR_OK;
		*res_len = 2;

               return tunnel_conv_id_set(fci_handle, (char*)tnl_IdConv->name,(char*)cmd_buf, sizeof(fpp_tunnel_id_conv_cmd_t));
	}
#endif
	default:
		res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
		*res_len = 2;
	}

	return 0;
}

static u_int32_t try_6rd(const u_int32_t *daddr, struct interface *itf)
{
	u_int32_t dst = 0;

	if (itf->tunnel_flags & TNL_6RD)
	{
		if (cmmPrefixEqual(daddr, itf->tunnel_parm6rd.prefix.s6_addr32, itf->tunnel_parm6rd.prefixlen)) {
			unsigned pbw0, pbi0;
			int pbi1;
			u_int32_t d;

			pbw0 = itf->tunnel_parm6rd.prefixlen >> 5;
			pbi0 = itf->tunnel_parm6rd.prefixlen & 0x1f;

			d = (ntohl(daddr[pbw0]) << pbi0) >> itf->tunnel_parm6rd.relay_prefixlen;

			pbi1 = pbi0 - itf->tunnel_parm6rd.relay_prefixlen;
			if (pbi1 > 0)
				d |= ntohl(daddr[pbw0 + 1]) >> (32 - pbi1);

			dst = (itf->tunnel_parm6rd.relay_prefix & ((1 << itf->tunnel_parm6rd.relay_prefixlen) - 1)) | htonl(d);
		}
	}
	else
	{
		if (((u_int16_t *)daddr)[0] == htons(0x2002)) {
			/* 6to4 v6 addr has 16 bits prefix, 32 v4addr, 16 SLA, ... */
			memcpy(&dst, &((u_int16_t *)daddr)[1], 4);
		}
	}

	return dst;
}

unsigned int tunnel_get_ipv4_dst(struct RtEntry *route, struct interface *itf)
{
	unsigned int dst;

#if defined(PROPRIETARY_6RD)
	dst = try_6rd(route->dAddr, itf);

	if (!dst)
		dst = try_6rd(route->gwAddr, itf);

	if (!dst)
		dst = itf->tunnel_parm6rd.relay_prefix;

#else
	dst = try_6rd(route->dAddr, itf);

	/* FIXME this doesn't match exactly the Linux ipv6->ipv4 address mapping */
	if (!dst)
	{
		/* ipv6 addr compatible v4 */
		if ((route->gwAddr[0] == 0) && (route->gwAddr[1] == 0) && (route->gwAddr[2] == 0) &&
			route->gwAddr[3] && (route->gwAddr[3] != htonl(0x00000001)))
			dst = route->gwAddr[3];
	}
#endif

	return dst;
}

/************************************************************
 *
 * __cmmGetTunnel6rd
 * Role : Check if interface is a 6rd tunnel and retrieve info from kernel
 ************************************************************/
static void __cmmGetTunnel6rd(int fd, struct interface *itf)
{
	struct ifreq ifr;
	int rc;

	itf->tunnel_flags &= ~TNL_6RD;
	memset(&itf->tunnel_parm6rd, 0, sizeof(struct ip_tunnel_6rd));

	if (____itf_get_name(itf, ifr.ifr_name, sizeof(ifr.ifr_name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);

		goto out;
	}

	ifr.ifr_ifru.ifru_data = (void *)&itf->tunnel_parm6rd;

	rc = ioctl(fd, SIOCGET6RD, &ifr);
	if (rc < 0)
		goto out;

	itf->tunnel_flags |= TNL_6RD;

out:
	return;
}

/************************************************************
 *
 * __cmmGetTunnel
 * Role : Check if interface is a tunnel and retrieve info from kernel
 ************************************************************/
int __cmmGetTunnel(int fd, struct interface *itf, struct rtattr *tb[])
{
	struct ifreq ifr;
	int rc, isipv4 = 0;

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

		/* Find out if the tunnel is an IPv4 / IPv6 tunnel*/
		isipv4 = (ioctl(fd, SIOCISETHIPV4TUNNEL, &ifr) == 0 );

		if(isipv4)
		{
			if(itf->tunnel_parm4.iph.protocol == IPPROTO_ETHERIP)
			{
				itf->itf_flags |= ITF_TUNNEL;
				itf->tunnel_family = AF_INET;
				goto out;
			}
		}
		else
		{
			if(itf->tunnel_parm6.proto == IPPROTO_ETHERIP)
			{
				itf->itf_flags |= ITF_TUNNEL;
				itf->tunnel_family = AF_INET6;
				goto out;
			}
		}

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

		__cmmGetTunnel6rd(fd, itf);

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


#ifdef SAM_LEGACY

int cmm4rdIdConvSetProcess(char ** keywords, int tabStart, int argc, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0;
	char SndBuffer[256];
	
	if(argc < 3)
		goto usage;

	fpp_tunnel_id_conv_cmd_t *pIdConvCmd = (fpp_tunnel_id_conv_cmd_t* )SndBuffer;
	memset(pIdConvCmd,0, sizeof(fpp_tunnel_id_conv_cmd_t));
	
	if(strcasecmp(keywords[tabStart++],"interface") != 0)
		goto usage;

	strncpy((char*)pIdConvCmd->name, keywords[tabStart++],IFNAMSIZ);
	if(strcasecmp(keywords[tabStart++],"enable") == 0)
		 pIdConvCmd->IdConvStatus = 1;
	
	if(rt_mw_sam_get_portsetid(&pIdConvCmd->sam_port_info))
	{
		cmm_print(DEBUG_STDOUT,"\t Third party shared library failed \n");
		goto usage;
	}
	
	rcvBytes = cmmSendToDaemon(daemon_handle,CMMD_CMD_TUNNEL_IDCONV_psid, pIdConvCmd, sizeof(fpp_tunnel_id_conv_cmd_t), SndBuffer);
	if (rcvBytes >=2)/* we expect 2 bytes in response */
	{
	if ((((u_int16_t*)SndBuffer)[0]) != CMMD_ERR_OK)
		{
			showErrorMsg("CMD_CMMTD_TUNNEL_IDCONV", ERRMSG_SOURCE_CMMD, SndBuffer);
			return -1;
		}
	}

	return 0;
	
usage:
	cmm_print(DEBUG_STDOUT,"\tset 4rd-id-conversion interface <4o6 Interface name> <enable/disable>\n");
	return -1;
}

#else
int cmm4rdIdConvSetProcess(char ** keywords, int tabStart, int argc, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0,rc =0;
	union u_txbuf txbuf;
	fpp_tunnel_id_conv_cmd_t *pIdConvCmd = (fpp_tunnel_id_conv_cmd_t* )txbuf.SndBuffer;

	if(!keywords[tabStart])
		goto usage;
	
	memset(pIdConvCmd,0, sizeof(fpp_tunnel_id_conv_cmd_t));
	if(strcasecmp(keywords[tabStart],"enable") == 0)
		pIdConvCmd->IdConvStatus = 1;
	
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_TUNNEL_4rd_ID_CONV_dport, pIdConvCmd, sizeof(fpp_tunnel_id_conv_cmd_t), txbuf.SndBuffer);
	rc =  (rcvBytes < sizeof(unsigned short) ) ? 0 : txbuf.result;
	if (rcvBytes !=  sizeof(unsigned short) || (rc))
		showErrorMsg("CMD_TUNNEL_4rd_ID_CONV", ERRMSG_SOURCE_FPP, txbuf.SndBuffer);
	
	return  rc;
	
usage:
	cmm_print(DEBUG_STDOUT,"\tset 4rd-id-conversion <enable/disable>\n");
	return -1;
}
#endif

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
                        else if (pTnlCmd->mode == TNL_ETHIPOIP4)
			{
				mode = "EtherIP";
				family = AF_INET;
			}
			else
				mode = "EtherIP6";

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

#ifdef SAM_LEGACY

int getTunnel4rdAddress(struct interface* itf, u_int32_t * Daddrv6,  unsigned int Daddr, unsigned short Dport)
{
       int i =0;
       for (i = 0; i< 4;i++)
               Daddrv6[i] = itf->tunnel_parm6.raddr.s6_addr32[i];
       rt_mw_sam_make_dst_ipv6( (struct in_addr *)&Daddr , Dport, (struct in6_addr *)Daddrv6 );
       return 0;
}
#else

static int
__getTunnel4rdAddress(__u32 *daddr6, __u32 daddr4, __u16 dport4, struct ip6_4rd_map_msg *mr)
{

       int i, pbw0, pbi0, pbi1;
       __u32 daddr[4];
       __u32 port_set_id = 0;
       __u32 mask;
       __u32 da = ntohl(daddr4);
       __u16 dp = ntohs(dport4);
       __u32 diaddr[4];
       int port_set_id_len = ( mr->eabit_len ) - ( 32 - mr->prefixlen ) ;

       if ( port_set_id_len < 0) {
               cmm_print(DEBUG_STDOUT," %s:  PSID length ERROR %d\n",__func__, port_set_id_len);
               return -1;
       }

       if ( port_set_id_len > 0) {
               mask = 0xffffffff >> (32 - port_set_id_len);
               port_set_id = ( dp >> (16 - mr->psid_offsetlen - port_set_id_len ) & mask ) ;
       }

       for (i = 0; i < 4; ++i)
               daddr[i] = ntohl(mr->relay_prefix.s6_addr32[i])
                       | ntohl(mr->relay_suffix.s6_addr32[i]);

       if( port_set_id_len != 0 ) {
               pbw0 = mr->relay_prefixlen >> 5;
               pbi0 = mr->relay_prefixlen & 0x1f;
               daddr[pbw0] |= (da << mr->prefixlen) >> pbi0;
               pbi1 = pbi0 - mr->prefixlen;
               if (pbi1 > 0)
                       daddr[pbw0+1] |= da << (32 - pbi1);

               if ( port_set_id_len > 0) {
                       pbw0 = (mr->relay_prefixlen + 32 - mr->prefixlen) >> 5;
                       pbi0 = (mr->relay_prefixlen + 32 - mr->prefixlen) & 0x1f;
                       daddr[pbw0] |= (port_set_id << (32 - port_set_id_len)) >> pbi0;
                       pbi1 = pbi0 - (32 - port_set_id_len);
                       if (pbi1 > 0)
                               daddr[pbw0+1] |= port_set_id << (32 - pbi1);
               }
       }

       memset(diaddr, 0, sizeof(diaddr));

       diaddr[2] = ( da >> 8 ) ;
       diaddr[3] = ( da << 24 ) ;
       diaddr[3] |= ( port_set_id << 8 ) ;

       for (i = 0; i < 4; ++i)
               daddr[i] = daddr[i] | diaddr[i] ;

       for (i = 0; i < 4; ++i)
               daddr6[i] = htonl(daddr[i]);

	/* DBG */
	cmm_print(DEBUG_INFO," %s : %08x %08x %08x %08x  PSID:%04x\n",__func__ ,daddr[0], daddr[1], daddr[2], daddr[3], port_set_id);
	/* DBG */

	return 0;
}



int getTunnel4rdAddress(struct interface* itf, u_int32_t * Daddrv6,  unsigned int Daddr, unsigned short Dport)
{

	struct list_head *entry, *next_entry;
	struct map_rule *mr = NULL, *mr_tmp = NULL;
	unsigned int mask  = 0;
	int mr_prefixlen = 0;	
	int count = 0;
	int err = 0;
	int i = 0;
	// set default daddr as that of tunnel remote address will be used for  all 4o6 tunnels and when packets are intended for BR/ P-SAM.
	for (i = 0; i< 4;i++)
		Daddrv6[i] = itf->tunnel_parm6.raddr.s6_addr32[i];
	

	cmm_print(DEBUG_INFO, "%s: mapping rule match \n", __func__);

	for (entry = list_first(&itf->mr_list); next_entry = list_next(entry), entry != &itf->mr_list; entry = next_entry)
	{

		mr = container_of(entry, struct map_rule, list);
		mask = 0xffffffff << (32 - mr->rule.prefixlen) ;
		cmm_print(DEBUG_INFO,"Prefix %d prefixlen %d daddr %d ",htonl(mr->rule.prefix), mr->rule.prefixlen, htonl(Daddr));
		if( (htonl(Daddr) & mask ) == htonl( mr->rule.prefix) ) {
	                if ( mr->rule.prefixlen >= mr_prefixlen ){
                                       mr_prefixlen = mr->rule.prefixlen ;
                                       mr_tmp = mr;
                                       count++;
			}
		}
	}
	if(count)
		err =__getTunnel4rdAddress(Daddrv6, Daddr, Dport, &mr_tmp->rule );
	return err;
}


#endif
