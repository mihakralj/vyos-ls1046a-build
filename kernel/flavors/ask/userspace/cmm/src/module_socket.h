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

#ifndef __MODULE_SOCKET_H
#define __MODULE_SOCKET_H

#include <sys/types.h>
#include "list.h"
#include "forward_engine.h"

#define HASH_SOCKET_SIZE	32
#define HASH_SOCKET(id)		((id) & (HASH_SOCKET_SIZE - 1))

static __inline u_int32_t HASH_SOCK_ADDR(int family, const u_int32_t *Saddr, const u_int32_t *Daddr, u_int16_t Sport, u_int16_t Dport, u_int16_t Proto)
{
	unsigned int a, b;

	if (family == AF_INET)
	{
		a = jhash((void *)Saddr, 4, (Proto << 16) | Proto);
		b = jhash((void *)Daddr, 4, (Sport << 16) | Dport);
	}
	else
	{
        	a = jhash((void *)Saddr, 16, (Proto << 16) | Proto);
        	b = jhash((void *)Daddr, 16, (Sport << 16) | Dport);
	}

	return jhash_2words(a, b, 0x48375934) % HASH_SOCKET_SIZE;
}

#if defined(LS1043)
#define SOCKET_UNCONNECTED	1
#define SOCKET_CONNECTED		0
#define SOCKET_UNCONNECTED_WO_SRC  2
#else
#define SOCKET_UNCONNECTED	0
#define SOCKET_CONNECTED		1
#endif //LS1043

#define SOCK_ID_PRIVATE_START	1
#define SOCK_ID_PRIVATE_END	255

#if (SOCK_ID_PRIVATE_START > SOCK_ID_PRIVATE_END)
         #error SOCK_ID_PRIVATE_START cannot be greater than SOCK_ID_PRIVATE_END
#endif

#if (SOCK_ID_PRIVATE_START == 0)
	#error SOCK_ID_PRIVATE_START cannot be zero
#endif

#if (SOCK_ID_PRIVATE_END > 65535)
	#error SOCK_ID_PRIVATE_END cannot be greater than 65535
#endif
#define NUM_INTERNAL_SOCKET_ID	(SOCK_ID_PRIVATE_END - SOCK_ID_PRIVATE_START + 1)
#define SOCK_MAX_ID			0x7FFF

struct socket {
	struct list_head list;
	struct list_head list_by_addr;
	u_int8_t family;
	u_int16_t id;
	u_int8_t type;
	u_int8_t mode;
	u_int32_t saddr[4];
	u_int32_t daddr[4];
	u_int16_t sport;
	u_int16_t dport;
	u_int8_t proto;
	u_int8_t queue;
	u_int16_t dscp;
	struct ct_route rt;
	int iifindex;
	int flags;
	unsigned int fwmark;
#if defined(LS1043)
	u_int16_t       expt_flag;
	u_int16_t       rsvd;
#endif //(LS1043)
#if defined(COMCERTO_2000) || defined(LS1043)
	u_int16_t secure;
	struct FlowEntry *rx_flow;
	struct FlowEntry *tx_flow;
#endif
};

extern struct list_head socket_table[HASH_SOCKET_SIZE];
extern struct list_head socket_table_by_addr[HASH_SOCKET_SIZE];
extern pthread_mutex_t socket_lock;

int socket_daemon(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, int fc, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
int cmmSocketSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle, int family);
int cmmSocketShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int __socket_open(FCI_CLIENT *fci_handle, struct socket *s);
void socket_remove(struct socket *s);
void __cmmSocketUpdateWithRoute(FCI_CLIENT *fci_handle, struct RtEntry *route);
struct socket *socket_find_by_addr(int family, const u_int32_t *saddr, const u_int32_t *daddr, u_int16_t sport, u_int16_t dport, u_int8_t proto);
int __socket_close(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct socket *s);
void __socket_add(struct socket * s);
u_int32_t new_socket_id(void);
void del_socket_id(u_int32_t sock_id_ext);

#if defined(COMCERTO_2000) || defined(LS1043)
struct socket *__cmmSocketFindFromFlow(int family, unsigned int *saddr, unsigned int *daddr, unsigned char proto, char *orig);
#endif


#endif
