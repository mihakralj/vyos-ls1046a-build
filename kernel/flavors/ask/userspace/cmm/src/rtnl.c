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

/*********************************************************************************************/

/*
The following routines were derived from the libnetlink.c file in the iproute2 package.
Copyright notice from that file follows...
*/

/*
 * libnetlink.c	RTnetlink service routines.
 *
 *		This program is free software; you can redistribute it and/or
 *		modify it under the terms of the GNU General Public License
 *		as published by the Free Software Foundation; either version
 *		2 of the License, or (at your option) any later version.
 *
 * Authors:	Alexey Kuznetsov, <kuznet@ms2.inr.ac.ru>
 *
 */

void cmm_rtnl_close(struct rtnl_handle *rth)
{
	close(rth->fd);
}

int cmm_rtnl_fd(struct rtnl_handle *rth)
{
	return rth->fd;
}

unsigned int cmm_rtnl_rcvbufsiz(struct rtnl_handle *rth, unsigned int size)
{
	socklen_t socklen = sizeof(size);
	unsigned int read_size = 0;
	int rc = 0;

	if (setsockopt(rth->fd, SOL_SOCKET, SO_RCVBUFFORCE, &size, socklen) < 0) {
		/* if this didn't work, we try at least to get the system
		 * wide maximum (or whatever the user requested) */
		rc = setsockopt(rth->fd, SOL_SOCKET, SO_RCVBUF, &size, socklen);
		if (rc < 0)
			cmm_print(DEBUG_ERROR, "%s::%d: setsockopt() failed %s\n", __func__, __LINE__, strerror(errno));
	}

	rc = getsockopt(rth->fd, SOL_SOCKET, SO_RCVBUF, &read_size, &socklen);
	if (rc < 0)
		cmm_print(DEBUG_ERROR, "%s::%d: getsockopt() failed %s\n", __func__, __LINE__, strerror(errno));

	return read_size;
}

int cmm_rtnl_set_nonblocking_mode(struct rtnl_handle *rth)
{
	if (fcntl(rth->fd, F_SETFL, O_NONBLOCK) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: fcntl(%d) failed %s\n", __func__, rth->fd, strerror(errno));
		goto err0;
	}

	rth->flags = RTNL_NON_BLOCKING_MODE;

	return 0;

err0:
	return -1;
}
int cmm_nl_open(struct rtnl_handle *rth, unsigned subscriptions, unsigned int group)
{
	struct sockaddr_nl nladdr;

	memset(rth, 0, sizeof(*rth));

	rth->fd = socket(PF_NETLINK, SOCK_RAW, group);
	if (rth->fd < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: socket() %s\n", __func__, __LINE__, strerror(errno));
		goto err0;
	}

	memset(&nladdr, 0, sizeof(nladdr));
	nladdr.nl_family = AF_NETLINK;
	nladdr.nl_groups = subscriptions;
	nladdr.nl_pid = 0;

	if (bind(rth->fd, (struct sockaddr*)&nladdr, sizeof(nladdr)) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: bind() %s\n", __func__, __LINE__, strerror(errno));
		goto err1;
	}

	return 0;

err1:
	close(rth->fd);

err0:
	return -1;

}

int inline cmm_rtnl_open(struct rtnl_handle *rth, unsigned subscriptions)
{
	return cmm_nl_open(rth, subscriptions, NETLINK_ROUTE);
}

int cmm_rtnl_neigh_dump_request(struct rtnl_handle *rth, int family)
{
	struct ndmsg ndm = {
		.ndm_family = family,
		.ndm_ifindex = 0,
		.ndm_state = 0,
		.ndm_flags = 0,
		.ndm_type = 0,
	};

	return cmm_rtnl_dump_request(rth, RTM_GETNEIGH, &ndm, sizeof(struct ndmsg));
}

void cmm_nlh_init(struct nlmsghdr *nlh, unsigned int len, unsigned short type, unsigned short flags)
{
	memset(nlh, 0, sizeof(struct nlmsghdr));
	nlh->nlmsg_len = NLMSG_SPACE(len);
	nlh->nlmsg_type = type;
	nlh->nlmsg_flags = flags;
	nlh->nlmsg_pid = 0;
	nlh->nlmsg_seq = 0;
}

int cmm_rtnl_send(struct rtnl_handle *rth, struct nlmsghdr *nlh)
{
	struct sockaddr_nl nladdr;
	struct iovec iov = {
		.iov_base = nlh, .iov_len = nlh->nlmsg_len,
	};
	struct msghdr msg= {
                .msg_name = &nladdr,
                .msg_namelen =  sizeof(nladdr),
                .msg_iov = &iov,
                .msg_iovlen = 1,
        };
	int rc;

	memset(&nladdr, 0, sizeof(nladdr));
	nladdr.nl_family = AF_NETLINK;
	nladdr.nl_groups = 0;
	nladdr.nl_pid = 0;

	if ((rc = sendmsg(rth->fd, &msg, 0)) < 0) {
		cmm_print(DEBUG_ERROR, "%s::%d: sendmsg() %s\n", __func__, __LINE__, strerror(errno));
	}

	return rc;
}

int cmm_rtnl_dump_request(struct rtnl_handle *rth, int type, void *req, int len)
{
	char buf[NLMSG_SPACE(len)] __attribute__ ((aligned (4)));
	struct nlmsghdr *nlh = (struct nlmsghdr *)buf;

	cmm_nlh_init(nlh, len, type, NLM_F_REQUEST | NLM_F_DUMP);

	memcpy(NLMSG_DATA(nlh), req, len);

	return cmm_rtnl_send(rth, nlh);
}

int cmm_rtnl_listen(struct rtnl_handle *rth, rtnl_filter_t handler, void *jarg)
{
	int len;
	struct nlmsghdr *nlh;
	struct sockaddr_nl nladdr;
	struct iovec iov;
	struct msghdr msg = {
		.msg_name = &nladdr,
		.msg_namelen = sizeof(nladdr),
		.msg_iov = &iov,
		.msg_iovlen = 1,
	};
	char *buf;
	int rc;
	uint32_t  buf_size;

	memset(&nladdr, 0, sizeof(nladdr));
	nladdr.nl_family = AF_NETLINK;
	nladdr.nl_pid = 0;
	nladdr.nl_groups = 0;

	buf = cmm_get_rtnl_buf(&buf_size);
	iov.iov_base = buf;
	do {
		iov.iov_len = buf_size;
		len = recvmsg(rth->fd, &msg, 0);

		if (len < 0) {
			if (errno == EINTR)
				continue;

			if (errno == EAGAIN)
				goto err;

			cmm_print(DEBUG_ERROR, "%s::%d: recvmsg() %s\n", __func__, __LINE__, strerror(errno));

			goto err;
		}

		if (len == 0) {
			cmm_print(DEBUG_ERROR, "%s::%d: EOF on netlink\n", __func__, __LINE__);
			goto err;
		}

		if (msg.msg_namelen != sizeof(nladdr)) {
			cmm_print(DEBUG_ERROR, "%s::%d: wrong sender address len(%d)\n", __func__, __LINE__, msg.msg_namelen);
			goto err;
		}

		nlh = (struct nlmsghdr *)buf;

		while (NLMSG_OK(nlh, len)) {

			if (nlh->nlmsg_type == NLMSG_ERROR) {
				struct nlmsgerr *err = NLMSG_DATA(nlh);

				cmm_print(DEBUG_ERROR, "%s::%d: netlink error %s\n", __func__, __LINE__, strerror(-err->error));
				goto err;
			}

			if (nlh->nlmsg_type == NLMSG_DONE)
				goto out;

			rc = handler(&nladdr, nlh, jarg);
			if (rc <= RTNL_CB_STOP) {
				if (rc == RTNL_CB_STOP)
					goto out;
 
				goto err;
			}

			nlh = NLMSG_NEXT(nlh, len);
		}

		if (msg.msg_flags & MSG_TRUNC) {
			cmm_print(DEBUG_ERROR, "%s::%d: truncated message\n", __func__, __LINE__);
			goto err;
		}

	} while (!(rth->flags & RTNL_NON_BLOCKING_MODE));

out:
	cmm_free_rtnl_buf(buf);
	return 0;

err:
	cmm_free_rtnl_buf(buf);
	return -1;
}

int cmm_parse_rtattr(struct rtattr *tb[], int max, struct rtattr *rta, int len)
{
	memset(tb, 0, sizeof(struct rtattr *) * (max + 1));

	while (RTA_OK(rta, len)) {
		if (rta->rta_type <= max)
			tb[rta->rta_type] = rta;

		rta = RTA_NEXT(rta,len);
	}

	if (len)
		cmm_print(DEBUG_ERROR, "%s::%d: payload too long %d %d\n", __func__, __LINE__, len, rta->rta_len);

	return 0;
}

struct rtattr *cmm_get_rtattr(struct rtattr *rta, int len, int type)
{
	while (RTA_OK(rta, len)) {

		if (rta->rta_type == type)
			goto found;

		rta = RTA_NEXT(rta, len);
	}

	return NULL;

found:
	return rta;
}


void cmm_addattr_l(struct nlmsghdr *n, int maxlen, int type, const void *data, int alen)
{
	int len = RTA_LENGTH(alen);
	struct rtattr *rta;

	rta = NLMSG_TAIL(n);
	rta->rta_type = type;
	rta->rta_len = len;
	memcpy(RTA_DATA(rta), data, alen);
	n->nlmsg_len = NLMSG_ALIGN(n->nlmsg_len) + RTA_ALIGN(len);
}

int cmm_addattr8(struct nlmsghdr *n, int maxlen, int type, __u8 data)
{
	int len = RTA_LENGTH(1);
	struct rtattr *rta;

	rta = NLMSG_TAIL(n);
	rta->rta_type = type;
	rta->rta_len = len;
	memcpy(RTA_DATA(rta), &data, 1);
	n->nlmsg_len = NLMSG_ALIGN(n->nlmsg_len) + len;
	return 0;
}

int cmm_addattr16(struct nlmsghdr *n, int maxlen, int type, __u16 data)
{
	int len = RTA_LENGTH(2);
	struct rtattr *rta;

	rta = NLMSG_TAIL(n);
	rta->rta_type = type;
	rta->rta_len = len;
	memcpy(RTA_DATA(rta), &data, 2);
	n->nlmsg_len = NLMSG_ALIGN(n->nlmsg_len) + len;
	return 0;
}

int cmm_addattr32(struct nlmsghdr *n, int maxlen, int type, __u32 data)
{
	int len = RTA_LENGTH(4);
	struct rtattr *rta;

	rta = NLMSG_TAIL(n);
	rta->rta_type = type;
	rta->rta_len = len;
	memcpy(RTA_DATA(rta), &data, 4);
	n->nlmsg_len = NLMSG_ALIGN(n->nlmsg_len) + len;
	return 0;
}

