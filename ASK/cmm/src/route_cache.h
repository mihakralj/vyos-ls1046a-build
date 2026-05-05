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
#ifndef __ROUTE_CACHE_H__
#define __ROUTE_CACHE_H__

#include "jhash.h"

	#define ROUTE_HASH_TABLE_SIZE	2048

	#ifndef RTA_FWMARK
	#define RTA_FWMARK 16
	#endif

	#define ROUTE_MAX_ID	0x10000

	struct fpp_rt {
		struct list_head list;

		int oifindex;
		int iifindex;
		int underlying_iifindex;
#ifdef VLAN_FILTER
		int underlying_vlan_id;
#endif
		unsigned short mtu;
		unsigned char dst_mac[ETH_ALEN];
		unsigned int dst_addr[4];
		int dst_addr_len;
#ifdef VLAN_FILTER
		unsigned short egress_vid;
		unsigned short vlan_filter_flags;
#endif
		int count;

		int flags;

		int id;
	};

	/*Structure representing  a route (internally to cmm)*/
	struct RtEntry {
		struct list_head list;
		struct list_head list_by_gw_ip;

		int count;						/*Number of time the route is used*/

		struct NeighborEntry *neighEntry;

		unsigned int sAddr[4];
		unsigned short sAddrLen;

		unsigned int dAddr[4];
		unsigned short dAddrLen;

		unsigned short flow_flags;

		int oifindex;
		int phys_oifindex;
#ifdef VLAN_FILTER
		unsigned short egress_vid;
		unsigned short vlan_filter_flags;
		int underlying_vlan_id;
#endif
		unsigned int gwAddr[4];
		unsigned short gwAddrLen;

		unsigned short mtu;
		int family;

		unsigned char table;
		unsigned char scope;
		unsigned char type;

		int flags;

		unsigned int fwmark;

		int iifindex;
		int underlying_iifindex;
	};

	struct ct_route {
		struct RtEntry	*route;
		struct fpp_rt	*fpp_route;
		int		fpp_route_id;
	};

	struct flow {
		int family;
		const unsigned int *sAddr;
		const unsigned int *dAddr;
		unsigned int fwmark;
		int iifindex; 
		int underlying_iif;
#ifdef VLAN_FILTER
		unsigned short  underlying_vlan_id;
#endif
		unsigned char proto;
		unsigned short flow_flags;
	};

	extern struct list_head rt_table[2 * ROUTE_HASH_TABLE_SIZE];
	extern struct list_head fpp_rt_table[ROUTE_HASH_TABLE_SIZE];
	extern struct list_head rt_table_by_gw_ip[2 * NEIGHBOR_HASH_TABLE_SIZE];
	extern pthread_mutex_t rtMutex;

	struct RtEntry *__cmmRouteFind(struct flow *flow);
	void __cmmRouteRemove(struct RtEntry *route);
	void __cmmRoutePut(struct RtEntry *route);
	struct RtEntry *__cmmRouteAdd(struct flow *flow);
	struct RtEntry *__cmmRouteGet(struct flow *flow);

	void __cmmFFPRouteUpdate(struct fpp_rt *route, int oifindex, const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len);
	struct fpp_rt *__cmmFPPRouteFind(int oifindex, int iifindex, const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len);
	void __cmmFPPRouteRemove(struct fpp_rt *route);
	void __cmmFPPRoutePut(struct fpp_rt *route);
	struct fpp_rt *__cmmFPPRouteAdd(int oifindex, int iifindex, int underlying_iifindex, 
				const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len);
	struct fpp_rt *__cmmFPPRouteGet(int oifindex, int iifindex, int underlying_iifindex, 
				const unsigned char *dst_mac, int mtu, const unsigned int *dst_addr, int dst_addr_len);

	int cmmRtShow(struct cli_def * cli, const char *command, char *argv[], int argc);
	int cmmFPPRtShow(struct cli_def * cli, const char *command, char *argv[], int argc);

	int cmmRtnlRoute(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg);
	int cmmRtnlRule(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg);
	void __cmmRouteUpdate(FCI_CLIENT *fci_handle, struct RtEntry *route);
	struct ctTable;
	void __cmmRouteLocalNew(FCI_CLIENT *fci_handle, struct ctTable* localctEntry);

#define cmmRouteEqual(route, flow, len) (!memcmp((route)->sAddr, (flow)->sAddr, (len)) && !memcmp((route)->dAddr, (flow)->dAddr, (len)) && ((route)->fwmark == (flow)->fwmark) && ((route)->iifindex == (flow)->iifindex))

static inline int cmmPrefixEqual(const u_int32_t *daddr1, const u_int32_t *daddr2, int lenbits)
{
	unsigned pdw, pbi;

	/* check complete u32 in prefix */
	pdw = lenbits >> 5;
	if (pdw && memcmp(daddr1, daddr2, pdw << 2))
		return 0;

	pbi = lenbits & 0x1f;
	if (pbi && ((daddr1[pdw] ^ daddr2[pdw]) & htonl((0xffffffff) << (32 - pbi))))
		return 0;

	return 1;
}

static inline u_int32_t HASH_RT(int family, const unsigned int *sAddr, const unsigned int *dAddr)
{
	u_int32_t key1, key2;

	if (family == AF_INET)
	{
		key1 = sAddr[0];
		key2 = dAddr[0];

		return (jhash_2words(key2, key1, 0x12563478) & (ROUTE_HASH_TABLE_SIZE - 1));
	}
	else
	{
		key1 = sAddr[0] ^ sAddr[1] ^ sAddr[2] ^ sAddr[3];
		key2 = dAddr[0] ^ dAddr[1] ^ dAddr[2] ^ dAddr[3];

		return (jhash_2words(key2, key1, 0x12563478) & (ROUTE_HASH_TABLE_SIZE - 1)) + ROUTE_HASH_TABLE_SIZE;
	}
}

static inline u_int32_t HASH_FPP_RT(u_int32_t ifindex, const u_int8_t *macaddr)
{
	u_int32_t key;

	key = ((u_int32_t *)macaddr)[0] ^ ((u_int16_t *)macaddr)[2];

	return (jhash_2words(key, ifindex, 0x12345678) & (ROUTE_HASH_TABLE_SIZE - 1));
}

#endif
