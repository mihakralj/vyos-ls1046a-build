 /*
  *  module_tunnel.h: Tunnel module header file
  *
  *  Copyright (C) 2007 Mindspeed Technologies, Inc.
  *  Copyright 2014-2016 Freescale Semiconductor, Inc.
  *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
  */

#include "client_daemon.h"

#ifndef __MODULE_TUNNEL_H__
#define __MODULE_TUNNEL_H__

#ifdef SAM_LEGACY
#define DEFAULT_SAM_FRAG_MTU 1460 /* This is the value that will be used for all fragmentation
decisions on packets intended for the tunnel MTU. This is a cumulative fix on an ACP fix where
the tunnel interface MTU is configured as 1500(instead of the real 1460 (1500 - IPv6hdr size))
Bytes in order to force IPv6 fragmentation */
#endif

#ifndef SAM_LEGACY
struct map_rule {
	struct list_head list;
	struct ip6_4rd_map_msg rule;
 };
#endif

struct tunnel_info
{
	char ifname[IFNAMSIZ];
	unsigned char phys_ifindex;	
	unsigned char ipsec 	   : 1,
		itf_programmed : 1,
		neigh_programmed : 1,
		sa_programmed : 1,	
		conf_6rd:1 ;
	unsigned int tunnel_proto;
	unsigned int tunnel_family;
	unsigned int mtu;
	unsigned int local[4];
	unsigned int remote[4];
	struct ip_tunnel_6rd tunnel_parm6rd;
};

/* dscp proppagation */
#define INHERIT_TC 0x1
#define DSCP_COPY  0x2

/* Functions prototypes */
int tunnel_daemon_msg_recv(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
int cmm_tunnel_parse_cmd(int argc, char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int __tunnel_add(FCI_CLIENT *fci_handle, struct interface *itf);
int __tunnel_del(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct interface *itf);
int __tunnel_update(FCI_CLIENT *fci_handle, struct interface *itf);
unsigned int tunnel_get_ipv4_dst(struct RtEntry *route, struct interface *itf);

struct interface *__cmmTunnelFindFromFlow(int family, unsigned int *saddr, unsigned int *daddr, unsigned char proto, char *orig);

void __cmmTunnelUpdateWithRoute(FCI_CLIENT *fci_handle, struct RtEntry *route);
int __cmmGetTunnel(int fd, struct interface *itf, struct rtattr *tb[]);
int __cmmGetTunnel_gre6(int fd, struct interface *itf, struct rtattr *tb[]);
int cmmTnlQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmm4rdIdConvSetProcess(char ** keywords, int tabStart, int argc, daemon_handle_t daemon_handle);
int getTunnel4rdAddress(struct interface* itf, u_int32_t * Daddrv6,  unsigned int Daddr, unsigned short Dport);

#endif /* __MODULE_TUNNEL_H__ */

