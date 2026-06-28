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
#ifndef IPSEC_FLOW_CACHE
#include "linux/xfrm.h"
#endif /* IPSEC_FLOW_CACHE */

#include "itf.h"
#include "ffbridge.h"
#include "module_lro.h"
#include "cmmd.h"
#include "module_ipsec.h"
#include "conntrack.h"

struct list_head ct_table[CONNTRACK_HASH_TABLE_SIZE];
struct list_head ct_table_by_rep[CONNTRACK_HASH_TABLE_SIZE];
struct list_head ct_table_by_orig_route[2 * ROUTE_HASH_TABLE_SIZE];
struct list_head ct_table_by_rep_route[2 * ROUTE_HASH_TABLE_SIZE];
struct list_head ct_table_by_orig_tunnel_route[2 * ROUTE_HASH_TABLE_SIZE];
struct list_head ct_table_by_rep_tunnel_route[2 * ROUTE_HASH_TABLE_SIZE];
#ifndef IPSEC_FLOW_CACHE
struct list_head ct_flow_no_sa_list;
#endif /* IPSEC_FLOW_CACHE */

struct conntrack_stats ct_stats;
struct nf_conntrack *p_nfconn_clone, *p_nfconn_update;
unsigned char resync_buf[CMM_MAX_64K_BUFF_SIZE];

pthread_mutex_t ctMutex = PTHREAD_MUTEX_INITIALIZER;		/*mutex to prevent race condition on the conntrack table*/

const unsigned char null_mac[ETH_ALEN] = {0, };
const unsigned int null_ip[4] = {0, };

extern void cmmDPDIPsecSAUpdate(struct cmm_ct *ctx);
extern int cmmNeighSendSolicit(void);
extern void cmmPPPoEAutoKeepAlive(void);

extern unsigned int nf_conntrack_max;

void * cb_data;
int cb_status;


/*****************************************************************
* cmmCtSetPermanent
*
*
******************************************************************/
static void cmmCtSetPermanent(struct nfct_handle * handler, int flags,  struct nf_conntrack * ctTemp, struct nf_conntrack *ct, int reset)
{
	int status;

	if(flags & LOCAL_CONN)
		return;

	if(!ctTemp)
	{
		cmm_print(DEBUG_INFO, "%s: Conntrack not Cloned",__func__);
		return;
	} 

	status = nfct_get_attr_u32(ctTemp, ATTR_STATUS);

	if (((status & IPS_PERMANENT) == 0) && (flags & (FPP_PROGRAMMED)))
	{
			status |= IPS_PERMANENT;
			/* Update conntrack status */
			if(ct)
				nfct_set_attr_u32(ct, ATTR_STATUS, status);
	}
	else if ((status & IPS_PERMANENT) && (reset)) /* This connection will now be deleted from PFE */
			status &= ~(IPS_PERMANENT);
	else 
		return;

	nfct_set_attr_u32(ctTemp, ATTR_STATUS, status);

	if (nfct_query(handler, NFCT_Q_UPDATE, (void*)ctTemp) < 0) {
		if (errno != ENOENT)
			cmm_print(DEBUG_ERROR, "%s: ATTR_STATUS update, nfct_query(NFCT_Q_UPDATE) %s\n", __func__, strerror(errno));
	}
	else
			cmm_print(DEBUG_INFO, "%s: Ct status %s  PERMANENT flag \n",__func__, (status & IPS_PERMANENT) ? "updated" : "removed");
	return;
}

/*****************************************************************
* cmmCtClone
*
*
******************************************************************/
static struct nf_conntrack* cmmCtClone( struct nf_conntrack* ct)
{
	nfct_copy(p_nfconn_clone, ct, NFCT_CP_OVERRIDE);
	return p_nfconn_clone;
}

/*****************************************************************
* cmmCtForceUpdate
*
* This function will trigger kernel to resend an update for the given
* entry that needs up-to-date information.
******************************************************************/
static void cmmCtForceUpdate(struct nfct_handle * handler, struct ctTable * ctEntry)
{
	struct nf_conntrack *ctTemp = p_nfconn_update;
	struct nf_conntrack *ct = ctEntry->ct;
	const unsigned int *Saddr, *Daddr, *SaddrReply, *DaddrReply;
	unsigned short Sport, Dport, SportReply, DportReply;
	int family, proto;


	nfct_clear(ctTemp);

	family = nfct_get_attr_u8(ct, ATTR_ORIG_L3PROTO);
	proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);

	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L3PROTO, family);
	nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, proto);

	nfct_set_attr_u8(ctTemp, ATTR_REPL_L3PROTO, family);
	nfct_set_attr_u8(ctTemp, ATTR_REPL_L4PROTO, proto);

	if (family == AF_INET)
	{
		Saddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		Daddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
		SaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
		DaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV4_DST);

		nfct_set_attr(ctTemp, ATTR_ORIG_IPV4_SRC, Saddr);
		nfct_set_attr(ctTemp, ATTR_ORIG_IPV4_DST, Daddr);
		nfct_set_attr(ctTemp, ATTR_REPL_IPV4_SRC, SaddrReply);
		nfct_set_attr(ctTemp, ATTR_REPL_IPV4_DST, DaddrReply);
	}
	else
	{
		Saddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
		Daddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_DST);
		SaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);
		DaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV6_DST);

		nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_SRC, Saddr);
		nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_DST, Daddr);
		nfct_set_attr(ctTemp, ATTR_REPL_IPV6_SRC, SaddrReply);
		nfct_set_attr(ctTemp, ATTR_REPL_IPV6_DST, DaddrReply);
	}

	Sport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
	Dport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_DST);
	SportReply = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
	DportReply = nfct_get_attr_u16(ct, ATTR_REPL_PORT_DST);

	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, Sport);
	nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, Dport);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, SportReply);
	nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, DportReply);

	if (nfct_query(handler, NFCT_Q_UPDATE, (void*)ctTemp) < 0) {
		if (errno != ENOENT)
			cmm_print(DEBUG_ERROR, "%s: ATTR_STATUS update, nfct_query(NFCT_Q_UPDATE) %s\n", __func__, strerror(errno));
	}
}

/*****************************************************************
* cmmCtNetlinkRemove
*
*
******************************************************************/
int cmmCtNetlinkRemove(struct nfct_handle * handler, struct nf_conntrack *ct)
{
	int rc;

	/*Remove the conntrack*/
	rc = nfct_query(handler, NFCT_Q_DESTROY, (void*)ct);
	if (rc < 0) {
		if (errno != ENOENT)
			cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_DESTROY) %s\n", __func__, strerror(errno));

		return -1;
	}

	return 0;
}

/*****************************************************************
* cmmCtShow
*
*
******************************************************************/
int cmmCtShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct ctTable * temp;
	struct list_head *entry;
	char buf[1024];
	int i, cpt = 0, nb_mult_ids = 0;
	int len;
	unsigned int timeout = 0, orig_timeout;

	for (i = 0 ; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
#ifndef IPSEC_FLOW_CACHE
		__pthread_mutex_lock(&sa_lock);
#endif /* IPSEC_FLOW_CACHE */
        __pthread_mutex_lock(&ctMutex);
#ifdef IPSEC_FLOW_CACHE
		__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

		for(entry = list_first(&ct_table[i]); entry != &ct_table[i]; entry = list_next(entry))
		{
			temp = container_of(entry, struct ctTable, list);
			cpt++;

			/* Keep track of original timeout */
			orig_timeout = nfct_get_attr_u32(temp->ct, ATTR_TIMEOUT);

			if (temp->flags & FPP_PROGRAMMED)
				cmmFeGetTimeout(globalConf.cli.fci_handle, temp, &timeout);
			else
				timeout = 0;
			
			nfct_set_attr_u32(temp->ct, ATTR_TIMEOUT, timeout);

			len = nfct_snprintf(buf, 1024, temp->ct, NFCT_T_UNKNOWN, NFCT_O_PLAIN, NFCT_OF_SHOW_LAYER3);

			nfct_set_attr_u32(temp->ct, ATTR_TIMEOUT, orig_timeout);

#ifdef	IPSEC_FLOW_CACHE
			if (temp->fEntryOrigFwd || temp->fEntryOrigOut)
			{
				len += snprintf(buf + len, 1024 - len, " IpSec:");
				if (temp->fEntryOrigOut)
					len += snprintf(buf + len, 1024 - len, " Orig OUT(sa_nr:%d H0:%04x)", temp->fEntryOrigOut->sa_nr, temp->fEntryOrigOut->sa_handle[0]);

				if (temp->fEntryOrigFwd)
					len += snprintf(buf + len, 1024 - len, " Orig FWD(sa_nr:%d H0:%04x)", temp->fEntryOrigFwd->sa_nr, temp->fEntryOrigFwd->sa_handle[0]);
			}

			if (temp->fEntryRepFwd || temp->fEntryRepOut)
			{
				len += snprintf(buf + len, 1024 - len, " IpSec:");
				if (temp->fEntryRepOut)
					len += snprintf(buf + len, 1024 - len, " Reply OUT(sa_nr:%d H0:%04x)", temp->fEntryRepOut->sa_nr, temp->fEntryRepOut->sa_handle[0]);

				if (temp->fEntryRepFwd)
					len += snprintf(buf + len, 1024 - len, " Reply FWD(sa_nr:%d H0:%04x)", temp->fEntryRepFwd->sa_nr, temp->fEntryRepFwd->sa_handle[0]);
			}

#else
			if (temp->fEntryOrigFwdSA || temp->fEntryOrigOutSA)
			{
				len += snprintf(buf + len, 1024 - len, " IpSec:");
				if (temp->fEntryOrigOutSA)
					len += snprintf(buf + len, 1024 - len, " Orig OUT(sa_nr:1 H0:%04x)", temp->fEntryOrigOutSA->SAInfo.sagd);

				if (temp->fEntryOrigFwdSA)
					len += snprintf(buf + len, 1024 - len, " Orig FWD(sa_nr:1 H0:%04x)", temp->fEntryOrigFwdSA->SAInfo.sagd);
			}

			if (temp->fEntryRepFwdSA || temp->fEntryRepOutSA)
			{
				len += snprintf(buf + len, 1024 - len, " IpSec:");
				if (temp->fEntryRepOutSA)
					len += snprintf(buf + len, 1024 - len, " Reply OUT(sa_nr:1 H0:%04x)", temp->fEntryRepOutSA->SAInfo.sagd);

				if (temp->fEntryRepFwdSA)
					snprintf(buf + len, 1024 - len, " Reply FWD(sa_nr:1 H0:%04x)", temp->fEntryRepFwdSA->SAInfo.sagd);
			}
#endif /* IPSEC_FLOW_CACHE */
			cli_print(cli, "%s, Flags: %x, n_id: %d, local-conn: %s", buf, temp->flags, temp->n_id, ((temp->flags & LOCAL_CONN) ? "yes" : "no"));

			if (temp->n_id > 1)
				nb_mult_ids++;
		}

#ifdef IPSEC_FLOW_CACHE
		__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
		__pthread_mutex_unlock(&ctMutex);
#ifndef IPSEC_FLOW_CACHE
		__pthread_mutex_unlock(&sa_lock);
#endif /* IPSEC_FLOW_CACHE */

		/* Give a chance to other processes waiting for the lock */
		if (!(i % 100))
			sched_yield();
	}
	cli_print(cli, "%d connections printed", cpt);
	cli_print(cli, "%d connections with > 1 ids", nb_mult_ids);

	return CLI_OK;
}

/*****************************************************************
* __cmmCtIsInv
*
* To be used on already matched conntracks (but that can be inverted)
******************************************************************/
static int __cmmCtIsInv(struct nf_conntrack *ct1, struct nf_conntrack *ct2)
{
	int family = nfct_get_attr_u8(ct1, ATTR_ORIG_L3PROTO);

	if (family == AF_INET) {
		if (nfct_get_attr_u32(ct1, ATTR_ORIG_IPV4_SRC) != nfct_get_attr_u32(ct2, ATTR_ORIG_IPV4_SRC))
			return 1;
	}
	else {
		if (memcmp(nfct_get_attr(ct1, ATTR_ORIG_IPV6_SRC), nfct_get_attr(ct2, ATTR_ORIG_IPV6_SRC), 16))
			return 1;
	}
	return 0;
}
/*****************************************************************
* __cmmCtFindId
*
*
******************************************************************/
static int __cmmCtFindId(struct ctTable *ctEntry, u_int32_t id)
{
	int i;

	for (i = 0; i < MAX_CT_ID; i++) {
		if (id == ctEntry->ids[i])
			return 1;
	}
	return 0;
}

/*****************************************************************
* __cmmCtAddId
*
*
******************************************************************/
static int __cmmCtAddId(struct ctTable *ctEntry, u_int32_t id)
{
	int i;

	for (i = 0; i < MAX_CT_ID; i++) {
		if (!ctEntry->ids[i]) {
			ctEntry->ids[i]= id;
			ctEntry->n_id++;
			return 0;
		}
	}
	cmm_print(DEBUG_ERROR, "%s: ctEntry id_array is full. This can lead to synchronisation issues with kernel conntrack table\n", __func__);
	return -1;
}

/*****************************************************************
* __cmmCtDelId
*
*
******************************************************************/
static int __cmmCtDelId(struct ctTable *ctEntry, u_int32_t id)
{
	int i;

	for (i = 0; i < MAX_CT_ID; i++) {
		if (id == ctEntry->ids[i]) {
			ctEntry->ids[i] = 0;
			ctEntry->n_id--;
			return 0;
		}
	}
	return -1;
}


/*****************************************************************
* __cmmCtAdd
*
*
******************************************************************/
static struct ctTable *__cmmCtAdd(struct nf_conntrack *ct)
{
	struct ctTable *newEntry;
	int key;
	const unsigned int *Saddr, *Daddr, *SaddrReply, *DaddrReply;

	/* Add the Conntrack to the local table */
	if (ct_stats.current >= nf_conntrack_max)
	{
		cmm_print(DEBUG_WARNING, "%s: maximum allowed conntracks %d reached\n", __func__, ct_stats.current);
		goto err0;
	}

	newEntry = (struct ctTable*) malloc(sizeof(struct ctTable));
	if (newEntry == NULL)
	{
		cmm_print(DEBUG_ERROR, "%s: malloc failed\n", __func__);
		goto err0;
	}
	memset(newEntry, 0, sizeof(struct ctTable));

	newEntry->ct = ct;

	newEntry->family = nfct_get_attr_u8(ct, ATTR_ORIG_L3PROTO);

	__cmmCtAddId(newEntry, nfct_get_attr_u32(ct, ATTR_ID));

	if (newEntry->family == AF_INET)
	{
		Saddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		Daddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
		SaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
		DaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV4_DST);
	}
	else
	{
		Saddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
		Daddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_DST);
		SaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);
		DaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV6_DST);
	}
	
	//Add the Conntrack to the local table
	key = HASH_CT(newEntry->family, Saddr,
			Daddr,
			nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC),
			nfct_get_attr_u16(ct, ATTR_ORIG_PORT_DST),
			nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO));

	list_add(&ct_table[key], &newEntry->list);

	//Add the Conntrack to the local by replier table (used for flow cache <-> ct lookup)
	key = HASH_CT(newEntry->family, SaddrReply,
			DaddrReply,
			nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC),
			nfct_get_attr_u16(ct, ATTR_REPL_PORT_DST),
			nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO));

	list_add(&ct_table_by_rep[key], &newEntry->list_by_rep);

	key = HASH_RT(newEntry->family, Saddr, SaddrReply);

	list_add(&ct_table_by_orig_route[key], &newEntry->list_by_orig_route);

	key = HASH_RT(newEntry->family, SaddrReply, Saddr);

	list_add(&ct_table_by_rep_route[key], &newEntry->list_by_rep_route);

	ct_stats.current++;

	newEntry->timeout = 0;
	newEntry->tlast = -1;

	return newEntry;

err0:
	return NULL;
}

/*****************************************************************
* __cmmCtRemove
*
*
******************************************************************/
void __cmmCtRemove(struct ctTable *ctEntry)
{
#ifndef IPSEC_FLOW_CACHE
	int ii;

	/* remove ctEntry from SAs lists */
	for (ii=0; ii<4; ii++)
	{
		cmm_print(DEBUG_INFO, "%s(%d) list_by_sa[%d] %p\n",
				__FUNCTION__,__LINE__,ii, &ctEntry->list_by_sa[ii]);
		list_del(&ctEntry->list_by_sa[ii]);
	}

	ctEntry->fEntryOrigFwdSA = NULL;
	ctEntry->fEntryOrigOutSA = NULL;
	ctEntry->fEntryRepFwdSA = NULL;
	ctEntry->fEntryRepOutSA = NULL;
	list_del(&ctEntry->flow_no_sa_list_node);
#endif /* IPSEC_FLOW_CACHE */

	ct_stats.current--;

	list_del(&ctEntry->list);
	list_del(&ctEntry->list_by_rep);

	list_del(&ctEntry->list_by_orig_route);
	list_del(&ctEntry->list_by_rep_route);

	nfct_destroy(ctEntry->ct);
	free(ctEntry);
}

/*****************************************************************
* __cmmCtFindFromFlow
*
*
******************************************************************/
struct ctTable *__cmmCtFindFromFlow(int family, unsigned int *saddr, unsigned int *daddr, unsigned short sport, unsigned short dport, unsigned char proto, char *orig, int local)
{
	
	struct ctTable *track_entry;
	struct list_head *entry;
	int key, key2;
	const unsigned int *Saddr, *Daddr, *SaddrReply, *DaddrReply;
	int ipAddrLen = IPADDRLEN(family);

	key = HASH_CT(family, saddr, daddr, sport, dport, proto);
	key2 = 0xffffffff;

second_pass_ct:

	entry = list_first(&ct_table[key]);

	while (entry != &ct_table[key])
	{

		track_entry = container_of(entry, struct ctTable, list);

		if (nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L3PROTO) != family)
			goto next_orig;

		/* If local is enabled , find the flow which matches
		   only local connections otherwise find the flow matching 
		   only non-local connections */
		if ( (local && !(track_entry->flags & LOCAL_CONN)) ||
				(!local && (track_entry->flags & LOCAL_CONN)))
			goto next_orig;

		if (family == AF_INET)
		{
			Saddr = nfct_get_attr(track_entry->ct, ATTR_ORIG_IPV4_SRC);
			Daddr = nfct_get_attr(track_entry->ct, ATTR_ORIG_IPV4_DST);
		}
		else
		{
			Saddr = nfct_get_attr(track_entry->ct, ATTR_ORIG_IPV6_SRC);
			Daddr = nfct_get_attr(track_entry->ct, ATTR_ORIG_IPV6_DST);
		}

		if (	/*IP addresses*/
			(!memcmp(Saddr, saddr, ipAddrLen)) &&
			(!memcmp(Daddr, daddr, ipAddrLen)) &&
			/*Port*/
			(nfct_get_attr_u16(track_entry->ct, ATTR_ORIG_PORT_SRC) == sport) &&
			(nfct_get_attr_u16(track_entry->ct, ATTR_ORIG_PORT_DST) == dport) &&
			/*Protocol*/
			(nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L4PROTO) == proto)
		)
		{
			//Entry found
			*orig = 1;
			goto end;
		}

		if (	/*IP addresses*/
			(!memcmp(Saddr, daddr, ipAddrLen)) &&
			(!memcmp(Daddr, saddr, ipAddrLen)) &&
			/*Port*/
			(nfct_get_attr_u16(track_entry->ct, ATTR_ORIG_PORT_SRC) == dport) &&
			(nfct_get_attr_u16(track_entry->ct, ATTR_ORIG_PORT_DST) == sport) &&
			/*Protocol*/
			(nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L4PROTO) == proto)
		)
		{

			//Entry found
			*orig = 0;
			goto end;
		}

	next_orig:
		entry = list_next(entry);
	}

	entry = list_first(&ct_table_by_rep[key]);

	while (entry != &ct_table_by_rep[key])
	{

		track_entry = container_of(entry, struct ctTable, list_by_rep);

		if (nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L3PROTO) != family)
			goto next_rep;

		/* If local is enabled , find the flow which matches
		   only local connections otherwise find the flow matching 
		   only non-local connections */
		if ( (local && !(track_entry->flags & LOCAL_CONN)) ||
				(!local && (track_entry->flags & LOCAL_CONN)))
			goto next_rep;

		if (family == AF_INET)
		{
			SaddrReply = nfct_get_attr(track_entry->ct, ATTR_REPL_IPV4_SRC);
			DaddrReply = nfct_get_attr(track_entry->ct, ATTR_REPL_IPV4_DST);
		}
		else
		{
			SaddrReply = nfct_get_attr(track_entry->ct, ATTR_REPL_IPV6_SRC);
			DaddrReply = nfct_get_attr(track_entry->ct, ATTR_REPL_IPV6_DST);
		}

		if (	/*IP addresses*/
			(!memcmp(SaddrReply, saddr, ipAddrLen)) &&
			(!memcmp(DaddrReply, daddr, ipAddrLen)) &&
			/*Port*/
			(nfct_get_attr_u16(track_entry->ct, ATTR_REPL_PORT_SRC) == sport) &&
			(nfct_get_attr_u16(track_entry->ct, ATTR_REPL_PORT_DST) == dport) &&
			/*Protocol*/
			(nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L4PROTO) == proto)
		)
		{
			//Entry found
			*orig = 0;
			goto end;
		}

		if (	/*IP addresses*/
			(!memcmp(SaddrReply, daddr, ipAddrLen)) &&
			(!memcmp(DaddrReply, saddr, ipAddrLen)) &&
			/*Port*/
			(nfct_get_attr_u16(track_entry->ct, ATTR_REPL_PORT_SRC) == dport) &&
			(nfct_get_attr_u16(track_entry->ct, ATTR_REPL_PORT_DST) == sport) &&
			/*Protocol*/
			(nfct_get_attr_u8(track_entry->ct, ATTR_ORIG_L4PROTO) == proto)
		)
		{
			//Entry found
			*orig = 1;
			goto end;
		}

	next_rep:
		entry = list_next(entry);
	}

	track_entry = NULL;

	if (key2 == 0xffffffff)
	{
		// HASH_CT is treating source and dest addr assymetrically.
		// Need to search second time because conntrack could be registered for opposite direction
		key2 = HASH_CT(family, daddr, saddr, dport, sport, proto);
		if (key != key2)
		{
			key = key2;
			goto second_pass_ct;
		}
	}
end:

	return track_entry;
}

/*****************************************************************
* cmmCtCompare
*
*
******************************************************************/
static int cmmCtCompare(struct nf_conntrack *ct1, struct nf_conntrack *ct2)
{
	int family;

	if ((family = nfct_get_attr_u8(ct1, ATTR_ORIG_L3PROTO)) != nfct_get_attr_u8(ct2, ATTR_ORIG_L3PROTO))
		return 0;

	if (family == AF_INET)
	{
		if (	/*IP addresses*/
			nfct_get_attr_u32(ct1, ATTR_ORIG_IPV4_SRC) != nfct_get_attr_u32(ct2, ATTR_ORIG_IPV4_SRC) ||
			nfct_get_attr_u32(ct1, ATTR_ORIG_IPV4_DST) != nfct_get_attr_u32(ct2, ATTR_ORIG_IPV4_DST) ||
			/*Reply IP addresses*/
			nfct_get_attr_u32(ct1, ATTR_REPL_IPV4_SRC) != nfct_get_attr_u32(ct2, ATTR_REPL_IPV4_SRC) ||
			nfct_get_attr_u32(ct1, ATTR_REPL_IPV4_DST) != nfct_get_attr_u32(ct2, ATTR_REPL_IPV4_DST))

			return 0;
	}
	else
	{
		if (	/*IP addresses*/
			memcmp(nfct_get_attr(ct1, ATTR_ORIG_IPV6_SRC), nfct_get_attr(ct2, ATTR_ORIG_IPV6_SRC), 16) ||
			memcmp(nfct_get_attr(ct1, ATTR_ORIG_IPV6_DST), nfct_get_attr(ct2, ATTR_ORIG_IPV6_DST), 16) ||

			/*Reply IP addresses*/
			memcmp(nfct_get_attr(ct1, ATTR_REPL_IPV6_SRC), nfct_get_attr(ct2, ATTR_REPL_IPV6_SRC), 16) ||
			memcmp(nfct_get_attr(ct1, ATTR_REPL_IPV6_DST), nfct_get_attr(ct2, ATTR_REPL_IPV6_DST), 16))
			return 0;
	}
	
	if(/*Port*/
		nfct_get_attr_u16(ct1, ATTR_ORIG_PORT_SRC) != nfct_get_attr_u16(ct2, ATTR_ORIG_PORT_SRC) ||
		nfct_get_attr_u16(ct1, ATTR_ORIG_PORT_DST) != nfct_get_attr_u16(ct2, ATTR_ORIG_PORT_DST) ||

		/*Reply Port*/
		nfct_get_attr_u16(ct1, ATTR_REPL_PORT_SRC) != nfct_get_attr_u16(ct2, ATTR_REPL_PORT_SRC) ||
		nfct_get_attr_u16(ct1, ATTR_REPL_PORT_DST) != nfct_get_attr_u16(ct2, ATTR_REPL_PORT_DST) ||

		/*Protocol*/
		nfct_get_attr_u8(ct1, ATTR_ORIG_L4PROTO) != nfct_get_attr_u8(ct2, ATTR_ORIG_L4PROTO)
	)
		return 0;

	return 1;
}

/*****************************************************************
* cmmCtCompareInv
*
*
******************************************************************/
static int cmmCtCompareInv(struct nf_conntrack *ct1, struct nf_conntrack *ct2)
{
	int family;

	if ((family = nfct_get_attr_u8(ct1, ATTR_ORIG_L3PROTO)) != nfct_get_attr_u8(ct2, ATTR_REPL_L3PROTO))
		return 0;

	if (family == AF_INET)
	{
		if (	/*IP addresses*/
			nfct_get_attr_u32(ct1, ATTR_ORIG_IPV4_SRC) != nfct_get_attr_u32(ct2, ATTR_REPL_IPV4_SRC) ||
			nfct_get_attr_u32(ct1, ATTR_ORIG_IPV4_DST) != nfct_get_attr_u32(ct2, ATTR_REPL_IPV4_DST) ||
			/*Reply IP addresses*/
			nfct_get_attr_u32(ct1, ATTR_REPL_IPV4_SRC) != nfct_get_attr_u32(ct2, ATTR_ORIG_IPV4_SRC) ||
			nfct_get_attr_u32(ct1, ATTR_REPL_IPV4_DST) != nfct_get_attr_u32(ct2, ATTR_ORIG_IPV4_DST))

			return 0;
	}
	else
	{
		if (	/*IP addresses*/
			memcmp(nfct_get_attr(ct1, ATTR_ORIG_IPV6_SRC), nfct_get_attr(ct2, ATTR_REPL_IPV6_SRC), 16) ||
			memcmp(nfct_get_attr(ct1, ATTR_ORIG_IPV6_DST), nfct_get_attr(ct2, ATTR_REPL_IPV6_DST), 16) ||

			/*Reply IP addresses*/
			memcmp(nfct_get_attr(ct1, ATTR_REPL_IPV6_SRC), nfct_get_attr(ct2, ATTR_ORIG_IPV6_SRC), 16) ||
			memcmp(nfct_get_attr(ct1, ATTR_REPL_IPV6_DST), nfct_get_attr(ct2, ATTR_ORIG_IPV6_DST), 16))
			return 0;
	}

	if(/*Port*/
		nfct_get_attr_u16(ct1, ATTR_ORIG_PORT_SRC) != nfct_get_attr_u16(ct2, ATTR_REPL_PORT_SRC) ||
		nfct_get_attr_u16(ct1, ATTR_ORIG_PORT_DST) != nfct_get_attr_u16(ct2, ATTR_REPL_PORT_DST) ||

		/*Reply Port*/
		nfct_get_attr_u16(ct1, ATTR_REPL_PORT_SRC) != nfct_get_attr_u16(ct2, ATTR_ORIG_PORT_SRC) ||
		nfct_get_attr_u16(ct1, ATTR_REPL_PORT_DST) != nfct_get_attr_u16(ct2, ATTR_ORIG_PORT_DST) ||

		/*Protocol*/
		nfct_get_attr_u8(ct1, ATTR_ORIG_L4PROTO) != nfct_get_attr_u8(ct2, ATTR_REPL_L4PROTO)
	)
		return 0;

	return 1;
}
/***************************************************************
* __cmmCtFindLocalCtFromFlow
*
*
******************************************************************/
struct ctTable *__cmmCtFindLocalCtFromFlow(struct flow *flow)
{
	struct ctTable *ctEntry;
	unsigned int iifindex;
	char orig = 0;

	ctEntry = __cmmCtFindFromFlow(flow->family, (unsigned int*)flow->sAddr, (unsigned int*)flow->dAddr,0,0,flow->proto, &orig, 1);
	if (!ctEntry)
		goto null_ct;


	if (!(ctEntry->flags & LOCAL_CONN))
		goto null_ct;

	if (orig)
		iifindex = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_COMCERTO_FP_IFINDEX);
	else
		iifindex = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_COMCERTO_FP_IFINDEX);

	if (flow->iifindex != iifindex)
	{
		cmm_print(DEBUG_INFO, "%s: flow ifindex doesn't match conntrack: %d-%d \n", __func__, flow->iifindex, iifindex);
		goto null_ct;
	}
	goto found;

null_ct:
	ctEntry = NULL;
found:
	return ctEntry;
}
/*****************************************************************
* __cmmCtFind
*
*
******************************************************************/
struct ctTable *__cmmCtFind(struct nf_conntrack *ctTemp)
{
	struct ctTable *ctEntry;
	struct list_head *entry;
	int family;
	int key;
	const void *dAddr, *sAddr;
	u_int16_t sport, dport;
	char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN];

	family = nfct_get_attr_u8(ctTemp, ATTR_ORIG_L3PROTO);

	if (family == AF_INET)
	{
		sAddr = nfct_get_attr(ctTemp, ATTR_ORIG_IPV4_SRC);
		dAddr = nfct_get_attr(ctTemp, ATTR_ORIG_IPV4_DST);
	}
	else
	{
		sAddr = nfct_get_attr(ctTemp, ATTR_ORIG_IPV6_SRC);
		dAddr = nfct_get_attr(ctTemp, ATTR_ORIG_IPV6_DST);
	}

	sport = nfct_get_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC);
	dport = nfct_get_attr_u16(ctTemp, ATTR_ORIG_PORT_DST);

	key = HASH_CT(family, sAddr, dAddr, sport, dport,
		nfct_get_attr_u8(ctTemp, ATTR_ORIG_L4PROTO));

	entry = list_first(&ct_table[key]);

	while (entry != &ct_table[key])
	{
		ctEntry = container_of(entry, struct ctTable, list);
		if (cmmCtCompare(ctEntry->ct, ctTemp))
			//Entry found
			goto found;

		entry = list_next(entry);
	}

	/* Search for inverted entry */
	entry = list_first(&ct_table_by_rep[key]);

	while (entry != &ct_table_by_rep[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_rep);
		if (cmmCtCompareInv(ctEntry->ct, ctTemp))
			goto found;

		entry = list_next(entry);
	}

	ctEntry = NULL;

found:
	cmm_print(DEBUG_INFO, "%s: conntrack %sfound src:%s dst:%s sport:%d dport:%d\n", __func__,
			ctEntry == NULL ? "not " : "",
			inet_ntop(family, sAddr, saddr_buf, INET6_ADDRSTRLEN),
			inet_ntop(family, dAddr, daddr_buf, INET6_ADDRSTRLEN),
			ntohs(sport), ntohs(dport));

	return ctEntry;
}


/*****************************************************************
* __cmmCtItfRegister
*
*
******************************************************************/
static int __cmmCtItfRegister(struct RtEntry *route, const char *dir)
{
	char ifname[IFNAMSIZ];
	int rc;
#ifdef VLAN_FILTER
	struct interface * in_itf;
	struct interface * br_itf;
	struct interface_bridge_vlan_info *vinfo = NULL;

	/* Check incoming interface whether it is part of a bridge with vlan filetring is enabled,
	   if enabled update ingress filtering details*/
	route->vlan_filter_flags = 0;
	in_itf = __itf_find(route->underlying_iifindex);
	if (in_itf) {
		if(in_itf->master) {
			br_itf = __itf_find(in_itf->master);
			if(br_itf) {
				if (br_itf->vlan_filtering_enab) {
					cmm_print(DEBUG_INFO, "%s: vlan filtering is enabled on bridge %d\n",__func__,in_itf->master);
					route->vlan_filter_flags |= VLAN_FILTER_INGRESS_EN;
					vinfo = vinfo_find_by_pvid(route->underlying_iifindex);
					if(vinfo) {
						if (vinfo->vlan_info.vid == route->underlying_vlan_id)
							route->vlan_filter_flags |= INGRESS_PVID;
					}
				}
			}
		}
	}
#endif

	if (!route->phys_oifindex || (route->flags & CHECK_BRIDGE_PORT))
	{
		rc = __itf_is_bridge(route->oifindex);
		if (rc < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: route %s output interface lookup failed\n", __func__, dir);
			goto err;
		}

		if (rc)
		{
			rc = cmmBrToFF(route);
			if (rc < 0)
			{
				cmm_print(DEBUG_ERROR, "%s: route %s output interface lookup failed\n", __func__, dir);
				goto err;
			}
		}
		else
			route->phys_oifindex = route->oifindex;

		route->flags &= ~CHECK_BRIDGE_PORT;
	}

	rc = __itf_is_programmed(route->phys_oifindex);
	if (rc <= 0)
	{
		cmm_print(DEBUG_ERROR, "%s: route %s output interface %s is not programmed in FPP\n", __func__, dir, if_indextoname(route->phys_oifindex, ifname));
		goto err;
	}

	return 0;

err:
	return -1;
}

/*****************************************************************
* __cmmNeighRegister
*
*
******************************************************************/
static int __cmmNeighRegister(struct RtEntry *route, const char *dir)
{
	char addr_buf[INET6_ADDRSTRLEN];
	int rc;

	if ((rc = __itf_is_noarp(route->oifindex)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: itf_is_noarp() failed for %s entry\n", __func__, dir);
		goto err;
	}

	if (rc)
		goto out;

	if (!route->neighEntry)
	{
		route->neighEntry = __cmmNeighGet(route->family, route->gwAddr, route->oifindex);
		if (!route->neighEntry)
		{
			cmm_print(DEBUG_ERROR, "%s: arp %s entry add failed\n", __func__, dir);
			goto err;
		}
	}

	if (!(route->neighEntry->state & NUD_VALID))
	{
		cmm_print(DEBUG_INFO, "%s: %s unresolved mac address for %s entry\n",
					__func__, inet_ntop(route->family, route->neighEntry->ipAddr, addr_buf, sizeof(addr_buf)), dir);

		goto err;
	}

out:
	return 0;

err:
	return -1;
}


/*****************************************************************
* __cmmFPPRouteRegister
*
*
******************************************************************/
static int __cmmFPPRouteRegister(struct ct_route *rt, const char *dir)
{
	const unsigned char *dst_mac;
	int iifindex;

	if (rt->fpp_route)
		goto out;

	if (rt->route->neighEntry)
		dst_mac = rt->route->neighEntry->macAddr;
	else
		dst_mac = null_mac;

#if defined(LS1043)
	if (__itf_is_bridge(rt->route->iifindex))
 		iifindex = rt->route->underlying_iifindex; 
	else
#endif
		iifindex = rt->route->iifindex;
		

	if (rt->route->flow_flags & FLOWFLAG_FLOATING_TUNNEL)
 		rt->fpp_route = __cmmFPPRouteGet(rt->route->phys_oifindex, iifindex, 
						rt->route->underlying_iifindex, 
						dst_mac, rt->route->mtu, rt->route->dAddr, IPADDRLEN(rt->route->family));
	else
 		rt->fpp_route = __cmmFPPRouteGet(rt->route->phys_oifindex, iifindex, 
						rt->route->underlying_iifindex, 
						dst_mac, rt->route->mtu, NULL, 0);

	if (!rt->fpp_route)
	{
		goto err;
	}


 	if (rt->route->flow_flags & ( FLOWFLAG_SOCKET_ROUTE | FLOWFLAG_FLOATING_TUNNEL | FLOWFLAG_LOCAL | FLOWFLAG_SA_ROUTE))
 		rt->fpp_route->flags |= IS_LOCAL;

#ifdef VLAN_FILTER
	/* Update vlan filter flags*/
	rt->fpp_route->vlan_filter_flags = rt->route->vlan_filter_flags;
	rt->fpp_route->egress_vid = rt->route->egress_vid;
	rt->fpp_route->underlying_vlan_id = rt->route->underlying_vlan_id;
#endif

out:
	return 0;

err:
	return -1;
}


/*****************************************************************
* __cmmRouteRegister
*
*
******************************************************************/
int __cmmRouteRegister(struct ct_route *rt, struct flow *flow, const char *dir)
{
	struct ctTable* ctEntry = NULL;

	if (!rt->route)
	{
		if (flow->flow_flags & FLOWFLAG_LOCAL)
		{
			if(!(flow->sAddr && flow->dAddr))
				goto err;

			ctEntry = __cmmCtFindLocalCtFromFlow(flow);
			
			if(!ctEntry) 
			{
				if(!(flow->sAddr[0]))
				{
					flow->iifindex=0;
					flow->fwmark=0;
					goto getRoute;
				}

				goto err;
			}	

			if (ctEntry->flags & LOCAL_CONN_ORIG)
				flow->fwmark = nfct_get_attr_u32(ctEntry->ct, ATTR_ORIG_COMCERTO_FP_MARK);
			else
				flow->fwmark = nfct_get_attr_u32(ctEntry->ct, ATTR_REPL_COMCERTO_FP_MARK);

			flow->iifindex = 0;
		}
getRoute:
		rt->route = __cmmRouteGet(flow);
		if (!rt->route)
		{
			cmm_print(DEBUG_ERROR, "%s: route %s entry add failed\n", __func__, dir);
			goto err;
		}
	}
	rt->route->flow_flags = flow->flow_flags;

	if (__cmmNeighRegister(rt->route, dir) < 0 && !(FLOWFLAG_IGNORE_NEIGH & flow->flow_flags))
	{
		goto err;
	}

	if (__cmmCtItfRegister(rt->route, dir) < 0)
	{
		goto err;
	}

	if (__cmmFPPRouteRegister(rt, dir) < 0)
	{
		goto err;
	}

	return 0;

err:
	return -1;
}

/*****************************************************************
* __cmmCtTunnelRouteRegister
*
*
******************************************************************/
static int __cmmCtTunnelRouteRegister(struct ct_route *rt, struct ct_route *tunnel_rt,
								struct flow *Saflow, const char *dir)
{
	struct interface *itf = NULL;
	unsigned int dAddrV4, dAddrV6[4];
	int encap_type = AF_UNSPEC; /* AF_UNSPEC 0, AF_INET  2 , AF_INET6 10, Secure 0xFF*/

	struct flow flow;

	if(Saflow)/* This is an IPSec Tunnel */
		encap_type = CMM_ENCAP_SECURE;
	else
	{
		itf = __itf_get(rt->route->oifindex);
		if (!itf)
			return -1;

		if (!__itf_is_tunnel(itf))
			goto out;
		encap_type = itf->tunnel_family;
		/* Interface tunnel family type is not expected as 0xff, 
		   as it is internally used for SAs.*/
		if (encap_type == CMM_ENCAP_SECURE)
			goto out;
	}

	switch (encap_type)
	{
		case AF_INET:
			{
				if (!____itf_is_floating_sit_tunnel(itf))
					goto out;


				dAddrV4 = tunnel_get_ipv4_dst(rt->route, itf);
				if (!dAddrV4)
					goto err0;
				SET_FLOW_PARAMS(flow, &itf->tunnel_parm4.iph.saddr, &dAddrV4,
						itf->tunnel_parm4.iph.protocol, itf->tunnel_family,
						itf->ifindex, FLOWFLAG_LOCAL | FLOWFLAG_FLOATING_TUNNEL);
			}
			break;
		case AF_INET6:
			{
				if (!____itf_is_4o6_tunnel(itf))
					goto out;

				/* Use tunnel remote address directly */
				memcpy(dAddrV6, itf->tunnel_parm6.raddr.s6_addr32, 16);

				SET_FLOW_PARAMS(flow, (unsigned int *)itf->tunnel_parm6.laddr.s6_addr,
						dAddrV6,itf->tunnel_parm6.proto, itf->tunnel_family,
						itf->ifindex, FLOWFLAG_LOCAL | FLOWFLAG_FLOATING_TUNNEL);
			}
			break;
		case CMM_ENCAP_SECURE:
			{
				SET_FLOW_PARAMS(flow, Saflow->sAddr, Saflow->dAddr, 0, Saflow->family, 0,
						FLOWFLAG_SA_ROUTE | FLOWFLAG_IGNORE_NEIGH);
			}
			break;
		case AF_UNSPEC:
		default:
			goto out;
	}


	flow.fwmark = 0;
	flow.underlying_iif = 0;

	if (__cmmRouteRegister(tunnel_rt, &flow, dir) < 0)
		goto err0;

out:
	__itf_put(itf);

	return 0;

err0:
	__itf_put(itf);

	return -1;
}

/*****************************************************************
* __cmmCheckFPPRouteIdUpdate
*
*
******************************************************************/
void __cmmCheckFPPRouteIdUpdate(struct ct_route *rt, int *flags)
{
	if (rt->fpp_route)
	{
		if (rt->fpp_route_id != rt->fpp_route->id)
		{
			*flags |= FPP_NEEDS_UPDATE;
			rt->fpp_route_id = rt->fpp_route->id;
		}
	}
	else if (rt->fpp_route_id)
	{
		*flags |= FPP_NEEDS_UPDATE;
		rt->fpp_route_id = 0;
	}
}

/*****************************************************************
* ____cmmCtLocalRegister
*
*
******************************************************************/
int ____cmmCtLocalRegister(FCI_CLIENT *fci_handle, struct ctTable* ctEntry)
{
	lro_socket_open(fci_handle, ctEntry);

	/* Update all dynamic connections/ tunnel routes for which tunnel route
	is not attached */
	__cmmRouteLocalNew(fci_handle, ctEntry);
	return 0;
}

#ifndef IPSEC_FLOW_CACHE

/* this function is called for each SA_INFO received in conntrack message,
   if there is a matching SA for given SA_INFO, that SA pointer gets
   filled in fEntrySA
*/
static void __cmm_ct_get_SA(struct ctTable *ctEntry,
						unsigned short *xfrm_handle,
						struct SATable **fEntrySA,
						int replier_f)
{
	struct SATable *sa_entry = *fEntrySA;
	struct list_head *list_node;
	int list_index = replier_f * 2;
	
	/* in case of rekey , ctEntry might have already having SA, 
	   If SPIs not matching, cleanup the existing SA and update with new SA */
	if ((sa_entry) && (sa_entry->SAInfo.sagd != *xfrm_handle))
	{
		/* ctEntry has 4 instances of struct list_head to store the ctEntry 
		 * in SAs lists
		 * Each ctEntry can exist in 4 SAs lists.
		 * first 2 list_head instances are for originator inbound and originator outbound 
		 * next 2 list_head instances are for replier inbound and replier outbound
		 */
		/* calculating list_index based on direction */
		if (!(sa_entry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
			list_index ++;
		list_node = &ctEntry->list_by_sa[list_index];
		list_del(list_node);
		cmm_print(DEBUG_INFO,"%s(%d) XFRM SPI of existing sagd %x, sagd %x, list_node %p , index %d\n",
			__FUNCTION__,__LINE__,sa_entry->SAInfo.sagd, *xfrm_handle, list_node, list_index);
	}
	/* same SPI case, just return */
	else if (sa_entry)
	{
		cmm_print(DEBUG_INFO,"%s(%d) XFRM SPI of existing sagd %x, xfm_hadle %x, return\n",
			__FUNCTION__,__LINE__,sa_entry->SAInfo.sagd, *xfrm_handle);
		return;
	}
	
	if (!(sa_entry = cmmSAFind(*xfrm_handle)) || (sa_entry->flags & SA_DELETE))
	{
		cmm_print(DEBUG_INFO,"%s(%d) xfrm_handle %x, SA not found, setting flow no SA flag\n",
			__FUNCTION__,__LINE__,*xfrm_handle);
		if (!replier_f)
			ctEntry->flags |= FLOW_NO_ORIG_SA;
		else
			ctEntry->flags |= FLOW_NO_REPL_SA;
		*fEntrySA = NULL;
		return;
	}
	/* non null SA, add it to ctEntry */
	list_index = replier_f * 2;
	if (!(sa_entry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
		list_index ++;
	list_node = &ctEntry->list_by_sa[list_index];
	cmm_print(DEBUG_INFO,"%s(%d) xfrm_handle %x,  %s SA found, list_node %p, list_index %d\n",
		__FUNCTION__,__LINE__,*xfrm_handle,
		(sa_entry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND) ? "Inbound" : "Outbound",
		 list_node, list_index);
	list_add(&sa_entry->ctentry_list[replier_f], list_node);
	*fEntrySA = sa_entry;
	return;
}

/* this function is used to fill originator or replier related SAs in ctentry */
static void __cmm_ct_fill_orig_repl_SAs(struct ctTable *ctEntry, 
						uint16_t *xfrm_handle,
						struct SATable **fEntryOutSA,
						struct SATable **fEntryFwdSA,
						int replier_f)
{
	/* assumption is : as we are setting flow-no-sa flag in multiple SAs info received case,
		so when this function is invoked , only first entry of each direction ((FWD or IN) and OUT) may be zero or non-zero */
	/*  zero indicates no SA is used for that direction */	
	/* filling FWD/IN SA info*/
	if (xfrm_handle[0])
	{
		__cmm_ct_get_SA(ctEntry, xfrm_handle, fEntryFwdSA, replier_f);
	
		cmm_print(DEBUG_INFO,"%s(%d) %s: fEntryFwd sa handle %x , SPI %x\n",
			__FUNCTION__,__LINE__, (replier_f) ? "REPLIER" : "ORIGINATOR",
			(*fEntryFwdSA) ? (*fEntryFwdSA)->SAInfo.sagd : 0,
			(*fEntryFwdSA) ? (*fEntryFwdSA)->SAInfo.id.spi : 0);
	}	

	/* filling OUT SA info */
	if (xfrm_handle[MAX_SAs_INFO_PER_DIR_IN_NL_MSG])
	{
		__cmm_ct_get_SA(ctEntry, xfrm_handle+MAX_SAs_INFO_PER_DIR_IN_NL_MSG, 
			fEntryOutSA, replier_f);
	
		cmm_print(DEBUG_INFO,"%s(%d) %s: fEntryOut sa handle %x , SPI %x\n",
			__FUNCTION__,__LINE__,  (replier_f) ? "REPLIER" : "ORIGINATOR", 
			(*fEntryOutSA)? (*fEntryOutSA)->SAInfo.sagd : 0,
			(*fEntryOutSA)? (*fEntryOutSA)->SAInfo.id.spi : 0);
	}	

	return;
}

/* this function is used to return the number of SAs used by connection per direction 
 */
static int __cmm_ct_get_num_per_dir_ipsec_SAs(uint16_t *xfrm_handle)
{
	int ii, num=0;

	for (ii=0; ii< MAX_SAs_INFO_PER_DIR_IN_NL_MSG; ii++)
	{
		if (xfrm_handle[ii])
			num ++;
	}
	return num;
}


/* this function is used to fill ipsec SAs info in ctentry
   from the received netlink ipsec attributes
 */
static void cmm_ct_fill_ipsec_info(struct ctTable *ctEntry, uint16_t *orig_xfrm_handle,
											uint16_t *rep_xfrm_handle)
{
	/* not offloading if ctentry consisting of more than one SA in any direction */
	if (orig_xfrm_handle && ((__cmm_ct_get_num_per_dir_ipsec_SAs(orig_xfrm_handle) > 1 ) ||
		(__cmm_ct_get_num_per_dir_ipsec_SAs(orig_xfrm_handle+MAX_SAs_INFO_PER_DIR_IN_NL_MSG) > 1 )))
	{
		cmm_print(DEBUG_ERROR, "%s(%d) multiple SAs per flow, setting flag FLOW_NO_SA\n",__FUNCTION__,__LINE__);
		ctEntry->flags |=  FLOW_NO_ORIG_SA;
	}
	if (rep_xfrm_handle && ((__cmm_ct_get_num_per_dir_ipsec_SAs(rep_xfrm_handle) > 1 ) ||
		(__cmm_ct_get_num_per_dir_ipsec_SAs(rep_xfrm_handle+MAX_SAs_INFO_PER_DIR_IN_NL_MSG) > 1 )))
	{
		cmm_print(DEBUG_ERROR, "%s(%d) multiple SAs per flow, setting flag FLOW_NO_SA\n",__FUNCTION__,__LINE__);
		ctEntry->flags |=  FLOW_NO_REPL_SA;
	}	

	if (!(ctEntry->flags &  FLOW_NO_ORIG_SA) && (orig_xfrm_handle))
		__cmm_ct_fill_orig_repl_SAs(ctEntry, orig_xfrm_handle,
							&ctEntry->fEntryOrigOutSA, &ctEntry->fEntryOrigFwdSA, 0);

	if (!(ctEntry->flags &  FLOW_NO_REPL_SA) && (rep_xfrm_handle))
		__cmm_ct_fill_orig_repl_SAs(ctEntry, rep_xfrm_handle,
						&ctEntry->fEntryRepOutSA, &ctEntry->fEntryRepFwdSA, 1);
	return;
}

/* When there is any new SA is established,
 * check if ctEntry is having any matching sgid with new SA's sgid
 * if matching return 1, else return 0
 */
uint8_t cmmCheckIfCtEntryWithSGID(struct ctTable *ctEntry, unsigned short sgid)
{
	unsigned short	*orig_xfrm_handle, *rep_xfrm_handle;
	struct nf_conntrack *ct = ctEntry->ct;
	int ii;

	if (ct)
	{
		orig_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_ORIG_COMCERTO_FP_XFRM_HANDLE);
		rep_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_REPL_COMCERTO_FP_XFRM_HANDLE);

		for (ii=0; ii< MAX_SAs_INFO_PER_DIR_IN_NL_MSG; ii++)
		{
			if ((orig_xfrm_handle && (orig_xfrm_handle[ii] == sgid)) ||
			    (rep_xfrm_handle && (rep_xfrm_handle[ii] == sgid)))
				return 1;
		}
	}
	return 0;
}

#endif /* IPSEC_FLOW_CACHE */
/*****************************************************************
* ____cmmCtRegister
*
*
******************************************************************/
int ____cmmCtRegister(FCI_CLIENT *fci_handle, struct ctTable *ctEntry)
{
	struct nf_conntrack *ct = ctEntry->ct;
	struct nf_conntrack *ctTemp = NULL;
	int dir = ctEntry->dir;
	const unsigned int *dAddrOrig, *dAddrRepl, *sAddrOrig, *sAddrRepl;
#ifdef IPSEC_FLOW_CACHE
	unsigned char proto;
	unsigned short dPortOrig, dPortRepl, sPortOrig, sPortRepl;
#else
	unsigned short dPortOrig, dPortRepl;
	unsigned short	*orig_xfrm_handle, *rep_xfrm_handle;
#endif 
	struct flow flow;
	void *tmp;
	int key;
	int rc;
 	int iif, rep_iif, iif_programmed;
	struct interface *itf,*out_itf;
	struct RtEntry *route;
	struct SATable *SAEntry = NULL;

#ifdef IPSEC_FLOW_CACHE
	proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);
#endif 

	if (ctEntry->family == AF_INET)
	{
		sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
		dAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
		dAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV4_DST);
	}
	else
	{
		sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
		sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);
		dAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV6_DST);
		dAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV6_DST);
	}

#ifdef IPSEC_FLOW_CACHE
	sPortOrig = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
	sPortRepl = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
#endif 
	dPortOrig = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_DST);
	dPortRepl = nfct_get_attr_u16(ct, ATTR_REPL_PORT_DST);

	if (dir & ORIGINATOR)
	{
		/* Check if originator packet passed through PRE_ROUTING hook */
		iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IIF);
		if (!iif)
		{
			ctEntry->flags |= LOCAL_CONN_ORIG;
			rc = ____cmmCtLocalRegister(fci_handle,ctEntry);
			goto end;
		}


 		cmm_print(DEBUG_INFO,"orig iif is %x\n", iif);

		/* Check if conntrack is between two fpp interfaces */
		iif_programmed = __itf_is_programmed(iif);
		if (iif_programmed <= 0 )
		{
			dir &= ~ORIGINATOR;
			goto replier;
		}
		
		// Is this CT secure ?
#ifndef IPSEC_FLOW_CACHE
		orig_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_ORIG_COMCERTO_FP_XFRM_HANDLE);
		rep_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_REPL_COMCERTO_FP_XFRM_HANDLE);
		
		if ((orig_xfrm_handle || rep_xfrm_handle))
		{
			if (orig_xfrm_handle)
			{
				cmm_print(DEBUG_INFO, "%s(%d) xfrm originator: handles %x, %x, %x, %x\n",
					__FUNCTION__,__LINE__, orig_xfrm_handle[0],orig_xfrm_handle[1],
					orig_xfrm_handle[2],orig_xfrm_handle[3]);
			}
			if (rep_xfrm_handle)
			{
				cmm_print(DEBUG_INFO, "%s(%d) xfrm REPL: handles %x, %x, %x, %x\n",
					__FUNCTION__,__LINE__, rep_xfrm_handle[0],rep_xfrm_handle[1],
					rep_xfrm_handle[2],rep_xfrm_handle[3]);
			}
			ctEntry->flags &= ~FLOW_NO_ORIG_SA;
			ctEntry->flags &= ~FLOW_NO_REPL_SA;
			/* if ctEntry is in flow_no_sa_list, remove it */
			list_del(&ctEntry->flow_no_sa_list_node);

			cmm_ct_fill_ipsec_info(ctEntry, orig_xfrm_handle, rep_xfrm_handle);
			if ((ctEntry->flags & FLOW_NO_ORIG_SA)  ||
				(ctEntry->flags & FLOW_NO_REPL_SA))
			{
				/* if ctEntry has FLOW_NO_SA flag set and 
				   this func not called from cmmUpdateCtEntriesInFlowNoSAList,
				   add it in flow_no_sa_list  */
				list_add(&ct_flow_no_sa_list, &ctEntry->flow_no_sa_list_node);
			}
		}
		cmm_print(DEBUG_INFO, "%s(%d) origfwdSA %p, origoutSA %p, repfwdSA %p, repOutSA %p\n",
			__FUNCTION__,__LINE__,ctEntry->fEntryOrigFwdSA,ctEntry->fEntryOrigOutSA,
			ctEntry->fEntryRepFwdSA, ctEntry->fEntryRepOutSA);
		if ((ctEntry->flags & FLOW_NO_ORIG_SA) == FLOW_NO_ORIG_SA) 
		{
			/* if ctEntry has FLOW_NO_SA flag set and 
			   this func not called from cmmUpdateCtEntriesInFlowNoSAList,
			   add it in flow_no_sa_list  */
			dir &= ~ORIGINATOR;
			goto replier;
		} 
#else
		/* If a packet is SNATed, then only IPSec policies which are based on the SNATed IP addresses are applied
		   on the packet. So  IPSec flows with SNATed IP addresses must be looked up if connection is SNATed */
		// Is this CT secure ?
		/* If a packet is SNATed, then only IPSec policies which are based on the SNATed IP addresses are applied
		   on the packet. So  IPSec flows with SNATed IP addresses must be looked up if connection is SNATed */
		if(memcmp(sAddrOrig, dAddrRepl, IPADDRLEN(ctEntry->family)))
		{
			if (!ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut = __cmmFlowGet(ctEntry->family, dAddrRepl, sAddrRepl, dPortRepl, sPortRepl, proto, FLOW_DIR_OUT);

			if (!ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd = __cmmFlowGet(ctEntry->family, dAddrRepl, sAddrRepl, dPortRepl, sPortRepl, proto, FLOW_DIR_FWD);
		}
		else
		{
			if (!ctEntry->fEntryOrigOut)
				ctEntry->fEntryOrigOut = __cmmFlowGet(ctEntry->family, sAddrOrig, dAddrOrig, sPortOrig, dPortOrig, proto, FLOW_DIR_OUT);

			if (!ctEntry->fEntryOrigFwd)
				ctEntry->fEntryOrigFwd = __cmmFlowGet(ctEntry->family, sAddrOrig, dAddrOrig, sPortOrig, dPortOrig, proto, FLOW_DIR_FWD);
		}

		if ((ctEntry->fEntryOrigOut && (ctEntry->fEntryOrigOut->flags & FLOW_NO_SA) == FLOW_NO_SA) ||
		    (ctEntry->fEntryOrigFwd && (ctEntry->fEntryOrigFwd->flags & FLOW_NO_SA) == FLOW_NO_SA)) 
		{
			dir &= ~ORIGINATOR;
			goto replier;
		} 
#endif /* IPSEC_FLOW_CACHE */
		flow.family = ctEntry->family;
		flow.sAddr = sAddrOrig;
		flow.dAddr = sAddrRepl;
		flow.iifindex = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IFINDEX);
#ifdef VLAN_FILTER
		flow.underlying_vlan_id = nfct_get_attr_u16(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_VID);
#endif
#ifdef LS1043
 		flow.underlying_iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_IIF);
#else
 		flow.underlying_iif = 0;
#endif
		flow.fwmark = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_MARK);
		flow.flow_flags = 0;

#ifdef IPSEC_FLOW_CACHE
		if (ctEntry->fEntryOrigOut && ctEntry->fEntryOrigOut->ignore_neigh)
		{
			SAEntry = __cmmSAFind(ctEntry->fEntryOrigOut->sa_handle[0]);
			if(!SAEntry)
			{
				dir &= ~ORIGINATOR;
				goto replier;
			}
			flow.flow_flags |= FLOWFLAG_IGNORE_NEIGH;
		}
#else
		if (ctEntry->fEntryOrigOutSA)
		{
			SAEntry =  ctEntry->fEntryOrigOutSA;
			flow.flow_flags |= FLOWFLAG_IGNORE_NEIGH;
		}
#endif /* IPSEC_FLOW_CACHE */

		if (__cmmRouteRegister(&ctEntry->orig, &flow, "originator") < 0)
		{
			dir &= ~ORIGINATOR;
			goto replier;
		}

		if(ctEntry->dir_filter & ORIGINATOR) {
			/*check if inbound interface is LAN and outbound interface WLAN and vice-versa if so forward normally*/
			itf = __itf_find(iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IIF));
			route = ctEntry->orig.route;
			out_itf = __itf_find(route->oifindex);
			if( !(__itf_is_wifi(out_itf) && (!is_wan_port_ifindex(iif))) &&
				!((!is_wan_port_ifindex(route->oifindex)) && __itf_is_wifi(itf))) {
				dir &= ~ORIGINATOR;
				goto replier;
			}
		}
		tmp = ctEntry->orig_tunnel.route;

		rc = __cmmCtTunnelRouteRegister(&ctEntry->orig, &ctEntry->orig_tunnel,
								(SAEntry)? &SAEntry->Sa_flow : NULL, "originator tunnel");

		if (ctEntry->orig_tunnel.route && !tmp)
		{
			key = HASH_RT(ctEntry->orig_tunnel.route->family, ctEntry->orig_tunnel.route->sAddr, ctEntry->orig_tunnel.route->dAddr);
			list_add(&ct_table_by_orig_tunnel_route[key], &ctEntry->list_by_orig_tunnel_route);
			ctEntry->flags |= FPP_NEEDS_UPDATE;
		}

		if (rc < 0)
		{
			dir &= ~ORIGINATOR;
			goto replier;
		}
	}

replier:
	if (dir & REPLIER)
	{
		SAEntry = NULL;
		/* Check if replier packet passed through PRE_ROUTING hook */
		rep_iif = nfct_get_attr_u32(ct, ATTR_REPL_COMCERTO_FP_IIF);
		if (!rep_iif)
		{
			ctEntry->flags |= LOCAL_CONN_REPL;
			rc = ____cmmCtLocalRegister(fci_handle,ctEntry);
			goto end;
		}


 		cmm_print(DEBUG_INFO,"repl iif is %x\n", rep_iif);
		/* Check if conntrack is between two fpp interfaces */
		if (!__itf_is_programmed(rep_iif))
		{
			dir &= ~REPLIER;
			goto program;
		}

#ifdef IPSEC_FLOW_CACHE
		// Is this CT secure ?
		/* If a packet is DNATed, then only IPSec policies which are based on the DNATed IP addresses are applied
		   on the packet. So  IPSec flows with DNATed IP addresses must be looked up if connection is DNATed */
		if(memcmp(sAddrRepl, dAddrOrig, IPADDRLEN(ctEntry->family)))
		{
			if (!ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut = __cmmFlowGet(ctEntry->family, dAddrOrig, sAddrOrig, dPortOrig, sPortOrig, proto, FLOW_DIR_OUT);

			if (!ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd = __cmmFlowGet(ctEntry->family, dAddrOrig, sAddrOrig, dPortOrig, sPortOrig, proto, FLOW_DIR_FWD);
		}
		else
		{
			if (!ctEntry->fEntryRepOut)
				ctEntry->fEntryRepOut = __cmmFlowGet(ctEntry->family, sAddrRepl, dAddrRepl, sPortRepl, dPortRepl, proto, FLOW_DIR_OUT);

			if (!ctEntry->fEntryRepFwd)
				ctEntry->fEntryRepFwd = __cmmFlowGet(ctEntry->family, sAddrRepl, dAddrRepl, sPortRepl, dPortRepl, proto, FLOW_DIR_FWD);
		}

		if ((ctEntry->fEntryRepOut && (ctEntry->fEntryRepOut->flags & FLOW_NO_SA) == FLOW_NO_SA) ||
		    (ctEntry->fEntryRepFwd && (ctEntry->fEntryRepFwd->flags & FLOW_NO_SA) == FLOW_NO_SA)) 
		{
			dir &= ~REPLIER;
			goto program;
		}
#else
		if ((ctEntry->flags & FLOW_NO_REPL_SA) == FLOW_NO_REPL_SA)
		{
			dir &= ~REPLIER;
			goto program;
		} 
#endif /* IPSEC_FLOW_CACHE */
		flow.family = ctEntry->family;
		flow.sAddr = sAddrRepl;
		flow.dAddr = sAddrOrig;
		flow.iifindex = nfct_get_attr_u32(ct, ATTR_REPL_COMCERTO_FP_IFINDEX);
#ifdef VLAN_FILTER
		flow.underlying_vlan_id = nfct_get_attr_u16(ct, ATTR_REPL_COMCERTO_FP_UNDERLYING_VID);
#endif
#ifdef LS1043
 		flow.underlying_iif = nfct_get_attr_u32(ct, ATTR_REPL_COMCERTO_FP_UNDERLYING_IIF);
#else
		flow.underlying_iif = 0;
#endif
		flow.fwmark = nfct_get_attr_u32(ct, ATTR_REPL_COMCERTO_FP_MARK);
		flow.flow_flags = 0;

#ifdef IPSEC_FLOW_CACHE
		if (ctEntry->fEntryRepOut && ctEntry->fEntryRepOut->ignore_neigh)
		{
			SAEntry = __cmmSAFind(ctEntry->fEntryRepOut->sa_handle[0]);
			if(!SAEntry)
			{
				dir &= ~REPLIER;
				goto program;
			}
			flow.flow_flags |= FLOWFLAG_IGNORE_NEIGH;
		}
#else
		if (ctEntry->fEntryRepOutSA)
		{
			SAEntry =  ctEntry->fEntryRepOutSA;
			flow.flow_flags |= FLOWFLAG_IGNORE_NEIGH;
		}
#endif /* IPSEC_FLOW_CACHE */

		if (__cmmRouteRegister(&ctEntry->rep, &flow, "replier") < 0)
		{
			dir &= ~REPLIER;
			goto program;
		}

		tmp = ctEntry->rep_tunnel.route;

		rc = __cmmCtTunnelRouteRegister(&ctEntry->rep, &ctEntry->rep_tunnel,
							(SAEntry)? &SAEntry->Sa_flow : NULL, "replier tunnel");

		if (ctEntry->rep_tunnel.route && !tmp)
		{
			key = HASH_RT(ctEntry->rep_tunnel.route->family, ctEntry->rep_tunnel.route->sAddr, ctEntry->rep_tunnel.route->dAddr);
			list_add(&ct_table_by_rep_tunnel_route[key], &ctEntry->list_by_rep_tunnel_route);
			ctEntry->flags |= FPP_NEEDS_UPDATE;
		}

		if (rc < 0)
		{
			dir &= ~REPLIER;
			goto program;
		}
	}

program:
	if (dir & ORIGINATOR)
	{
		rc = cmmFeRouteUpdate(fci_handle, ADD | UPDATE, ctEntry->orig.fpp_route);
		if (rc < 0)
		{
			dir &= ~ORIGINATOR;
			goto program_replier;
		}

		if (ctEntry->orig_tunnel.fpp_route)
		{
			rc = cmmFeRouteUpdate(fci_handle, ADD | UPDATE, ctEntry->orig_tunnel.fpp_route);
			if (rc < 0)
			{
				dir &= ~ORIGINATOR;
				goto program_replier;
			}
		}
	}

program_replier:

	if (dir & REPLIER)
	{
		rc = cmmFeRouteUpdate(fci_handle, ADD | UPDATE, ctEntry->rep.fpp_route);
		if (rc < 0)
		{
			dir &= ~REPLIER;
			goto program_ct;
		}

		if (ctEntry->rep_tunnel.fpp_route)
		{
			rc = cmmFeRouteUpdate(fci_handle, ADD | UPDATE, ctEntry->rep_tunnel.fpp_route);
			if (rc < 0)
			{
				dir &= ~REPLIER;
				goto program_ct;
			}
		}
	}

program_ct:
	if (ctEntry->fpp_dir != dir)
	{
		ctEntry->flags |= FPP_NEEDS_UPDATE;
		ctEntry->fpp_dir = dir;
	}

	__cmmCheckFPPRouteIdUpdate(&ctEntry->orig, &ctEntry->flags);
	__cmmCheckFPPRouteIdUpdate(&ctEntry->orig_tunnel, &ctEntry->flags);
	__cmmCheckFPPRouteIdUpdate(&ctEntry->rep, &ctEntry->flags);
	__cmmCheckFPPRouteIdUpdate(&ctEntry->rep_tunnel, &ctEntry->flags);

	cmm_third_part_update(ctEntry, dir);

	if (dir)
		rc = cmmFeCtUpdate(fci_handle, ADD | UPDATE, ctEntry);
	else
		rc = cmmFeCtUpdate(fci_handle, UPDATE, ctEntry);

end:
	if (rc == 0)
	{
		/* check if permananet is not already set */
		if (!(ctEntry->flags & SENT_PERMANENT_INFO))
		{
			ctTemp = cmmCtClone(ct);
			/* Use of globalConf.nf_conntrack_handle must be protected by ctMutex */
			cmmCtSetPermanent(globalConf.nf_conntrack_handle, ctEntry->flags ,
				ctTemp, ct, 0);
			ctEntry->flags |= SENT_PERMANENT_INFO;
		}
		cmm_print(DEBUG_INFO, "%s: CtAdd Success\n", __func__);
		goto out;
	}

	cmm_print(DEBUG_ERROR, "%s: CtAdd failed\n", __func__);

	return -1;

out:
	return 0;
}

/*****************************************************************
* __cmmCtUpdateWithRoute
*
*
******************************************************************/
void __cmmCtUpdateWithRoute(FCI_CLIENT *fci_handle, struct RtEntry *route)
{
	struct ctTable *ctEntry;
	struct list_head *entry;
	struct fpp_rt *fpp_route;
	int key;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	key = HASH_RT(route->family, route->sAddr, route->dAddr);

	entry = list_first(&ct_table_by_orig_route[key]);
	while (entry != &ct_table_by_orig_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_orig_route);
		entry = list_next(entry);
		if (ctEntry->orig.route == route)
		{
			fpp_route = ctEntry->orig.fpp_route;
			ctEntry->orig.fpp_route = NULL;

			____cmmCtRegister(fci_handle, ctEntry);

			__cmmFPPRouteDeregister(fci_handle, fpp_route, "originator");
		}
	}

	entry = list_first(&ct_table_by_rep_route[key]);
	while (entry != &ct_table_by_rep_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_rep_route);
		entry = list_next(entry);
		if (ctEntry->rep.route == route)
		{
			fpp_route = ctEntry->rep.fpp_route;
			ctEntry->rep.fpp_route = NULL;

			____cmmCtRegister(fci_handle, ctEntry);

			__cmmFPPRouteDeregister(fci_handle, fpp_route, "replier");
		}
	}

	entry = list_first(&ct_table_by_orig_tunnel_route[key]);
	while (entry != &ct_table_by_orig_tunnel_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_orig_tunnel_route);
		entry = list_next(entry);
		if (ctEntry->orig_tunnel.route == route)
		{
			fpp_route = ctEntry->orig_tunnel.fpp_route;
			ctEntry->orig_tunnel.fpp_route = NULL;

			____cmmCtRegister(fci_handle, ctEntry);

			__cmmFPPRouteDeregister(fci_handle, fpp_route, "originator tunnel");
		}
        }

	entry = list_first(&ct_table_by_rep_tunnel_route[key]);
	while (entry != &ct_table_by_rep_tunnel_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_rep_tunnel_route);
                entry = list_next(entry);

		if (ctEntry->rep_tunnel.route == route)
		{
			fpp_route = ctEntry->rep_tunnel.fpp_route;
			ctEntry->rep_tunnel.fpp_route = NULL;

			____cmmCtRegister(fci_handle, ctEntry);

			__cmmFPPRouteDeregister(fci_handle, fpp_route, "replier tunnel");
		}
	}
}

/*****************************************************************
* __cmmCtRegister
*
*
******************************************************************/
static int __cmmCtRegister(FCI_CLIENT *fci_handle, struct nfct_handle *handle, struct nf_conntrack *ct, struct ctTable *ctEntry, int dir)
{
	struct RtEntry *rtEntryOrig = NULL;
	struct RtEntry *rtEntryRep = NULL;
	const unsigned int *sAddrOrig, *sAddrRepl;
	unsigned short sPortOrig, sPortRepl;
	int family;
	unsigned char proto;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	family = nfct_get_attr_u8(ct, ATTR_ORIG_L3PROTO);
	proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);

	if (family == AF_INET)
	{
		sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
	}
	else
	{
		sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
		sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);
	}

	sPortOrig = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
	sPortRepl = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);

	__pthread_mutex_lock(&rtMutex);

	if (!ctEntry)
	{
		if (!cmmFcIsConntrackAllowed(fci_handle, ct, &rtEntryOrig))
		{
			cmm_print(DEBUG_INFO, "%s: conntrack not allowed\n", __func__);
			goto fail0;
		}

		if (family == AF_INET)
		{
			struct RtEntry *rtEntryPolicy;

			rtEntryPolicy = cmmPolicyRouting(sAddrOrig[0], sAddrRepl[0], proto, sPortOrig, sPortRepl);
			if (rtEntryPolicy)
			{
				if (rtEntryOrig)
					____cmmRouteDeregister(rtEntryOrig, "originator");

				rtEntryOrig = rtEntryPolicy;
			}

			rtEntryPolicy = cmmPolicyRouting(sAddrRepl[0], sAddrOrig[0], proto, sPortRepl, sPortOrig);
			if (rtEntryPolicy)
			{
#if 0
				if (rtEntryRep)
					____cmmRouteDeregister(rtEntryRep, "replier");
#endif
				rtEntryRep = rtEntryPolicy;
			}
		}

		ctEntry = __cmmCtAdd(ct);
		if (!ctEntry)
		{
			cmm_print(DEBUG_ERROR, "%s: conntrack add failed\n", __func__);
			goto fail0;
		}

		ct_stats.created++;

		if (globalConf.asymff_enable) {
			if(cmmFcIsConntrackAsymFastForwarded(ct))
				ctEntry->dir_filter = ORIGINATOR;
		}

		if (rtEntryOrig)
			ctEntry->orig.route = rtEntryOrig;

		if (rtEntryRep)
			ctEntry->rep.route = rtEntryRep;

	}

	ctEntry->dir = dir;

	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	____cmmCtRegister(fci_handle, ctEntry);

#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);

	/* since we keep the conntrack in the local cache don't return NFCT_CB_CONTINUE */
	return NFCT_CB_STOLEN;

fail0:
	if (rtEntryOrig)
		____cmmRouteDeregister(rtEntryOrig, "originator");

	if (rtEntryRep)
		____cmmRouteDeregister(rtEntryRep, "replier");

	__pthread_mutex_unlock(&rtMutex);

	return NFCT_CB_CONTINUE;
}

/*****************************************************************
* __cmmCtUpdate
*
*
******************************************************************/
static void __cmmCtUpdate(struct nf_conntrack *ct, struct nfct_handle *handle, struct ctTable *ctEntry)
{
	cmm_print(DEBUG_INFO, "%s\n", __func__);

	nfct_destroy(ctEntry->ct);
	ctEntry->ct = ct;
}

/*****************************************************************
* __cmmFPPRouteDeregister
*
*
******************************************************************/
void __cmmFPPRouteDeregister(FCI_CLIENT *fci_handle, struct fpp_rt *fpp_route, const char *dir)
{
	int rc = 0;

	if (!fpp_route)
		return;

	cmm_print(DEBUG_INFO, "%s: removing %s route entry\n", __func__, dir);

	if (fpp_route->count == 1)
	{
		rc = cmmFeRouteUpdate(fci_handle, REMOVE, fpp_route);
	}

	/* In case of a deregister error don't free the route entry, we still need to track the fpp state */
	if (rc < 0)
		fpp_route->count--;
	else
		__cmmFPPRoutePut(fpp_route);
}

/*****************************************************************
* ____cmmRouteDeregister
*
*
******************************************************************/
void ____cmmRouteDeregister(struct RtEntry *route, const char *dir)
{
	cmm_print(DEBUG_INFO, "%s: removing %s route entry\n", __func__, dir);

	if (route->count == 1)
	{
		//Try to remove arp entries
		if (route->neighEntry)
		{
			__cmmNeighPut(route->neighEntry);
			route->neighEntry = NULL;
		}
		else
		{
			cmm_print(DEBUG_WARNING, "%s: %s ARP/Neighbor entry not found\n", __func__, dir);
		}
	}

	__cmmRoutePut(route);
}


/*****************************************************************
* __cmmRouteDeregister
*
*
******************************************************************/
void __cmmRouteDeregister(FCI_CLIENT *fci_handle, struct ct_route *rt, const char *dir)
{
	if (rt->fpp_route)
	{
		__cmmFPPRouteDeregister(fci_handle, rt->fpp_route, dir);
		rt->fpp_route = NULL;
	}

	if (rt->route)
	{
		____cmmRouteDeregister(rt->route, dir);
		rt->route = NULL;
	}
	else
	{
		cmm_print(DEBUG_WARNING, "%s: %s route entry not found\n", __func__, dir);
	}
}

/*****************************************************************
* ____cmmCtLocalDeregister
*
*
******************************************************************/
int  ____cmmCtLocalDeregister(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct ctTable *ctEntry)
{
	/* As the local route is attached to ctEntry for local connections */
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);

	__cmmRouteDeregister(fci_handle, &ctEntry->orig, "originator");
	__cmmRouteDeregister(fci_handle, &ctEntry->rep, "replier");

	lro_socket_close(fci_handle, fci_key_handle, ctEntry);

	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);


	return 0;
}

/*****************************************************************
* ____cmmCtDeregister
*
*
******************************************************************/
int ____cmmCtDeregister(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct ctTable *ctEntry)
{
	int rc;

	if (ctEntry->flags & LOCAL_CONN)
	{
		rc = ____cmmCtLocalDeregister(fci_handle,fci_key_handle,ctEntry);
		goto ct_remove;
	}

	rc = cmmFeCtUpdate(fci_handle, REMOVE, ctEntry);

	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);

	//Try to remove route entries
	__cmmRouteDeregister(fci_handle, &ctEntry->rep, "replier");
	__cmmRouteDeregister(fci_handle, &ctEntry->orig, "originator");

	if (ctEntry->rep_tunnel.route)
	{
		__cmmRouteDeregister(fci_handle, &ctEntry->rep_tunnel, "replier tunnel");
		list_del(&ctEntry->list_by_rep_tunnel_route);
	}

	if (ctEntry->orig_tunnel.route)
	{
		__cmmRouteDeregister(fci_handle, &ctEntry->orig_tunnel, "originator tunnel");
		list_del(&ctEntry->list_by_orig_tunnel_route);
	}

	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);

#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);

	// Flow cache entry remove
	if (ctEntry->fEntryOrigOut)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, ctEntry->fEntryOrigOut))
		{
			__cmmFlowPut(ctEntry->fEntryOrigOut);

			ctEntry->fEntryOrigOut = NULL;
		}

	if (ctEntry->fEntryOrigFwd)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, ctEntry->fEntryOrigFwd))
		{
			__cmmFlowPut(ctEntry->fEntryOrigFwd);

			ctEntry->fEntryOrigFwd = NULL;
		}

	if (ctEntry->fEntryRepOut)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, ctEntry->fEntryRepOut))
		{
			__cmmFlowPut(ctEntry->fEntryRepOut);

			ctEntry->fEntryRepOut = NULL;
		}

	if (ctEntry->fEntryRepFwd)
		if (!cmmFlowKeyEngineRemove(fci_key_handle, ctEntry->fEntryRepFwd))
		{
			__cmmFlowPut(ctEntry->fEntryRepFwd);

			ctEntry->fEntryRepFwd = NULL;
		}
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

ct_remove:
	if (!rc)
		__cmmCtRemove(ctEntry);
	else
		cmm_print(DEBUG_ERROR, "%s: DeRegister failed\n", __func__);

	return rc;
}

/*****************************************************************
* __cmmCtDeregister
*
*
******************************************************************/
static void __cmmCtDeregister(struct cmm_ct *ctx, struct nf_conntrack *ct)
{
	struct ctTable *ctEntry;
	u_int32_t id1, id2;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	ctEntry = __cmmCtFind(ct);
	if (!ctEntry) {
		cmm_print(DEBUG_INFO, "%s: conntrack entry not found\n", __func__);
		goto out;
	}

	/* Make sure we're destroying the correct ctEntry (bz46186) */
	id1 = nfct_get_attr_u32(ct, ATTR_ID);
	id2 = nfct_get_attr_u32(ctEntry->ct, ATTR_ID);

	if (id1 != 0 && id2 != 0 && id1 != id2)
	{
		cmm_print(DEBUG_WARNING, "%s: ID mismatch (%d, %d)\n", __func__, id1, id2);
		goto out;
	}
	ct_stats.destroyed++;
	____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry);

out:
	return;
}

static const char *conntrack_event_type(enum nf_conntrack_msg_type type)
{
	if (type & NFCT_T_UPDATE)
		return "UPDATE";

	if (type & NFCT_T_DESTROY)
		return "DESTROY";

	if (type & NFCT_T_NEW)
		return "NEW";

	return "unsupported type";
}

static const char *conntrack_status(u_int32_t status)
{
	if (status & IPS_ASSURED)
		return "ASSURED";

	if ((status & (IPS_CONFIRMED | IPS_SEEN_REPLY)) == IPS_CONFIRMED)
		return "CONFIRMED";

	if ((status & (IPS_CONFIRMED | IPS_SEEN_REPLY)) == (IPS_CONFIRMED | IPS_SEEN_REPLY))
		return "CONFIRMED/SEEN_REPLY";

	return "unsupported status";
}

static const char *conntrack_tcp_state(u_int8_t state)
{
	static const char *tcp_state[] = {
		[TCP_CONNTRACK_NONE] = "NONE",
		[TCP_CONNTRACK_SYN_SENT] = "SYN_SENT",
		[TCP_CONNTRACK_SYN_RECV] = "SYN_RECV",
		[TCP_CONNTRACK_ESTABLISHED] = "ESTABLISHED",
		[TCP_CONNTRACK_FIN_WAIT] = "FIN_WAIT",
		[TCP_CONNTRACK_CLOSE_WAIT] = "CLOSE_WAIT",
		[TCP_CONNTRACK_LAST_ACK] = "LAST_ACK",
		[TCP_CONNTRACK_TIME_WAIT] = "TIME_WAIT",
		[TCP_CONNTRACK_CLOSE] = "CLOSE",
		[TCP_CONNTRACK_LISTEN] = "LISTEN",
		[TCP_CONNTRACK_SYN_SENT2] = "SYN_SENT2",
		[TCP_CONNTRACK_MAX] = "MAX"
	};

	if (state >= TCP_CONNTRACK_MAX)
		return "unknown state";

	return tcp_state[state];
}

static int cmmCtCheckCtCb(enum nf_conntrack_msg_type type,
	      struct nf_conntrack *ct,
	      void *data)
{
	struct nf_conntrack *ct_local = (struct nf_conntrack *) cb_data;

	cb_status = 0;

	if((nfct_get_attr_u32(ct, ATTR_ID) != nfct_get_attr_u32(ct_local, ATTR_ID))
	|| __cmmCtIsInv(ct, ct_local))
		cb_status = -1;

	return NFCT_CB_CONTINUE;
}

static int cmmCheckUpdateEvent(struct nfct_handle *handle, struct nf_conntrack *ct)
{
	int rc = 0;

	cb_data = ct;

	if (nfct_query(handle, NFCT_Q_GET, (void*)ct) < 0) {
		if (errno == ENOENT) {
			rc = -1;
			goto exit;
		}
		else
			cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_GET) %s %d\n", __func__, strerror(errno), errno);
	}
	else if (cb_status < 0)
		rc = -1;

exit:
	return rc;
}

static int cmmCheckEvent(struct cmm_ct *ctx, struct nf_conntrack *ct, struct ctTable **ctEntry, enum nf_conntrack_msg_type type, u_int32_t id)
{
	/* First pass on entries not already tracked in CMM */
	switch (type) {
		case NFCT_T_UPDATE:
			/* Racy update event, need to check if entry
			 * does really exist
			 */
			if (!*ctEntry || !__cmmCtFindId(*ctEntry, id))
				if (cmmCheckUpdateEvent(ctx->get_handle, ct) < 0)
					return -1;

			break;

		case NFCT_T_DESTROY:
			if (!*ctEntry)
				return -1;

			break;

		default:
			break;
	}

	/* Second pass on tracked entries */
	if (*ctEntry)
		switch (type) {
			case NFCT_T_NEW:
				/* Do not process a new event
				 * which arrives after an update
				 */
				if (*ctEntry && __cmmCtFindId(*ctEntry, id))
					return -1;

			case NFCT_T_UPDATE:
				if (!__cmmCtFindId(*ctEntry, id))
					__cmmCtAddId(*ctEntry, id);

				/* Do not process multiple ids entries */
				if ((*ctEntry)->n_id > 1)
					return -1;

				break;

			case NFCT_T_DESTROY:
				/* If after __cmmCtDelId
				 * n_id > 1 : keep the conntrack but do not unregister it, need to wait.
				 *            Still in the middle of an out-of-sync period
				 * n_id = 1 : coming back from an out-of-sync period, ask kernel for an update
				 *            but still destroy the current entry as its state is not in sync anymore
				 * n_id = 0 : regular case, deregister
				 */
				if (!__cmmCtDelId(*ctEntry, id)) {
					if ((*ctEntry)->n_id > 1) {
						return -1;
					}
					else if (((*ctEntry)->n_id) == 1)
						cmmCtForceUpdate(ctx->handle, *ctEntry);
				}
				else
					return -1;

				break;

			default:
				break;
		}
	/* Success, event will be processed */
	return 0;
}

/*****************************************************************
* __cmmCtCatch()
*
* 	This function is called by libnetfilter_conntrack library
* 	when an event occurs on conntrack table
*
******************************************************************/
static int __cmmCtCatch(struct cmm_ct *ctx, enum nf_conntrack_msg_type type, struct nf_conntrack *ct)
{
	u_int8_t l4proto;
	u_int8_t l3proto;
	u_int8_t state;
	int rc = NFCT_CB_CONTINUE;
	u_int32_t status;
	struct ctTable *ctEntry = NULL;
	u_int32_t id, ctFlags = 0;
	struct nf_conntrack *ctTemp = NULL;

	// If Forward Engine programmation is forbidden, don't do anything
	if (globalConf.enable == 0)
		goto exit;

#ifdef C2000_DPI
	status = nfct_get_attr_u32(ct, ATTR_STATUS);
	if ((globalConf.dpi_enable) && ((status & IPS_DPI_ALLOWED) != IPS_DPI_ALLOWED))
	{
		goto exit;
	}
#endif

	l3proto = nfct_get_attr_u8(ct, ATTR_ORIG_L3PROTO);

	// Optimization: If srcAddr and dstAddr are both 127.0.0.1, get out right away.
#define LOCAL_ADDR	htonl(0x7f000001)
	if (l3proto == AF_INET)
	{
		__u32 *dAddr, *sAddr;
		sAddr = (__u32 *)nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		dAddr = (__u32 *)nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
		if (sAddr && *sAddr == LOCAL_ADDR && dAddr && *dAddr == LOCAL_ADDR)
		{
			//cmm_print(DEBUG_INFO, "%s: Ignoring local connection\n", __func__);
			goto exit;
		}
	}

	if ((l3proto != AF_INET) && (l3proto != AF_INET6))
	{
		cmm_print(DEBUG_INFO, "%s: unsupported L3 Proto: %#x\n", __func__, l3proto);
		goto exit;
	}

	// In case of TCP we should check that the connection is established
	l4proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);
	id = nfct_get_attr_u32(ct, ATTR_ID);

	if (!id)
		cmm_print(DEBUG_ERROR, "%s: Conntrack event with NULL id\n", __func__);
	
	if( (l4proto == IPPROTO_UDP) || SUPPORTED_LOCAL_PROTO(l4proto))
	{
		ctEntry = __cmmCtFind(ct);

		if (cmmCheckEvent(ctx, ct, &ctEntry, type, id) < 0)
			goto exit;

		switch (type) {
		case NFCT_T_DESTROY:
			cmm_print(DEBUG_INFO, "%s: proto %d connection %s(%#x)\n", __func__, l4proto, conntrack_event_type(type), type);

			if (ctEntry) {
				ct_stats.destroyed++;
				____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry);
			}
			break;

		case NFCT_T_NEW:
		case NFCT_T_UPDATE:

			status = nfct_get_attr_u32(ct, ATTR_STATUS);

			cmm_print(DEBUG_INFO, "%s: proto %d connection %s(%#x) %s(%#x)\n", __func__,
					l4proto, conntrack_event_type(type), type,
					conntrack_status(status), status);

			if (ctEntry) {
				ctTemp = cmmCtClone(ct);
				__cmmCtUpdate(ct, ctx->handle, ctEntry);
						
				cmmCtSetPermanent(ctx->handle, ctEntry->flags , ctTemp, ctEntry->ct, 0);
				rc = NFCT_CB_STOLEN;
			}


			if (l4proto == IPPROTO_UDP) {
				if (status & IPS_ASSURED) {
					rc = __cmmCtRegister(ctx->fci_handle, ctx->handle, ct, ctEntry, ORIGINATOR | REPLIER);
				}
				else if (status & IPS_CONFIRMED) {
					rc = __cmmCtRegister(ctx->fci_handle, ctx->handle, ct, ctEntry, ORIGINATOR);
				}
				else {
					goto exit;
				}
			}
			else if (SUPPORTED_LOCAL_PROTO(l4proto))
			{
				if ((status & (IPS_CONFIRMED | IPS_SEEN_REPLY)) == IPS_CONFIRMED) {
					rc = __cmmCtRegister(ctx->fci_handle, ctx->handle, ct, ctEntry, ORIGINATOR);
				}
				/* Actually IPIP connections will never reach ASSURED state, so both directions are
				 * programmed when packets have been seen in opposite direction */
				else if ((status & (IPS_CONFIRMED | IPS_SEEN_REPLY)) == (IPS_CONFIRMED | IPS_SEEN_REPLY)) {
					rc = __cmmCtRegister(ctx->fci_handle, ctx->handle, ct, ctEntry, ORIGINATOR | REPLIER);
				}
				else {
					goto exit;
				}
			}

			break;

		default:
			cmm_print(DEBUG_INFO, "%s: proto %d connection %s(%#x)\n", __func__,
					l4proto, conntrack_event_type(type), type);

			break;
		}
	}
	else if (l4proto == IPPROTO_TCP)
	{
		switch (type) {
		case NFCT_T_DESTROY:
			cmm_print(DEBUG_INFO, "%s: TCP connection %s(%#x)\n", __func__, conntrack_event_type(type), type);
			__cmmCtDeregister(ctx, ct);
			break;

		case NFCT_T_NEW:
		case NFCT_T_UPDATE:
			status = nfct_get_attr_u32(ct, ATTR_STATUS);

			ctEntry = __cmmCtFind(ct);

			if (ctEntry) {
				ctTemp = cmmCtClone(ct);
				__cmmCtUpdate(ct, ctx->handle, ctEntry);
				cmmCtSetPermanent(ctx->handle, ctEntry->flags , ctTemp, ctEntry->ct, 0);
				rc = NFCT_CB_STOLEN;
			}

			if (nfct_attr_is_set(ct, ATTR_TCP_STATE))
			{
				state = nfct_get_attr_u8(ct, ATTR_TCP_STATE);

				cmm_print(DEBUG_INFO, "%s: TCP connection %s(%#x) %s(%#x) %s(%#x)\n", __func__,
							conntrack_event_type(type), type,
							conntrack_status(status), status,
							conntrack_tcp_state(state), state);

				if ((state == TCP_CONNTRACK_ESTABLISHED) && (status & IPS_ASSURED))
					rc = __cmmCtRegister(ctx->fci_handle, ctx->handle, ct, ctEntry, ORIGINATOR | REPLIER);
				else
					if (ctEntry)
					{
						ct_stats.destroyed++;
						ctFlags = ctEntry->flags;
						if(!ctTemp)
							ctTemp = cmmCtClone(ct);
						if(!____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry))
							cmmCtSetPermanent(ctx->handle, ctFlags, ctTemp, NULL, 1);
					}
			}
			else
			{
				cmm_print(DEBUG_INFO, "%s: TCP connection %s(%#x) %s(%#x) missing state attribute\n", __func__,
							conntrack_event_type(type), type,
							conntrack_status(status), status);
				goto exit;
			}

			break;

		default:
			cmm_print(DEBUG_INFO, "%s: TCP connection %s(%#x)\n", __func__,
				conntrack_event_type(type), type);

			break;
		}
	}

exit:
	return rc;
}

static int count = 0;

/*****************************************************************
* cmmCtCatch()
*
* 	This function is called by libnetfilter_conntrack library
* 	when an event occurs on conntrack table
*
******************************************************************/
static int cmmCtCatch(enum nf_conntrack_msg_type type, struct nf_conntrack *ct, void *data)
{
	struct cmm_ct *ctx = data;
	int rc;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);

	rc = __cmmCtCatch(ctx, type, ct);

	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	count++;

	return rc;
}


/*****************************************************************
* file_write
*
*
******************************************************************/
static int file_write(const char *filename, const void *buf, int size)
{
	int fd;

	if ((fd = open(filename, O_WRONLY)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: open(%s) failed, %s\n", __func__, filename, strerror(errno));
		goto err0;
	}

	if (write(fd, buf, size) != size)
	{
		cmm_print(DEBUG_ERROR, "%s: write() failed, %s\n", __func__, strerror(errno));
		goto err1;
	}

	close(fd);

	return 0;

err1:
	close(fd);

err0:
	return -1;
}

/*****************************************************************
* cmmCtKernelModuleInit
*
*
******************************************************************/
static int cmmCtKernelModuleInit()
{
	cmm_print(DEBUG_INFO, "%s:\n", __func__);

	return file_write(TCP_BE_LIBERAL_PATH, "1", 1);
}

/*****************************************************************
* cmmCtKernelModuleUnInit
*
*
******************************************************************/
static void cmmCtKernelModuleUnInit()
{
	cmm_print(DEBUG_INFO, "%s:\n", __func__);

	file_write(TCP_BE_LIBERAL_PATH, "0", 1);
}
#ifdef APP_SOLICIT
/*****************************************************************
* cmmNeighborKernelModuleInit
* 
*
******************************************************************/
static int cmmRtnlKernelModuleInit()
{
	cmm_print(DEBUG_INFO, "%s:\n", __func__);

	/*Prepare the neighbor code to be in a good configuration*/
	if (file_write(APP_SOLICIT_IPV4_PATH, "1", 1) < 0)
	{
		goto err0;
	}

	if (file_write(APP_SOLICIT_IPV4_WAN_PATH, "1", 1) < 0)
	{
		goto err1;
	}

	if (file_write(APP_SOLICIT_IPV4_LAN_PATH, "1", 1) < 0)
	{
		goto err2;
	}

	if (file_write(APP_SOLICIT_IPV6_PATH, "1", 1) < 0)
	{
		goto err3;
	}

	if (file_write(APP_SOLICIT_IPV6_WAN_PATH, "1", 1) < 0)
	{
		goto err4;
	}

	if (file_write(APP_SOLICIT_IPV6_LAN_PATH, "1", 1) < 0)
	{
		goto err5;
	}

	return 0;

err5:
	file_write(APP_SOLICIT_IPV6_WAN_PATH, "0", 1);

err4:
	file_write(APP_SOLICIT_IPV6_PATH, "0", 1);

err3:
	file_write(APP_SOLICIT_IPV4_LAN_PATH, "0", 1);

err2:
	file_write(APP_SOLICIT_IPV4_WAN_PATH, "0", 1);

err1:
	file_write(APP_SOLICIT_IPV4_PATH, "0", 1);

err0:
	return -1;
}

/*****************************************************************
* cmmNeighborKernelModuleUnInit
* 
*
******************************************************************/
static void cmmRtnlKernelModuleUnInit()
{
	cmm_print(DEBUG_INFO, "%s:\n", __func__);

	file_write(APP_SOLICIT_IPV4_PATH, "0", 1);

	file_write(APP_SOLICIT_IPV4_WAN_PATH, "0", 1);

	file_write(APP_SOLICIT_IPV4_LAN_PATH, "0", 1);

	file_write(APP_SOLICIT_IPV6_PATH, "0", 1);

	file_write(APP_SOLICIT_IPV6_WAN_PATH, "0", 1);

	file_write(APP_SOLICIT_IPV6_LAN_PATH, "0", 1);
}
#endif




/*****************************************************************
* cmmCtResync
*
*
******************************************************************/
static int cmmCtResync(struct cmm_ct *ctx)
{
	struct ctTable *ctEntry;
	struct list_head *entry, *next;
	int family;
	int i;
	int queried = 0;
	int destroyed = 0;
	int len, read = 0;
	int mult_ids_dest = 0;
	int entry_exist = 0;
	unsigned char *buf = resync_buf;

	cmm_print(DEBUG_ERROR, "%s: start %d\n", __func__, ct_stats.current);

	/* Flush all state events in the netlink socket */
	while (read < 2 * NFNL_SOCK_SIZE)
	{
		len = nfnl_recv(nfct_nfnlh(ctx->catch_handle), buf, CMM_MAX_64K_BUFF_SIZE);
		if (len <= 0)
		{
			/* Buffer is empty, exit */
			if (errno == EAGAIN)
				break;

			cmm_print(DEBUG_ERROR, "%s: nfnl_recv() %s\n", __func__, strerror(errno));

			/* Lost events again, try to re-resync later */
			if (errno == ENOBUFS)
				goto err;

			continue;
		}

		read += len;
	}

	cmm_print(DEBUG_ERROR, "%s: flushed %d bytes\n", __func__, read);

	__pthread_mutex_lock(&ctMutex);


	/* Go through all connections and determine which no longer exist in the kernel, then remove them */
	for (i = 0; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&ct_table[i]); next = list_next(entry), entry != &ct_table[i]; entry = next)
		{
			ctEntry = container_of(entry, struct ctTable, list);

			queried++;

			if (ctEntry->n_id > 1) {
				mult_ids_dest++;
				goto destroy;
			}

			cb_data = ctEntry->ct;

			if (nfct_query(ctx->get_handle, NFCT_Q_GET, (void*)ctEntry->ct) < 0) {
				if (errno != ENOENT)
					cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_GET) %s\n", __func__, strerror(errno));
				else
					goto destroy;

			}
			else if (cb_status < 0) {
destroy:
				destroyed++;
				ct_stats.destroyed++;
				____cmmCtDeregister(ctx->fci_handle, ctx->fci_key_handle, ctEntry);
			}
		}

	}

	cmm_print(DEBUG_ERROR, "%s: end %d %d %d %d %d\n", __func__, ct_stats.current, queried, destroyed, mult_ids_dest, entry_exist);

	__pthread_mutex_unlock(&ctMutex);

	/* Now dump all existing conntracks */
	family = AF_UNSPEC;
	if (nfct_query(ctx->catch_handle, NFCT_Q_DUMP, (void *)&family) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_DUMP) %s\n", __func__, strerror(errno));
		goto err;
	}

	return 0;

err:
	cmm_print(DEBUG_ERROR, "%s: end %d %d %d\n", __func__, ct_stats.current, queried, destroyed);
	return -1;
}

sig_atomic_t timer_expired = 0;

static void sig_alarm_hdlr(int signum)
{
	timer_expired = 1;
}

/*****************************************************************
* cmmCtThread
*
* Function that initializes Conntrack event catching
*
******************************************************************/
static void *cmmCtThread(void *data)
{
	struct cmm_ct *ctx = data;
	fd_set set;
	int fd_ct, fd_fci;
#if !defined(IPSEC_SUPPORT_DISABLED)
	int fd_key;
#endif
	int fd_neigh, fd_link, fd_ifaddr, fd_route, fd_rule, fd_abm;
	struct timeval timeout;
	struct itimerval itimer;
	struct sigaction action;
	int need_resync = 0;
	int rc;

	cmm_print(DEBUG_INFO, "%s: pid %d\n", __func__, getpid());

#if !defined(IPSEC_SUPPORT_DISABLED)
	fd_key = fci_fd(ctx->fci_key_catch_handle);
#endif
	fd_fci = fci_fd(ctx->fci_catch_handle);
	fd_ct = nfct_fd(ctx->catch_handle);

	/* Dump all conntracks */
	cmmCtResync(ctx);

	action.sa_handler = sig_alarm_hdlr;
	sigemptyset(&action.sa_mask);
	action.sa_flags = 0;

	if (sigaction(SIGALRM, &action, NULL) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: sigaction() failed %s\n", __func__, strerror(errno));
		goto out;
	}

	/* Set periodic timer, every one second */
	itimer.it_value.tv_sec = 1;
	itimer.it_value.tv_usec = 0;
	itimer.it_interval.tv_sec = 1;
	itimer.it_interval.tv_usec = 0;

	if (setitimer(ITIMER_REAL, &itimer, NULL) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: setitimer() failed %s\n", __func__, strerror(errno));
		goto out;
	}

	fd_neigh = cmm_rtnl_fd(&ctx->rth_neigh);
	fd_link = cmm_rtnl_fd(&ctx->rth_link);
	fd_ifaddr = cmm_rtnl_fd(&ctx->rth_ifaddr);
	fd_route = cmm_rtnl_fd(&ctx->rth_route);
	fd_rule = cmm_rtnl_fd(&ctx->rth_rule);
	fd_abm= cmm_rtnl_fd(&ctx->rth_abm);

	while (1)
	{
		if (timer_expired)
		{
#if PPPOE_AUTO_ENABLE
                        cmmPPPoEAutoKeepAlive();
#endif

			cmmDPDIPsecSAUpdate(ctx);

			/* Resync if needed and system is idle */
			if (need_resync && !count)
			{
				if (!cmmCtResync(ctx))
					need_resync = 0;
			}

			cmmNeighSendSolicit();

			count = 0;
			timer_expired = 0;
		}

		FD_ZERO (&set);
		FD_SET (fd_ct, &set);
#if !defined(IPSEC_SUPPORT_DISABLED)
		FD_SET (fd_key, &set);
#endif
		FD_SET (fd_fci, &set);
		FD_SET (fd_neigh, &set);
		FD_SET (fd_link, &set);
		FD_SET (fd_ifaddr, &set);
		FD_SET (fd_route, &set);
		FD_SET (fd_rule, &set);

		if(globalConf.auto_bridge)
			FD_SET (fd_abm, &set);

		timeout.tv_sec = 1;
		timeout.tv_usec = 0;

		rc = select(FD_SETSIZE, &set, NULL, NULL, &timeout);
		if (rc < 0)
		{
			if (errno == EINTR)
				continue;

			cmm_print(DEBUG_ERROR, "%s: select() failed %s\n", __func__, strerror(errno));

			goto out;
		}

#if !defined(IPSEC_SUPPORT_DISABLED)
		if (FD_ISSET(fd_key, &set))
		{
			rc = fci_catch(ctx->fci_key_catch_handle);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: fci_catch() failed %s\n", __func__, strerror(errno));
				}
			}
		}
#endif
		if (FD_ISSET(fd_ct, &set))
		{
			rc = nfct_catch(ctx->catch_handle);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: nfct_catch() %s\n", __func__, strerror(errno));

					if (errno == ENOBUFS)
					{
						/* At this point we lost some conntrack events,
						   try to resync later when system is idle */
						need_resync = 1;
					}
				}
			}
		}

		if (FD_ISSET(fd_fci, &set))
		{
			rc = fci_catch(ctx->fci_catch_handle);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: fci_catch() failed %s\n", __func__, strerror(errno));
				}
			}
		}

		if (FD_ISSET(fd_neigh, &set))
		{
			rc = cmm_rtnl_listen(&ctx->rth_neigh, cmmRtnlNeigh, ctx);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));

					if (errno == ENOBUFS)
					{
						/* If the above function exits, there was some error, try to dump all ARP/Neighbor entries in case an event was lost */
						cmm_rtnl_neigh_dump_request(&ctx->rth_neigh, AF_INET);

						cmm_rtnl_neigh_dump_request(&ctx->rth_neigh, AF_INET6);
					}

				}
			}
		}

		if (FD_ISSET(fd_link, &set))
		{
			rc = cmm_rtnl_listen(&ctx->rth_link, cmmRtnlLink, &itf_table);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));
				}
			}
		}

		if (FD_ISSET(fd_ifaddr, &set))
		{
			rc = cmm_rtnl_listen(&ctx->rth_ifaddr, cmmRtnlIfAddr, &itf_table);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));
				}
			}
		}

		if (FD_ISSET(fd_route, &set))
		{
			rc = cmm_rtnl_listen(&ctx->rth_route, cmmRtnlRoute, ctx);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));
				}
			}
		}

		if (FD_ISSET(fd_rule, &set))
		{
			rc = cmm_rtnl_listen(&ctx->rth_rule, cmmRtnlRule, ctx);
			if (rc < 0)
			{
				if (errno != EAGAIN)
				{
					cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));
				}
			}
		}
		if(globalConf.auto_bridge){
			if (FD_ISSET(fd_abm, &set))
			{
				rc = cmm_rtnl_listen(&ctx->rth_abm, cmm_l2flow_netlink_rcv, ctx);
				if (rc < 0)
				{
					if (errno != EAGAIN)
					{
						cmm_print(DEBUG_ERROR, "%s: cmm_rtnl_listen() failed %s\n", __func__, strerror(errno));
					}
				}
			}
		}



	}

out:
	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	kill (0, SIGTERM);
	pthread_exit(NULL);

	return NULL;
}

int cmmCtInit(struct cmm_ct *ctx)
{
	int fd;
	int i;
	int size;
	socklen_t socklen = sizeof(size);

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	p_nfconn_clone = nfct_new();
	if (!p_nfconn_clone)
	{
		goto err0;
	}

	p_nfconn_update = nfct_new();
	if (!p_nfconn_update)
	{
		goto err0;
	}

	for (i = 0; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
		list_head_init(&ct_table[i]);
		list_head_init(&ct_table_by_rep[i]);
	}

#ifdef IPSEC_FLOW_CACHE
	for (i = 0; i < FLOW_HASH_TABLE_SIZE; i++)
		list_head_init(&flow_table[i]);
#else
	list_head_init(&ct_flow_no_sa_list);
#endif /* IPSEC_FLOW_CACHE */

	for (i = 0; i < 2 * NEIGHBOR_HASH_TABLE_SIZE; i++)
	{
		list_head_init(&rt_table_by_gw_ip[i]);
		list_head_init(&neigh_table[i]);
	}

	list_head_init(&neigh_state_table);

	for (i = 0; i < NEIGHBOR_HASH_TABLE_SIZE; i++)
		list_head_init(&neigh_table_by_mac[i]);

	for (i = 0; i < ROUTE_HASH_TABLE_SIZE; i++)
		list_head_init(&fpp_rt_table[i]);

	for (i = 0; i < 2 * ROUTE_HASH_TABLE_SIZE; i++)
	{
		list_head_init(&rt_table[i]);
		list_head_init(&ct_table_by_orig_route[i]);
		list_head_init(&ct_table_by_rep_route[i]);
		list_head_init(&ct_table_by_orig_tunnel_route[i]);
		list_head_init(&ct_table_by_rep_tunnel_route[i]);
	}

	for (i = 0; i < HASH_SOCKET_SIZE ; i++) {
		list_head_init(&socket_table[i]);
		list_head_init(&socket_table_by_addr[i]);
	}

	for (i = 0; i < MC_NUM_HASH_ENTRIES ; i++)
		list_head_init(&mc_table[i]);

	for (i = 0; i < L2FLOW_HASH_TABLE_SIZE; i++)
		list_head_init(&l2flow_table[i]);

	for (i = 0; i < SA_HASH_TABLE_SIZE; i++)
		list_head_init(&sa_table[i]);

	memset(&ct_stats, 0, sizeof(struct conntrack_stats));

	if (cmmCtKernelModuleInit() < 0)
	{
		goto err0;
	}
#ifdef APP_SOLICIT
	if (cmmRtnlKernelModuleInit() < 0)
	{
		goto err1;
	}
#endif
	ctx->fci_catch_handle = fci_open(FCILIB_FF_TYPE, NL_FF_GROUP);
	if (!ctx->fci_catch_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d fci_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err2;
	}

	ctx->fci_handle = fci_open(FCILIB_FF_TYPE, 0);
	if (!ctx->fci_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d fci_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err3;
	}

#if !defined(IPSEC_SUPPORT_DISABLED)
	ctx->fci_key_catch_handle = fci_open(FCILIB_KEY_TYPE, NL_KEY_ALL_GROUP);
	if (!ctx->fci_key_catch_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d fci_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err4;
	}

	ctx->fci_key_handle = fci_open(FCILIB_KEY_TYPE, 0);
	if (!ctx->fci_key_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d fci_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err5;
	}
#endif

	// Open a Conntrack socket
	ctx->catch_handle = nfct_open(CONNTRACK, NFCT_ALL_CT_GROUPS);
	if (!ctx->catch_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d nfct_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err6;
	}

	// Open a Netfilter socket
	ctx->handle = nfct_open(CONNTRACK, 0);
	if (!ctx->handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d nfct_open()failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err7;
	}

	// Open a Netfilter socket
	ctx->get_handle = nfct_open(CONNTRACK, 0);
	if (!ctx->get_handle)
	{
		cmm_print(DEBUG_CRIT, "%s:%d nfct_open()failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err8;
	}

	/* Open a Netlink socket and register to the Multicast groups*/
	if (cmm_rtnl_open(&ctx->rth_neigh, RTMGRP_NEIGH) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s:%d cmm_rtnl_open(NEIGHBOR) failed\n", __func__, __LINE__);
		goto err9;
	}

	if (cmm_rtnl_open(&ctx->rth_link, RTMGRP_LINK) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s:%d cmm_rtnl_open(LINK) failed\n", __func__, __LINE__);
		goto err10;
	}

	if (cmm_rtnl_open(&ctx->rth_ifaddr, RTMGRP_IPV4_IFADDR | RTMGRP_IPV6_IFADDR) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s:%d cmm_rtnl_open(IFADDR) failed\n", __func__, __LINE__);
		goto err11;
	}

	if (cmm_rtnl_open(&ctx->rth_route, RTMGRP_IPV4_ROUTE | RTMGRP_IPV6_ROUTE) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s:%d cmm_rtnl_open(ROUTE) failed\n", __func__, __LINE__);
		goto err12;
	}

	if (cmm_rtnl_open(&ctx->rth_rule, RTMGRP_IPV4_RULE) < 0)
	{
		cmm_print(DEBUG_STDERR, "%s:%d cmm_rtnl_open(RULE) failed\n", __func__, __LINE__);
		goto err13;
	}

	if(cmmBridgeInit(ctx))
		goto err14;
	
	fd = fci_fd(ctx->fci_catch_handle);

	if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d fcntl(%d) failed %s\n", __func__, __LINE__, fd, strerror(errno));
		goto err14;
	}

#if !defined(IPSEC_SUPPORT_DISABLED)
	fd = fci_fd(ctx->fci_key_catch_handle);

	if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d fcntl(%d) failed %s\n", __func__, __LINE__, fd, strerror(errno));
		goto err14;
	}
#endif

	if (nfnl_set_nonblocking_mode((struct nfnl_handle *)nfct_nfnlh(ctx->catch_handle)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d nfnl_set_nonblocking_mode() failed %s\n", __func__, __LINE__, strerror(errno));
		goto err14;
	}

	if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_neigh) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(NEIGHBOR) failed\n", __func__, __LINE__);
		goto err14;
	}

	if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_link) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(LINK) failed\n", __func__, __LINE__);
		goto err14;
	}

	if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_ifaddr) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(IFADDR) failed\n", __func__, __LINE__);
		goto err13;
	}

	if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_route) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(ROUTE) failed\n", __func__, __LINE__);
		goto err14;
	}

	if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_rule) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(RULE) failed\n", __func__, __LINE__);
		goto err14;
	}
	if(globalConf.auto_bridge)
		if (cmm_rtnl_set_nonblocking_mode(&ctx->rth_abm) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s:%d cmm_rtnl_set_nonblocking_mode(abm) failed\n", __func__, __LINE__);
			goto err14;
		}

	// Change socket size to avoid losing messages
	nfnl_rcvbufsiz((struct nfnl_handle *)nfct_nfnlh(ctx->catch_handle), NFNL_SOCK_SIZE);

#ifdef ROUTER
	nfnl_rcvbufsiz((struct nfnl_handle *)nfct_nfnlh(ctx->handle), 128 * 1024);
#endif

	nfnl_set_rcv_buffer_size((struct nfnl_handle *)nfct_nfnlh(ctx->catch_handle), 128 * 1024);

	nfnl_unset_sequence_tracking((struct nfnl_handle *)nfct_nfnlh(ctx->catch_handle));

	cmm_rtnl_rcvbufsiz(&ctx->rth_neigh, 1024 * 1024);
	
	cmm_rtnl_rcvbufsiz(&ctx->rth_link, 512 * 1024);

	
	fd = fci_fd(ctx->fci_catch_handle);
	size = NFNL_SOCK_SIZE;
	if(setsockopt(fd, SOL_SOCKET, SO_RCVBUFFORCE, &size, socklen) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s:%d setsockopt(socket %d) failed %s\n", __func__, __LINE__, fd, strerror(errno));
		goto err14;
	}

	fci_register_cb(ctx->fci_catch_handle, cmmFeCatch);
#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_register_cb(ctx->fci_key_catch_handle, cmmKeyCatch);
#endif
	nfct_callback_register(ctx->catch_handle, NFCT_T_ALL, cmmCtCatch, ctx);
	nfct_callback_register(ctx->get_handle, NFCT_T_ALL, cmmCtCheckCtCb, NULL);

#ifndef LS1043
	for (i = 0; i < GEM_PORTS; i++) {
		if (itf_name_update(ctx->fci_handle, &port_table[i]) < 0)
			goto err14;
	}
#endif

	if (itf_table_init(&itf_table) < 0)
		goto err14;

	// Reset the Forward Engine
	cmmFeReset(ctx->fci_handle);

	if (pthread_create(&ctx->pthread, NULL, cmmCtThread, ctx) < 0)
	{
		cmm_print(DEBUG_CRIT, "%s:%d pthread_create() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err14;
	}

	return 0;

err14:
	cmm_rtnl_close(&ctx->rth_rule);

err13:
	cmm_rtnl_close(&ctx->rth_route);

err12:
	cmm_rtnl_close(&ctx->rth_ifaddr);

err11:
	cmm_rtnl_close(&ctx->rth_link);

err10:
	cmm_rtnl_close(&ctx->rth_neigh);

err9:
	nfct_close(ctx->get_handle);

err8:
	nfct_close(ctx->handle);

err7:
	nfct_close(ctx->catch_handle);

err6:
#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_close(ctx->fci_key_handle);

err5:
	fci_close(ctx->fci_key_catch_handle);

err4:
#endif
	fci_close(ctx->fci_handle);

err3:
	fci_close(ctx->fci_catch_handle);

err2:
#ifdef APP_SOLICIT
	cmmRtnlKernelModuleUnInit();
err1:
#endif

	cmmCtKernelModuleUnInit();

err0:
	if (p_nfconn_update)
		nfct_destroy(p_nfconn_update);
	if (p_nfconn_clone)
		nfct_destroy(p_nfconn_clone);
	return -1;
}

void cmmCtExit(struct cmm_ct *ctx)
{
	cmm_print(DEBUG_INFO, "%s\n", __func__);

#if defined(__UCLIBC__)
	/* workaround uclibc pthread_cancel() bug, force thread to exit */
#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_close(ctx->fci_key_catch_handle);
#endif
#else
	pthread_cancel(ctx->pthread);
#endif

	pthread_join(ctx->pthread, NULL);

	cmm_rtnl_close(&ctx->rth_rule);

	cmm_rtnl_close(&ctx->rth_route);

	cmm_rtnl_close(&ctx->rth_ifaddr);

	cmm_rtnl_close(&ctx->rth_link);

	cmm_rtnl_close(&ctx->rth_neigh);

	nfct_close(ctx->handle);

	nfct_close(ctx->catch_handle);

#if !defined(IPSEC_SUPPORT_DISABLED)
	fci_close(ctx->fci_key_handle);

#if !defined(__UCLIBC__)
	fci_close(ctx->fci_key_catch_handle);
#endif
#endif
	fci_close(ctx->fci_handle);

	fci_close(ctx->fci_catch_handle);

#ifdef APP_SOLICIT
	cmmRtnlKernelModuleUnInit();
#endif
	cmmCtKernelModuleUnInit();

	if (p_nfconn_update)
		nfct_destroy(p_nfconn_update);
	if (p_nfconn_clone)
		nfct_destroy(p_nfconn_clone);

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);
}

/*****************************************************************
* cmmCtChangeProcess4
*
*
******************************************************************/
int cmmCtChangeProcess4(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	unsigned int tmp;
	u_int64_t tmp64;
	int cpt = tabStart;
	cmmd_ct_ex_cmd_t ctCmd;
	union u_rxbuf rxbuf;

	memset(&ctCmd, 0, sizeof(ctCmd));

	// orig srcIP
	if (!keywords[cpt])
		goto help;
	if (inet_pton(AF_INET, keywords[cpt], &ctCmd.saddr) != 1)
		goto help;

	// orig destIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET, keywords[cpt], &ctCmd.daddr) != 1)
		goto help;

	// orig srcPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.sport = htons(tmp);

	// orig destPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.dport = htons(tmp);

	// reply srcIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET, keywords[cpt], &ctCmd.saddr_reply) != 1)
		goto help;

	// reply destIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET, keywords[cpt], &ctCmd.daddr_reply) != 1)
		goto help;

	// reply srcPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.sport_reply = htons(tmp);

	// reply destPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.dport_reply = htons(tmp);

	// protocol
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.protocol = tmp;

	// mark
	if (!keywords[++cpt])
		goto help;
	tmp64 = (u_int64_t)strtoull(keywords[cpt], NULL, 0);
	ctCmd.qosconnmark = tmp64;

	if (keywords[++cpt])
		goto help;

	ctCmd.action = CMMD_ACTION_UPDATE;
	if (cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV4_CONNTRACK, &ctCmd, sizeof(ctCmd), &rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("CMD_IPV4_CONNTRACK, ACTION_UPDATE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}

	return 0;

help:
	cmm_print(DEBUG_STDOUT, "Usage: ipv4 update {orig-srcIPaddr} {orig-destIPaddr} {orig-srcPort} {orig-destPort} {reply-srcIPaddr} {reply-destIPaddr} {reply-srcPort} {reply-destPort} {protocol} {mark}\n");
	return -1;
}

/*****************************************************************
* cmmCtChangeProcess6
*
*
******************************************************************/
int cmmCtChangeProcess6(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	unsigned int tmp;
	u_int64_t tmp64;
	int cpt = tabStart;
	cmmd_ct6_ex_cmd_t ctCmd;
	union u_rxbuf rxbuf;

	memset(&ctCmd, 0, sizeof(ctCmd));

	// orig srcIP
	if (!keywords[cpt])
		goto help;
	if (inet_pton(AF_INET6, keywords[cpt], &ctCmd.saddr) != 1)
		goto help;

	// orig destIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET6, keywords[cpt], &ctCmd.daddr) != 1)
		goto help;

	// orig srcPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.sport = htons(tmp);

	// orig destPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.dport = htons(tmp);

	// reply srcIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET6, keywords[cpt], &ctCmd.saddr_reply) != 1)
		goto help;

	// reply destIP
	if (!keywords[++cpt])
		goto help;
	if (inet_pton(AF_INET6, keywords[cpt], &ctCmd.daddr_reply) != 1)
		goto help;

	// reply srcPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.sport_reply = htons(tmp);

	// reply destPort
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.dport_reply = htons(tmp);

	// protocol
	if (!keywords[++cpt])
		goto help;
	tmp = strtoul(keywords[cpt], NULL, 0);
	if (tmp > 0xFFFF)
		goto help;
	ctCmd.protocol = tmp;

	// mark
	if (!keywords[++cpt])
		goto help;
	tmp64 = (u_int64_t)strtoull(keywords[cpt], NULL, 0);
	ctCmd.qosconnmark = tmp64;

	if (keywords[++cpt])
		goto help;

	ctCmd.action = CMMD_ACTION_UPDATE;
	if (cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV6_CONNTRACK, &ctCmd, sizeof(ctCmd), &rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("CMD_IPV6_CONNTRACK, ACTION_UPDATE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}

	return 0;

help:
	cmm_print(DEBUG_STDOUT, "Usage: ipv6 update {orig-srcIPaddr} {orig-destIPaddr} {orig-srcPort} {orig-destPort} {reply-srcIPaddr} {reply-destIPaddr} {reply-srcPort} {reply-destPort} {protocol} {mark}\n");
	return -1;
}

/*****************************************************************
* cmmCtChange
* For now only supports mark change
*
******************************************************************/
static int cmmCtChange(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len)
{
	struct nf_conntrack *ctTemp;
	struct ctTable *ctEntry;
	u_int64_t qosmark;
	int rc = 0;

	ctTemp = nfct_new();
	if (!ctTemp)
	{
		cmm_print(DEBUG_ERROR, "%s: nfct_new() failed - %s\n", __func__, strerror(errno));
		rc = -1;
		goto out0;
	}
	
	if (function_code == CMMD_CMD_IPV4_CONNTRACK)
	{
		cmmd_ct_ex_cmd_t *cmd = (cmmd_ct_ex_cmd_t*) cmd_buf;

		nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, cmd->protocol);
		nfct_set_attr_u8(ctTemp, ATTR_ORIG_L3PROTO, AF_INET);
		nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_SRC, cmd->saddr);
		nfct_set_attr_u32(ctTemp, ATTR_ORIG_IPV4_DST, cmd->daddr);
		nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_SRC, cmd->saddr_reply);
		nfct_set_attr_u32(ctTemp, ATTR_REPL_IPV4_DST, cmd->daddr_reply);

		nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, cmd->sport);
		nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, cmd->dport);
		nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, cmd->sport_reply);
		nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, cmd->dport_reply);
		qosmark = cmd->qosconnmark;
	}
	else if (function_code == CMMD_CMD_IPV6_CONNTRACK)
	{
		fpp_ct6_ex_cmd_t *cmd = (fpp_ct6_ex_cmd_t *) cmd_buf;

		nfct_set_attr_u8(ctTemp, ATTR_ORIG_L4PROTO, cmd->protocol);
		nfct_set_attr_u8(ctTemp, ATTR_ORIG_L3PROTO, AF_INET6);
		nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_SRC, &cmd->saddr[0]);
		nfct_set_attr(ctTemp, ATTR_ORIG_IPV6_DST, &cmd->daddr[0]);

		nfct_set_attr(ctTemp, ATTR_REPL_IPV6_SRC, &cmd->saddr_reply[0]);
		nfct_set_attr(ctTemp, ATTR_REPL_IPV6_DST, &cmd->daddr_reply[0]);

		nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_SRC, cmd->sport);
		nfct_set_attr_u16(ctTemp, ATTR_ORIG_PORT_DST, cmd->dport);
		nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_SRC, cmd->sport_reply);
		nfct_set_attr_u16(ctTemp, ATTR_REPL_PORT_DST, cmd->dport_reply);
		qosmark = cmd->qosconnmark;
	}
	else
	{/* Function code not valid */
		cmm_print(DEBUG_ERROR, "%s: Function code %x is not valid\n", __func__, function_code);
		res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
		*res_len = 2;
		goto out1;
	}

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	
	if (!(ctEntry = __cmmCtFind(ctTemp)))
	{
		cmm_print(DEBUG_WARNING, "%s: conntrack not found\n", __func__);
		res_buf[0] = CMMD_ERR_NOT_FOUND;
		*res_len = 2;
		goto out2;
	}

	ctEntry->flags |= FPP_NEEDS_UPDATE;
	cmmQosmarkSet(ctEntry->ct, qosmark);

	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	rc = ____cmmCtRegister(fci_handle, ctEntry);
	if (rc == 0)
	{
		res_buf[0] = CMMD_ERR_OK;
		*res_len = 2;
	}

#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);

out2:
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

out1:
	free(ctTemp);

out0:
	return rc;
}

int cmmCtHandle(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	u_int16_t action = ((u_int16_t *)cmd_buf)[0];
	int rc;

	switch (action)
	{
	case CMMD_ACTION_UPDATE:
		rc = cmmCtChange(fci_handle, function_code, cmd_buf, res_buf, res_len);
		break;

	case CMMD_ACTION_QUERY:
	case CMMD_ACTION_QUERY_CONT:
		rc = fci_cmd(fci_handle, function_code, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
		break;

	default:
		res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
		*res_len = 2;
		rc = 0;
		break;
	}

	return rc;
}

/*****************************************************************
* cmmQosmarkGet
*
*
******************************************************************/

u_int64_t cmmQosmarkGet(struct nf_conntrack *ct)
{
	u_int64_t mark;
#ifdef USE_QOSCONNMARK
		mark = nfct_get_attr_u64(ct, ATTR_QOSCONNMARK);
#else
		mark = (u_int64_t)nfct_get_attr_u32(ct, ATTR_MARK);
#endif
	return mark;
}

/*****************************************************************
* cmmQosmarkSet
*
*
******************************************************************/

void cmmQosmarkSet(struct nf_conntrack *ct, u_int64_t qosmark)
{
#ifdef USE_QOSCONNMARK
		nfct_set_attr_u64(ct, ATTR_QOSCONNMARK, qosmark);
#else
		nfct_set_attr_u32(ct, ATTR_MARK, (u_int32_t)qosmark);
#endif
}

#ifdef C2000_DPI
/*****************************************************************
* cmmDPIEnableShow
*
*
******************************************************************/
int cmmDPIEnableShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	if(globalConf.dpi_enable)
		cli_print(cli, " The DPI flag is enabled");
	else
		cli_print(cli, " The DPI flag is disabled");
	return CLI_OK;
}

int cmmDPIFlagProcessClientCmd(u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len)
{
        cmmd_dpi_enable_t    *entryCmd = (cmmd_dpi_enable_t*) cmd_buf;

        cmm_print(DEBUG_INFO, "cmmDPIFlagProcessClientCmd\n");

        res_buf[0] = CMMD_ERR_OK;
        *res_len = 2;

        switch (entryCmd->action) {
                case CMMD_DPIFLAG_ACTION_ENABLE:
                        cmm_print(DEBUG_INFO, "cmmDPIFlagProcessClientCmd- CMMD_DPIFLAG_ACTION_ENABLE\n");
                        globalConf.dpi_enable = 1;
                        break;

                case CMMD_DPIFLAG_ACTION_DISABLE:
                        cmm_print(DEBUG_INFO, "cmmDPIFlagProcessClientCmd- CMMD_DPIFLAG_ACTION_DISABLE\n");
                        globalConf.dpi_enable = 0;
                        break;

                default:
                        res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
                        break;
        }
        return 0;
}

void cmmDPIFlagPrintHelp(int cmd_type)
{
        if (cmd_type == DPI_UNKNOWN_CMD || cmd_type == DPI_ENABLE_CMD)
        {
            cmm_print(DEBUG_STDOUT, "Usage: set dpi enable \n"
                                    "       set dpi disable \n");
        }
}

int cmmDPIFlagSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cmd_type = DPI_UNKNOWN_CMD;
	int cpt = tabStart;
	int rc;

	char sndBuffer[256];
	union u_rxbuf rxbuf;
	cmmd_dpi_enable_t* entryCmd = (cmmd_dpi_enable_t*) sndBuffer;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	cmm_print(DEBUG_INFO, "Entered DPI Flag Set Process\n");

	if(!keywords[cpt])
		goto help;

	if( (strcasecmp(keywords[cpt], "enable") == 0) ||
	    (strcasecmp(keywords[cpt], "disable") == 0) )
	{
		cmd_type = DPI_ENABLE_CMD;

		if(strcasecmp(keywords[cpt], "enable") == 0)
			entryCmd->action = CMMD_DPIFLAG_ACTION_ENABLE;
		else
			entryCmd->action = CMMD_DPIFLAG_ACTION_DISABLE;
	}
	else
		goto keyword_error;

	rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_DPIENABLE, sndBuffer, sizeof(cmmd_dpi_enable_t), rxbuf.rcvBuffer);
	if(rc != 2)
	{
		if(rc >= 0)
			cmm_print(DEBUG_STDERR, "Unexpected response size for CMD_DPIENABLE: %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != CMMD_ERR_OK)
	{
		showErrorMsg("CMD_DPIENABLE", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
		return -1;
	}
        
	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmDPIFlagPrintHelp(cmd_type);
	return -1;
}
#endif /*C2000_DPI*/

