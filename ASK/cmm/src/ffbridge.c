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
#include "ffbridge.h"
#include "itf.h"
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <string.h>
#include <ctype.h>

#ifndef VLAN_FILTER
static void __cmmGetBridgePorts(int fd, struct interface *bridge)
{
	unsigned long args[4] = {BRCTL_GET_PORT_LIST, (unsigned long)bridge->ifindices, MAX_PORTS, 0};
	struct ifreq ifr;
	int max_ports;

	memset(bridge->ifindices, 0, MAX_PORTS * sizeof(int));
	if (____itf_get_name(bridge, ifr.ifr_name, sizeof(ifr.ifr_name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: ____itf_get_name(%d) failed\n", __func__, __LINE__, bridge->ifindex);

		goto out;
	}

	ifr.ifr_data = (char *) &args;

	if ((max_ports = ioctl(fd, SIOCDEVPRIVATE, &ifr)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: ioctl() %s\n", __func__, __LINE__, strerror(errno));
		goto out;
	}
out:
	return;
}

void __cmmGetBridges(int fd)
{
	int ifindices[MAX_BRIDGES];
	unsigned long args[3] = {BRCTL_GET_BRIDGES, (unsigned long)ifindices, MAX_BRIDGES};
	struct interface *itf;
	char ifname[IFNAMSIZ];
	int ifindex;
	int num, i;

	num = ioctl(fd, SIOCGIFBR, args);
	if (num < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: ioctl() %s\n", __func__, __LINE__, strerror(errno));

		goto out;
	}

	for (i = 0; i < num; i++)
	{
		ifindex = ifindices[i];

		itf = __itf_find(ifindex);
		if (!itf)
			continue;

		itf->itf_flags |= ITF_BRIDGE;

		__cmmGetBridgePorts(fd, itf);

		cmm_print(DEBUG_INFO, "%s::%d: %s is a bridge\n", __func__, __LINE__, if_indextoname(itf->ifindex, ifname));
	}

out:
	return;
}

static void cmmBrgetAllMacPort(int br_ifindex)
{
	struct __fdb_entry fe[CHUNK];
	int i, n;
	unsigned long args[4];
	struct ifreq ifr;
	int retries;
	int fd;

	cmm_print(DEBUG_INFO, "%s(%d)\n", __func__, br_ifindex);

	if ((fd = socket(AF_INET, SOCK_STREAM, 0)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err;
	}

	args[0] = BRCTL_GET_FDB_ENTRIES;
	args[1] = (unsigned long) fe;
	args[2] = CHUNK;
	args[3] = 0;

	if (__itf_get_name(br_ifindex, ifr.ifr_name, sizeof(ifr.ifr_name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_get_name(%d) failed\n", __func__, __LINE__, br_ifindex);

		goto close;
	}

	ifr.ifr_data = (char *) args;

	while (1) {
		retries = 0;

	retry:
		n = ioctl(fd, SIOCDEVPRIVATE, &ifr);
		/* table can change during ioctl processing */
		if (n < 0)
		{
			if (errno == EAGAIN)
			{
				if (++retries < 10)
					goto retry;
				else
					goto close;
			}

			cmm_print(DEBUG_ERROR, "%s::%d: ioctl() %s\n", __func__, __LINE__, strerror(errno));
			goto close;

		} else if (n == 0)
			goto close;

		for (i = 0; i < n; i++) {
			if (fe[i].is_local)
				continue;

			__cmmNeighUpdateAllMacs(br_ifindex, fe[i].mac_addr, fe[i].port_no);
		}

		args[3] += n;
	}

close:
	close(fd);

err:
	return;
}
#endif

int cmmBrToFF(struct RtEntry *route)
{
	int ifindex;
	char brname[IFNAMSIZ], ifname[IFNAMSIZ];
#ifdef VLAN_FILTER
	struct interface *br_itf;
	struct interface_bridge_vlan_info *vinfo = NULL;
	unsigned short vid;
#endif

	if (!route->neighEntry)
	{
		cmm_print(DEBUG_ERROR, "%s: neighbor entry not set in route\n", __func__);
		goto err;
	}

#ifdef VLAN_FILTER
	br_itf = __itf_find(route->oifindex);
	if (!br_itf) {
		cmm_print(DEBUG_ERROR, "%s: bridge index is missing in route\n", __func__);
		goto err;
	}

	/* Check if vlan filtering is enabled, if enabled try to fetch the PVID configured on bridge interface.
	Based on this vid, try to get the tagging information configured on egress interface.*/
	if (br_itf->vlan_filtering_enab)
	{
		route->vlan_filter_flags |= VLAN_FILTER_EN;

		/* Check first PVID on bridge first*/
		vinfo = vinfo_find_by_pvid(route->oifindex);
		if(vinfo) {
			/* Get the index of interface through which neighbour can be reached */
			ifindex = cmm_br_get_neigh(route->neighEntry->macAddr,route->oifindex,vinfo->vlan_info.vid);
			if (ifindex <= 0)
				goto err;

			vid = vinfo->vlan_info.vid;
			/* Extract port tag configuration on outgoing interface for matching vid*/
			vinfo = vinfo_find_by_vid(ifindex,vid);
			if(vinfo) {
				route->egress_vid = vinfo->vlan_info.vid;

				if (vinfo->vlan_info.flags & BRIDGE_VLAN_INFO_UNTAGGED) {
					route->vlan_filter_flags |= EGRESS_UNTAG;
					cmm_print(DEBUG_INFO, "%s: ifindex %d is Egress untagged\n", __func__, ifindex);
				}
			}
			else {
				cmm_print(DEBUG_ERROR, "%s: egress filtering is failed for vlan id %d\n", __func__, vid);
				goto err;
			}
		}
		else {
			cmm_print(DEBUG_ERROR, "%s PVID is not found on bridge\n",__func__);
			goto err;
		}
	}
	else
		ifindex = cmm_br_get_neigh(route->neighEntry->macAddr,route->oifindex,0);
#else
	/* FIXME Update also if more than N seconds have passed since last update */
	if (route->neighEntry->port < 0)
		cmmBrgetAllMacPort(route->oifindex);

	if (route->neighEntry->port < 0)
		goto err;

	ifindex = __itf_get_from_bridge_port(route->oifindex, route->neighEntry->port);

	if (ifindex <= 0)
		goto err;
#endif

	if (route->phys_oifindex != ifindex)
	{
		route->phys_oifindex = ifindex;
	}

	cmm_print(DEBUG_ERROR, "%s::%d: if:%s br:%s "
#ifndef VLAN_FILTER
			"port:%d "
#endif
			"mac:%.2x:%.2x:%.2x:%.2x:%.2x:%.2x\n",
			__func__, __LINE__,
			if_indextoname(route->phys_oifindex, ifname),
			if_indextoname(route->oifindex, brname),
#ifndef VLAN_FILTER
			route->neighEntry->port,
#endif
			route->neighEntry->macAddr[0], route->neighEntry->macAddr[1],
			route->neighEntry->macAddr[2], route->neighEntry->macAddr[3],
			route->neighEntry->macAddr[4], route->neighEntry->macAddr[5]);

	return 0;

err:
	return -1;
}


/* This function gets the physical port information from the bridge_port */
int cmmBrGetPhysItf(int br_ifindex, unsigned char *fdb_mac)
{
#ifdef VLAN_FILTER
	return cmm_br_get_neigh(fdb_mac,br_ifindex,0);
#else
	struct __fdb_entry fe[CHUNK];
	int i, n;
	unsigned long args[4];
	struct ifreq ifr;
	int retries;
	int fd, phys_ifindex = -1;

	cmm_print(DEBUG_INFO, "%s(%d)\n", __func__, br_ifindex);

	if ((fd = socket(AF_INET, SOCK_STREAM, 0)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err;
	}

	args[0] = BRCTL_GET_FDB_ENTRIES;
	args[1] = (unsigned long) fe;
	args[2] = CHUNK;
	args[3] = 0;

	if (__itf_get_name(br_ifindex, ifr.ifr_name, sizeof(ifr.ifr_name)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s::%d: __itf_get_name(%d) failed\n", __func__, __LINE__, br_ifindex);

		goto close;
	}

	ifr.ifr_data = (char *) args;

	while (1) {
		retries = 0;

	retry:
		n = ioctl(fd, SIOCDEVPRIVATE, &ifr);
		/* table can change during ioctl processing */
		if (n < 0)
		{
			if (errno == EAGAIN)
			{
				if (++retries < 10)
					goto retry;
				else
					goto close;
			}

			cmm_print(DEBUG_ERROR, "%s::%d: ioctl() %s\n", __func__, __LINE__, strerror(errno));
			goto close;

		} else if (n == 0)
			goto close;

		for (i = 0; i < n; i++) {
			if (fe[i].is_local)
				continue;

			if (memcmp(fdb_mac, fe[i].mac_addr, 6) == 0)
			{
				cmm_print(DEBUG_INFO, "%s(%d) Found mac\n", __func__, fe[i].port_no);
				phys_ifindex = __itf_get_from_bridge_port(br_ifindex, fe[i].port_no);
				goto close;
			}
		}

		args[3] += n;
	}

close:
	close(fd);

err:
	return (phys_ifindex);
#endif
}


