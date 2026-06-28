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
#include "fpp.h"

#include <linux/if_vlan.h>
#include <linux/sockios.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>


/*
** cmmAltconfClient
** Client side demux - check input and find client side processor for it
*/
int cmmAltConfClient(int argc,char **argv,int firstarg ,daemon_handle_t daemon_handle)
{
	unsigned int option_id;
	unsigned int num_params;
	unsigned int params[FPP_ALTCONF_OPTION_MAX_PARAMS];
        char * endptr;
        unsigned long tmp;

	cmm_print(DEBUG_COMMAND, "cmmAltConfClient\n");
	if(!argv[firstarg])
		goto usage;

	if (argc <= firstarg) 
		goto usage;

	/* get option ID */
	if (strncasecmp(argv[firstarg], "all", 1) == 0) {
		if(argc < 2)
			goto usage;
		cmm_print(DEBUG_COMMAND, "cmmAltConfClient: ALTCONF_OPTION_ALL / ALTCONF_ALL_NUM_PARAMS (argc %d)\n", argc);
		option_id = FPP_ALTCONF_OPTION_ALL; 
		num_params = FPP_ALTCONF_ALL_NUM_PARAMS;
		if (strncasecmp(argv[firstarg+1], "default", 1) == 0)
			return altconfResetProcess(daemon_handle);
		else
			goto usage; 
	}
	else if (strncasecmp(argv[firstarg], "mcttl", 1) == 0) {
		if(argc < 2)
			goto usage;
		cmm_print(DEBUG_COMMAND, "cmmAltConfClient: ALTCONF_OPTION_MCTTL / ALTCONF_MCTTL_NUM_PARAMS (argc %d)\n", argc);
		option_id = FPP_ALTCONF_OPTION_MCTTL; 
		num_params = FPP_ALTCONF_MCTTL_NUM_PARAMS;
		if (strncasecmp(argv[firstarg+1], "default", 1) == 0)
			params[0] = FPP_ALTCONF_MCTTL_MODE_DEFAULT;
		else if	(strncasecmp(argv[firstarg+1], "ignore", 1) == 0)
			params[0] = FPP_ALTCONF_MCTTL_MODE_IGNORE;
		else
			goto usage;	

		return altconfSetProcess(daemon_handle, option_id, num_params, params);
	}
	else if (strcasecmp(argv[firstarg], "ipsecrl") == 0) {
		if(argc < 2)
			goto usage;
		cmm_print(DEBUG_COMMAND, "cmmAltConfClient: ALTCONF_OPTION_IPSECRL / ALTCONF_MCTTL_NUM_PARAMS (argc %d)\n", argc);
		option_id = FPP_ALTCONF_OPTION_IPSECRL; 
		num_params = FPP_ALTCONF_IPSECRL_NUM_PARAMS;
		if(!argv[firstarg+1])
			goto usage;

		if (strcasecmp(argv[firstarg+1], "on") == 0){
			params[0] = FPP_ALTCONF_IPSECRL_ON; /* Setting action as on */
			if((argv[firstarg+2] != NULL) && (strcasecmp(argv[firstarg+2], "rate") == 0))
			{
				if(!argv[firstarg+3])
					goto usage;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(argv[firstarg+3], &endptr, 0);
				if ((argv[firstarg+3] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rate_limiting ERROR: rate must be a number between 8 and %u (Kbps)\n", UINT_MAX);
					goto usage;
				}

				params[1] = tmp; /* Setting bandwidth */
				params[2] = 0; /* Setting bucketsize as bucketsize parameter is option. FPP sets to default value */
			}
			else
			 goto usage;

			/* bucket_size parameter is optional */
			if((argv[firstarg+4] != NULL) && (strcasecmp(argv[firstarg+4], "bucket_size") == 0))
			{
				if(!argv[firstarg+5])
					goto usage;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(argv[firstarg+5], &endptr, 0);
				if ((argv[firstarg+5] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "rate_limiting ERROR: bucket_size must be a number between 8 and %u\n", UINT_MAX);
					goto usage;
				}

				params[2] = tmp; /* Setting bucket size */
			}
			
                }
		else if	(strcasecmp(argv[firstarg+1], "off") == 0)
		{
			params[0] = FPP_ALTCONF_IPSECRL_OFF; /* Setting action as off */
                }
		else
			goto usage;	

		return altconfSetProcess(daemon_handle, option_id, num_params, params);
	}
	else
		goto usage;

usage:
	cmm_print(DEBUG_STDOUT, "Usage:\n\tconfig [option] [mode]\n");
	cmm_print(DEBUG_STDOUT, "\tconfig mcttl default\n");
	cmm_print(DEBUG_STDOUT, "\tconfig mcttl ignore\n");
	cmm_print(DEBUG_STDOUT, "\tconfig ipsecrl {on|off}\n"
                                    "\t\trate {Kbps}\n"
                                    "\t\t[bucket_size {bits}]\n");
	cmm_print(DEBUG_STDOUT, "\tconfig all default\n");
	return 0;  
}

/*
** altconfResetProcess
** 
*/
int altconfResetProcess(daemon_handle_t daemon_handle)
{
	char rspbuf[512];
	int rsplen; 

	 cmm_print(DEBUG_COMMAND, "altconfResetProcess\n");

	if (((rsplen = cmmSendToDaemon(daemon_handle,FPP_CMD_ALTCONF_RESET,NULL,0,rspbuf)) < sizeof(unsigned short) ) || cmmDaemonCmdRC(rspbuf))
	{
		showErrorMsg("CMD_ALTCONF_RESET", ERRMSG_SOURCE_FPP, rspbuf);
		/*  break; */ return 0;
	}  

	return 0;
}

/*
** altconfSetProcess
**
*/
int altconfSetProcess(daemon_handle_t daemon_handle, unsigned int option_id, unsigned int num_params, unsigned int *params)
{
	char rspbuf[512];
	int rsplen, i;
	fpp_alt_set_cmd_t cmd;

	cmm_print(DEBUG_COMMAND, "altconfSetProcess optionID 0x%04x, num_params %d\n", option_id, num_params);

	cmd.option_id = option_id;
	cmd.num_params = num_params;

	for(i = 0; i < num_params; i++) {
		cmd.params[i] = params[i];
		cmm_print(DEBUG_COMMAND, "altconfSetProcess params[%d] 0x%x\n", i, params[i]);
	}

	if (((rsplen = cmmSendToDaemon(daemon_handle,FPP_CMD_ALTCONF_SET,&cmd,sizeof(cmd),rspbuf)) < sizeof(unsigned short) ) ||	cmmDaemonCmdRC(rspbuf))
	{
		showErrorMsg("CMD_ALTCONF_SET", ERRMSG_SOURCE_FPP, rspbuf);
		/*  break; */ return 0;
	}  

	return 0;
}


