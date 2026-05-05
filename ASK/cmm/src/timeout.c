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
#include <ctype.h>

/*****************************************************************
* cmmSetTimeout
******************************************************************/
int timeoutSet(daemon_handle_t daemon_handle, char *argv[], int argc) 
{

	unsigned short proto;
	unsigned long long in_val1, in_val2 =0;
	fpp_timeout_cmd_t cmd;
	union u_rxbuf rxbuf;
	int arg =0;
	
	memset(&cmd, 0, sizeof(fpp_timeout_cmd_t));
	
	if ((argc < 2) || (argc > 4))
		goto usage;
	
	if(strncmp(argv[arg], "udp", strlen(argv[arg])) == 0)
		proto = IPPROTO_UDP;
	else if (strncmp(argv[arg], "tcp", strlen(argv[arg])) == 0) 
		proto = IPPROTO_TCP;
	else if (strncmp(argv[arg], "other", strlen(argv[arg])) == 0)
		proto = 0;
	else
		goto usage;
	
	in_val1 = cmm_convert_to_numeric( argv[++arg] );
	if( !in_val1 )
	  return -1;
 
	while(++arg < argc) // we need to look for some optional parameters
	{
		if(strncmp(argv[arg],"4rd", strlen(argv[arg])) == 0)
			cmd.sam_4o6_timeout = 1;
		else
		{
			in_val2 = cmm_convert_to_numeric( argv[arg] );
			if( !in_val2 )
				return -1;
		} 
	}
	
	if ((in_val1 <= 0) || (in_val1 >= UINT32_MAX)) // Cover negative, max values, null values and errors
		goto usage;
        else if (in_val2 >= UINT32_MAX)
		goto usage;
	else 
	{
		cmd.protocol = proto;
		cmd.timeout_value1 = in_val1;
		cmd.timeout_value2 = in_val2;
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_IPV4_SET_TIMEOUT, &cmd, sizeof(cmd), &rxbuf.rcvBuffer) == 2)
		{
			if(rxbuf.result != 0)
				showErrorMsg("CMD_IPV4_SET_TIMEOUT", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
		return 0;
	}

usage:
	cmm_print(DEBUG_STDERR, "Usage: set timeout <udp tcp other> value [value2] [4rd]\n");
	return -1;
}

unsigned long long  cmm_convert_to_numeric(char *str )
{
    int i;
     
    if( strlen(str) > MAX_TIMEOUT_STR_LEN )
    {
       cmm_print(DEBUG_STDERR,"Too big string for timeout value\n");
       return 0;
    } 

    for(i=0;i<strlen(str);i++)
    {
       if( !isdigit(str[i] ))
       {
          cmm_print(DEBUG_STDERR,"timeout value should be numeric string \n");
          return 0;
       }
    } 
    return (unsigned long long)strtoll(str, NULL, 10);
}
/*****************************************************************
* cmmSetTimeout
******************************************************************/
int cmmtimeoutSet(daemon_handle_t daemon_handle, char *argv[], int argc, int tab) 
{
	if(argc > tab)
		return timeoutSet(daemon_handle, &argv[tab], argc - tab);
	else
                cmm_print(DEBUG_ERROR, "Usage: set timeout <udp tcp other> value [value2] [4rd]\n");

	return 0;
}

int cmmFeGetTimeout(FCI_CLIENT *fci_handle, struct ctTable *ctEntry, unsigned int *timeout)
{
	unsigned short rcvBuffer[256];
	unsigned short rcvBytes = 0;
	fpp_timeout_cmd_t *conntime;
	fpp_ct_cmd_t cmd;
	fpp_ct6_cmd_t cmd6;
	int rc;

	if (ctEntry->family == AF_INET)
	{
		memset(&cmd, 0, sizeof(fpp_ct_cmd_t));

		cmd.protocol = nfct_get_attr_u8(ctEntry->ct, ATTR_ORIG_L4PROTO);

		cmd.saddr = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_IPV4_SRC);
		cmd.daddr = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_IPV4_DST);
		cmd.saddr_reply = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_IPV4_SRC);
		cmd.daddr_reply = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_IPV4_DST);

		cmd.sport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_SRC);
		cmd.dport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_DST);
		cmd.sport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_SRC);
		cmd.dport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_DST);

		rc = fci_query(fci_handle, FPP_CMD_IPV4_GET_TIMEOUT, sizeof(fpp_ct_cmd_t), (unsigned short *)&cmd, &rcvBytes, rcvBuffer);
	}
	else
	{
		memset(&cmd6, 0, sizeof(fpp_ct6_cmd_t));

		cmd6.protocol = nfct_get_attr_u8(ctEntry->ct, ATTR_ORIG_L4PROTO);

		memcpy(cmd6.saddr, nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV6_SRC), 16);
		memcpy(cmd6.daddr, nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV6_DST), 16);
		memcpy(cmd6.saddr_reply, nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV6_SRC), 16);
		memcpy(cmd6.daddr_reply, nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV6_DST), 16);
	
		cmd6.sport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_SRC);
		cmd6.dport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_DST);
		cmd6.sport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_SRC);
		cmd6.dport_reply = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_DST);

		rc = fci_query(fci_handle, FPP_CMD_IPV6_GET_TIMEOUT, sizeof(fpp_ct6_cmd_t), (unsigned short *)&cmd6, &rcvBytes, rcvBuffer);
	}

	if (rc != 0)
	{
		cmm_print(DEBUG_ERROR, "%s: error %d received from FPP for CMD_IPV4/IPV6_GET_TIMEOUT\n", __func__, rc);
		goto err;
	}


	if (rcvBytes != sizeof(fpp_timeout_cmd_t))
	{
		cmm_print(DEBUG_ERROR, "%s: wrong response length %d received from FPP for CMD_IPV4/IPV6_GET_TIMEOUT\n", __func__, rcvBytes);
		goto err;
	}

	conntime = (fpp_timeout_cmd_t *)rcvBuffer;
	*timeout = conntime->timeout_value1;

	return 0;

err:
	return -1;
}

static void FragTimeoutSetUsage()
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: set frag  \n"
			"\n"
			"                                  [ipv4 | ipv6 |  sam-ipv4]\n"
			"                                  [timeout {0-65536} (in ms)] \n"
			"                                  [mode {acp | drop} (not supported for sam-ipv4)]\n"
		);
}

int cmmFragTimeoutSet(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	char rcvBuffer[256];
	int command;	
	fpp_frag_timeout_cmd_t cmd;


        if(!keywords[cpt])
               goto keyword_error;

	if (!strcasecmp(keywords[cpt], "ipv6"))
		command = FPP_CMD_IPV6_FRAGTIMEOUT;
	else if (!strcasecmp(keywords[cpt], "ipv4"))
		command = FPP_CMD_IPV4_FRAGTIMEOUT;
        else if(!strcasecmp(keywords[cpt], "sam-ipv4"))
               command = FPP_CMD_IPV4_SAMFRAGTIMEOUT;
	else
		goto keyword_error;


	if (!keywords[++cpt])
			goto print_help;

	if (strcasecmp(keywords[cpt], "timeout") == 0)
	{
		if (!keywords[++cpt])
			goto print_help;
		
		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp > USHRT_MAX))
		{
			cmm_print(DEBUG_CRIT, "ERROR: timeout must be a number between 0 and %d\n", USHRT_MAX);
			goto  print_help;
		}
		cmd.timeout = tmp;
	}
	else
		goto keyword_error;


	if(command == FPP_CMD_IPV4_SAMFRAGTIMEOUT)
	{
		if(keywords[++cpt])
		{
			cmm_print(DEBUG_CRIT,"ERROR: %s not supported for type sam-ipv4 \n",keywords[cpt]);
			goto keyword_error;
		}
	}
	else 
	{
		if(!keywords[++cpt])
			goto print_help;
		if(strcasecmp(keywords[cpt], "mode") == 0) 
		{
			if(!keywords[++cpt])
				goto print_help;

			if(strcasecmp(keywords[cpt], "drop") == 0)
			{
				cmd.mode = 1; 
			}
			else if(strcasecmp(keywords[cpt], "acp") == 0)
			{
				cmd.mode = 0;
			}
			else
			{
				cmm_print(DEBUG_CRIT, "frag ERROR: mode must be either drop or acp \n");
				goto print_help;
			}
		}
		else
			goto keyword_error;
	}

	if (cmmSendToDaemon(daemon_handle, command, &cmd, sizeof(cmd), rcvBuffer) < sizeof(unsigned short) || cmmDaemonCmdRC(rcvBuffer))
		cmm_print(DEBUG_CRIT,  "%s: error sending message to daemon\n", __func__);
	
	return 0;



keyword_error:
	cmm_print(DEBUG_STDOUT,"ERR: unknown keyword %s\n", keywords[cpt]);

print_help:
	FragTimeoutSetUsage();
	return -1;
}
