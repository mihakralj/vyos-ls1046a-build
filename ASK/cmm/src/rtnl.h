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

#ifndef __RTNL_H__
#define __RTNL_H__

#include <linux/fib_rules.h>

#ifndef NDA_PAYLOAD
#define NDA_PAYLOAD(n)	NLMSG_PAYLOAD((n), sizeof(struct ndmsg))
#endif

#ifndef NDA_RTA
#define NDA_RTA(r) ((struct rtattr*)(((char*)(r)) + NLMSG_ALIGN(sizeof(struct ndmsg))))
#endif

#ifndef FRA_PAYLOAD
#define FRA_PAYLOAD(n)	NLMSG_PAYLOAD((n), sizeof(struct fib_rule_hdr))
#endif

#ifndef FRA_RTA
#define FRA_RTA(r)  ((struct rtattr*)(((char*)(r)) + NLMSG_ALIGN(sizeof(struct fib_rule_hdr))))
#endif

#define RTNL_NON_BLOCKING_MODE	(1 << 0)

#define RTNL_CB_CONTINUE	1
#define RTNL_CB_STOP		0
#define RTNL_CB_ERROR		-1

struct rtnl_handle
{
	int fd;
	int flags;
};

typedef int (*rtnl_filter_t)(const struct sockaddr_nl *, struct nlmsghdr *n, void *);

int cmm_nl_open(struct rtnl_handle *rth, unsigned subscriptions, unsigned int group);
int cmm_rtnl_open(struct rtnl_handle *rth, unsigned subscriptions);
void cmm_rtnl_close(struct rtnl_handle *rth);
int cmm_rtnl_fd(struct rtnl_handle *rth);
unsigned int cmm_rtnl_rcvbufsiz(struct rtnl_handle *rth, unsigned int size);
int cmm_rtnl_set_nonblocking_mode(struct rtnl_handle *rth);
void cmm_nlh_init(struct nlmsghdr *nlh, unsigned int len, unsigned short type, unsigned short flags);
int cmm_rtnl_send(struct rtnl_handle *rth, struct nlmsghdr *nlh);
int cmm_rtnl_dump_request(struct rtnl_handle *rth, int type, void *req, int len);
int cmm_rtnl_neigh_dump_request(struct rtnl_handle *rth, int family);
int cmm_rtnl_listen(struct rtnl_handle *rtnl, rtnl_filter_t handler, void *jarg);
int cmm_parse_rtattr(struct rtattr *tb[], int max, struct rtattr *rta, int len);
struct rtattr *cmm_get_rtattr(struct rtattr *rta, int len, int type);
void cmm_addattr_l(struct nlmsghdr *n, int maxlen, int type, const void *data, int alen);
int cmm_addattr8(struct nlmsghdr *n, int maxlen, int type, __u8 data);
int cmm_addattr16(struct nlmsghdr *n, int maxlen, int type, __u16 data);
int cmm_addattr32(struct nlmsghdr *n, int maxlen, int type, __u32 data);

#endif
