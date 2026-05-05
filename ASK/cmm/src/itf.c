/*
 *
 *  Copyright (C) 2010 Mindspeed Technologies, Inc.
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
#include <string.h>
#include <errno.h>
#include <net/if.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <time.h>
#include <linux/netlink.h>
#include <linux/sockios.h>
#include <linux/rtnetlink.h>
#include <linux/if_bridge.h>
#include <linux/if_vlan.h>
#include <net/if_arp.h>

/* 4RD (IPv4 Residual Deployment) netlink constants - NXP ASK extension.
 * These are defined in the ASK-patched kernel but not in glibc headers. */
#ifndef RTM_NEW4RD
#define RTM_NEW4RD	97
#define RTM_DEL4RD	98
#define RTM_GET4RD	99
#endif
#include "itf.h"
#include "pppoe.h"
#include "ffbridge.h"
#include "module_lro.h"
#include "route_cache.h"
#include "module_rx.h"

#if defined(LS1043)
struct gemac_port port_table[GEM_PORTS] = {
				   {"eth0", "wan", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT, 1},
				   {"eth1", "lan1", GEMAC_PORT_TYPE_LAN, 0, GEMAC0_PORT + 1 , 1},
				   {"eth2", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC0_PORT + 2, 1},
				   {"eth3", "wan1", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT + 3, 1},
				   {"eth4", "wan2", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT + 4, 1},
				   {"eth5", "wan3", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT + 5, 1},
				   {"eth6", "wan5", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT + 6, 1}
				};
#elif defined(COMCERTO_2000) && !defined(LS1012A)
struct gemac_port port_table[GEM_PORTS] = {
				   {"eth0", "wan", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT, 1},
				   {"eth2", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC1_PORT, 1},
				   {"eth3", "wan1", GEMAC_PORT_TYPE_WAN, 0, GEMAC2_PORT, 1}
				};
#elif defined(LS1012A)
struct gemac_port port_table[GEM_PORTS] = {
				   {"eth0", "wan", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT, 1},
				   {"eth2", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC1_PORT, 1},
				};
#elif defined(LS1088)
struct gemac_port port_table[GEM_PORTS] = {
				   {"eth0", "wan", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT, 1},
				   {"eth2", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC1_PORT, 1},
				   {"eth3", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC1_PORT, 1},
				};
#else
struct gemac_port port_table[GEM_PORTS] = {
				   {"eth0", "wan", GEMAC_PORT_TYPE_WAN, 0, GEMAC0_PORT, 1},
				   {"eth2", "lan", GEMAC_PORT_TYPE_LAN, 0, GEMAC1_PORT, 1}
				};
#endif

struct interface_table itf_table;

static struct interface_addr *__addr_find(struct interface *itf, unsigned int *ipaddr, unsigned int len);

#ifdef WIFI_ENABLE
extern struct wifi_ff_entry glbl_wifi_ff_ifs[MAX_WIFI_FF_IFS];
#endif

extern int tunnel_send_cmd(FCI_CLIENT *fci_handle, int request, struct interface *itf);

int LO_IFINDEX;

static void __addr_remove(struct interface_addr *addr)
{
	cmm_print(DEBUG_INFO, "%s: address removed\n", __func__);

	list_del(&addr->list);
	free(addr);
}

static struct interface_addr *__addr_add(struct interface *itf)
{
	struct interface_addr *addr;

	addr = malloc(sizeof(struct interface_addr));
	if (!addr)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
		goto err;
	}
	memset(addr, 0, sizeof(struct interface_addr));

	list_add(&itf->addr_list, &addr->list);

	cmm_print(DEBUG_INFO, "%s: address added\n", __func__);

	return addr;

err:
	return NULL;
}

static void __addr_update(struct interface_addr *addr, struct ifaddrmsg *ifa, struct rtattr *tb[])
{
	struct rtattr *attr;

	attr = tb[IFA_ADDRESS];

	addr->len = RTA_PAYLOAD(attr);
	memcpy(addr->address, RTA_DATA(attr), addr->len);

	addr->prefixlen = ifa->ifa_prefixlen;
	addr->scope = ifa->ifa_scope;
	addr->family = ifa->ifa_family;
}

static void __newaddr(struct ifaddrmsg *ifa, struct rtattr *tb[])
{
	struct interface *itf;
	struct interface_addr *addr;

	itf = __itf_find(ifa->ifa_index);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, ifa->ifa_index);
		goto out;
	}

	addr = __addr_add(itf);
	if (!addr)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __addr_add(%d) failed\n", __func__, __LINE__, ifa->ifa_index);
		goto out;
	}

	__addr_update(addr, ifa, tb);

out:
	return;
}

static void newaddr(struct interface_table *ctx, struct ifaddrmsg *ifa, struct rtattr *tb[])
{
	__pthread_mutex_lock(&ctx->lock);
	__newaddr(ifa, tb);
	__pthread_mutex_unlock(&ctx->lock);
}


static void __deladdr(struct ifaddrmsg *ifa, struct rtattr *tb[])
{
	struct interface *itf;
	struct interface_addr *addr;
	struct rtattr *attr;

	attr = tb[IFA_ADDRESS];

	itf = __itf_find(ifa->ifa_index);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, ifa->ifa_index);
		goto out;
	}
		
	addr = __addr_find(itf, RTA_DATA(attr), RTA_PAYLOAD(attr));
	if (!addr)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __addr_find failed\n", __func__, __LINE__);
		goto out;
	}

	__addr_remove(addr);
out:
	return;
}


static void deladdr(struct interface_table *ctx, struct ifaddrmsg *ifa, struct rtattr *tb[])
{
	__pthread_mutex_lock(&ctx->lock);
	__deladdr(ifa, tb);
	__pthread_mutex_unlock(&ctx->lock);
}


static struct interface_addr *__addr_find(struct interface *itf, unsigned int *ipaddr, unsigned int len)
{
	struct interface_addr *addr;
	struct list_head *entry;

	for (entry = list_first(&itf->addr_list); entry != &itf->addr_list; entry = list_next(entry))
	{
		addr = container_of(entry, struct interface_addr, list);
		if (cmmPrefixEqual(addr->address, ipaddr, 8 * len))
			goto found;
	}

	return NULL;

found:
	return addr;
}

#ifndef SAM_LEGACY
static struct map_rule * mr_add(struct interface *itf)
{
       struct map_rule *mr;

       mr = malloc(sizeof(struct map_rule));
       if (!mr)
       {
               cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
               goto err;
       }
       memset(mr, 0, sizeof(struct map_rule));

       list_add(&itf->mr_list, &mr->list);

       cmm_print(DEBUG_INFO, "%s: map rule added\n", __func__);

       return mr;

err:
       return NULL;
}


static void  mr_debug( struct interface * itf)
{
       struct list_head *entry, *next_entry;
       struct map_rule *mr;


       for (entry = list_first(&itf->mr_list); next_entry = list_next(entry), entry != &itf->mr_list; entry = next_entry)
       {
               mr = container_of(entry, struct map_rule, list);
       cmm_print(DEBUG_ERROR, "%03d : %03d.%03d.%03d.%03d/%02d %02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x/%03d %02x%02x     :%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x/%03d eabit:%03d offset:%03d \n",
                                       mr->rule.entry_num,
                                       (ntohl(mr->rule.prefix) >> 24) & 0xff,
                                       (ntohl(mr->rule.prefix) >> 16) & 0xff,
                                       (ntohl(mr->rule.prefix) >>  8) & 0xff,
                                       ntohl(mr->rule.prefix) & 0xff,
                                       mr->rule.prefixlen,
                                       mr->rule.relay_prefix.s6_addr[0],
                                       mr->rule.relay_prefix.s6_addr[1],
                                       mr->rule.relay_prefix.s6_addr[2],
                                       mr->rule.relay_prefix.s6_addr[3],
                                       mr->rule.relay_prefix.s6_addr[4],
                                       mr->rule.relay_prefix.s6_addr[5],
                                       mr->rule.relay_prefix.s6_addr[6],
                                       mr->rule.relay_prefix.s6_addr[7],
                                       mr->rule.relay_prefix.s6_addr[8],
                                       mr->rule.relay_prefix.s6_addr[9],
                                       mr->rule.relay_prefix.s6_addr[10],
                                       mr->rule.relay_prefix.s6_addr[11],
                                       mr->rule.relay_prefix.s6_addr[12],
                                       mr->rule.relay_prefix.s6_addr[13],
                                       mr->rule.relay_prefix.s6_addr[14],
                                       mr->rule.relay_prefix.s6_addr[15],
                                       mr->rule.relay_prefixlen,
                                       mr->rule.relay_suffix.s6_addr[0],
                                       mr->rule.relay_suffix.s6_addr[1],
                                       mr->rule.relay_suffix.s6_addr[2],
                                       mr->rule.relay_suffix.s6_addr[3],
                                       mr->rule.relay_suffix.s6_addr[4],
                                       mr->rule.relay_suffix.s6_addr[5],
                                       mr->rule.relay_suffix.s6_addr[6],
                                       mr->rule.relay_suffix.s6_addr[7],
                                       mr->rule.relay_suffix.s6_addr[8],
                                       mr->rule.relay_suffix.s6_addr[9],
                                       mr->rule.relay_suffix.s6_addr[10],
                                       mr->rule.relay_suffix.s6_addr[11],
                                       mr->rule.relay_suffix.s6_addr[12],
                                       mr->rule.relay_suffix.s6_addr[13],
                                       mr->rule.relay_suffix.s6_addr[14],
                                       mr->rule.relay_suffix.s6_addr[15],
                                       mr->rule.relay_suffixlen,
                                       mr->rule.eabit_len,
                                       mr->rule.psid_offsetlen );


       }
       return ;
}


static struct map_rule * mr_find( int entry_num,struct interface * itf)
{
       struct list_head *entry, *next_entry;
       struct map_rule *mr;

       cmm_print(DEBUG_INFO, "%s: mapping rule entry find %d \n", __func__, entry_num);

       for (entry = list_first(&itf->mr_list); next_entry = list_next(entry), entry != &itf->mr_list; entry = next_entry)
       {
               mr = container_of(entry, struct map_rule, list);
               if(mr->rule.entry_num == entry_num)
                       return mr;

       }
       return NULL;
}

static void __mr_delete(struct map_rule *mr)
{
       cmm_print(DEBUG_INFO, "%s: mapping rule removed\n", __func__);

       list_del(&mr->list);
       free(mr);
}

static void mr_delete(int entry_num, int reset, struct interface* itf)
{
       struct list_head *entry, *next_entry;
       struct map_rule *mr;

       cmm_print(DEBUG_INFO, "%s: mapping rule entry delete  %d reset is %d \n", __func__, entry_num,reset);
       for (entry = list_first(&itf->mr_list); next_entry = list_next(entry), entry != &itf->mr_list; entry = next_entry)
       {
               mr = container_of(entry, struct map_rule, list);
               if(reset)
                       __mr_delete(mr);
               else if(mr->rule.entry_num == entry_num)
               {
                       __mr_delete(mr);
                       return;
               }

       }
       return ;
}

static void __mr_update(struct map_rule * mr, struct ip6_4rd_map_msg *mr_msg)
{
       mr->rule.prefix = mr_msg->prefix ;
       memcpy(&mr->rule.relay_prefix,&mr_msg->relay_prefix, sizeof(mr_msg->relay_prefix));
       memcpy(&mr->rule.relay_suffix,&mr_msg->relay_suffix, sizeof(mr_msg->relay_suffix));
       mr->rule.prefixlen = mr_msg->prefixlen ;
       mr->rule.relay_prefixlen = mr_msg->relay_prefixlen ;
       mr->rule.relay_suffixlen = mr_msg->relay_suffixlen ;
       mr->rule.psid_offsetlen = mr_msg->psid_offsetlen ;
       mr->rule.eabit_len = mr_msg->eabit_len ;
       mr->rule.entry_num = mr_msg->entry_num ;
       return;
}

static void mr_update(FCI_CLIENT *fci_handle,struct ip6_4rd_map_msg *mr_msg)
{
       struct interface *itf;
       struct map_rule *mr;

       itf = __itf_find(mr_msg->ifindex);
       if (!itf)
       {
               cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, mr_msg->ifindex);
               goto out;
       }

       mr = mr_find(mr_msg->entry_num, itf);
       if (!mr)
       {
               // Add new mapping rule
               mr = mr_add(itf);
               if(!mr)
               {
                       cmm_print(DEBUG_ERROR, "%s::%d: __mr_add(%d) failed\n", __func__, __LINE__, mr_msg->entry_num);
                       goto out;
               }
	       if(!(itf->tunnel_flags & TNL_4RD))
	       {
			/* Set tunnel mode to 4RD and update tunnel in FPP */
			itf->tunnel_flags |= TNL_4RD;
			itf->flags |= FPP_NEEDS_UPDATE;
			tunnel_send_cmd(fci_handle, UPDATE,itf);	
	       }
			
       }
       __mr_update(mr, mr_msg);
out:
       return;
}

static void mr_remove(FCI_CLIENT *fci_handle,struct ip6_4rd_map_msg *mr_msg)
{
       struct interface *itf;
       struct list_head *entry;

       itf = __itf_find(mr_msg->ifindex);
       if (!itf)
       {
               cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, mr_msg->ifindex);
               goto out;
       }

       mr_delete(mr_msg->entry_num, mr_msg->reset, itf);
       entry = &itf->mr_list;
       if(list_empty(entry))
       {
	       if(itf->tunnel_flags & TNL_4RD)	
	       {
		       itf->tunnel_flags &= ~TNL_4RD;
		       /* The tunnel needs  to be updated as type 4o6 */
			itf->flags |= FPP_NEEDS_UPDATE;
			tunnel_send_cmd(fci_handle, UPDATE,itf);	
	       }	
       }
out:
       return;
}
#endif

#ifdef VLAN_FILTER
struct interface_bridge_vlan_info *vinfo_find_by_vid(int ifindex, u_int16_t vid)
{
	struct interface *itf;
	struct interface_bridge_vlan_info *vinfo;
	struct list_head *entry;

	itf = __itf_find(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, ifindex);
		goto out;
	}

	for (entry = list_first(&itf->bridge_vlan_info_list); entry != &itf->bridge_vlan_info_list; entry = list_next(entry))
	{
		vinfo = container_of(entry, struct interface_bridge_vlan_info, list);
		if (vinfo->vlan_info.vid == vid)
			return vinfo;
	}
out:
	return NULL;
}

struct interface_bridge_vlan_info *vinfo_find_by_pvid(int ifindex)
{
	struct interface *itf;
	struct interface_bridge_vlan_info *vinfo;
	struct list_head *entry;

	itf = __itf_find(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_find(%d) failed\n", __func__, __LINE__, ifindex);
		goto out;
	}

	for (entry = list_first(&itf->bridge_vlan_info_list); entry != &itf->bridge_vlan_info_list; entry = list_next(entry))
	{
		vinfo = container_of(entry, struct interface_bridge_vlan_info, list);
		if (vinfo->vlan_info.flags & BRIDGE_VLAN_INFO_PVID)
			return vinfo;
	}
out:
	return NULL;
}

static void vinfo_add(struct interface *itf, struct bridge_vlan_info *vinfo)
{
	struct interface_bridge_vlan_info *br_vinfo;

	br_vinfo = calloc(1,sizeof(struct interface_bridge_vlan_info));
	if (!br_vinfo)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: calloc() failed\n", __func__, __LINE__);
		return;
	}
	memcpy(&br_vinfo->vlan_info, vinfo, sizeof(struct bridge_vlan_info));
	list_add(&itf->bridge_vlan_info_list, &br_vinfo->list);
}

static void add_itf_bridge_vlan_info(struct interface *itf, struct rtattr *tb)
{
	struct rtattr *i, *list = tb;
	int rem = RTA_PAYLOAD(list);

	for (i = RTA_DATA(list); RTA_OK(i, rem); i = RTA_NEXT(i, rem)) {
		struct bridge_vlan_info *vinfo;
		//struct interface_bridge_vlan_info *br_vinfo;
		if (i->rta_type != IFLA_BRIDGE_VLAN_INFO)
			continue;

		vinfo = RTA_DATA(i);
		vinfo_add(itf,vinfo);
	}
}

static void vinfo_remove(struct interface_bridge_vlan_info *vinfo)
{
        list_del(&vinfo->list);
        free(vinfo);
}

static void del_itf_bridge_vlan_info(struct interface *itf)
{
	struct interface_bridge_vlan_info *vinfo;
	struct list_head *entry, *next_entry;

	for (entry = list_first(&itf->bridge_vlan_info_list); next_entry = list_next(entry), entry != &itf->bridge_vlan_info_list; entry = next_entry)
	{
		vinfo = container_of(entry, struct interface_bridge_vlan_info, list);
		vinfo_remove(vinfo);
	}
}

static void update_itf_bridge_vlan_info(struct interface *itf,  struct ifinfomsg *ifi, struct rtattr *tb, int dellink)
{
	/* Update vlan filtering information on interface only when family is AF_BRIDGE */
	if (ifi->ifi_family != AF_BRIDGE)
		return;

	/* Delete the entire list of configuration and add */
	del_itf_bridge_vlan_info(itf);
	if(!dellink)
		add_itf_bridge_vlan_info(itf,tb);
}

static void update_vlan_filtering_info(struct interface *itf, struct rtattr *tb)
{
	struct rtattr *i, *list = tb;
	int rem = RTA_PAYLOAD(list);
	struct rtattr *attr;

	for (i = RTA_DATA(list); RTA_OK(i, rem); i = RTA_NEXT(i, rem)) {

		if (i->rta_type != IFLA_INFO_DATA)
			continue;

		attr = cmm_get_rtattr(RTA_DATA(i), RTA_PAYLOAD(i), IFLA_BR_VLAN_FILTERING);
		if(attr) {
			cmm_print(DEBUG_INFO, "%s: vlan filter=%d on iface %s\n", __func__, *(__u8 *)RTA_DATA(attr), itf->ifname);
			itf->vlan_filtering_enab = *(__u8 *)RTA_DATA(attr);
		}
	}
}
#endif

static void __itf_remove(struct interface *itf)
{
	struct interface_addr *addr;
	struct list_head *entry, *next_entry;
#ifndef SAM_LEGACY
	struct map_rule *mr;
#endif

	cmm_print(DEBUG_INFO, "%s: interface(%d) removed\n", __func__, itf->ifindex);

	for (entry = list_first(&itf->addr_list); next_entry = list_next(entry), entry != &itf->addr_list; entry = next_entry)
	{
		addr = container_of(entry, struct interface_addr, list);
		__addr_remove(addr);
	}
#ifndef SAM_LEGACY
       for (entry = list_first(&itf->mr_list); next_entry = list_next(entry), entry != &itf->mr_list; entry = next_entry)
       {
	       mr = container_of(entry, struct map_rule, list);
	       __mr_delete(mr);

       }
#endif

#ifdef VLAN_FILTER
       del_itf_bridge_vlan_info(itf);
#endif
	if (__itf_is_l2tp(itf))
		l2tp_itf_del(itf_table.fci_handle, itf);

	list_del(&itf->list);
	free(itf);
}

static void __itf_update(struct interface_table *ctx, struct interface *itf, struct ifinfomsg *ifi, struct rtattr *tb[], int dellink)
{
	struct rtattr *attr;

	itf->ifindex = ifi->ifi_index;
	if_indextoname(itf->ifindex, itf->ifname);
	itf->type = ifi->ifi_type;
	itf->ifi_flags = ifi->ifi_flags;

	attr = tb[IFLA_LINKINFO];
	if (attr)
	{
		attr = cmm_get_rtattr(RTA_DATA(attr), RTA_PAYLOAD(attr), IFLA_INFO_KIND);
		if (attr)
		{
			strncpy(itf->link_kind, RTA_DATA(attr), sizeof(itf->link_kind) - 1);
			itf->link_kind[sizeof(itf->link_kind) - 1] = '\0';
		}
#ifdef VLAN_FILTER
		update_vlan_filtering_info(itf, tb[IFLA_LINKINFO]);
#endif
	}

	cmm_print(DEBUG_INFO, "%s: index=%d, name=%s, kind=%s, type=%d, flags=0x%x\n", __func__,
				itf->ifindex, itf->ifname, itf->link_kind, itf->type, itf->ifi_flags);

#ifdef VLAN_FILTER
	if (!strcmp(itf->link_kind,"bridge")) {
		cmm_print(DEBUG_INFO, "%s: interface %s is marked as ITF_BRIDGE\n", __func__,itf->ifname);
		itf->itf_flags |= ITF_BRIDGE;
	}
#endif

	/* If the interface is down don't try to update the other fields,
	   the information may already be missing in the kernel and we will get wrong results */
	if (!__itf_is_up(itf))
		goto out;

#ifdef WIFI_ENABLE	
	/* FIXME : Skip wireless events */
	attr = tb[IFLA_WIRELESS];
	
	if(attr)
		goto out;	
#endif

	attr = tb[IFLA_ADDRESS];
	if (attr)
	{
		/* mark mac address valid */
		itf->macaddr_len = RTA_PAYLOAD(attr);

		/* Ethip kernel mod WA compatibility */
		if(itf->macaddr_len > 6)
			itf->macaddr_len = 6;
		
		memcpy(itf->macaddr, RTA_DATA(attr), itf->macaddr_len);
	}
	else
	{
		memset(itf->macaddr, 0, 6);
		itf->macaddr_len = 0;
	}

	attr = tb[IFLA_MTU];
	if (attr)
		itf->mtu = *(unsigned int *)RTA_DATA(attr);
	else
		itf->mtu = 0;

#ifdef VLAN_FILTER
	attr = tb[IFLA_MASTER];
	if(attr)
		itf->master = *(unsigned int *)RTA_DATA(attr);
	else
		itf->master = 0;
#endif

	attr = tb[IFLA_LINK];
	if (attr)
		itf->phys_ifindex = *(int *)RTA_DATA(attr);
	else
		/* will be updated later if pppoe */
		itf->phys_ifindex = itf->ifindex;

#ifdef VLAN_FILTER
	attr = tb[IFLA_AF_SPEC];
	if(attr)
		update_itf_bridge_vlan_info(itf, ifi, attr, dellink);
#endif

	if (__itf_is_pppoe(itf))
	{
		itf->itf_flags &= ~ITF_PPPOE_SESSION_UP;

#ifdef WIFI_ENABLE
		/* sometimes 3G interface names comes up as pppXXXXNN */
		/* differentiate if WIFI interface or ppp interface */
		/* __cmmGetWiFi checks if the interface itf passed is WIFI, then sets WIFI flag */
		__cmmGetWiFi(ctx->fd,itf);
		/* if WIFI interface , do not perform PPPoE functionality stuff */
		if (__itf_is_wifi(itf))
			goto proceed_to_lro;
#endif /* WIFI_ENABLE */

		if (!ctx->fp)
		{
			ctx->fp = fopen(PPPOE_PATH, "r");
			if (!ctx->fp)
			{
				cmm_print(DEBUG_ERROR, "%s::%d: fopen(%s) error %s\n", __func__, __LINE__, PPPOE_PATH, strerror(errno));
				goto out;
			}
		}

		__cmmGetPPPoESession(ctx->fp, itf);
	}
	else
        {

		__cmmGetVlan(ctx->fd, itf);
#ifdef WIFI_ENABLE	
		__cmmGetWiFi(ctx->fd, itf);
#endif

#ifndef VLAN_FILTER
		itf->itf_flags &= ~ITF_BRIDGE;
#endif
#if defined(LS1043)
		itf->itf_flags &= ~ITF_BRIDGED_PORT;
#endif
#ifndef VLAN_FILTER
		__cmmGetBridges(ctx->fd);
#endif
		/* if WIFI interface and no l2 header support, do not call cmm get tunnel*/
		if (!((__itf_is_wifi(itf)) && (itf->type == ARPHRD_NONE)))
			__cmmGetTunnel(ctx->fd, itf, tb);
		__cmmGetMacVlan(ctx->fd, itf);

#if defined(LS1043)
		if ( !(unsupported_bridged_interface(itf) ))
			cmmGetBridgedItf(itf);
#endif
	}

proceed_to_lro:
	lro_interface_update(itf);

out:
	cmm_print(DEBUG_INFO, "%s: itf: %lx, ifindex: %d, phys_ifindex: %d, flags: %x\n", __func__, (unsigned long)itf, itf->ifindex, itf->phys_ifindex, itf->itf_flags);

	/* FIXME resolve physical interface for vlan + bridge, bridge + vlan */
}

static struct interface *__itf_add(struct interface_table *ctx, int ifindex)
{
	struct interface *itf;
	int key;
#ifdef VLAN_FILTER
	struct bridge_vlan_info vinfo;
#endif
	itf = malloc(sizeof(struct interface));
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: malloc() failed\n", __func__, __LINE__);
		goto err;
	}

	memset(itf, 0, sizeof(struct interface));

	itf->count = 0;

	list_head_init(&itf->addr_list);
#ifndef SAM_LEGACY
	list_head_init(&itf->mr_list);
#endif
#ifdef VLAN_FILTER
	/* Default vlan filtering configuration */
	list_head_init(&itf->bridge_vlan_info_list);
	memset(&vinfo, 0, sizeof(vinfo));
	vinfo.vid = 1;
	vinfo.flags = BRIDGE_VLAN_INFO_PVID | BRIDGE_VLAN_INFO_UNTAGGED;
	vinfo_add(itf,&vinfo);
#endif

	key = HASH_ITF(ifindex);
	list_add(&ctx->hash[key], &itf->list);

	cmm_print(DEBUG_INFO, "%s: interface(%d) added\n", __func__, ifindex);

	return itf;

err:
	return NULL;
}

#ifndef SAM_LEGACY
static int __cmmGetMappingRuleFilter(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	struct interface_table *ctx = arg;
	struct ip6_4rd_map_msg *mr;
//	struct rtattr *tb[IF_MR_MAX + 1];

	if (nlh->nlmsg_type != RTM_NEW4RD)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink message(%d)\n",
						 __func__, __LINE__, nlh->nlmsg_type);
		goto out;
	}

	mr = NLMSG_DATA(nlh);

	mr_update(ctx->fci_handle,mr);	
	struct interface * itf = __itf_find(mr->ifindex);
	if (itf)
	{
		mr_debug(itf);
	}


out:
	return RTNL_CB_CONTINUE;
}

static int __cmmGetMappingRule(struct interface_table *ctx)
{
	struct ip6_4rd_map_msg mr;

	int rc;
	memset(&mr,0,sizeof(mr));

	if ((rc = cmm_rtnl_dump_request(&ctx->rth, RTM_GET4RD, &mr, sizeof(struct ip6_4rd_map_msg))) < 0)
		goto out;

	rc = cmm_rtnl_listen(&ctx->rth, __cmmGetMappingRuleFilter, ctx);

out:
	return rc;
}
#endif

void __itf_update_connection(FCI_CLIENT *fci_handle, int ifindex)
{
	struct list_head *entry, *next_entry;
	struct RtEntry *route;
	int i;

	for (i = 0; i < ROUTE_HASH_TABLE_SIZE * 2; i++)
	{
		for (entry = list_first(&rt_table[i]); next_entry = list_next(entry), entry != &rt_table[i]; entry = next_entry)
		{
			route = container_of(entry, struct RtEntry, list);

			if (!((route->oifindex == ifindex) || (route->phys_oifindex == ifindex)))
				continue;
#ifndef VLAN_FILTER
			/* Force lookup of bridge port */
			if (route->neighEntry)
				route->neighEntry->port = -1;
#endif
			route->flags |= CHECK_BRIDGE_PORT;

			__cmmRouteUpdate(fci_handle, route);
		}
	}
}


static void __updatelink(struct interface_table *ctx, struct ifinfomsg *ifi, struct rtattr *tb[], int dellink)
{
	struct interface *itf;

#if defined(LS1043)
	int was_bridged_port;
#endif

	itf = __itf_find(ifi->ifi_index);
	if (!itf)
	{
		if (dellink && !(ifi->ifi_flags & IFF_UP) && (ifi->ifi_change == 0xffffffff))
			goto out;

		itf = __itf_add(ctx, ifi->ifi_index);
		if (!itf)
		{
			cmm_print(DEBUG_ERROR, "%s::%d: __itf_add(%d) failed\n", __func__, __LINE__, ifi->ifi_index);
			goto out;
		}
	}

#if defined(LS1043)
	was_bridged_port = __itf_is_bridged_port(itf);
#endif

	__itf_update(ctx, itf, ifi, tb, dellink);

	/* For IP tunnels we don't care about physical output interface here,
		everything is handled later through the tunnel route output interface */
	if (__itf_is_tunnel(itf))
	{
			if (__itf_is_up(itf))
				__tunnel_add(ctx->fci_handle, itf);
			else if(dellink)
				__tunnel_del(ctx->fci_handle, ctx->fci_key_handle, itf);
			else
				__tunnel_update(ctx->fci_handle, itf);
	}
	else if (__itf_is_l2tp(itf))
	{
		if (__itf_is_up(itf))
			/* L2TP interface being a virtual interface, the physical and logical ifindices are the same */
			l2tp_itf_add(ctx->fci_handle, ADD, itf);
		else
			__l2tp_itf_del(ctx->fci_handle, itf);
	}
	else
	{
#ifdef WIFI_ENABLE
		if(__itf_is_wifi(itf))
		{
		//	cmm_print( DEBUG_INFO, "%s : vap : %s\n", __func__, itf->ifname);

			if (__itf_is_up(itf))
				cmmFeWiFiUpdate(ctx->fci_handle, ctx->fd, ADD, itf);
			else
				cmmFeWiFiUpdate(ctx->fci_handle, ctx->fd, REMOVE, itf);
		}
		else
#endif
		if (__itf_is_programmed(itf->phys_ifindex) > 0)
		{
			if (__itf_is_pppoe(itf) && ctx->fp)
			{
				if (__itf_is_up(itf) && (itf->itf_flags & ITF_PPPOE_SESSION_UP))
					cmmFePPPoEUpdate(ctx->fci_handle, ADD, itf);
				else
					cmmFePPPoEUpdate(ctx->fci_handle, REMOVE, itf);
			}
			else if (__itf_is_vlan(itf))
			{
				if (cmmVlanCheckPolicy(itf))
				{
					if (__itf_is_up(itf))
					{
						cmmFeVLANUpdate(ctx->fci_handle, ADD, itf);
					}
					else
						cmmFeVLANUpdate(ctx->fci_handle, REMOVE, itf);
				}
			}
			else if (__itf_is_macvlan(itf))
			{
				if (__itf_is_up(itf)) {
					cmmFeMacVlanUpdate(ctx->fci_handle,ctx->fd,ADD,itf);
				}
				else  {
					cmmFeMacVlanUpdate(ctx->fci_handle,ctx->fd,REMOVE,itf);
				}
			}
		}
		else if (____itf_is_bridge( itf ))
		{
#if defined(LS1043)
			cmmFeUpdateAllBridgedIfs(ctx->fci_handle, ctx->fd, itf);
#else
#ifdef WIFI_ENABLE	
			if (__itf_is_up(itf))	
				cmmFeWiFiBridgeUpdate(ctx->fci_handle, ctx->fd, ADD, itf);
#endif
#endif
		}

#if defined(LS1043)
		if ( ((was_bridged_port) && !__itf_is_bridged_port(itf)) || ((!was_bridged_port) && __itf_is_bridged_port(itf)))
		{
			itf->flags |= FPP_NEEDS_UPDATE;
			cmmFeBridgedIfUpdate(ctx->fci_handle, ctx->fd, itf);
		}
#endif
	}

	if (dellink && !__itf_is_up(itf) && (ifi->ifi_change == 0xffffffff))
		__itf_remove(itf);

out:
	return;
}

static void updatelink(struct interface_table *ctx, struct ifinfomsg *ifi, struct rtattr *tb[], int dellink)
{
	__pthread_mutex_lock(&ctx->lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	__updatelink(ctx, ifi, tb, dellink);
	__itf_update_connection(ctx->fci_handle, ifi->ifi_index);
	mc_update_table(ctx->fci_handle, tb, ifi);

#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&ctx->lock);
}

struct interface *__itf_find(int ifindex)
{
	struct list_head *entry;
	struct interface *itf;
	int key;

	cmm_print(DEBUG_INFO, "%s: find interface(%d)\n", __func__, ifindex);

	key = HASH_ITF(ifindex);

	entry = list_first(&itf_table.hash[key]);
	while (entry != &itf_table.hash[key])
	{
		itf = container_of(entry, struct interface, list);
		if (itf->ifindex == ifindex)
			goto found;

		entry = list_next(entry);
	}

	itf = NULL;

found:
	return itf;
}

static int __cmmGetAddrFilter(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	struct ifaddrmsg *ifa;
	struct rtattr *tb[IFA_MAX + 1];

	if (nlh->nlmsg_type != RTM_NEWADDR)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink message(%d)\n",
						 __func__, __LINE__, nlh->nlmsg_type);

		goto out;
	}

	ifa = NLMSG_DATA(nlh);

	cmm_parse_rtattr(tb, IFA_MAX, IFA_RTA(ifa), IFA_PAYLOAD(nlh));

	if (!tb[IFA_ADDRESS])
	{
		cmm_print(DEBUG_ERROR, "%s::%d: rtnetlink message missing interface addr\n", __func__, __LINE__);
		goto out;
	}

	__newaddr(ifa, tb);

out:
	return RTNL_CB_CONTINUE;
}

static int __cmmGetAddr(struct rtnl_handle *rth, int family)
{
	struct ifaddrmsg ifa = {
		.ifa_family = family,
		.ifa_prefixlen = 0,
		.ifa_flags = 0,
		.ifa_scope = 0,
		.ifa_index = 0,
	};
	int rc;

	if ((rc = cmm_rtnl_dump_request(rth, RTM_GETADDR, &ifa, sizeof(struct ifaddrmsg))) < 0)
		goto out;

	rc = cmm_rtnl_listen(rth, __cmmGetAddrFilter, NULL);

out:
	return rc;
}

static int __cmmGetLinkFilter(const struct sockaddr_nl *nladdr, struct nlmsghdr *nlh, void *arg)
{
	struct interface_table *ctx = arg;
	struct ifinfomsg *ifi;
	struct rtattr *tb[IFLA_MAX + 1];

	if (nlh->nlmsg_type != RTM_NEWLINK)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: unexpected netlink message(%d)\n",
						 __func__, __LINE__, nlh->nlmsg_type);
		goto out;
	}

	ifi = NLMSG_DATA(nlh);

	cmm_parse_rtattr(tb, IFLA_MAX, IFLA_RTA(ifi), IFLA_PAYLOAD(nlh));

	__updatelink(ctx, ifi, tb, 0);

out:
	return RTNL_CB_CONTINUE;
}

static int __cmmGetLink(struct interface_table *ctx)
{
	struct ifinfomsg ifi = {
		.ifi_family = AF_UNSPEC,
		.ifi_type = 0,
		.ifi_index = 0,
		.ifi_flags = 0,
		.ifi_change = 0,
	};
	int rc;

	if ((rc = cmm_rtnl_dump_request(&ctx->rth, RTM_GETLINK, &ifi, sizeof(struct ifinfomsg))) < 0)
		goto out;

	rc = cmm_rtnl_listen(&ctx->rth, __cmmGetLinkFilter, ctx);

out:
	return rc;
}

static int __itf_table_update(struct interface_table *ctx)
{
	int rc = 0;

	rc = __cmmGetLink(ctx);
	if (rc < 0)
		goto out;

	rc = __cmmGetAddr(&ctx->rth, AF_INET);
	if (rc < 0)
		goto out;

	rc = __cmmGetAddr(&ctx->rth, AF_INET6);
	if (rc < 0)
		goto out;

#ifndef SAM_LEGACY
	rc = __cmmGetMappingRule(ctx);
	if (rc < 0)
		cmm_print(DEBUG_ERROR, "%s::%d: __cmmGetMappingRule failed\n", __func__, __LINE__);
#endif

	return 0;
out:
	return rc;
}

static int itf_table_update(struct interface_table *ctx)
{
	int rc;

	__pthread_mutex_lock(&ctx->lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	rc = __itf_table_update(ctx);

#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&ctx->lock);

	return rc;
}


struct interface *__itf_get(int ifindex)
{
	struct interface *itf;

	itf = __itf_find(ifindex);
	if (!itf)
	{
		__itf_table_update(&itf_table);

		itf = __itf_find(ifindex);
		if (!itf)
			goto err;
	}

//	itf->count++;

	return itf;

err:
	return NULL;
}

void __itf_put(struct interface *itf)
{
#if 0
	itf->count--;
	if (itf->count <= 0)
		__itf_remove(itf);
#endif
}

/* itf_match_src_ipaddr
 *
 * Note: Locking the corresponding mutexes is the responsibility of the calling function, from calling thread.
 * Currently this routine is used only from route addition/change's context, where locking is already appropriately handled.
 */
int itf_match_src_ipaddr(int ifindex, int family, unsigned int *ipaddr )
{
	struct interface *itf;
	struct interface_addr *addr;
	struct list_head *entry;
	char address[INET6_ADDRSTRLEN];
	int rc = 0;

	itf = __itf_get(ifindex);
	if (!itf)
	{
		cmm_print(DEBUG_ERROR, "%s: itf does not exist for ifindex %d\n", __func__, ifindex);
		rc = 1;
		goto out;
	}

	for (entry = list_first(&itf->addr_list); entry != &itf->addr_list; entry = list_next(entry))
	{
		addr = container_of(entry, struct interface_addr, list);
		if (addr->family == family)
		{
			if(!memcmp(ipaddr, addr->address, addr->len))
			{
				cmm_print(DEBUG_INFO,"%s matches source address of interface %s",inet_ntop(family, ipaddr, address, sizeof(address)),
													itf->ifname);
				rc = 1;
				break;
			}

		}
	}

	__itf_put(itf);
out:
	return rc;
}


int itf_get_ipaddr(int ifindex, int family, unsigned char scope, unsigned int *ipaddr, unsigned int *target)
{
	struct interface *itf;
	struct interface_addr *addr;
	struct list_head *entry;
	int rc = -1;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	itf = __itf_get(ifindex);
	if (!itf)
		goto unlock;

	for (entry = list_first(&itf->addr_list); entry != &itf->addr_list; entry = list_next(entry))
	{
		addr = container_of(entry, struct interface_addr, list);
		if ((addr->family == family) && (addr->scope == scope) && (((family == AF_INET) && cmmPrefixEqual(target, addr->address, addr->prefixlen)) || (family == AF_INET6)))
		{
			memcpy(ipaddr, addr->address, addr->len);
			rc = 0;
			break;
		}
	}

	__itf_put(itf);

unlock:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return rc;
}

int __itf_get_macaddr(struct interface *itf, unsigned char *macaddr)
{
	if (!itf->macaddr_len)
		goto err;

	memcpy(macaddr, itf->macaddr, itf->macaddr_len);

	return 0;

err:
	return -1;
}

int itf_get_macaddr(int ifindex, unsigned char *macaddr)
{
	struct interface *itf;
	int rc = -1;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	itf = __itf_get(ifindex);
	if (!itf)
		goto unlock;

	rc = __itf_get_macaddr(itf, macaddr);

	__itf_put(itf);

unlock:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return rc;
}

int __itf_get_mtu(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	rc = itf->mtu;

out:
	return rc;
}


int ____itf_get_name(struct interface *itf, char *ifname, int len)
{
	if (!if_indextoname(itf->ifindex, itf->ifname))
	{
		cmm_print(DEBUG_WARNING, "%s: if_indextoname() failed\n", __func__);

		if (itf->ifname[0] == '\0')
			goto err;
	}

	len = (len < IFNAMSIZ ? len : IFNAMSIZ) - 1;

	ifname[len] = '\0';

	memcpy(ifname, itf->ifname, len);

	return 0;

err:
	return -1;
}

int __itf_get_name(int ifindex, char *ifname, int len)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	rc = ____itf_get_name(itf, ifname, len);

out:
	return rc;
}


int ____itf_is_bridge(struct interface *itf)
{
	if (itf->itf_flags & ITF_BRIDGE)
		return 1;

	return 0;
}

int __itf_is_bridge(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	if (itf->itf_flags & ITF_BRIDGE)
		rc = 1;
	else
		rc = 0;

out:
	return rc;
}

#if defined(LS1043)
int __itf_is_bridged_port(struct interface *itf)
{
	if (itf->itf_flags & ITF_BRIDGED_PORT)
		return 1;

	return 0;
}
#endif

#ifdef WIFI_ENABLE
int __itf_is_wifi(struct interface *itf)
{
	if (itf->itf_flags & ITF_WIFI)
		return 1;

	return 0;
}
#endif

int __itf_is_vlan(struct interface *itf)
{
	if (itf->itf_flags & ITF_VLAN)
		return 1;

	return 0;
}


int __itf_is_pointopoint(struct interface *itf)
{
	if (itf->ifi_flags & IFF_POINTOPOINT)
		return 1;

	return 0;
}


int __itf_is_pppoe(struct interface *itf)
{
	if ((itf->type == ARPHRD_PPP) && !(itf->itf_flags & ITF_L2TP))
		return 1;

	return 0;
}


int __itf_is_noarp(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	if (itf->ifi_flags & IFF_NOARP)
		rc = 1;
	else
		rc = 0;

out:
	return rc;
}


int __itf_is_up(struct interface *itf)
{
	if (itf->ifi_flags & IFF_UP)
		return 1;

	return 0;
}


int __itf_is_tunnel(struct interface *itf)
{
	if (itf->itf_flags & ITF_TUNNEL)
		return 1;

	return 0;
}

int __itf_is_l2tp(struct interface *itf)
{
	if (itf->itf_flags & ITF_L2TP)
		return 1;

	return 0;
}

#ifndef VLAN_FILTER
int __itf_get_from_bridge_port(int ifindex, int port)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	if (!____itf_is_bridge(itf))
		goto out;

	if (port >= MAX_PORTS)
		goto out;

	rc = itf->ifindices[port];

out:
	return rc;
}
#endif

int ____itf_is_programmed(struct interface *itf)
{
	int i;

	if (itf->flags & FPP_PROGRAMMED)
		return 1;
	else {
		/* LAN/WAN interfaces are programmed in FPP by default */
		for (i = 0; i < GEM_PORTS; i++)
		{
			if (port_table[i].enable && port_table[i].ifindex == itf->ifindex)
				return 1;
		}

		return 0;

	}
}

int __itf_is_programmed(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	rc = ____itf_is_programmed(itf);

out:
	return rc;
}


int itf_is_programmed(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */

	itf = __itf_get(ifindex);
	if (!itf)
		goto out;

	rc = ____itf_is_programmed(itf);

out:
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return rc;
}

int __itf_is_macvlan(struct interface *itf)
{
	if (itf->itf_flags & ITF_MACVLAN)
		return 1;

	return 0;
}

int ____itf_is_4o6_tunnel(struct interface *itf)
{
	if (!__itf_is_tunnel(itf) || (itf->tunnel_parm6.proto != IPPROTO_IPIP ))
		return 0;
	else
		return 1;
}


int ____itf_is_floating_sit_tunnel(struct interface *itf)
{
	if (!__itf_is_tunnel(itf) || (itf->type != ARPHRD_SIT) || itf->tunnel_parm4.iph.daddr)
		return 0;
	else
		return 1;
}


int __itf_is_floating_sit_tunnel(int ifindex)
{
	struct interface *itf;
	int rc = -1;

	itf = __itf_find(ifindex);
	if (!itf)
		goto out;

	rc = ____itf_is_floating_sit_tunnel(itf);

out:
	return rc;
}

int itf_name_update(FCI_CLIENT *fci_handle, struct gemac_port *port)
{
	/* Send a message to FPP to set the interface name associated with  each GEM Port */
	fpp_port_update_cmd_t cmd;
	int ret;

	cmd.port_id = port->port_id;
	strncpy(cmd.ifname, port->ifname, sizeof(cmd.ifname));
	cmd.ifname[sizeof(cmd.ifname) - 1] = '\0';

	cmm_print(DEBUG_INFO, "%s: port mapping %d <=> %s\n", __func__, cmd.port_id, cmd.ifname);

	if (FPP_ERR_OK != (ret = fci_write(fci_handle, FPP_CMD_PORT_UPDATE , sizeof(cmd), (unsigned short *) &cmd)))
	{
		cmm_print(DEBUG_CRIT, "%s: Port update failed in FPP %d \n", __func__, ret);
		return -1;
	}

	return 0;
}

int itf_table_init(struct interface_table *ctx)
{
	int i;

	pthread_mutex_init(&ctx->lock, NULL);

	for (i = 0; i < ITF_HASH_TABLE_SIZE; i++)
		list_head_init(&ctx->hash[i]);

	for ( i = 0; i < GEM_PORTS; i++)
	{
		port_table[i].ifindex = if_nametoindex(port_table[i].ifname);
		if (!port_table[i].ifindex)
			cmm_print(DEBUG_ERROR, "%s::%d: if_nametoindex(%s) failed\n", __func__, __LINE__,  port_table[i].ifname);
	}

	LO_IFINDEX = if_nametoindex(LO_INTERFACE_NAME);
	if (!LO_IFINDEX)
		cmm_print(DEBUG_ERROR, "%s::%d: if_nametoindex(%s) failed\n", __func__, __LINE__, LO_INTERFACE_NAME);
	
	ctx->fp = fopen(PPPOE_PATH, "r");
	/* we will retry later if it fails here, this happens when ppp modules are not loaded yet */

	ctx->fd = socket(AF_INET, SOCK_DGRAM, 0);
	if (ctx->fd < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: inet socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err1;
	}

	/* get netlink link and address information */
	if (cmm_rtnl_open(&ctx->rth, 0) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: netlink socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err2;
	}

	ctx->fci_handle = fci_open(FCILIB_FF_TYPE, 0);
	if (!ctx->fci_handle)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: fci_open() %s\n", __func__, __LINE__, strerror(errno));
		goto err3;
	}

#if !defined(IPSEC_SUPPORT_DISABLED)
	ctx->fci_key_handle = fci_open(FCILIB_KEY_TYPE, 0);
	if (!ctx->fci_key_handle)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: fci_open() %s\n", __func__, __LINE__, strerror(errno));
		goto err4;
	}
#endif

#ifdef WIFI_ENABLE
	cmmWiFiReset(ctx->fci_handle);
#endif
	cmmVlanReset(ctx->fci_handle);

	if (itf_table_update(ctx) < 0)
		goto err4;

	return 0;

#if !defined(IPSEC_SUPPORT_DISABLED)
err4:
	fci_close(ctx->fci_handle);
#endif

err3:
	cmm_rtnl_close(&ctx->rth);

err2:
	close(ctx->fd);

err1:
	if (ctx->fp)
		fclose(ctx->fp);

	return -1;
}


/*****************************************************************
* cmmRtnlLink
* 
*
******************************************************************/
int cmmRtnlLink(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct interface_table *ctx = arg;
	struct ifinfomsg *ifi;
	struct rtattr *tb[IFLA_MAX + 1];
	char ifname[IFNAMSIZ];

	switch (nlh->nlmsg_type)
	{
	case RTM_NEWLINK:
	case RTM_DELLINK:
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported LINK netlink message %x\n", __func__, nlh->nlmsg_type);
		goto out;
		break;
	}

	ifi = NLMSG_DATA(nlh);

	if (nlh->nlmsg_type == RTM_NEWLINK)
	{
		cmm_print(DEBUG_INFO, "%s: RTM_NEWLINK %s\n", __func__, if_indextoname(ifi->ifi_index, ifname));
	}
	else
	{
		cmm_print(DEBUG_INFO, "%s: RTM_DELLINK %s\n", __func__, if_indextoname(ifi->ifi_index, ifname));
	}

	cmm_print(DEBUG_INFO, "%s: ifinfomsg family: %x, type: %x, index: %d, flags: %x, change: %x\n", __func__,
				ifi->ifi_family, ifi->ifi_type,
				ifi->ifi_index, ifi->ifi_flags, ifi->ifi_change);

	cmm_parse_rtattr(tb, IFLA_MAX, IFLA_RTA(ifi), IFLA_PAYLOAD(nlh));

	updatelink(ctx, ifi, tb, nlh->nlmsg_type == RTM_DELLINK);

out:
	return RTNL_CB_CONTINUE;
}

/*****************************************************************
* cmmRtnlIfAddr
* 
*
******************************************************************/
int cmmRtnlIfAddr(const struct sockaddr_nl *who, struct nlmsghdr *nlh, void *arg)
{
	struct interface_table *ctx = arg;
	struct ifaddrmsg *ifa;
	struct rtattr *tb[IFA_MAX + 1];
	char address[INET6_ADDRSTRLEN];
	unsigned int *ipaddr;
#ifndef SAM_LEGACY
	struct ip6_4rd_map_msg *mr;
#endif

	switch (nlh->nlmsg_type)
	{
#ifndef SAM_LEGACY
	case RTM_NEW4RD:
        case RTM_DEL4RD:
		{
			mr = NLMSG_DATA(nlh);


			if(nlh->nlmsg_type == RTM_NEW4RD)
				mr_update(ctx->fci_handle,mr);
			else if(nlh->nlmsg_type == RTM_DEL4RD)
				mr_remove(ctx->fci_handle,mr);
			struct interface * itf = __itf_find(mr->ifindex);
			if (itf)
			{
				mr_debug(itf);
			}

			return 0;
		}
#endif
	case RTM_NEWADDR:
	case RTM_DELADDR:
		break;

	default:
		cmm_print(DEBUG_ERROR, "%s: unsupported IFADDR netlink message %x\n", __func__, nlh->nlmsg_type);
		goto out;
		break;
	}	

	ifa = NLMSG_DATA(nlh);

	cmm_print(DEBUG_INFO, "%s: ifaddr family: %x, prefixlen: %d, flags: %x, scope: %d, index: %d\n", __func__,
					ifa->ifa_family, ifa->ifa_prefixlen,
					ifa->ifa_flags, ifa->ifa_scope, ifa->ifa_index);

	cmm_parse_rtattr(tb, IFA_MAX, IFA_RTA(ifa), IFA_PAYLOAD(nlh));

	if (!tb[IFA_ADDRESS])
		goto out;

	ipaddr = RTA_DATA(tb[IFA_ADDRESS]);

	if (nlh->nlmsg_type == RTM_NEWADDR)
	{
		cmm_print(DEBUG_INFO, "%s: RTM_NEWADDR %s\n", __func__, inet_ntop(ifa->ifa_family, ipaddr, address, sizeof(address)));

		newaddr(ctx, ifa, tb);
	}
	else
	{
		cmm_print(DEBUG_INFO, "%s: RTM_DELADDR %s\n", __func__, inet_ntop(ifa->ifa_family, ipaddr, address, sizeof(address)));

		deladdr(ctx, ifa, tb);
	}

out:
	return RTNL_CB_CONTINUE;
}
