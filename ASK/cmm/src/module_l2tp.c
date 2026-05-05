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
#include "module_l2tp.h"
#include "fpp_private.h"
#include "fpp.h"
#include "cmmd.h"

int l2tp_itf_add(FCI_CLIENT *fci_handle, int request, struct interface *itf)
{
	int rc = CMMD_ERR_NOT_CONFIGURED;
	fpp_l2tp_itf_add_cmd_t cmd;

	cmm_print(DEBUG_INFO, "%s ifindex(%d)\n", __func__, itf->ifindex);

	if (!__itf_is_up(itf))
		goto out;

	if (!(itf->flags & USER_ADDED) || (!itf->l2tp.sock)) /* If USER_ADDED l2tp.sock should never be NULL */
		goto out;

	switch (request)
	{
	case ADD:
		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == FPP_PROGRAMMED) {
			rc = 0;
			goto out;
		}

		if ((itf->flags & (FPP_PROGRAMMED | FPP_NEEDS_UPDATE)) == (FPP_PROGRAMMED | FPP_NEEDS_UPDATE))
		{
			cmm_print(DEBUG_ERROR, "%s: trying to update L2TP/PPP interface(%d)\n", __func__, itf->ifindex);
			goto out;
		}

		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: Command not supported\n", __func__);
		rc = CMMD_ERR_UNKNOWN_COMMAND;
		goto out;
		break;
	}

	memset(&cmd, 0, sizeof(cmd));

	cmd.sock_id = itf->l2tp.sock->id;
	cmd.local_tun_id = itf->l2tp.local_tun_id;
	cmd.peer_tun_id = itf->l2tp.peer_tun_id;
	cmd.local_ses_id = itf->l2tp.local_ses_id;
	cmd.peer_ses_id = itf->l2tp.peer_ses_id;
	cmd.options = itf->l2tp.options;

	if (____itf_get_name(itf, cmd.ifname, sizeof(cmd.ifname)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		goto out;
	}
	cmm_print(DEBUG_COMMAND, "Send FPP_CMD_L2TP_ITF_ADD\n");
	rc = fci_write(fci_handle, FPP_CMD_L2TP_ITF_ADD, sizeof(cmd), (unsigned short *) &cmd);
	if (rc == FPP_ERR_OK)
	{
		itf->flags |= FPP_PROGRAMMED;
		itf->flags &= ~FPP_NEEDS_UPDATE;
	}
	else
		cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_L2TP_ITF_ADD\n", __func__, rc);

out:
	return rc;
}

int __l2tp_itf_del(FCI_CLIENT *fci_handle, struct interface *itf)
{
	fpp_l2tp_itf_del_cmd_t cmd;
	int rc = FPP_ERR_OK;

	if (!(itf->flags & FPP_PROGRAMMED))
		goto out;

	memset(&cmd, 0, sizeof(cmd));

	if (____itf_get_name(itf, cmd.ifname, sizeof(cmd.ifname)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ____itf_get_name(%d) failed\n", __func__, itf->ifindex);
		goto out;
	}
	cmm_print(DEBUG_COMMAND, "Send FPP_CMD_L2TP_ITF_DEL\n");
	rc = fci_write(fci_handle, FPP_CMD_L2TP_ITF_DEL, sizeof(cmd), (unsigned short *) &cmd);
	if (rc == FPP_ERR_OK)
	{
		itf->flags &= ~FPP_PROGRAMMED;
		itf->flags &= ~FPP_NEEDS_UPDATE;
	}
	else
		cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_L2TP_ITF_DEL\n", __func__, rc);

out:
	return rc;
}

int l2tp_itf_del(FCI_CLIENT *fci_handle, struct interface *itf)
{
	int rc;
	struct socket *s;

	s = itf->l2tp.sock;

	rc = __l2tp_itf_del(fci_handle, itf);

	if(itf->l2tp.sock)
	{
		__socket_close(fci_handle,itf_table.fci_key_handle,s);
		itf->l2tp.sock =NULL;
		itf->flags &=~USER_ADDED;
		itf->itf_flags &= ~ITF_L2TP;
	}
	return rc;
}

int l2tp_new_session(FCI_CLIENT *fci_handle, cmmd_l2tp_session_t *cmd, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ifindex;
	struct interface *itf;
	int rc = 0;
	struct socket *s = NULL;
	/* Aligned local copies to avoid packed struct member address issues on arm64 */
	u_int32_t peer_addr_aligned[4], local_addr_aligned[4];
	memcpy(peer_addr_aligned, cmd->peer_addr, sizeof(peer_addr_aligned));
	memcpy(local_addr_aligned, cmd->local_addr, sizeof(local_addr_aligned));

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&socket_lock);

	ifindex = if_nametoindex(cmd->itf_name);

	itf = __itf_get(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s: interface %s not found\n", __func__, cmd->itf_name);
		res_buf[0] = CMMD_ERR_NOT_FOUND;
		goto err;
	}
	if (itf->flags & FPP_PROGRAMMED)
	{
		cmm_print(DEBUG_ERROR, "%s: interface %s is already offloaded to PFE\n", __func__, cmd->itf_name);
		res_buf[0] = CMMD_ERR_DUPLICATE;
		goto err;
	}
	else if(itf->l2tp.sock) /* This should not happen */
	{
		/* Close stale socket */		
		rc = __socket_close(fci_handle, itf_table.fci_key_handle, itf->l2tp.sock);

		if(rc == CMMD_ERR_OK)
		{
			itf->l2tp.sock =NULL;
			itf->flags &=~USER_ADDED;
		}
		else
		{
			cmm_print(DEBUG_ERROR, "%s: Stale Socket ID %d could not be closed\n", __func__,itf->l2tp.sock->id);
			res_buf[0] = rc; /* Should we ignore this error and continue ?*/
			goto err;
		}
	}

	itf->itf_flags |= ITF_L2TP;
	/* In case if pppoe auto mode is set , clear the bit 
	as this interface is being configured as L2TP interface not ppp 
	interface*/
	itf->itf_flags &= ~ITF_PPPOE_AUTO_MODE ; 
	itf->l2tp.local_ses_id = cmd->local_ses_id;
	itf->l2tp.peer_ses_id = cmd->peer_ses_id;
	itf->l2tp.local_tun_id = cmd->local_tun_id;
	itf->l2tp.peer_tun_id = cmd->peer_tun_id;

	s = socket_find_by_addr(cmd->family, peer_addr_aligned, local_addr_aligned, cmd->peer_port, cmd->local_port, IPPROTO_UDP);
	if (!s)	{

		s = malloc(sizeof(struct socket));

		if (!s)	{
			cmm_print(DEBUG_ERROR, "%s: malloc() failed\n", __func__);
			goto err;
		}

		memset(s, 0, sizeof(struct socket));
		s->family = cmd->family;
		s->id = new_socket_id();
		if(!s->id)
		{
			cmm_print(DEBUG_ERROR, "%s: No Socket ID available \n", __func__);
			res_buf[0] = CMMD_ERR_SOCKID_ALREADY_USED;
			free(s);
			goto err;
		}
		s->type = CMMD_SOCKET_TYPE_L2TP;
		s->mode = SOCKET_CONNECTED;
		memcpy(s->saddr, cmd->peer_addr, IPADDRLEN(s->family));
		memcpy(s->daddr, cmd->local_addr, IPADDRLEN(s->family));
		s->sport = cmd->peer_port;
		s->dport = cmd->local_port;
		s->proto = IPPROTO_UDP;
		s->dscp = cmd->dscp;
		s->fwmark = cmd->fwmark;
		s->queue = cmd->queue;
		__socket_add(s);
	}
	else {
	/*
 	 * TODO
 	 * In theory we could have multiple L2TP interfaces / sessions on the same socket
	 * Not likely in CPE...
 	 * Would need ref_count on L2TP socket
	 * 
 	 */
		res_buf[0] = CMMD_ERR_DUPLICATE;
		cmm_print(DEBUG_ERROR, "%s: can't offload this L2TP session (%s), it uses the same socket\n", __func__, cmd->itf_name);
	}

	itf->l2tp.sock = s;
	itf->flags |= USER_ADDED;

	rc = l2tp_itf_add(fci_handle, ADD, itf);
	if(rc != CMMD_ERR_OK) {
		itf->flags &= ~USER_ADDED;
		goto err;
	}

	rc = __socket_open(fci_handle, s);
	if(rc != CMMD_ERR_OK) {
		l2tp_itf_del(fci_handle, itf);
		goto err;
	}

	if (rc >= 0) {
		res_buf[0] = rc;
		rc = 0;
	}
err:
	__pthread_mutex_unlock(&socket_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	*res_len = 2;
	return rc;
}


int l2tp_delete_session(FCI_CLIENT *fci_handle, cmmd_l2tp_session_t *cmd, u_int16_t *res_buf, u_int16_t *res_len)
{
	int rc = 0;
	struct interface *itf;
	int ifindex;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&socket_lock);


	ifindex = if_nametoindex(cmd->itf_name);

	itf = __itf_get(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s: interface %s not found\n", __func__, cmd->itf_name);
		rc = CMMD_ERR_NOT_FOUND;
		goto err;
	}

	if (__itf_is_l2tp(itf))
		rc = l2tp_itf_del(itf_table.fci_handle, itf);
err:
	__pthread_mutex_unlock(&socket_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	res_buf[0] = rc;
	*res_len = 2;

	return rc;
}


int l2tp_daemon(FCI_CLIENT *fci_handle,int command, cmmd_l2tp_session_t *cmd,  u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	int rc = 0; 
	
	cmm_print(DEBUG_INFO, "%s\n", __func__);

	res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;

	*res_len = 2;

	if (cmd_len < sizeof(*cmd))
	{
		cmm_print(DEBUG_ERROR, "%s: l2tp session command size too small(%d, %zu)\n", __func__, cmd_len, sizeof(*cmd));
		return rc;
	}
	if(command == CMMD_CMD_L2TP_SESSION_CREATE)
		return l2tp_new_session(fci_handle, cmd, res_buf, res_len);
	else if(command == CMMD_CMD_L2TP_SESSION_DESTROY)
		return l2tp_delete_session(fci_handle, cmd, res_buf, res_len);
	else
	{
		cmm_print(DEBUG_ERROR, "%s: Invalid l2tp command\n", __func__);
		return rc;
	}
}

