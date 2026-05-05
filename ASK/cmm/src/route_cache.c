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
#include "route_cache.h"
#include "itf.h"
#include "ffbridge.h"

struct list_head rt_table[2 * ROUTE_HASH_TABLE_SIZE];
struct list_head rt_table_by_gw_ip[2 * NEIGHBOR_HASH_TABLE_SIZE];

struct list_head fpp_rt_table[ROUTE_HASH_TABLE_SIZE];

static u_int32_t route_ids[ROUTE_MAX_ID / (8 * sizeof(u_int32_t))] = {0, };
static u_int32_t route_id = 0;

pthread_mutex_t rtMutex = PTHREAD_MUTEX_INITIALIZER;		/*mutex to prevent race condition on the route table*/
extern unsigned short TunMtu;

static int cmmRouteNetlinkLookupFilter(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	struct RtEntry *route = arg;
	struct rtmsg *rtm;
	struct rtattr *attr[RTA_MAX + 1];

	if (nlh->nlmsg_type != RTM_NEWROUTE) {
			cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink message(%d)\n",
						 __func__, __LINE__, nlh->nlmsg_type);

		goto err;
	}

	rtm = NLMSG_DATA(nlh);

	cmm_parse_rtattr(attr, RTA_MAX, RTM_RTA(rtm), RTM_PAYLOAD(nlh));

	if (!attr[RTA_DST] || !attr[RTA_OIF]) {
		cmm_print(DEBUG_ERROR, "%s::%d: rtnetlink message missing mandatory attribute\n", __func__, __LINE__);
		goto err;
	}

	route->family = rtm->rtm_family;
	route->table = rtm->rtm_table;
	route->scope = rtm->rtm_scope;
	route->type = rtm->rtm_type;

	route->mtu = 0;
	if (attr[RTA_METRICS]) {
		struct rtattr *mxrta;

		mxrta = cmm_get_rtattr(RTA_DATA(attr[RTA_METRICS]), RTA_PAYLOAD(attr[RTA_METRICS]), RTAX_MTU);
		if (mxrta)
			route->mtu = *(unsigned int *)RTA_DATA(mxrta);
	}

	route->dAddrLen = RTA_PAYLOAD(attr[RTA_DST]);
	memcpy(route->dAddr, RTA_DATA(attr[RTA_DST]), route->dAddrLen);
	
	if(attr[RTA_PREFSRC]) {
		route->sAddrLen=RTA_PAYLOAD(attr[RTA_PREFSRC]);
		memcpy(route->sAddr, RTA_DATA(attr[RTA_PREFSRC]), route->sAddrLen);
	}

	if (attr[RTA_GATEWAY]) {
		route->gwAddrLen = RTA_PAYLOAD(attr[RTA_GATEWAY]);
		memcpy(route->gwAddr, RTA_DATA(attr[RTA_GATEWAY]), route->gwAddrLen);
	}
	else
	{
		route->gwAddrLen = route->dAddrLen;
		memcpy(route->gwAddr, route->dAddr, route->dAddrLen);
	}

	route->oifindex = *(int *)RTA_DATA(attr[RTA_OIF]);

	/* Always stop parsing on first match */
	return RTNL_CB_STOP;

err:
	return RTNL_CB_ERROR;
}

static int cmmRouteNetlinkLookup(struct flow *flow, struct RtEntry *route)
{
	struct rtnl_handle rth;
	int ipAddrLen = IPADDRLEN(flow->family);
	char iifname[IFNAMSIZ], oifname[IFNAMSIZ];
	char buf1[INET6_ADDRSTRLEN], buf2[INET6_ADDRSTRLEN], buf3[INET6_ADDRSTRLEN];
	char buf[256] __attribute__ ((aligned (4)));
	struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
	struct rtmsg *rtm;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (cmm_rtnl_open(&rth, 0) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: cmm_rtnl_open() failed, %s\n", __func__, __LINE__, strerror(errno));
		goto err0;
	}

	cmm_nlh_init(nlh, sizeof(struct rtmsg), RTM_GETROUTE, NLM_F_REQUEST);

	rtm = NLMSG_DATA(nlh);
	memset(rtm, 0, sizeof(struct rtmsg));
	rtm->rtm_family = flow->family;

	rtm->rtm_dst_len = ipAddrLen * 8;
	rtm->rtm_table = RT_TABLE_UNSPEC;
	rtm->rtm_protocol = RTPROT_UNSPEC;
	rtm->rtm_scope = RT_SCOPE_UNIVERSE;
	rtm->rtm_type = RTN_UNSPEC;

	cmm_addattr_l(nlh, sizeof(buf), RTA_DST, flow->dAddr, ipAddrLen);

	if (flow->sAddr)
	{
		cmm_addattr_l(nlh, sizeof(buf), RTA_SRC, flow->sAddr, ipAddrLen);
		rtm->rtm_src_len = ipAddrLen * 8;
	}
	else
		rtm->rtm_src_len = 0;

	if (!(flow->flow_flags & FLOWFLAG_SOCKET_ROUTE) && flow->iifindex)
		cmm_addattr_l(nlh, sizeof(buf), RTA_IIF, &flow->iifindex, sizeof(int));

        if (flow->fwmark)
		cmm_addattr_l(nlh, sizeof(buf), RTA_FWMARK, &flow->fwmark, sizeof(unsigned int));

	if (cmm_rtnl_send(&rth, nlh) < 0)
		goto err1;

	if (cmm_rtnl_listen(&rth, cmmRouteNetlinkLookupFilter, route) < 0)
		goto err1;

	route->fwmark = flow->fwmark;
	route->iifindex = flow->iifindex;
	route->underlying_iifindex = flow->underlying_iif;
#ifdef VLAN_FILTER
	route->underlying_vlan_id = flow->underlying_vlan_id;
#endif

	if (flow->sAddr)
	{
		route->sAddrLen = ipAddrLen;
		if(flow->sAddr[0])
			memcpy(route->sAddr, flow->sAddr, route->sAddrLen);
	}
	else
	{
		route->sAddrLen = 0;
	}

	cmm_print(DEBUG_INFO, "route entry: idev:%s saddr:%s fwmark:%08x daddr:%s gw:%s odev:%s mtu:%d\n",
						if_indextoname(route->iifindex, iifname),
						inet_ntop(route->family, route->sAddr, buf1, INET6_ADDRSTRLEN),
						route->fwmark,
						inet_ntop(route->family, route->dAddr, buf2, INET6_ADDRSTRLEN),
						inet_ntop(route->family, route->gwAddr, buf3, INET6_ADDRSTRLEN),
						if_indextoname(route->oifindex, oifname), route->mtu);

	cmm_rtnl_close(&rth);

	return 0;

err1:
	cmm_rtnl_close(&rth);

err0:
	return -1;
}

/*****************************************************************
* __cmmRouteFind
*
*
******************************************************************/
struct RtEntry *__cmmRouteFind(struct flow *flow)
{
	struct RtEntry *route;
	struct list_head *entry;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	int key;
	int ipAddrLen = IPADDRLEN(flow->family);

	cmm_print(DEBUG_INFO, "%s: Route(%s, %s)\n", __func__, inet_ntop(flow->family, flow->sAddr, sbuf, sizeof(sbuf)),
								inet_ntop(flow->family, flow->dAddr, dbuf, sizeof(dbuf)));

	key = HASH_RT(flow->family, flow->sAddr, flow->dAddr);

	entry = list_first(&rt_table[key]);
	while (entry != &rt_table[key])
	{
		route = container_of(entry, struct RtEntry, list);
		if (cmmRouteEqual(route, flow, ipAddrLen))
		{
			if (route->flags & INVALID)
				route = NULL;

			goto found;
		}

		entry = list_next(entry);
	}

	route = NULL;

found:
	return route;
}


static u_int32_t new_route_id(void)
{
	int offset, mask;

	for (;;)
	{
		if (++route_id >= ROUTE_MAX_ID)
			route_id = 1;

		offset = route_id / (8 * sizeof(u_int32_t));
		mask = 1 << (route_id & 0x1f);
		if (!(route_ids[offset] & mask))
			break;
	}

	route_ids[offset] |= mask;

	return route_id;
}

static void del_route_id(u_int32_t route_id)
{
	int offset = route_id / (8 * sizeof(u_int32_t));
	int mask = 1 << (route_id & 0x1f);

	route_ids[offset] &= ~mask;
}


/*****************************************************************
* __cmmRouteRemove
*
*
******************************************************************/

/* NOTE: The rtMutex must be locked by the caller of this routine. */

void __cmmRouteRemove(struct RtEntry *route)
{
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];

	cmm_print(DEBUG_INFO, "%s: Route(%s, %s) removed\n", __func__, inet_ntop(route->family, route->sAddr, sbuf, sizeof(sbuf)),
									inet_ntop(route->family, route->dAddr, dbuf, sizeof(dbuf)));

	if (!(route->flags & RT_POLICY))
		list_del(&route->list);

	list_del(&route->list_by_gw_ip);

	free(route);
}

/*****************************************************************
* __cmmRoutePut
*
*
******************************************************************/
void __cmmRoutePut(struct RtEntry *route)
{
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];

	cmm_print(DEBUG_INFO, "%s: Route(%s, %s) put\n", __func__, inet_ntop(route->family, route->sAddr, sbuf, sizeof(sbuf)),
								inet_ntop(route->family, route->dAddr, dbuf, sizeof(dbuf)));

	route->count--;

	if (route->count <= 0)
	{
		__cmmRouteRemove(route);
	}
}


/*****************************************************************
* __cmmRouteAdd
*
*
******************************************************************/
struct RtEntry *__cmmRouteAdd(struct flow *flow)
{
	struct RtEntry *route;
	char sbuf[INET6_ADDRSTRLEN], dbuf[INET6_ADDRSTRLEN];
	int key;
#ifdef SAM_LEGACY
	struct interface *itf = NULL;
#endif

	route = malloc(sizeof(struct RtEntry));
	if (!route)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
		goto err0;
	}

	memset(route, 0, sizeof(struct RtEntry));

	route->count = 0;

	/* Get Route information from kernel */
	if (cmmRouteNetlinkLookup(flow, route) < 0)
	{
		cmm_print(DEBUG_WARNING, "%s::%d: cmmRouteNetlinkLookup() failed\n", __func__, __LINE__);
		goto err1;
	}

#ifdef SAM_LEGACY
	if(((itf = __itf_get(route->oifindex)) != NULL) && (____itf_is_4o6_tunnel(itf)))
               route->mtu = TunMtu;
#endif

	key = HASH_RT(route->family, route->sAddr, route->dAddr);

	list_add(&rt_table[key], &route->list);

	key = HASH_NEIGHBOR(route->family, route->gwAddr);

	list_add(&rt_table_by_gw_ip[key], &route->list_by_gw_ip);

	cmm_print(DEBUG_INFO, "%s: Route(%s, %s) added\n", __func__, inet_ntop(route->family, route->sAddr, sbuf, sizeof(sbuf)),
								inet_ntop(route->family, route->dAddr, dbuf, sizeof(dbuf)));

	return route;

err1:
	free(route);

err0:
	return NULL;
}


/*****************************************************************
* __cmmRouteGet
*
*
******************************************************************/
struct RtEntry *__cmmRouteGet(struct flow *flow)
{
	struct RtEntry *route;

	route = __cmmRouteFind(flow);
	if (!route)
	{
		route = __cmmRouteAdd(flow);
		if (!route)
			goto err;
	}

	route->count++;

	return route;

err:
	return NULL;
}

/*****************************************************************
* __cmmFPPRouteFind
*
*
******************************************************************/
struct fpp_rt *__cmmFPPRouteFind(int oifindex, int iifindex, const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len)
{
	struct fpp_rt *route;
	char mac[MAC_ADDRSTRLEN];
	struct list_head *entry;
	int key;

	cmm_print(DEBUG_INFO, "%s: Route(%d, %s)\n", __func__, oifindex, mac_ntop(dst_mac, mac, MAC_ADDRSTRLEN));

	key = HASH_FPP_RT(oifindex, dst_mac);

	entry = list_first(&fpp_rt_table[key]);
	while (entry != &fpp_rt_table[key])
	{
		route = container_of(entry, struct fpp_rt, list);
		if (!memcmp(route->dst_mac, dst_mac, ETH_ALEN) && (route->oifindex == oifindex) && (route->iifindex == iifindex) &&
		    (route->mtu == mtu) && ((dst_addr_len == route->dst_addr_len) && (!dst_addr_len || !memcmp(route->dst_addr, dst_addr, dst_addr_len))))
		{
			goto found;
		}

		entry = list_next(entry);
	}

	route = NULL;

found:
	return route;
}

/*****************************************************************
* __cmmFPPRouteRemove
*
*
******************************************************************/

/* NOTE: The rtMutex must be locked by the caller of this routine. */

void __cmmFPPRouteRemove(struct fpp_rt *route)
{
	char mac[MAC_ADDRSTRLEN];

	cmm_print(DEBUG_INFO, "%s: Route(%d, %s) removed\n", __func__, route->oifindex, mac_ntop(route->dst_mac, mac, MAC_ADDRSTRLEN));

	list_del(&route->list);

	del_route_id(route->id);

	free(route);
}

/*****************************************************************
* __cmmFPPRoutePut
*
*
******************************************************************/
void __cmmFPPRoutePut(struct fpp_rt *route)
{
	char mac[MAC_ADDRSTRLEN];

	cmm_print(DEBUG_INFO, "%s: Route(%d, %s) put\n", __func__, route->oifindex, mac_ntop(route->dst_mac, mac, MAC_ADDRSTRLEN));

	route->count--;

	if (route->count <= 0)
	{
		__cmmFPPRouteRemove(route);
	}
}


/*****************************************************************
* __cmmFPPRouteAdd
*
*
******************************************************************/
struct fpp_rt *__cmmFPPRouteAdd(int oifindex, int iifindex, int underlying_iifindex, const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len)
{
	struct fpp_rt *route;
	char mac[MAC_ADDRSTRLEN];
	int key;

	route = malloc(sizeof(struct fpp_rt));
	if (!route)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
		return NULL;
	}

	memset(route, 0, sizeof(struct fpp_rt));

	route->count = 0;

	memcpy(route->dst_mac, dst_mac, ETH_ALEN);
	route->oifindex = oifindex;
	route->iifindex = iifindex;
	route->underlying_iifindex = underlying_iifindex;
	route->mtu = mtu;
	if (dst_addr_len)
	{
		route->dst_addr_len = dst_addr_len;
		memcpy(route->dst_addr, dst_addr, dst_addr_len);
	}

	/* For now just use the pointer as the unique id, should be improved later */
	route->id = new_route_id();

	key = HASH_FPP_RT(route->oifindex, route->dst_mac);

	list_add(&fpp_rt_table[key], &route->list);

	cmm_print(DEBUG_INFO, "%s: Route(%d/%d, %s) added\n", __func__, route->oifindex, route->iifindex, mac_ntop(route->dst_mac, mac, MAC_ADDRSTRLEN));

	return route;

}


/*****************************************************************
* __cmmFPPRouteGet
*
*
******************************************************************/
struct fpp_rt *__cmmFPPRouteGet(int oifindex, int iifindex, int underlying_iifindex, const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len)
{
	struct fpp_rt *route;

	route = __cmmFPPRouteFind(oifindex, iifindex, dst_mac, mtu, dst_addr, dst_addr_len);
	if (!route)
	{
		route = __cmmFPPRouteAdd(oifindex, iifindex, underlying_iifindex, dst_mac, mtu, dst_addr, dst_addr_len);
		if (!route)
			goto err;
	}

	route->count++;

	return route;

err:
	return NULL;
}

/*****************************************************************
* cmmFPPRtShow
*
*
******************************************************************/
int cmmFPPRtShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct fpp_rt *route;
	struct list_head *entry;
	int i, n;
	char mac_buf[MAC_ADDRSTRLEN], oifname[IFNAMSIZ];

	cli_print(cli, "FPP Route:");

	n = 0;
	for (i = 0; i < ROUTE_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&rtMutex);

		for (entry = list_first(&fpp_rt_table[i]); entry != &fpp_rt_table[i]; entry = list_next(entry))
		{
//			char daddr_buf[INET_ADDRSTRLEN];

			route = container_of(entry, struct fpp_rt, list);

//			inet_ntop(AF_INET, &route->dst_addr, daddr_buf, sizeof(daddr_buf));

			cli_print(cli, "OIf: %s, Mtu: %d, Mac: %s, Id: %d, Count: %d, Flags: %x",
								if_indextoname(route->oifindex, oifname),
								route->mtu,
								mac_ntop(route->dst_mac, mac_buf, sizeof(mac_buf)),
								route->id,
								route->count, route->flags);

			n++;
		}

		__pthread_mutex_unlock(&rtMutex);
	}

	if (n > 0)
		cli_print(cli, "%d FPP Routes printed\n", n);

	return CLI_OK;
}


/*****************************************************************
* cmmRtShow
*
*
******************************************************************/
int cmmRtShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct RtEntry *route;
	struct list_head *entry;
	int i, n;
	char iifname[IFNAMSIZ], oifname[IFNAMSIZ], phys_oifname[IFNAMSIZ];

	cli_print(cli, "IPv4 Route:");

	n = 0;
	for (i = 0; i < ROUTE_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&rtMutex);

		for (entry = list_first(&rt_table[i]); entry != &rt_table[i]; entry = list_next(entry))
		{
			char saddr_buf[INET_ADDRSTRLEN], daddr_buf[INET_ADDRSTRLEN], gw_buf[INET_ADDRSTRLEN];

			route = container_of(entry, struct RtEntry, list);

			inet_ntop(AF_INET, &route->sAddr, saddr_buf, sizeof(saddr_buf));
			inet_ntop(AF_INET, &route->dAddr, daddr_buf, sizeof(daddr_buf));
			inet_ntop(AF_INET, &route->gwAddr, gw_buf, sizeof(gw_buf));

			cli_print(cli, "IIf: %s, Mark: %08x, Src: %s, Dst: %s --> Gateway: %s, OIf: %s, PhysOif: %s, Count: %d",
								if_indextoname(route->iifindex, iifname), route->fwmark,
								saddr_buf, daddr_buf, gw_buf, if_indextoname(route->oifindex, oifname),
								if_indextoname(route->phys_oifindex, phys_oifname), route->count);

			n++;
		}

		__pthread_mutex_unlock(&rtMutex);
	}

	if (n > 0)
		cli_print(cli, "%d IPv4 Routes printed\n", n);

	cli_print(cli, "IPv6 Route:");

	n = 0;
	for (i = ROUTE_HASH_TABLE_SIZE; i < 2 * ROUTE_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&rtMutex);
		for (entry = list_first(&rt_table[i]); entry != &rt_table[i]; entry = list_next(entry))
		{
			char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN], gw_buf[INET6_ADDRSTRLEN];

			route = container_of(entry, struct RtEntry, list);

			inet_ntop(AF_INET6, route->sAddr, saddr_buf, sizeof(saddr_buf));
			inet_ntop(AF_INET6, route->dAddr, daddr_buf, sizeof(daddr_buf));
			inet_ntop(AF_INET6, route->gwAddr, gw_buf, sizeof(gw_buf));

			cli_print(cli, "IIf: %s, Mark: %08x, Src: %s, Dst: %s --> Gateway: %s, Oif: %s, PhysOif: %s, Count: %d",
								if_indextoname(route->iifindex, iifname), route->fwmark,
								saddr_buf, daddr_buf, gw_buf, if_indextoname(route->oifindex, oifname),
								if_indextoname(route->phys_oifindex, phys_oifname), route->count);

			n++;
		}

		__pthread_mutex_unlock(&rtMutex);
	}

	if (n > 0)
		cli_print(cli, "%d IPv6 Routes printed\n", n);

	return CLI_OK;
}

/*****************************************************************
* __cmmCtTunnelRouteUpdate
*
*
******************************************************************/
static void __cmmCtTunnelRouteUpdate(FCI_CLIENT *fci_handle, struct ctTable *ctEntry, struct RtEntry *route, int dir)
{
	struct ct_route rt;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (dir == ORIGINATOR)
		rt = ctEntry->orig_tunnel;
	else
		rt = ctEntry->rep_tunnel;

	if (route->flags & INVALID)
	{
		if (dir == ORIGINATOR)
		{
			ctEntry->orig_tunnel.route = NULL;
			ctEntry->orig_tunnel.fpp_route = NULL;

			list_del(&ctEntry->list_by_orig_tunnel_route);
		}
		else
		{
			ctEntry->rep_tunnel.route = NULL;
			ctEntry->rep_tunnel.fpp_route = NULL;

			list_del(&ctEntry->list_by_rep_tunnel_route);
		}
	}
	else
	{
		rt.route = NULL;

		if (dir == ORIGINATOR)
			ctEntry->orig_tunnel.fpp_route = NULL;
		else
			ctEntry->rep_tunnel.fpp_route = NULL;
	}

	____cmmCtRegister(fci_handle, ctEntry);

	if (dir == ORIGINATOR)
	 	__cmmRouteDeregister(fci_handle, &rt, "originator tunnel");
	else
		__cmmRouteDeregister(fci_handle, &rt, "replier tunnel");
}

/*****************************************************************
* __cmmCtRouteUpdate
*
*
******************************************************************/
static void __cmmCtRouteUpdate(FCI_CLIENT *fci_handle, struct ctTable *ctEntry, struct RtEntry *route, int dir)
{
	struct ct_route rt;
	struct ct_route tunnel_rt;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (dir == ORIGINATOR)
	{
		rt = ctEntry->orig;
		tunnel_rt = ctEntry->orig_tunnel;

		if (ctEntry->orig_tunnel.route)
		{
			ctEntry->orig_tunnel.route = NULL;
			ctEntry->orig_tunnel.fpp_route = NULL;
			list_del(&ctEntry->list_by_orig_tunnel_route);
		}
	}
	else
	{
		rt = ctEntry->rep;
		tunnel_rt = ctEntry->rep_tunnel;

		if (ctEntry->rep_tunnel.route)
		{			
			ctEntry->rep_tunnel.route = NULL;
			ctEntry->rep_tunnel.fpp_route = NULL;
			list_del(&ctEntry->list_by_rep_tunnel_route);
		}
	}

	if (route->flags & INVALID)
	{
		if (dir == ORIGINATOR)
		{
			ctEntry->orig.route = NULL;
			ctEntry->orig.fpp_route = NULL;
		}
		else
		{
			ctEntry->rep.route = NULL;
			ctEntry->rep.fpp_route = NULL;
		}
	}
	else
	{
		rt.route = NULL;

		if (dir == ORIGINATOR)
			ctEntry->orig.fpp_route = NULL;
		else
			ctEntry->rep.fpp_route = NULL;
	}

	____cmmCtRegister(fci_handle, ctEntry);

	if (dir == ORIGINATOR)
	{
		__cmmRouteDeregister(fci_handle, &rt, "originator");
		__cmmRouteDeregister(fci_handle, &tunnel_rt, "originator tunnel");
	}
	else
	{
		__cmmRouteDeregister(fci_handle, &rt, "replier");
		__cmmRouteDeregister(fci_handle, &tunnel_rt, "replier tunnel");
	}
}

/*****************************************************************
* __cmmTunnelRouteUpdate
*
*
******************************************************************/
static void __cmmTunnelRouteUpdate(FCI_CLIENT *fci_handle, struct interface *itf, struct RtEntry *route)
{
	struct ct_route rt = itf->rt;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (route->flags & INVALID)
	{
		itf->rt.route = NULL;
		itf->rt.fpp_route = NULL;
	}
	else
	{
		rt.route = NULL;
		itf->rt.fpp_route = NULL;
	}

	__tunnel_add(fci_handle, itf);

	__cmmRouteDeregister(fci_handle, &rt, "tunnel");
}


/*****************************************************************
* __cmmSocketRouteUpdate
*
*
******************************************************************/
static void __cmmSocketRouteUpdate(FCI_CLIENT *fci_handle, struct socket *s, struct RtEntry *route)
{
	struct ct_route rt = s->rt;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (route->flags & INVALID)
	{
		s->rt.route = NULL;
		s->rt.fpp_route = NULL;
	}
	else
	{
		rt.route = NULL;
		s->rt.fpp_route = NULL;
	}

	__pthread_mutex_lock(&socket_lock);
	__socket_open(fci_handle, s);
	__pthread_mutex_unlock(&socket_lock);

	__cmmRouteDeregister(fci_handle, &rt, "socket");
}


/*****************************************************************
* __cmmRouteUpdate
*
*
******************************************************************/
void __cmmRouteUpdate(FCI_CLIENT *fci_handle, struct RtEntry *route)
{
	struct RtEntry route_prev;
	struct ctTable *ctEntry;
	struct interface *itf;
	struct socket *s;
	struct list_head *entry;
	struct NeighborEntry *neigh = NULL;
	struct flow flow = {
			.family = route->family,
			.sAddr = route->sAddr,
			.dAddr = route->dAddr,
			.fwmark = route->fwmark,
			.iifindex = route->iifindex,
#ifdef VLAN_FILTER
			.underlying_vlan_id = route->underlying_vlan_id,
#endif
#ifdef LS1043 
			.underlying_iif = route->underlying_iifindex,
#endif
			.flow_flags = route->flow_flags,
		};
	int key;
	int i;

	cmm_print(DEBUG_INFO, "%s: Update route entry\n", __func__);

	memcpy(&route_prev, route, sizeof(struct RtEntry));

	if (cmmRouteNetlinkLookup(&flow, route) < 0)
	{
		cmm_print(DEBUG_INFO, "%s::%d: route was removed\n", __func__, __LINE__);

		route->flags |= INVALID;

		goto update;
	}
	
	if (memcmp(route->gwAddr, route_prev.gwAddr, route->gwAddrLen))
	{
		cmm_print(DEBUG_INFO, "%s::%d: route changed gateway address\n", __func__, __LINE__);

		route->phys_oifindex = 0;

		list_del(&route->list_by_gw_ip);

		key = HASH_NEIGHBOR(route->family, route->gwAddr);

		list_add(&rt_table_by_gw_ip[key], &route->list_by_gw_ip);

		neigh = route->neighEntry;
		route->neighEntry = NULL;

		goto update;
	}

	if (route->oifindex != route_prev.oifindex)
	{
		cmm_print(DEBUG_INFO, "%s::%d: route changed output interface\n", __func__, __LINE__);

		route->phys_oifindex = 0;

		neigh = route->neighEntry;
		route->neighEntry = NULL;

		goto update;
	}
#ifdef LS1043
	else
	{
		if (__itf_is_bridge(route->oifindex))
		{
			/* Need to check how to get phys_oifindex to minimize updates */
			/*if (route->phys_oifindex != route_prev.phys_oifindex)*/
			cmm_print(DEBUG_INFO, "%s::%d: route is for bridge interface %d - %d\n", __func__, __LINE__, route->phys_oifindex, route_prev.phys_oifindex);
			{
				route->phys_oifindex = 0;

				neigh = route->neighEntry;
				route->neighEntry = NULL;
				goto update;
			}
		}
	}
#endif

	if (route->mtu != route_prev.mtu)
	{
		cmm_print(DEBUG_INFO, "%s::%d: route changed mtu\n", __func__, __LINE__);

		goto update;
	}

	return;

update:
	key = HASH_RT(route->family, route->sAddr, route->dAddr);

	entry = list_first(&ct_table_by_orig_route[key]);
	while (entry != &ct_table_by_orig_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_orig_route);
		entry = list_next(entry);
		if (ctEntry->orig.route == route)
			__cmmCtRouteUpdate(fci_handle, ctEntry, route, ORIGINATOR);
	}

	entry = list_first(&ct_table_by_rep_route[key]);
	while (entry != &ct_table_by_rep_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_rep_route);
		entry = list_next(entry);
		if (ctEntry->rep.route == route)
			__cmmCtRouteUpdate(fci_handle, ctEntry, route, REPLIER);
	}

	/* Conntracks pointing to tunnel interface */
	entry = list_first(&ct_table_by_orig_tunnel_route[key]);
	while (entry != &ct_table_by_orig_tunnel_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_orig_tunnel_route);
		entry = list_next(entry);
		if (ctEntry->orig_tunnel.route == route)
			__cmmCtTunnelRouteUpdate(fci_handle, ctEntry, route, ORIGINATOR);
	}

	entry = list_first(&ct_table_by_rep_tunnel_route[key]);
	while (entry != &ct_table_by_rep_tunnel_route[key])
	{
		ctEntry = container_of(entry, struct ctTable, list_by_rep_tunnel_route);
		entry = list_next(entry);
		if (ctEntry->rep_tunnel.route == route)
			__cmmCtTunnelRouteUpdate(fci_handle, ctEntry, route, REPLIER);
	}

	/* Tunnel interfaces */
	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);

			if (!__itf_is_tunnel(itf))
				continue;

			if (itf->rt.route == route)
				__cmmTunnelRouteUpdate(fci_handle, itf, route);
		}
	}

	/* Sockets */
	for (i = 0; i < HASH_SOCKET_SIZE; i++)
	{
		for (entry = list_first(&socket_table[i]); entry != &socket_table[i]; entry = list_next(entry))
		{
			s = container_of(entry, struct socket, list);

			if (s->rt.route == route)
				__cmmSocketRouteUpdate(fci_handle, s, route);
		}
	}

	/* Check for SAs which are affected with route */
	 __cmmSAUpdateWithRoute(fci_handle, route);

	if (neigh)
		__cmmNeighPut(neigh);
}

/*****************************************************************
* cmmRouteFlushCache 
*
*
******************************************************************/
static void cmmRouteFlushCache(int family)
{
	int fd;
	const char buf[] = "0";

	if (family == AF_INET)
		fd = open("/proc/sys/net/ipv4/route/flush", O_WRONLY);
	else
		fd = open("/proc/sys/net/ipv6/route/flush", O_WRONLY);

	if (fd < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: open() failed %s\n", __func__, strerror(errno));	
		goto out;
	}

	if(write(fd, buf, sizeof(buf)) == -1)
		cmm_print(DEBUG_ERROR, "%s: open() failed %s\n", __func__, strerror(errno));	

	close(fd);

out:
	return;
}

static int __cmmRouteIsTnlItf(int family, const unsigned int* daddr, struct interface* itf, int prefix_match, int prefix_len)
{
	unsigned int* tunnel_daddr;
	int addr_len = IPADDRLEN(family);

	if (!__itf_is_tunnel(itf))
		goto out;

	if (itf->rt.route)
		goto out;

	if (itf->tunnel_family != family)
		goto out;

	if (itf->tunnel_family == AF_INET)
		tunnel_daddr = &itf->tunnel_parm4.iph.daddr;
	else
		tunnel_daddr = itf->tunnel_parm6.raddr.s6_addr32;

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

static int __cmmRouteIsTnlConn (int family, const unsigned int* daddr,
				int dir, struct ctTable* ctEntry,
				int prefix_match, int prefix_len)
{
	struct interface* itf;
	unsigned int tunnel_daddr[4];
	int addr_len = IPADDRLEN(family);
	int rc = 0;
	struct RtEntry* route;
	unsigned short dport;

	/* Conntracks pointing to a tunnel interface */
	if (dir & ORIGINATOR)
		route = ctEntry->orig.route;
	else
		route = ctEntry->rep.route;

	itf = __itf_get(route->oifindex);
	if (!itf)
		goto out;

	if (itf->tunnel_family != family)
		goto free_itf;

	if (family == AF_INET)
	{
		if (!____itf_is_floating_sit_tunnel(itf))
			goto free_itf;

		tunnel_daddr[0] = tunnel_get_ipv4_dst(route, itf);
		if (!tunnel_daddr[0])
			goto free_itf;

		if (prefix_match)
		{
			if (cmmPrefixEqual(&tunnel_daddr[0], daddr, prefix_len))
				rc = 1;
		}
		else
		{
			if (tunnel_daddr[0] == *daddr)
				rc = 1;
		}

	}
	else /* AF_INET6 */
	{
		if (!____itf_is_4o6_tunnel(itf))
			goto free_itf;

		if (dir & ORIGINATOR)
			dport = nfct_get_attr_u16(ctEntry->ct, ATTR_ORIG_PORT_DST);
		else
			dport = nfct_get_attr_u16(ctEntry->ct, ATTR_REPL_PORT_DST);

		if(getTunnel4rdAddress(itf, tunnel_daddr, *daddr , dport) < 0)
			goto free_itf;

		if (prefix_match)
		{
			if (cmmPrefixEqual(tunnel_daddr, daddr, prefix_len))
				rc = 1;
		}
		else
		{
			if (!memcmp(daddr, tunnel_daddr, addr_len))
				rc = 1;
		}
	}

free_itf:
	__itf_put(itf);
out:
	return rc;
}

/*****************************************************************
 * __cmmRouteLocalNew
 *
 *
 ******************************************************************/
void __cmmRouteLocalNew(FCI_CLIENT *fci_handle, struct ctTable* localctEntry)
{
	struct nf_conntrack *ct = localctEntry->ct;
	struct ctTable* ctEntry;
	struct list_head *entry;
	struct interface *itf;
	int i;
	const unsigned int* daddr = NULL;

	if (localctEntry->family == AF_INET)
	{
		if (localctEntry->flags & LOCAL_CONN_ORIG)
			daddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
		else
			daddr = nfct_get_attr(ct, ATTR_REPL_IPV4_DST);
	}
	else
	{
		if (localctEntry->flags & LOCAL_CONN_ORIG)
			daddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_DST);
		else
			daddr = nfct_get_attr(ct, ATTR_REPL_IPV6_DST);
	}

	/* Look for connections waiting for a route */
	for (i = 0; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&ct_table[i]); entry != &ct_table[i]; entry = list_next(entry))
		{
			ctEntry = container_of(entry, struct ctTable, list);

			if (ctEntry->flags & LOCAL_CONN)
				continue;

			if ((ctEntry->dir & ORIGINATOR) && ctEntry->orig.route &&
					!ctEntry->orig_tunnel.route)
			{
				if (__cmmRouteIsTnlConn(localctEntry->family, daddr, ORIGINATOR, ctEntry, 0, 0))
				{
					____cmmCtRegister(fci_handle, ctEntry);
				}
			}

			if ((ctEntry->dir & REPLIER) && ctEntry->rep.route &&
			    !ctEntry->rep_tunnel.route)
			{
				if (__cmmRouteIsTnlConn(localctEntry->family, daddr, REPLIER, ctEntry, 0, 0))
				{
					____cmmCtRegister(fci_handle, ctEntry);
				}
			}
		}
	}

	/* Look for tunnels waiting for a route */
	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);
			if(__cmmRouteIsTnlItf(localctEntry->family, daddr, itf,0,0))
				 __tunnel_add(fci_handle, itf);
		}

	}
}

/*****************************************************************
 * __cmmRouteNew
 *
 *
 ******************************************************************/
static void __cmmRouteNew(FCI_CLIENT *fci_handle, struct rtmsg *rtm, unsigned int *dAddr, int flushed)
{
	struct list_head *entry;
	struct ctTable *ctEntry;
	struct SATable *s;
	struct interface *itf;
	const unsigned int *ct_daddr;
	int i;
	struct socket *soc;

	/* Look for connections waiting for a route */
	for (i = 0; i < CONNTRACK_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&ct_table[i]); entry != &ct_table[i]; entry = list_next(entry))
		{
			ctEntry = container_of(entry, struct ctTable, list);

			if (ctEntry->family != rtm->rtm_family)
				goto tunnel_originator;

			if ((ctEntry->dir & ORIGINATOR) && (!ctEntry->orig.route))
			{
				if (ctEntry->family == AF_INET)
					ct_daddr = nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV4_SRC);
				else
					ct_daddr = nfct_get_attr(ctEntry->ct, ATTR_REPL_IPV6_SRC);

				if (cmmPrefixEqual(ct_daddr, dAddr, rtm->rtm_dst_len))
				{
					if (!flushed)
					{
						cmmRouteFlushCache(rtm->rtm_family);
						flushed = 1;
					}

					____cmmCtRegister(fci_handle, ctEntry);
				}
			}

			if ((ctEntry->dir & REPLIER) && (!ctEntry->rep.route))
			{
				if (ctEntry->family == AF_INET)
					ct_daddr = nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV4_SRC);
				else
					ct_daddr = nfct_get_attr(ctEntry->ct, ATTR_ORIG_IPV6_SRC);

				if (cmmPrefixEqual(ct_daddr, dAddr, rtm->rtm_dst_len))
				{
					if (!flushed)
					{
						cmmRouteFlushCache(rtm->rtm_family);
						flushed = 1;
					}

					____cmmCtRegister(fci_handle, ctEntry);
				}
			}

		tunnel_originator:
			/* Conntracks pointing to a tunnel interface */
			if ((ctEntry->dir & ORIGINATOR) && ctEntry->orig.route &&
			    !ctEntry->orig_tunnel.route)
			{
				if (__cmmRouteIsTnlConn(ctEntry->family, dAddr, ORIGINATOR, ctEntry, 1, rtm->rtm_dst_len ))
				{
					if (!flushed)
					{
						cmmRouteFlushCache(rtm->rtm_family);
						flushed = 1;
					}

					____cmmCtRegister(fci_handle, ctEntry);
				}
			}

/*		tunnel_replier:*/
			if ((ctEntry->dir & REPLIER) && ctEntry->rep.route &&
			    !ctEntry->rep_tunnel.route)
			{
				if (__cmmRouteIsTnlConn(ctEntry->family, dAddr, REPLIER, ctEntry, 1, rtm->rtm_dst_len ))
				{
					if (!flushed)
					{
						cmmRouteFlushCache(rtm->rtm_family);
						flushed = 1;
					}

					____cmmCtRegister(fci_handle, ctEntry);
				}
			}
		}
	}
	
	/* Look for tunnels waiting for a route */
	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
	{
		for (entry = list_first(&itf_table.hash[i]); entry != &itf_table.hash[i]; entry = list_next(entry))
		{
			itf = container_of(entry, struct interface, list);
			if(__cmmRouteIsTnlItf(itf->tunnel_family, dAddr, itf, 1, rtm->rtm_dst_len))
			{
				if (!flushed)
				{
					cmmRouteFlushCache(rtm->rtm_family);
					flushed = 1;
				}

				__tunnel_add(fci_handle, itf);
			}
		}
	}

	/* Look for SA's waiting for a route */
	for (i = 0; i < SA_HASH_TABLE_SIZE; i++)
        {
                for (entry = list_first(&sa_table[i]); entry != &sa_table[i]; entry = list_next(entry))
                {
                        s = container_of(entry, struct SATable, list_by_h);
                        if(__cmmRouteIsSA(s->SAInfo.proto_family, dAddr, s, 1, rtm->rtm_dst_len))
                        {
                                if (!flushed)
                                {
                                        cmmRouteFlushCache(rtm->rtm_family);
                                        flushed = 1;
                                }

                                __pthread_mutex_lock(&sa_lock);
                                __cmmSATunnelRegister(fci_handle, s);
                                __pthread_mutex_unlock(&sa_lock);

                        }
                }
        }

	for (i = 0; i < HASH_SOCKET_SIZE; i++)
	{
		for (entry = list_first(&socket_table[i]); entry != &socket_table[i]; entry = list_next(entry))
		{
			soc = container_of(entry, struct socket, list);

			if (!soc->rt.route && soc->type == CMMD_SOCKET_TYPE_LANWAN)
			{
				if (!flushed)
				{
					cmmRouteFlushCache(rtm->rtm_family);
					flushed = 1;
				}

				__pthread_mutex_lock(&socket_lock);
				__socket_open(fci_handle, soc);
				__pthread_mutex_unlock(&socket_lock);
			}
		}
	}

}


/*****************************************************************
* cmmRtnlRule
* 
*
******************************************************************/
int cmmRtnlRule(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct fib_rule_hdr *frh;
	struct rtattr *tb[FRA_MAX + 1];

	switch (nlh->nlmsg_type) {
	case RTM_NEWRULE:
	case RTM_DELRULE:
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported RULE netlink message %x\n", __func__, nlh->nlmsg_type);
		goto out;
		break;
	}

	frh = NLMSG_DATA(nlh);

	cmm_parse_rtattr(tb, FRA_MAX, FRA_RTA(frh), FRA_PAYLOAD(nlh));

	if (nlh->nlmsg_type == RTM_NEWRULE)
	{
		cmm_print(DEBUG_INFO, "%s: RTM_NEWRULE\n", __func__);
	}
	else
	{
		cmm_print(DEBUG_INFO, "%s: RTM_DELRULE\n", __func__);
	}

out:
	return RTNL_CB_CONTINUE;
}

static unsigned int default_route[4] = { 0, };

/*****************************************************************
* cmmRtnlRoute
* 
*
******************************************************************/
int cmmRtnlRoute(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct cmm_ct *ctx = arg;
	struct rtmsg *rtm;
	struct rtattr *tb[RTA_MAX + 1];
	unsigned int *dAddr, *gwAddr;
	char dst[INET6_ADDRSTRLEN];
	char gateway[INET6_ADDRSTRLEN];
	struct list_head *entry, *next_entry;
	struct RtEntry *route;
	int flushed = 1;
	int i;

	switch (nlh->nlmsg_type)
	{
	case RTM_NEWROUTE:
	case RTM_DELROUTE:
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported ROUTE netlink message %x\n", __func__, nlh->nlmsg_type);
		goto out;
		break;
	}

	rtm = NLMSG_DATA(nlh);

	cmm_print(DEBUG_INFO, "%s: rtmsg family: %x, dst_len: %d, src_len: %d, tos: %x, table: %d, protocol: %d, scope: %d, type: %d, flags: %x\n", __func__,
					rtm->rtm_family, rtm->rtm_dst_len,
					rtm->rtm_src_len, rtm->rtm_tos, rtm->rtm_table,
					rtm->rtm_protocol, rtm->rtm_scope,
					rtm->rtm_type, rtm->rtm_flags);

	/* Don't process local routes */
	if (rtm->rtm_table == RT_TABLE_LOCAL)
		goto out;

	if (rtm->rtm_scope >= RT_SCOPE_HOST)
		goto out;

	/* Don't process multicast, broadcast routes */
	if (rtm->rtm_type != RTN_UNICAST)
		goto out;

	cmm_parse_rtattr(tb, RTA_MAX, RTM_RTA(rtm), RTM_PAYLOAD(nlh));

	if (!tb[RTA_DST])
	{
		if (rtm->rtm_dst_len)
		{
			cmm_print(DEBUG_ERROR, "%s: route missing destination address\n", __func__);
			goto out;
		}

		dAddr = default_route;
	}
	else
		dAddr = RTA_DATA(tb[RTA_DST]);

	if (tb[RTA_GATEWAY])
		gwAddr = RTA_DATA(tb[RTA_GATEWAY]);
	else
		gwAddr = dAddr;

	if (nlh->nlmsg_type == RTM_NEWROUTE)
	{
		cmm_print(DEBUG_INFO, "%s: RTM_NEWROUTE %s/%d %s\n", __func__, inet_ntop(rtm->rtm_family, dAddr, dst, sizeof(dst)), rtm->rtm_dst_len, inet_ntop(rtm->rtm_family, gwAddr, gateway, sizeof(gateway)));
	}
	else
	{
		cmm_print(DEBUG_INFO, "%s: RTM_DELROUTE %s/%d %s\n", __func__, inet_ntop(rtm->rtm_family, dAddr, dst, sizeof(dst)), rtm->rtm_dst_len, inet_ntop(rtm->rtm_family, gwAddr, gateway, sizeof(gateway)));
	}

	/* Ignore all route cache events */
	if(rtm->rtm_flags & RTM_F_CLONED)
		goto out;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	/* Don't flush the route cache if the event is generated by the cache itself  */
	/* Route cache events are now disabled */

	flushed = 0;

	/* Look for connections/tunnels using this route */
	for (i = 0; i < ROUTE_HASH_TABLE_SIZE * 2; i++)
	{
		for (entry = list_first(&rt_table[i]); next_entry = list_next(entry), entry != &rt_table[i]; entry = next_entry)
		{
			route = container_of(entry, struct RtEntry, list);

			if (route->family != rtm->rtm_family)
				continue;

			if (cmmPrefixEqual(route->dAddr, dAddr, rtm->rtm_dst_len))
			{
				if (!flushed)
				{
					cmmRouteFlushCache(rtm->rtm_family);
					flushed = 1;
				}

				__cmmRouteUpdate(ctx->fci_handle, route);
			}
		}
	}

	if (nlh->nlmsg_type == RTM_DELROUTE)
		goto unlock;

	__cmmRouteNew(ctx->fci_handle, rtm, dAddr, flushed);

unlock:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

out:
	return RTNL_CB_CONTINUE;
}
