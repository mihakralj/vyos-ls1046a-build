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


#include <net/if.h>
#include <linux/if_vlan.h>
#include <linux/sockios.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <ctype.h>

#include "libcmm.h"
#include "fpp.h"
#include "cmm.h"
#include "itf.h"
#include "module_icc.h"


static void print_error(u_int32_t rc)
{
	switch (rc)
	{
	case FPP_ERR_ICC_TOO_MANY_ENTRIES:
		cmm_print(DEBUG_ERROR,"Error: Too many entries\n");
		break;
	case FPP_ERR_ICC_ENTRY_ALREADY_EXISTS:
		cmm_print(DEBUG_ERROR,"Error: Entry already exists\n");
		break;
	case FPP_ERR_ICC_ENTRY_NOT_FOUND:
		cmm_print(DEBUG_ERROR,"Error: Entry not found\n");
		break;
	case FPP_ERR_ICC_THRESHOLD_OUT_OF_RANGE:
		cmm_print(DEBUG_ERROR,"Error: Threshold value out of range\n");
		break;
	default:
		cmm_print(DEBUG_ERROR,"Error from CMM, error = `%d'\n", rc);
		break;
	}
}

int IccReset(daemon_handle_t daemon_handle, int argc, char *argv[])
{

        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_icc_reset_cmd_t 	reset_cmd;
	
	if (argc != 0)
		goto usage;
		
	memset(&cmd, 0, sizeof(cmd));
        memset(&res, 0, sizeof(res));
	memset(&reset_cmd, 0, sizeof(reset_cmd));
	
	cmd.func 	= FPP_CMD_ICC_RESET;
	cmd.length 	= sizeof(reset_cmd);
	memcpy(&cmd.buf, &reset_cmd, sizeof(reset_cmd));
	if (cmm_send(daemon_handle, &cmd, 0) != 0) {
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (cmm_recv(daemon_handle, &res, 0) < 0) {
                cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (res.rc != FPP_ERR_OK) {
		print_error(res.rc);
                return -1;
        }
	
        return CLI_OK;
usage:
	cmm_print(DEBUG_ERROR, "Usage: icc reset\n");
        return -1;
}

int IccThreshold(daemon_handle_t daemon_handle, int argc, char *argv[])
{
        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_icc_threshold_cmd_t thresh_cmd;
	u_int32_t val1, val2;

	if (argc != 2)
		goto usage;

        memset(&cmd, 0, sizeof(cmd));
        memset(&res, 0, sizeof(res));
        memset(&thresh_cmd, 0, sizeof(thresh_cmd));

	if (parse_value(argv[0], &val1, 1024) < 0)
	{
		cmm_print(DEBUG_ERROR, "ERROR: Invalid bmu1 threshold: %s\n", argv[0]);
		goto usage;
	}
	if (parse_value(argv[1], &val2, 1024) < 0)
	{
		cmm_print(DEBUG_ERROR, "ERROR: Invalid bmu2 threshold: %s\n", argv[1]);
		goto usage;
	}

	thresh_cmd.bmu1_threshold = val1;
	thresh_cmd.bmu2_threshold = val2;
        
        cmd.func        = FPP_CMD_ICC_THRESHOLD;
        cmd.length      = sizeof(fpp_icc_threshold_cmd_t);
	memcpy(&cmd.buf, &thresh_cmd, sizeof(thresh_cmd));

        if (cmm_send(daemon_handle, &cmd, 0) != 0) {
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (cmm_recv(daemon_handle, &res, 0) < 0) {
                cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (res.rc != FPP_ERR_OK) {
		print_error(res.rc);
                return -1;
        }

        return CLI_OK;
usage:
	cmm_print(DEBUG_ERROR, "Usage: icc threshold <bmu1-thresh> <bmu2-thresh>\n");
        return CLI_OK;

}

static int icc_add_delete(daemon_handle_t daemon_handle, int argc, char *argv[], u_int32_t action)
{
	int i;
        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_icc_add_delete_cmd_t ad_cmd;
	unsigned short interface;
	u_int32_t val1, val2;
	int arg = 2;

	if (argc < 3)
		goto usage;

        memset(&cmd, 0, sizeof(cmd));
        memset(&res, 0, sizeof(res));
        memset(&ad_cmd, 0, sizeof(ad_cmd));
	ad_cmd.action = action;

	if (parse_icc_interface(argv[0], &interface, ICC_NUM_INTERFACES) < 0)
	{
		cmm_print(DEBUG_ERROR, "ERROR: Invalid interface: %s\n", argv[0]);
		goto usage;
	}
	ad_cmd.interface = interface;

	if (strcasecmp(argv[1], "ethertype") == 0)
	{
		if (argc != 3)
			goto usage;
		ad_cmd.table_type = ICC_TABLETYPE_ETHERTYPE;
		if (parse_value(argv[2], &val1, 0xffff) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid ethertype: %s\n", argv[2]);
			goto usage;
		}
		ad_cmd.ethertype.type = val1;
	}
	else if (strcasecmp(argv[1], "protocol") == 0)
	{
		ad_cmd.table_type = ICC_TABLETYPE_PROTOCOL;
		while (argv[arg])
		{
			if (parse_range(argv[arg], &val1, &val2, 255) < 0)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid protocol: %s\n", argv[arg]);
				goto usage;
			}
			for (i = val1; i <= val2; i++)
				setbit_in_array(ad_cmd.protocol.ipproto, i, 1);
			arg++;
		}
	}
	else if (strcasecmp(argv[1], "dscp") == 0)
	{
		ad_cmd.table_type = ICC_TABLETYPE_DSCP;
		while (argv[arg])
		{
			if (parse_range(argv[arg], &val1, &val2, 63) < 0)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid dscp value: %s\n", argv[arg]);
				goto usage;
			}
			for (i = val1; i <= val2; i++)
				setbit_in_array(ad_cmd.dscp.dscp_value, i, 1);
			arg++;
		}
	}
	else if (strcasecmp(argv[1], "saddr") == 0 || strcasecmp(argv[1], "daddr") == 0)
	{
		if (argc > 4)
			goto usage;
		ad_cmd.table_type = strcasecmp(argv[1], "saddr") == 0 ? ICC_TABLETYPE_SADDR : ICC_TABLETYPE_DADDR;
		if (inet_pton(AF_INET, argv[2], &ad_cmd.ipaddr.v4_addr) != 1)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid IP address: %s\n", argv[2]);
			goto usage;
		}
		if (argc == 4)
		{
			if (parse_value(argv[3], &val1, 32) < 0 || val1 == 0)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid mask length: %s\n", argv[3]);
				goto usage;
			}
			ad_cmd.ipaddr.v4_masklen = val1;
		}
		else
		{
			ad_cmd.ipaddr.v4_masklen = 32;
		}
	}
	else if (strcasecmp(argv[1], "saddr6") == 0 || strcasecmp(argv[1], "daddr6") == 0)
	{
		if (argc > 4)
			goto usage;
		ad_cmd.table_type = strcasecmp(argv[1], "saddr6") == 0 ? ICC_TABLETYPE_SADDR6 : ICC_TABLETYPE_DADDR6;
		if (inet_pton(AF_INET6, argv[2], ad_cmd.ipv6addr.v6_addr) != 1)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid IPv6 address: %s\n", argv[2]);
			goto usage;
		}
		if (argc == 4)
		{
			if (parse_value(argv[3], &val1, 128) < 0 || val1 == 0)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid mask length: %s\n", argv[3]);
				goto usage;
			}
			ad_cmd.ipv6addr.v6_masklen = val1;
		}
		else
		{
			ad_cmd.ipv6addr.v6_masklen = 128;
		}
	}
	else if (strcasecmp(argv[1], "port") == 0)
	{
		if (argc != 4)
			goto usage;
		ad_cmd.table_type = ICC_TABLETYPE_PORT;
		if (parse_range(argv[2], &val1, &val2, 0xffff) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid source port: %s\n", argv[2]);
			goto usage;
		}
		ad_cmd.port.sport_from = val1;
		ad_cmd.port.sport_to = val2;
		if (parse_range(argv[3], &val1, &val2, 0xffff) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid destination port: %s\n", argv[3]);
			goto usage;
		}
		ad_cmd.port.dport_from = val1;
		ad_cmd.port.dport_to = val2;
	}
	else if (strcasecmp(argv[1], "sport") == 0)
	{
		if (argc != 3)
			goto usage;
		ad_cmd.table_type = ICC_TABLETYPE_PORT;
		if (parse_range(argv[2], &val1, &val2, 0xffff) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid source port: %s\n", argv[2]);
			goto usage;
		}
		ad_cmd.port.sport_from = val1;
		ad_cmd.port.sport_to = val2;
		ad_cmd.port.dport_from = 0;
		ad_cmd.port.dport_to = 65535;
	}
	else if (strcasecmp(argv[1], "dport") == 0)
	{
		if (argc != 3)
			goto usage;
		ad_cmd.table_type = ICC_TABLETYPE_PORT;
		if (parse_range(argv[2], &val1, &val2, 0xffff) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid destination port: %s\n", argv[2]);
			goto usage;
		}
		ad_cmd.port.dport_from = val1;
		ad_cmd.port.dport_to = val2;
		ad_cmd.port.sport_from = 0;
		ad_cmd.port.sport_to = 65535;
	}
	else if (strcasecmp(argv[1], "vlan") == 0)
	{
		ad_cmd.table_type = ICC_TABLETYPE_VLAN;
		if (argc > 4)
			goto usage;
		if (parse_range(argv[2], &val1, &val2, 8191) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid vlan ID: %s\n", argv[2]);
			goto usage;
		}
		ad_cmd.vlan.vlan_from = val1;
		ad_cmd.vlan.vlan_to = val2;
		if (argc == 4)
		{
			if (parse_range(argv[3], &val1, &val2, 7) < 0)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid vlan priority: %s\n", argv[3]);
				goto usage;
			}
		}
		else
		{
			val1 = 0;
			val2 = 7;
		}
		ad_cmd.vlan.prio_from = val1;
		ad_cmd.vlan.prio_to = val2;
	}
	else
	{
		cmm_print(DEBUG_ERROR, "ERROR: Unknown keyword %s\n", argv[1]);
		goto usage;
	}

        cmd.func        = FPP_CMD_ICC_ADD_DELETE;
        cmd.length      = sizeof(fpp_icc_add_delete_cmd_t);
	memcpy(&cmd.buf, &ad_cmd, sizeof(ad_cmd));

        if (cmm_send(daemon_handle, &cmd, 0) != 0) {
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (cmm_recv(daemon_handle, &res, 0) < 0) {
                cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (res.rc != FPP_ERR_OK) {
		print_error(res.rc);
                return -1;
        }

        return CLI_OK;

usage:
	cmm_print(DEBUG_ERROR, "Usage: icc [add | delete] <interface>\n"
					"\t[\n"
					"\t\t[ethertype <type>] |\n"
					"\t\t[protocol <proto-1> <proto-2> ...] |\n"
					"\t\t[dscp <value-1> <value-2> ...] |\n"
					"\t\t[saddr <addr> [<masklen>]] |\n"
					"\t\t[daddr <addr> [<masklen>]] |\n"
					"\t\t[saddr6 <addr> [<masklen>]] |\n"
					"\t\t[daddr6 <addr> [<masklen>]] |\n"
					"\t\t[port <srcport> <dstport>] |\n"
					"\t\t[sport <srcport>] |\n"
					"\t\t[dport <dstport>] |\n"
					"\t\t[vlan <id> [<priority>]] |\n"
					"\t]\n");
        return CLI_OK;
}

int IccAdd(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	return icc_add_delete(daemon_handle, argc, argv, ICC_ACTION_ADD);
}

int IccDelete(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	return icc_add_delete(daemon_handle, argc, argv, ICC_ACTION_DELETE);
}

int IccQuery(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	int i, j;
        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_icc_query_cmd_t query_cmd;
        fpp_icc_query_reply_t *prsp = (fpp_icc_query_reply_t *)&res.buf[0];
	u_int16_t interface;
	u_int16_t interface_from, interface_to;
	u_int16_t action;
	char buf[1024];

	if (argc > 1)
		goto usage;

        memset(&cmd, 0, sizeof(cmd));
        memset(&res, 0, sizeof(res));
        memset(&query_cmd, 0, sizeof(query_cmd));

	if (argc == 1)
	{
		if (parse_icc_interface(argv[0], &interface_from, ICC_NUM_INTERFACES) < 0)
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid interface: %s\n", argv[0]);
			goto usage;
		}
		interface_to = interface_from;
	}
	else
	{
		interface_from = 0;
		interface_to = ICC_NUM_INTERFACES - 1;
	}

	for (interface = interface_from; interface <= interface_to; interface++)
	{
		cmm_print(DEBUG_STDOUT, "ICC interface %d --\n", interface);
		cmd.func = FPP_CMD_ICC_QUERY;
		cmd.length = sizeof(fpp_icc_query_cmd_t);
		action = ICC_ACTION_QUERY;
		while (1)
		{
			query_cmd.action = action;
			query_cmd.interface = interface;
			memcpy(&cmd.buf, &query_cmd, sizeof(query_cmd));
			if (cmm_send(daemon_handle, &cmd, 0) != 0) {
				cmm_print(DEBUG_ERROR, "Error sending message to CMM, error = `%s'\n", strerror(errno));
				return -1;
			}
			if (cmm_recv(daemon_handle, &res, 0) < 0) {
				cmm_print(DEBUG_ERROR, "Error receiving message from CMM, error = `%s'\n", strerror(errno));
				return -1;
			}
			if (prsp->query_result != 0 || prsp->interface != interface)
				break;
			switch (prsp->table_type)
			{
				case ICC_TABLETYPE_ETHERTYPE:
				{
					cmm_print(DEBUG_STDOUT, "Ethertype: 0x%04x\n", prsp->ethertype.type);
					break;
				}
				case ICC_TABLETYPE_PROTOCOL:
				{
					buf[0] = '\0';
					for (i = 0; i < 256; i++)
					{
						if (!testbit_in_array(prsp->protocol.ipproto, i))
							continue;
						j = i + 1;
						if (j == 256 || !testbit_in_array(prsp->protocol.ipproto, j))
						{
							sprintf(buf + strlen(buf), "%d ", i);
						}
						else
						{
							while (j < 255 && testbit_in_array(prsp->protocol.ipproto, j + 1))
								j++;
							sprintf(buf + strlen(buf), "%d-%d ", i, j);
						}
						i = j;
					}
					cmm_print(DEBUG_STDOUT, "Protocols: %s\n", buf);
					break;
				}
				case ICC_TABLETYPE_DSCP:
				{
					buf[0] = '\0';
					for (i = 0; i < 64; i++)
					{
						if (!testbit_in_array(prsp->dscp.dscp_value, i))
							continue;
						j = i + 1;
						if (j == 64 || !testbit_in_array(prsp->dscp.dscp_value, j))
						{
							sprintf(buf + strlen(buf), "%d ", i);
						}
						else
						{
							while (j < 63 && testbit_in_array(prsp->dscp.dscp_value, j + 1))
								j++;
							sprintf(buf + strlen(buf), "%d-%d ", i, j);
						}
						i = j;
					}
					cmm_print(DEBUG_STDOUT, "DSCP values: %s\n", buf);
					break;
				}
				case ICC_TABLETYPE_SADDR:
				{
					cmm_print(DEBUG_STDOUT, "IPv4 Source Address: %s/%d\n",
							inet_ntop(AF_INET, &prsp->ipaddr.v4_addr, buf, sizeof(buf)),
							prsp->ipaddr.v4_masklen);
					break;
				}
				case ICC_TABLETYPE_DADDR:
				{
					cmm_print(DEBUG_STDOUT, "IPv4 Destination Address: %s/%d\n",
							inet_ntop(AF_INET, &prsp->ipaddr.v4_addr, buf, sizeof(buf)),
							prsp->ipaddr.v4_masklen);
					break;
				}
				case ICC_TABLETYPE_SADDR6:
				{
					cmm_print(DEBUG_STDOUT, "IPv6 Source Address: %s/%d\n",
							inet_ntop(AF_INET6, prsp->ipv6addr.v6_addr, buf, sizeof(buf)),
							prsp->ipv6addr.v6_masklen);
					break;
				}
				case ICC_TABLETYPE_DADDR6:
				{
					cmm_print(DEBUG_STDOUT, "IPv6 Destination Address: %s/%d\n",
							inet_ntop(AF_INET6, prsp->ipv6addr.v6_addr, buf, sizeof(buf)),
							prsp->ipv6addr.v6_masklen);
					break;
				}
				case ICC_TABLETYPE_PORT:
				{
					cmm_print(DEBUG_STDOUT, "Ports: Source %d-%d / Destination %d-%d\n",
							prsp->port.sport_from, prsp->port.sport_to,
							prsp->port.dport_from, prsp->port.dport_to);
					break;
				}
				case ICC_TABLETYPE_VLAN:
				{
					cmm_print(DEBUG_STDOUT, "VLAN: ID %d-%d / Priority %d-%d\n",
							prsp->vlan.vlan_from, prsp->vlan.vlan_to,
							prsp->vlan.prio_from, prsp->vlan.prio_to);
					break;
				}
			}
			action = ICC_ACTION_QUERY_CONT;
		}
		cmm_print(DEBUG_STDOUT, "-------------\n\n");
	}

        return CLI_OK;

usage:
	cmm_print(DEBUG_ERROR, "Usage: icc query [<interface>]\n");
        return CLI_OK;
}
