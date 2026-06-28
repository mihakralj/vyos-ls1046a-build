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

struct tunnel_info
{
	char ifname[IFNAMSIZ];
	unsigned char phys_ifindex;	
	unsigned char ipsec 	   : 1,
		itf_programmed : 1,
		neigh_programmed : 1,
		sa_programmed : 1;
	unsigned int tunnel_proto;
	unsigned int tunnel_family;
	unsigned int mtu;
	unsigned int local[4];
	unsigned int remote[4];
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

#endif /* __MODULE_TUNNEL_H__ */

