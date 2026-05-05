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

#include <signal.h>
#include <net/if.h>
#include <sys/time.h>

#include "cmm.h"
#include "itf.h"
#include "ffbridge.h"
#include "cmmd.h"

/*****************************************************************
* cmmAsymFFEnableShow
*
*
******************************************************************/
int cmmAsymFFEnableShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	if(globalConf.asymff_enable)
		cli_print(cli, " The Asymmetric Fast forward support is enabled");
	else
		cli_print(cli, " The Asymmetric Fast forward support is disabled");
	return CLI_OK;
}

int cmmAsymFFProcessClientCmd(u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len)
{
        cmmd_asymff_enable_t    *entryCmd = (cmmd_asymff_enable_t*) cmd_buf;

        cmm_print(DEBUG_INFO, "cmmAsymFFProcessClientCmd\n");

        res_buf[0] = CMMD_ERR_OK;
        *res_len = 2;

        switch (entryCmd->action) {
                case CMMD_ASYM_FF_ACTION_ENABLE:
                        cmm_print(DEBUG_INFO, "cmmAsymFFProcessClientCmd- CMMD_ASYM_FF_ACTION_ENABLE\n");
                        globalConf.asymff_enable = 1;
                        break;

                case CMMD_ASYM_FF_ACTION_DISABLE:
                        cmm_print(DEBUG_INFO, "cmmAsymFFProcessClientCmd- CMMD_ASYM_FF_ACTION_DISABLE\n");
                        globalConf.asymff_enable = 0;
                        break;

                default:
                        res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
                        break;
        }
        return 0;
}

void cmmAsymFFPrintHelp(int cmd_type)
{
        if (cmd_type == ASYMFF_UNKNOWN_CMD || cmd_type == ASYMFF_ENABLE_CMD)
        {
            cmm_print(DEBUG_STDOUT, "Usage: set asymff enable \n"
                                    "       set asymff disable \n");
        }
}

int cmmAsymFFSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cmd_type = ASYMFF_UNKNOWN_CMD;
	int cpt = tabStart;
	int rc;

	char sndBuffer[256];
	union u_rxbuf rxbuf;
	cmmd_asymff_enable_t* entryCmd = (cmmd_asymff_enable_t*) sndBuffer;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	cmm_print(DEBUG_INFO, "Entered Asymmetric Fast forward Set Process\n");

	if(!keywords[cpt])
		goto help;

	if( (strcasecmp(keywords[cpt], "enable") == 0) ||
	    (strcasecmp(keywords[cpt], "disable") == 0) )
	{
		cmd_type = ASYMFF_ENABLE_CMD;

		if(strcasecmp(keywords[cpt], "enable") == 0)
			entryCmd->action = CMMD_ASYM_FF_ACTION_ENABLE;
		else
			entryCmd->action = CMMD_ASYM_FF_ACTION_DISABLE;
	}
	else
		goto keyword_error;

	rc = cmmSendToDaemon(daemon_handle, CMMD_ASYM_FF_ENABLE, sndBuffer, sizeof(cmmd_asymff_enable_t), rxbuf.rcvBuffer);
	if(rc != 2)
	{
		if(rc >= 0)
			cmm_print(DEBUG_STDERR, "Unexpected response size for CMMD_ASYM_FF_ENABLE: %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != CMMD_ERR_OK)
	{
		showErrorMsg("CMMD_ASYM_FF_ENABLE", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
		return -1;
	}
        
	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmAsymFFPrintHelp(cmd_type);
	return -1;
}

