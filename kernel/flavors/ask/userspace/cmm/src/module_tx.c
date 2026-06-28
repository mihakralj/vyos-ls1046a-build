/*
 *  Copyright 2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */

#ifdef LS1043
#include <ctype.h>
#include <limits.h>
#include "cmm.h"
#include "fpp.h"
#include "module_tx.h"



/*
 * This function query DSCP VLAN PCP mapping. It gets mapping status on interface, if it is enable
 * it also gets each DSCP mapped VLAN PCP value.
*/
int cmmDSCPVlanPcpMapQueryProcess(char ** keywords, int cpt, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
	short rc;
	short index;
	fpp_query_dscp_vlanpcp_map_cmd_t *pDscpVlanPcpMapCmd = (fpp_query_dscp_vlanpcp_map_cmd_t *)&rxbuf.rcvBuffer;

	if(!keywords[cpt])
		goto help;
	memset(pDscpVlanPcpMapCmd, 0, sizeof(fpp_query_dscp_vlanpcp_map_cmd_t));
	if (get_port_id(keywords[cpt]) >= 0)
	{
		STR_TRUNC_COPY(pDscpVlanPcpMapCmd->ifname, keywords[cpt], sizeof(pDscpVlanPcpMapCmd->ifname));
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid interface name(%s)\n", keywords[cpt]);
		goto help;
	}

	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QUERY_IFACE_DSCP_VLANPCP_MAP,
			pDscpVlanPcpMapCmd, sizeof(fpp_query_dscp_vlanpcp_map_cmd_t), rxbuf.rcvBuffer);

	if (rcvBytes < sizeof(fpp_query_dscp_vlanpcp_map_cmd_t)  ) {
		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
		cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP_CMD_QUERY_IFACE_DSCP_VLANPCP_MAP cmd, rc:%d rcvBytes %d\n", rc, rcvBytes);
		return CLI_OK;
	}

	cmm_print(DEBUG_STDOUT, "DSCP VLAN PCP Map :\n");
	cmm_print(DEBUG_STDOUT, "Status : %s\n", pDscpVlanPcpMapCmd->enable ? "Enable" : "Disable");
	if (pDscpVlanPcpMapCmd->enable)
	{
		cmm_print(DEBUG_STDOUT, "Below information is VLAN PCP configured for dscp value groups:\n");
		for (index = 0; index <= MAX_VLAN_PCP; index++)
			cmm_print(DEBUG_STDOUT, "dscp[%d - %d] : %d\n", index*8, (((index + 1)*8) - 1), pDscpVlanPcpMapCmd->vlan_pcp[index]);
	}
	return CLI_OK;
help:
	cmm_print(DEBUG_STDOUT, "Usage: query tx-dscp-to-vlanpcp {physical interface name}\n");
	return CLI_OK;
}


/************************************************************
 *
 * This function prints the command usage help.
 *
 ************************************************************/

static void cmmDscpVlanPcpMapSetPrintHelp(void)
{
	cmm_print(DEBUG_STDOUT, 
		"Usage:\n"
		"\n"
		"	set tx dscp-to-vlanpcp [iface name] enable|disable\n"
		"	set tx dscp-to-vlanpcp [iface name] dscp [0-7] vlan-pcp [0-7]\n"
		"		Here dscp input value is 3 most significant bits value of dscp\n"
		"		Like below :\n"
		"		msb(3bits) of dscp value 0-7   is 0\n"
		"		msb(3bits) of dscp value 8-15  is 1\n"
		"		msb(3bits) of dscp value 16-23 is 2\n"
		"		msb(3bits) of dscp value 24-31 is 3\n"
		"		msb(3bits) of dscp value 32-39 is 4\n"
		"		msb(3bits) of dscp value 40-47 is 5\n"
		"		msb(3bits) of dscp value 48-55 is 6\n"
		"		msb(3bits) of dscp value 56-63 is 7\n"
                "\n"
                "\n"
		);
}


/*
 * This function does the following actions.
 *  1. Enable/Disable DSCP to VLAN P bit(PCP) mapping on an interface.
 *  2. Maps specific DSCP most siginificant 3 bits value with VLAN P bits value.
 * It returns SUCCESS after successful configuration,
 * otherwise returns ERROR.
*/
static int dscp_vlanpcpmap_cfg(char **keywords, int *pcpt, daemon_handle_t daemon_handle)
{
	fpp_dscp_vlanpcp_map_t dscp_vlanpcp_map_cmd;
	union u_rxbuf rxbuf;
	int cpt;
	int cmd = 0;
	uint32_t val;

	cpt = *pcpt;
	if(!keywords[++cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: interface name(%s) is invalid\n", keywords[cpt]);
		return ERROR;
	}

	memset(&dscp_vlanpcp_map_cmd, 0, sizeof(dscp_vlanpcp_map_cmd));
	if (get_port_id(keywords[cpt]) >= 0)
	{
		STR_TRUNC_COPY(dscp_vlanpcp_map_cmd.ifname, keywords[cpt], sizeof(dscp_vlanpcp_map_cmd.ifname));
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid interface name(%s)\n", keywords[cpt]);
		return ERROR;
	}

	/* handle dscp or enable/disable */
	if(!keywords[++cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting dscp, vlan pcp configuration or enable/disable\n");
		return ERROR;
	}
	if(strcasecmp(keywords[cpt], "enable") == 0) {
		dscp_vlanpcp_map_cmd.status = 1;
		cmd = FPP_CMD_DSCP_VLANPCP_MAP_STATUS;
		goto send_cmd;
	}
	else if(strcasecmp(keywords[cpt], "disable") == 0) {
		dscp_vlanpcp_map_cmd.status = 0;
		cmd = FPP_CMD_DSCP_VLANPCP_MAP_STATUS;
		goto send_cmd;
	}
	else if(strcasecmp(keywords[cpt], "dscp") == 0) {
		/* Get dscp number from the string */
		/* dscp value range is 0-63, but storing only 8 entries, it means
		 * the dscp range 0-63(64 entries ) should fit into 8 entries. For this 
		 * considering dscp value 3 most significant bits, it value range is 0-7,
		 * same like vlan pcp value range 0-7. So, that avoid dscp tp vlan-pcp 
		 * many-to-one mapping. So, dscp max value is also checking with MAX_VLAN_PCP.
		 */
		if (qm_get_num(keywords, &cpt, MAX_VLAN_PCP, &val, "invalid dscp value\n"))
			return ERROR;
		dscp_vlanpcp_map_cmd.dscp = (uint8_t)val;
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting dscp configuration\n");
		return ERROR;
	}

	/* handle vlan pcp */
	if(!keywords[cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting vlan pcp configuration\n");
		return ERROR;
	}
	if(strcasecmp(keywords[cpt], "vlan-pcp") == 0) {
		/* Get channel id number from the string */
		if (qm_get_num(keywords, &cpt, MAX_VLAN_PCP, &val, "invalid vlan pcp value\n"))
			return ERROR;
		dscp_vlanpcp_map_cmd.vlan_pcp = (uint8_t)val;
		cmd = FPP_CMD_DSCP_VLANPCP_MAP_CFG;
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting vlan pcp configuration\n");
		return ERROR;
	}

send_cmd:
	/* Send the command to CDX */
	if(cmmSendToDaemon(daemon_handle, cmd, &dscp_vlanpcp_map_cmd, sizeof(dscp_vlanpcp_map_cmd),
		&rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
		{
			if (cmd == FPP_CMD_DSCP_VLANPCP_MAP_STATUS)
				showErrorMsg("FPP_CMD_DSCP_VLANPCP_MAP_STATUS", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			else if (cmd == FPP_CMD_DSCP_VLANPCP_MAP_CFG)
				showErrorMsg("FPP_CMD_DSCP_VLANPCP_MAP_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			else
				showErrorMsg("Invalid cmd", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	return SUCCESS;
}

int cmmTxSetProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt;
	int retval;

	cpt = tabStart;
	if (!keywords[cpt])  {
		retval = ERROR;
		goto err_ret;
	} else
		retval = INVALID_KEYWORD;
	while(1)
	{
		/* handle DSCP to VLAN PCP mapping configuration */
		if(strcasecmp(keywords[cpt], "dscp-to-vlanpcp") == 0)
		{
			retval = dscp_vlanpcpmap_cfg(keywords, &cpt, daemon_handle);
			break;
		}
		break;
	} 
err_ret:
	switch(retval) {
		case INVALID_KEYWORD:
			cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);
		case ERROR:
			cmmDscpVlanPcpMapSetPrintHelp();
			break;
		default:
			return 0;
	}
	return -1;
}
#endif

