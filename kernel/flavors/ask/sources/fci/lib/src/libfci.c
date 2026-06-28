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
 */

#include <sys/socket.h>
#include <sys/types.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <linux/netlink.h>
#include <unistd.h>

#include "libfci.h"

#define FCI_SOCK_SIZE 1048576

#ifndef NETLINK_FF
#define NETLINK_FF 30
#endif

#ifndef NETLINK_KEY
#define NETLINK_KEY 32
#endif

/*
* Debug macros
*/
#define FCILIB_PRINT	0
#define FCILIB_ERR	0
#define	FCILIB_INIT	0
#define	FCILIB_OPEN	0
#define	FCILIB_CLOSE	0
#define	FCILIB_WRITE	0
#define	FCILIB_READ	0
#define FCILIB_DUMP	0
#define FCILIB_CATCH	0

#ifdef FCILIB_PRINT
#define FCILIB_PRINTF(type, info, args...) do {if(type) fprintf(stderr, info, ## args);} while(0);
#else
#define FCILIB_PRINTF(type, info, args...) do {} while(0);
#endif

#define FCI_PAYLOAD(n)	NLMSG_PAYLOAD((n), sizeof(struct fci_hdr))
#define FCI_DATA(f)	((unsigned short *)((char *)(f) + sizeof(struct fci_hdr)))

struct fci_hdr
{
	u_int16_t fcode;
	u_int16_t len;
} __attribute__((packed));

static FCI_CLIENT *fci_create_client(int nl_type, unsigned long group);
static int fci_destroy_client(FCI_CLIENT *this_client);
static int fci_read(FCI_CLIENT *this_client, struct iovec *iov, int iovlen);
static int fci_get_response(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *rep_buf, unsigned short *rep_len);
static int fci_process_data(FCI_CLIENT *this_client, unsigned char *hdr, unsigned short *buf, int len);
static int __fci_cmd(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *cmd_buf, unsigned short cmd_len, unsigned short *rep_buf, unsigned short *rep_len);

#ifdef ARCH_ODP
static unsigned int cmd_socket_src_id = CMM_SOCK_PID_CMD;
static unsigned int event_socket_src_id = CMM_SOCK_PID_EVENT;
#endif

/****************************** PUBLICS FUNCTIONS ********************************/

/*
 * fci_open -
 *
 */
FCI_CLIENT *fci_open(unsigned long client_type, unsigned long group)
{
	FCI_CLIENT *new_client = NULL;
	
	/* Create client according to the requested socket type */
	switch(client_type)
	{
		case FCILIB_FF_TYPE:
			FCILIB_PRINTF(FCILIB_OPEN, "fci_open:%d client type FCILIB_FF_CLIENT with group %ld\n", __LINE__, group);
#ifdef ARCH_ODP
			new_client = fci_create_client(NETLINK_GENERIC, group);
#else
			new_client = fci_create_client(NETLINK_FF, group);
#endif
		break;

		case FCILIB_KEY_TYPE:
			FCILIB_PRINTF(FCILIB_OPEN, "fci_open:%d client type FCILIB_KEY_CLIENT with group %ld\n", __LINE__, group);
			new_client = fci_create_client(NETLINK_KEY, group);
		break;

		default:
			FCILIB_PRINTF(FCILIB_ERR, "LIB_FCI: fci_open():%d client type %ld not supported\n", __LINE__, client_type);
			new_client = NULL;
		break;
	}

	/* Unique ID used to identify this client */
	return new_client;
}


/*
 * fci_register_cb -
 *
 */
int fci_register_cb(FCI_CLIENT *this_client, int (*cb)(unsigned short fcode, unsigned short len, unsigned short *payload))
{
	if(this_client != NULL)
	{
		this_client->event_cb = cb;

		FCILIB_PRINTF(FCILIB_INIT, "fci_register_cb(): event callback registered for socket id %d\n", this_client->nl_sock_id);

		return 0;
	}
	else
	{
		return -1;
	}
}


/*
 * fci_close -
 *
 */
int fci_close(FCI_CLIENT *this_client)
{
	int rc;

	FCILIB_PRINTF(FCILIB_CLOSE, "fci_close: socket id %d\n", this_client->nl_sock_id);

	/* unregister FCI client */
	if (this_client == NULL)
		return -1;

	if ((rc = fci_destroy_client(this_client)) < 0)
	{
		FCILIB_PRINTF(FCILIB_ERR, "fci_close: fci_destroy_client failed !\n");	

		return rc;
	}

	return 0;
}


/*
 * fci_cmd -
 *
 */
int fci_cmd(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *cmd_buf, unsigned short cmd_len, unsigned short *rep_buf, unsigned short *rep_len)
{
	FCILIB_PRINTF(FCILIB_WRITE, "%s: send fcode %#x length %d through socket %d\n", __func__, fcode, cmd_len, this_client->nl_sock_id);

	return __fci_cmd(this_client, fcode, cmd_buf, cmd_len, rep_buf, rep_len);
}


/*
 * fci_write -
 *
 */
int fci_write(FCI_CLIENT *this_client, unsigned short fcode, unsigned short cmd_len, unsigned short *cmd_buf)
{
	unsigned short rep_buf[FCI_MAX_PAYLOAD / sizeof(u_int16_t)] __attribute__ ((aligned (4)));
	unsigned short rep_len = sizeof(rep_buf);
	int rc;

	FCILIB_PRINTF(FCILIB_WRITE, "%s: send fcode %#x length %d through socket %d\n", __func__, fcode, cmd_len, this_client->nl_sock_id);

	rep_buf[0] = 0;
	rc = __fci_cmd(this_client, fcode, cmd_buf, cmd_len, rep_buf, &rep_len);
	if (rc < 0)
		return rc;

	return rep_buf[0];
}

/*
 * fci_query -
 *
 */
int fci_query(FCI_CLIENT *this_client, unsigned short fcode, unsigned short cmd_len, unsigned short *cmd_buf, unsigned short *rep_len, unsigned short *rep_buf)
{
	unsigned short lrep_buf[FCI_MAX_PAYLOAD / sizeof(u_int16_t)] __attribute__ ((aligned (4)));
	unsigned short lrep_len = sizeof(lrep_buf);
	int rc;

	FCILIB_PRINTF(FCILIB_WRITE, "%s: send fcode %#x length %d through socket %d\n", __func__, fcode, cmd_len, this_client->nl_sock_id);

	if (rep_len)
		*rep_len = 0;

	rc = __fci_cmd(this_client, fcode, cmd_buf, cmd_len, lrep_buf, &lrep_len);
	if (rc < 0)
		return rc;

	if ((lrep_len > 2) && rep_len && rep_buf)
	{
		memcpy(rep_buf, lrep_buf + 1, lrep_len - 2);
		*rep_len = lrep_len - 2;
	}

	return lrep_buf[0];
}


/*
 * fci_catch -
 *
 */
int fci_catch(FCI_CLIENT *this_client)
{
	unsigned char hdr[NLMSG_LENGTH(sizeof(struct fci_hdr))] __attribute__ ((aligned (4)));
	unsigned short rep_buf[FCI_MAX_PAYLOAD / sizeof(u_int16_t)] __attribute__ ((aligned (4)));
	struct iovec iov[]= {
		{
			.iov_base = &hdr,
			.iov_len = sizeof(hdr)
		},
		{
			.iov_base = rep_buf,
			.iov_len = sizeof(rep_buf)
		}
	};
	int rc;


	if(this_client == NULL)
	{
		return -1;
	}

	FCILIB_PRINTF(FCILIB_CATCH,"%s: socket_id %d\n", __func__, this_client->nl_sock_id);
	/* now, listen to the netlink subsystem */
	while (1)
	{
		rc = fci_read(this_client, iov, 2);
		if (rc < 0) 
		{
			/* we got interrupted prematurely, retry ... */
			if (errno == EINTR)
				continue;

			if (errno == EAGAIN)
				break;

			FCILIB_PRINTF(FCILIB_ERR,"%s: fci_read() failed %s\n", __func__, strerror(errno));

			break;
		}

		/* process incoming data from kernel */
		rc = fci_process_data(this_client, hdr, rep_buf, rc);

		if (rc <= FCI_CB_STOP)
			break;
	}

	return rc;
}

/*
 * fci_fd -
 *
 */
int fci_fd(FCI_CLIENT *this_client)
{
	return this_client->nl_sock_id;
}


/****************************** PRIVATES FUNCTIONS ********************************/

static struct fci_hdr *fci_check_msg(unsigned char *hdr, int len)
{
	struct nlmsghdr *nlh;
	struct fci_hdr *fh;

	/* get fci message within the reveived buffer */
	nlh = (struct nlmsghdr *)hdr;

	if (!NLMSG_OK(nlh, len))
	{
		FCILIB_PRINTF(FCILIB_ERR, "LIBFCI: %s() netlink message not ok %d %zu %d\n", __func__, len, sizeof(struct nlmsghdr), nlh->nlmsg_len);
		goto err;
	}

	if (nlh->nlmsg_type == NLMSG_ERROR)
	{
		struct nlmsgerr *err = NLMSG_DATA(nlh);
		errno = -err->error;
		goto err;
	}

	if (nlh->nlmsg_type == NLMSG_DONE)
		goto err;

	if (NLMSG_PAYLOAD(nlh, 0) < sizeof(struct fci_hdr))
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: %s() message too short(%d)\n", __func__, len);
		goto err;
	}

	fh = NLMSG_DATA(nlh);

	if (FCI_PAYLOAD(nlh) < fh->len)
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: %s() message truncated(%d, %zu)\n", __func__, len, sizeof(struct fci_hdr) + fh->len);
		goto err;
	}

	return fh;

err:
	return NULL;
}

/*
 * __fci_cmd -
 *
 */
static int __fci_cmd(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *cmd_buf, unsigned short cmd_len, unsigned short *rep_buf, unsigned short *rep_len)
{
	unsigned char hdr[NLMSG_LENGTH(sizeof(struct fci_hdr))] __attribute__ ((aligned (4)));
	struct iovec iov[]= {
		{
			.iov_base = &hdr,
			.iov_len = sizeof(hdr)
		},
		{
			.iov_base = cmd_buf,
			.iov_len = cmd_len
		}
	};

	struct msghdr msg = {
		.msg_name = &this_client->dst_addr,
		.msg_namelen = sizeof(struct sockaddr_nl),
		.msg_iov = iov,
		.msg_iovlen = 2,
	};
	struct nlmsghdr *nlh;
	struct fci_hdr *fh;
	int rc;

	
	FCILIB_PRINTF(FCILIB_WRITE, "%s: send fcode %#x length %d through socket %d\n", __func__, fcode, cmd_len, this_client->nl_sock_id);

	nlh = (struct nlmsghdr *)hdr;

	nlh->nlmsg_len = NLMSG_SPACE(sizeof(struct fci_hdr) + cmd_len);

	 /* standard message type */
	nlh->nlmsg_type = 0;

	/* sender PID */
#ifdef ARCH_ODP
	nlh->nlmsg_pid = this_client->src_addr.nl_pid;
	iov[1].iov_len = nlh->nlmsg_len - iov[0].iov_len;
#else
	nlh->nlmsg_pid = 0;
#endif


	/* don't ask for an answer */
	nlh->nlmsg_flags = (NLM_F_REQUEST);

	nlh->nlmsg_seq = 0;

	fh = NLMSG_DATA(nlh);

	fh->fcode = fcode;
	fh->len = cmd_len;

	/* Post the message to Netlink stack */	
	rc = sendmsg(this_client->nl_sock_id, &msg, 0);
	if (rc < 0)
	{
		FCILIB_PRINTF(FCILIB_ERR, "LIBFCI: sendto(%d) failed %s\n", this_client->nl_sock_id, strerror(errno));
		
		goto out;
	}	

	if (this_client->nl_type != NETLINK_KEY)
	{
		rc = fci_get_response(this_client, fcode, rep_buf, rep_len);
	}
	else
		rc = 0;

out:
	return rc;
}


/*
 * fci_process_data -
 *
 */
static int fci_process_data(FCI_CLIENT *this_client, unsigned char *hdr, unsigned short *buf, int len)
{
	struct fci_hdr *fh;

	if (!this_client->event_cb)
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: %s() no event callback registered (socket id %d)\n", __func__, this_client->nl_sock_id);

		return FCI_CB_STOP;
	}

	if (!(fh = fci_check_msg(hdr, len)))
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: %s() message error\n", __func__);
		return FCI_CB_CONTINUE;
	}

	return this_client->event_cb(fh->fcode, fh->len, buf);
}


/*
 * fci_create_client -
 *
 */
static FCI_CLIENT *fci_create_client(int nl_type, unsigned long group)
{
	FCI_CLIENT *this_client;
	int socket_id;
	int rc; 
	int size = FCI_SOCK_SIZE;
	int status;
	socklen_t socklen = sizeof(size);

	this_client = malloc(sizeof(FCI_CLIENT));
	if (!this_client)
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: client allocation failed\n");
		goto err0;
	}

	memset(this_client, 0, sizeof(FCI_CLIENT));

	/* open netlink socket for user space client */
	socket_id = socket(AF_NETLINK, SOCK_RAW, nl_type);
        if (socket_id < 0)
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: socket() failed %s\n", strerror(errno));

		goto err1;
	}
        /* first we try the FORCE option, which is introduced in kernel
	 * 2.6.14 to give "root" the ability to override the system wide
	 * maximum */
	status = setsockopt(socket_id, SOL_SOCKET, SO_RCVBUFFORCE, &size, socklen);
	if (status < 0) {
		/* if this didn't work, we try at least to get the system
		 * wide maximum (or whatever the user requested) */
		if (setsockopt(socket_id, SOL_SOCKET, SO_RCVBUF, &size, socklen) < 0) {
			close(socket_id);
			goto err1;
		}
	}

	/* fill client properties */
	this_client->nl_sock_id = socket_id;

	this_client->nl_type = nl_type;
	
	/* fill netlink destination */
	this_client->dst_addr.nl_family = AF_NETLINK;
#ifdef ARCH_ODP
	if (nl_type == NETLINK_GENERIC) 
	{
		if (group)
			this_client->dst_addr.nl_pid = FCI_SOCK_PID_EVENT;
		else
	 		this_client->dst_addr.nl_pid = FCI_SOCK_PID_CMD;
	}
	else
		this_client->dst_addr.nl_pid = 0; 
#else
	/* For linux kernel */
	this_client->dst_addr.nl_pid = 0; 
#endif
	
	 /* no multicast groups */
	this_client->dst_addr.nl_groups = 0;

	/* fill netlink source */
        this_client->src_addr.nl_family = AF_NETLINK;

	/* This application's PID*/
#ifdef ARCH_ODP
	if (nl_type == NETLINK_GENERIC) 
	{
		if (group)
		       // this_client->src_addr.nl_pid = event_socket_src_id++;
		       this_client->src_addr.nl_pid = event_socket_src_id;
		else
		        this_client->src_addr.nl_pid = cmd_socket_src_id++;
		group = 0;
	}
	else
        	this_client->src_addr.nl_pid = 0; 
		
	FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: source addr %d \n", this_client->src_addr.nl_pid );
#else
        this_client->src_addr.nl_pid = 0; 
#endif

	this_client->src_addr.nl_groups = group;

	rc = bind(this_client->nl_sock_id, (struct sockaddr *)&this_client->src_addr, sizeof(this_client->src_addr));
	if(rc < 0)
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: bind(%d) failed %s\n", this_client->nl_sock_id, strerror(errno));
		goto err2;
	}
	

        return this_client;

err2:
	/* closing netlink socket */
	close(this_client->nl_sock_id);

err1:
	free(this_client);

err0:
	return NULL;
}


/*
 * fci_destroy_client -
 *
 */
static int fci_destroy_client(FCI_CLIENT *this_client)
{
	FCILIB_PRINTF(FCILIB_CLOSE, "fci_destroy_client\n");
	
	/* closing netlink socket */
	close(this_client->nl_sock_id);

	free(this_client);

	return 0;
}

/*
 * fci_get_response -
 *
 */
static int fci_get_response(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *rep_buf, unsigned short *rep_len)
{
	unsigned char hdr[NLMSG_LENGTH(sizeof(struct fci_hdr))] __attribute__ ((aligned (4)));
	struct iovec iov[]= {
		{
			.iov_base = &hdr,
			.iov_len = sizeof(hdr)
		},
		{
			.iov_base = rep_buf,
			.iov_len = *rep_len
		}
	};
	struct fci_hdr *fh;
	int len;

	FCILIB_PRINTF(FCILIB_READ, "%s: socket_id %d\n", __func__, this_client->nl_sock_id);

	*rep_len = 0;

	/* now, listen to the netlink subsystem */
	if ((len = fci_read(this_client, iov, 2)) < 0)
	{
		FCILIB_PRINTF(FCILIB_ERR, "LIBFCI: %s failed\n", __func__);

		return len;
	}

	if (!(fh = fci_check_msg(hdr, len)))
	{
		FCILIB_PRINTF(FCILIB_ERR,"LIBFCI: %s() message error\n", __func__);
		return -1;
	}

	/* Must match sent function code */
	if (fh->fcode != fcode)
		return -1;

	*rep_len = fh->len;

	return 0;
}

/*
 * fci_read -
 *
 */
static int fci_read(FCI_CLIENT *this_client, struct iovec *iov, int iovlen)
{
	struct msghdr msg = {
		.msg_iov = iov,
		.msg_iovlen = iovlen,
	};
	int rc;

	FCILIB_PRINTF(FCILIB_READ,"%s: socket id %d\n", __func__, this_client->nl_sock_id);

	rc = recvmsg(this_client->nl_sock_id, &msg, 0);

	return rc;	
}
