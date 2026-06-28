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
#include "module_rtp.h"
#include "fpp.h"

static void cmmRTPSetUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: set rtp \n"
			"\n"
			"                                  [open | update]\n"
			"                                       [ccn {call control number}] \n"
			"                                       [sock_id_a {socket ID}]\n"
			"                                       [sock_id_b {socket ID}] \n"
			"\n"
			"                                  [takeover]\n"
			"                                       [ccn {call control number}] \n"
			"                                       [sock_id {socket ID}]\n"
			"                                       [seq_nb_base {Sequence number base }]\n"
#if defined(LS1043)
			"                                       [ssrc {RTP/RTCP SSRC}]\n"
#else
			"                                       [rtp_ssrc {RTP SSRC}]\n"
#endif //(LS1043)
			"                                       [rtp_time_base {RTP timestamp base}]\n"
			"                                       [rtp_time_incr {RTP timestamp increment}]\n"
			"                                       [rtp_time_incr_mode {0:TS value, 1:Freq HZ}]\n"
			"                                       [ssrc_mode {0: regular, 1: auto}]\n"
#if defined(LS1043)
			"                                       [marker {Marker Bit Configuration Mode}]\n"
			"                                       [ssrc_1 {RTCP SSRC_1}]\n"
#endif //(LS1043)
			"\n"
			"                                  [control]\n"
			"                                       [ccn {call control number}] \n"
#if !defined(LS1043)
			"                                       [rtp_media_ctrl {0-3}]\n" 
#else
			"										[rtp_media_ctrl {0-31}:\n"
			"											Bit 0 (1/0):socket A to B RTP relayed/discarded\n"
			"											Bit 1 (1/0):socket B to A RTP relayed/discarded\n"
			"											Bit 2 (1/0):VLAN P bit learning feature enable/disable\n"
			"											Bit 3 (1/0):Enable/disable copy of VLAN P bit value for packets received on Socket A, sent on Socket B\n"
			"											Bit 4 (1/0):Enable/disable copy of VLAN P bit value for packets received on Socket B, sent on Socket A]\n"
			"										[vlan_p_bit_conf {0-256}:(default disable)\n"
			"											Bit 0 (1/0):Enable/disable VLAN P bit value for packets received on Socket A\n"
			"											Bit 1 (1/0):Enable/disable VLAN P bit value for packets received on Socket B\n"
			"											Bit 2to4 (0to7):VLAN P bit value for packets received on Socket A\n"
			"											Bit 5to7 (0to7):VLAN P bit value for packets received on Socket B]\n"
#endif //(LS1043)

			"\n"
			"                                  [close]\n"
			"                                       [ccn {call control number}] \n"	  
			"\n"
			"                                  [spectx_ctrl]\n"
			"                                       [ccn {call control number}] \n"	  
			"                                       [type_tx {0: Transmission start, 1: Reception response, 2: Transmission stop, 3: Transmission single shot}] \n"	  
			"\n"
			"                                  [spectx_payload]\n"
			"                                       [ccn {call control number}] \n"	  
			"                                       [id {0: special payload 1, 1: special payload 2}] \n"	  
			"                                       [rtp_payload {Special RTP payload (string)}] \n"
			"\n"
	          );
}
static void cmmRTCPQueryUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: query rtcp [sock_id {socket ID}] [reset {full | partial}]\n"
			"\n"
	          );
}
int cmmRTPSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp; 
#if defined(LS1043)
	unsigned long tmp1;
#else
	unsigned int takeover_mode = 0;
#endif
	char * endptr;
	union u_rxbuf rxbuf;


	if(!keywords[cpt])
		goto print_help;

	if((strcasecmp(keywords[cpt], "open") == 0)
	||(strcasecmp(keywords[cpt], "update") == 0))
	{
		fpp_rtp_open_cmd_t cmd;
		unsigned int action;

		if(strcasecmp(keywords[cpt], "open") == 0)
			action = FPP_CMD_RTP_OPEN;
		else
			action = FPP_CMD_RTP_UPDATE;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "sock_id_a") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id_a parameter must be a number between 0 and %d\n", USHRT_MAX);
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
				cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id_b parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket_b= tmp;
		}
		else
			goto keyword_error;

		// Send  command
		if(cmmSendToDaemon(daemon_handle, action, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0) {
				char buffer[256];
				sprintf(buffer, "Action ID (%d)", action);
				showErrorMsg(buffer, ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			}
		}
	}
	


	
	else if (strcasecmp(keywords[cpt], "takeover") == 0)
	{
		fpp_rtp_takeover_cmd_t cmd;

		memset(&cmd,0,sizeof(fpp_rtp_takeover_cmd_t));
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "sock_id") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.socket= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;

#if defined(LS1043)
		do {
			if((strcasecmp(keywords[cpt], "seq_nb_base") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: seq_nb_base parameter must be a number between 0 and %d\n", USHRT_MAX);
					goto  print_help;
				}
				cmd.param_flags |= SEQ_NUM_TAKEOVER;
				cmd.seq_number_base= tmp;
			}
 			else if((strcasecmp(keywords[cpt], "ssrc") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp1 > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id_b parameter must be a number between 0 and %d\n", (unsigned int) UINT_MAX);
					goto  print_help;
				}
				cmd.param_flags |= SSRC_TAKEOVER;
				cmd.ssrc= (unsigned int)tmp1;
			}
			else if((strcasecmp(keywords[cpt], "rtp_time_base") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp1 > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_base parameter must be a number between 0 and %d\n", (unsigned int) UINT_MAX);
					goto  print_help;
				}
				cmd.param_flags |= TIMESTAMP_TAKEOVER;
				cmd.ts_base= (unsigned int)tmp1;
			}
			else if((strcasecmp(keywords[cpt], "rtp_time_incr") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				if(!(cmd.param_flags & TIMESTAMP_TAKEOVER))
					cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_base must be set\n");
	
				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp1 > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_incr parameter must be a number between 0 and %d\n", (unsigned int) UINT_MAX);
					goto  print_help;
				}
				cmd.ts_incr= (unsigned int)tmp1;
			}
			else if((strcasecmp(keywords[cpt], "rtp_time_incr_mode") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				if(!(cmd.param_flags & TIMESTAMP_TAKEOVER))
					cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_base must be set\n");
	

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp > 1))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_incr_mode parameter must be 0 or 1\n");
					goto  print_help;
				}
				if(tmp)
					cmd.mode |= FPP_RTP_TAKEOVER_MODE_TSINCR_FREQ;
			}
			else if((strcasecmp(keywords[cpt], "ssrc_mode") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp > 1))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: ssrc_mode parameter must be 0 or 1\n");
					goto  print_help;
				}
				if(tmp)
					cmd.mode |= FPP_RTP_TAKEOVER_MODE_AUTO_SSRC;
			}
			else if((strcasecmp(keywords[cpt], "marker") == 0))
			{
				if(!keywords[++cpt])
						goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp > 1))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: marker bit configuration mode must be 0 or 1\n");
					goto  print_help;
				}
				cmd.param_flags |= MARKER_BIT_TAKEOVER;
				cmd.marker_bit_conf_mode = tmp;
			}
			else if((strcasecmp(keywords[cpt], "ssrc_1") == 0))
			{
				if(!keywords[++cpt])
					goto print_help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) ||  (tmp1 > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rtp ERROR: SSRC_1 parameter must be a number between 0 and %d\n", (unsigned int) UINT_MAX);
					goto  print_help;
				}
				cmd.param_flags |= SSRC_1_TAKEOVER;
				cmd.ssrc_1 = (unsigned int)tmp1;
			}
			else
				goto keyword_error;
		}while (keywords[++cpt] != NULL);

#else

		if((strcasecmp(keywords[cpt], "seq_nb_base") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: seq_nb_base parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.seq_number_base= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "rtp_ssrc") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > ULONG_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id_b parameter must be a number between 0 and %d\n", (unsigned int) ULONG_MAX);
				goto  print_help;
			}
			cmd.ssrc= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;

		if((strcasecmp(keywords[cpt], "rtp_time_base") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > ULONG_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_base parameter must be a number between 0 and %d\n", (unsigned int) ULONG_MAX);
				goto  print_help;
			}
			cmd.ts_base= tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto send;

		if((strcasecmp(keywords[cpt], "rtp_time_incr") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > ULONG_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_incr parameter must be a number between 0 and %d\n", (unsigned int) ULONG_MAX);
				goto  print_help;
			}
			cmd.ts_incr= tmp;
		}
		else
			goto keyword_error;	

		if(!keywords[++cpt])
			goto send;

		if((strcasecmp(keywords[cpt], "rtp_time_incr_mode") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 1))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_time_incr_mod parameter must be 0 or 1\n");
				goto  print_help;
			}
			if(tmp)
				takeover_mode = FPP_RTP_TAKEOVER_MODE_TSINCR_FREQ;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto send;


		if((strcasecmp(keywords[cpt], "ssrc_mode") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 1))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ssrc_mode parameter must be 0 or 1\n");
				goto  print_help;
			}
			if(tmp)
				takeover_mode |= FPP_RTP_TAKEOVER_MODE_SSRC;
		}
		else
			goto keyword_error;		

		cmd.mode = takeover_mode;


send:
#endif // #else of  LS1043

		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_TAKEOVER, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_RTP_TAKEOVER", ERRMSG_SOURCE_FPP,rxbuf.rcvBuffer);
		}

	}
	else if (strcasecmp(keywords[cpt], "control") == 0)
	{
		fpp_rtp_ctrl_cmd_t cmd;
		
#if defined(LS1043)
		cmd.vlan_p_bit_conf  = 0;
#endif //LS1043

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "rtp_media_ctrl") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
#if defined(LS1043)
			if ((keywords[cpt] == endptr) ||  (tmp > 31))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_media_ctrl parameter must be a number between 0 and 31\n");
				goto  print_help;
			}
#else
			if ((keywords[cpt] == endptr) ||  (tmp > 3))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: rtp_media_ctrl parameter must be a number between 0 and 3\n");
				goto  print_help;
			}
#endif // LS1043
			cmd.control_dir= tmp;

		
		}
		else
			goto keyword_error;

#if defined(LS1043)
		if((keywords[++cpt]) &&
		   (strcasecmp(keywords[cpt], "vlan_p_bit_conf") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 256))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: vlan_p_bit_conf  parameter must be a number between 0 and 256\n");
				goto  print_help;
			}
			cmd.vlan_p_bit_conf = tmp;
		}
#endif //LS1043

		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_CONTROL, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_RTP_CONTROL", ERRMSG_SOURCE_FPP,rxbuf.rcvBuffer);
		}	
	}	
	else if (strcasecmp(keywords[cpt], "close") == 0)
	{
		fpp_rtp_close_cmd_t cmd;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;

		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_CLOSE, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_RTP_CLOSE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}	
	}	
	else if (strcasecmp(keywords[cpt], "spectx_ctrl") == 0)
	{
		fpp_rtp_spec_tx_ctrl_cmd_t cmd;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "type_tx") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 3))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: type_tx must be a number between 0 and 3\n");
				goto  print_help;
			}
			cmd.type= tmp;		
		}
		else
			goto keyword_error;


		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_SPECTX_CTRL, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_RTP_SPECTX_CTRL", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}		
	}

	else if (strcasecmp(keywords[cpt], "spectx_payload") == 0)
	{
		fpp_rtp_spec_tx_payload_cmd_t cmd;
		char *rtp_payload;
		unsigned short rtp_payload_s;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "ccn") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: ccn must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd.call_id = tmp;
		}
		else
			goto keyword_error;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "id") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
		
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 1))
			{
				cmm_print(DEBUG_CRIT, "rtp ERROR: id must be a number between 0 and 1\n");
				goto  print_help;
			}
			cmd.payload_id = tmp;		
		}
		else
			goto keyword_error;


		if(!keywords[++cpt])
			goto print_help;
	
		if((strcasecmp(keywords[cpt], "rtp_payload") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
	
			rtp_payload = keywords[cpt];
		}
		else
			goto keyword_error;

		rtp_payload_s = strlen(rtp_payload);

		if(rtp_payload_s > FPP_MAX_SPTX_STRING_SIZE)
		{
				cmm_print(DEBUG_CRIT, "rtp ERROR: RTP payload string size must be < %d\n", FPP_MAX_SPTX_STRING_SIZE);
				goto  print_help;
		}

		cmd.payload_length = rtp_payload_s;
		memcpy(cmd.payload, rtp_payload, rtp_payload_s);
		// Send  command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_SPECTX_PLD, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_RTP_SPECTX_PLD", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
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
	cmmRTPSetUsage();
	return -1;
}

int cmmRTCPQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	char rcvBuffer[256] __attribute__ ((aligned (4)));
	fpp_rtcp_query_cmd_t cmd;
	fpp_rtcp_query_res_t *rsp = (fpp_rtcp_query_res_t *)(rcvBuffer + 4);
	unsigned int tmp;
	char * endptr;
	int rcvBytes;

	if(!keywords[cpt])
		goto print_help;
	
	if((strcasecmp(keywords[cpt], "sock_id") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;

		memset(&cmd, 0, sizeof(cmd));

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_CRIT, "rtp ERROR: sock_id must be a number between 0 and %d\n", USHRT_MAX);
			goto  print_help;
		}
		cmd.socket_id = tmp;

		if (keywords[++cpt])
		{
			if (strcasecmp(keywords[cpt], "reset") == 0)
                        {
                            if(!keywords[++cpt])
                                cmd.flags = 0x1; //default is full reset
                            else if(strcasecmp(keywords[cpt], "full") == 0)
                                cmd.flags = 0x1;
                            else if(strcasecmp(keywords[cpt], "partial") == 0)
                                cmd.flags = 0x2;
                        }
                        else
				goto print_help;
		}
	}
	else
		goto keyword_error;

	// Send  command
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RTCP_QUERY, &cmd, sizeof(fpp_rtcp_query_cmd_t), rcvBuffer + 2);
	
        if (rcvBytes < sizeof(unsigned short))
        {
                cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
                goto out;
        }

        if ((((unsigned short*)rcvBuffer)[1]) != 0)
        {
		showErrorMsg("CMD_RTCP_QUERY", ERRMSG_SOURCE_FPP, rcvBuffer);
                goto out;
        }

        if (rcvBytes < (sizeof(fpp_rtcp_query_res_t) + sizeof(unsigned short)))
        {
                cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
                goto out;
        }

	cmm_print(DEBUG_STDOUT, 	"RTCP Statistics (sport %u dport %u)\n\n"
							"prev_reception_period(ms)        : %u\n"
							"last_reception_period(ms)        : %u\n"
							"num_tx_pkts                      : %u\n"
							"num_rx_pkts                      : %u\n"
							"last_rx_Seq                      : %u\n"
							"last_rx_TimeStamp                : %u\n"
							"RTP_header                       : %x %x %x %x %x %x %x %x %x %x %x %x\n"
							"num_dup_rx                       : %u\n"
							"num_rx_since_RTCP                : %u\n"
							"num_tx_bytes                     : %u\n"
							"min_jitter(us)                   : %u\n"
							"max_jitter(us)                   : %u\n"
							"average_jitter(us)               : %u\n"
							"num_rx_lost_pkts                 : %u\n"
							"min_reception_period(us)         : %u\n"
							"max_reception_period(us)         : %u\n"
							"average_reception_period(us)     : %u\n"
							"num_malformed_pkts               : %u\n"
							"num_expected_pkts                : %u\n"
							"num_late_pkts                    : %u\n"
							"ssrc_overwrite_value             : %x\n",
							ntohs(rsp->sport),
							ntohs(rsp->dport),
							rsp->prev_reception_period,
							rsp->last_reception_period,
							rsp->num_tx_pkts,
							rsp->num_rx_pkts,
							rsp->last_rx_seq,
							rsp->last_rx_timestamp,
							rsp->rtp_header[0], rsp->rtp_header[1], rsp->rtp_header[2], rsp->rtp_header[3], rsp->rtp_header[4], rsp->rtp_header[5],
							rsp->rtp_header[6], rsp->rtp_header[7], rsp->rtp_header[8], rsp->rtp_header[9], rsp->rtp_header[10], rsp->rtp_header[11],
							rsp->num_rx_dup,
							rsp->num_rx_since_rtcp,
							rsp->num_tx_bytes,
							rsp->min_jitter, rsp->max_jitter, rsp->average_jitter,
							rsp->num_rx_lost_pkts,
							rsp->min_reception_period,
							rsp->max_reception_period,
							rsp->average_reception_period,
							rsp->num_malformed_pkts,
							rsp->num_expected_pkts,
							rsp->num_late_pkts,
							rsp->ssrc_overwrite_value
							);
out:
	return 0 ;
	
keyword_error:
	cmm_print(DEBUG_STDOUT,"ERR: unknown keyword %s\n", keywords[cpt]);
print_help:
	cmmRTCPQueryUsage();
	return -1;
}


/******************** RTP Statistics for QoS Measurement ********************************/
static void cmmRTPStatsSetUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: set rtpstats \n"
			"\n"
			"                                  [enable]\n"
			"                                  	[id {RTP stream ID}]\n"
			"                                  	[type  {connection type: ip4 | ip6 | mc4 | mc6 | relay | relay6}]\n"
			"                                  	[saddr {ip source}]\n"
			"                                  	[daddr {ip destination}]\n"
			"                                  	[sport {port source}]\n"
			"                                  	[dport {port destination}]\n"
			"                                  	[proto {layer 3 protocol: udp | tcp}]\n"
			"                                  	[auto  {udp port discovery mode}]\n"
			"\n"
			"                                  [disable]\n"
			"                                  	[id {RTP stream ID}]\n"
			"\n"
			"                                  [dtmf_relay]\n"
			"                                       [{pt value 1} {pt value 2}]\n"
			"\n"
	          );
}

static void cmmRTPStatsQueryUsage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: query rtpstats [id {RTP stream ID}] [reset {full | partial}]\n"
			"\n"
	          );
}

int cmmRTPStatsSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	union u_rxbuf rxbuf;
	fpp_rtp_stat_enable_cmd_t cmd_ena;
	fpp_rtp_stat_disable_cmd_t cmd_dis;
	fpp_rtp_stat_dtmf_pt_cmd_t cmd_dtmf_pt;
	unsigned char pt_low, pt_high;
	int family;
	int rcvBytes;

	memset(&cmd_ena, 0 , sizeof(fpp_rtp_stat_enable_cmd_t));
	memset(&cmd_dis, 0 , sizeof(fpp_rtp_stat_disable_cmd_t));
	memset(&cmd_dtmf_pt, 0 , sizeof(fpp_rtp_stat_dtmf_pt_cmd_t));

	if(!keywords[cpt])
		goto print_help;

	if((strcasecmp(keywords[cpt], "enable") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;

		if(strcasecmp(keywords[cpt], "id") == 0)
		{
		  	if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: stream ID parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd_ena.stream_id= tmp;
		}
		else
			goto keyword_error;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "type") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			if(strcasecmp(keywords[cpt], "ip4") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_IP4;
				family = AF_INET;
			}
			else if(strcasecmp(keywords[cpt], "ip6") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_IP6;
				family = AF_INET6;
			}
			else if(strcasecmp(keywords[cpt], "mc4") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_MC4;
				family = AF_INET;
			}  
			else if (strcasecmp(keywords[cpt], "mc6") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_MC6;
				family = AF_INET6;
			}
			else if (strcasecmp(keywords[cpt], "relay") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_RLY;
				family = AF_INET;
			}
			else if (strcasecmp(keywords[cpt], "relay6") == 0) {
				cmd_ena.stream_type = FPP_RTPSTATS_TYPE_RLY6;
				family = AF_INET6;
			}

			else
				goto keyword_error;
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "saddr") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			if (!inet_pton(family, keywords[cpt], cmd_ena.saddr))
			{
				cmm_print(DEBUG_CRIT,"ERROR: reading source address %s\n", keywords[cpt]);
				goto keyword_error;
			}
		}
		else
			goto keyword_error;

		if(!keywords[++cpt])
			goto print_help;
	
		if((strcasecmp(keywords[cpt], "daddr") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			if (!inet_pton(family, keywords[cpt], cmd_ena.daddr))
			{
				cmm_print(DEBUG_CRIT,"ERROR:reading destination address %s\n", keywords[cpt]);
				goto keyword_error;
			}
		}
		else
			goto keyword_error;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "sport") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: sport must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd_ena.sport =  htons(tmp);
		}
		else
			goto keyword_error;
		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "dport") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: dport must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd_ena.dport =  htons(tmp);
		}
		else
			goto keyword_error;

		
		if(!keywords[++cpt])
			goto print_help;
		
		if((strcasecmp(keywords[cpt], "proto") == 0))
		{
			if(!keywords[++cpt])
				goto print_help;
			
			if(strcasecmp(keywords[cpt], "udp") == 0)
				cmd_ena.proto = IPPROTO_UDP;
			else if (strcasecmp(keywords[cpt], "tcp") == 0)
				cmd_ena.proto = IPPROTO_TCP;			
			else
				goto keyword_error;
		}
		else
			goto keyword_error;
		
		/* auto mode is optional */
		if(keywords[++cpt])
		{	
			if((strcasecmp(keywords[cpt], "auto") == 0))
			{
				cmd_ena.mode = 1;			
			}
			else
				goto keyword_error;
		}
		
		// Send  command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_STATS_ENABLE, &cmd_ena, sizeof(cmd_ena), &rxbuf.rcvBuffer);
	
		if (rcvBytes < sizeof(unsigned short))
		{
			cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
			goto out;
		}

		if (rxbuf.result != 0)
		{
			showErrorMsg("CMD_RTP_STATS_ENABLE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			goto out;
		}
	}
	else if((strcasecmp(keywords[cpt], "disable") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;

		if(strcasecmp(keywords[cpt], "id") == 0)
		{
		  	if(!keywords[++cpt])
				goto print_help;
			
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
			{
				cmm_print(DEBUG_CRIT, "ERROR: stream ID parameter must be a number between 0 and %d\n", USHRT_MAX);
				goto  print_help;
			}
			cmd_dis.stream_id = tmp;
		}
		else
			goto keyword_error;
		
		// Send command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_STATS_DISABLE, &cmd_dis, sizeof(cmd_dis), &rxbuf.rcvBuffer);
		
		if (rcvBytes < sizeof(unsigned short))
		{
			cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
			goto out;
		}

		if (rxbuf.result != 0)
		{
			showErrorMsg("CMD_RTP_STATS_DISABLE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			goto out;
		}
	}
	else if((strcasecmp(keywords[cpt], "dtmf_relay") == 0))
	{
		if(!keywords[++cpt])
		      goto print_help;
		
		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > 255))
		{
			cmm_print(DEBUG_CRIT, "ERROR: payload type parameter must be a number between 0 and %d\n", 255);
			goto  print_help;
		}
		pt_low = (0x00FF & tmp);
		
		/* if only one parameter specified, re-used first one */
		if(!keywords[++cpt]) 
			pt_high = pt_low;
		else 
		{
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) ||  (tmp > 255))
			{
				cmm_print(DEBUG_CRIT, "ERROR: payload type parameter must be a number between 0 and %d\n", 255);
				goto  print_help;
			}
			pt_high = (0x00FF & tmp);
			
			/* make sure values are ordered */
			if(pt_high < pt_low) 
			{
				pt_high = pt_low;
				pt_low = (0x00FF & tmp);
 			}
		}
		
		cmd_dtmf_pt.pt = (pt_high << 8) | pt_low;
		
		// Send command
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_STATS_DTMF_PT, &cmd_dtmf_pt, sizeof(cmd_dtmf_pt), &rxbuf.rcvBuffer);
		
		if (rcvBytes < sizeof(unsigned short))
		{
			cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
			goto out;
		}

		if (rxbuf.result != 0)
		{
			showErrorMsg("CMD_RTP_STATS_DTMF_PT", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			goto out;
		}
	}
	else
	{
		goto keyword_error;
	}
out:
	return 0;

keyword_error:
	cmm_print(DEBUG_STDOUT,"ERROR: unknown keyword %s\n", keywords[cpt]);
print_help:
	cmmRTPStatsSetUsage();
	return -1;
}


int cmmRTPStatsQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	/* TO CLEAN: Temporary fix for alignment */
	char rcvBuffer[256] __attribute__ ((aligned (4)));
	fpp_rtcp_query_cmd_t cmd;
	fpp_rtcp_query_res_t *rsp = (fpp_rtcp_query_res_t*)(rcvBuffer + 4);
	unsigned int tmp;
	char * endptr;
	int rcvBytes;

	if(!keywords[cpt])
		goto print_help;
	
	if((strcasecmp(keywords[cpt], "id") == 0))
	{
		if(!keywords[++cpt])
			goto print_help;

		memset(&cmd, 0, sizeof(cmd));

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_CRIT, "rtpstats: stream id must be a number between 0 and %d\n", USHRT_MAX);
		        goto  print_help;
		}
		cmd.socket_id = tmp;

		if(keywords[++cpt]) 
		{
		    if(strcasecmp(keywords[cpt++], "reset") == 0)
                    {
                        if(!keywords[cpt])
                            cmd.flags = 0x1; //default is full reset
                        else if(strcasecmp(keywords[cpt], "full") == 0)
                            cmd.flags = 0x1;
                        else if(strcasecmp(keywords[cpt], "partial") == 0)
                            cmd.flags = 0x2;
                    }
		    else
			goto print_help;
		}
	}
	else
		goto keyword_error;

	// Send  command
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_RTP_STATS_QUERY, &cmd, sizeof(cmd), rcvBuffer + 2);
	
	if (rcvBytes < sizeof(unsigned short))
	{
		cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
		goto out;
	}

	if ((((unsigned short*)rcvBuffer)[1]) != 0)
	{
		showErrorMsg("CMD_RTP_STATS_QUERY", ERRMSG_SOURCE_FPP, rcvBuffer);
		goto out;
	}

	if (rcvBytes < (sizeof(fpp_rtcp_query_res_t) + sizeof(unsigned short)))
	{
		cmm_print(DEBUG_CRIT, "Short message received from FPP \n");
		goto out;
	}


	cmm_print(DEBUG_STDOUT, 	"RTP Statistics (sport %u dport %u)\n\n"
							"prev_reception_period(ms)        : %u\n"
							"last_reception_period(ms)        : %u\n"
							"num_tx_pkts                      : %u\n"
							"num_rx_pkts                      : %u\n"
							"last_rx_Seq                      : %u\n"
							"last_rx_TimeStamp                : %u\n"
							"RTP_header                       : %x %x %x %x %x %x %x %x %x %x %x %x\n"
							"num_dup_rx                       : %u\n"
							"num_rx_since_RTCP                : %u\n"
							"num_tx_bytes                     : %u\n"
							"min_jitter(us)                   : %u\n"
							"max_jitter(us)                   : %u\n"
							"average_jitter(us)               : %u\n"
							"num_rx_lost_pkts                 : %u\n"
							"num_cumulative_rx_lost_pkts      : %u\n"
							"min_reception_period(us)         : %u\n"
							"max_reception_period(us)         : %u\n"
							"average_reception_period(us)     : %u\n"
							"num_malformed_pkts               : %u\n"
							"num_expected_pkts                : %u\n"
		  					"num_late_pkts                    : %u\n",
							ntohs(rsp->sport),
							ntohs(rsp->dport),
							rsp->prev_reception_period,
							rsp->last_reception_period,
							rsp->num_tx_pkts,
							rsp->num_rx_pkts,
							rsp->last_rx_seq,
							rsp->last_rx_timestamp,
							rsp->rtp_header[0], rsp->rtp_header[1], rsp->rtp_header[2], rsp->rtp_header[3], rsp->rtp_header[4], rsp->rtp_header[5],
							rsp->rtp_header[6], rsp->rtp_header[7], rsp->rtp_header[8], rsp->rtp_header[9], rsp->rtp_header[10], rsp->rtp_header[11],
							rsp->num_rx_dup,
							rsp->num_rx_since_rtcp,
							rsp->num_tx_bytes,
							rsp->min_jitter, rsp->max_jitter, rsp->average_jitter,
							rsp->num_rx_lost_pkts,
							rsp->num_cumulative_rx_lost_pkts,
							rsp->min_reception_period,
							rsp->max_reception_period,
							rsp->average_reception_period,
							rsp->num_malformed_pkts,
							rsp->num_expected_pkts,
		  					rsp->num_late_pkts
							);


out:
	return 0 ;
	
keyword_error:
	cmm_print(DEBUG_STDOUT,"ERR: unknown keyword %s\n", keywords[cpt]);
print_help:
	cmmRTPStatsQueryUsage();
	return -1;
}




