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
#include "itf.h"
#include "fpp.h"
#include "cmmd.h"
#include <string.h>

#if 0
#include <linux/sockios.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <net/if.h>
#endif
#include <net/if_arp.h>

#if 0
static int macvlan_get_phys_interface(struct interface *itf, struct interface **phy_itf)
{
	struct interface *pitf = itf;

	while(1) {
		if (pitf->ifindex == pitf->phys_ifindex) {
			if (phy_itf != NULL)
				*phy_itf = pitf;
			return pitf->ifindex;
		}

		pitf = __itf_get(pitf->phys_ifindex);
		if (pitf == NULL)
			break;
	}

	return -1;
}
#endif

/*********************************************************************
 * __cmmGetMacVlan:
 *
 ********************************************************************/
void __cmmGetMacVlan(int fd, struct interface *itf)
{
	itf->itf_flags &= ~ITF_MACVLAN;
	if (!strcmp(itf->link_kind, LINK_KIND_MACVLAN))
		itf->itf_flags |= ITF_MACVLAN;
}

/*****************************************************************
* cmmFeMacVlanUpdate
*
*****************************************************************/
int cmmFeMacVlanUpdate(FCI_CLIENT *fci_handle, int fd, int request, 
				struct interface *itf)
{
	fpp_macvlan_cmd_t cmd;
	short ret;
	int action;

	switch (request)
	{
	default:
	case ADD:
		if ((itf->flags & FPP_PROGRAMMED) == FPP_PROGRAMMED)
			goto out;

		action = FPP_ACTION_REGISTER;
		break;
	case REMOVE:
		if (!(itf->flags & FPP_PROGRAMMED))
			goto out;

		action = FPP_ACTION_DEREGISTER;
		break;
	}

	memset(&cmd, 0, sizeof(cmd));
	cmd.action = action;
	if (____itf_get_name(itf, cmd.macvlan_ifname, sizeof(cmd.macvlan_ifname)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);

		goto err;
	}

	/* Get parent interface name */
	if (__itf_get_name(itf->phys_ifindex , cmd.macvlan_phy_ifname, sizeof(cmd.macvlan_phy_ifname)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, itf->phys_ifindex);

		goto err;
	}

	memcpy(cmd.macaddr, itf->macaddr, itf->macaddr_len);
	switch (action)
	{
	case FPP_ACTION_REGISTER:
		cmm_print(DEBUG_COMMAND, "Send FPP_CMD_MACVLAN_ENTRY FPP_ACTION_REGISTER\n");

		ret = fci_write(fci_handle, FPP_CMD_MACVLAN_ENTRY, sizeof(fpp_macvlan_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_MACVLAN_ENTRY_ALREADY_REGISTERED))
		{
			cmm_print(DEBUG_COMMAND, "Send FPP_CMD_MACVLAN_ENTRY FPP_ACTION_REGISTER: success\n");
			itf->flags |= FPP_PROGRAMMED;
			//itf->flags &= ~FPP_NEEDS_UPDATE; FIXME
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending FPP_CMD_MACVLAN_ENTRY, FPP_ACTION_REGISTER\n", __func__, ret);
			goto err;
		}
		break;
	case FPP_ACTION_DEREGISTER:
	
		cmm_print(DEBUG_COMMAND, "Send FPP_CMD_MACVLAN_ENTRY FPP_ACTION_DEREGISTER\n");

		ret = fci_write(fci_handle, FPP_CMD_MACVLAN_ENTRY, sizeof(fpp_macvlan_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_MACVLAN_ENTRY_NOT_FOUND))
		{
			cmm_print(DEBUG_COMMAND, "Send FPP_CMD_MACVLAN_ENTRY FPP_ACTION_DEREGISTER: success\n");
			itf->flags &= ~FPP_PROGRAMMED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending FPP_CMD_MACVLAN_ENTRY, FPP_ACTION_DEREGISTER\n", __func__, ret);
			goto err;
		}
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unknown FPP_CMD_MACVLAN_ENTRY action %x\n", __func__, action);
		break;
	}
out:
	return 0;

err:
	return -1;
}

/*******************************************************************
 * cmmMacVlanQuery
 * 
 *******************************************************************/
int cmmMacVlanQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        short rc;
        int count = 0;
        cmmd_macvlan_cmd_t* pMacVlanCmd = (cmmd_macvlan_cmd_t *) rxbuf.rcvBuffer;

        pMacVlanCmd->action = CMMD_ACTION_QUERY;
        rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_MACVLAN_ENTRY, pMacVlanCmd,
                                  sizeof(cmmd_macvlan_cmd_t) , rxbuf.rcvBuffer);

        if (rcvBytes < (int)sizeof(cmmd_macvlan_cmd_t) ) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == CMMD_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR,
                         "ERROR: FPP MACVLAN does not support CMMD_ACTION_QUERY\n");
                } else if (rc == CMMD_ERR_MACVLAN_ENTRY_NOT_FOUND) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP MACVLAN table empty\n");
                } else {
                    cmm_print(DEBUG_STDERR,
                            "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }
            cmm_print(DEBUG_STDOUT, "MACVLAN interfaces:\n");
            do {
                        cmm_print(DEBUG_STDOUT, "Interface: %s, Physical Interface: %s HWaddr: %02x:%02x:%02x:%02x:%02x:%02x \n", pMacVlanCmd->macvlan_ifname, pMacVlanCmd->macvlan_phy_ifname,
                        pMacVlanCmd->macaddr[0],pMacVlanCmd->macaddr[1],
                        pMacVlanCmd->macaddr[2],pMacVlanCmd->macaddr[3],
                        pMacVlanCmd->macaddr[4],pMacVlanCmd->macaddr[5]);
                        count++;
                        pMacVlanCmd->action = CMMD_ACTION_QUERY_CONT;
                        rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_MACVLAN_ENTRY, pMacVlanCmd,
                                           sizeof(cmmd_macvlan_cmd_t) , rxbuf.rcvBuffer);
           }while (rcvBytes >= sizeof(cmmd_macvlan_cmd_t) );
           cmm_print(DEBUG_STDOUT, "Total MACVLAN Entries:%d\n", count);

        return CLI_OK;
}

/*****************************************************************
* cmmMacVlanLocalShow
*
*
******************************************************************/
int cmmMacVlanLocalShow(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	struct list_head *entry;
	struct interface *itf;
	char ifname[IFNAMSIZ], phys_ifname[IFNAMSIZ];
	int i;

	cli_print(cli,"MACVLAN Interfaces:"); 
	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&itf_table.lock);

		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_macvlan(itf))
				continue;

			cli_print(cli, "Interface: %s, Physical Interface: %s, HWaddr: %02x:%02x:%02x:%02x:%02x:%02x, %s", if_indextoname(itf->ifindex, ifname), if_indextoname(itf->phys_ifindex, phys_ifname), itf->macaddr[0], itf->macaddr[1], itf->macaddr[2], itf->macaddr[3], itf->macaddr[4], itf->macaddr[5], (__itf_is_up(itf)?"UP":"DOWN"));
		}

		__pthread_mutex_unlock(&itf_table.lock);
	}

	return CLI_OK;
}
