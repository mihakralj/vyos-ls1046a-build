/*
 *
 *  Copyright (C) 2015 Freescale Semiconductor, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#include <stdbool.h>
#include <signal.h>
#include <net/if.h>
#include <sys/time.h>

#include "cmm.h"
#include "module_ipsec.h"
#include "keytrack.h"

#define SA_STATE_INIT           0x1
#define SA_STATE_VALID          0x2
#define SA_STATE_DEAD           0x3
#define SA_STATE_EXPIRED        0x4
#define SA_STATE_DYING          0x5

struct nlkey_sa_notify {
        unsigned short sagd;
        unsigned short rsvd;
        unsigned int  action;
};

struct list_head sa_table[SA_HASH_TABLE_SIZE];
pthread_mutex_t sa_lock = PTHREAD_MUTEX_INITIALIZER;


static inline unsigned short  getSAHash(unsigned short id)
{
	return (id  & (SA_HASH_TABLE_SIZE -1));
}


int cmmSAShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int i, count = 0;
	struct SATable *pSAEntry;
	struct list_head *entry;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	__pthread_mutex_lock(&sa_lock);
	for (i = 0; i < SA_HASH_TABLE_SIZE; i++)
	{
		for(entry = list_first(&sa_table[i]); entry != &sa_table[i]; entry = list_next(entry))	
		{
			count++;
			pSAEntry = container_of(entry, struct SATable, list_by_h);
			cli_print(cli, "Sagd: %d, SPI:0x%x, sa_type: %d, protocal: %d\n", pSAEntry->SAInfo.sagd, pSAEntry->SAInfo.id.spi, pSAEntry->SAInfo.id.sa_type, pSAEntry->SAInfo.id.proto_family);
			if(pSAEntry->SAInfo.proto_family != 0)
			{
				if(pSAEntry->SAInfo.proto_family == PROTO_FAMILY_IPV4)
					cli_print(cli, "IPv4 Tunnel Header Source: %s, Destination: %s \n", inet_ntop(AF_INET, &pSAEntry->SAInfo.tunnel.ipv4h.SourceAddress, sbuf, sizeof(sbuf)), inet_ntop(AF_INET, &pSAEntry->SAInfo.tunnel.ipv4h.DestinationAddress, dbuf, sizeof(dbuf)));
				else
					cli_print(cli, "IPv6 Tunnel Header Source: %s, Destination: %s \n", inet_ntop(AF_INET6, pSAEntry->SAInfo.tunnel.ipv6h.SourceAddress, sbuf, sizeof(sbuf)), inet_ntop(AF_INET6, pSAEntry->SAInfo.tunnel.ipv6h.DestinationAddress, dbuf, sizeof(dbuf)));
			}
		}
	}
	__pthread_mutex_unlock(&sa_lock);
	cli_print(cli, "Total SA count %d\n", count);
	return CLI_OK;
}


struct SATable *__cmmSAFind(unsigned short handle)
{
	unsigned short hash = getSAHash(handle);
	struct SATable *SAEntry = NULL;
	struct list_head *entry;
	
	for(entry = list_first(&sa_table[hash]); entry != &sa_table[hash]; entry = list_next(entry))
	{
		SAEntry = container_of(entry, struct SATable, list_by_h);
		if (SAEntry->SAInfo.sagd == handle)
			return SAEntry;
	}

	return NULL;
}

static struct SATable *__cmmSAAdd(PCommandIPSecCreateSA pSA_info)
{
	struct SATable *newEntry;
	unsigned short hash;
	int i;

	newEntry = (struct SATable*) calloc(1, sizeof(struct SATable));
	if (newEntry == NULL)
	{
		cmm_print(DEBUG_ERROR, "%s: malloc failed\n", __func__);
		goto err0;
	}

	newEntry->SAInfo.sagd = pSA_info->sagd;
	hash = getSAHash(newEntry->SAInfo.sagd);
	memcpy(&newEntry->SAInfo.id, &pSA_info->said, sizeof(newEntry->SAInfo.id));
	
#ifdef IPSEC_FLOW_CACHE
	/* Add it to the hash table */
	for(i =0; i < MAX_SA_PER_FLOW; i++)
		list_head_init(&newEntry->flow_list[i]);
#else
	/* init ctentries table */
	for(i =0; i < 2; i++)
		list_head_init(&newEntry->ctentry_list[i]);

#endif /* IPSEC_FLOW_CACHE */
	list_add(&sa_table[hash], &newEntry->list_by_h);

err0:
	return newEntry;
}


int __cmmSATunnelRegister(FCI_CLIENT *fci_handle, struct SATable* SAEntry)
{
	CommandIPSecSetTunnelRoute cmd_set_tnl_route;
	int rc = 0;
	SAEntry->Sa_flow.family = SAEntry->SAInfo.proto_family;
	
	if (SAEntry->SAInfo.proto_family == PROTO_FAMILY_IPV4)
	{
		SAEntry->Sa_flow.sAddr = &SAEntry->SAInfo.tunnel.ipv4h.SourceAddress;
		SAEntry->Sa_flow.dAddr = &SAEntry->SAInfo.tunnel.ipv4h.DestinationAddress;
	}
	else
	{
		SAEntry->Sa_flow.sAddr = SAEntry->SAInfo.tunnel.ipv6h.SourceAddress;
		SAEntry->Sa_flow.dAddr = SAEntry->SAInfo.tunnel.ipv6h.DestinationAddress;
	}
	
	SAEntry->Sa_flow.fwmark = 0;
	SAEntry->Sa_flow.iifindex = 0;
	SAEntry->Sa_flow.proto = 0;
	/* Eventhough SA is local connection, as the connection will not exist in kernel this is disabled */
	SAEntry->Sa_flow.flow_flags = FLOWFLAG_SA_ROUTE;

	rc = __cmmRouteRegister(fci_handle, &SAEntry->tnl_rt, &SAEntry->Sa_flow, "sa");
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3,19,0)
/* 
   In 3.19 kernel, neighbor entry in linux neighbor cache is not created during the creation of route entry
   in linux route cache as was done in previous versions. Consider a scenario where an SA is waiting for a
   neigbor 'X' and some other connection creates this neigbor entry 'X' in CMM. Now the neigbor creation
   event received by CMM will be ignored since neighbor entry is already present in CMM and no changes were
   made to neighbor entry. SA waiting for neighbor will never know the creation of neighbor entry 'X' in CMM.
   To fix this a dummy entry in created in CMM if the required neighbor entry is not present in linux neighbor cache. 
*/
	if(SAEntry->tnl_rt.route && !SAEntry->tnl_rt.route->neighEntry)
	{
		SAEntry->tnl_rt.route->neighEntry = __cmmNeighAdd(SAEntry->tnl_rt.route->family, SAEntry->tnl_rt.route->gwAddr, SAEntry->tnl_rt.route->oifindex);
		SAEntry->tnl_rt.route->neighEntry->count++;
	}
#endif
	if (rc < 0)
		goto program;

	cmm_print(DEBUG_INFO, "%s:Neighor resolved \n", __func__);
	cmmFeRouteUpdate(fci_handle, ADD | UPDATE, SAEntry->tnl_rt.fpp_route);

program:

	__cmmCheckFPPRouteIdUpdate(&SAEntry->tnl_rt, &SAEntry->flags);
	cmd_set_tnl_route.sagd = SAEntry->SAInfo.sagd;
	cmd_set_tnl_route.route_id = SAEntry->tnl_rt.fpp_route_id;

	/* Send the tunnel command to FPP */
	if (SAEntry->flags & FPP_NEEDS_UPDATE)
	{
		if (cmmKeyEnginetoIPSec(fci_handle, FPP_CMD_IPSEC_SA_TNL_ROUTE, sizeof(CommandIPSecSetTunnelRoute),(unsigned short*) &cmd_set_tnl_route) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s:cmmKeyEnginetoIPSec failed while setting tunnel route:\n", __func__);
			return -1;
		}
	}

	SAEntry->flags &= ~FPP_NEEDS_UPDATE;

	return rc;
}


static void __cmmSARouteUpdate(FCI_CLIENT *fci_handle, struct SATable *s, struct RtEntry *route)
{
	struct ct_route rt = s->tnl_rt;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (route->flags & INVALID)
	{
		s->tnl_rt.route = NULL;
		s->tnl_rt.fpp_route = NULL;
	}
	else
	{
		rt.route = NULL;
		s->tnl_rt.fpp_route = NULL;
	}

	__pthread_mutex_lock(&sa_lock);
	__cmmSATunnelRegister(fci_handle, s);
	__pthread_mutex_unlock(&sa_lock);

	__cmmRouteDeregister(fci_handle, &rt, "sa");
}

void __cmmSAUpdateWithRoute(FCI_CLIENT *fci_handle, struct RtEntry *route)
{
	struct SATable *s;
	struct list_head *entry;
	int i;


	for (i = 0; i < SA_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&sa_table[i]); entry != &sa_table[i]; entry = list_next(entry))
		{
			s = container_of(entry, struct SATable, list_by_h);

			if (s->tnl_rt.route == route)
				 __cmmSARouteUpdate(fci_handle, s, route);
		}
	}

}

int __cmmRouteIsSA(int family, const unsigned int* daddr, struct SATable* sa, int prefix_match, int prefix_len)
{
	unsigned int* tunnel_daddr;
	int addr_len = IPADDRLEN(family);


	if (sa->tnl_rt.route)
		goto out;

	if (sa->SAInfo.proto_family != family)
		goto out;

	if (sa->SAInfo.proto_family == PROTO_FAMILY_IPV4)
		tunnel_daddr = &sa->SAInfo.tunnel.ipv4h.DestinationAddress;
	else
		tunnel_daddr = sa->SAInfo.tunnel.ipv6h.DestinationAddress;

	if (prefix_match)
	{
		if (cmmPrefixEqual(tunnel_daddr, daddr, prefix_len))
		return 1;
	}
	else
	{
		if (memcmp(tunnel_daddr, daddr, addr_len) == 0)
		return 1;
	}
out:
	return 0;
}

static int __cmmSARemove(FCI_CLIENT *fci_handle, struct SATable *SAEntry)
{
	__cmmRouteDeregister(fci_handle, &SAEntry->tnl_rt, "sa");

        /* Remove it from the hash table */
	list_del(&SAEntry->list_by_h);
	free(SAEntry);

	return 0;
}


int cmmSACreate(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	PCommandIPSecCreateSA pSA_cmd = (PCommandIPSecCreateSA)payload;
	struct SATable *pSAEntry;
	int rc = 0;
	if (len != sizeof(CommandIPSecCreateSA))
	{
		cmm_print(DEBUG_ERROR, "%s: command length doesn't match %zu-%d\n", __func__, sizeof(CommandIPSecCreateSA), len);
		return -1;
	}

	cmm_print(DEBUG_INFO, "%s: fcode 0x%x len %d bytes\n", __func__, fcode, len);

	__pthread_mutex_lock(&sa_lock);
	pSAEntry = __cmmSAFind(pSA_cmd->sagd);

	if (pSAEntry)
	{
		cmm_print(DEBUG_ERROR, "%s: SA exists :%x \n", __func__, pSA_cmd->sagd);
		rc = -1;
		goto out;
	}
	
	pSAEntry = __cmmSAAdd(pSA_cmd);
	if(!pSAEntry)
	{
		rc = -1;
		goto out;
	}
	cmm_print(DEBUG_INFO, "%s: new SA added :%x, SPI %x \n", __func__, 
		pSA_cmd->sagd,pSAEntry->SAInfo.id.spi);

out:
	__pthread_mutex_unlock(&sa_lock);
	return rc;


}

int cmmSADelete(FCI_CLIENT *fci_handle, PCommandIPSecDeleteSA pSA_cmd)
{
	struct SATable *pSAEntry;
	int rc = 0;
	__pthread_mutex_lock(&sa_lock);
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	pSAEntry = __cmmSAFind(pSA_cmd->sagd);
	if (!pSAEntry)
	{
		cmm_print(DEBUG_ERROR, "%s: SA doesn't exist :%x \n", __func__, pSA_cmd->sagd);
		rc = -1;
		goto out;
	}
	cmm_print(DEBUG_INFO, "%s(%d) SA %p, XFRM handle %x, SPI %x\n",
		__FUNCTION__,__LINE__,pSAEntry, pSA_cmd->sagd,pSAEntry->SAInfo.id.spi);
	
#ifndef IPSEC_FLOW_CACHE
	pSAEntry->flags |= SA_DELETE;
#endif
	if(cmmUpdateFlows(pSAEntry) < 0)
	{
		rc = -1;
		goto out;
	}
	__cmmSARemove(fci_handle, pSAEntry);	

out:	
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);
	__pthread_mutex_unlock(&sa_lock);
	return rc;
}


int cmmSAFlush(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	int i, rc = 0;
	struct SATable *pSAEntry;
	struct list_head *entry;

	__pthread_mutex_lock(&sa_lock);
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	
	for (i = 0; i < SA_HASH_TABLE_SIZE; i++)
	{
		for(entry = list_first(&sa_table[i]); entry != &sa_table[i]; )	
		{
			pSAEntry = container_of(entry, struct SATable, list_by_h);
			entry = list_next(entry);
#ifndef IPSEC_FLOW_CACHE
			pSAEntry->flags |= SA_DELETE;
#endif /* IPSEC_FLOW_CACHE */
			if(cmmUpdateFlows(pSAEntry) < 0)
			{
				cmm_print(DEBUG_INFO,"%s Failed \n", __func__);
			}
			__cmmSARemove(fci_handle, pSAEntry);
		}
	}
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);
	__pthread_mutex_unlock(&sa_lock);
	return rc;
}

int cmmSASetOffloadState(FCI_CLIENT *fci_handle, unsigned short sagd, bool offload_status)
{
	struct nlkey_sa_notify offload_status_change = {};
	/* Return -1 when offload is disabled so that SA never gets offloaded to CDX */
	int rc = (offload_status) ? 0:-1; 
	offload_status_change.sagd = sagd;
	offload_status_change.action = offload_status;
	if (cmmKeyEnginetoIPSec(fci_handle, FPP_CMD_IPSEC_SA_ACTION_OFFLOAD, sizeof(struct nlkey_sa_notify),
		(unsigned short *)&offload_status_change) < 0)
	{
		cmm_print(DEBUG_INFO,"%s: FPP_CMD_IPSEC_SA_ACTION_OFFLOAD failed", __func__);
		rc = -1;
	}
	return rc;
}

int cmmSASetState(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload, 
						int *state_valid, unsigned short *sgid)
{
	PCommandIPSecSetState pSA_cmd = (PCommandIPSecSetState)payload;
	struct SATable *pSAEntry;
	int rc = 0;
	if (len != sizeof(PCommandIPSecSetState))
	{
		cmm_print(DEBUG_ERROR, "%s: command length doesn't match %zu-%d\n", __func__, sizeof(PCommandIPSecSetState), len);
		return -1;
	}
	__pthread_mutex_lock(&sa_lock);
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	pSAEntry = __cmmSAFind(pSA_cmd->sagd);

	if (!pSAEntry)
	{
		cmm_print(DEBUG_ERROR, "%s: SA doesn't exist :%x \n", __func__, pSA_cmd->sagd);
		rc = -1;
		goto out;
	}
	cmm_print(DEBUG_INFO,"%s:  SA State change %d ",__func__,pSA_cmd->state);
#ifdef LS1043
	/* Do not offload Transport mode flows on LS104x,
	   Reset offloaded flag on the corresponding xfrm SA in Linux */
	rc = cmmSASetOffloadState(fci_handle, pSA_cmd->sagd, IS_TUNNEL_MODE_SA(pSAEntry));
#else
	rc = cmmSASetOffloadState(fci_handle, pSA_cmd->sagd, true);
#endif
	if(rc < 0)
		goto out;

	pSAEntry->SAInfo.state = pSA_cmd->state;
	if(pSA_cmd->state == SA_STATE_DYING)
	{
#ifndef IPSEC_FLOW_CACHE
		pSAEntry->flags |= SA_DELETE;
#endif /* IPSEC_FLOW_CACHE */
		if(cmmUpdateFlows(pSAEntry) < 0)
		{
			rc = -1;
			goto out;
		}
		__cmmSARemove(fci_handle, pSAEntry);
	}

	if (pSA_cmd->state == SA_STATE_VALID)
	{
		*state_valid = 1;
		*sgid = pSAEntry->SAInfo.sagd;
#ifndef IPSEC_FLOW_CACHE
		if (pSA_cmd->parent_sa_sagd)
		{
			/* Find flows having rekey_sagd value,
			update those with sagd value and new SA pointers */
			cmmUpdateFlowsWithNewSAInfo(pSAEntry, pSA_cmd->parent_sa_sagd);
		}
#endif /* IPSEC_FLOW_CACHE */
	}
	else
		*state_valid = 0;

out:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);
	__pthread_mutex_unlock(&sa_lock);
	return rc;


}

int cmmSASetTunnel(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	PCommandIPSecSetTunnel pSA_cmd = (PCommandIPSecSetTunnel)payload;
	int rc = 0;
	struct SATable *pSAEntry;
	if (len != sizeof(CommandIPSecSetTunnel))
	{
		cmm_print(DEBUG_ERROR, "%s: command length doesn't match %zu-%d\n", __func__, sizeof(CommandIPSecSetTunnel), len);
		return -1;
	}
	__pthread_mutex_lock(&sa_lock);
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	pSAEntry = __cmmSAFind(pSA_cmd->sagd);

	if (!pSAEntry)
	{
		cmm_print(DEBUG_ERROR, "%s: SA doesn't exist :%x \n", __func__, pSA_cmd->sagd);
		rc = -1;
		goto out;
	}

	pSAEntry->SAInfo.proto_family = pSA_cmd->proto_family;
	if (pSA_cmd->proto_family == PROTO_FAMILY_IPV4)
		memcpy(&pSAEntry->SAInfo.tunnel.ipv4h, &pSA_cmd->h.ipv4h, IPV4_HDR_SIZE);
	else
		memcpy(&pSAEntry->SAInfo.tunnel.ipv6h, &pSA_cmd->h.ipv6h, IPV6_HDR_SIZE);

	/* Find the route for tunnel and corresponding neighbor here */
	rc = __cmmSATunnelRegister(fci_handle, pSAEntry);
out:	
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&itf_table.lock);
	__pthread_mutex_unlock(&sa_lock);
	return rc;
}


struct SATable *cmmSAFind(unsigned short handle)
{
	return __cmmSAFind(handle);
}
