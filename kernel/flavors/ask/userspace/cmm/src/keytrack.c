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

/* Include cmm.h first to get netinet/in.h before linux/in6.h (musl compat) */
#include "cmm.h"
#include <net/if.h>
#if defined (LS1043)
#include <linux/xfrm.h>
#endif
#include "fpp.h"
#include "cmmd.h"
#include "module_ipsec.h"

/*************************************************************
Conventions:
-----------

Key Engine = Linux XFRM module (a.k.a SADB Managment module)

IPSec = IPSec module running on FPP

**************************************************************/

#ifdef IPSEC_FLOW_CACHE
struct list_head flow_table[FLOW_HASH_TABLE_SIZE];
pthread_mutex_t flowMutex = PTHREAD_MUTEX_INITIALIZER;	
#else
extern struct list_head ct_flow_no_sa_list;
#endif /* IPSEC_FLOW_CACHE */

#define DEFAULT_DPD_TIMEOUT 20
char gSAQueryTimerEnable = 0; /*Flag to check SA query timer is enabled or not*/
int gSAQueryTimerVal = DEFAULT_DPD_TIMEOUT;
char ipsec_algo_name[128];

/*****************************************************************
* cmmKeyEnginetoIPSec
*
*
******************************************************************/
int cmmKeyEnginetoIPSec(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	int rc = 0;

	cmm_print(DEBUG_INFO, "%s: fcode 0x%x len %d bytes\n", __func__, fcode, len);

	if ((rc = fci_write(fci_handle, fcode, len, payload)))
	{
		cmm_print(DEBUG_ERROR, "%s: error %d while sending function code 0x%x\n", __func__, rc, fcode);

		return -1;
	}
	
	return 0;

}

/*****************************************************************
* cmmIPSectoKeyEngine
*
*
******************************************************************/
int cmmIPSectoKeyEngine(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	int rc = 0;

	cmm_print(DEBUG_INFO, "cmmIPSectoKeyEngine: fcode 0x%x len %d bytes\n", fcode, len);
	
	if ((rc = fci_write(fci_handle, fcode, len, payload)))
	{
		cmm_print(DEBUG_ERROR, "%s: error %d while sending function code 0x%x\n", __func__, rc, fcode);

		return -1;
	}

	return 0;
}

#ifdef IPSEC_FLOW_CACHE
/*****************************************************************
* cmmFlowKeyEngineRemove
*
*
******************************************************************/
int cmmFlowKeyEngineRemove(FCI_CLIENT *fci_handle, struct FlowEntry *fentry)
{
	unsigned short msg[sizeof(fentry->fl) + sizeof(fentry->family) + sizeof(fentry->dir)] = {0};
	unsigned short len = 0;
#ifdef IPSEC_DBG
	cmm_print(DEBUG_INFO, "%s\n", __func__);
#endif

	memcpy(msg, (unsigned char *)&fentry->fl, sizeof(struct flowi));
	len += sizeof(struct flowi);
	
	msg[len/2] = fentry->family;
	len += sizeof(fentry->family);

	msg[len/2] = fentry->dir;
	len += sizeof(fentry->dir);

	/* Notify the XFRM to remove this flow from its flow cache */
	return cmmIPSectoKeyEngine(fci_handle, FPP_CMD_NETKEY_FLOW_REMOVE, len, (unsigned short *)msg);
}

/*****************************************************************
* __cmmFlowGet
*
*
******************************************************************/
struct FlowEntry *__cmmFlowGet(	int family, const unsigned int *Saddr, 
				const unsigned int *Daddr, unsigned short Sport, 
				unsigned short Dport, unsigned char proto, 
				unsigned short dir)
{
	struct FlowEntry *flow;

	flow = __cmmFlowFind(family, Saddr, Daddr, Sport, Dport, proto, dir);

	if (flow)
		flow->ref_count++;

	return flow;
}

/*****************************************************************
* __cmmFlowPut
*
*
******************************************************************/
void __cmmFlowPut(struct FlowEntry *flow)
{
	if(flow->ref_count)
		flow->ref_count--;

	if(!(flow->ref_count))
		__cmmFlowRemove(flow);
}

/*****************************************************************
* __cmmFlowFind
*
*
******************************************************************/
struct FlowEntry *__cmmFlowFind(int family, const unsigned int *Saddr, 
				const unsigned int *Daddr, unsigned short Sport, 
				unsigned short Dport, unsigned char proto,
				unsigned short dir)
{
	struct FlowEntry *flow;
	struct list_head *entry;
	unsigned int key;

	key = HASH_CT(family, Saddr, Daddr, Sport, Dport, proto);

	entry = list_first(&flow_table[key]);

	while (entry != &flow_table[key])
	{
		flow = container_of(entry, struct FlowEntry, list);


		if( family == AF_INET)
		{

			if(!memcmp(Saddr, &flow->fl.u.ip4.saddr, 4) &&
			!memcmp(Daddr, &flow->fl.u.ip4.daddr, 4) &&  
			(Sport == flow->fl.u.ip4.fl4_sport) &&
			(Dport == flow->fl.u.ip4.fl4_dport) &&
			(proto == flow->fl.flowi_proto) &&
			(dir   == flow->dir))
			{
				cmm_print(DEBUG_INFO, " flow found: Sport %d Dport %d dir :%d\n", Sport, Dport, dir);
				//Entry found
				goto found;
			}

		}
		else if (family ==AF_INET6)
		{
			if(((family == AF_INET6) && !memcmp(Saddr, flow->fl.u.ip6.saddr.s6_addr32 /*flow->fl.nl_u.ip6_u.saddr*/, 16) &&
			!memcmp(Daddr, flow->fl.u.ip6.daddr.s6_addr32, 16)) &&
			/*Port*/
			(Sport == flow->fl.u.ip6.fl4_sport) &&
			(Dport == flow->fl.u.ip6.fl4_dport) &&
			/*Protocol*/
			(proto == flow->fl.flowi_proto) &&
			(dir == flow->dir))
			{
				cmm_print(DEBUG_INFO, " flow found: Sport %d Dport %d dir :%d\n", Sport, Dport, dir);
				//Entry found
				goto found;
			}
		}

		entry = list_next(entry);
	}

	cmm_print(DEBUG_INFO, " flow NOT found\n");

	flow = NULL;
found:
	return flow;
}

/*****************************************************************
* __cmmFlowRemove
*
*
******************************************************************/
void __cmmFlowRemove(struct FlowEntry *flow)
{
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	unsigned int *sAddr, *dAddr;
	int i;

	if (flow->family == AF_INET)
	{
		sAddr = &flow->fl.u.ip4.saddr;
		dAddr = &flow->fl.u.ip4.daddr;
	}
	else
	{
		sAddr = flow->fl.u.ip6.saddr.s6_addr32;
		dAddr = flow->fl.u.ip6.daddr.s6_addr32;
	}

	cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d) removed\n",
			__func__,
			inet_ntop(flow->family, sAddr, sbuf, INET6_ADDRSTRLEN),
			inet_ntop(flow->family, dAddr, dbuf, INET6_ADDRSTRLEN),
			  ntohs(flow->fl.u.ip4.fl4_sport), ntohs(flow->fl.u.ip4.fl4_dport), flow->fl.flowi_proto);

	list_del(&flow->list);
	for(i =0 ; i< MAX_SA_PER_FLOW; i++)
		list_del(&flow->list_by_sa[i]);
	free(flow);
}


/*****************************************************************
* __cmmFlowAdd
*
*
******************************************************************/
struct FlowEntry *__cmmFlowAdd(int family, struct flowi *fl, unsigned char sa_nr, unsigned short *sa_handle, unsigned short dir)
{
	struct FlowEntry *flow;
	unsigned int *sAddr, *dAddr;
	unsigned short sport, dport;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	struct SATable *SAEntry = NULL;
	unsigned int key;
	int i;

	//Add the new flow to the local flow cache
	flow = (struct FlowEntry *) malloc(sizeof(struct FlowEntry));
	if (!flow)
	{
		cmm_print(DEBUG_ERROR, "%s: malloc() failed\n", __func__);
		goto err0;
	}

	memset(flow, 0, sizeof(struct FlowEntry));

	memcpy(&flow->fl, fl, sizeof(struct flowi));
	memcpy(&flow->sa_handle, sa_handle, sizeof(unsigned short) * sa_nr);
	flow->sa_nr = sa_nr;
	flow->family = family;
	flow->dir = dir;

	if (family == AF_INET)
	{
		sAddr = &fl->u.ip4.saddr;
		dAddr = &fl->u.ip4.daddr;
		sport = fl->u.ip4.fl4_sport;
		dport = fl->u.ip4.fl4_dport;
	key = HASH_CT(family, sAddr, dAddr, fl->u.ip4.fl4_sport, fl->u.ip4.fl4_dport, fl->flowi_proto);
	}
	else
	{
		sAddr = fl->u.ip6.saddr.s6_addr32;
		dAddr = fl->u.ip6.daddr.s6_addr32;
		sport = fl->u.ip6.fl6_sport;
		dport = fl->u.ip6.fl6_dport;
	key = HASH_CT(family, sAddr, dAddr, fl->u.ip6.fl4_sport, fl->u.ip6.fl4_dport, fl->flowi_proto);
	}


	list_add(&flow_table[key], &flow->list);
	for (i =0; i < flow->sa_nr; i++)
	{
		SAEntry = __cmmSAFind(flow->sa_handle[i]);
		if(SAEntry)
			list_add(&SAEntry->flow_list[i], &flow->list_by_sa[i]);
	}
	cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d) added\n",
			__func__,
			inet_ntop(family, sAddr, sbuf, INET6_ADDRSTRLEN),
			inet_ntop(family, dAddr, dbuf, INET6_ADDRSTRLEN),
			ntohs(sport), ntohs(dport), fl->flowi_proto);

err0:
	return flow;
}

/* This function displays the list of local Secure Flows */

int cmmFlowLocalShow( struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct FlowEntry *flow;
	struct list_head *entry;
	int i, j, cpt=0,len=0;
	char buf1[INET6_ADDRSTRLEN], buf2[INET6_ADDRSTRLEN];
	char output_buf[256];

	for (i = 0 ; i < FLOW_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&flowMutex);

		for(entry = list_first(&flow_table[i]);
			entry != &flow_table[i];
			entry = list_next(entry))
		{
			flow = container_of(entry, struct FlowEntry, list);
			cpt++;

		//	cli_print(cli,"Incoming interface: %s  Outgoing interface :%s \n ",
		//	if_indextoname(flow->fl.iif ,iifname ),
		//	if_indextoname( flow->fl.oif, oifname));

			if (flow->family == AF_INET)
			{
				cli_print(cli ,
					"[%4d]:Src Addr : %s     Dest Addr : %s \n",cpt,
					inet_ntop(flow->family, &flow->fl.u.ip4.saddr, buf1,INET6_ADDRSTRLEN),
					inet_ntop(flow->family, &flow->fl.u.ip4.daddr, buf2, INET6_ADDRSTRLEN) );
			}
            else
			{
				cli_print(cli ,
					"[%4d]:Src Addr : %s     Dest Addr : %s \n",cpt,
					inet_ntop(flow->family, flow->fl.u.ip6.saddr.s6_addr32, buf1,INET6_ADDRSTRLEN),
					inet_ntop(flow->family, flow->fl.u.ip6.daddr.s6_addr32, buf2,INET6_ADDRSTRLEN) );
			}

			if ((flow->fl.flowi_proto == IPPROTO_TCP) ||
				(flow->fl.flowi_proto == IPPROTO_UDP))
    		{
				if(flow->family == AF_INET)
					cli_print(cli , "Protocol : %x  Sport :%d  Dport :%d\n",
						flow->fl.flowi_proto,
						htons(flow->fl.u.ip4.fl4_sport),
						htons(flow->fl.u.ip4.fl4_dport));
				else 
				cli_print(cli , "Protocol : %x  Sport :%d  Dport :%d\n",
						flow->fl.flowi_proto,
						htons(flow->fl.u.ip6.fl4_sport),
						htons(flow->fl.u.ip6.fl4_dport));

			}
			else if (flow->fl.flowi_proto == IPPROTO_ICMP)
			{
				cli_print(cli , "Protocol : ICMP  Type :%x  Code :%x\n",
					flow->fl.u.ip4.fl4_icmp_type,
					flow->fl.u.ip4.fl4_icmp_code);
			}
			else if ((flow->fl.flowi_proto == IPPROTO_AH) ||
				(flow->fl.flowi_proto == IPPROTO_ESP))
			{
				cli_print(cli , "Protocol : AH/ESP  spi :%x\n",
					flow->fl.u.ip4.fl4_ipsec_spi);
			}

			len += snprintf(output_buf+len ,256-len, "Dir:%x Flags :%x ", flow->dir,flow->flags);
			len += snprintf(output_buf+len , 256-len, "SA(sa_nr:%d HO:", flow->sa_nr);

			for (j = 0; j < flow->sa_nr; j++)
		           len += snprintf(output_buf+len,256-len, "%x:", flow->sa_handle[j]);

			len += snprintf(output_buf+len , 256-len, ")");
			cli_print(cli, "%s", output_buf);
			cli_print(cli, "Flow ref count %d\n",flow->ref_count);
			len = 0;
		}

		__pthread_mutex_unlock(&flowMutex);
		/* Give a chance to other processes waiting for the lock */
		if (!(i % 100))
			sched_yield();
	}

	cli_print(cli, " %d  secure connections printed", cpt);

	return CLI_OK;
}


/*****************************************************************
* __cmmFetchFlowParams
*
*
******************************************************************/
int __cmmFetchFlowParams(int family, struct flowi *fl, unsigned int **flsaddr, unsigned int **fldaddr, unsigned short *flsport, unsigned short *fldport)
{
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	switch (fl->flowi_proto)
	{
		case IPPROTO_IPIP:
		case IPPROTO_GRE:
			/* Make sure to reset port information */
			if (family == AF_INET)
			{
				fl->u.ip4.fl4_sport = 0;
				fl->u.ip4.fl4_dport = 0;
			}
			else
			{
				fl->u.ip6.fl6_sport = 0;
				fl->u.ip6.fl6_dport = 0;
			}
			/* fallthrough */
		case IPPROTO_TCP:
		case IPPROTO_UDP:
		{

			if (family == AF_INET)
			{
				*flsaddr = &fl->u.ip4.saddr;
				*fldaddr = &fl->u.ip4.daddr;
				*flsport = fl->u.ip4.fl4_sport;
				*fldport = fl->u.ip4.fl4_dport;
			}
			else
			{
				*flsaddr = fl->u.ip6.saddr.s6_addr32;
				*fldaddr = fl->u.ip6.daddr.s6_addr32;
				*flsport = fl->u.ip6.fl6_sport;
				*fldport = fl->u.ip6.fl6_dport;
			}

			cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d)\n",
					__func__,
					inet_ntop(family, *flsaddr, sbuf, INET6_ADDRSTRLEN),
					inet_ntop(family, *fldaddr, dbuf, INET6_ADDRSTRLEN),
					ntohs(*flsport), ntohs(*fldport), fl->flowi_proto);
		}
		break;

		default:
			return 1;

	}
	return 0;

}

/*****************************************************************
* __cmmUpdateFlowDependecies 
*
*
******************************************************************/
void __cmmUpdateFlowDependecies(struct FlowEntry *flow, unsigned int *flsaddr, unsigned int *fldaddr,
				unsigned short flsport, unsigned short fldport, int dir )
{
	char orig;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	char ifname[IFNAMSIZ];
	struct ctTable *ctEntry;
	struct socket *sock;
	struct interface *itf;
	/* Look for connections that use this flow */
	
	cmm_print(DEBUG_INFO,"%s \n ",__func__);
	ctEntry = __cmmCtFindFromFlow(flow->family, flsaddr, fldaddr, flsport, fldport, flow->fl.flowi_proto, &orig, 0);
	if (ctEntry)
	{
		/* Update flow pointers in Ct entry */
		if (orig) {
			if (dir == FLOW_DIR_OUT)
			{
				if (!ctEntry->fEntryOrigOut) {
					ctEntry->fEntryOrigOut = flow;
					flow->ref_count++;
				}
				else {
					if(ctEntry->fEntryOrigOut != flow)  {
						__cmmFlowPut(ctEntry->fEntryOrigOut);
						ctEntry->fEntryOrigOut = flow;
						flow->ref_count++;
					}
				}
			}

			if (dir == FLOW_DIR_FWD)
			{
				if (!ctEntry->fEntryOrigFwd) {
					ctEntry->fEntryOrigFwd = flow;
					flow->ref_count++;
				}
				else {
					if(ctEntry->fEntryOrigFwd != flow)  {
						__cmmFlowPut(ctEntry->fEntryOrigFwd);
						ctEntry->fEntryOrigFwd = flow;
						flow->ref_count++;
					}
				}
			}

		}
		else {
			if (dir == FLOW_DIR_OUT)
			{
				if (!ctEntry->fEntryRepOut) {
					ctEntry->fEntryRepOut = flow;
					flow->ref_count++;
				}
				else {
					if(ctEntry->fEntryRepOut != flow)  {
						__cmmFlowPut(ctEntry->fEntryRepOut);
						ctEntry->fEntryRepOut = flow;
						flow->ref_count++;
					}
				}
			}

			if (dir == FLOW_DIR_FWD)
			{
				if (!ctEntry->fEntryRepFwd) {
					ctEntry->fEntryRepFwd = flow;
					flow->ref_count++;
				}
				else {
					if(ctEntry->fEntryRepFwd != flow)  {
						__cmmFlowPut(ctEntry->fEntryRepFwd);
						ctEntry->fEntryRepFwd = flow;
						flow->ref_count++;
					}
				}
			}

		}

		/* Update connection in FPP */
		____cmmCtRegister(globalConf.ct.fci_handle, ctEntry);
	}

	/* Look for tunnels that use this flow */
	itf = __cmmTunnelFindFromFlow(flow->family, flsaddr, fldaddr, flow->fl.flowi_proto, &orig);
	if (itf)
	{
		cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d) matches tunnel: %s\n",
				__func__,
				inet_ntop(flow->family, flsaddr, sbuf, INET6_ADDRSTRLEN),
				inet_ntop(flow->family, fldaddr, dbuf, INET6_ADDRSTRLEN),
				ntohs(flsport), ntohs(fldport), flow->fl.flowi_proto, if_indextoname(itf->ifindex, ifname));

		/* Update flow pointers in tunnel entry */
		if (orig) {
			if (dir == FLOW_DIR_OUT)
			{
				if (!itf->flow_orig) {
					itf->flow_orig = flow;
					flow->ref_count++;
				}
				else {
					if(itf->flow_orig != flow)  {
						__cmmFlowPut(itf->flow_orig);
						itf->flow_orig = flow;
						flow->ref_count++;
					}
				}
			}
		}
		else {
			if (dir == FLOW_DIR_IN)
			{
				if (!itf->flow_rep) {
					itf->flow_rep = flow;
					flow->ref_count++;
				}
				else {
					if(itf->flow_rep != flow)  {
						__cmmFlowPut(itf->flow_rep);
						itf->flow_rep = flow;
						flow->ref_count++;
					}
				}
			}
		}

		__tunnel_add(globalConf.ct.fci_handle, itf);
	}

	/* Look for sockets that use this flow */
	sock = __cmmSocketFindFromFlow(flow->family, flsaddr, fldaddr, flow->fl.flowi_proto, &orig);
	if (sock)
	{
		cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d) matches socket ID %d\n",
				__func__,
				inet_ntop(flow->family, flsaddr, sbuf, INET6_ADDRSTRLEN),
				inet_ntop(flow->family, fldaddr, dbuf, INET6_ADDRSTRLEN),
				ntohs(flsport), ntohs(fldport), flow->fl.flowi_proto, sock->id);
		/* Update flow pointers in socket entry */
		/*tx and rx flows are swapped for sockets (L2TP sockets.) , 
		  this is because unlike RTP Relay, for L2TP a single socket is used, 
		  so the socket is created with daddr  as local address and saddr as peer address. 
		  This might need revisiting if the flows are extended for other sockets, 
		  right now this reduces code duplication as well as additional checks for sockets in PFE  */

		if (orig) {
			if (dir == FLOW_DIR_OUT)
			{
				if (!sock->rx_flow) {
					sock->rx_flow = flow;
					flow->ref_count++;
				}
				else {
					if (sock->rx_flow != flow) {
						__cmmFlowPut(sock->rx_flow);
						sock->rx_flow = flow;
						flow->ref_count++;
					}
				}
			}
		} else {
			if (dir == FLOW_DIR_IN)
			{
				if (!sock->tx_flow) {
					sock->tx_flow = flow;
					flow->ref_count++;
				}
				else {
					if (sock->tx_flow != flow) {
						__cmmFlowPut(sock->tx_flow);
						sock->tx_flow = flow;
						flow->ref_count++;
					}
				}
			}
		}
		__socket_open(globalConf.ct.fci_handle, sock);
	}
}

int cmmUpdateFlows(struct SATable *pSAEntry)
{
	struct FlowEntry *flow; 
	struct list_head *entry;
	unsigned int *flsaddr, *fldaddr;
	unsigned short flsport, fldport;
	int i;


	cmm_print(DEBUG_INFO,"%s\n",__func__);

	for(i =0; i< MAX_SA_PER_FLOW; i++)
	{
		entry = list_first(&pSAEntry->flow_list[i]);
		while (entry != &pSAEntry->flow_list[i])
		{
			flow = container_of(entry, struct FlowEntry, list_by_sa[i]);
			entry = list_next(entry);
			if(!__cmmFetchFlowParams(flow->family, &flow->fl, &flsaddr, &fldaddr, &flsport, &fldport))
			{
				if(flow->sa_handle[i] == pSAEntry->SAInfo.sagd)
				{
					flow->sa_handle[i] = 0;  
					flow->flags |= FPP_NEEDS_UPDATE;
					list_del(&flow->list_by_sa[i]);
					__cmmUpdateFlowDependecies(flow, flsaddr, fldaddr, flsport, fldport, flow->dir);
				}
			}
		}
	}

	return 0;

}

/*****************************************************************
* cmmKeyEngineFlow2Conntrack
*
*
******************************************************************/
int cmmKeyEngineFlow2Conntrack(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	unsigned short sa_nr;
	unsigned short family;
	unsigned short sa_handle[2];
	unsigned short msg_len = 0;
	unsigned short ignore_neigh;
	struct flowi *fl;
	struct FlowEntry *flow;
	unsigned int *flsaddr, *fldaddr;
	unsigned short flsport, fldport;
	int i, dir;
	struct SATable *SAEntry;

	// decode nl_key message

	// number of SA associated to this flow
	sa_nr = *payload++;
	msg_len += sizeof(unsigned short);

	if (sa_nr > 2){
		cmm_print(DEBUG_INFO, "%s: ERROR: Number of SAs should not increase max limit\n", __func__);
		goto error;
	}
	// SA handles
	for (i = 0; i < sa_nr; i++) {
		sa_handle[i] = *payload++;
		//#ifdef  IPSEC_DBG
		cmm_print(DEBUG_INFO, "%s: sa_handle %x\n", __func__, sa_handle[i]);
		//#endif
	}

	msg_len += sa_nr * sizeof(unsigned short);

	cmm_print(DEBUG_INFO, "%s: sa_nr %d msg_len %d len %d\n", __func__, sa_nr, msg_len, len);

	if (msg_len > len) {
		cmm_print(DEBUG_INFO, "%s: ERROR msg_len > len\n", __func__);
		goto error;
	}

	// Flow descriptor
	family = *payload++;
	msg_len += sizeof(unsigned short);
	dir = *payload++;
	msg_len += sizeof(unsigned short);
	ignore_neigh = *payload++;
	msg_len += sizeof(unsigned short);

	if ((len - msg_len) != sizeof(struct flowi)) {
		cmm_print(DEBUG_INFO, "%s: ERROR (len - msg_len) != sizeof(struct flowi)\n", __func__);
		goto error;
	}

	fl = (struct flowi*)payload;

	cmm_print(DEBUG_INFO, "%s: fl->proto %d dir %d\n", __func__, fl->flowi_proto, dir);

	if(__cmmFetchFlowParams(family, fl, &flsaddr, &fldaddr, &flsport, &fldport))
		goto error;		

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
	__pthread_mutex_lock(&flowMutex);
	__pthread_mutex_lock(&sa_lock);

	/* Create or update flow */
	flow = __cmmFlowFind(family, flsaddr, fldaddr, flsport, fldport, fl->flowi_proto, dir);
	if (!flow)
	{
		flow = __cmmFlowAdd(family, fl, sa_nr, sa_handle, dir);
		if (!flow)
		{
			cmm_print(DEBUG_ERROR, "%s: flow add failed\n", __func__);
			goto out;
		}
		flow->ignore_neigh = ignore_neigh;
	}
	else
	{
		if ((flow->sa_nr != sa_nr) || memcmp(flow->sa_handle, sa_handle, sa_nr * sizeof(unsigned short))) {

			for (i =0; i < sa_nr; i++)
			{
				if(flow->sa_handle[i] != sa_handle[i])
				{
					flow->sa_handle[i] = sa_handle[i];
					SAEntry = __cmmSAFind(flow->sa_handle[i]);
					if(SAEntry)
					{
						list_del(&flow->list_by_sa[i]);
						/* We could already be associated with an SA,
                                                   In that case, delete the previous association before linking to
						   the new SA */
						list_add(&SAEntry->flow_list[i], &flow->list_by_sa[i]);
					}
				}
			}
			flow->sa_nr = sa_nr;
			flow->ignore_neigh = ignore_neigh;
			flow->flags |= FPP_NEEDS_UPDATE;
		}
	}

	for (i = 0; i < flow->sa_nr; i++) {
		if (!cmmSAFind(flow->sa_handle[i]))
		{
			flow->flags |= FLOW_NO_SA; 
			break;
		}
	}

	__cmmUpdateFlowDependecies(flow, flsaddr, fldaddr, flsport, fldport, dir);


out:
	__pthread_mutex_unlock(&sa_lock);
	__pthread_mutex_unlock(&flowMutex);
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);


	return 0;

error :
	return -1;
}

/*****************************************************************
* cmmKeyEngineFlowRemove
*
*
******************************************************************/
int cmmKeyEngineFlowRemove(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload)
{
	unsigned short family;
	unsigned short dir;
	unsigned short msg_len = 0;
	struct flowi *fl;
	struct FlowEntry *flow;
	unsigned int *flsaddr, *fldaddr;
	unsigned short flsport, fldport;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];

	// decode nl_key message

	// Flow descriptor
	family = *payload++;
	msg_len += sizeof(unsigned short);
	dir = *payload++;
	msg_len += sizeof(unsigned short);

	if ((len - msg_len) != sizeof(struct flowi))
	{
		cmm_print(DEBUG_ERROR,"%s: msg_len mismatch for struct flowi\n",__func__);
		goto error;
	}

	fl = (struct flowi*)payload;

	switch (fl->flowi_proto)
	{
	case IPPROTO_IPIP:
	case IPPROTO_GRE:
		/* Make sure to reset port information */
		if (family == AF_INET)
		{
			fl->u.ip4.fl4_sport = 0;
			fl->u.ip4.fl4_dport = 0;
		}
		else
		{
			fl->u.ip6.fl6_sport = 0;
			fl->u.ip6.fl6_dport = 0;
		}
		/* fallthrough */
	case IPPROTO_TCP:
	case IPPROTO_UDP:

		if (family == AF_INET)
		{
			flsaddr = &fl->u.ip4.saddr;
			fldaddr = &fl->u.ip4.daddr;
			flsport = fl->u.ip4.fl4_sport;
			fldport = fl->u.ip4.fl4_dport;
		}
		else
		{
			flsaddr = fl->u.ip6.saddr.s6_addr32;
			fldaddr = fl->u.ip6.daddr.s6_addr32;
			flsport = fl->u.ip6.fl6_sport;
			fldport = fl->u.ip6.fl6_dport;
		}

		cmm_print(DEBUG_INFO, "%s: Flow(%s, %s, %d, %d, %d)\n",
			__func__,
			inet_ntop(family, flsaddr, sbuf, INET6_ADDRSTRLEN),
			inet_ntop(family, fldaddr, dbuf, INET6_ADDRSTRLEN),
				  ntohs(flsport), ntohs(fldport), fl->flowi_proto);

		pthread_mutex_lock(&itf_table.lock);
		pthread_mutex_lock(&ctMutex);
		pthread_mutex_lock(&rtMutex);
		pthread_mutex_lock(&neighMutex);
		pthread_mutex_lock(&flowMutex);

		/* Find the flow */
		flow = __cmmFlowFind(family, flsaddr, fldaddr, flsport, fldport, fl->flowi_proto, dir);
		if (!flow)
		{
			//cmm_print(DEBUG_INFO, "%s: flow not found\n", __func__);
			goto out;
		}
		else
		{
			if (flow->ref_count == 0)
				/*Remove this flow from cache*/
				__cmmFlowRemove(flow);
		}
	out:
		pthread_mutex_unlock(&flowMutex);
		pthread_mutex_unlock(&neighMutex);
		pthread_mutex_unlock(&rtMutex);
		pthread_mutex_unlock(&ctMutex);
		pthread_mutex_unlock(&itf_table.lock);

	default:
		break;
	}

	return 0;
	
error :
	return -1;
}
#else

void __cmmUpdateFlowDependecies(struct ctTable *ctEntry)
{
	/* Update connection in FPP */
	____cmmCtRegister(globalConf.ct.fci_handle, ctEntry);
	return;
}

/* This function is called when there is change in SA state (after call to cmmSASetState)
   main purpose is to check if we can offload any ctentries which are not yet offloaded
*/
void cmmUpdateCtEntriesInFlowNoSAList(unsigned short sgid)
{
	struct ctTable *ctEntry;
	struct list_head *entry;
	/* there can be some ctEntries received before SA creation,
	   those ctEntries are set to FLOW_NO_SA flag and added to flow_no_sa list
	   go through those lists and update flows
	*/
	__pthread_mutex_lock(&sa_lock);
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);

	for(entry = list_first(&ct_flow_no_sa_list); entry != &ct_flow_no_sa_list; )	
	{
		ctEntry =  container_of(entry, struct ctTable, flow_no_sa_list_node);
		cmm_print(DEBUG_INFO,"%s CTentry %p\n",__func__,ctEntry);
		cmm_print(DEBUG_INFO, "%s(%d) origfwdSA %p, origoutSA %p, repfwdSA %p, repOutSA %p\n",
				__FUNCTION__,__LINE__,ctEntry->fEntryOrigFwdSA,ctEntry->fEntryOrigOutSA,
					ctEntry->fEntryRepFwdSA,ctEntry->fEntryRepOutSA);
		entry = list_next(entry);
		if (cmmCheckIfCtEntryWithSGID(ctEntry, sgid))
		{
			ctEntry->flags |= FPP_NEEDS_UPDATE;
			__cmmUpdateFlowDependecies(ctEntry);
		}
	}
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);
	__pthread_mutex_unlock(&sa_lock);

	return;
}

int cmmUpdateFlows(struct SATable *pSAEntry)
{
	struct ctTable *ctEntry; 
	struct list_head *entry;
	int list_sa_index, dir =0;
	struct list_head *list_node;


	cmm_print(DEBUG_INFO,"%s SA entry %p\n",__func__,pSAEntry);

	for (dir = 0; dir < 2; dir++)
	{
		entry = list_first(&pSAEntry->ctentry_list[dir]);
		while (entry != &pSAEntry->ctentry_list[dir])
		{
			list_sa_index = 2*dir;
			if (!(pSAEntry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
				list_sa_index ++;
			ctEntry = container_of(entry, struct ctTable, list_by_sa[list_sa_index]);
			cmm_print(DEBUG_INFO,"%s CTentry %p\n",__func__,ctEntry);
			cmm_print(DEBUG_INFO, "%s(%d) origfwdSA %p, origoutSA %p, repfwdSA %p, repOutSA %p\n",
				__FUNCTION__,__LINE__,ctEntry->fEntryOrigFwdSA,ctEntry->fEntryOrigOutSA,
					ctEntry->fEntryRepFwdSA,ctEntry->fEntryRepOutSA);
			entry = list_next(entry);
			ctEntry->flags |= FPP_NEEDS_UPDATE;
			
			if (!dir)
			{
				if (!(pSAEntry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
				{
					cmm_print(DEBUG_INFO,"%s(%d) SA entry %p, origoutsa %p making to null\n",
						__FUNCTION__,__LINE__,pSAEntry,ctEntry->fEntryOrigOutSA);
					ctEntry->fEntryOrigOutSA = NULL;
				}
				else
				{
					cmm_print(DEBUG_INFO,"%s(%d) SA entry %p, origfwdsa %p making to null\n",
						__FUNCTION__,__LINE__,pSAEntry,ctEntry->fEntryOrigFwdSA);
					ctEntry->fEntryOrigFwdSA = NULL;
				}
			}
			else
			{
				if (!(pSAEntry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
				{
					cmm_print(DEBUG_INFO,"%s(%d) SA entry %p, repoutsa %p making to null\n",
						__FUNCTION__,__LINE__,pSAEntry,ctEntry->fEntryRepOutSA);
					ctEntry->fEntryRepOutSA = NULL;
				}
				else
				{
					cmm_print(DEBUG_INFO,"%s(%d) SA entry %p, Repfwdsa %p making to null\n",
						__FUNCTION__,__LINE__,pSAEntry,ctEntry->fEntryRepFwdSA);
					ctEntry->fEntryRepFwdSA = NULL;
				}
			}
			
			list_node = &ctEntry->list_by_sa[list_sa_index];
			list_del(list_node);
			__cmmUpdateFlowDependecies(ctEntry);
		}
	}
	return 0;

}

static void cmmReplaceXfrmHandle(unsigned short  *xfrm_handle, unsigned short old_val,
				unsigned short new_val)
{
	int ii;
	if (xfrm_handle)
	{
		for (ii=0; ii<2*MAX_SAs_INFO_PER_DIR_IN_NL_MSG; ii++)
		{
			if (xfrm_handle[ii] == old_val)
			{
				cmm_print(DEBUG_INFO,"%s(%d) old val 0x%x, new val 0x%x\n",__FUNCTION__,__LINE__, old_val,new_val);
				xfrm_handle[ii] = new_val;
				return;
			}
		}
		cmm_print(DEBUG_INFO,"%s(%d) xfrm originator: handles 0x%x, 0x%x, 0x%x, 0x%x\n",
					__FUNCTION__,__LINE__, xfrm_handle[0],xfrm_handle[1],
					xfrm_handle[2],xfrm_handle[3]);
	}
	return;
}

int cmmUpdateFlowsWithNewSAInfo(struct SATable *pNewSAEntry,unsigned short old_xfrm_handle)
{
	struct ctTable *ctEntry;
	struct list_head *entry;
	int list_sa_index, dir =0;
	struct SATable *pSAEntry;
	unsigned short	*orig_xfrm_handle, *rep_xfrm_handle;
	struct nf_conntrack *ct;


	/* Find SA with old_xfrm_handle */
	cmm_print(DEBUG_INFO,"%s(%d) old_xfrm_handle 0x%x, new handle 0x%x\n",
		__FUNCTION__,__LINE__,old_xfrm_handle,pNewSAEntry->SAInfo.sagd);
	pSAEntry = cmmSAFind(old_xfrm_handle);
	if (!pSAEntry)
	{
		return 0;
	}

	for (dir = 0; dir < 2; dir++)
	{
		entry = list_first(&pSAEntry->ctentry_list[dir]);
		while (entry != &pSAEntry->ctentry_list[dir])
		{
			list_sa_index = 2*dir;
			if (!(pSAEntry->SAInfo.id.flags & NLKEY_SAFLAGS_INBOUND))
				list_sa_index ++;
			ctEntry = container_of(entry, struct ctTable, list_by_sa[list_sa_index]);
			ct = ctEntry->ct;
			orig_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_ORIG_COMCERTO_FP_XFRM_HANDLE);
			rep_xfrm_handle = (unsigned short *)nfct_get_attr(ct,ATTR_REPL_COMCERTO_FP_XFRM_HANDLE);

			cmmReplaceXfrmHandle(orig_xfrm_handle, old_xfrm_handle, pNewSAEntry->SAInfo.sagd);
			cmmReplaceXfrmHandle(rep_xfrm_handle, old_xfrm_handle, pNewSAEntry->SAInfo.sagd);
			entry = list_next(entry);
		}
	}

	/* update old_xfrm_value with new SA xfrm handle value in netlink messages of CtEntry */
	cmmUpdateFlows(pSAEntry);
	return 0;

}

#endif /* IPSEC_FLOW_CACHE */

/*****************************************************************
* cmmKeyCatch
*
*
******************************************************************/
int cmmKeyCatch(unsigned short fcode, unsigned short len, unsigned short *payload)
{
	int rc = FCI_CB_CONTINUE;	
	int state_valid;
	unsigned short sgid;

	cmm_print(DEBUG_INFO, "%s: fcode 0x%x len %d bytes\n", __func__, fcode, len);

	switch (fcode)
	{
		case FPP_CMD_NETKEY_SA_ADD:
			if(cmmSACreate(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP;
			else if(cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP; 
			break;
		case FPP_CMD_NETKEY_SA_DELETE:
			{
				PCommandIPSecDeleteSA pSA_cmd = (PCommandIPSecDeleteSA)payload;
				if (len != sizeof(CommandIPSecDeleteSA))
				{
					cmm_print(DEBUG_ERROR, "%s: command length doesn't match %zu-%d\n", 
						__func__, sizeof(CommandIPSecDeleteSA), len);
					rc = FCI_CB_STOP;
					break;
				}

				if(cmmSADelete(globalConf.ct.fci_handle, pSA_cmd) < 0)
					rc = FCI_CB_STOP;
				if(cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
					rc = FCI_CB_STOP;
			}
			break;
		case FPP_CMD_NETKEY_SA_FLUSH:
			if(cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP;
			if(cmmSAFlush(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP;
			break;
		case FPP_CMD_NETKEY_SA_SET_TUNNEL:
			if(cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP;
			else if(cmmSASetTunnel(globalConf.ct.fci_handle, fcode, len, payload) < 0)
				rc = FCI_CB_STOP;
			break;
		case FPP_CMD_NETKEY_SA_SET_KEYS:
		case FPP_CMD_NETKEY_SA_SET_NATT:
		case FPP_CMD_NETKEY_SA_SET_LIFETIME:
			if (cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
			{
				rc = FCI_CB_STOP;
			}
			break;
		case FPP_CMD_NETKEY_SA_SET_STATE:
			if(cmmSASetState(globalConf.ct.fci_key_handle, fcode, len, payload, &state_valid, &sgid)< 0)
				rc = FCI_CB_STOP;
			else if (cmmKeyEnginetoIPSec(globalConf.ct.fci_handle, fcode, len, payload) < 0)
			{
				rc = FCI_CB_STOP;
			}
#ifndef IPSEC_FLOW_CACHE
			/* if SA cmd state valid received, check if any ctEntries can be offloaded */
			else if (state_valid)
				cmmUpdateCtEntriesInFlowNoSAList(sgid);
#endif /* iPSEC_FLOW_CACHE */
			break;

#ifdef IPSEC_FLOW_CACHE
		case FPP_CMD_NETKEY_FLOW_ADD:
			cmmKeyEngineFlow2Conntrack(globalConf.ct.fci_handle, fcode, len, payload);
			break;

		case FPP_CMD_NETKEY_FLOW_REMOVE:
			cmmKeyEngineFlowRemove(globalConf.ct.fci_handle, fcode, len, payload);
			break;
#endif /* IPSEC_FLOW_CACHE */

		default:
			cmm_print(DEBUG_ERROR, "%s: unknow message(%x) received from linux Key Engine\n", __func__, fcode);
			break;
	}

	return rc;
}

int cmmGetAuthAlgoName(unsigned char auth_algo, char* alg_name)
{
	switch (auth_algo)
	{
		case SADB_AALG_NONE:
			strcpy(alg_name, "NONE");
		break;
		case SADB_AALG_MD5HMAC:
			strcpy(alg_name, "MD5-HMAC");
		break;
		case SADB_AALG_SHA1HMAC:
			strcpy(alg_name, "SHA1-HMAC");
		break;
		case SADB_X_AALG_SHA2_256HMAC:
			strcpy(alg_name, "SHA2-256HMAC");
		break;
		case SADB_X_AALG_SHA2_384HMAC:
			strcpy(alg_name, "SHA2-384HMAC");
		break;
		case SADB_X_AALG_SHA2_512HMAC:
			strcpy(alg_name, "SHA2-512HMAC");
		break;
		case SADB_X_AALG_RIPEMD160HMAC:
			strcpy(alg_name, "RIPEMD-160HMAC");
		break;
		case SADB_X_AALG_AES_XCBC_MAC:
			strcpy(alg_name, "AES-XCBC");
		break;
		case SADB_X_AALG_NULL:
			strcpy(alg_name, "NULL");
		break;
		default:
			strcpy(alg_name, "UNKNOWN");
		break;

	}
	return 0;
}

int cmmGetCipherAlgoName(unsigned char cipher_algo, char* alg_name)
{
	switch (cipher_algo)
	{
		case SADB_EALG_NONE:
			strcpy(alg_name, "NONE");
		break;
		case SADB_EALG_DESCBC:
			strcpy(alg_name, "DES-CBC");
		break;
		case SADB_EALG_3DESCBC:
			strcpy(alg_name, "3DES-CBC");
		break;
		case SADB_X_EALG_CASTCBC:
			strcpy(alg_name, "CAST-CBC");
		break;
		case SADB_X_EALG_BLOWFISHCBC:
			strcpy(alg_name, "BLOWFISH-CBC");
		break;
		case SADB_EALG_NULL:
			strcpy(alg_name, "NULL");
		break;
		case SADB_X_EALG_AESCBC:
			strcpy(alg_name, "AES-CBC");
		break;
		case SADB_X_EALG_AESCTR:
			strcpy(alg_name, "AES-CTR");
		break;
		case SADB_X_EALG_AES_CCM_ICV8:
			strcpy(alg_name, "AES-CCM-ICV8");
		break;
		case SADB_X_EALG_AES_CCM_ICV12:
			strcpy(alg_name, "AES-CCM-ICV12");
		break;
		case SADB_X_EALG_AES_CCM_ICV16:
			strcpy(alg_name, "AES-CCM-ICV16");
		break;
		case SADB_X_EALG_AES_GCM_ICV8:
			strcpy(alg_name, "AES-GCM-ICV8");
		break;
		case SADB_X_EALG_AES_GCM_ICV12:
			strcpy(alg_name, "AES-GCM-ICV12");
		break;
		case SADB_X_EALG_AES_GCM_ICV16:
			strcpy(alg_name, "AES-GCM-ICV16");
		break;
		case SADB_X_EALG_NULL_AES_GMAC:
			strcpy(alg_name, "AES-GMAC");
		break;
		case SADB_X_EALG_CAMELLIACBC:
			strcpy(alg_name, "CAMELLIA-CBC");
		break;
		case SADB_X_EALG_SERPENTCBC:
			strcpy(alg_name, "SERPENT-CBC");
		break;
		case SADB_X_EALG_TWOFISHCBC:
			strcpy(alg_name, "TWOFISH-CBC");
		break;
		default:
			strcpy(alg_name, "UNKNOWN");
		break;
	}
	return 0;
}

/************************************************************
 **
 ** cmmSECfailStatsQueryProcess
 **
 *************************************************************/
#if defined (LS1043)
int cmmSECfailStatsQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
	fpp_sec_failure_stats_query_cmd_t *pStats; 

	memset(rxbuf.rcvBuffer, 0, CMM_BUF_SIZE);
	pStats = (fpp_sec_failure_stats_query_cmd_t *)rxbuf.rcvBuffer;

	if ((keywords[tabStart]) &&
		(strcasecmp(keywords[tabStart],"reset") == 0))
	{
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_IPSEC_RESET_SEC_FAILURE_STATS,
			pStats, sizeof(fpp_sec_failure_stats_query_cmd_t), rxbuf.rcvBuffer);
		return CLI_OK;
	}
		
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_IPSEC_SEC_FAILURE_STATS,
		pStats, sizeof(fpp_sec_failure_stats_query_cmd_t), rxbuf.rcvBuffer);
	if (rcvBytes < sizeof(fpp_sec_failure_stats_query_cmd_t) + sizeof (uint16_t))
	{
		cmm_print(DEBUG_STDERR,"ERROR: Cmd not supported\n");
		return CLI_OK;
	}
	cmm_print(DEBUG_STDOUT, "SEC failure stats:\n");

	cmm_print(DEBUG_STDOUT, "\t ICV failures:                           %u\n",
		pStats->SEC_failure_stats.icv_failures);
	cmm_print(DEBUG_STDOUT, "\t HW errors:                              %u\n",
		pStats->SEC_failure_stats.hw_errs);
	cmm_print(DEBUG_STDOUT, "\t CCM AAD size errors:                    %u\n",
		pStats->SEC_failure_stats.CCM_AAD_size_errs);
	cmm_print(DEBUG_STDOUT, "\t ANTI REPLAY LATE errors:                %u\n",
		pStats->SEC_failure_stats.anti_replay_late_errs);
	cmm_print(DEBUG_STDOUT, "\t ANTI REPLAY REPLAY errors:              %u\n", 
		pStats->SEC_failure_stats.anti_replay_replay_errs);
	cmm_print(DEBUG_STDOUT, "\t SEQUENCE NUMBER OVERFLOW errors:        %u\n",
		pStats->SEC_failure_stats.seq_num_overflows);
	cmm_print(DEBUG_STDOUT, "\t DMA errors:                             %u\n",
		pStats->SEC_failure_stats.DMA_errs);
	cmm_print(DEBUG_STDOUT, "\t DECO-watchdog-timer-timed-out-errors:   %u\n",
		pStats->SEC_failure_stats.DECO_watchdog_timer_timedout_errs);
	cmm_print(DEBUG_STDOUT, "\t Input frame read errors:                %u\n",
		pStats->SEC_failure_stats.input_frame_read_errs);
	cmm_print(DEBUG_STDOUT, "\t Protocol format errors:                 %u\n",
		pStats->SEC_failure_stats.protocol_format_errs);
	cmm_print(DEBUG_STDOUT, "\t IPSEC TTL 0 errors:                     %u\n",
		pStats->SEC_failure_stats.ipsec_ttl_zero_errs);
	cmm_print(DEBUG_STDOUT, "\t IPSEC pad check failures:               %u\n",
		pStats->SEC_failure_stats.ipsec_pad_chk_failures);
	cmm_print(DEBUG_STDOUT, "\t Output frame length rollovers:          %u\n",
		pStats->SEC_failure_stats.output_frame_length_rollover_errs);
	cmm_print(DEBUG_STDOUT, "\t TBL buffers too small errors:           %u\n",
		pStats->SEC_failure_stats.tbl_buff_too_small_errs);
	cmm_print(DEBUG_STDOUT, "\t TBL buffer pool depletion errors:       %u\n",
		pStats->SEC_failure_stats.tbl_buff_pool_depletion_errs);
	cmm_print(DEBUG_STDOUT, "\t Output frame too large errors:          %u\n",
		pStats->SEC_failure_stats.output_frame_too_large_errs);
	cmm_print(DEBUG_STDOUT, "\t Compound frame write errors:            %u\n",
		pStats->SEC_failure_stats.cmpnd_frame_write_errs);
	cmm_print(DEBUG_STDOUT, "\t Buffer too small errors:                %u\n",
		pStats->SEC_failure_stats.buff_too_small_errs);
	cmm_print(DEBUG_STDOUT, "\t Buffer pool depletion errors:           %u\n",
		pStats->SEC_failure_stats.buff_pool_depletion_errs);
	cmm_print(DEBUG_STDOUT, "\t Output frame write errors:              %u\n",
		pStats->SEC_failure_stats.output_frame_write_errs);
	cmm_print(DEBUG_STDOUT, "\t Compound frame read errors:             %u\n",
		pStats->SEC_failure_stats.cmpnd_frame_read_errs);
	cmm_print(DEBUG_STDOUT, "\t Preheader read errors:                  %u\n",
		pStats->SEC_failure_stats.prehdr_read_errs);
	cmm_print(DEBUG_STDOUT, "\t Errors other than above:                %u\n",
		pStats->SEC_failure_stats.other_errs);

	return CLI_OK;
}
#endif /* LS1043 */
/************************************************************
 **
 ** cmmSAQueryProcess
 **
 *************************************************************/


int cmmSAQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0,count=0,len=0;
	union u_rxbuf rxbuf;
	uint8_t *key_p;
	int temp_val,i;
#ifndef LS1043
	int key_size;
#endif
	short rc;
	char output_buf[256];
	char buf1[INET6_ADDRSTRLEN];
	char buf2[INET6_ADDRSTRLEN];

	memset(rxbuf.rcvBuffer, 0, CMM_BUF_SIZE);
	fpp_sa_query_cmd_t *pSAQuery = (fpp_sa_query_cmd_t *)rxbuf.rcvBuffer;

	rcvBytes = cmmSendToDaemon(daemon_handle,FPP_CMD_IPSEC_SA_ACTION_QUERY ,
								pSAQuery, sizeof(fpp_sa_query_cmd_t) , rxbuf.rcvBuffer);

	if (rcvBytes < sizeof(fpp_sa_query_cmd_t)  + sizeof(unsigned short)) {
		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
		if (rc == FPP_ERR_UNKNOWN_ACTION) {
			cmm_print(DEBUG_STDERR, "ERROR: doess not support ACTION_QUERY\n");
		} else if (rc == FPP_ERR_SA_ENTRY_NOT_FOUND) {
			cmm_print(DEBUG_STDERR, "ERROR: SA table empty\n");
		} else {
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
		}
		return CLI_OK;
	}

		cmm_print(DEBUG_STDOUT, "SA List:\n");

	do {
		len += sprintf(output_buf+len , "[%d] Handle : %x  SPI: %x ", 
		count,pSAQuery->handle, htonl(pSAQuery->spi) );
		len += sprintf(output_buf+len , "sa_type:  " );
		switch (pSAQuery->sa_type)
		{
			case 50:
				sprintf(output_buf+len , "ESP-Tunnel\n" );
				break;
			case 51:
				sprintf(output_buf+len , "AH\n" );
				break;
			default:
				sprintf(output_buf+len , "Unknown:%d\n", pSAQuery->sa_type);
				break;
		}

		cmm_print(DEBUG_STDOUT, "%s", output_buf);
		len = 0;
		cmm_print(DEBUG_STDOUT, "\n");
		len = sprintf(output_buf+len , "Source Addr:");
		if (pSAQuery->family == FPP_PROTO_IPV4)
		{
			len += sprintf(output_buf+len , "%s " , 
			inet_ntop(AF_INET, &pSAQuery->src_ip[0],buf1,INET_ADDRSTRLEN));
		}
	    else if(pSAQuery->family == FPP_PROTO_IPV6)
		{
			len += sprintf(output_buf+len , "%s " , 
			inet_ntop(AF_INET6, &pSAQuery->src_ip[0],buf1,INET6_ADDRSTRLEN));
		}
		else
			cmm_print(DEBUG_ERROR, "ERROR: Incorrect SA Family" );

		len += sprintf(output_buf+len , "Destination Addr:");
		if (pSAQuery->family == FPP_PROTO_IPV4)
		{
			len += sprintf(output_buf+len , "%s " , 
			inet_ntop(AF_INET, &pSAQuery->dst_ip[0],buf1,INET_ADDRSTRLEN));
		}
		else if(pSAQuery->family == FPP_PROTO_IPV6)
		{
			len += sprintf(output_buf+len , "%s " , 
			inet_ntop(AF_INET6, &pSAQuery->dst_ip[0],buf1,INET6_ADDRSTRLEN));
		}
		else
			cmm_print(DEBUG_ERROR, "ERROR: Incorrect SA Family" );

		len += sprintf (output_buf+len, " mtu :%d state: %d\n", pSAQuery->mtu , pSAQuery->state);	

		cmm_print(DEBUG_STDOUT, "%s",output_buf);
		len = 0;
		cmm_print(DEBUG_STDOUT, "\n");

		cmm_print(DEBUG_STDOUT, "Flags:%x Replay_window:%x\n", pSAQuery->flags,pSAQuery->replay_window);
		if (pSAQuery->replay_window)
			cmm_print(DEBUG_STDOUT, "ANTI REPLAY ENABLE");
		else
			cmm_print(DEBUG_STDOUT, "ANTI REPLAY DISABLE");

		len += sprintf(output_buf+len, "\nAuth Algorithm: ");  
		if (pSAQuery->auth_algo > SADB_AALG_MAX)
		{
			sprintf(output_buf + len, " Unknown %d", pSAQuery->auth_algo);
		}
		else
		{
			cmmGetAuthAlgoName(pSAQuery->auth_algo, ipsec_algo_name);
			sprintf(output_buf + len, " %s ", ipsec_algo_name );
		}
		cmm_print(DEBUG_STDOUT,"%s\n", output_buf);
		len = 0;
		
		if (pSAQuery->auth_algo)
		{
			len += sprintf(output_buf+len, "Auth Key : " );
			for (i = 0; i < (pSAQuery->auth_key_len/4); i++)
			{
				key_p = &pSAQuery->auth_key[i*4];
				temp_val = htonl(*(unsigned int*)key_p);
				len += sprintf (output_buf+len,"%08x", temp_val);
			}

			cmm_print(DEBUG_STDOUT, "%s",output_buf);
			len = 0;
#ifndef LS1043
			if (pSAQuery->auth_algo == 3)
			{
				len += sprintf(output_buf+len, "\nExt-Auth Key : " );
				key_size = sizeof(pSAQuery->ext_auth_key);
				for (i = 0; i < (key_size/4); i++)
				{
					key_p = &pSAQuery->ext_auth_key[i*4];
					temp_val =htonl(*(unsigned int*)key_p);
					len += sprintf (output_buf+len,"%08x", temp_val);
				}
				cmm_print(DEBUG_STDOUT,"%s", output_buf);
				len = 0;
				cmm_print(DEBUG_STDOUT, "\n");
			}
#endif
		}

		
		len += sprintf(output_buf+len, "\nCipher Algorithm: ");

		if (pSAQuery->cipher_algo > SADB_EALG_MAX)
		{
			sprintf(output_buf + len, " Unknown %d", pSAQuery->cipher_algo);
		}
		else
		{
			cmmGetCipherAlgoName(pSAQuery->cipher_algo, ipsec_algo_name);
			sprintf(output_buf + len, " %s ",ipsec_algo_name );
		}
		cmm_print(DEBUG_STDOUT,"%s", output_buf);
		len = 0;
		if (pSAQuery->cipher_algo)
		{
			len += sprintf(output_buf+len, "\nCipher Key : " );
			for (i = 0; i < (pSAQuery->cipher_key_len/4); i++)
			{
				key_p = &pSAQuery->cipher_key[i*4];
				temp_val =htonl(*(unsigned int*)key_p);
				len += sprintf (output_buf+len,"%08x", temp_val);
			}
			cmm_print(DEBUG_STDOUT,"%s", output_buf);
			len = 0;
			cmm_print(DEBUG_STDOUT, "\n");

		}
	
		if (pSAQuery->mode == 1 ) // SA_MODE_TUNNEL
		{
			cmm_print(DEBUG_STDOUT, "Tunnel details : " );
			if (pSAQuery->tunnel_proto_family == AF_INET)
			{
				cmm_print (DEBUG_STDOUT, 
					"IPV4: Dest Addr: %s SrcAddr :%s TOS: %x Protocol:%x Total Length :%x\n",
					 inet_ntop(AF_INET, &pSAQuery->tnl.ipv4.daddr,buf1,INET_ADDRSTRLEN),
					 inet_ntop(AF_INET, &pSAQuery->tnl.ipv4.saddr,buf2,INET_ADDRSTRLEN),
					 pSAQuery->tnl.ipv4.tos, pSAQuery->tnl.ipv4.protocol , 
					 pSAQuery->tnl.ipv4.total_length);	
			}
			else
			{
				cmm_print(DEBUG_STDOUT, 
					"IPV6:Dest Addr: %s SrcAddr :%s\n", 
					inet_ntop(AF_INET6, &pSAQuery->tnl.ipv6.daddr[0],buf1,INET6_ADDRSTRLEN),
					inet_ntop(AF_INET6, &pSAQuery->tnl.ipv6.saddr[0],buf2,INET6_ADDRSTRLEN));

				cmm_print (DEBUG_STDOUT, 
					"TrafficClassHi:%x Version:%x FlowLabelHi:%x TrafficClass:%x FlowLabelLo:%x \n", 
					pSAQuery->tnl.ipv6.traffic_class_hi,
					pSAQuery->tnl.ipv6.version,
					pSAQuery->tnl.ipv6.flow_label_high,
					pSAQuery->tnl.ipv6.traffic_class,
					pSAQuery->tnl.ipv6.flow_label_lo);
			}
		}

		cmm_print(DEBUG_STDOUT, "Life time details : " );
		cmm_print (DEBUG_STDOUT,"soft_byte_limit : %llx hard_byte_limit :%llx\n", 
		pSAQuery->soft_byte_limit,pSAQuery->hard_byte_limit); 

		cmm_print (DEBUG_STDOUT,"soft_packet_limit : %llx hard_packet_limit :%llx\n", 
		pSAQuery->soft_packet_limit,pSAQuery->hard_packet_limit); 

		count++; 
		cmm_print(DEBUG_STDOUT, "\n " );
		memset(rxbuf.rcvBuffer,0,256);
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_IPSEC_SA_ACTION_QUERY_CONT, pSAQuery, sizeof(fpp_sa_query_cmd_t) , rxbuf.rcvBuffer);
	}while(rcvBytes >= sizeof(fpp_sa_query_cmd_t) + sizeof(unsigned short));

	cmm_print(DEBUG_STDOUT, "Total SA Entries: %d\n", count);
	return CLI_OK;
}

#if defined (LS1043)
static void cmmSendSAExpireEvent(fpp_stat_ipsec_entry_response_t *pEntryResponse)
{
	char netlinkbuff[512]  __attribute__((aligned(RTA_ALIGNTO)));
	struct rtnl_handle rth;
	struct xfrm_user_expire *pUe;
	struct nlmsghdr *pNlh;
	struct xfrm_usersa_info  *pSa;
	int iRet = 0;

	if (cmm_nl_open(&rth, 0, NETLINK_XFRM) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: cmm_rtnl_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		return;
	}

	pNlh = (struct nlmsghdr *)netlinkbuff;
	cmm_nlh_init(pNlh, sizeof(struct xfrm_user_expire), XFRM_MSG_EXPIRE, NLM_F_REQUEST);
	pUe = NLMSG_DATA(pNlh);
	memset(pUe,0x0,sizeof(struct xfrm_user_expire));
	pSa = &pUe->state;

	if(pEntryResponse->family == FPP_PROTO_IPV4) {
		pSa->family =  (AF_INET);
		pUe->state.id.daddr.a4 = pEntryResponse->dst_ip[0];
	}
	else if(pEntryResponse->family == FPP_PROTO_IPV6) {
		pSa->family =  (AF_INET6);
		memcpy(&pUe->state.id.daddr.a6, &pEntryResponse->dst_ip[0], sizeof(pUe->state.id.daddr.a6));
	}
	else 
	{
		cmm_print(DEBUG_ERROR, "ERROR: Incorrect SA Family(0x%x). Not sending Expire event.", pEntryResponse->family );
		return;
	}
	pSa->id.spi = htonl(pEntryResponse->spi);
	pSa->id.proto = pEntryResponse->proto;

	pUe->hard = 0;/*hard 0 means rekey operation. */
	if ((iRet = cmm_rtnl_send(&rth, pNlh)) < 0)
	{
		cmm_print(DEBUG_ERROR, "cmm_rtnl_send failed(%d) in sequence ovwerflow check send message. \n", iRet);
		goto clean;
	}

clean:
	cmm_rtnl_close(&rth);
	return ;
}
#endif

void cmmDPDIPsecSAUpdate(struct cmm_ct *ctx)
{
	static unsigned int gDPDCurrAutoTimeout = 0;
	static time_t last_dpd = 0;
	double dt;
	time_t now;
	netkey_sa_update_cmd_t msg;
	unsigned short *payload;
	fpp_stat_ipsec_status_cmd_t ipsecStatusCmd;
	uint64_t total_bytes_transmitted_0={0};
	uint64_t total_bytes_transmitted_1={0};
	uint64_t total_bytes_transmitted={0};
	short ret = 1;
	union u_rxbuf rxbuf;
	unsigned short rcvBytes = 0;
	unsigned short fcode;
	unsigned short len = 0;
#if defined (LS1043)
	int iQueryTimerVal = 0;
#endif

	now = time(NULL);

	dt = now - last_dpd;

	gDPDCurrAutoTimeout += (unsigned int) dt;

#if defined (LS1043)
	if (gSAQueryTimerEnable)
		iQueryTimerVal = gSAQueryTimerVal;
	else
		iQueryTimerVal = SA_SEQNO_OVERFLOW_CHECK_TIMEOUT;
#endif

#if defined (LS1043)
	if (gDPDCurrAutoTimeout >= iQueryTimerVal)
#else
	if (gSAQueryTimerEnable)
#endif
	{
		fpp_stat_ipsec_entry_response_t *pEntryResponse = (fpp_stat_ipsec_entry_response_t *)rxbuf.rcvBuffer;
		ipsecStatusCmd.action = FPP_CMM_STAT_QUERY;
#if defined (LS1043)
		ipsecStatusCmd.iQueryTimerVal = iQueryTimerVal;
#endif
		/* Send CMD_STAT_IPSEC_STATUS command */
		ret = fci_write(ctx->fci_handle, FPP_CMD_STAT_IPSEC_STATUS, sizeof(ipsecStatusCmd), (unsigned short *) &ipsecStatusCmd);

		if ((ret != FPP_ERR_OK) || (ret < 0))
		{
			cmm_print(DEBUG_ERROR, "Error %d when sending FPP_CMD_STAT_IPSEC_STATUS \n", ret);
		}
		else
		{
			while (1)
			{
				ret = fci_query(ctx->fci_handle,FPP_CMD_STAT_IPSEC_ENTRY,0,NULL,
						&rcvBytes,(unsigned short *) (rxbuf.rcvBuffer + sizeof(ret)));
				rcvBytes += sizeof(ret);
				memcpy(rxbuf.rcvBuffer, &ret, sizeof(ret));
				if ((ret != FPP_ERR_OK) || (ret < 0))
				{
					cmm_print(DEBUG_ERROR, "Error %d when sending FPP_CMD_STAT_IPSEC_ENTRY \n", ret);
					break;
				}
				if ((rcvBytes) != sizeof(fpp_stat_ipsec_entry_response_t))
				{
					cmm_print(DEBUG_ERROR, "%s: wrong response length %d received from FPP for FPP_CMD_STAT_IPSEC_ENTRY\n", __func__, rcvBytes);
					break;
				}
				if (pEntryResponse->eof)
					break;

#if defined (LS1043)
				if (gSAQueryTimerEnable)
#endif
				{
					total_bytes_transmitted_0 = pEntryResponse->total_bytes_processed[0];
					total_bytes_transmitted_1 = pEntryResponse->total_bytes_processed[1];
					total_bytes_transmitted_1 <<= 32;

					total_bytes_transmitted = total_bytes_transmitted_0 | total_bytes_transmitted_1;

					memset(&msg, 0, sizeof(netkey_sa_update_cmd_t));
					msg.sagd = pEntryResponse->sagd;
					msg.packets = pEntryResponse->total_pkts_processed;
					msg.bytes = total_bytes_transmitted;

					fcode = NETKEY_CMD_SA_INFO_UPDATE;
					len = sizeof(msg);
					payload = (unsigned short *)&msg;
					cmmIPSectoKeyEngine(ctx->fci_key_handle, fcode, len, payload);
				}
#if defined (LS1043)
				if (pEntryResponse->seqOverflow)
					cmmSendSAExpireEvent(pEntryResponse);
#endif
			}
		}
		gDPDCurrAutoTimeout = 0;
	}
	last_dpd = now;
}

void cmmDPDSaQueryPrintHelp(int cmd_type)
{
	if (cmd_type == SAQUERY_UNKNOWN_CMD || cmd_type == SAQUERY_ENABLE_CMD)
	{
		cmm_print(DEBUG_STDOUT, "Usage: set sa_query_timer enable \n"
									"      set sa_query_timer disable \n");
	}
	if (cmd_type == SAQUERY_UNKNOWN_CMD || cmd_type == SAQUERY_TIMER_CMD)
	{
		cmm_print(DEBUG_STDOUT,
					"Usage: set sa_query_timer timer_value <time in seconds> \n");
	}
	if (cmd_type == SAQUERY_UNKNOWN_CMD)
	{
		cmm_print(DEBUG_STDOUT, "\n");
	}
}

int cmmDPDSaQuerySetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cmd_type = SAQUERY_UNKNOWN_CMD;
	int cpt = tabStart;
	char * endptr;
	unsigned int tmp;
	int rc;

	char sndBuffer[256];
	union u_rxbuf rxbuf;
	cmmd_saquery_timer_t* entryCmd = (cmmd_saquery_timer_t*) sndBuffer;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	cmm_print(DEBUG_INFO, "Entered DPD SA Query Set Process\n");

	if(!keywords[cpt])
		goto help;

	if( (strcasecmp(keywords[cpt], "enable") == 0) ||
	    (strcasecmp(keywords[cpt], "disable") == 0) )
	{
		cmd_type = SAQUERY_ENABLE_CMD;

		if(strcasecmp(keywords[cpt], "enable") == 0)
			entryCmd->action = CMMD_DPDSAQUERY_ACTION_ENABLE;
		else
			entryCmd->action = CMMD_DPDSAQUERY_ACTION_DISABLE;
	}
	else if(strcasecmp(keywords[cpt], "timer_value") == 0)
	{
		cmd_type = SAQUERY_TIMER_CMD;
		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) ||  (tmp < 5) || (tmp > MAX_QUERY_TIMER_VAL))
		{
			cmm_print(DEBUG_CRIT, "SA_Query_Timer ERROR: Timer Value must in the ragnge of 5-%d seconds \n",MAX_QUERY_TIMER_VAL);
			goto help;
		}

		entryCmd->action = CMMD_DPDSAQUERY_ACTION_SETTIMER;
		entryCmd->SaQueryTimerVal = tmp;
	}
	else
		goto keyword_error;

	rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPSEC_DPDSAQUERYTIMER, sndBuffer, sizeof(cmmd_saquery_timer_t), rxbuf.rcvBuffer);
	if(rc != 2)
	{
		if(rc >= 0)
			cmm_print(DEBUG_STDERR, "Unexpected response size for CMD_IPSEC_DPDSAQUERYTIMER: %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != CMMD_ERR_OK)
	{
		showErrorMsg("CMDD_DPDSAQUERYTIMER", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
		return -1;
	}

	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmDPDSaQueryPrintHelp(cmd_type);
	return -1;

}


int cmmDPDSAQUERYProcessClientCmd(u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len)
{
	cmmd_saquery_timer_t	*entryCmd = (cmmd_saquery_timer_t*) cmd_buf;

	cmm_print(DEBUG_INFO, "cmmDPDSAQUERYProcessClientCmd\n");

	res_buf[0] = CMMD_ERR_OK;
	*res_len = 2;

	switch (entryCmd->action) {
		case CMMD_DPDSAQUERY_ACTION_ENABLE:
			cmm_print(DEBUG_INFO, "cmmDPDSAQUERYProcessClientCmd- CMMD_DPDSAQUERY_ACTION_ENABLE\n");
			gSAQueryTimerEnable = 1;
			break;

		case CMMD_DPDSAQUERY_ACTION_DISABLE:
			cmm_print(DEBUG_INFO, "cmmDPDSAQUERYProcessClientCmd- CMMD_DPDSAQUERY_ACTION_ENABLE\n");
			gSAQueryTimerEnable = 0;
			break;

		case CMMD_DPDSAQUERY_ACTION_SETTIMER:
			cmm_print(DEBUG_INFO, "cmmDPDSAQUERYProcessClientCmd- CMMD_DPDSAQUERY_ACTION_SETTIMER\n");
	                gSAQueryTimerVal = entryCmd->SaQueryTimerVal;
			break;

		default:
			res_buf[0] = CMMD_ERR_UNKNOWN_ACTION;
			break;
	}
	return 0;
}

int cmmSaQueryTimerShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	if(gSAQueryTimerEnable)
		cli_print(cli, " The SA query timer is enabled: the current timer value is %d", gSAQueryTimerVal);
	else
		cli_print(cli, " The SA query timer is disabled: the current timer value is %d", gSAQueryTimerVal);

	return CLI_OK;
}
