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
/* Include cmm.h first to ensure musl headers set guards before kernel headers (musl compat) */
#include "cmm.h"
#include <libnetfilter_conntrack/libnetfilter_conntrack.h>
#include <libnetfilter_conntrack/libnetfilter_conntrack_tcp.h>


#undef NLMSG_TAIL
#include <net/if.h>
#include <net/if_arp.h>
#include <linux/if_packet.h>
/* linux/if_ether.h removed - ethhdr already defined via cmm.h -> net/ethernet.h */
#include <netinet/icmp6.h>
#include "itf.h"
#include "forward_engine.h"
#include "neighbor_resolution.h"

extern pthread_mutex_t tnlMutex;

struct list_head neigh_table[2 * NEIGHBOR_HASH_TABLE_SIZE];
struct list_head neigh_table_by_mac[NEIGHBOR_HASH_TABLE_SIZE];
struct list_head neigh_state_table;

pthread_mutex_t neighMutex = PTHREAD_MUTEX_INITIALIZER;

static struct neighReq *neighReqHead = NULL;


static int cmmNeighborNetlinkLookupAllFilter(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	int ifindex = *(int *)arg;
	struct NeighborEntry *neigh;
	struct ndmsg *ndm;
	struct rtattr *attr;

	if (nlh->nlmsg_type != RTM_NEWNEIGH) {
		cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink message(%d)\n",
					 __func__, __LINE__, nlh->nlmsg_type);

		goto out;
	}

	ndm = NLMSG_DATA(nlh);

	if (ndm->ndm_state & NUD_NOARP)
		goto out;

	if (ndm->ndm_ifindex != ifindex)
		goto out;

	attr = cmm_get_rtattr(NDA_RTA(ndm), NDA_PAYLOAD(nlh), NDA_DST);
	if (!attr) {
		cmm_print(DEBUG_ERROR, "%s::%d: rtnetlink message missing daddr\n", __func__, __LINE__);
		goto out;
	}

	/* If an entry already exists then skip update,
	it will be done through the listner thread. This is to avoid calling the ARP/Neighbor state machine from here */
	neigh = __cmmNeighFind(ndm->ndm_family, RTA_DATA(attr), ifindex);
	if ((!neigh) && !itf_match_src_ipaddr(ifindex, ndm->ndm_family, RTA_DATA(attr))){
		neigh = __cmmNeighAdd(ndm->ndm_family, RTA_DATA(attr), ifindex);
		if (!neigh) {
			cmm_print(DEBUG_ERROR, "%s::%d: __cmmNeighAdd() failed\n", __func__, __LINE__);
			goto out;
		}
	}
	else
		goto out;

	/* Putting data in ArpEntry */
	neigh->state = ndm->ndm_state;

	if (neigh->state & NUD_VALID) {
		int key;

		attr = cmm_get_rtattr(NDA_RTA(ndm), NDA_PAYLOAD(nlh), NDA_LLADDR);

		if (attr)
			memcpy(neigh->macAddr, RTA_DATA(attr), RTA_PAYLOAD(attr));
		else
			memset(neigh->macAddr, 0, ETH_ALEN);

		key = HASH_MAC(neigh->macAddr);
		list_add(&neigh_table_by_mac[key], &neigh->list_by_mac);
	}
	else
		memset(neigh->macAddr, 0, ETH_ALEN);

out:
	return RTNL_CB_CONTINUE;
}

static int cmmNeighborNetlinkLookupAll(int family, int ifindex)
{
	struct rtnl_handle rth;
	struct ndmsg ndm = {
		.ndm_family = family,
		.ndm_ifindex = 0,
		.ndm_state = 0,
		.ndm_flags = 0,
		.ndm_type = 0,
	};

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	if (cmm_rtnl_open(&rth, 0) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: netlink socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err0;
	}

	if (cmm_rtnl_dump_request(&rth, RTM_GETNEIGH, &ndm, sizeof(struct ndmsg)) < 0)
		goto err1;

	cmm_rtnl_listen(&rth, cmmNeighborNetlinkLookupAllFilter, &ifindex);

	cmm_rtnl_close(&rth);

	return 0;

err1:
	cmm_rtnl_close(&rth);

err0:
	return -1;
}

static int get_neigh(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	struct ndmsg *ndm;
	struct rtattr *attr;
	uint32_t * out_index = (uint32_t *)arg;

	ndm = NLMSG_DATA(nlh);

	if (ndm->ndm_family != AF_BRIDGE) {
		cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink family(%d)\n",
				__func__, __LINE__, ndm->ndm_family);
		goto err;
	}

	attr = cmm_get_rtattr(NDA_RTA(ndm), NDA_PAYLOAD(nlh), NDA_MASTER);
	if (!attr)
	{
		cmm_print(DEBUG_STDOUT, "%s: Master interface attribute is not set\n",__func__);
		goto err;
	}

	*out_index = ndm->ndm_ifindex;
	/* Always stop parsing on first match */
	return RTNL_CB_STOP;
err:
	return RTNL_CB_ERROR;
}

/* This function returns the index of physical port for given
   neighbour mac, bridge index and  vlan */
int cmm_br_get_neigh(unsigned char *lladdr, uint32_t br_ifindex, uint16_t vlan)
{
	struct rtnl_handle rth;
	struct ndmsg ndm = {
		.ndm_family = AF_BRIDGE,
		.ndm_ifindex = 0,
		.ndm_state = 0,
		.ndm_flags = 0,
		.ndm_type = 0,
	};
	int len = sizeof(struct ndmsg);
	char buf[256];
	struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
	uint32_t out_index = 0;
	char ifname[IFNAMSIZ];

	if(!lladdr)
		goto err0;

	if (cmm_rtnl_open(&rth, 0) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: netlink socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err0;
	}

	cmm_nlh_init(nlh, len, RTM_GETNEIGH, NLM_F_REQUEST);
	memcpy(NLMSG_DATA(nlh), &ndm, len);

	/* Add attributes*/
	cmm_addattr_l(nlh, sizeof(buf), NDA_LLADDR, lladdr, ETH_ALEN);
	cmm_addattr32(nlh, sizeof(buf), NDA_MASTER, br_ifindex);
	if (vlan > 0)
		cmm_addattr16(nlh, sizeof(buf), NDA_VLAN, vlan);

	if (cmm_rtnl_send(&rth, nlh) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: cmm_rtnl_send %s\n", __func__, __LINE__, strerror(errno));
		goto err1;
	}

	(void)cmm_rtnl_listen(&rth, get_neigh, &out_index);
	cmm_print(DEBUG_STDOUT, "%s: outgoing interaface is %s\n", __func__, if_indextoname(out_index,ifname));

	cmm_rtnl_close(&rth);

	return out_index;

err1:
	cmm_rtnl_close(&rth);

err0:
	return -1;
}

int cmmNeighAddSolicitQ(int family, int ifindex, unsigned int *dst_ip, unsigned char *dst_mac)
{
	struct neighReq *n;

	n = (struct neighReq *)malloc(sizeof(struct neighReq));
	if (!n) {
		return -1;
	}
	
	n->family = family;
	n->ifindex = ifindex;
	if (family == AF_INET)
		n->dst_ip[0] = *dst_ip;
	else
		memcpy((unsigned char *)n->dst_ip, (unsigned char *)dst_ip, 16);
	
	if (dst_mac != NULL) {
		memcpy(n->dst_mac, dst_mac, 6);
		n->dst_mac_null=0;
	}
	else {
		n->dst_mac_null=1;
	}

	if (neighReqHead != NULL)
		n->next = neighReqHead;
	else
		n->next = NULL;

	neighReqHead = n;
	
	return 0;
}

void cmmNeighSendSolicitQ(void)
{
	struct neighReq *n, *next;

	if (neighReqHead == NULL)
		return;
	 
	for(n = neighReqHead; n != NULL; n = next) {

		if (n->family == AF_INET) {
			cmmArpRequest(n->ifindex, n->dst_ip[0], 
						n->dst_mac_null ? NULL : n->dst_mac);
		}
		else  {
			cmmNeighborSolicitation(n->ifindex, n->dst_ip, 
						n->dst_mac_null ? NULL : n->dst_mac);
		}
		
		next = n->next;
		free(n);
	}
	neighReqHead = NULL;

	return;
}





#define MULTICAST_SOLICITED_NODE "FF02::1:FF00:0000"

int cmmNeighborSolicitation(int ifindex, unsigned int *dst_ip, unsigned char *dst_mac)
{
	struct sockaddr_in6 sockaddr;
	struct __attribute__((packed)) {
		struct nd_neighbor_solicit hdr;
		uint8_t nd_opt_type;
		uint8_t nd_opt_len;
		unsigned char src_mac[ETH_ALEN];
	} neighbor;
	int sockopt;
	unsigned int multi_addr[4];
	int fd;
	int len;
	int rc = -1;

	/* dst_mac is NULL for multicast solicitations */
	if(dst_mac == NULL) {
		if(inet_pton(AF_INET6, MULTICAST_SOLICITED_NODE, multi_addr) != 1)
			goto out;
	}

	fd = socket (AF_INET6, SOCK_RAW, IPPROTO_ICMPV6);
	if (fd < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: socket() %s\n", __func__, __LINE__, strerror(errno));
		goto out;
	}

	memset(&sockaddr, 0, sizeof(struct sockaddr_in6));

	sockaddr.sin6_family = AF_INET6;
	sockaddr.sin6_scope_id = ifindex;

	if (itf_get_ipaddr(ifindex, AF_INET6, RT_SCOPE_LINK, (unsigned int *)&sockaddr.sin6_addr.s6_addr32, dst_ip) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: itf_get_ipaddr(%d) failed\n", __func__, __LINE__, ifindex);
		goto close;
	}
	

	if (bind(fd, (struct sockaddr *)&sockaddr, sizeof(sockaddr)) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: bind() %s\n", __func__, __LINE__, strerror(errno));
		goto close;
	}

	memset(&sockaddr, 0, sizeof(struct sockaddr_in6));
	sockaddr.sin6_family = AF_INET6;
	if(dst_mac == NULL)	{
		memcpy(sockaddr.sin6_addr.s6_addr, multi_addr, sizeof(multi_addr));
		memcpy(sockaddr.sin6_addr.s6_addr + 13, (unsigned char *)dst_ip + 13, 3);
	}
	else {
		memcpy(sockaddr.sin6_addr.s6_addr, (unsigned char *)dst_ip, 16);
	}
	
	memset(&neighbor.hdr, 0, sizeof(struct nd_neighbor_solicit));
	neighbor.hdr.nd_ns_type = ND_NEIGHBOR_SOLICIT;
	memcpy(&neighbor.hdr.nd_ns_target, dst_ip, 16);

	neighbor.nd_opt_type = ND_OPT_SOURCE_LINKADDR;
	neighbor.nd_opt_len = 1; /* 8 bytes */

	if (itf_get_macaddr(ifindex, neighbor.src_mac) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: itf_get_macaddr(%d) failed\n", __func__, __LINE__, ifindex);
		goto close;
	}

	sockopt = 255;
	if(dst_mac == NULL)	{
		if (setsockopt(fd, SOL_IPV6, IPV6_MULTICAST_HOPS, (char *) &sockopt, sizeof(sockopt)) < 0) {
			cmm_print(DEBUG_ERROR, "%s::%d: setsockopt() %s\n", __func__, __LINE__, strerror(errno));
			goto close;
		}
	}
	else {
		if (setsockopt(fd, SOL_IPV6, IPV6_UNICAST_HOPS, (char *) &sockopt, sizeof(sockopt)) < 0) {
			cmm_print(DEBUG_ERROR, "%s::%d: setsockopt() %s\n", __func__, __LINE__, strerror(errno));
			goto close;
		}
	}

	len = sizeof(neighbor);
	if (sendto(fd, &neighbor, len, 0, (struct sockaddr *) &sockaddr, sizeof(sockaddr)) < len) {
		cmm_print(DEBUG_ERROR, "%s::%d: sendto() %s\n", __func__, __LINE__, strerror(errno));
		goto close;
	}

	rc = 0;

close:
	close(fd);
out:
	return rc;
}

int cmmArpRequest(int ifindex, unsigned int dst_ip, unsigned char *dst_mac)
{
	struct sockaddr_ll sockaddr;
	struct __attribute__((packed)) {
		struct arphdr ah;
		unsigned char src_mac[ETH_ALEN];
		unsigned int src_ip;
		unsigned char dst_mac[ETH_ALEN];
		unsigned int dst_ip;
	} arp;
	unsigned int src_ip;
	unsigned char src_mac[ETH_ALEN];
	int fd;
	int len;
	int rc = -1;

	fd = socket(PF_PACKET, SOCK_DGRAM, 0);
	if (fd < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: socket() %s\n", __func__, __LINE__, strerror(errno));
		goto out;
	}

	memset(&sockaddr, 0, sizeof(struct sockaddr_ll));
	sockaddr.sll_family = AF_PACKET;
	sockaddr.sll_ifindex = ifindex;
	sockaddr.sll_protocol = htons(ETH_P_ARP);

	if (bind(fd, (struct sockaddr *) &sockaddr, sizeof(struct sockaddr_ll)) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: bind() %s\n", __func__, __LINE__, strerror(errno));
		goto close;
	}

	memset(&sockaddr, 0, sizeof(struct sockaddr_ll));
	sockaddr.sll_family = AF_PACKET;
	sockaddr.sll_ifindex = ifindex;
	sockaddr.sll_protocol = htons(ETH_P_ARP);
	sockaddr.sll_halen = ETH_ALEN;

	if (itf_get_ipaddr(ifindex, AF_INET, RT_SCOPE_UNIVERSE, &src_ip, &dst_ip) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: itf_get_ipaddr(%d) failed\n", __func__, __LINE__, ifindex);
		goto close;
	}

	if (itf_get_macaddr(ifindex, src_mac) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: itf_get_macaddr(%d) failed\n", __func__, __LINE__, ifindex);
		goto close;
	}

	if (!dst_mac)
		/* broadcast */
		memset(sockaddr.sll_addr, 0xFF, ETH_ALEN);
	else
		/* unicast */
		memcpy(sockaddr.sll_addr, dst_mac, ETH_ALEN);

	arp.ah.ar_hrd = htons(ARPHRD_ETHER);
	arp.ah.ar_pro = htons(ETH_P_IP);
	arp.ah.ar_hln = ETH_ALEN;
	arp.ah.ar_pln = 4;
	arp.ah.ar_op = htons(ARPOP_REQUEST);

	memcpy(arp.src_mac, src_mac, ETH_ALEN);
	memcpy(&arp.src_ip, &src_ip, 4);

	if (!dst_mac)
		memset(arp.dst_mac, 0, ETH_ALEN);
	else
		memcpy(arp.dst_mac, sockaddr.sll_addr, ETH_ALEN);

	memcpy(&arp.dst_ip, &dst_ip, 4);

	len = sizeof(arp);
	if (sendto(fd, &arp, len, 0, (struct sockaddr *) &sockaddr, sizeof(struct sockaddr_ll)) < len) {
		cmm_print(DEBUG_ERROR, "%s::%d: sendto() %s\n", __func__, __LINE__, strerror(errno));
		goto close;
	}

	rc = 0;

close:
	close(fd);
out:
	return rc;
}

#ifndef VLAN_FILTER
/*****************************************************************
* __cmmNeighUpdateAllMacs
*
*
******************************************************************/
void __cmmNeighUpdateAllMacs(int ifindex, unsigned char *macAddr, int port)
{
	struct NeighborEntry *neigh;
	struct list_head *entry;
	int key;

	key = HASH_MAC(macAddr);

	entry = list_first(&neigh_table_by_mac[key]);

	while (entry != &neigh_table_by_mac[key])
	{
		neigh = container_of(entry, struct NeighborEntry, list_by_mac);

		if ((neigh->ifindex == ifindex) &&
		    !memcmp(neigh->macAddr, macAddr, ETH_ALEN))
			neigh->port = port;

		entry = list_next(entry);
	}
}
#endif

/*****************************************************************
* __cmmNeighFind
*
*
******************************************************************/
struct NeighborEntry *__cmmNeighFind(int family, const unsigned int *ipAddr, int ifindex)
{
	struct NeighborEntry *neigh;
	struct list_head *entry;
	char buf[INET6_ADDRSTRLEN];
	int key;
	int ipAddrLen = IPADDRLEN(family);

	cmm_print(DEBUG_INFO, "%s: Neighbor(%d, %s)\n", __func__, ifindex, inet_ntop(family, ipAddr, buf, sizeof(buf)));

	key = HASH_NEIGHBOR(family, ipAddr);

	entry = list_first(&neigh_table[key]);

	while (entry != &neigh_table[key])
	{
		neigh = container_of(entry, struct NeighborEntry, list);
		if (!memcmp(neigh->ipAddr, ipAddr, ipAddrLen) && neigh->ifindex == ifindex)
			goto found;

		entry = list_next(entry);
	}

	neigh = NULL;

found:

	return neigh;
}


/*****************************************************************
* __cmmNeighRemove
*
*
******************************************************************/

/* NOTE: The neighMutex must be locked by the caller of this routine. */

void __cmmNeighRemove(struct NeighborEntry *neigh)
{
	char buf[INET6_ADDRSTRLEN];

	list_del(&neigh->list);

	if (neigh->state & NUD_VALID)
		list_del(&neigh->list_by_mac);

	if (neigh->flags & NEEDS_SOLICIT)
		list_del(&neigh->list_by_state);
	
	cmm_print(DEBUG_INFO, "%s: Neighbor(%d, %s) removed\n", __func__, neigh->ifindex, inet_ntop(neigh->family, neigh->ipAddr, buf, sizeof(buf)));

	free(neigh);
}

/*****************************************************************
* __cmmNeighPut
*
*
******************************************************************/
void __cmmNeighPut(struct NeighborEntry *neigh)
{
	char buf[INET6_ADDRSTRLEN];

	cmm_print(DEBUG_INFO, "%s: Neighbor(%d, %s) put\n", __func__, neigh->ifindex, inet_ntop(neigh->family, neigh->ipAddr, buf, sizeof(buf)));

	neigh->count--;

	if (neigh->count <= 0)
	{
		__cmmNeighRemove(neigh);
	}
}


/*****************************************************************
* __cmmNeighAdd
*
*
******************************************************************/
struct NeighborEntry *__cmmNeighAdd(int family, const unsigned int *ipAddr, int ifindex)
{
	struct NeighborEntry *neigh;
	char buf[INET6_ADDRSTRLEN];
	int key;

	neigh = malloc(sizeof(struct NeighborEntry));
	if (!neigh)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
		goto err0;
	}

	memset(neigh, 0, sizeof(struct NeighborEntry));

	neigh->count = 0;
#ifndef VLAN_FILTER
	neigh->port = -1;
#endif
	neigh->nr_probes = 0;
	neigh->ifindex = ifindex;
	neigh->ipAddrLen = IPADDRLEN(family);
	neigh->family = family;
	memcpy(neigh->ipAddr, ipAddr, neigh->ipAddrLen);

	key = HASH_NEIGHBOR(family, ipAddr);

	list_add(&neigh_table[key], &neigh->list);

	cmm_print(DEBUG_INFO, "%s: Neighbor(%d, %s) added\n", __func__, ifindex, inet_ntop(family, ipAddr, buf, sizeof(buf)));

	return neigh;

err0:
	return NULL;
}


/*****************************************************************
* __cmmNeighGet
*
*
******************************************************************/
struct NeighborEntry *__cmmNeighGet(int family, const unsigned int *ipAddr, int ifindex)
{
	struct NeighborEntry *neigh;

	neigh = __cmmNeighFind(family, ipAddr, ifindex);
	if (!neigh)
	{
		if (cmmNeighborNetlinkLookupAll(family, ifindex) < 0)
		{
			cmm_print(DEBUG_ERROR, "%s::%d: cmmNeighborNetlinkLookupAll() failed\n", __func__, __LINE__);
			goto err;
		}

		neigh = __cmmNeighFind(family, ipAddr, ifindex);
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3,19,0)
/* 
   In 3.19 kernel, neighbor entry in linux neighbor cache is not created during the creation of route entry in linux
   route cache as was done in previous versions. If neighbor entry is not present in linux during route look up in CMM,
   then neigbhor is not attached to CMM route and the subsequent neighbor creation event from linux is also ignored by CMM.
   As a result all the connections using this CMM route are not fast forwarded.
   To resolve this, if neighbor entry is not present in linux then a dummy neighbor is created in CMM. This dummy neighbor
   will be updated when the CMM receives the neighbor creation event
*/
		if(!neigh)
			neigh = __cmmNeighAdd(family, ipAddr, ifindex);
#endif
		if (!neigh)
			goto err;
	}

	neigh->count++;

	return neigh;

err:
	return NULL;
}


/*****************************************************************
* cmmNeighShow
*
*
******************************************************************/
int cmmNeighShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int i;
	struct NeighborEntry *temp;
	struct list_head *entry;
	char addr_buf[INET6_ADDRSTRLEN];
	char mac_buf[MAC_ADDRSTRLEN];
	char ifname[IFNAMSIZ];
	int count;

	cli_print(cli, "IPv4 ARP:");

	count = 0;

	for (i = 0; i < NEIGHBOR_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&neighMutex);

		for (entry = list_first(&neigh_table[i]); entry != &neigh_table[i]; entry = list_next(entry))
		{
			temp = container_of(entry, struct NeighborEntry, list);

			cli_print(cli, "IP addr: %s --> MAC addr: %s If: %s, "
#ifndef VLAN_FILTER
					"Port:%d, "
#endif
					"state: %x, Count: %d",
				inet_ntop(AF_INET, temp->ipAddr, addr_buf, sizeof(addr_buf)),
				mac_ntop(temp->macAddr, mac_buf, sizeof(mac_buf)),
				if_indextoname(temp->ifindex, ifname),
#ifndef VLAN_FILTER
				temp->port,
#endif
				temp->state,
				temp->count);

			count++;
		}
		__pthread_mutex_unlock(&neighMutex);
	}

	cli_print(cli, "Total ARP Entries: %d\n", count);

	cli_print(cli, "IPv6 Neighbor:");

	count = 0;

	for (i = NEIGHBOR_HASH_TABLE_SIZE; i < 2 * NEIGHBOR_HASH_TABLE_SIZE; i++)
	{
		__pthread_mutex_lock(&neighMutex);
		for (entry = list_first(&neigh_table[i]); entry != &neigh_table[i]; entry = list_next(entry))
		{

			temp = container_of(entry, struct NeighborEntry, list);

			cli_print(cli, "IP addr: %s --> MAC addr: %s If: %s, "
#ifndef VLAN_FILTER
					"Port: %d, "
#endif
					"state: %x, Count: %d",
				inet_ntop(AF_INET6, temp->ipAddr, addr_buf, sizeof(addr_buf)),
				mac_ntop(temp->macAddr, mac_buf, sizeof(mac_buf)),
				if_indextoname(temp->ifindex, ifname),
#ifndef VLAN_FILTER
				temp->port,
#endif
				temp->state,
				temp->count);

				count++;
		}
		__pthread_mutex_unlock(&neighMutex);
	}

	cli_print(cli, "Total Neighbor Entries: %d\n", count);

	return CLI_OK;
}


/*****************************************************************
* cmmNeighborResolved
* 
*
******************************************************************/
static void __cmmNeighborResolved(FCI_CLIENT *fci_handle, struct NeighborEntry *neigh)
{
	/* Process conntrack entries that use this neighbor */
	struct RtEntry *route;
	struct list_head *entry;
	int key;

#ifndef VLAN_FILTER
	/* Force lookup of bridge port */
	neigh->port = -1;
#endif
	key = HASH_MAC(neigh->macAddr);
	list_add(&neigh_table_by_mac[key], &neigh->list_by_mac);

	key = HASH_NEIGHBOR(neigh->family, neigh->ipAddr);

	entry = list_first(&rt_table_by_gw_ip[key]);
	while (entry != &rt_table_by_gw_ip[key])
	{
		route = container_of(entry, struct RtEntry, list_by_gw_ip);
		entry = list_next(entry);

		if (route->neighEntry != neigh)
			continue;

		/* Force lookup of bridge port */
		route->flags |= CHECK_BRIDGE_PORT;

		__cmmCtUpdateWithRoute(fci_handle, route);

		__cmmTunnelUpdateWithRoute(fci_handle, route);

		__cmmSocketUpdateWithRoute(fci_handle, route);
		/* Process SA entries that use this neighbour */
		__cmmSAUpdateWithRoute(fci_handle, route);
	}
}

/*****************************************************************
* cmmNeighborUnresolved
* 
*
******************************************************************/
static void __cmmNeighborUnresolved(FCI_CLIENT *fci_handle, struct NeighborEntry *neigh)
{
	/* Process conntrack entries that use this neighbor */
	struct RtEntry *route;
	struct list_head *entry;
	int key;

	cmm_print(DEBUG_INFO, "%s: Remove ARP/Neighbor entry\n", __func__);

	list_del(&neigh->list_by_mac);

#ifndef VLAN_FILTER
	/* Reset bridge port information, if any */
	neigh->port = -1;
#endif
	key = HASH_NEIGHBOR(neigh->family, neigh->ipAddr);

	entry = list_first(&rt_table_by_gw_ip[key]);
	while (entry != &rt_table_by_gw_ip[key])
	{
		route = container_of(entry, struct RtEntry, list_by_gw_ip);
		entry = list_next(entry);

		if (route->neighEntry != neigh)
			continue;

		/* Force lookup of bridge port */
		route->flags |= CHECK_BRIDGE_PORT;

		__cmmCtUpdateWithRoute(fci_handle, route);

		__cmmTunnelUpdateWithRoute(fci_handle, route);

		__cmmSocketUpdateWithRoute(fci_handle, route);
		/* Process SA entries that use this neighbour */
		__cmmSAUpdateWithRoute(fci_handle, route);
	}
}

/*****************************************************************
* cmmNeighborUpdate
* 
*
******************************************************************/
static int cmmNeighborUpdate(struct cmm_ct *ctx, const struct sockaddr_nl *who, struct nlmsghdr *n)
{
	char buf[INET6_ADDRSTRLEN];
	struct ndmsg *r = NLMSG_DATA(n);
	struct rtattr * tb [NDA_MAX + 1];
	struct NeighborEntry *neigh;
	unsigned int *ipAddr;
	unsigned char *macAddr;
	unsigned int macAddrLen;
	unsigned short old_state;

	/*Parse the message*/
	cmm_parse_rtattr(tb, NDA_MAX, NDA_RTA(r), NDA_PAYLOAD(n));

	/* Check if the event can interrest us*/
	if (!tb[NDA_DST])
		return 0;

	if (r->ndm_state & NUD_NOARP)
		return 0;

	ipAddr = RTA_DATA(tb[NDA_DST]);

	if (tb[NDA_LLADDR])
	{
		macAddr = RTA_DATA(tb[NDA_LLADDR]);
		macAddrLen = RTA_PAYLOAD(tb[NDA_LLADDR]);
	}
	else
	{
		macAddr = NULL;
		macAddrLen = 0;
	}

	/*Get the IP address in ascii*/
	inet_ntop(r->ndm_family, ipAddr, buf, INET6_ADDRSTRLEN);
	cmm_print(DEBUG_INFO, "%s: %s, state=%x\n", __func__, buf, r->ndm_state);

	/*Try to find a corresponding entry in the ARP table*/
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	neigh = __cmmNeighFind(r->ndm_family, ipAddr, r->ndm_ifindex);
	if (!neigh)
		goto out;

	/* Update local entry */
	old_state = neigh->state;
	neigh->state = r->ndm_state;
	cmm_print(DEBUG_INFO, "%s: old state = 0x%0x new state = 0x%0x\n", __func__, old_state, neigh->state);

	if((r->ndm_state & NUD_REACHABLE) && (neigh->flags & NEEDS_SOLICIT))
	{
		neigh->flags &= ~NEEDS_SOLICIT;
		neigh->nr_probes = 0;
		list_del(&neigh->list_by_state);
		cmm_print(DEBUG_INFO, "%s: Deleted Entry in Neighbor by state list \n", __func__);
	}

	//Neigh entries which are used by atleast one conntrack are added to state list
	if ((r->ndm_state & NUD_STALE) && (!(neigh->flags & NEEDS_SOLICIT)) && neigh->count > 0)
	{
		neigh->flags |= NEEDS_SOLICIT;
		list_add(&neigh_state_table, &neigh->list_by_state);
		cmm_print(DEBUG_INFO, "%s: Added Entry in Neighbor by state list \n", __func__);
	}

	/* Neigh entries which are not used by any conntrack are removed from CMM when
	the corresponding neigh entry is deleted by Kernel. */
	if(n->nlmsg_type == RTM_DELNEIGH && neigh->count <= 0)
	{
		__cmmNeighRemove(neigh);
		goto out;
	}

	/*In those states, the MAC address is usable*/
	if (r->ndm_state & NUD_VALID)
	{
		cmm_print(DEBUG_INFO, "%s: Update ARP/Neighbor entry\n", __func__);

		if (!(old_state & NUD_VALID))
		{
			memcpy(neigh->macAddr, macAddr, macAddrLen);

			__cmmNeighborResolved(ctx->fci_handle, neigh);
		}
		else
		{
			if (memcmp(neigh->macAddr, macAddr, macAddrLen))
			{
				memcpy(neigh->macAddr, macAddr, macAddrLen);

				list_del(&neigh->list_by_mac);

				__cmmNeighborResolved(ctx->fci_handle, neigh);
			}
		}
	}
	else
	{
		memset(neigh->macAddr, 0, ETH_ALEN);

		if (old_state & NUD_VALID)
			__cmmNeighborUnresolved(ctx->fci_handle, neigh);
	}

out:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return 0;
}


/*****************************************************************
* cmmNeighThread
*
* Function that sends neighbor soliciations
*
******************************************************************/
int cmmNeighSendSolicit(void)
{
	struct NeighborEntry *neigh;
	struct list_head *entry;
	struct list_head *next;

	__pthread_mutex_lock(&neighMutex);
	
	for (entry = list_first(&neigh_state_table); entry != &neigh_state_table; entry = next)
	{
		next = list_next(entry);
		
		neigh = container_of(entry, struct NeighborEntry, list_by_state);

		if(neigh->nr_probes < MAX_UCAST_SOLICIT )
		{
			if(cmmNeighAddSolicitQ(neigh->family, neigh->ifindex,
						neigh->ipAddr, neigh->macAddr) == 0)
				neigh->nr_probes++;
		}
		else if ((neigh->nr_probes >= MAX_UCAST_SOLICIT) && 
				(neigh->nr_probes < (MAX_UCAST_SOLICIT + MAX_MCAST_SOLICIT)))
		{
			if(cmmNeighAddSolicitQ(neigh->family, neigh->ifindex,
						neigh->ipAddr, NULL) == 0)
				neigh->nr_probes++;
		}
		else
		{
			neigh->flags &= ~NEEDS_SOLICIT;
			neigh->nr_probes = 0;
			list_del(&neigh->list_by_state);
			cmm_print(DEBUG_INFO, "%s: Deleted Entry in Neighbor by state list \n", __func__);
		}
		
	}

	__pthread_mutex_unlock(&neighMutex);

	cmmNeighSendSolicitQ();

	return 0;
}

/*****************************************************************
* cmmNeighborGet
* 
*
******************************************************************/
static int cmmNeighborGet(struct cmm_ct *ctx, const struct sockaddr_nl *who, struct nlmsghdr *n)
{
	struct ndmsg *r = NLMSG_DATA(n);
	char tab6[INET6_ADDRSTRLEN];
	char ifname[IFNAMSIZ];
	struct rtattr * tb [NDA_MAX + 1];

	/* ARP request immediately done by CMM as Kernel waits 1 seconds before sending it */	
	
	/*Parse the message*/
        cmm_parse_rtattr(tb, NDA_MAX, NDA_RTA(r), NDA_PAYLOAD(n));

	inet_ntop(r->ndm_family, RTA_DATA(tb[NDA_DST]), tab6, INET6_ADDRSTRLEN);
	cmm_print(DEBUG_INFO, "%s: %s(%s), state=%x\n", __func__, tab6, if_indextoname(r->ndm_ifindex, ifname), r->ndm_state);

	/*IPv4 Neighbor*/
	if (r->ndm_family == AF_INET)
	{
		cmmArpRequest(r->ndm_ifindex, *(unsigned int *)(RTA_DATA(tb[NDA_DST])), NULL);
	}
	else if(r->ndm_family == AF_INET6)
	{
		cmmNeighborSolicitation(r->ndm_ifindex, (unsigned int *)(RTA_DATA(tb[NDA_DST])), NULL);
	}

	// Return, 
	return RTNL_CB_CONTINUE;
}


/*****************************************************************
* cmmRtnlNeigh
* 
*
******************************************************************/
int cmmRtnlNeigh(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct cmm_ct *ctx = arg;

	switch (nlh->nlmsg_type) {
	case RTM_GETNEIGH:
		cmmNeighborGet(ctx, who, nlh);
		break;

	case RTM_NEWNEIGH:
	case RTM_DELNEIGH:
		cmmNeighborUpdate(ctx, who, nlh);
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported NEIGH netlink message %x\n", __func__, nlh->nlmsg_type);
		break;
	}

	return RTNL_CB_CONTINUE;
}
