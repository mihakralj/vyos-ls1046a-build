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

#include <linux/if_vlan.h>
#include <linux/sockios.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>

static int cmmVlanFillCmdFromState(struct interface *itf, fpp_vlan_cmd_t *cmd,
				   int action, int use_programmed_state)
{
	cmd->action = action;

	if (____itf_get_name(itf, cmd->vlan_ifname, sizeof(cmd->vlan_ifname)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		return -1;
	}

	if (use_programmed_state)
	{
		cmd->vlan_id = itf->fpp_prog_vlan_id;

		if (__itf_get_name(itf->fpp_prog_phys_ifindex, cmd->vlan_phy_ifname,
				   sizeof(cmd->vlan_phy_ifname)) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n",
				  __func__, itf->fpp_prog_phys_ifindex);
			return -1;
		}
	}
	else
	{
		cmd->vlan_id = itf->vlan_id;

		if (__itf_get_name(itf->phys_ifindex, cmd->vlan_phy_ifname,
				   sizeof(cmd->vlan_phy_ifname)) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n",
				  __func__, itf->phys_ifindex);
			return -1;
		}
	}

#if defined(LS1043)
	memcpy(cmd->macaddr, itf->macaddr, ETH_ALEN);
#endif
	return 0;
}

static void cmmVlanStoreProgrammedState(struct interface *itf)
{
	itf->fpp_prog_phys_ifindex = itf->phys_ifindex;
	itf->fpp_prog_vlan_id = itf->vlan_id;
}


void __cmmGetVlan(int fd, struct interface *itf)
{
	struct vlan_ioctl_args if_request;

	/* Sometimes VLAN interface is deleted and added back with new ifindex , where the deleted vlan
	interface's vlan flag is not getting set, causing VLAN not to get DEREGISTERED 
	when it is deleted with out explicit DOWN event. If the flag is set then it will
	get deleted with delete event , This is observed while doing bridge configuration
	with VLANs */

	/*itf->itf_flags &= ~ITF_VLAN;*/

	if (itf->phys_ifindex == itf->ifindex)
		goto out;

	memset(&if_request, 0, sizeof(if_request));

	if_request.cmd = GET_VLAN_VID_CMD;
	if (____itf_get_name(itf, if_request.device1, sizeof(if_request.device1)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		goto out;
	}

	if (ioctl(fd, SIOCGIFVLAN, &if_request) < 0)
		goto out;

	itf->itf_flags |= ITF_VLAN;
	itf->vlan_id = if_request.u.VID;

out:
	return;
}
/*****************************************************************
* cmmFeVLANUpdate
*
*
******************************************************************/
int cmmFeVLANUpdate(FCI_CLIENT *fci_handle, int request, struct interface *itf)
{
	fpp_vlan_cmd_t cmd;
	short ret = CMMD_ERR_OK;

	switch (request)
	{
	default:
	case ADD:
		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED)
			goto out;

		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == (FPP_PROGRAMMED | FPP_NEEDS_UPDATE))
		{
			cmm_print(DEBUG_INFO, "%s: refreshing programmed vlan interface(%d)\n",
				  __func__, itf->ifindex);

			memset(&cmd, 0, sizeof(cmd));
			if (cmmVlanFillCmdFromState(itf, &cmd, FPP_ACTION_DEREGISTER, 1) < 0)
			{
				ret = CMMD_ERR_WRONG_COMMAND_PARAM;
				goto out;
			}

			ret = fci_write(fci_handle, FPP_CMD_VLAN_ENTRY,
					sizeof(fpp_vlan_cmd_t), (unsigned short *)&cmd);
			if ((ret != FPP_ERR_OK) && (ret != FPP_ERR_VLAN_ENTRY_NOT_FOUND))
			{
				cmm_print(DEBUG_ERROR,
					  "%s: Error %d while refreshing old vlan interface(%d)\n",
					  __func__, ret, itf->ifindex);
				goto out;
			}

			itf->flags &= ~FPP_PROGRAMMED;
		}

		break;

	case REMOVE:
		if (!(itf->flags & FPP_PROGRAMMED))
			goto out;

		break;
	}

	memset(&cmd, 0, sizeof(cmd));
	switch (request)
	{
	case ADD:
		if (cmmVlanFillCmdFromState(itf, &cmd, FPP_ACTION_REGISTER, 0) < 0)
		{
			ret = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto out;
		}

		cmm_print(DEBUG_COMMAND, "Send CMD_VLAN_ENTRY ACTION_REGISTER\n");

		ret = fci_write(fci_handle, FPP_CMD_VLAN_ENTRY, sizeof(fpp_vlan_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_VLAN_ENTRY_ALREADY_REGISTERED))
		{
			itf->flags |= FPP_PROGRAMMED;
			itf->flags &= ~FPP_NEEDS_UPDATE;
			cmmVlanStoreProgrammedState(itf);
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_VLAN_ENTRY, ACTION_REGISTER\n", __func__, ret);
			goto out;
		}
		

		break;

	case REMOVE:
		if (cmmVlanFillCmdFromState(itf, &cmd, FPP_ACTION_DEREGISTER, 1) < 0)
		{
			ret = CMMD_ERR_WRONG_COMMAND_PARAM;
			goto out;
		}
	
		cmm_print(DEBUG_COMMAND, "Send CMD_VLAN_ENTRY ACTION_DEREGISTER\n");

		ret = fci_write(fci_handle, FPP_CMD_VLAN_ENTRY, sizeof(fpp_vlan_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_VLAN_ENTRY_NOT_FOUND))
		{
			itf->flags &= ~FPP_PROGRAMMED;
			itf->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_VLAN_ENTRY, ACTION_DEREGISTER\n", __func__, ret);
			goto out;
		}

		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unknown CMD_VLAN_ENTRY request %x\n", __func__, request);
		ret = CMMD_ERR_UNKNOWN_ACTION;
		break;
	}

out:
	return ret;
}

/*****************************************************************
* cmmVlanReset
* 
*
*
******************************************************************/
void cmmVlanReset(FCI_CLIENT *fci_handle)
{
	struct list_head *entry;
	struct interface *itf;
	short ret;
	int i;

	// Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send CMD_VLAN_RESET\n");

	__pthread_mutex_lock(&itf_table.lock);

	ret = fci_write(fci_handle, FPP_CMD_VLAN_RESET, 0, NULL); 
	if (ret == FPP_ERR_OK)
	{
		for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
		{
			for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
			{
				itf = container_of(entry, struct interface, list);

				if (!__itf_is_vlan(itf))
					continue;

				itf->flags &= ~FPP_PROGRAMMED;
			}
		}
	}
	else
	{
		cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_VLAN_RESET\n", __func__, ret);
	}

	__pthread_mutex_unlock(&itf_table.lock);
}

/*****************************************************************
* cmmVlanCheckPolicy
* check if it is allowable to create device with given name
* 0 means prohibited
* non-zero means allowed
******************************************************************/
int cmmVlanCheckPolicy(struct interface *itf)
{
	// Full implementation will query allow list for prohibit policy
	// and allow list for prohibit policy here
	if (globalConf.vlan_policy != MANUAL) 
		return 1;

	return 0;
}

/*****************************************************************
* cmmVlanLocalShow
*
*
******************************************************************/
int cmmVlanLocalShow(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	struct list_head *entry;
	struct interface *itf;
	char ifname[IFNAMSIZ], phys_ifname[IFNAMSIZ];
	int i;

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&itf_table.lock);

		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_vlan(itf))
				continue;

			cli_print(cli, "Interface: %s, VLAN Id: %4d, physical Interface: %s, Flags: %x", if_indextoname(itf->ifindex, ifname), itf->vlan_id, if_indextoname(itf->phys_ifindex, phys_ifname), itf->flags);

		}

		__pthread_mutex_unlock(&itf_table.lock);
	}

	return CLI_OK;
}

int cmmVlanShowClientCmd(u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_local_show_cmd_t *cmd = (cmmd_local_show_cmd_t *)cmd_buf;
	cmmd_vlan_local_show_res_t *res = (cmmd_vlan_local_show_res_t *)res_buf;
	struct list_head *entry;
	struct interface *itf;
	int i;
	int skipcount;

	memset(res, 0, sizeof(*res));
	*res_len = sizeof(*res);

	if (cmd_len < sizeof(*cmd))
	{
		res->rc = CMMD_ERR_WRONG_COMMAND_SIZE;
		res->eof = 1;
		return 0;
	}

	res->rc = CMMD_ERR_NOT_FOUND;
	res->eof = 1;
	skipcount = cmd->skip;

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&itf_table.lock);

		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_vlan(itf))
				continue;

			if (skipcount)
			{
				skipcount--;
				continue;
			}

			res->rc = CMMD_ERR_OK;
			res->eof = 0;
			res->vlan_id = itf->vlan_id;
			res->ifindex = itf->ifindex;
			res->phys_ifindex = itf->phys_ifindex;
			res->flags = itf->flags;
			__pthread_mutex_unlock(&itf_table.lock);
			return 0;
		}

		__pthread_mutex_unlock(&itf_table.lock);
	}

	return 0;
}

int cmmVlanShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	union u_rxbuf rxbuf;
	cmmd_local_show_cmd_t cmd = { 0 };
	cmmd_vlan_local_show_res_t *res = (cmmd_vlan_local_show_res_t *)rxbuf.rcvBuffer;
	char ifname[IFNAMSIZ];
	char phys_ifname[IFNAMSIZ];
	int rc;
	int count = 0;

	if (keywords[tabStart])
	{
		cmm_print(DEBUG_STDERR, "ERROR: show vlan does not take extra arguments\n");
		return -1;
	}

	while (1)
	{
		rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_LOCAL_SHOW, &cmd, sizeof(cmd), rxbuf.rcvBuffer);
		if (rc < (int)sizeof(*res))
		{
			cmm_print(DEBUG_STDERR, "ERROR: wrong response size %d for local VLAN show\n", rc);
			return -1;
		}

		if (res->rc == CMMD_ERR_NOT_FOUND)
			break;

		if (res->rc != CMMD_ERR_OK)
		{
			cmm_print(DEBUG_STDERR, "ERROR: local VLAN show failed rc %u\n", res->rc);
			return -1;
		}

		cmm_print(DEBUG_STDOUT, "Interface: %s, VLAN Id: %4u, physical Interface: %s, Flags: %x\n",
			if_indextoname(res->ifindex, ifname) ? ifname : "unknown",
			res->vlan_id,
			if_indextoname(res->phys_ifindex, phys_ifname) ? phys_ifname : "unknown",
			res->flags);
		count++;
		cmd.skip++;
	}

	if (!count)
	{
		cmm_print(DEBUG_STDERR, "ERROR: CMM local VLAN table empty\n");
		return 0;
	}

	cmm_print(DEBUG_STDOUT, "Total VLAN Entries:%d\n", count);
	return 0;
}

int vlanAddProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	unsigned char rspbuf[512];
	int rsplen;
	cmmd_vlan_cmd_t cmd;

	if (argc < 1)
		goto usage;

	cmd.action = CMMD_ACTION_REGISTER;
	cmd.vlan_id = 0;
	strncpy(cmd.vlan_ifname, argv[0], IFNAMSIZ);
	STR_TRUNC_END(cmd.vlan_ifname, IFNAMSIZ);
	cmd.vlan_phy_ifname[0] = 0;

	if (((rsplen = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_ENTRY, &cmd, sizeof(cmd), rspbuf)) < sizeof(unsigned short)) ||
	     cmmDaemonCmdRC(rspbuf))
	{
		cmm_print(DEBUG_ERROR, "Error sending CMD_VLAN_ENTRY Register\n");
		/*  break; */ return 0;
	}

	return 0;

usage:
	cmm_print(DEBUG_ERROR, "Usage: vlan add ifname.vlanid\n");
	return 0;
}

int vlanDeleteProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	unsigned char rspbuf[512];
	int rsplen;
	cmmd_vlan_cmd_t cmd;

	if (argc < 1)
		goto usage;

	cmd.action = CMMD_ACTION_DEREGISTER;
	cmd.vlan_id = 0;
	strncpy(cmd.vlan_ifname, argv[0], IFNAMSIZ);
	STR_TRUNC_END(cmd.vlan_ifname, IFNAMSIZ);
	cmd.vlan_phy_ifname[0] = 0;

	if (((rsplen = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_ENTRY, &cmd, sizeof(cmd), rspbuf)) < sizeof(unsigned short)) ||
	     cmmDaemonCmdRC(rspbuf))
	{
		cmm_print(DEBUG_ERROR, "Error sending CMD_VLAN_ENTRY DeRegister\n");
		return 0;
	}

	return 0;

usage:
	cmm_print(DEBUG_ERROR, "Usage: vlan delete ifname.vlanid\n");
	return 0;
}

static int vlanShowProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	unsigned char rspbuf[512]; 
	int rsplen;
	cmmd_vlan_cmd_t cmd;
	cmmd_vlan_response_t *pqrsp;
	int skipcount = 0;
  
	do {
		memset(&cmd, 0, sizeof(cmd));
		cmd.action = CMMD_ACTION_QUERY_LOCAL;
		cmd.vlan_id = skipcount;

		pqrsp = (cmmd_vlan_response_t *) (rspbuf + 4);

		if (((rsplen = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_ENTRY, &cmd, sizeof(cmd), rspbuf)) < sizeof(unsigned short)) ||
		    cmmDaemonCmdRC(rspbuf))
		{
			cmm_print(DEBUG_STDOUT, "No vlans defined.\n");
			break;
		}

		rsplen -= 4;

		while (rsplen >= sizeof(*pqrsp))
		{
			cmm_print(DEBUG_STDOUT, "Interface: %s, VLAN Id: %d, physical Interface: %s\n",
				pqrsp->vlan_ifname,
				pqrsp->vlan_id,
				pqrsp->vlan_phy_if_name);

			rsplen -= sizeof(*pqrsp);
			pqrsp += 1;
			skipcount += 1;
		}

		if ((rsplen & 1) == 0)
			break;
	} while(1);

	return 0;
}

/*
** cmmVlanClient
** Client side demux - check input and find client side processor for it
*/
int cmmVlanClient(int argc, char **argv, int firstarg, daemon_handle_t daemon_handle)
{
	if (argc <= firstarg)
		goto usage;

	if (strncasecmp(argv[firstarg], "add", 1) == 0)
		return vlanAddProcess(daemon_handle, argc - firstarg - 1, &argv[firstarg + 1]);
	else if (strncasecmp(argv[firstarg], "delete", 1) == 0)
		return vlanDeleteProcess(daemon_handle, argc - firstarg - 1, &argv[firstarg + 1]);
	else if (strncasecmp(argv[firstarg], "show", 1) == 0)
		return vlanShowProcess(daemon_handle, argc - firstarg - 1, &argv[firstarg + 1]);

	return 0;

usage:
	cmm_print(DEBUG_ERROR, "Usage:\n\tvlan [add|delete] ifname.vlanid\n\tvlan show\n");
	return 0;  
}

/*
** cmmVlanProcessClientCmd
** Daemon side demux.
** receives command from client side, processes it and sends response back.
** Return code is a length of a response in bytes, not including 2 bytes of command rc.
** To prevent daemon issuing commands to fpp - return code must be greater then 1
*/
int cmmVlanProcessClientCmd(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_vlan_cmd_t *pcmd = (fpp_vlan_cmd_t *) cmd_buf;
	cmmd_vlan_response_t *pqrsp;
	struct interface *itf;
	struct list_head *entry;
	int skipcount, len, i;
	int rc = 0;

	res_buf[0] = CMMD_ERR_OK;

	__pthread_mutex_lock(&itf_table.lock);

	switch (pcmd->action)
	{
	case CMMD_ACTION_REGISTER:
		*res_len = 2;

		itf = __itf_find(if_nametoindex(pcmd->vlan_ifname));
		if (!itf)
		{
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			break;
		}

		if (!__itf_is_vlan(itf) || !__itf_is_up(itf))
		{
			res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
			break;
		}

		rc = cmmFeVLANUpdate(fci_handle, ADD, itf);
		if (rc > 0)
		{
			res_buf[0] = rc;
			rc = 0;
		}

		break;

	case CMMD_ACTION_DEREGISTER:
		*res_len = 2;

		itf = __itf_find(if_nametoindex(pcmd->vlan_ifname));
		if (!itf)
		{
			res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
			break;
		}

		if (!__itf_is_vlan(itf))
		{
			res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
			break;
		}

		rc = cmmFeVLANUpdate(fci_handle, REMOVE, itf);
		if (rc > 0)
		{
			res_buf[0] = rc;
			rc = 0;
		}

		break;

	case CMMD_ACTION_QUERY_LOCAL:

		skipcount = pcmd->vlan_id;
		len = 4;
		pqrsp = (cmmd_vlan_response_t *)((char*)res_buf + 4);

		for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
		{
			for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
			{
				itf = container_of(entry, struct interface, list);

				if (!__itf_is_vlan(itf))
					continue;

				if (skipcount <= 0)
				{
					if_indextoname(itf->ifindex, pqrsp->vlan_ifname);
					pqrsp->vlan_id = itf->vlan_id;
					if_indextoname(itf->phys_ifindex, pqrsp->vlan_phy_if_name);
					len += sizeof(*pqrsp);
					pqrsp += 1;

					if (len + sizeof(*pqrsp) >= *res_len)
					{
						
						len += 1; // odd length means that there are more entries to report

						goto out;
					}
				} else {
					skipcount--;
				}
			}
		}

	out:
		*res_len = len;

		break;
 	case CMMD_ACTION_QUERY:
  	case CMMD_ACTION_QUERY_CONT:
       		 rc = fci_cmd(fci_handle, function_code, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
		 break;

	default:
		res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
		*res_len = 2;
		break;
	}

	__pthread_mutex_unlock(&itf_table.lock);
	return rc;
}

/*****************************************************************
 * * cmmVlanQuery
 * *
 * *
 * ******************************************************************/
int cmmVlanQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        short rc;
        int count = 0;
        cmmd_vlan_cmd_t* pVlanCmd = (cmmd_vlan_cmd_t *) rxbuf.rcvBuffer;

        pVlanCmd->action = CMMD_ACTION_QUERY;
        rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_ENTRY, pVlanCmd, 
                                  sizeof(cmmd_vlan_cmd_t) , rxbuf.rcvBuffer);

        if (rcvBytes < sizeof(cmmd_vlan_cmd_t) + sizeof(unsigned short)) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == CMMD_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR, 
                         "ERROR: FPP VLANP does not support ACTION_QUERY\n");
                } else if (rc == CMMD_ERR_VLAN_ENTRY_NOT_FOUND) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP VLAN table empty\n");
                } else {
                    cmm_print(DEBUG_STDERR, 
                            "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }
            cmm_print(DEBUG_STDOUT, "VLAN interfaces:\n");
            do {
			cmm_print(DEBUG_STDOUT, 
                         "Interface: %s, VLAN Id  : %4d,  physical Interface: %s \n",
                          pVlanCmd->vlan_ifname, pVlanCmd->vlan_id,
			pVlanCmd->vlan_phy_ifname);
                	count++;
                	pVlanCmd->action = CMMD_ACTION_QUERY_CONT;
			rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_VLAN_ENTRY, pVlanCmd,
                       		           sizeof(cmmd_vlan_cmd_t) , rxbuf.rcvBuffer);
           }while (rcvBytes >= sizeof(cmmd_vlan_cmd_t) + sizeof(unsigned short));
           cmm_print(DEBUG_STDOUT, "Total VLAN Entries:%d\n", count);

        return CLI_OK;
} 
