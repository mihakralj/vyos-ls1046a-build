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
#include <ctype.h>
#include "module_stat.h"

static int parse_interface(char *pstring, unsigned short *pinterface_number);

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmStatShowPrintHelp()
{
	char buf[128];

	print_all_gemac_ports(buf, 128);
#ifndef LS1043
	cmm_print(DEBUG_STDOUT, "Usage: show stat queue {queue_no} interface {%s} query|query_reset\n"
#else
	cmm_print(DEBUG_STDOUT, "Usage: "
#endif
				"       show stat interface {%s} query|query_reset\n"
				"       show stat vlan query|query_reset\n"
				"       show stat connection query\n"
				"       show stat pppoe query|query_reset\n"
				"       show stat bridge query|query_reset\n"
				"       show stat tunnel [name {tnl-name}] query|query_reset\n"
				"       show stat ipsec query|query_reset\n"
#if defined(FLOW_STATS)
				"       show stat flow query|query_reset|reset sip {sip} dip {dip} sport {sport} dport {dport} proto {protonumber}\n"
#endif
#ifndef LS1043
				, buf
#endif
				, buf);
}

#if defined(FLOW_STATS)
int parseFlowQueryCmd(char ** keywords, int *cpt, fpp_stat_flow_status_cmd_t *flowStatusCmd)
{
	unsigned int tmp;
	char * endptr;

	if(!keywords[++*cpt])
		return -1;

	if(strcasecmp(keywords[*cpt], "sip") == 0)
	{
		if(!keywords[++*cpt])
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid source IP address\n");
			return -1;
		}
		if (inet_pton(AF_INET, keywords[*cpt], &flowStatusCmd->Saddr))
		{
			flowStatusCmd->ip_family = 4; /* IPv4 address */
		}
		else if (inet_pton(AF_INET6, keywords[*cpt], flowStatusCmd->Saddr_v6))
		{
			flowStatusCmd->ip_family = 6; /* IPv6 address */
		}
		else
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid source IP address: %s\n", keywords[*cpt]);
			return -1;
		}
	}

	if(!keywords[++*cpt])
		return -1;
	if(strcasecmp(keywords[*cpt], "dip") == 0)
	{
		if(!keywords[++*cpt])
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid destination IP address\n");
			return -1;
		}

		if (inet_pton(AF_INET, keywords[*cpt], &flowStatusCmd->Daddr))
		{
			if (flowStatusCmd->ip_family != 4)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid source and destination ip combination. source is ipv6(%s) and  destination is ipv4(%s)\n", keywords[*cpt-2], keywords[*cpt]);
				return -1;
			}
		}
		else if (inet_pton(AF_INET6, keywords[*cpt], flowStatusCmd->Daddr_v6))
		{
			if (flowStatusCmd->ip_family != 6)
			{
				cmm_print(DEBUG_ERROR, "ERROR: Invalid source and destination ip combination. source is ipv4(%s) and  destination is ipv6(%s)\n", keywords[*cpt-2], keywords[*cpt]);
				return -1;
			}
		}
		else
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid destination IP address: %s\n", keywords[*cpt]);
			return -1;
		}
	}

	if(!keywords[++*cpt])
		return -1;
	if(strcasecmp(keywords[*cpt], "sport") == 0)
	{
		if(!keywords[++*cpt])
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid source port\n");
			return -1;
		}

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[*cpt], &endptr, 0);
		if ((keywords[*cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_ERROR, "ERROR: source port must be a number between 0 and %d\n", USHRT_MAX);
			return -1;
		}
		flowStatusCmd->Sport = htons(tmp);
	}

	if(!keywords[++*cpt])
		return -1;
	if(strcasecmp(keywords[*cpt], "dport") == 0)
	{
		if(!keywords[++*cpt])
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid destination port\n");
			return -1;
		}

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[*cpt], &endptr, 0);
		if ((keywords[*cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_ERROR, "ERROR: destination port must be a number between 0 and %d\n", USHRT_MAX);
			return -1;
		}
		flowStatusCmd->Dport = htons(tmp);
	}

	if(!keywords[++*cpt])
		return -1;
	if(strcasecmp(keywords[*cpt], "proto") == 0)
	{
		if(!keywords[++*cpt])
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid protocol number\n");
			return -1;
		}

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[*cpt], &endptr, 0);
		if ((keywords[*cpt] == endptr) || (tmp > 255))
		{
			cmm_print(DEBUG_ERROR, "ERROR: Invalid protocol number\n");
			return -1;
		}
		flowStatusCmd->Protocol = tmp;
	}

	return 0;
}
#endif

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmStatShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int cmdToSend = 0; /* bits field*/
	int rcvBytes = 0;
        union u_rxbuf rxbuf;
	char *pinterface;
	unsigned short interface;
#ifndef LS1043
	char * endptr;
	unsigned int tmp;
	fpp_stat_queue_cmd_t queueShowCmd;
#endif
	fpp_stat_interface_cmd_t interfaceShowCmd;
	fpp_stat_connection_cmd_t connShowCmd;
	fpp_stat_pppoe_status_cmd_t pppoeStatusCmd;
	fpp_stat_bridge_status_cmd_t bridgeStatusCmd;
	fpp_stat_ipsec_status_cmd_t ipsecStatusCmd;
	fpp_stat_vlan_status_cmd_t vlanStatusCmd;
	fpp_stat_tunnel_status_cmd_t tunnelStatusCmd;
#ifndef LS1043
	fpp_stat_queue_response_t *queueStatRsp;
#endif
	fpp_stat_interface_pkt_response_t *interfacePktStatRsp;
	fpp_stat_conn_response_t *connStatRsp;
#if defined(FLOW_STATS)
	fpp_stat_flow_status_cmd_t flowStatusCmd;
	fpp_stat_flow_entry_response_t *flowStatusResp;
#endif

	uint64_t total_bytes_received_0;
	uint64_t total_bytes_received_1;
	uint64_t total_bytes_received;
	uint64_t total_bytes_transmitted_0;
	uint64_t total_bytes_transmitted_1;
	uint64_t total_bytes_transmitted;

	//goto help
	if(!keywords[cpt])
		goto help;
#ifndef LS1043
	if(strcasecmp(keywords[cpt], "queue") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
		{
			cmm_print(DEBUG_CRIT, "Stat ERROR: Queue Number must be a number between 0 and %d\n", FPP_NUM_QUEUES - 1);
			goto help;
		}
		queueShowCmd.queue = tmp;

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "interface") != 0) 
			goto keyword_error;

		if(!keywords[++cpt])
			goto help;

		pinterface = keywords[cpt];
		if (parse_interface(pinterface, &interface) < 0)
			goto help;

		queueShowCmd.interface = interface;

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0) 
		{
			queueShowCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
		{
			queueShowCmd.action = FPP_CMM_STAT_QUERY_RESET;
		}
		else
			goto keyword_error;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_QUEUE);
	}
#endif
	else if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;
									
		pinterface = keywords[cpt];
		if (parse_interface(pinterface, &interface) < 0)
			goto help;

		interfaceShowCmd.interface = interface;

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0) 
		{
			interfaceShowCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
		{
			interfaceShowCmd.action = FPP_CMM_STAT_QUERY_RESET;
 		}
		else
			goto keyword_error;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_INTERFACE_PKT);
	}
	else if(strcasecmp(keywords[cpt], "connection") == 0)
	{
		if(!keywords[++cpt])
			goto help;
									
		if(strcasecmp(keywords[cpt], "query") != 0) 
			goto keyword_error;
		
		connShowCmd.action = FPP_CMM_STAT_QUERY;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_CONNECTION);
	}
        else if(strcasecmp(keywords[cpt], "pppoe") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0) 
		{
			pppoeStatusCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
		{
			pppoeStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
 		}
		else
			goto keyword_error;

 		cmdToSend |= CMD_BIT(FPP_CMD_STAT_PPPOE_STATUS);
	}
	else if(strcasecmp(keywords[cpt], "bridge") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0) 
		{
			bridgeStatusCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
		{
			bridgeStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
 		}
		else
			goto keyword_error;

 		cmdToSend |= CMD_BIT(FPP_CMD_STAT_BRIDGE_STATUS);
	}
	else if(strcasecmp(keywords[cpt], "ipsec") == 0)
	{
		if(!keywords[++cpt])
			goto help;

 		if((strcasecmp(keywords[cpt], "query") == 0) || (strcasecmp(keywords[cpt], "query_reset") == 0)) {
			if(strcasecmp(keywords[cpt], "query") == 0) 
			{
				ipsecStatusCmd.action = FPP_CMM_STAT_QUERY;
			}
			else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
			{
				ipsecStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
	 		}
			else
				goto keyword_error;

			cmdToSend |= CMD_BIT(FPP_CMD_STAT_IPSEC_STATUS);
		}
		else
		 	goto help;
	}
	else if(strcasecmp(keywords[cpt], "vlan") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0) 
		{
			vlanStatusCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0) 
		{
			vlanStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
 		}
		else
			goto keyword_error;

 		cmdToSend |= CMD_BIT(FPP_CMD_STAT_VLAN_STATUS);
	}	
	else if(strcasecmp(keywords[cpt], "tunnel") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		memset(&tunnelStatusCmd, 0, sizeof(tunnelStatusCmd));
		if(strcasecmp(keywords[cpt], "name") == 0)
		{
			if(!keywords[++cpt])
				goto help;
			strncpy(tunnelStatusCmd.if_name, keywords[cpt], sizeof(tunnelStatusCmd.if_name) - 1);
			if(!keywords[++cpt])
				goto help;
		}
		if(strcasecmp(keywords[cpt], "query") == 0)
		{
			tunnelStatusCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0)
		{
			tunnelStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
		}
		else
			goto keyword_error;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_TUNNEL_STATUS);
	}
#if defined(FLOW_STATS)
	else if(strcasecmp(keywords[cpt], "flow") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "query") == 0)
		{
			flowStatusCmd.action = FPP_CMM_STAT_QUERY;
		}
		else if(strcasecmp(keywords[cpt], "query_reset") == 0)
		{
			flowStatusCmd.action = FPP_CMM_STAT_QUERY_RESET;
		}
		else
			goto keyword_error;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_FLOW);
		if (parseFlowQueryCmd(keywords, &cpt, &flowStatusCmd) != 0)
		{
			goto keyword_error;
		}
	}
#endif
	else
		goto keyword_error;
#ifndef LS1043
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_QUEUE))
	{
		/* Send CMD_STAT_QUEUE command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_QUEUE, & queueShowCmd, sizeof(queueShowCmd), rxbuf.rcvBuffer);

		if ( rcvBytes != sizeof(fpp_stat_queue_response_t))
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected returned result from FPP rc:%04x\n",
						  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result);
  			goto exit;
                }
		else
		{
			queueStatRsp = (fpp_stat_queue_response_t *)(rxbuf.rcvBuffer);
			cmm_print(DEBUG_STDOUT, "Emitted Pkts: 0x%0x \n" "Dropped Packets: 0x%0x\n" "Peak Queue Occupancy: 0x%0x \n",
						queueStatRsp->emitted_pkts, queueStatRsp->dropped_pkts, queueStatRsp->peak_queue_occ);
		}
	}
#endif

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_INTERFACE_PKT))
	{
		/* Send CMD_STAT_INTERFACE_PKT command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_INTERFACE_PKT, & interfaceShowCmd, sizeof(interfaceShowCmd), rxbuf.rcvBuffer);

		if ( rcvBytes != sizeof(fpp_stat_interface_pkt_response_t)  )
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected returned result from FPP rc:%04x\n",
					  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result);
			goto exit;
		}
		else
		{
			interfacePktStatRsp = (fpp_stat_interface_pkt_response_t *)(rxbuf.rcvBuffer);

			total_bytes_transmitted_0 = interfacePktStatRsp->total_bytes_transmitted[0];
			total_bytes_transmitted_1 = interfacePktStatRsp->total_bytes_transmitted[1];
			total_bytes_transmitted_1 <<= 32;

			total_bytes_transmitted = total_bytes_transmitted_0 | total_bytes_transmitted_1;

			total_bytes_received_0 = interfacePktStatRsp->total_bytes_received[0];
			total_bytes_received_1 = interfacePktStatRsp->total_bytes_received[1];
			total_bytes_received_1 <<= 32;

			total_bytes_received = total_bytes_received_0 | total_bytes_received_1;

			cmm_print(DEBUG_STDOUT, "Total Bytes Received: %" PRIu64 " \n"
						"Total Bytes Transmitted: %" PRIu64 " \n"
						"Total Packets Received: %u \n"
						"Total Packets Transmitted: %u \n",
						total_bytes_received, total_bytes_transmitted, 
						interfacePktStatRsp->total_pkts_received, interfacePktStatRsp->total_pkts_transmitted);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_CONNECTION))
	{
		/* Send CMD_STAT_CONNECTION command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_CONNECTION, & connShowCmd, sizeof(connShowCmd), rxbuf.rcvBuffer);

		if ( rcvBytes != sizeof(fpp_stat_conn_response_t) )
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected returned result from FPP rc:%04x\n",
					  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result);
  			goto exit;
		}
		else
		{
			connStatRsp = (fpp_stat_conn_response_t *)(rxbuf.rcvBuffer);
#if !defined(COMCERTO_2000)
			cmm_print(DEBUG_STDOUT, "Maximum Active Connections: %u \n"
						"Number of Active Connections: %u \n",
				nf_conntrack_max, connStatRsp->num_active_connections);
#else
			cmm_print(DEBUG_STDOUT, "Maximum connections supported in FE: %u \n"
						"Maximum Active Connections: %s \n"
						"Number of Active Connections: %u \n"
						"Configured FE DDR Memory available for connections: %s \n"
						"FE DDR Memory used by connections: %s \n",
				connStatRsp->max_active_connections,
				"N/A",
				connStatRsp->num_active_connections,
				"N/A",
				"N/A");
#endif
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_PPPOE_STATUS))
	{
		int count = 0;
		char interface[IFNAMSIZ];
		fpp_stat_pppoe_entry_response_t *pEntryResponse = (fpp_stat_pppoe_entry_response_t *)rxbuf.rcvBuffer;
		/* Send CMD_STAT_PPPOE_STATUS command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_PPPOE_STATUS, &pppoeStatusCmd, sizeof(pppoeStatusCmd),rxbuf.rcvBuffer);
		if (rcvBytes != 2)
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  );
			goto exit;
		}

		while (1)
		{
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_PPPOE_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_stat_pppoe_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  	(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  	);
				goto exit;
			}
			if (pEntryResponse->eof)
			    	break;

			get_port_name(pEntryResponse->interface_no, interface, IFNAMSIZ);

			cmm_print(DEBUG_STDOUT, "Session ID = 0x%0x "
			                        "Physical Interface=%-6s "
						"Packets Received = 0x%0x "
						"Packets Transmitted = 0x%0x\n",
						    pEntryResponse->sessionid,
						    interface,
						    pEntryResponse->total_packets_received,
						    pEntryResponse->total_packets_transmitted);
			count++;
		}
		cmm_print(DEBUG_STDOUT, "\n Statistics of %d PPPoE Sessions displayed \n", count);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_BRIDGE_STATUS))
	{
		int count = 0;
		char input_interface[IFNAMSIZ];
		char output_interface[IFNAMSIZ];
		fpp_stat_bridge_entry_response_t *pEntryResponse = (fpp_stat_bridge_entry_response_t *)rxbuf.rcvBuffer;
		/* Send CMD_STAT_BRIDGE_STATUS command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_BRIDGE_STATUS, &bridgeStatusCmd, sizeof(bridgeStatusCmd), rxbuf.rcvBuffer);
		if (rcvBytes != 2)
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  );
			goto exit;
		}

		while (1)
		{
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_BRIDGE_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_stat_bridge_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  	(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
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

			cmm_print(DEBUG_STDOUT, "Input=%-6s "
				                "DA=%02X:%02X:%02X:%02X:%02X:%02X "
				                "SA=%02X:%02X:%02X:%02X:%02X:%02X "
						"Type=%04X "
						"Output=%s "
						"SessionId=%d "
						"Packets Transmitted = 0x%0x\n",
						    input_interface,
						    pEntryResponse->dst_mac[0], pEntryResponse->dst_mac[1], pEntryResponse->dst_mac[2],
						    pEntryResponse->dst_mac[3], pEntryResponse->dst_mac[4], pEntryResponse->dst_mac[5],
						    pEntryResponse->src_mac[0], pEntryResponse->src_mac[1], pEntryResponse->src_mac[2],
						    pEntryResponse->src_mac[3], pEntryResponse->src_mac[4], pEntryResponse->src_mac[5],
						    pEntryResponse->ether_type, output_interface,pEntryResponse->session_id,
						    pEntryResponse->total_packets_transmitted);
			count++;
		}
		cmm_print(DEBUG_STDOUT, "\n Statistics of %d Bridge Table Entries displayed \n", count);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_IPSEC_STATUS))
	{
		int count = 0;
		fpp_stat_ipsec_entry_response_t *pEntryResponse = (fpp_stat_ipsec_entry_response_t *)rxbuf.rcvBuffer;
		/* Send CMD_STAT_IPSEC_STATUS command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_IPSEC_STATUS, &ipsecStatusCmd, sizeof(ipsecStatusCmd),rxbuf.rcvBuffer);
		if (rcvBytes != 2)
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  );
			goto exit;
		}

		while (1)
		{
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_IPSEC_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_stat_ipsec_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  	(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  	);
				goto exit;
			}
			if (pEntryResponse->eof)
			    	break;

			total_bytes_transmitted_0 = pEntryResponse->total_bytes_processed[0];
			total_bytes_transmitted_1 = pEntryResponse->total_bytes_processed[1];
			total_bytes_transmitted_1 <<= 32;

			total_bytes_transmitted = total_bytes_transmitted_0 | total_bytes_transmitted_1;

			cmm_print(DEBUG_STDOUT, "\nSPI: %x \n",  pEntryResponse->spi);
			if(pEntryResponse->family == FPP_PROTO_IPV4){
				cmm_print(DEBUG_STDOUT, "Dst IP: %d.%d.%d.%d \n", (pEntryResponse->dst_ip[0] & 0x000000ff), 
						    				((pEntryResponse->dst_ip[0] >> 8) & 0x000000ff),
						    				((pEntryResponse->dst_ip[0] >> 16) & 0x000000ff),
						    				((pEntryResponse->dst_ip[0] >> 24) & 0x000000ff));
			}
			else{
				char addr[64];
			
				inet_ntop(AF_INET6, pEntryResponse->dst_ip, addr, sizeof(addr));
				cmm_print(DEBUG_STDOUT,"Dst IP: %s\n", addr);
			}
			cmm_print(DEBUG_STDOUT, "IPsec proto: %d \n",  pEntryResponse->proto);
			cmm_print(DEBUG_STDOUT, "Total Packets Processed: %u \n"
						"Total Bytes Processed: %" PRIu64 " \n \n",
						 pEntryResponse->total_pkts_processed, total_bytes_transmitted);
			count++;
		}
		cmm_print(DEBUG_STDOUT, "\n Statistics of %d SA Entries displayed \n", count);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_VLAN_STATUS))
	{
		int count = 0;
		fpp_stat_vlan_entry_response_t *pEntryResponse = (fpp_stat_vlan_entry_response_t *)rxbuf.rcvBuffer;
		/* Send FPP_CMD_STAT_VLAN_STATUS command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_VLAN_STATUS, &vlanStatusCmd, sizeof(vlanStatusCmd), rxbuf.rcvBuffer);
		if (rcvBytes != 2)
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  );
			goto exit;
		}

		while (1)
		{
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_VLAN_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_stat_vlan_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  	(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result 
			  	);
				goto exit;
			}
			if (pEntryResponse->eof)
			    	break;

			total_bytes_transmitted_0 = pEntryResponse->total_bytes_transmitted[0];
			total_bytes_transmitted_1 = pEntryResponse->total_bytes_transmitted[1];
			total_bytes_transmitted_1 <<= 32;

			total_bytes_transmitted = total_bytes_transmitted_0 | total_bytes_transmitted_1;

			total_bytes_received_0 = pEntryResponse->total_bytes_received[0];
			total_bytes_received_1 = pEntryResponse->total_bytes_received[1];
			total_bytes_received_1 <<= 32;

			total_bytes_received = total_bytes_received_0 | total_bytes_received_1;
			
			cmm_print(DEBUG_STDOUT, "===========================================\n");
			cmm_print(DEBUG_STDOUT, "VLAN ID = %4d\n"
			                        "VLAN Interface Name = %s\n"
			                        "VLAN Physical Interface Name = %s\n"
						"Packets Received =\t%u\n"
						"Packets Transmitted =\t%u\n"
						"Bytes Received =\t%" PRIu64 "\n"
						"Bytes Transmitted =\t%" PRIu64 "\n",
						    pEntryResponse->vlanID,
						    pEntryResponse->vlanifname,
						    pEntryResponse->phyifname,						    
						    pEntryResponse->total_packets_received,
						    pEntryResponse->total_packets_transmitted, total_bytes_received, total_bytes_transmitted);

			count++;
		}
		if (count > 1)
			cmm_print(DEBUG_STDOUT, "\n Statistics of %d VLAN Entries displayed \n", count);
	}
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_TUNNEL_STATUS))
	{
		int count = 0;
		fpp_stat_tunnel_entry_response_t *pEntryResponse = (fpp_stat_tunnel_entry_response_t *)rxbuf.rcvBuffer;
		/* Send FPP_CMD_STAT_TUNNEL_STATUS command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_TUNNEL_STATUS, &tunnelStatusCmd, sizeof(tunnelStatusCmd), rxbuf.rcvBuffer);
		if (rcvBytes != 2)
		{
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
				  (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result);
			goto exit;
		}

		while (1)
		{
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_TUNNEL_ENTRY, NULL, 0, rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_stat_tunnel_entry_response_t))
			{
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",
					(rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result);
				goto exit;
			}
			if (pEntryResponse->eof)
				break;

			total_bytes_transmitted_0 = pEntryResponse->total_bytes_transmitted[0];
			total_bytes_transmitted_1 = pEntryResponse->total_bytes_transmitted[1];
			total_bytes_transmitted_1 <<= 32;

			total_bytes_transmitted = total_bytes_transmitted_0 | total_bytes_transmitted_1;

			total_bytes_received_0 = pEntryResponse->total_bytes_received[0];
			total_bytes_received_1 = pEntryResponse->total_bytes_received[1];
			total_bytes_received_1 <<= 32;

			total_bytes_received = total_bytes_received_0 | total_bytes_received_1;

			cmm_print(DEBUG_STDOUT, "Tunnel Name = %s, "
						"Packets Received = %u, "
						"Packets Transmitted = %u, "
						"Bytes Received = %" PRIu64 ", "
						"Bytes Transmitted = %" PRIu64 "\n",
						    pEntryResponse->if_name,
						    pEntryResponse->total_packets_received,
						    pEntryResponse->total_packets_transmitted, total_bytes_received, total_bytes_transmitted);

			count++;
		}
		if (count > 1)
			cmm_print(DEBUG_STDOUT, "\n Statistics of %d Tunnel Entries displayed \n", count);
	}
#if defined(FLOW_STATS)
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_FLOW))
	{
		/* Send CMD_STAT_FLOW command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_FLOW, &flowStatusCmd, sizeof(flowStatusCmd), rxbuf.rcvBuffer);

		if (rcvBytes != sizeof(fpp_stat_flow_entry_response_t))
		{
			unsigned short ackstatus;
			ackstatus = rcvBytes < sizeof(unsigned short) ? 0 : rxbuf.result;
			if (ackstatus == FPP_ERR_FLOW_ENTRY_NOT_FOUND)
				cmm_print(DEBUG_STDERR, "ERROR: Flow entry not found\n");
			else
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected returned result from FPP rc:%04x rcvBytes <%x>\n",
						ackstatus,rcvBytes);
			goto exit;
		}
		else
		{
			flowStatusResp = (fpp_stat_flow_entry_response_t *)(rxbuf.rcvBuffer);

			cmm_print(DEBUG_STDOUT, "Total Packets: %" PRIu64 "\n"
						"Total Bytes  : %" PRIu64 "\n",
						flowStatusResp->TotalPackets, flowStatusResp->TotalBytes);
		}
	}
#endif


	return 0;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmStatShowPrintHelp();

exit:
	return -1;

}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmStatSetPrintHelp(int cmd_type)
{
	char buf[128];

	print_all_gemac_ports(buf, 128);
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_ENABLE_CMD)
	{
#ifndef LS1043
#if defined(FLOW_STATS)
	    cmm_print(DEBUG_STDOUT, "Usage: set stat enable {queue|interface|vlan|pppoe|bridge|ipsec|tunnel|flow}\n"
		                     "      set stat disable {queue|interface|vlan|pppoe|bridge|ipsec|tunnel|flow}\n");
#else
	    cmm_print(DEBUG_STDOUT, "Usage: set stat enable {queue|interface|vlan|pppoe|bridge|ipsec|tunnel}\n"
		                     "      set stat disable {queue|interface|vlan|pppoe|bridge|ipsec|tunnel}\n");
#endif
#else
#if defined(FLOW_STATS)
	    cmm_print(DEBUG_STDOUT, "Usage: set stat enable {interface|vlan|pppoe|bridge|ipsec|tunnel|flow}\n"
		                     "      set stat disable {interface|vlan|pppoe|bridge|ipsec|tunnel|flow}\n");
#else
	    cmm_print(DEBUG_STDOUT, "Usage: set stat enable {interface|vlan|pppoe|bridge|ipsec|tunnel}\n"
		                     "      set stat disable {interface|vlan|pppoe|bridge|ipsec|tunnel}\n");
#endif
#endif
	}
#ifndef LS1043
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_QUEUE_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                      "Usage: set stat queue {queue_no} interface {%s} reset \n", buf);
      	}
#endif
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_INTERFACE_PKT_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                      "Usage: set stat interface {%s} reset\n", buf);
      	}
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_VLAN_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set stat vlan reset\n");
	}      	
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_PPPOE_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set stat pppoe reset\n");
	}
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_BRIDGE_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set stat bridge reset\n");
	}
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_IPSEC_CMD)
	{
	    cmm_print(DEBUG_STDOUT, 
                  "Usage: set stat ipsec reset\n");
	}
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_TUNNEL_CMD)
	{
	    cmm_print(DEBUG_STDOUT, "Usage: set stat tunnel reset\n");
	}
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_CONNECTION_CMD)
	{
	    cmm_print(DEBUG_STDOUT, "Usage: set stat connection { maxconn <no.of connections> } \n"
				    "                           { memconn <Value of FPP DDR memory available for connections specified in bytes> }\n");
	}
#if defined(FLOW_STATS)
	if (cmd_type == FPP_STAT_UNKNOWN_CMD || cmd_type == FPP_STAT_FLOW_CMD)
	{
	    cmm_print(DEBUG_STDOUT, "Usage: set stat flow reset\n");
	}
#endif

	if (cmd_type == FPP_STAT_UNKNOWN_CMD)
	{
	    cmm_print(DEBUG_STDOUT, "\n");
	}

}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmStatSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cmd_type = FPP_STAT_UNKNOWN_CMD;
	int cpt = tabStart;
	unsigned int cmdToSend = 0; /* bits field*/
	char * endptr;
        int rcvBytes = 0;
	char *pinterface;
	unsigned short interface;
#ifndef LS1043
	unsigned int tmp;
#endif

	fpp_stat_enable_cmd_t statEnableCmd;
#ifndef LS1043
	fpp_stat_queue_cmd_t queueResetCmd;  
#endif
	fpp_stat_interface_cmd_t interfaceResetCmd;
	fpp_stat_pppoe_status_cmd_t pppoeResetCmd;
	fpp_stat_bridge_status_cmd_t bridgeResetCmd;
	fpp_stat_ipsec_status_cmd_t ipsecResetCmd;
	fpp_stat_vlan_status_cmd_t vlanResetCmd;
	fpp_stat_tunnel_status_cmd_t tunnelResetCmd;
#if defined(FLOW_STATS)
	fpp_stat_flow_status_cmd_t flowResetCmd;
#endif

	union u_rxbuf rxbuf;

	if(!keywords[cpt])
		goto help;

	if( (strcasecmp(keywords[cpt], "enable") == 0) ||
	    (strcasecmp(keywords[cpt], "disable") == 0) )
	{
		cmd_type = FPP_STAT_ENABLE_CMD;

		if(strcasecmp(keywords[cpt], "enable") == 0)
			statEnableCmd.action = FPP_CMM_STAT_ENABLE;
		else
			statEnableCmd.action = FPP_CMM_STAT_DISABLE;

		if(!keywords[++cpt])
			goto help;
#ifndef LS1043
		if(strcasecmp(keywords[cpt], "queue") == 0)
			statEnableCmd.bitmask = FPP_STAT_QUEUE_BITMASK;
#endif
		else if(strcasecmp(keywords[cpt], "interface") == 0)
			statEnableCmd.bitmask = FPP_STAT_INTERFACE_BITMASK;
		else if(strcasecmp(keywords[cpt], "pppoe") == 0)
			statEnableCmd.bitmask = FPP_STAT_PPPOE_BITMASK;
		else if(strcasecmp(keywords[cpt], "bridge") == 0)
			statEnableCmd.bitmask = FPP_STAT_BRIDGE_BITMASK;
		else if(strcasecmp(keywords[cpt], "ipsec") == 0)
			statEnableCmd.bitmask = FPP_STAT_IPSEC_BITMASK;
		else if(strcasecmp(keywords[cpt], "vlan") == 0)
			statEnableCmd.bitmask = FPP_STAT_VLAN_BITMASK;			
		else if(strcasecmp(keywords[cpt], "tunnel") == 0)
			statEnableCmd.bitmask = FPP_STAT_TUNNEL_BITMASK;
#if defined(FLOW_STATS)
		else if(strcasecmp(keywords[cpt], "flow") == 0)
			statEnableCmd.bitmask = FPP_STAT_FLOW_BITMASK;
#endif
		else
			goto keyword_error;

		cmdToSend |= CMD_BIT(FPP_CMD_STAT_ENABLE);
	
	}
#ifndef LS1043
	else if(strcasecmp(keywords[cpt], "queue") == 0)
	{
		cmd_type = FPP_STAT_QUEUE_CMD;
		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
		{
			cmm_print(DEBUG_CRIT, "Stat ERROR: Queue Number must be a number between 0 and %d\n", FPP_NUM_QUEUES - 1);
			goto help;
		}
		memset(&queueResetCmd, 0, sizeof(queueResetCmd));
		queueResetCmd.queue= tmp;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "interface") != 0)
			goto keyword_error;

		if(!keywords[++cpt])
			goto help;

		pinterface = keywords[cpt];
		if (parse_interface(pinterface, &interface) < 0)
			goto help;

		queueResetCmd.interface = interface;

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		queueResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_QUEUE);
	}
#endif
	else if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		cmd_type = FPP_STAT_INTERFACE_PKT_CMD;
		if(!keywords[++cpt])
			goto help;
		pinterface = keywords[cpt];

		memset(&interfaceResetCmd, 0, sizeof(interfaceResetCmd));
		if (parse_interface(pinterface, &interface) < 0)
			goto help;

		interfaceResetCmd.interface = interface;

		if(!keywords[++cpt])
			goto help;


		if(strcasecmp(keywords[cpt], "reset") != 0)
				goto help;

		interfaceResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_INTERFACE_PKT);
	}
	else if(strcasecmp(keywords[cpt], "pppoe") == 0)
	{
		cmd_type = FPP_STAT_PPPOE_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		pppoeResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_PPPOE_STATUS);
	}
	else if(strcasecmp(keywords[cpt], "bridge") == 0)
	{
		cmd_type = FPP_STAT_BRIDGE_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		bridgeResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_BRIDGE_STATUS);
	}
	else if(strcasecmp(keywords[cpt], "ipsec") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") == 0) 
		{
			cmd_type = FPP_STAT_IPSEC_CMD;

			ipsecResetCmd.action = FPP_CMM_STAT_RESET;
			cmdToSend |= CMD_BIT(FPP_CMD_STAT_IPSEC_STATUS);

		}
		else
			goto help;
	}
	else if(strcasecmp(keywords[cpt], "vlan") == 0)
	{
		cmd_type = FPP_STAT_VLAN_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		vlanResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_VLAN_STATUS);
	}		
	else if(strcasecmp(keywords[cpt], "tunnel") == 0)
	{
		cmd_type = FPP_STAT_TUNNEL_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		tunnelResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_TUNNEL_STATUS);
	}
	else if(strcasecmp(keywords[cpt], "connection") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		if((strcasecmp(keywords[cpt], "maxconn") != 0) &&
			(strcasecmp(keywords[cpt], "memconn") != 0) )
			goto help;
		if(strcasecmp(keywords[cpt], "maxconn") == 0)
		{
			if(!keywords[++cpt])
				goto help;

			strtoul(keywords[cpt], &endptr, 0);
		}
		else if ( strcasecmp(keywords[cpt], "memconn") == 0 )
		{
			if(!keywords[++cpt])
				goto help;

			strtoul(keywords[cpt], &endptr, 0);
		}
		/* Just Positively acknowledge the command & return without programming FE */
		goto ack_return;
	}
#if defined(FLOW_STATS)
	else if(strcasecmp(keywords[cpt], "flow") == 0)
	{
		cmd_type = FPP_STAT_FLOW_CMD;
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "reset") != 0)
			goto help;

		flowResetCmd.action = FPP_CMM_STAT_RESET;
		cmdToSend |= CMD_BIT(FPP_CMD_STAT_FLOW);
	}
#endif
	else
		goto keyword_error;


	/*
	 * Parsing have been performed
	 * Now send the right commands
	 */
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_ENABLE))
	{
		/* Send CMD_STAT_ENABLE command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_ENABLE, &statEnableCmd, sizeof(statEnableCmd), rxbuf.rcvBuffer);
	}
#ifndef LS1043
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_QUEUE))
	{
		/* Send CMD_STAT_QUEUE command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_QUEUE, &queueResetCmd, sizeof(queueResetCmd), rxbuf.rcvBuffer);
	}
#endif
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_INTERFACE_PKT))
	{
		/* Send CMD_STAT_INTERFACE_PKT command */
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_INTERFACE_PKT, &interfaceResetCmd, sizeof(interfaceResetCmd), rxbuf.rcvBuffer);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_PPPOE_STATUS))
	{
		// Send CMD_STAT_PPPOE_STATUS command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_PPPOE_STATUS, &pppoeResetCmd, sizeof(pppoeResetCmd), rxbuf.rcvBuffer);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_BRIDGE_STATUS))
	{
		// Send CMD_STAT_BRIDGE_STATUS command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_BRIDGE_STATUS, &bridgeResetCmd, sizeof(bridgeResetCmd), rxbuf.rcvBuffer);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_IPSEC_STATUS))
        {
                /* Send CMD_STAT_IPSEC_STATUS command */
                 rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_IPSEC_STATUS, &ipsecResetCmd, sizeof(ipsecResetCmd), rxbuf.rcvBuffer);
        }

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_VLAN_STATUS))
	{
		// Send FPP_CMD_STAT_VLAN_STATUS command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_VLAN_STATUS, &vlanResetCmd, sizeof(vlanResetCmd), rxbuf.rcvBuffer);
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_TUNNEL_STATUS))
	{
		// Send FPP_CMD_STAT_TUNNEL_STATUS command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_TUNNEL_STATUS, &tunnelResetCmd, sizeof(tunnelResetCmd), rxbuf.rcvBuffer);
	}

#if defined(FLOW_STATS)
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_STAT_FLOW))
	{
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_STAT_FLOW, &flowResetCmd, sizeof(flowResetCmd), rxbuf.rcvBuffer);
	}
#endif

	if ( (rcvBytes == 2) && (rxbuf.result != 0))
	  cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%04x\n",rxbuf.result);        

ack_return:
	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmStatSetPrintHelp(cmd_type);
	return -1;

}

static int parse_interface(char *pstring, unsigned short *pinterface_number)
{
	if ((short)(*pinterface_number = get_port_id(pstring)) < 0)
	{
		/*cmm_print(DEBUG_CRIT, "ERROR: Invalid interface specification: %s\n", pstring);*/
		return -1;
	}
	return 0;
}

