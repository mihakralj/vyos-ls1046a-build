/*
 *
 *  Copyright (C) 2014 Mindspeed Technologies, Inc.
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
#include "fpp_private.h"
#include "fpp.h"
#include "cmmd.h"
#include "itf.h"
#include "module_lro.h"

struct lro_interface {
	char ifname[IFNAMSIZ];
	int used;
};

#define LRO_MAX_ITF	4

static struct lro_interface lro_itf[LRO_MAX_ITF];

int lro_interface_add(char *ifname)
{
	int i;

	for (i = 0; i < LRO_MAX_ITF; i++)
	{
		if (lro_itf[i].used)
			continue;

		cmm_print(DEBUG_INFO, "%s: lro interface added\n", ifname);

		strncpy(lro_itf[i].ifname, ifname, IFNAMSIZ);
		STR_TRUNC_END(lro_itf[i].ifname, IFNAMSIZ);

		lro_itf[i].used = 1;

		return 0;
	}

	return -1;
}

void lro_interface_update(struct interface *itf)
{
	int i;
	char cmd[32 + IFNAMSIZ];

	for (i = 0; i < LRO_MAX_ITF; i++)
	{
		if (lro_itf[i].used && !strcmp(lro_itf[i].ifname, itf->ifname)) {

			cmm_print(DEBUG_INFO, "%s: lro enabled\n", itf->ifname);

			itf->flags |= ITF_LRO;

			snprintf(cmd, 32 + IFNAMSIZ, "ethtool -K %s lro on", itf->ifname);
			if(system(cmd) == -1)
				cmm_print(DEBUG_ERROR, "%s: system command failed...  \n", __func__);					
			break;
		}
	}
}

void lro_socket_open(FCI_CLIENT *fci_handle, struct ctTable *ctEntry)
{
	unsigned char proto;
	struct socket *s;
	const unsigned int *daddr, *saddr;
	unsigned short dport, sport;
	struct nf_conntrack *ct = ctEntry->ct;
	int ifindex;
	struct interface *itf;

	if (ctEntry->family != AF_INET)
		return;

	proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);
	if (proto != IPPROTO_TCP)
		return;

	if (ctEntry->flags & LOCAL_CONN_ORIG) {
		ifindex = nfct_get_attr_u32(ct, ATTR_REPL_COMCERTO_FP_IFINDEX);

		saddr = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
		daddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);

		sport = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
		dport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
	} else {
		ifindex = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IFINDEX);

		saddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		daddr = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);

		sport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
		dport = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
	}

	itf = __itf_find(ifindex);
	if (!itf)
		return;

	if (!(itf->flags & ITF_LRO))
		return;

	if (!____itf_is_programmed(itf))
		return;

	__pthread_mutex_lock(&socket_lock);

	s = socket_find_by_addr(AF_INET, saddr, daddr, sport, dport, IPPROTO_TCP);
	if (!s) {
		s = malloc(sizeof(struct socket));
		if (!s) {
			cmm_print(DEBUG_ERROR, "%s: malloc() failed\n", __func__);
			goto unlock;
		}

		memset(s, 0, sizeof(struct socket));

		s->family = AF_INET;

		s->id = new_socket_id();
		if (!s->id) {
			cmm_print(DEBUG_ERROR, "%s: No Socket ID available \n", __func__);

			free(s);

			goto unlock;
		}

		s->type = CMMD_SOCKET_TYPE_LRO;
		s->mode = SOCKET_CONNECTED;
		memcpy(s->saddr, saddr, IPADDRLEN(s->family));
		memcpy(s->daddr, daddr, IPADDRLEN(s->family));
		s->sport = sport;
		s->dport = dport;
		s->proto = IPPROTO_TCP;
		s->dscp = 0;
		s->fwmark = 0;
		s->queue = 0;

		__socket_add(s);
	}

	__socket_open(fci_handle, s);

unlock:
	__pthread_mutex_unlock(&socket_lock);
}


void lro_socket_close(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct ctTable *ctEntry)
{
	unsigned char proto;
	struct socket *s;
	const unsigned int *daddr, *saddr;
	unsigned short dport, sport;
	struct nf_conntrack *ct = ctEntry->ct;

	if (ctEntry->family != AF_INET)
		return;

	proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);
	if (proto != IPPROTO_TCP)
		return;

	if (ctEntry->flags & LOCAL_CONN_ORIG) {
		saddr = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
		daddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);

		sport = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
		dport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
	} else {
		saddr = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
		daddr = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);

		sport = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
		dport = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
	}

	__pthread_mutex_lock(&socket_lock);

	s = socket_find_by_addr(AF_INET, saddr, daddr, sport, dport, IPPROTO_TCP);
	if (!s)
		goto unlock;

	if (s->type == CMMD_SOCKET_TYPE_LRO)
	{
		__socket_close(fci_handle, fci_key_handle, s);
	}

unlock:
	__pthread_mutex_unlock(&socket_lock);
}

