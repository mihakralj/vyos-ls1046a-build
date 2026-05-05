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
#include <stdlib.h>
#include <string.h>
#include "module_natpt.h"

extern struct socket *socket_find(u_int16_t id);

static void cmmNATPTSetUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: set natpt \n"
			"\n"
			"                                  [open]\n"
			"                                       [sock_id_a {socket ID}]\n"
			"                                       [sock_id_b {socket ID}]\n"
			"                                       [6to4] \n"
			"                                       [4to6] \n"
			"\n"
			"                                  [close]\n"
			"                                       [sock_id_a {socket ID}]\n"
			"                                       [sock_id_b {socket ID}]\n"
			"\n"
	          );
}
static void cmmNATPTQueryUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: query natpt\n"
			"             [sock_id_a {socket ID}]\n"
			"             [sock_id_b {socket ID}]\n"
			"\n"
	          );
}
int cmmNATPTSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	union u_rxbuf rxbuf;

	if(!keywords[cpt])
		goto print_help;

	if (strcasecmp(keywords[cpt], "open") == 0)
	{
		fpp_natpt_open_cmd_t cmd;

		if(!keywords[++cpt])
			goto print_help;
		
		memset(&cmd, 0, sizeof(cmd));

		if((strcasecmp(keywords[cpt], "sock_id_a") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: sock_id_a parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket_a= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
	
		if((strcasecmp(keywords[cpt], "sock_id_b") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: sock_id_b parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket_b= tmp;
		}
		else
			goto keyword_error;

		while (keywords[++cpt])
		{
			if((strcasecmp(keywords[cpt], "6to4") == 0))
				cmd.control |= FPP_NATPT_CONTROL_6to4;
			else if((strcasecmp(keywords[cpt], "4to6") == 0))
				cmd.control |= FPP_NATPT_CONTROL_4to6;
			else
				goto keyword_error;
		}

		if (cmd.control == 0)
		{
			cmm_print(DEBUG_CRIT, "ERROR: 6to4 or 4to6 must be specified\n");
			goto print_help;
		}

		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_NATPT_OPEN, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_NATPT_OPEN", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	
	else if (strcasecmp(keywords[cpt], "close") == 0)
	{
		fpp_natpt_close_cmd cmd;

		if(!keywords[++cpt])
			goto print_help;
		
		memset(&cmd, 0, sizeof(cmd));

		if((strcasecmp(keywords[cpt], "sock_id_a") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: sock_id_a parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket_a= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
	
		if((strcasecmp(keywords[cpt], "sock_id_b") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: sock_id_b parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket_b= tmp;
		}
		else
			goto keyword_error;

		if (keywords[++cpt])
			goto keyword_error;

		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_NATPT_CLOSE, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_NATPT_CLOSE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	else
	{
		goto keyword_error;
	}
	
	return 0;

keyword_error:
	cmm_print(DEBUG_STDOUT,"ERR: unknown keyword %s\n", keywords[cpt]);
print_help:
	cmmNATPTSetUsage();
	return -1;
}

int cmmNATPTQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int rcvBytes;
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	union u_rxbuf rxbuf;
	fpp_natpt_query_cmd_t cmd;
	fpp_natpt_query_response_t *pEntryResponse;

	if(!keywords[cpt])
		goto print_help;

	memset(&cmd, 0, sizeof(cmd));

	if((strcasecmp(keywords[cpt], "sock_id_a") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;
		
		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_CRIT, "ERROR: sock_id_a parameter must be a number between 0 and %d\n", USHRT_MAX);
			goto  print_help;
		}
		cmd.socket_a= tmp;
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto print_help;

	if((strcasecmp(keywords[cpt], "sock_id_b") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;
	
		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_CRIT, "ERROR: sock_id_b parameter must be a number between 0 and %d\n", USHRT_MAX);
			goto  print_help;
		}
		cmd.socket_b= tmp;
	}
	else
		goto keyword_error;

	if (keywords[++cpt])
		goto keyword_error;

	// Send  command
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_NATPT_QUERY, &cmd, sizeof(cmd), &rxbuf.rcvBuffer);
	if (rcvBytes != sizeof(fpp_natpt_query_response_t))
	{
		cmm_print(DEBUG_STDERR, "ERROR: CMD_NATPT_QUERY Unexpected result returned from FPP rc:%04x - received %d - expected %zu\n",
			(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result,
			rcvBytes, sizeof(fpp_natpt_query_response_t)
		);
		return -1;
	}
	pEntryResponse = (fpp_natpt_query_response_t *)rxbuf.rcvBuffer;
	if (pEntryResponse->retcode != 0)
	{
		showErrorMsg("CMD_NATPT_QUERY", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		return -1;
	}

	cmm_print(DEBUG_STDOUT, "NAT-PT Entry:\n\tSocket A: %d, Socket B: %d%s%s%s\n",
					pEntryResponse->socket_a, pEntryResponse->socket_b,
					(pEntryResponse->control & FPP_NATPT_CONTROL_6to4) ? ", 6to4" : "",
					(pEntryResponse->control & FPP_NATPT_CONTROL_4to6) ? ", 4to6" : "",
					(pEntryResponse->control & FPP_NATPT_CONTROL_TCPFIN) ? ", TCP_FIN" : "");
	cmm_print(DEBUG_STDOUT, "\t# of IPv6 Packets Received: %lld\n", pEntryResponse->stat_v6_received);
	cmm_print(DEBUG_STDOUT, "\t# of IPv6 Packets Transmitted: %lld\n", pEntryResponse->stat_v6_transmitted);
	cmm_print(DEBUG_STDOUT, "\t# of IPv6 Packets Dropped: %lld\n", pEntryResponse->stat_v6_dropped);
	cmm_print(DEBUG_STDOUT, "\t# of IPv6 Packets Sent to ACP: %lld\n", pEntryResponse->stat_v6_sent_to_ACP);
	cmm_print(DEBUG_STDOUT, "\t# of IPv4 Packets Received: %lld\n", pEntryResponse->stat_v4_received);
	cmm_print(DEBUG_STDOUT, "\t# of IPv4 Packets Transmitted: %lld\n", pEntryResponse->stat_v4_transmitted);
	cmm_print(DEBUG_STDOUT, "\t# of IPv4 Packets Dropped: %lld\n", pEntryResponse->stat_v4_dropped);
	cmm_print(DEBUG_STDOUT, "\t# of IPv4 Packets Sent to ACP: %lld\n", pEntryResponse->stat_v4_sent_to_ACP);

	return 0;


keyword_error:
	cmm_print(DEBUG_STDOUT,"ERR: unknown keyword %s\n", keywords[cpt]);
print_help:
	cmmNATPTQueryUsage();
	return -1;
}


int cmmNATPTOpenProcessClientCmd(FCI_CLIENT* fci_handle, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	fpp_natpt_open_cmd_t *cmd = (fpp_natpt_open_cmd_t*)cmd_buf;
	struct socket *sockA, *sockB;
	int rc = 0;
	struct RtEntry *socka_rtentry;
	struct RtEntry *sockb_rtentry;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&socket_lock);

	sockA = socket_find(cmd->socket_a);
	sockB = socket_find(cmd->socket_b);
	if(!sockA || !sockB)
	{
		rc = CMMD_ERR_NOT_CONFIGURED;
		goto out;
	}

	socka_rtentry = sockA->rt.route;
	sockb_rtentry = sockB->rt.route;
	if (socka_rtentry && sockb_rtentry)
	{
		int socka_iif, sockb_iif;
		struct ct_route old_route;

		// input interface is the other socket's output interface
		socka_iif = sockb_rtentry->oifindex;
		sockb_iif = socka_rtentry->oifindex;

		// Update input interface for sockA
		old_route = sockA->rt;
		sockA->rt.route = NULL;
		sockA->rt.fpp_route = NULL;
		sockA->rt.fpp_route_id = 0;
		sockA->iifindex = socka_iif;
		__socket_open(fci_handle, sockA);
		__cmmRouteDeregister(fci_handle, &old_route, "socket");

		// Update input interface for sockB
		old_route = sockB->rt;
		sockB->rt.route = NULL;
		sockB->rt.fpp_route = NULL;
		sockB->rt.fpp_route_id = 0;
		sockB->iifindex = sockb_iif;
		__socket_open(fci_handle, sockB);
		__cmmRouteDeregister(fci_handle, &old_route, "socket");
	}

	rc = fci_cmd(fci_handle, FPP_CMD_NATPT_OPEN, (unsigned short *)cmd_buf, cmd_len, (unsigned short *)res_buf, res_len);
out:
	__pthread_mutex_unlock(&socket_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return rc;
}
