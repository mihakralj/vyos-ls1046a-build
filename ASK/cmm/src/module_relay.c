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
#include "ffbridge.h"
#include "cmmd.h"
#include "fpp.h"
#include <sys/ioctl.h>
 
/* Structure representing a pppoe entry (internally to cmm) */
struct PPPoERelayEntry {
	struct PPPoERelayEntry *next;
	int count;
	fpp_pppoe_relay_cmd_t *pppoe;
};

struct PPPoERelayEntry *relay_table = NULL;
pthread_mutex_t RelayMutex = PTHREAD_MUTEX_INITIALIZER;

/************************************************************
*
*
*
*************************************************************/

#if 1

static int cmmGetIfMac(unsigned char *ifname, unsigned char *mac){
    struct ifreq ifr;
    int fd;

    memcpy(ifr.ifr_name,ifname,sizeof(ifr.ifr_name));
    fd = socket(AF_INET, SOCK_DGRAM,0);

    if ( fd < 0 )
    {
        cmm_print(DEBUG_ERROR, "%s::%d:Socket Creation Failed  \n",__func__, __LINE__);
        return -1;
    }

    if ( ioctl(fd, SIOCGIFHWADDR, &ifr) < 0 )
    {
        cmm_print(DEBUG_ERROR, "%s::%d:Ioctl Call Failed \n",__func__, __LINE__);
        close(fd);
        return -1;
    }
    memcpy(mac,ifr.ifr_hwaddr.sa_data,IFHWADDRLEN);
    close(fd);
    return 0;
}

static int cmmRelayAdd(FCI_CLIENT * fci_handler, struct fpp_relay_info *sh, u_int16_t *res_buf, u_int16_t *res_len)
{
    struct PPPoERelayEntry *temp, *new_relay_entry = NULL;
    fpp_pppoe_relay_cmd_t *cmd;
    int ret = 0;
    int in_ifindex = -1, out_ifindex = -1, phys_in_ifindex = -1, phys_out_ifindex = -1;
    char in_ifname[IFNAMSIZ], out_ifname[IFNAMSIZ];
    res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
    *res_len = 2;

    __pthread_mutex_lock(&RelayMutex);
    __pthread_mutex_lock(&itf_table.lock);
    /*Check if we have not already been sent to Forward Engine */
    temp = relay_table;

    while (temp != NULL) {
        if (!memcmp(temp->pppoe->peermac1, sh->peermac1, 6)
            && temp->pppoe->sesID == sh->sesID
            && !memcmp(temp->pppoe->peermac2, sh->peermac2, 6)
            && temp->pppoe->relaysesID == sh->relaysesID) {
            cmm_print(DEBUG_INFO, "Relay Match found\n");
            break;
        }

        temp = temp->next;
    }

    if (temp) {
        temp->count++;
        goto end;
    }

  /* Check the interface names whether they are valid and get the physical interface
   * names for bridge interfaces */

    memset(in_ifname , 0, IFNAMSIZ);
    memset(out_ifname , 0, IFNAMSIZ);

    in_ifindex = if_nametoindex(sh->ipifname);
    if (!in_ifindex)
    {
        cmm_print(DEBUG_ERROR, "%s::%d:if_nametoindex Failed  %s\n",__func__, __LINE__, sh->ipifname);
        goto end;
    }

    if ( __itf_is_bridge(in_ifindex))
    {

        cmm_print(DEBUG_INFO, "%s::%d:IP interface name is Bridge interface : %d\n",__func__, __LINE__, in_ifindex);
        phys_in_ifindex = cmmBrGetPhysItf(in_ifindex, sh->peermac1);
        if (phys_in_ifindex < 0)
        {
        	cmm_print(DEBUG_ERROR, "%s::%d:Error in finding the Physical interface  %d\n",__func__, __LINE__, phys_in_ifindex);
                goto end;
        }

        cmm_print(DEBUG_INFO, "%s::%d:Physical interface is : %d\n",__func__, __LINE__, phys_in_ifindex);
        if (! __itf_is_programmed(phys_in_ifindex))
        {
		cmm_print(DEBUG_ERROR, "%s::%d:Interface is not programmed to FPP  %d\n",__func__, __LINE__, phys_in_ifindex);
        	goto end;
        }

        if_indextoname(phys_in_ifindex, in_ifname);
    }
    else
    {
        if (! __itf_is_programmed(in_ifindex))
        {
		cmm_print(DEBUG_ERROR, "%s::%d:Interface is not programmed to FPP  %d\n",__func__, __LINE__, in_ifindex);
        	goto end;
        }
    	memcpy(in_ifname,sh->ipifname, 6);
    }

    out_ifindex = if_nametoindex(sh->opifname);
    if (!out_ifindex)
    {
        cmm_print(DEBUG_ERROR, "%s::%d:if_nametoindex Failed  %s\n",__func__, __LINE__, sh->opifname);
        goto end;
    }

    if (  __itf_is_bridge(out_ifindex))
    {
        phys_out_ifindex = cmmBrGetPhysItf(out_ifindex, sh->peermac2);
        if (phys_out_ifindex < 0)
        {
        	cmm_print(DEBUG_ERROR, "%s::%d:Error in finding the Physical interface  %d\n",__func__, __LINE__, phys_out_ifindex);
                goto end;
        }
        cmm_print(DEBUG_INFO, "%s::%d:Physical interface is : %d\n",__func__, __LINE__, phys_out_ifindex);
        if (! __itf_is_programmed(phys_out_ifindex))
        {
		cmm_print(DEBUG_ERROR, "%s::%d:Interface is not programmed to FPP  %d\n",__func__, __LINE__, phys_out_ifindex);
        	goto end;
        }
        if_indextoname(phys_out_ifindex, out_ifname);
    }
    else
    {
        if (! __itf_is_programmed(out_ifindex))
        {
		cmm_print(DEBUG_ERROR, "%s::%d:Interface is not programmed to FPP  %d\n",__func__, __LINE__, out_ifindex);
        	goto end;
        }
     	memcpy(out_ifname,sh->opifname, 6);
    }

    /*No existing entry found, try to create a new one */
    cmd = (fpp_pppoe_relay_cmd_t *) malloc(sizeof(fpp_pppoe_relay_cmd_t));
    if (cmd == NULL)
    {
        cmm_print(DEBUG_ERROR, "%s::%d:Error while allocating memory for PPPoERelayCommand \n",__func__, __LINE__);
        ret = -1;
        goto end;
    }

    new_relay_entry =  (struct PPPoERelayEntry *) malloc(sizeof(struct PPPoERelayEntry));
    if (new_relay_entry == NULL)
    {
        cmm_print(DEBUG_ERROR, "%s::%d:Error while allocating memory for PPPoERelayEntry \n",__func__, __LINE__);
        ret = -1;
        free(cmd);
        goto end;
    }
    
    
    /* Getting the input/output interface MAC address from the kernel and copy to cmd structure*/
    if ( cmmGetIfMac((unsigned char *)sh->ipifname,(unsigned char *)cmd->ipif_mac) < 0 )
    {
	cmm_print(DEBUG_ERROR, "%s::%d:Error while getting the input interface mac \n",__func__, __LINE__);
	free(cmd);
	free(new_relay_entry);
	goto end;
    }
    if ( cmmGetIfMac((unsigned char *)sh->opifname,(unsigned char *)cmd->opif_mac) < 0 )
    {
	cmm_print(DEBUG_ERROR, "%s::%d:Error while getting the output interface mac \n",__func__, __LINE__);
	free(cmd);
	free(new_relay_entry);
	goto end;
    }

    cmm_print(DEBUG_INFO, "Send CMD_PPPOE_RELAY_ENTRY ACTION_REGISTER\n");
    cmd->action = FPP_ACTION_REGISTER;
    cmd->sesID = sh->sesID;
    memcpy(cmd->peermac1, sh->peermac1, 6);
    memcpy(cmd->ipifname, in_ifname, IFNAMSIZ);
    cmd->relaysesID = sh->relaysesID;
    memcpy(cmd->peermac2, sh->peermac2, 6);
    memcpy(cmd->opifname, out_ifname, IFNAMSIZ);
#if 1
    cmm_print(DEBUG_INFO,
              "Sending command %02x:%02x:%02x:%02x:%02x:%02x(%s %d) to %02x:%02x:%02x:%02x:%02x:%02x(%s %d)\n",
              cmd->peermac1[0], cmd->peermac1[1], cmd->peermac1[2],
              cmd->peermac1[3], cmd->peermac1[4], cmd->peermac1[5],
              cmd->ipifname, cmd->sesID, cmd->peermac2[0],
              cmd->peermac2[1], cmd->peermac2[2], cmd->peermac2[3],
              cmd->peermac2[4], cmd->peermac2[5], cmd->opifname,
              cmd->relaysesID);
#endif
    ret = fci_cmd(fci_handler, FPP_CMD_PPPOE_RELAY_ENTRY, (unsigned short *) cmd, sizeof(*cmd), res_buf, res_len);
    if (ret != 0 || res_buf[0] != FPP_ERR_OK)
    {
         if (ret != 0) 
             cmm_print(DEBUG_ERROR, "Error '%s' when sending CMD_PPPOE_RELAY_ENTRY, ACTION_REGISTER\n", strerror(errno));
         else
             cmm_print(DEBUG_ERROR, "Error %d when sending CMD_PPPOE_RELAY_ENTRY, ACTION_REGISTER\n", res_buf[0]);
         free(cmd);
         free(new_relay_entry);
         goto end;
    }


    cmm_print(DEBUG_INFO, "Send CMD_PPPOE_RELAY_ENTRY SUCCESS\n");
    new_relay_entry->count = 1;
    new_relay_entry->pppoe = cmd;

    new_relay_entry->next = relay_table;
    relay_table = new_relay_entry;


  end:
    __pthread_mutex_unlock(&itf_table.lock);
    __pthread_mutex_unlock(&RelayMutex);
    return ret;
}
#endif
static int cmmRelayRemove(FCI_CLIENT * fci_handler,fpp_relay_info_t *sh, u_int16_t *res_buf, u_int16_t *res_len);

int cmmRelayProcessClientCmd(FCI_CLIENT * fci_handle, int function_code,
                             u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
    cmmd_relay_info_t *sh;
    int rc = 0;

    sh = (cmmd_relay_info_t *) cmd_buf;
    switch (function_code) {
    case CMMD_CMD_PPPOE_RELAY_ADD:
#if 0
        cmm_print(DEBUG_INFO,
                  "Received CMD_PPPOE_RELAY_ENTRY command from RP-PPPoE...\n");
        cmm_print(DEBUG_INFO, "size:%d\n", buffer_size);
        cmm_print(DEBUG_INFO,
                  "Received the peers %02x:%02x:%02x:%02x:%02x:%02x(%s %d) to %02x:%02x:%02x:%02x:%02x:%02x(%s %d)\n",
                  sh->peermac1[0], sh->peermac1[1], sh->peermac1[2],
                  sh->peermac1[3], sh->peermac1[4], sh->peermac1[5],
                  sh->ipifname, sh->sesID, sh->peermac2[0],
                  sh->peermac2[1], sh->peermac2[2], sh->peermac2[3],
                  sh->peermac2[4], sh->peermac2[5], sh->opifname,
                  sh->relaysesID);
#endif
        rc = cmmRelayAdd(fci_handle, sh, res_buf, res_len);
        break;

    case CMMD_CMD_PPPOE_RELAY_REMOVE:
        rc = cmmRelayRemove(fci_handle, sh, res_buf, res_len);
        break;

    default:
        res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
        *res_len = 2;
        break;

    }
    return rc;
}

/*****************************************************************
 * * cmmPPPoELocalShow
 * *
 * *
 * ******************************************************************/
int cmmRelayLocalShow(struct cli_def *cli, const char *command, char *argv[],
                      int argc)
{
    struct PPPoERelayEntry *temp;

    __pthread_mutex_lock(&RelayMutex);

    for (temp = relay_table; temp != NULL; temp = temp->next) {
        if (temp->pppoe) {
            cli_print(cli, "%02x.%02x.%02x.%02x.%02x.%02x[%s  %d]<==::==>%02x.%02x.%02x.%02x.%02x.%02x[%s  %d]",
                      temp->pppoe->peermac1[0],temp->pppoe->peermac1[1],
                      temp->pppoe->peermac1[2],temp->pppoe->peermac1[3],
                      temp->pppoe->peermac1[4],temp->pppoe->peermac1[5],
            	      temp->pppoe->ipifname,temp->pppoe->sesID,
                      temp->pppoe->peermac2[0],temp->pppoe->peermac2[1],
                      temp->pppoe->peermac2[2],temp->pppoe->peermac2[3],
                      temp->pppoe->peermac2[4],temp->pppoe->peermac2[5],
		      temp->pppoe->opifname,temp->pppoe->relaysesID);
        } else
            cli_print(cli, "Internal Error");
    }

    __pthread_mutex_unlock(&RelayMutex);

    return CLI_OK;
}


/*****************************************************************
 * * cmmRelayRemove
 * *
 * *
 * ******************************************************************/
static int cmmRelayRemove(FCI_CLIENT * fci_handler,fpp_relay_info_t *sh, u_int16_t *res_buf, u_int16_t *res_len)
{
    struct PPPoERelayEntry *temp = NULL, *prevEntry = NULL;
    fpp_pppoe_relay_cmd_t *cmd;
    int ret = 0;
    res_buf[0] = CMMD_ERR_NOT_CONFIGURED;
    *res_len = 2;

    __pthread_mutex_lock(&RelayMutex);

    temp = relay_table;
    if (temp == NULL) {
        cmm_print(DEBUG_ERROR, "relay_table is NULL\n");
        goto end;
    }
    // Do a test on the first of the list
    while ((temp != NULL)) {
        if (!memcmp(temp->pppoe->peermac1, sh->peermac1, 6)
            && temp->pppoe->sesID == sh->sesID
            &&!memcmp(temp->pppoe->peermac2, sh->peermac2, 6)
            && temp->pppoe->relaysesID == sh->relaysesID) {
            cmm_print(DEBUG_ERROR, "An entry has been found to remove\n");
            break;
        } else {
            prevEntry = temp;
            temp = temp->next;
        }
    }
    // The entry have not been found, should not happen
    if (temp == NULL) {
        cmm_print(DEBUG_ERROR,
                  "An entry have been removed already on localtable or the delete command for same entry\n");
        goto end;
    }


    cmd = temp->pppoe;
    cmd->action = FPP_ACTION_DEREGISTER;

#if 1
    cmm_print(DEBUG_INFO,
              "Removing Entry %02x:%02x:%02x:%02x:%02x:%02x(%s %d) to %02x:%02x:%02x:%02x:%02x:%02x(%s %d)\n",
              cmd->peermac1[0], cmd->peermac1[1], cmd->peermac1[2],
              cmd->peermac1[3], cmd->peermac1[4], cmd->peermac1[5],
              cmd->ipifname, cmd->sesID, cmd->peermac2[0],
              cmd->peermac2[1], cmd->peermac2[2], cmd->peermac2[3],
              cmd->peermac2[4], cmd->peermac2[5], cmd->opifname,
              cmd->relaysesID);
#endif

    ret = fci_cmd(fci_handler, FPP_CMD_PPPOE_RELAY_ENTRY, (unsigned short *) cmd, sizeof(fpp_pppoe_relay_cmd_t), res_buf, res_len);
    if (ret != 0 || (res_buf[0] != FPP_ERR_OK && res_buf[0] != FPP_ERR_PPPOE_ENTRY_NOT_FOUND))	
    {
        if (ret != 0)
            cmm_print(DEBUG_ERROR, "Error '%s' while sending CMD_PPPOE_RELAY_ENTRY, ACTION_DEREGISTER\n", strerror(errno));
        else
            cmm_print(DEBUG_ERROR, "Error %d while sending CMD_PPPOE_RELAY_ENTRY, ACTION_DEREGISTER\n", res_buf[0]);
        goto end;
    }

    if (prevEntry == NULL)
        relay_table = temp->next;
    else
        prevEntry->next = temp->next;

    free(temp->pppoe);
    free(temp);
  end:
    __pthread_mutex_unlock(&RelayMutex);
    return ret;
}

static int relay_print_usage()
{
	cmm_print(DEBUG_ERROR,
		 "Usage: relay <add|del> <MAC1> <MAC2> <IN iface> <OUT iface> <session ID> <relay session ID>\n"
		 "\n"
		 "\n"
		 "       Ex:  relay add 00:00:00:00:00:01 00:00:00:00:00:02 eth2 eth1 1 1\n"
		 "            relay del 00:00:00:00:00:01 00:00:00:00:00:02 eth2 eth1 1 1\n"
		);
	return -1;
}

static int relay_parse_cmd(int argc, char ** keywords, daemon_handle_t daemon_handle)
{
	cmmd_relay_info_t cmd;
	union u_rxbuf rxbuf;
	int fc;
	int rc;
	unsigned long tmp;

	if (argc < 7)
		return relay_print_usage();

	if (strcmp(*keywords, "add") == 0)
		fc = CMMD_CMD_PPPOE_RELAY_ADD;
	else if (strcmp(*keywords, "del") == 0)
		fc = CMMD_CMD_PPPOE_RELAY_REMOVE;
	else
		return relay_print_usage();

	keywords++;
	if (!parse_macaddr(*keywords, cmd.peermac1))
		return relay_print_usage();

	keywords++;
	if (!parse_macaddr(*keywords, cmd.peermac2))
		return relay_print_usage();
	
	keywords++;
	strncpy(cmd.ipifname, *keywords, sizeof(cmd.ipifname));
	STR_TRUNC_END(cmd.ipifname, sizeof(cmd.ipifname));

	keywords++;
	strncpy(cmd.opifname, *keywords, sizeof(cmd.opifname));
	STR_TRUNC_END(cmd.opifname, sizeof(cmd.opifname));

	keywords++;
	tmp = strtoul(*keywords, NULL, 0);
	if(tmp > UINT_MAX)
		return relay_print_usage();
	cmd.sesID = tmp;

	keywords++;
	tmp = strtoul(*keywords, NULL, 0);
	if(tmp > UINT_MAX)
		return relay_print_usage();
	cmd.relaysesID = tmp;

	rc = cmmSendToDaemon(daemon_handle, fc, &cmd, sizeof(cmd), rxbuf.rcvBuffer);
	if (rc != 2) /* we expect 2 bytes in response */
	{
		cmm_print(DEBUG_STDERR, "unexpected response length %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != CMMD_ERR_OK)
	{
		cmm_print(DEBUG_STDERR, "Error %d received from CMM Deamon\n", rxbuf.result);
		return -1;
	}

	return 0;
}

int cmmRelayParseCmd(int argc, char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        if (tabStart < argc)
                return relay_parse_cmd(argc - tabStart, &keywords[tabStart], daemon_handle);
        else
                return relay_print_usage();
}

