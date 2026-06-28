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
#include <net/if.h>

#include "cmm.h"
#include "pppoe.h"
#include "fpp.h"

#if PPPOE_AUTO_ENABLE
#include <sys/ioctl.h>
#include <linux/ppp_defs.h>
#include <linux/if_ppp.h>
#endif

#if PPPOE_AUTO_ENABLE
	#define DEFAULT_AUTO_TIMEOUT    1  // in secs

	#define PPPOE_AUTO_MODE         0x1

	#define PPPIOCSFPPIDLE  _IOW('t', 53, struct ppp_idle)      /* Set the FPP stats */
#endif


/*****************************************************************
* __cmmGetPPPoE
*
*
******************************************************************/
int __cmmGetPPPoESession(FILE *fp, struct interface* ppp_itf)
{
	char buf[256];
	char phys_ifname[IFNAMSIZ];
	char ifname[IFNAMSIZ];
	unsigned char macaddr[ETH_ALEN];
	unsigned int session_id;
	struct interface *itf;
	int ifindex;
	int unit;

	if (fseek(fp, 0, SEEK_SET))
	{
		cmm_print(DEBUG_ERROR, "%s::%d: fseek() failed %s\n", __func__, __LINE__, strerror(errno));
		goto err;
	}

	while (fgets(buf, sizeof(buf), fp))
	{
		// Id   Address           Device     PPPDevice  Unit
		if (sscanf(buf, "%04X%hhx:%hhx:%hhx:%hhx:%hhx:%hhx%16s%16s%d", &session_id, &macaddr[0], &macaddr[1], &macaddr[2], &macaddr[3], &macaddr[4], &macaddr[5], phys_ifname, ifname, &unit) == 10)
		{
			ifindex = if_nametoindex(ifname);

			itf = __itf_find(ifindex);
			if (!itf)
				continue;

			if (!__itf_is_pppoe(itf))
			{
				cmm_print(DEBUG_ERROR, "%s::%d: not point to point interface %s\n", __func__, __LINE__, ifname);
				continue;
			}

			if (!(itf->itf_flags & ITF_PPPOE_SESSION_UP))
                        {
                               itf->itf_flags  |= ITF_PPPOE_SESSION_UP;
                        }


			itf->unit = unit;
			session_id &= 0xFFFF;

			if (itf->session_id != session_id)
			{
				itf->flags |= FPP_NEEDS_UPDATE;
				itf->session_id = session_id;
			}

			if (memcmp(itf->dst_macaddr, macaddr, 6))
			{
				itf->flags |= FPP_NEEDS_UPDATE;
				memcpy(itf->dst_macaddr, macaddr, 6);
			}

			itf->phys_ifindex = if_nametoindex(phys_ifname);

			cmm_print(DEBUG_INFO, "%s::%d: %s is pppoe\n", __func__, __LINE__, if_indextoname(itf->ifindex, ifname));
		}
	}

#if PPPOE_AUTO_ENABLE
        if ( !(ppp_itf->itf_flags & ITF_PPPOE_AUTO_MODE))
        {
                if(__itf_is_up(ppp_itf) && (!(ppp_itf->itf_flags & ITF_PPPOE_SESSION_UP)))
                {
                        cmm_print(DEBUG_INFO, "%s::%d: Setting PPP interface in auto mode (%d)\n", __func__, __LINE__, ppp_itf->ifindex);
                        ppp_itf->itf_flags |= ITF_PPPOE_AUTO_MODE;
                }
        }
#endif


	return 0;

err:
	return -1;
}


/*****************************************************************
* cmmFePPPoEUpdate
*
*
******************************************************************/
int cmmFePPPoEUpdate(FCI_CLIENT *fci_handle, int request, struct interface *itf)
{
	fpp_pppoe_cmd_t cmd;
	short ret;
	int action;

	switch (request)
	{
	default:
	case ADD:
		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED)
			goto out;

		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == (FPP_PROGRAMMED | FPP_NEEDS_UPDATE))
		{
			cmm_print(DEBUG_ERROR, "%s: trying to update PPPoE interface(%d)\n", __func__, itf->ifindex);
			goto err;
		}

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
	memcpy(cmd.macaddr, itf->dst_macaddr, 6);
	cmd.sessionid = itf->session_id;

#if PPPOE_AUTO_ENABLE
        if( itf->itf_flags & ITF_PPPOE_AUTO_MODE)
                cmd.mode |= PPPOE_AUTO_MODE;
#endif

	if (____itf_get_name(itf, cmd.log_intf, sizeof(cmd.log_intf)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);

		goto err;
	}

	switch (action)
	{
	case FPP_ACTION_REGISTER:
		cmm_print(DEBUG_COMMAND, "Send CMD_PPPOE_ENTRY ACTION_REGISTER\n");

		if (__itf_get_name(itf->phys_ifindex, cmd.phy_intf, sizeof(cmd.phy_intf)) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: __itf_get_name(%d) failed\n", __func__, itf->phys_ifindex);

			goto err;
		}

		ret = fci_write(fci_handle, FPP_CMD_PPPOE_ENTRY, sizeof(fpp_pppoe_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_PPPOE_ENTRY_ALREADY_REGISTERED))
		{
			itf->flags |= FPP_PROGRAMMED;
			itf->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_PPPOE_ENTRY, ACTION_REGISTER\n", __func__, ret);
			goto err;
		}

		break;
#if 0
	case ACTION_UPDATE:
		cmm_print(DEBUG_COMMAND, "Send CMD_PPPOE_ENTRY ACTION_UPDATE\n");

		ret = fci_write(fci_handle, CMD_PPPOE_ENTRY, sizeof(struct PPPoECommand), (unsigned short *) &cmd);
		if (ret == FPP_ERR_OK)
		{
			itf->flags &= ~FPP_NEEDS_UPDATE;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_PPPOE_ENTRY, ACTION_UPDATE\n", __func__, ret);
			goto err;
		}

		break;
#endif
	case FPP_ACTION_DEREGISTER:
	
		cmm_print(DEBUG_COMMAND, "Send CMD_PPPOE_ENTRY ACTION_DEREGISTER\n");

		ret = fci_write(fci_handle, FPP_CMD_PPPOE_ENTRY, sizeof(fpp_pppoe_cmd_t), (unsigned short *) &cmd);
		if ((ret == FPP_ERR_OK) || (ret == FPP_ERR_PPPOE_ENTRY_NOT_FOUND))
		{
			itf->flags &= ~FPP_PROGRAMMED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_PPPOE_ENTRY, ACTION_DEREGISTER\n", __func__, ret);
			goto err;
		}

		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unknown CMD_PPPOE_ENTRY action %x\n", __func__, action);
		break;
	}

out:
	return 0;

err:
	return -1;
}

/*****************************************************************
* cmmPPPoELocalShow
*
*
******************************************************************/
int cmmPPPoELocalShow(struct cli_def * cli, const char *command, char *argv[], int argc)
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

			if (!__itf_is_pppoe(itf))
				continue;

			cli_print(cli, "PPP Device: %s, Session ID: %d, MAC addr: %02X:%02X:%02X:%02X:%02X:%02X, Physical Device: %s, Flags: %x, itf_flags: %x\n", if_indextoname(itf->ifindex, ifname), itf->session_id,
				itf->dst_macaddr[0],
				itf->dst_macaddr[1],
				itf->dst_macaddr[2],
				itf->dst_macaddr[3],
				itf->dst_macaddr[4],
				itf->dst_macaddr[5],
				if_indextoname(itf->phys_ifindex, phys_ifname),
				itf->flags , itf->itf_flags);
		}

		__pthread_mutex_unlock(&itf_table.lock);
	}

	return CLI_OK;
}

/*****************************************************************
* cmmPPPoEQueryProcess
*
*
******************************************************************/
int cmmPPPoEQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	fpp_pppoe_cmd_t *command;
	int count = 0;
	union u_rxbuf rxbuf;
	int rcvBytes = 0;
	short rc;

	command = (fpp_pppoe_cmd_t *)rxbuf.rcvBuffer;
        
	command->action = FPP_ACTION_QUERY;

	/* issue command */
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_PPPOE_ENTRY, command, sizeof(fpp_pppoe_cmd_t), rxbuf.rcvBuffer);
	if (rcvBytes < sizeof(fpp_pppoe_cmd_t)) {
		rc = (rcvBytes < sizeof(unsigned short)) ? 0 : rxbuf.result;
		if (rc == FPP_ERR_UNKNOWN_ACTION) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP CMD_PPPoE_ENTRY does not support ACTION_QUERY\n");
		} else if (rc == FPP_ERR_PPPOE_ENTRY_NOT_FOUND) {
			cmm_print(DEBUG_STDERR, "ERROR: FPP PPPoE table empty\n");
		} else {
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
		}

		return CLI_OK;
	}

	do {
		/* display entry received from FPP */
		cmm_print(DEBUG_STDOUT, "PPP Device: %s, Session ID: %d, MAC addr: %02X:%02X:%02X:%02X:%02X:%02X, Physical Device: %s\n",
				command->log_intf, command->sessionid,
				command->macaddr[0],
				command->macaddr[1],
				command->macaddr[2],
				command->macaddr[3],
				command->macaddr[4],
				command->macaddr[5],
				command->phy_intf);

		command->action = FPP_ACTION_QUERY_CONT;
		count++;

		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_PPPOE_ENTRY, command, sizeof(fpp_pppoe_cmd_t), rxbuf.rcvBuffer);
	} while (rcvBytes == sizeof(fpp_pppoe_cmd_t));

	cmm_print(DEBUG_STDOUT, "PPPoE Entry Count: %d\n", count);

	return CLI_OK;
}

#if PPPOE_AUTO_ENABLE

int cmmPPPoEAutoGetIdle( struct interface* itf , unsigned long* rcv_sec , unsigned long* xmit_sec)
{
        fpp_pppoe_idle_t cmd , *rcv_cmd;
        int ret = -1;
        unsigned short rcvlen = 0;
        unsigned char rcvbuf[256];

        if (____itf_get_name(itf, cmd.ppp_if, sizeof(cmd.ppp_if)) < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);

                goto err;
        }
        cmd.xmit_idle = 0;
        cmd.recv_idle  = 0;

        ret = fci_query(itf_table.fci_handle, FPP_CMD_PPPOE_GET_IDLE, sizeof(fpp_pppoe_idle_t), (unsigned short *) &cmd, &rcvlen, (unsigned short*) &rcvbuf[0]);

        if (ret != FPP_ERR_OK)
                goto err;

        rcv_cmd = (fpp_pppoe_idle_t*) &rcvbuf[0];
        *rcv_sec = rcv_cmd->recv_idle;
        *xmit_sec = rcv_cmd->xmit_idle;
        cmm_print(DEBUG_INFO, "%s: Received GET_IDLE time rcv: %d xmit: %d\n", __func__, rcv_cmd->recv_idle, rcv_cmd->xmit_idle);
        return 0;

err:
        cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_PPPOE_GET_IDLE\n", __func__, ret);

        return -1;

}

int cmmPPPoEUpdateDriv(struct interface* itf, unsigned long rcv_sec, unsigned long xmit_sec)
{
        struct ppp_idle cmd;
        char ifname[IFNAMSIZ];
        int unit = itf->unit;
        int fd;

        if (____itf_get_name(itf, ifname, sizeof(ifname)) < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);

                goto err;
        }

        if (unit < 0)
	{
                cmm_print(DEBUG_ERROR, "%s: unit number not found for %s\n", __func__, ifname);
                goto err;
	}

        cmm_print(DEBUG_INFO, "%s: ifname=%s, unit=%d, recv_idle=%lu, xmit_idle=%lu\n", __func__, ifname, unit, rcv_sec, xmit_sec);
        fd = open ("/dev/ppp", O_RDWR);
        if (fd < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: ( open failed : %d) %s\n", __func__, unit, strerror(errno));
                goto err;
        }

        if (ioctl (fd, PPPIOCATTACH, &unit) < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: ioctl(PPPIOCATTACH, %d) %s\n", __func__, unit, strerror(errno));
                close(fd);
                goto err;
        }
        cmd.recv_idle = rcv_sec;
        cmd.xmit_idle = xmit_sec;

        if (ioctl (fd, PPPIOCSFPPIDLE, &cmd) < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: ioctl(PPPIOCSFPPIDLE, %d) %s\n", __func__, unit, strerror(errno));
                close(fd);
                goto err;
        }
#if 0
        if (ioctl (fd, PPPIOCDETACH, &unit) < 0)
        {
                cmm_print(DEBUG_ERROR, "%s: Couldn't attach to interface unit %d:\n", __func__, unit);
                close(fd);
                goto err;
        }
#endif

        close(fd);
        return 0;

err:
        return -1;
}

void cmmPPPoEAutoKeepAlive(void)
{
        static unsigned int gPPPoECurrAutoTimeout = 0;
        struct list_head *entry;
        static time_t last_pppoe = 0;
        double dt;
        time_t now;
        unsigned long rcv_sec = 0,xmit_sec = 0;
        struct interface* itf;
        int i;

        now = time(NULL);

        dt = now - last_pppoe;

        gPPPoECurrAutoTimeout += (unsigned int) dt;

        if (gPPPoECurrAutoTimeout >= DEFAULT_AUTO_TIMEOUT)
        {
                __pthread_mutex_lock(&itf_table.lock);
                for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
                {
                        entry = list_first(&itf_table.hash[i]);
                        while (entry != &itf_table.hash[i])
                        {
                                itf = container_of(entry, struct interface, list);
                                if ((itf->itf_flags & ITF_PPPOE_AUTO_MODE) && (itf->flags & FPP_PROGRAMMED))
                                {
                                        if (cmmPPPoEAutoGetIdle(itf, &rcv_sec, &xmit_sec) == 0)
                                        {
                                                cmmPPPoEUpdateDriv(itf, rcv_sec,xmit_sec);
                                        }

                                }
                                 entry = list_next(entry);
                        }
                }
                __pthread_mutex_unlock(&itf_table.lock);
                gPPPoECurrAutoTimeout = 0;

        }
        last_pppoe = now;
}
#endif

