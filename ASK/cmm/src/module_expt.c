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

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmExptShowPrintHelp()
{
	cmm_print(DEBUG_STDOUT, "show expt not yet supported\n");
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmExptShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	cmmExptShowPrintHelp();
	return -1;
}
/************************************************************
 *
 *
 *
 *************************************************************/
int cmmExptQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	cmmExptShowPrintHelp();
        return -1;
}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmExptSetPrintHelp()
{
	cmm_print(DEBUG_STDOUT, 
                  "Usage: set expt queue {0|1|2|3} [dscp {0-63}]\n"
                  "                                      [control]\n");
}

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmExptSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	int num_dscp = 0;
	unsigned int tmp, i = 0;
	char * endptr;
	unsigned char selected_queue = 0, first_dscp = 0, last_dscp = 0, dscp_range = 0;
	unsigned char dscp_value[FPP_EXPT_MAX_DSCP + 1] = {0};
	char sndBuffer[256];
	union u_rxbuf rxbuf;
	fpp_expt_queue_dscp_cmd_t *dscpCmd = NULL;
	fpp_expt_queue_control_cmd_t *ctrlCmd = NULL;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	cmm_print(DEBUG_INFO, "Entered Expt Set Process\n");

	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		cmm_print(DEBUG_INFO, "cmmExptSetProcess - reset all queues\n");
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_EXPT_QUEUE_RESET, NULL, 0, rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_EXPT_QUEUE_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
		return (rxbuf.result);
	}
	else if(strcasecmp(keywords[cpt], "queue") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		 /*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp > FPP_EXPT_MAX_QUEUE))
		{
			cmm_print(DEBUG_STDERR, "Expt ERROR: selected queue must be a number between 0 and %d\n", FPP_EXPT_MAX_QUEUE);
			goto help;
		}
		selected_queue = tmp;
		cmm_print(DEBUG_INFO, "cmmExptSetProcess - queue %d selected\n", selected_queue);

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "dscp") == 0)
		{
			/* get list of dscp values assigned to the selected queue */
			if(!keywords[++cpt])
				goto help;
			num_dscp = 0;
			first_dscp = 0;
			cmm_print(DEBUG_INFO, "cmmExptSetProcess - parsing dscp list for queue %d\n", selected_queue);
			while(keywords[cpt])
			{
				cmm_print(DEBUG_INFO, "cmmExptSetProcess - processing arg '%s' \n", keywords[cpt]);
				if(strcasecmp(keywords[cpt], "-") == 0)
				{
					dscp_range = 1;
					cmm_print(DEBUG_INFO, "cmmExptSetProcess - dscp range detected\n");
				}
				else
				{
					endptr = NULL;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > FPP_EXPT_MAX_DSCP))
					{
						cmm_print(DEBUG_STDERR, "Expt ERROR: DSCP value out of range\n");
						goto help;
					}
					else
					{
						cmm_print(DEBUG_INFO, "cmmExptSetProcess - one more dscp added\n");
						/* save low-end dscp value i.e. the first value specified*/
						if(num_dscp == 0)
							first_dscp = tmp;
						last_dscp = tmp; /* save high end dscp i.e. the last one specified*/
						dscp_value[num_dscp++] = tmp;
					}
				}
				cpt++;
			}

			dscpCmd = (fpp_expt_queue_dscp_cmd_t *) sndBuffer;
			dscpCmd->queue = selected_queue;
			/* no dscp specified means all dscp */
			if(num_dscp == 0) 
			{
				for(i = 0; i < FPP_EXPT_MAX_DSCP; i++)
					dscpCmd->dscp[i] = i;
				dscpCmd->num_dscp = FPP_EXPT_MAX_DSCP + 1;
				cmm_print(DEBUG_INFO, "cmmExptSetProcess - all dscp assigned\n");
			}
			else if (dscp_range)
			{
				if(last_dscp <= first_dscp)
				{
					cmm_print(DEBUG_STDERR, "Expt ERROR: wrong DSCP range\n");
					goto help;
				}
				for(i = first_dscp; i <= last_dscp; i++)
					dscpCmd->dscp[i - first_dscp] = i;
				dscpCmd->num_dscp = (last_dscp - first_dscp) + 1; 
				cmm_print(DEBUG_INFO, "cmmExptSetProcess - dscp range %d to %d\n", first_dscp, last_dscp);
			}
			else
			{
				cmm_print(DEBUG_INFO, "cmmExptSetProcess - dscp non-ordered list\n");
				dscpCmd->num_dscp = num_dscp;
				for(i = 0; i < dscpCmd->num_dscp; i++)
					dscpCmd->dscp[i] = dscp_value[i];
			}
			cmm_print(DEBUG_INFO, "cmmExptSetProcess - %d dscp assigned ->\n", dscpCmd->num_dscp);
			for(i = 0; i < dscpCmd->num_dscp; i++)
				cmm_print(DEBUG_INFO, "%d ", dscpCmd->dscp[i]);
			cmm_print(DEBUG_INFO, "\n");

			if(cmmSendToDaemon(daemon_handle, FPP_CMD_EXPT_QUEUE_DSCP, sndBuffer, sizeof(fpp_expt_queue_dscp_cmd_t), rxbuf.rcvBuffer) == 2)
			{
				if (rxbuf.result != 0)
					showErrorMsg("CMD_EXPT_QUEUE_DSCP", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
				return (rxbuf.result);
			}
		}
		else if(strcasecmp(keywords[cpt], "control") == 0)
		{
			memset(sndBuffer, 0, sizeof(sndBuffer));
			ctrlCmd = (fpp_expt_queue_control_cmd_t *) sndBuffer;
			ctrlCmd->queue = selected_queue;
			cmm_print(DEBUG_INFO, "cmmExptSetProcess - control assigned to queue %d\n", ctrlCmd->queue);

			if(cmmSendToDaemon(daemon_handle, FPP_CMD_EXPT_QUEUE_CONTROL, sndBuffer, sizeof(fpp_expt_queue_control_cmd_t), rxbuf.rcvBuffer) == 2)
			{
				if (rxbuf.result)
					showErrorMsg("CMD_EXPT_QUEUE_CONTROL", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
				return (rxbuf.result);
			}
		}
		else {
			cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);
			goto help;
		}

		return 0;
	}
	

help:
	cmmExptSetPrintHelp();
	return -1;
}


