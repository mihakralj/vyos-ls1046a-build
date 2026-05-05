/*
 *
 *	Copyright (C) 2007 Mindspeed Technologies, Inc.
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
#include "cmmd.h"
#include <sys/ioctl.h>


static void cmmRouteDumpTable(char *output_device);

struct route_list {
	struct route_list *next;
	cmmd_route_entry_t route;
};
struct route_list *route_table = NULL;
pthread_mutex_t RouteMutex = PTHREAD_MUTEX_INITIALIZER;

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmRouteShowPrintHelp()
{
	cmmRouteSetPrintHelp();
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmRouteShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{	
	return 0;
}
/************************************************************
 *
 *
 *
 *************************************************************/
int cmmRouteQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	cmmRouteSetPrintHelp();
		return -1;
}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmRouteSetPrintHelp()
{
	cmm_print(DEBUG_STDOUT, "Usage: set route interface {if_name} {add | del | query}\n"
									"	[type {ip type} ]\n"
									"	[prio {priority} ]\n"
									"	[mtu {mtu} ]\n"
									"	[dstip {ipv4_dst_addr_min-ipv4_dst_addr_max } ]\n"
									"	[srcip {ipv4_src_addr_min-ipv4_dst_addr_max } ]\n"
									"	[input {interface name} ]\n"
									"	[proto {proto} ]\n"
									"	[dstport {port_dst_min-port_dst_max} ]\n"
									"	[srcport {port_src_min-port_src_max} ]\n");
}

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmRouteSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	int rc;
	
	char sndBuffer[256];
	union u_rxbuf rxbuf;
	cmmd_route_entry_t * entryCmd = (cmmd_route_entry_t *) sndBuffer;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	cmm_print(DEBUG_INFO, "Entered Route Set Process\n");

	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if (strlen(keywords[cpt]) > (IFNAMSIZ - 1)) {
			cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters %zu \n", 
				keywords[cpt], (IFNAMSIZ - 1), strlen(keywords[cpt]));
			goto help;
		}
		strcpy(entryCmd->output_device_str, keywords[cpt]);
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;
	
	if(strcasecmp(keywords[cpt], "add") == 0)
	{		
		entryCmd->action = CMMD_EXTROUTE_ACTION_ADD;
	}
	else if(strcasecmp(keywords[cpt], "del") == 0)
	{
		entryCmd->action = CMMD_EXTROUTE_ACTION_REMOVE;
	}
	else if(strcasecmp(keywords[cpt], "query") == 0)
	{
		entryCmd->action = CMMD_EXTROUTE_ACTION_QUERY;
		goto send;
	}
	else if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		entryCmd->action = CMMD_EXTROUTE_ACTION_RESET;
		goto send;
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;

	while (keywords[cpt] != NULL)
	{
		if (strcasecmp(keywords[cpt], "dstip") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			
			/*check that the range form is used*/
			endptr = strchr(keywords[cpt], '-');
			if (endptr) {
				*endptr = '\0';
			}

			if(inet_pton(AF_INET, keywords[cpt], &entryCmd->dst_addr[0]) != 1)
				goto help;

			if (endptr) {
				if(inet_pton(AF_INET, endptr+1, &entryCmd->dst_addr[1]) != 1)
					goto help;
			}
			else {
				entryCmd->dst_addr[1] = entryCmd->dst_addr[0];
			}
		}
		else if (strcasecmp(keywords[cpt], "srcip") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			
			/* check that the range form is used */
			endptr = strchr(keywords[cpt], '-');
			if (endptr) {
				*endptr = '\0';
			}

			if(inet_pton(AF_INET, keywords[cpt], &entryCmd->src_addr[0]) != 1)
				goto help;

			if (endptr) {
				if(inet_pton(AF_INET, endptr+1, &entryCmd->src_addr[1]) != 1)
					goto help;
			}
			else {
				entryCmd->src_addr[1] = entryCmd->src_addr[0];
			}
		}
		else if (strcasecmp(keywords[cpt], "input") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			
			if (strlen(keywords[cpt]) > (IFNAMSIZ - 1)) {
				cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters %zu \n", 
					keywords[cpt], (IFNAMSIZ - 1), strlen(keywords[cpt]));
				goto help;
			}

			strcpy(entryCmd->input_device_str, keywords[cpt]);
		}
		else if (strcasecmp(keywords[cpt], "proto") == 0)
		{
			if(!keywords[++cpt])
				goto help;
				
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);

			if (tmp <= 255)
				entryCmd->proto = tmp;
			else 
				goto help;
		}
		else if (strcasecmp(keywords[cpt], "dstport") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			
			endptr = strchr(keywords[cpt], '-');
			if (endptr) {
				*endptr = '\0';
			}
			tmp = strtoul(keywords[cpt], NULL, 0);
			if (tmp <= 65535)
				entryCmd->dst_port[0] = tmp;
			else 
				goto help;

			if (endptr) {
				tmp = strtoul(endptr+1, &endptr, 0);
				if (tmp <= 65535)
					entryCmd->dst_port[1] = tmp;
				else 
					goto help;
			} else {
				entryCmd->dst_port[1] = entryCmd->dst_port[0];
			}
		}
		else if (strcasecmp(keywords[cpt], "srcport") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			
			endptr = strchr(keywords[cpt], '-');
			if (endptr) {
				*endptr = '\0';
			}
			tmp = strtoul(keywords[cpt], NULL, 0);
			if (tmp <= 65535)
				entryCmd->src_port[0] = tmp;
			else 
				goto help;

			if (endptr) {
				tmp = strtoul(endptr+1, &endptr, 0);
				if (tmp <= 65535)
					entryCmd->src_port[1] = tmp;
				else 
					goto help;
			} else {
				entryCmd->src_port[1] = entryCmd->src_port[0];
			}
		}
		else if (strcasecmp(keywords[cpt], "prio") == 0)
		{
			if(!keywords[++cpt])
				goto help;
				
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			entryCmd->prio = tmp;
		}
		else if (strcasecmp(keywords[cpt], "mtu") == 0)
		{
			if(!keywords[++cpt])
				goto help;
				
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			entryCmd->mtu = tmp;
		}
		else
			goto help;

		cpt++;
	}
send:
	rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_EXTROUTE, sndBuffer, sizeof(cmmd_route_entry_t), rxbuf.rcvBuffer);
	if(rc == 2)
	{
		if (rxbuf.result != CMMD_ERR_OK)
			showErrorMsg("CMD_EXTROUTE", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
	}
	else
		cmm_print(DEBUG_STDERR, "Unexpected size %d received for CMD_EXTROUTE\n", rc);

	return 0;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmRouteSetPrintHelp();
	return -1;
}

static void cmmRouteDumpTable(char *output_device)
{
	struct route_list *temp;
	char s[INET_ADDRSTRLEN], s2[INET_ADDRSTRLEN];
	char mtu_buf[16];
	char dstip_buf[40];
	char srcip_buf[40];
	char input_buf[16];
	char proto_buf[16];
	char dstport_buf[32];
	char srcport_buf[32];

	cmm_print(DEBUG_INFO, "cmmRouteDumpTable for %s device \n", output_device);

	__pthread_mutex_lock(&RouteMutex);

	for (temp = route_table; temp != NULL; temp = temp->next) {
		if(!strcasecmp(temp->route.output_device_str, output_device) || !strcasecmp(output_device, "all"))
		{

			if (temp->route.mtu != 0)
				sprintf(mtu_buf, "mtu:%d ", temp->route.mtu);
			else
				mtu_buf[0] = '\0';
		
			if (temp->route.dst_addr[0]) {
				if (temp->route.dst_addr[1] != temp->route.dst_addr[0]) {
					sprintf(dstip_buf, "%s-%s",
						inet_ntop(AF_INET, &temp->route.dst_addr[0], s, sizeof(s)),
						inet_ntop(AF_INET, &temp->route.dst_addr[1], s2, sizeof(s2))); 
				} else {
					sprintf(dstip_buf, "%s",
						inet_ntop(AF_INET, &temp->route.dst_addr[0], s, sizeof(s)));
				}
			} else {
				strcpy(dstip_buf, "*");
			}
		
			if (temp->route.src_addr[0]) {
				if (temp->route.src_addr[1] != temp->route.src_addr[0]) {
					sprintf(srcip_buf, "%s-%s",
						inet_ntop(AF_INET, &temp->route.src_addr[0], s, sizeof(s)),
						inet_ntop(AF_INET, &temp->route.src_addr[1], s2, sizeof(s2))); 
				} else {
					sprintf(srcip_buf, "%s",
						inet_ntop(AF_INET, &temp->route.src_addr[0], s, sizeof(s)));
				}
			} else {
				strcpy(srcip_buf, "*");
			}
		
			if (temp->route.input_device_str[0]) {
				strcpy(input_buf, temp->route.input_device_str);
			} else {
				strcpy(input_buf, "*");
			}
		
			if (temp->route.proto) {
				sprintf(proto_buf, "%d", temp->route.proto);
			} else {
				strcpy(proto_buf, "*");
			}
		
			if (temp->route.dst_port[0]) {
				if (temp->route.dst_port[1] != temp->route.dst_port[0]) {
					sprintf(dstport_buf, "%d-%d", temp->route.dst_port[0], temp->route.dst_port[1]);
				} else {
					sprintf(dstport_buf, "%d", temp->route.dst_port[0]);
				}
			} else {
				strcpy(dstport_buf, "*");
			}
		
			if (temp->route.src_port[0]) {
				if (temp->route.src_port[1] != temp->route.src_port[0]) {
					sprintf(srcport_buf, "%d-%d", temp->route.src_port[0], temp->route.src_port[1]);
				} else {
					sprintf(srcport_buf, "%d", temp->route.src_port[0]);
				}
			} else {
				strcpy(srcport_buf, "*");
			}
		
			cmm_print(DEBUG_STDOUT, "dev:%s prio:%d %sdstip:%s srcip:%s input:%s proto:%s dstport:%s srcport:%s\n",
					temp->route.output_device_str, temp->route.prio, mtu_buf, dstip_buf, srcip_buf, input_buf,
					proto_buf, dstport_buf, srcport_buf);
		}
	}
	__pthread_mutex_unlock(&RouteMutex);

}

int cmmRouteProcessClientCmd(FCI_CLIENT* fciMsgHandler, int function_code, u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_route_entry_t	*entryCmd = (cmmd_route_entry_t *) cmd_buf;
	struct route_list *temp, *prev, *newentry;


	cmm_print(DEBUG_INFO, "cmmRouteProcessClientCmd\n");
	
	res_buf[0] = CMMD_ERR_OK;
	*res_len = 2;

	switch (entryCmd->action) {
		case CMMD_EXTROUTE_ACTION_ADD:
			cmm_print(DEBUG_INFO, "cmmRouteProcessClientCmd - EXTROUTE_ACTION_ADD\n");
			newentry = (struct route_list *)calloc(1, sizeof(struct route_list));
			if (!newentry)
			{
				cmm_print(DEBUG_ERROR, "%s:calloc failed for CMMD_EXTROUTE_ACTION_ADD", __func__);
				res_buf[0] = CMMD_ERR_MEMORY;
				break;
			}
			newentry->route = *entryCmd;

			/* route_table needs to be arranged in order of  priority */
			__pthread_mutex_lock(&RouteMutex);
		
			if (!route_table) {
				route_table = newentry;
				__pthread_mutex_unlock(&RouteMutex);
				break;
			}
			
			prev = NULL;
			for (temp = route_table; temp != NULL; temp = temp->next) {
				if (temp->route.prio >= newentry->route.prio) {
					break;
				} 
				prev = temp;
			}
			if (prev) {
				newentry->next = prev->next;
				prev->next = newentry;
			} else {
				newentry->next = route_table;
				route_table = newentry;
			}
			__pthread_mutex_unlock(&RouteMutex);
			break;

		case CMMD_EXTROUTE_ACTION_REMOVE:
			cmm_print(DEBUG_INFO, "cmmRouteProcessClientCmd - EXTROUTE_ACTION_REMOVE\n");
			__pthread_mutex_lock(&RouteMutex);
			temp = route_table;
			if (!temp) {
				__pthread_mutex_unlock(&RouteMutex);
				break;
			}
			prev = NULL;

			while (temp != NULL) {
				if (!memcmp(temp->route.output_device_str, entryCmd->output_device_str, sizeof(entryCmd->output_device_str))
					&& !memcmp(temp->route.dst_addr, entryCmd->dst_addr, sizeof(entryCmd->dst_addr))
					&& !memcmp(temp->route.src_addr, entryCmd->src_addr, sizeof(entryCmd->src_addr))
					&& (temp->route.proto == entryCmd->proto)
					&& !memcmp(temp->route.dst_port, entryCmd->dst_port, sizeof(entryCmd->dst_port))
					&& !memcmp(temp->route.src_port, entryCmd->src_port, sizeof(entryCmd->src_port))) {
					cmm_print(DEBUG_INFO, "An entry has been found to remove\n");
					break;
				} else {
					prev = temp;
					temp = temp->next;
				}
			}
			if (temp == NULL) {
				cmm_print(DEBUG_ERROR,	"An entry have been removed already on localtable or the delete command for same entry\n");
				goto end;
			}
			if (prev == NULL)
				route_table = temp->next;
			else
				prev->next = temp->next;

			free(temp);
end:
			__pthread_mutex_unlock(&RouteMutex);
			break;

		case CMMD_EXTROUTE_ACTION_QUERY:
			cmm_print(DEBUG_INFO, "cmmRouteProcessClientCmd - EXTROUTE_ACTION_QUERY for %s device\n", entryCmd->output_device_str);
			cmmRouteDumpTable(entryCmd->output_device_str);
			break;

		case CMMD_EXTROUTE_ACTION_RESET:
			cmm_print(DEBUG_INFO, "cmmRouteProcessClientCmd - EXTROUTE_ACTION_RESET\n");
			__pthread_mutex_lock(&RouteMutex);
			for (temp = route_table; temp != NULL; temp = route_table) {
				route_table = temp->next;
				free(temp);
			}
			__pthread_mutex_unlock(&RouteMutex);
			break;

		default:
			res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
			break;
	}

	return 0;
}

struct RtEntry *cmmPolicyRouting(unsigned int srcip, unsigned int dstip, unsigned short proto, unsigned short sport, unsigned short dport)
{
	struct route_list *temp;
	char saddr_buf[INET_ADDRSTRLEN], daddr_buf[INET_ADDRSTRLEN];
	struct RtEntry *route = NULL;
	fpp_rt_cmd_t rtCmd;
	int key;

	cmm_print(DEBUG_INFO, "%s: srcip=%s, dstip=%s, proto=%d, sport=%d, dport=%d\n", __func__,
					inet_ntop(AF_INET, &srcip, saddr_buf, sizeof(saddr_buf)),
					inet_ntop(AF_INET, &dstip, daddr_buf, sizeof(daddr_buf)),
					proto, ntohs(sport), ntohs(dport));

	__pthread_mutex_lock(&RouteMutex);

	rtCmd.output_device[0] = '\0';
	for (temp = route_table; temp != NULL; temp = temp->next) {
		if (temp->route.dst_addr[0]) {
			if ((ntohl(temp->route.dst_addr[0]) > ntohl(dstip)) || (ntohl(temp->route.dst_addr[1]) < ntohl(dstip))) {
				continue;
			}
		}
		if (temp->route.src_addr[0]) {
			if ((ntohl(temp->route.src_addr[0]) > ntohl(srcip)) || (ntohl(temp->route.src_addr[1]) < ntohl(srcip))) {
				continue;
			}
		}
		if (temp->route.input_device_str[0]) {
			// look up the input interface if we don't already know it
			if (rtCmd.output_device[0] == '\0') {
				struct RtEntry *input_route;
				struct flow flow = {
							.family = AF_INET,
							.sAddr = &dstip,
							.dAddr = &srcip,
						};

				input_route = __cmmRouteFind(&flow);  // reverse src and dest addrs for input interface
				if (input_route)
				{
					__itf_get_name(input_route->oifindex, rtCmd.output_device, sizeof(rtCmd.output_device) - 1);
					cmm_print(DEBUG_INFO, "cmmPolicyRouting: ingress interface=%s\n", rtCmd.output_device);
				}
				else
				{
					cmm_print(DEBUG_WARNING, "cmmPolicyRouting: no ingress interface found\n");
					continue;
				}
			}
			if (strcmp(rtCmd.output_device, temp->route.input_device_str) != 0) {
				continue;
			}
		}
		if (temp->route.proto) {
			if (temp->route.proto != proto) {
				continue;
			}
		}
		if (temp->route.dst_port[0]) {
			if ((temp->route.dst_port[0] > ntohs(dport)) || (temp->route.dst_port[1] < ntohs(dport))) {
				continue;
			}
		}
		if (temp->route.src_port[0]) {
			if ((temp->route.src_port[0] > ntohs(sport)) || (temp->route.src_port[1] < ntohs(sport))) {
				continue;
			}
		}
		break;
	}

	if (!temp)
		goto out;

	cmm_print(DEBUG_INFO, "%s: route found\n", __func__);
	route = malloc(sizeof(struct RtEntry));
	if (!route)
	{
		cmm_print(DEBUG_ERROR, "%s: malloc() failed\n", __func__);
		goto out;
	}
	memset(route, 0, sizeof(struct RtEntry));

	route->count++;

	route->family = AF_INET;
	route->type = RTN_UNICAST;

	route->sAddr[0] = srcip;
	route->sAddrLen = 4;
	route->dAddr[0] = dstip;
	route->dAddrLen = 4;
	route->gwAddr[0] = dstip;
	route->gwAddrLen = 4;

	route->oifindex = if_nametoindex(temp->route.output_device_str);
	if (!route->oifindex)
		cmm_print(DEBUG_ERROR, "%s: route interface name is not valid\n", __func__);

	route->mtu = temp->route.mtu ? : __itf_get_mtu(route->oifindex);

	route->flags |= RT_POLICY;

	key = HASH_NEIGHBOR(route->family, route->gwAddr);

	list_add(&rt_table_by_gw_ip[key], &route->list_by_gw_ip);

out:
	__pthread_mutex_unlock(&RouteMutex);

	return route;
}
