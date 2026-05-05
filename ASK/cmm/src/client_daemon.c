/*
 *
 *  Copyright (C) 2007 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017-2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#include <signal.h>
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/msg.h>

#include "cmm.h"
#include "libcmm.h"
#include "fpp.h"
#include "cmmd.h"
#include "voicebuf.h"
#include "module_tx.h"

int dumpmem(int argc, char *argv[]);

#ifndef caseretstr
#define caseretstr(x) case x: return #x
#endif

#ifndef ERR_CODE_PREFIX_LENGTH
#define ERR_CODE_PREFIX_LENGTH (8) /* FPP_ERR_ */
#endif

char * getErrorString(unsigned short error)
{
	switch (error)
	{
	/*-------------------------------- General -------------------------------*/
	caseretstr(FPP_ERR_OK);
	caseretstr(FPP_ERR_UNKNOWN_COMMAND);
	caseretstr(FPP_ERR_WRONG_COMMAND_SIZE);
	caseretstr(FPP_ERR_WRONG_COMMAND_PARAM);

	caseretstr(FPP_ERR_UNKNOWN_ACTION);
	caseretstr(FPP_ERR_UNKNOWN_INTERFACE);
	caseretstr(FPP_ERR_NOT_ENOUGH_MEMORY);
	caseretstr(FPP_ERR_CREATION_FAILED);
	caseretstr(FPP_ERR_WRONG_PARAM_VALUE);
	caseretstr(FPP_ERR_PARAM_VALUE_OUT_OF_RANGE);

	/*-------------------------------- RX Module -----------------------------*/
	caseretstr(FPP_ERR_BRIDGE_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_BRIDGE_ENTRY_ALREADY_EXISTS);
	caseretstr(FPP_ERR_BRIDGE_WRONG_MODE);

	/*-------------------------------- MacVlan -------------------------------*/
	caseretstr(FPP_ERR_MACVLAN_ENTRY_ALREADY_REGISTERED);
	caseretstr(FPP_ERR_MACVLAN_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_MACVLAN_ENTRY_INVALID);

	/*-------------------------------- Conntrack -----------------------------*/
	caseretstr(FPP_ERR_CT_ENTRY_ALREADY_REGISTERED);
	caseretstr(FPP_ERR_CT_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_CT_ENTRY_INVALID_SA);
	caseretstr(FPP_ERR_CT_ENTRY_TOO_MANY_SA_OP);

	/*-------------------------------- IP ------------------------------------*/ 
	caseretstr(FPP_ERR_RT_ENTRY_ALREADY_REGISTERED);
	caseretstr(FPP_ERR_RT_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_RT_ENTRY_LINKED);
	caseretstr(FPP_ERR_RT_LINK_NOT_POSSIBLE);

	/*-------------------------------- QOS -----------------------------------*/
	caseretstr(FPP_ERR_QM_QUEUE_RATE_LIMITED);
	caseretstr(FPP_ERR_QM_RATE_LIMIT_NOT_APPLIED_TO_OFF);
	caseretstr(FPP_ERR_QM_QUEUE_OUT_OF_RANGE);
	caseretstr(FPP_ERR_QM_NUM_DSCP_OUT_OF_RANGE);
	caseretstr(FPP_ERR_QM_DSCP_OUT_OF_RANGE);
	caseretstr(FPP_ERR_QM_NO_FREE_SHAPER);
	caseretstr(FPP_ERR_QM_NO_QUEUE_SPECIFIED);
	caseretstr(FPP_ERR_QM_INGRESS_POLICER_HANDLE_NULL);
	caseretstr(FPP_ERR_QM_INGRESS_SET_PROFILE_FAILED);

	/*-------------------------------- VLAN ----------------------------------*/
	caseretstr(FPP_ERR_VLAN_ENTRY_ALREADY_REGISTERED);
	caseretstr(FPP_ERR_VLAN_ENTRY_NOT_FOUND);

	/*-------------------------------- Multicast -----------------------------*/
	caseretstr(FPP_ERR_MC_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_MC_MAX_LISTENERS);
	caseretstr(FPP_ERR_MC_DUP_LISTENER);
	caseretstr(FPP_ERR_MC_ENTRY_OVERLAP);
	caseretstr(FPP_ERR_MC_INVALID_ADDR);
	caseretstr(FPP_ERR_MC_INTERFACE_NOT_ALLOWED);

	/*-------------------------------- PPPoE ---------------------------------*/
	caseretstr(FPP_ERR_PPPOE_ENTRY_ALREADY_REGISTERED);
	caseretstr(FPP_ERR_PPPOE_ENTRY_NOT_FOUND);

	/*-------------------------------- IPSec ---------------------------------*/
	caseretstr(FPP_ERR_SA_DUPLICATED);
	caseretstr(FPP_ERR_SA_DUPLICATED_HANDLE);
	caseretstr(FPP_ERR_SA_UNKNOWN );
	caseretstr(FPP_ERR_SA_INVALID_CIPHER_KEY);
	caseretstr(FPP_ERR_SA_INVALID_DIGEST_KEY);
	caseretstr(FPP_ERR_SA_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_SA_SOCK_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_SA_INVALID_MODE);

	/*-------------------------------- Tunnels -------------------------------*/
	caseretstr(FPP_ERR_TNL_MAX_ENTRIES);
	caseretstr(FPP_ERR_TNL_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_TNL_NOT_SUPPORTED);
	caseretstr(FPP_ERR_TNL_NO_FREE_ENTRY);
	caseretstr(FPP_ERR_TNL_ALREADY_CREATED);

	/*-------------------------------- Stat ----------------------------------*/
	caseretstr(FPP_ERR_STAT_FEATURE_NOT_ENABLED );

	/*-------------------------------- Exceptions ----------------------------*/
	caseretstr(FPP_ERR_EXPT_QUEUE_OUT_OF_RANGE);
	caseretstr(FPP_ERR_EXPT_NUM_DSCP_OUT_OF_RANGE);
	caseretstr(FPP_ERR_EXPT_DSCP_OUT_OF_RANGE);

	/*-------------------------------- Sockets -------------------------------*/
	caseretstr(FPP_ERR_SOCK_ALREADY_OPEN);
	caseretstr(FPP_ERR_SOCKID_ALREADY_USED);
	caseretstr(FPP_ERR_SOCK_ALREADY_OPENED_WITH_OTHER_ID);
	caseretstr(FPP_ERR_TOO_MANY_SOCKET_OPEN);
	caseretstr(FPP_ERR_SOCKID_UNKNOWN);
	caseretstr(FPP_ERR_SOCK_ALREADY_IN_USE);
	caseretstr(FPP_ERR_RTP_CALLID_IN_USE);
	caseretstr(FPP_ERR_RTP_UNKNOWN_CALL);
	caseretstr(FPP_ERR_WRONG_SOCKID);
	caseretstr(FPP_ERR_RTP_SPECIAL_PKT_LEN);
	caseretstr(FPP_ERR_RTP_CALL_TABLE_FULL);
	caseretstr(FPP_ERR_WRONG_SOCK_FAMILY);
	caseretstr(FPP_ERR_WRONG_SOCK_PROTO);
	caseretstr(FPP_ERR_WRONG_SOCK_TYPE);
	caseretstr(FPP_ERR_MSP_NOT_READY);
	caseretstr(FPP_ERR_WRONG_SOCK_MODE);
#if defined (LS1043)
	caseretstr(FPP_ERR_NO_ROUTE_TO_SOCK);
	caseretstr(FPP_ERR_SOCK_UPDATE_ERR);
#endif //LS1043

	/* ------------------------------- NATPT ---------------------------------*/
	caseretstr(FPP_ERR_NATPT_UNKNOWN_CONNECTION);

	/* ------------------------------- RTP -----------------------------------*/
	caseretstr(FPP_ERR_RTP_STATS_MAX_ENTRIES);
	caseretstr(FPP_ERR_RTP_STATS_STREAMID_ALREADY_USED);
	caseretstr(FPP_ERR_RTP_STATS_STREAMID_UNKNOWN);
	caseretstr(FPP_ERR_RTP_STATS_DUPLICATED);
	caseretstr(FPP_ERR_RTP_STATS_WRONG_DTMF_PT);
	caseretstr(FPP_ERR_RTP_STATS_WRONG_TYPE);
	caseretstr(FPP_ERR_RTP_STATS_NOT_AVAILABLE );

	/*-------------------------------- Voice Buffer --------------------------*/
	caseretstr(FPP_ERR_VOICE_BUFFER_UNKNOWN);
	caseretstr(FPP_ERR_VOICE_BUFFER_USED);
	caseretstr(FPP_ERR_VOICE_BUFFER_PT);
	caseretstr(FPP_ERR_VOICE_BUFFER_FRAME_SIZE);
	caseretstr(FPP_ERR_VOICE_BUFFER_ENTRIES);
	caseretstr(FPP_ERR_VOICE_BUFFER_SIZE);
	caseretstr(FPP_ERR_VOICE_BUFFER_STARTED);

	/*-------------------------------- Altconf -------------------------------*/
	caseretstr(FPP_ERR_ALTCONF_OPTION_NOT_SUPPORTED);
	caseretstr(FPP_ERR_ALTCONF_MODE_NOT_SUPPORTED);
	caseretstr(FPP_ERR_ALTCONF_WRONG_NUM_PARAMS);

	/*-------------------------------- PKTCAP --------------------------------*/
	caseretstr(FPP_ERR_PKTCAP_ALREADY_ENABLED);
	caseretstr(FPP_ERR_PKTCAP_NOT_ENABLED);
	caseretstr(FPP_ERR_PKTCAP_FLF_RESET);

	/*-------------------------------- ICC -----------------------------------*/
	caseretstr(FPP_ERR_ICC_TOO_MANY_ENTRIES);
	caseretstr(FPP_ERR_ICC_ENTRY_ALREADY_EXISTS);
	caseretstr(FPP_ERR_ICC_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_ICC_THRESHOLD_OUT_OF_RANGE);
	caseretstr(FPP_ERR_ICC_INVALID_MASKLEN);

	caseretstr(FPP_ERR_FLOW_ENTRY_NOT_FOUND);
	caseretstr(FPP_ERR_INVALID_IP_FAMILY);

	/*-------------------------------- WiFi ----------------------------------*/
	caseretstr(FPP_ERR_WIFI_DUPLICATE_OPERATION);

	default:
		return (char *) "Unknown error code";
	}
}

/*****************************************************************
 * showErrorMsg()
 *
 *      Print error message
 *  
 *****************************************************************/
void showErrorMsg(char *commandCodeString, unsigned int source, char *rxBuffer)
{
	char *errString = getErrorString(((unsigned short*)rxBuffer)[0]);
	char resString[255];
	int length = strlen(errString);

	if (length >= ERR_CODE_PREFIX_LENGTH)
		memmove(&resString[0], &errString[ERR_CODE_PREFIX_LENGTH], length - ERR_CODE_PREFIX_LENGTH + 1);
	
	cmm_print(DEBUG_STDERR, "Error %s (%d) received from %s for %s\n", 
		resString, 
		((unsigned short*)rxBuffer)[0], 
		((source == ERRMSG_SOURCE_FPP) ? "FPP" : "CMM Daemon"),
		commandCodeString);
}

/*****************************************************************
 * cmmClientPrintHelp()
 *
 *      Print command syntax
 *  
 *****************************************************************/
void cmmClientPrintHelp()
{
	
	/*
	 * The commands syntax must be:
	 *    <set / show>  <module name>  <option1> <value1> <option2> <value2> .... 
	 */

	cmm_print(DEBUG_STDOUT, "Command usage: set <module_name> [option ...]\n"
									"\trx: Manage RX module (ICC, Bridging  ...)\n"
									"\ttx: Manage TX module (DSCP VLAN PCP mapping)\n"
									"\tqm: Manage QM module (QOS, Rate Limiting ,Ingress QOS ...)\n"
									"\tmc6:	Manage IPv6 Multicast module\n"
									"\tmc4:	Manage IPv4 Multicast module\n"
                                    					"\tstat: Manage Statistics module\n"
									"\troute: Manage Extended Route module\n"
									"\tconfig: Manage Alternate Configurations\n"
									"\tsocket: Manage Socket module\n"
									"\tsocket6: Manage V6 socket module\n"
									"\trtp: Manage RTP Relay module\n"
									"\tnatpt: Manage NAT-PT module\n"
									"\tsa_query_timer: Manage IPsec SA query timer module\n"
#ifdef C2000_DPI
									"\tdpi: Manage DPI Enable/disable\n"
#endif
									"\tasym_fastforward: Manage Asymmetric Fastforward Enable/disable\n"
									"\trtpstats: Manage RTP Stats for Fast Forwarded connections\n"
									"\tbridge: Manage bridge (timeout)\n"
									"\ttimeout: set timeout for udp/tc flows\n"
									"\texpt_queue: manage queue configurations\n"
									"\tff: manage fast forwarding control\n"
									"\tipsec: manage ipsec configurations\n"
									"\tvoicebuf: manage voicebuf control \n"
									"\tfrag: manage ipv4/ipv6 fragmentation configurations\n"		
									"\t4rd-id-conversion: Enable/ Disable IPv4 header Identification conversion,\n"
									"\t\tfor 4rd interfaces\n");
	cmm_print(DEBUG_STDOUT, "\nCommand usage: show <module_name> [option ...]\n"
									"\trx: show RX module (ICC, Bridging  ...)\n"
									"\tqm: show QM module (QOS, Rate Limiting....)\n"
									"\tmc6:	show IPv6 Multicast module\n"
									"\tmc4:	show  IPv4 Multicast module\n"
                                    					"\tstat: show Statistics module\n"
									"\troute: show Extended Route module\n"
									"\tsocket: show Socket module\n"
									"\tsocket6: show V6 socket module\n");
	cmm_print(DEBUG_STDOUT, "\nCommand usage: query < module_name> \n"
                              						"\trx: Receive (bridge)\n"
                              						"\tvlan: VLAN devices\n"
                              						"\tpppoe: PPPoE Sessions\n"
                              						"\tsa: SA Entries\n"
#if defined (LS1043)
                              						"\tsecfailstats: IPSEC SEC engine failure statistics [reset]\n"
#endif
                              						"\tmc6: IPv6 Multicast Entries\n"
                              						"\tmc4:	IPv4 Multicast Entries\n"
                              						"\tqm: QOS information\n"
                              						"\tqmexptrate: Exception Rate\n"
									"\tqmingress: Ingress Policer information\n"
#if defined (LS1043)
									"\tqmffrate: fast forward rate limiting\n"
#ifdef SEC_PROFILE_SUPPORT
									"\tqmsecrate: ipsec forward rate limiting\n"
#endif /* endif for SEC_PROFILE_SUPPORT */
#endif
									"\ttx-dscp-to-vlanpcp: dscp vlan pcp mapping\n"
									"\tconnections: IPV4 connections\n"
									"\troute: IP routes\n"
                              						"\tv6connections: IPV6 Connections\n"
									"\tsocket: IPV4 sockets\n"
									"\trtcp: RTP relay statistics\n"
									"\trtpstats: RTP statistics for Fast Forwarded connections\n"
				  					"\tnatpt: NAT-PT Entries\n"
				  					"\tl2flows: L2 and L3-4 flows Entries\n"
									"\tmacvlan: Mac-vlan interfaces\n"
									"\ttunnels: tunnel interfaces\n");

	cmm_print(DEBUG_STDOUT, "\nCommand usage: { msp | dm | prf | tunnel | relay | vlan | pktcapture | icc | ipv4 |ipv6 } <options> \n");

}

/*****************************************************************
 * cmmSendToDaemon()
 *
 *      This function sends a message to cmm daemon
 *      and waits for the answer.
 *  
 *****************************************************************/
int cmmSendToDaemon(daemon_handle_t handle, unsigned short commandCode, void * dataToSend, int dataSize, void* dataToRcv)
{
	int rcvBytes = 0;
#ifdef NEW_IPC
	cmm_command_t cmd;
	cmm_response_t res;

	cmd.func = commandCode;
	cmd.length = dataSize;
	memcpy(cmd.buf, dataToSend, dataSize);

	if(cmm_send(handle, &cmd, 0) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s: cmm_send failed, %s\n", __func__, strerror(errno));
		return -1;
	}

	rcvBytes = cmm_recv(handle, &res, 0);
	if (rcvBytes < 0)
	{
		cmm_print(DEBUG_STDERR, "%s: cmm_recv failed, %s\n", __func__, strerror(errno));
		return rcvBytes;
	}

	if (dataToRcv) {
		/* XXX: to have same output as with old IPC,
		 * this should be refactored
		 */
		memcpy(dataToRcv, res.buf, res.length);
	}

	rcvBytes = res.length;
#else
	int queueIdRx;
	int queueIdTx;
	struct cmm_msg msg;
	key_t key;

	handle = ((handle & 0xff) ^ ((handle >> 8) & 0xff)) | 1;

	key = ftok("/tmp", handle);
	if (key == (key_t)-1)
	{
		cmm_print(DEBUG_STDERR, "%s: ftok(%d) failed, %s\n", __func__, handle, strerror(errno));
		return -1;
	}

	queueIdRx = msgget(key, 0);
	if (queueIdRx < 0)
	{
		cmm_print(DEBUG_STDERR, "%s: rx msgget() failed, %s\n", __func__, strerror(errno));
		return -1;
	}

	key = ftok("/tmp", handle ^ 0xff);
	if (key == (key_t)-1)
	{
		cmm_print(DEBUG_STDERR, "%s: ftok(%d) failed, %s\n", __func__, handle ^ 0xff, strerror(errno));
		return -1;
	}

	queueIdTx = msgget(key, 0);
	if (queueIdTx < 0)
	{
		cmm_print(DEBUG_STDERR, "%s: tx msgget() failed, %s\n", __func__, strerror(errno));
		return -1;
	}

	if (dataSize > sizeof(msg.buffer))
		return -1;

	msg.mtype = commandCode;
	if (dataToSend)
		memcpy(msg.buffer, dataToSend, dataSize);

#if 0
	if ((globalConf.debug_level & DEBUG_INFO) || (globalConf.log_level & DEBUG_INFO))
	{
		int rcvDataSize;
		cmm_print(DEBUG_INFO, "commandCode: (%04x) \n", (unsigned int)msg.mtype);
		for(rcvDataSize = 0; rcvDataSize < dataSize; rcvDataSize+=2)
		{
			cmm_print(DEBUG_INFO, "%02x%02x \n", msg.buffer[rcvDataSize + 1], msg.buffer[rcvDataSize]);
		}
		cmm_print(DEBUG_INFO, "\n");
	}
#endif

	if (msgsnd(queueIdTx, &msg, dataSize, 0) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s: msgsnd() failed, %s\n", __func__, strerror(errno));
		return -1;
	}

	// Now wait for an answer
	if ((rcvBytes = msgrcv(queueIdRx, &msg, sizeof(msg.buffer), commandCode, 0)) < 0)
	{
		// Error !!
		cmm_print(DEBUG_STDERR, "%s: msgrcv() failed, %s\n", __func__, strerror(errno));
		return -1;
	}

#if 0
	if ((globalConf.debug_level & DEBUG_INFO) || (globalConf.log_level & DEBUG_INFO))
	{
		int rcvDataSize;
		cmm_print(DEBUG_INFO, "commandAck:  (%04x) \n", (unsigned int)msg.mtype);
		for(rcvDataSize = 0; rcvDataSize < rcvBytes ; rcvDataSize += 2)
		{
			cmm_print(DEBUG_INFO, "%04x \n", ((unsigned short *)msg.buffer)[rcvDataSize]);
		}
		cmm_print(DEBUG_INFO, "\n");
	}
#endif

	if ((dataToRcv) && (rcvBytes))
		memcpy(dataToRcv, msg.buffer, rcvBytes);
#endif
	return (rcvBytes );
}


/*****************************************************************
 * cmmClientCommandParser()
 *
 *      cmm client command parser
 *  
 *****************************************************************/
int cmmClientProcessCmd(char * command, int argc, char ** argv, daemon_handle_t daemon_handle)
{
	char * keywords[128];	/*We allow up to 256 keywords*/
	int cpt,i;
	char *saveptr, *strl = NULL;
	if ((command == NULL) && (argc == 0))
	  goto help;
	/*
	 * First split the string to get 
	 * each keywords in a separate buffer
	 */
	if (command ) {
	for (cpt = 0, strl = command; cpt < 128 ; cpt++, strl = NULL)
	{
		keywords[cpt] = strtok_r(strl, " ", &saveptr);
		if(keywords[cpt] == NULL)
			break;
	}
	} else {
	  cpt = 0;
	}
	for(i=0; (i < argc) && (cpt < 128); i++) {
	  keywords[cpt] = argv[i];
	  cpt++;
	}
	if (cpt < 128)
	  keywords[cpt] = NULL;
	// We nead at least 2 keywords to perform the parsing
	if(cpt < 1)
		goto help;

	/*
	 * Check if first keyword is correct
	 */
	if(strcasecmp(keywords[0], "set")== 0)
	{
	  	if(cpt < 2)
	    		goto help;
		if (strcasecmp(keywords[1], "rx") == 0)
		{
			/*Call Rx process function*/
			if (cmmRxSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
#ifdef LS1043
		else if (strcasecmp(keywords[1], "tx") == 0)
		{
			/*Call TX process function*/
			if(cmmTxSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
#endif
		else if (strcasecmp(keywords[1], "qm") == 0)
		{
			/*Call QM process function*/
			if(cmmQmSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "mc6") == 0)
		{
			/*Call QM process function*/
			if(cmmMc6SetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "mc4") == 0)
		{
			/*Call QM process function*/
			if(cmmMc4SetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "timeout") == 0)
		{
			if(cmmtimeoutSet(daemon_handle, keywords, cpt, 2))
				return -1;
		}
		else if (strcasecmp(keywords[1], "stat") == 0)
		{
			if(cmmStatSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "expt_queue") == 0)
		{
			if(cmmExptSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "ff") == 0)
		{
			if(cmmFFControlProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "route") == 0)
		{
			if(cmmRouteSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "sa_query_timer") == 0)
		{
			if(cmmDPDSaQuerySetProcess(keywords, 2, daemon_handle))
				return -1;
		}
#ifdef C2000_DPI
		else if (strcasecmp(keywords[1], "dpi") == 0)
		{
			if(cmmDPIFlagSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
#endif
		else if (strcasecmp(keywords[1], "asym_fastforward") == 0)
		{
			if(cmmAsymFFSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "config") == 0)
		{
			if (cmmAltConfClient(cpt,keywords,2,daemon_handle))
				return -1;

		}
		else if (strcasecmp(keywords[1], "ipsec") == 0)
		{
			if(cmmIPsecSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "rtp") == 0)
		{
			if (cmmRTPSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (!strcasecmp(keywords[1], "socket"))
		{
			if (cmmSocketSetProcess(keywords, 2, daemon_handle, AF_INET))
				return -1;
		}
		else if (!strcasecmp(keywords[1], "socket6"))
		{
			if (cmmSocketSetProcess(keywords, 2, daemon_handle, AF_INET6))
				return -1;
		}
		else if (strcasecmp(keywords[1], "rtpstats") == 0)
		{
			if (cmmRTPStatsSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "natpt") == 0)
		{
			if (cmmNATPTSetProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "voicebuf") == 0)
		{
			if (cmmVoiceBufSetProcess(cpt - 2, &keywords[2], daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "frag") == 0)
		{
			if (cmmFragTimeoutSet(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "bridge") == 0)
		{
			if (cmmBridgeControlProcess(keywords, 2, daemon_handle))
				return -1;
		}		
		else if (strcasecmp(keywords[1], "4rd-id-conversion") == 0)
		{
			if (cmm4rdIdConvSetProcess(keywords, 2, (cpt - 2), daemon_handle))
				return -1;
		}
		else
			goto help;
	}
	else if (strcasecmp(keywords[0], "show") == 0)
	{
	  	if(cpt < 2)
	    		goto help;
		
		if (strcasecmp(keywords[1], "rx") == 0)
		{
			/*Call Rx process function*/
			if (cmmRxShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "qm") == 0)
		{
			/*Call QM process function*/
			if(cmmQmShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "mc6") == 0)
		{
			/*Call QM process function*/
			if(cmmMc6ShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "mc4") == 0)
		{
			/*Call QM process function*/
			if(cmmMc4ShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "stat") == 0)
		{
			/*Call Stat process function*/
			if(cmmStatShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "expt") == 0)
		{
			/*Call QM process function*/
			if(cmmExptShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "route") == 0)
		{
			if(cmmRouteShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (!strcasecmp(keywords[1], "socket") || !strcasecmp(keywords[1], "socket6"))
		{
			if(cmmSocketShowProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else
			goto help;
	}
	else if (strcasecmp(keywords[0], "query") == 0)
	{
	  	if(cpt < 2)
	    		goto help;
		
		if (strcasecmp(keywords[1], "rx") == 0)
		{
			/*Call Rx process function*/
			if (cmmRxQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "qm") == 0)
		{
			/*Call QM process function*/
			if(cmmQmQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "qmingress") == 0)
		{
			/*Call QM process function*/
			if(cmmQmIngressQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
#ifdef SEC_PROFILE_SUPPORT
		else if (strcasecmp(keywords[1], "qmsecrate") == 0)
		{
			/*Call Sec QM process function*/
			if(cmmQmSecQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
#endif /* endif for SEC_PROFILE_SUPPORT */
		else if (strcasecmp(keywords[1], "qmexptrate") == 0)
		{
			/*Call QM process function*/
			if(cmmQmExptRateQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
#if defined (LS1043)
		else if (strcasecmp(keywords[1], "qmffrate") == 0)
		{
			/*Call QM process function*/
			if(cmmQmFFRateQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "qm-dscp-fqmap") == 0)
		{
			/*Call QM dscp fq map process function*/
			if(cmmQmDSCPFqMapQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "tx-dscp-to-vlanpcp") == 0)
		{
			/*Call TX dscp vlan pcp map query function*/
			if(cmmDSCPVlanPcpMapQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
#endif
		else if (strcasecmp(keywords[1], "mc6") == 0)
		{
			/*Call QM process function*/
			if(cmmMc6QueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
		else if (strcasecmp(keywords[1], "mc4") == 0)
		{
			/*Call QM process function*/
			if(cmmMc4QueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
                else if (strcasecmp(keywords[1], "pppoe") == 0)
                {
                        if(cmmPPPoEQueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "route") == 0)
                {
                        if(cmmRtQueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "connections") == 0)
                {
                        if(cmmCtQueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "v6connections") == 0)
                {
                        if(cmmCt6QueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "vlan") == 0)
                {
                        if(cmmVlanQuery(keywords, 2, daemon_handle))
                                return -1;
                }
#if defined(LS1043)
                else if (strcasecmp(keywords[1], "ipr4_stats") == 0)
                {
                        if(cmmIpr4StatsQuery(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "ipr6_stats") == 0)
                {
                        if(cmmIpr6StatsQuery(keywords, 2, daemon_handle))
                                return -1;
                }
#endif
		else if (strcasecmp(keywords[1], "macvlan") == 0)
                {
                        if(cmmMacVlanQueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "sa") == 0)
                {
                        if(cmmSAQueryProcess(keywords, 2, daemon_handle))
                                return -1;
                }
#if defined(LS1043)
		else if (strcasecmp(keywords[1], "secfailstats") == 0)
		{
			if(cmmSECfailStatsQueryProcess(keywords, 2, daemon_handle))
				return -1;
		}
#endif
                else if (strcasecmp(keywords[1], "rtcp") == 0)
                {
                        if(cmmRTCPQueryProcess(keywords, 2, daemon_handle))
                        		return -1;
                }
                else if (strcasecmp(keywords[1], "rtpstats") == 0)
                {
                        if(cmmRTPStatsQueryProcess(keywords, 2, daemon_handle))
		  				return -1;
                }
                else if (strcasecmp(keywords[1], "tunnels") == 0)
                {
                        if(cmmTnlQueryProcess(keywords, 2, daemon_handle))
                        		return -1;
                }
		else if (strcasecmp(keywords[1], "natpt") == 0)
		{
		  		if(cmmNATPTQueryProcess(keywords, 2, daemon_handle))
		  				return -1;
  		}
#ifdef AUTO_BRIDGE
		else if (strcasecmp(keywords[1], "l2flows") == 0)
		{
		  		if(cmmL2FlowQueryProcess(keywords, 2, daemon_handle))
		  				return -1;
  		}
#endif
		else
		{
                	goto help;
  		}
	}
	else if (strncasecmp(keywords[0], "msp",3) ==0)
	{
		/* mspmem */
		if (cmmPrfMem(cpt,keywords,1,daemon_handle))
			return -1;
	}
	else if (strncasecmp(keywords[0], "dm",2) ==0)
	{
		if (dumpmem(cpt,keywords))
			return -1;
	}
	else if (strcasecmp(keywords[0],"prf") == 0) 
	{
		if (cmmPrfNM(cpt,keywords,1,daemon_handle))
			return -1;
	}
	else if (strcasecmp(keywords[0],"tunnel") == 0) 
	{
		if (cmm_tunnel_parse_cmd(cpt,keywords,1,daemon_handle))
			return -1;
	}
	else if (strcasecmp(keywords[0],"relay") == 0)
	{
		return cmmRelayParseCmd(cpt,keywords,1,daemon_handle);
	}
	else if (strncasecmp(keywords[0], "vlan", 4) == 0) 
	{
		if (cmmVlanClient(cpt,keywords, 1, daemon_handle))
			return -1;
	}
	else if (strcasecmp(keywords[0], "ipv4") == 0)
	{
	  	if(cpt < 2)
	    		goto help;
		
		if (strcasecmp(keywords[1], "update") == 0)
		{
			/*Call IPv4 update function*/
			if (cmmCtChangeProcess4(keywords, 2, daemon_handle))
				return -1;
		}
	}
	else if (strcasecmp(keywords[0], "ipv6") == 0)
	{
	  	if(cpt < 2)
	    		goto help;
		
		if (strcasecmp(keywords[1], "update") == 0)
		{
			/*Call IPv6 update function*/
			if (cmmCtChangeProcess6(keywords, 2, daemon_handle))
				return -1;
		}
	}
	else if (strcasecmp(keywords[0], "pktcapture") == 0)
        {
                if (cpt < 2)
                        goto help;

                if (strcasecmp(keywords[1], "status") == 0)
                {
                        if (PktCapStatProcess(daemon_handle, cpt-2,  &keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "slice") == 0)
                {
                        if (PktCapSliceProcess(daemon_handle, cpt-2,&keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "filter") == 0)
                {
                        if (PktCapFilterProcess(daemon_handle , cpt-2 , &keywords[2]))
                                return -1;
                }
		else
		{
			char buf[128];
			print_all_gemac_ports(buf, 128);
			cmm_print(DEBUG_STDOUT, "Command usage: pktcapture [status| slice| filter] [%s] <value>\n", buf);
		}
		
        }
	else if (strcasecmp(keywords[0], "icc") == 0)
        {
                if (cpt < 2)
                        goto help;

                if (strcasecmp(keywords[1], "reset") == 0)
                {
                        if (IccReset(daemon_handle, cpt-2,  &keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "threshold") == 0)
                {
                        if (IccThreshold(daemon_handle, cpt-2,&keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "add") == 0)
                {
                        if (IccAdd(daemon_handle , cpt-2 , &keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "delete") == 0)
                {
                        if (IccDelete(daemon_handle , cpt-2 , &keywords[2]))
                                return -1;
                }
                else if (strcasecmp(keywords[1], "query") == 0)
                {
                        if (IccQuery(daemon_handle, cpt-2,&keywords[2]))
                                return -1;
                }
		else
		{
			cmm_print(DEBUG_STDOUT, "Command usage: icc [reset | threshold | add | delete | query] <parameters...>\n");
		}
		
        }

	else
		goto help;

	return 0;

help:
	cmmClientPrintHelp();

	return -1;
}


/*****************************************************************
 * cmmClient()
 *
 *      cmm client main function
 *  
 *****************************************************************/
int cmmClient(char * command, int argc, char **argv)
{
	daemon_handle_t daemon_handle;
	int rc;

#ifdef NEW_IPC
	daemon_handle = cmm_open();
	if (!daemon_handle)
	{
		cmm_print(DEBUG_STDERR, "%s: CMM handle creation failed\n", __func__);
		return -1;
	}
#else
	/*First check cmm daemon is alive*/
	if( (daemon_handle = cmmIsDaemonRunning()) <= 0)
	{
		cmm_print(DEBUG_CRIT, "Error, cmm daemon is not running\n");
		return -1;
	}
#endif

	/*Process the command*/
	rc = cmmClientProcessCmd(command, argc, argv, daemon_handle);

#ifdef NEW_IPC
	cmm_close(daemon_handle);
#endif

	return rc;
}

static int cmmCommandParse(struct cmm_daemon *ctx, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);

/*****************************************************************
 * cmmdaemonThread()
 *
 *      cmm client main function
 *  
 *****************************************************************/
static void *cmmDaemonThread(void *data)
{
	struct cmm_daemon *ctx = data;

#ifdef NEW_IPC
	cmm_command_t cmd;
	cmm_response_t res;
#else
	struct cmm_msg msg;
#endif
	int dataSize;
	unsigned short dataRcvSize;
	int rc;
	unsigned int func;
	u_int8_t *rx_buf;
	u_int8_t *tx_buf;

	cmm_print(DEBUG_INFO, "%s: pid %d\n", __func__, getpid());

	while (1)
	{
		// Waiting for a message
#ifdef NEW_IPC
		/* We have to reset errno, because it could left after previous error condition,
		 * and we will wrongly report it later as "daemon_errno" even if no error occured.
		 * From 'man errno':
		 * "Successful calls never set errno; once set, it remains until another error occurs".
		 * XXX: this is a hack needed to support "daemon_errno" field in cmm_response_t.
		 * CMM library on client side sets system errno according to "daemon_errno" 
		 * and returns -1 when it's not 0.
		 */
		errno = 0;

		memset(&cmd, 0, sizeof(cmd));
		memset(&res, 0, sizeof(res));

		// msgrcv expects size msgsz as length after msgtype.
		msgrcv(ctx->queueIdRx, &cmd, (sizeof(cmd) - sizeof(cmd.msg_type)), 0, 0);
		res.msg_type = cmd.msg_type;
		dataSize = cmd.length;
		dataRcvSize = sizeof(res.buf);
		func = cmd.func;
		rx_buf = cmd.buf;
		tx_buf = res.buf;
#else
		dataSize = msgrcv(ctx->queueIdRx, &msg, sizeof(msg.buffer), 0, 0);
		dataRcvSize = sizeof(msg.buffer);
		func = msg.mtype;
		rx_buf = msg.buffer;
		tx_buf = msg.buffer;
#endif
		if (dataSize < 0)
		{
			/* Exit if queue id no longer exists */
			if ((errno == EIDRM) || (errno == ENOENT))
				break;

			// If we have an error receiving a msg, do nothing and continue waiting for a new one
			cmm_print(DEBUG_WARNING, "%s: msgrcv() failed, %s\n", __func__, strerror(errno));
			rc = -1;
			dataRcvSize = 0;
			goto answer;
		}

		if (dataSize > CMM_BUF_SIZE) { 
			cmm_print(DEBUG_ERROR, "%s: message size exceeds limit of %d\n", __func__, CMM_BUF_SIZE); 
			rc = -1; 
			dataRcvSize = 0; 
			goto answer; 
		}

		// Debug
		if ((globalConf.debug_level & DEBUG_INFO) || (globalConf.log_level & DEBUG_INFO))
		{
			int x;
			char outbuf[CMM_BUF_SIZE * 3 + 1]; /* 3 chars for each byte + trailing '\0' */
			cmm_print(DEBUG_INFO, "%s: Received commandCode: (%04x) size %d\n", __func__, func, dataSize);
			for (x = 0, outbuf[0] = '\0'; x < dataSize; x++)
			{
				snprintf(outbuf + strlen(outbuf), sizeof(outbuf) - strlen(outbuf), "%02x ", rx_buf[x]);
				if ((x & 0xF) == 0xF || x == dataSize - 1)
				{
					cmm_print(DEBUG_INFO, "%s\n", outbuf);
					outbuf[0] = '\0';
				}
			}
			cmm_print(DEBUG_INFO, "\n");
		}

		rc = cmmCommandParse(ctx, func, rx_buf, dataSize, (u_int16_t*)tx_buf, &dataRcvSize);

		if (dataRcvSize > CMM_BUF_SIZE) {
			cmm_print(DEBUG_ERROR, "%s: message size exceeds limit of %d\n", __func__, CMM_BUF_SIZE);
			rc = -1;
			dataRcvSize = 0;
			goto answer;
		}

		// Debug
		if ((globalConf.debug_level & DEBUG_INFO) || (globalConf.log_level & DEBUG_INFO))
		{
			int x;
			char outbuf[CMM_BUF_SIZE * 3 + 1]; /* 3 chars for each byte + trailing '\0' */
			cmm_print(DEBUG_INFO, "%s: Sending ack commandCode: %04x, rc 0x%04x, dataSize: %d \n", __func__, func, rc ,dataRcvSize);
			for (x = 0, outbuf[0] = '\0'; x < dataRcvSize; x++)
			{
				snprintf(outbuf + strlen(outbuf), sizeof(outbuf) - strlen(outbuf), "%02x ", tx_buf[x]);
				if ((x & 0xF) == 0xF || x == dataRcvSize - 1)
				{
					cmm_print(DEBUG_INFO, "%s\n", outbuf);
					outbuf[0] = '\0';
				}
			}
			cmm_print(DEBUG_INFO, "\n");
		}

answer:
		// Send answer to client		
#ifdef NEW_IPC
		res.func = func;
		if (rc < 0) {
			if (errno)
				res.daemon_errno = errno;
			else
				res.daemon_errno = EIO;
		}

		res.length = dataRcvSize;
		if (msgsnd(ctx->queueIdTx, &res, sizeof(res) - sizeof(res.buf) + res.length, 0) < 0)
#else
		if (msgsnd(ctx->queueIdTx, &msg, dataRcvSize, 0) < 0)
#endif
		{
			cmm_print(DEBUG_WARNING, "%s: msgsnd() failed, %s\n", __func__, strerror(errno));
			break;
		}
	}

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	kill(0, SIGTERM);
	pthread_exit(NULL);

	return NULL;
}

int cmmDaemonInit(struct cmm_daemon *ctx)
{
	key_t key;
	int id = (int)globalConf.cmmPid;

	id = ((id & 0xff) ^ ((id >> 8) & 0xff)) | 1;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	key = ftok("/tmp", id ^ 0xff);
	if (key == (key_t)-1)
	{
		cmm_print(DEBUG_CRIT, "%s: ftok(%d) failed, %s\n", __func__, id ^ 0xff, strerror(errno));
		goto err0;
	}

	// Remove any stale message queue from previous run
	ctx->queueIdRx = msgget(key, 0);
	if (ctx->queueIdRx >= 0)
	{
		cmm_print(DEBUG_INFO, "%s: removing stale rx queue %d\n", __func__, ctx->queueIdRx);
		msgctl(ctx->queueIdRx, IPC_RMID, NULL);
	}

	// Create the message queue
	ctx->queueIdRx = msgget(key, IPC_CREAT | IPC_EXCL);
	if (ctx->queueIdRx < 0)
	{
		cmm_print(DEBUG_CRIT, "%s: rx msgget() failed, %s\n", __func__, strerror(errno));
		goto err0;
	}

	key = ftok("/tmp", id);
	if (key == (key_t)-1)
	{
		cmm_print(DEBUG_CRIT, "%s: ftok(%d) failed, %s\n", __func__, id, strerror(errno));
		goto err1;
	}

	// Remove any stale message queue from previous run
	ctx->queueIdTx = msgget(key, 0);
	if (ctx->queueIdTx >= 0)
	{
		cmm_print(DEBUG_INFO, "%s: removing stale tx queue %d\n", __func__, ctx->queueIdTx);
		msgctl(ctx->queueIdTx, IPC_RMID, NULL);
	}

	ctx->queueIdTx = msgget(key, IPC_CREAT | IPC_EXCL);
	if (ctx->queueIdTx < 0)
	{
		cmm_print(DEBUG_CRIT, "%s: tx msgget() failed, %s\n", __func__, strerror(errno));
		goto err1;
	}

	ctx->fci_handle = fci_open(FCILIB_FF_TYPE, 0);
	if (!ctx->fci_handle)
	{
		cmm_print(DEBUG_CRIT, "%s: fci_open() failed, %s\n", __func__, strerror(errno));
		goto err2;
	}

#if !defined(IPSEC_SUPPORT_DISABLED)
	ctx->fci_key_handle = fci_open(FCILIB_KEY_TYPE, 0);
	if (!ctx->fci_key_handle)
	{
		cmm_print(DEBUG_CRIT, "%s::%d: fci_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err3;
	}
#endif
	voice_buffer_reset(ctx->fci_handle);

	// Thread for getting cmm client command
	if (pthread_create(&ctx->pthread, NULL, cmmDaemonThread, ctx) < 0)
	{
		cmm_print(DEBUG_CRIT, "%s: pthread_create() failed, %s\n", __func__, strerror(errno));
		goto err4;
	}

	return 0;

err4:
#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_close(ctx->fci_key_handle);

err3:
#endif
	fci_close(ctx->fci_handle);

err2:
	msgctl(ctx->queueIdTx, IPC_RMID, NULL);

err1:
	msgctl(ctx->queueIdRx, IPC_RMID, NULL);

err0:
	return -1;
}

void cmmDaemonExit(struct cmm_daemon *ctx)
{
	cmm_print(DEBUG_INFO, "%s\n", __func__);

#if defined(__UCLIBC__)
	/* workaround uclibc pthread_cancel() bug, force thread to exit */
	msgctl(ctx->queueIdTx, IPC_RMID, NULL);
	msgctl(ctx->queueIdRx, IPC_RMID, NULL);
#else
	pthread_cancel(ctx->pthread);
#endif

	pthread_join(ctx->pthread, NULL);

	fci_close(ctx->fci_handle);
#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_close(ctx->fci_key_handle);
#endif
#if !defined(__UCLIBC__)
	msgctl(ctx->queueIdTx, IPC_RMID, NULL);
	msgctl(ctx->queueIdRx, IPC_RMID, NULL);
#endif

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);
}

/*****************************************************************
 * cmmCommandParse()
 *
 * returns:
 * 0  - success
 * -1 - system error. errno variable indicates error in this case. 
 *****************************************************************/
static int cmmCommandParse(struct cmm_daemon *ctx, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	if ((function_code &  FPP_CMD_TRC_MASK) == FPP_CMD_TRC_VAL)
		goto FCI_CMD;

	switch (function_code)
	{
        case CMMD_CMD_IPV4_CONNTRACK:
        case CMMD_CMD_IPV6_CONNTRACK:
		return cmmCtHandle(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);

	case CMMD_CMD_IPV4_FF_CONTROL:
		return cmmFeFFControl(ctx->fci_handle, cmd_buf, cmd_len, res_buf, res_len);

	// Multicast commands, we accept but we need to do a local process
	case CMMD_CMD_MC6_MULTICAST:
	case CMMD_CMD_MC6_RESET:
	case CMMD_CMD_MC6_MODE:
		return cmmMc6ProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len); 

	case CMMD_CMD_MC4_MULTICAST:
	case CMMD_CMD_MC4_RESET:
		return cmmMc4ProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);

	/* Tunnel commands */
	case CMMD_CMD_TUNNEL_ADD:
	case CMMD_CMD_TUNNEL_DEL: 
 	case CMMD_CMD_TUNNEL_SHOW:
	case CMMD_CMD_TUNNEL_IDCONV_psid:
#ifdef SAM_LEGACY	
 	case CMMD_CMD_TUNNEL_SAMREADY:
#endif
		return tunnel_daemon_msg_recv(ctx->fci_handle, ctx->fci_key_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);

        case CMMD_CMD_PPPOE_RELAY_ADD:
        case CMMD_CMD_PPPOE_RELAY_REMOVE:
		return cmmRelayProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);

	case CMMD_CMD_VLAN_ENTRY:
		return cmmVlanProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);
#if defined (LS1043)
	case CMMD_CMD_IPR_V4_STATS:
	case CMMD_CMD_IPR_V6_STATS:
		return cmmIprStatsProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);
#endif
	case CMMD_CMD_EXTROUTE:
		return cmmRouteProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, res_buf, res_len);
								 
	case CMMD_CMD_IPSEC_DPDSAQUERYTIMER:
		return cmmDPDSAQUERYProcessClientCmd(cmd_buf, res_buf, res_len);
#ifdef C2000_DPI
	case CMMD_CMD_DPIENABLE:
		return cmmDPIFlagProcessClientCmd(cmd_buf, res_buf, res_len);
#endif
	case CMMD_ASYM_FF_ENABLE:
		return cmmAsymFFProcessClientCmd(cmd_buf, res_buf, res_len);
	case CMMD_CMD_SOCKET_OPEN:
	case CMMD_CMD_SOCKET_CLOSE:
	case CMMD_CMD_SOCKET_UPDATE:
	case CMMD_CMD_SOCKET_SHOW:
		return socket_daemon(ctx->fci_handle, ctx->fci_key_handle, function_code, cmd_buf, cmd_len, res_buf, res_len);

	case CMMD_CMD_VOICE_FILE_LOAD:
		return voice_file_load(ctx->fci_handle, (cmmd_voice_file_load_cmd_t *)cmd_buf, res_buf, res_len);

	case CMMD_CMD_VOICE_FILE_UNLOAD:
		return voice_file_unload(ctx->fci_handle, (cmmd_voice_file_unload_cmd_t *)cmd_buf, res_buf, res_len);

	case CMMD_CMD_L2TP_SESSION_CREATE:
	case CMMD_CMD_L2TP_SESSION_DESTROY:
		return l2tp_daemon(ctx->fci_handle,function_code, (cmmd_l2tp_session_t *) cmd_buf, cmd_len, res_buf, res_len);

	//Bridge commands
	case FPP_CMD_RX_L2BRIDGE_ENABLE:
	case FPP_CMD_RX_L2BRIDGE_ADD:
	case FPP_CMD_RX_L2BRIDGE_REMOVE:
	case FPP_CMD_RX_L2BRIDGE_QUERY_STATUS:
	case FPP_CMD_RX_L2BRIDGE_QUERY_ENTRY:
		return cmmL2BridgeProcessClientCmd(ctx->fci_handle, function_code, cmd_buf, cmd_len, res_buf, res_len); 

	case FPP_CMD_NATPT_OPEN:
		return cmmNATPTOpenProcessClientCmd(ctx->fci_handle, cmd_buf, cmd_len, res_buf, res_len);

	// Special processing for QM Reset and Scheduler config (need to notify eth driver)
	
#ifndef LS1043
	case FPP_CMD_QM_RESET:
		cmmQmResetQ2Prio((fpp_qm_reset_cmd_t *)cmd_buf, cmd_len);
		goto FCI_CMD;

	case FPP_CMD_QM_SCHED_CFG:
		cmmQmUpdateQ2Prio((fpp_qm_scheduler_cfg_t *)cmd_buf, cmd_len);
		goto FCI_CMD;
#endif
#ifdef LS1043
        case FPP_CMD_QM_QUERY_FF_RATE:
        case FPP_CMD_QM_FF_RATE:
	case FPP_CMD_QM_WBFQ_CFG:
	case FPP_CMD_QM_CQ_CFG:
	case FPP_CMD_QM_CQ_STATS:
	case FPP_CMD_QM_CHNL_ASSIGN:
	case FPP_CMD_QM_DSCP_FQ_MAP_STATUS:
	case FPP_CMD_QM_DSCP_FQ_MAP_CFG:
	case FPP_CMD_QM_DSCP_FQ_MAP_RESET:
	case FPP_CMD_QM_QUERY_IFACE_DSCP_FQID_MAP:
	case FPP_CMD_QM_INGRESS_POLICER_ENABLE:
	case FPP_CMD_QM_INGRESS_POLICER_CONFIG:
	case FPP_CMD_QM_INGRESS_POLICER_RESET:
	case FPP_CMD_QM_INGRESS_POLICER_QUERY_STATS:
#ifdef SEC_PROFILE_SUPPORT
	case FPP_CMD_QM_QUERY_SEC_POLICERRATE:
	case FPP_CMD_QM_SEC_POLICER_RATE:
	case FPP_CMD_QM_SEC_POLICER_RESET:
#endif /* endif for SEC_PROFILE_SUPPORT */
		goto FCI_CMD;
#endif
	// Accept the remaining qm commands
#ifdef LS1043
	case FPP_CMD_QM_RESET:
	case FPP_CMD_DSCP_VLANPCP_MAP_STATUS:
	case FPP_CMD_DSCP_VLANPCP_MAP_CFG:
	case FPP_CMD_QUERY_IFACE_DSCP_VLANPCP_MAP:
#endif
	case FPP_CMD_QM_QOSENABLE:
	case FPP_CMD_QM_QOSALG:
	case FPP_CMD_QM_NHIGH:
	case FPP_CMD_QM_MAX_TXDEPTH:
	case FPP_CMD_QM_MAX_QDEPTH:
	case FPP_CMD_QM_MAX_WEIGHT:
	case FPP_CMD_QM_RATE_LIMIT:
	case FPP_CMD_QM_EXPT_RATE:
	case FPP_CMD_QM_QUERY:
	case FPP_CMD_QM_QUERY_EXPT_RATE:
	case FPP_CMD_QM_SHAPER_CFG:
	case FPP_CMD_QM_DSCP_MAP:
	case FPP_CMD_QM_QUEUE_QOSENABLE:
	case FPP_CMD_QM_QUERY_PORTINFO:
	case FPP_CMD_QM_QUERY_QUEUE:
	case FPP_CMD_QM_QUERY_SHAPER:
	case FPP_CMD_QM_QUERY_SCHED:
	// Accept ICC commands
	case FPP_CMD_ICC_RESET:
	case FPP_CMD_ICC_THRESHOLD:
	case FPP_CMD_ICC_ADD_DELETE:
	case FPP_CMD_ICC_QUERY:
	// Accept some RX commands
	case FPP_CMD_RX_CNG_ENABLE:
	case FPP_CMD_RX_CNG_DISABLE:
	case FPP_CMD_RX_CNG_SHOW:
	case FPP_CMD_RX_L2FLOW_ENTRY:
	case FPP_CMD_RX_L2BRIDGE_FLOW_TIMEOUT:	
	// Accept timeout set command
	case FPP_CMD_IPV4_SET_TIMEOUT:
	case FPP_CMD_IPV4_FRAGTIMEOUT:
	case FPP_CMD_IPV4_SAMFRAGTIMEOUT:
	case FPP_CMD_IPV4_GET_TIMEOUT:
	case FPP_CMD_IPV6_GET_TIMEOUT:
	case FPP_CMD_IPV6_FRAGTIMEOUT:
        // accept for ACTION_QUERY
        case FPP_CMD_IP_ROUTE:
        case FPP_CMD_PPPOE_ENTRY:
        case FPP_CMD_IPSEC_SA_ACTION_QUERY:
        case FPP_CMD_IPSEC_SA_ACTION_QUERY_CONT:
#ifdef LS1043
	case FPP_CMD_IPSEC_SEC_FAILURE_STATS:
	case FPP_CMD_IPSEC_RESET_SEC_FAILURE_STATS:
#endif /* LS1043 */

	// Voice Buffer
	case FPP_CMD_VOICE_BUFFER_START:
	case FPP_CMD_VOICE_BUFFER_STOP:
	// accept stat commands
        case FPP_CMD_STAT_ENABLE:
	case FPP_CMD_STAT_QUEUE:
        case FPP_CMD_STAT_INTERFACE_PKT:
        case FPP_CMD_STAT_CONNECTION:
        case FPP_CMD_STAT_PPPOE_STATUS:
        case FPP_CMD_STAT_PPPOE_ENTRY:
        case FPP_CMD_STAT_BRIDGE_STATUS:
        case FPP_CMD_STAT_BRIDGE_ENTRY:
        case FPP_CMD_STAT_IPSEC_STATUS:
        case FPP_CMD_STAT_IPSEC_ENTRY:
        case FPP_CMD_STAT_VLAN_STATUS:
        case FPP_CMD_STAT_VLAN_ENTRY:        
        case FPP_CMD_STAT_TUNNEL_STATUS:
        case FPP_CMD_STAT_TUNNEL_ENTRY:
#if defined(FLOW_STATS)
	case FPP_CMD_STAT_FLOW:
#endif
	// accept alternate Configuration commands
	case FPP_CMD_ALTCONF_SET:
	case FPP_CMD_ALTCONF_RESET:
	// Expt
	case FPP_CMD_EXPT_QUEUE_RESET:
	case FPP_CMD_EXPT_QUEUE_DSCP:
	case FPP_CMD_EXPT_QUEUE_CONTROL:
	case FPP_CMD_IPSEC_FRAG_CFG:
	// Socket and RTP statistics
	case FPP_CMD_RTP_CLOSE:
	case FPP_CMD_RTP_CONTROL:
	case FPP_CMD_RTP_OPEN:
	case FPP_CMD_RTP_SPECTX_CTRL:
	case FPP_CMD_RTP_SPECTX_PLD:
	case FPP_CMD_RTP_TAKEOVER:
	case FPP_CMD_RTP_UPDATE:
	case FPP_CMD_RTCP_QUERY:
	case FPP_CMD_RTP_STATS_ENABLE:
	case FPP_CMD_RTP_STATS_DISABLE:
	case FPP_CMD_RTP_STATS_QUERY:
	case FPP_CMD_RTP_STATS_DTMF_PT:
	case FPP_CMD_NATPT_CLOSE:
	case FPP_CMD_NATPT_QUERY:
	case FPP_CMD_PKTCAP_IFSTATUS:
	case FPP_CMD_PKTCAP_SLICE:
	case FPP_CMD_PKTCAP_FLF:
	case FPP_CMD_PKTCAP_QUERY:
	case FPP_CMD_MACVLAN_ENTRY:
	case FPP_CMD_TUNNEL_QUERY:
	case FPP_CMD_TUNNEL_QUERY_CONT:
	case FPP_CMD_TUNNEL_4rd_ID_CONV_dport:
		goto FCI_CMD;

	// Other commands, we refuse
	default:
		res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
		*res_len = 2;
	}

	return 0;

FCI_CMD:
	//Sending message to FPP
	return fci_cmd(ctx->fci_handle, function_code, (unsigned short *)cmd_buf, cmd_len, (unsigned short *)res_buf, res_len);
}


/////////////////////////////////////////////////////////////////////////////////////////////////////////

int parse_value(char *p, u_int32_t *value, u_int32_t maxval)
{
	char *endp;
	u_int32_t val;
	val = strtoul(p, &endp, 0);
	if (*endp || val > maxval)
		return -1;
	*value = val;
	return 0;
}


int parse_range(char *p, u_int32_t *from, u_int32_t *to, u_int32_t maxval)
{
	char *endp;
	u_int32_t fromval, toval;
	fromval = strtoul(p, &endp, 0);
	if (*endp)
	{
		if (*endp++ != '-')
			return -1;
		if (parse_value(endp, &toval, maxval) < 0)
			return -1;
	}
	else
		toval = fromval;
	if (toval < fromval || toval > maxval)
		return -1;
	*from = fromval;
	*to = toval;
	return 0;
}

/////////////////////////////////////////////////////////////////////////////////////////////////////////


#include<sys/stat.h>
#include<sys/mman.h>

#define DISP_LINE_LEN	16
#define MMAP_SIZE	0x01000000

volatile unsigned char *base_addr;

int dumpmem(int argc, char *argv[])
{
	unsigned long	addr, size, length=16;
	unsigned long	i, nbytes, linebytes;
	unsigned char	*cp;
	int fd;
	int rc = 0;
	char outbuf[256];

	/* We use the last specified parameters, unless new ones are
	 * entered.
	 */

	if (argc != 2 && argc != 3) {
		cmm_print(DEBUG_ERROR, "Usage : dm[b|w|l] address [size]\n");
		return(1);
	}

	switch(argv[0][2]) {
	case 'b':
		size = 1;
		break;
	case 'w':
		size = 2;
		break;
	case 'l':
		size = 4;
		break;
	default:
		size = 4;
		break;
	}

	addr = strtoul(argv[1], NULL, 16);
	fd = open("/dev/mem", O_RDWR);
	if (fd == -1) {
		cmm_print(DEBUG_ERROR, "open() error. errno:%m\n");
		return(1);
	}
	base_addr = mmap(0,
			 MMAP_SIZE,
			 PROT_READ|PROT_WRITE,
			 MAP_SHARED,
			 fd,
			 (addr & 0xFF000000));
	if (base_addr == (unsigned char*)(-1)){
                cmm_print(DEBUG_ERROR, "mmap() error. errno:%m\n");
                rc = 1;
                goto err;
        }

	/* If another parameter, it is the length to display.
	 * Length is the number of objects, not number of bytes.
	 */
	if (argc == 3) {
		length = strtoul(argv[2], NULL, 16);
	}

	/* Print the lines.
	 *
	 * We buffer all read data, so we can make sure data is read only
	 * once, and all accesses are with the specified bus width.
	 */
	nbytes = length * size;
	if (nbytes > MMAP_SIZE) {
		cmm_print(DEBUG_ERROR, "display size over ( 0x%08x > 0x%08x ).\n", (int)nbytes, MMAP_SIZE);
		rc = 1;
		goto err;
	}
	do {
		u_int8_t	linebuf[DISP_LINE_LEN];
		unsigned int	*uip = (unsigned int   *)linebuf;
		unsigned short	*usp = (unsigned short *)linebuf;
		unsigned char	*ucp = (unsigned char *)linebuf;

		sprintf(outbuf, "%08lx:", addr);
		linebytes = (nbytes>DISP_LINE_LEN)?DISP_LINE_LEN:nbytes;
		for (i=0; i<linebytes; i+= size) {
			if (size == 4) {
				sprintf(outbuf + strlen(outbuf), " %08x", (*uip++ = *((unsigned int *)(base_addr+(addr&0x00ffffff)))));
			} else if (size == 2) {
				sprintf(outbuf + strlen(outbuf), " %04x", (*usp++ = *((unsigned short *)(base_addr+(addr&0x00ffffff)))));
			} else {
				sprintf(outbuf + strlen(outbuf), " %02x", (*ucp++ = *((unsigned char *)(base_addr+(addr&0x00ffffff)))));
			}
			addr += size;
		}
		sprintf(outbuf + strlen(outbuf), "    ");
		cp = linebuf;
		for (i=0; i<linebytes; i++) {
			if ((*cp < 0x20) || (*cp > 0x7e))
				sprintf(outbuf + strlen(outbuf), ".");
			else
				sprintf(outbuf + strlen(outbuf), "%c", *cp);
			cp++;
		}
		cmm_print(DEBUG_STDOUT, "%s\n", outbuf);
		nbytes -= linebytes;
	} while (nbytes > 0);

	munmap((void*)base_addr, MMAP_SIZE);
err:
	close(fd);
	return (rc);
}
