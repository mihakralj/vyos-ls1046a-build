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

#ifndef _FCILIB_H
#define _FCILIB_H

#include <linux/netlink.h>

/* FCI messages definitions */
#define FCI_MAX_PAYLOAD 512


/* Supported netlink protocol types */
#define FCILIB_FF_TYPE	0 /* NETLINK_FF */
#define FCILIB_KEY_TYPE	2 /* NETLINK_KEY */

/* Netlink multicast groups supported by FCI */
#define NL_FF_GROUP		(1 << 0)
#define NL_KEY_SA_GROUP		(1 << 0)
#define NL_KEY_FLOW_GROUP	(1 << 1)
#define NL_KEY_ALL_GROUP	(NL_KEY_SA_GROUP | NL_KEY_FLOW_GROUP)

#ifndef CMM_SOCK_PID_CMD
#define CMM_SOCK_PID_CMD 100
#endif

#ifndef FCI_SOCK_PID_CMD
#define FCI_SOCK_PID_CMD 200
#endif

#ifndef CMM_SOCK_PID_EVENT
#define CMM_SOCK_PID_EVENT 300
#endif


#ifndef FCI_SOCK_PID_EVENT
#define FCI_SOCK_PID_EVENT 400
#endif

/* FCI callbacks return codes */
enum FCI_CB_ACTION {
	FCI_CB_STOP = 0,		/* stop catching event from FCI */
	FCI_CB_CONTINUE,	/* continue event catching */
};

/*
* Structures
*
*/

typedef struct t_FCI_CLIENT
{
	int nl_sock_id;
	int nl_type;
	struct sockaddr_nl src_addr;
	struct sockaddr_nl dst_addr;
	int (*event_cb)(unsigned short fcode, unsigned short len, unsigned short *payload);
	
} FCI_CLIENT;


/*
* Prototypes
*/
FCI_CLIENT * fci_open(unsigned long socket_type, unsigned long group);
int fci_close(FCI_CLIENT *client);
int fci_catch(FCI_CLIENT *client);
int fci_cmd(FCI_CLIENT *this_client, unsigned short fcode, unsigned short *cmd_buf, unsigned short cmd_len, unsigned short *rep_buf, unsigned short *rep_len);
int fci_write(FCI_CLIENT *client, unsigned short fcode, unsigned short len, unsigned short *payload);
int fci_register_cb(FCI_CLIENT *client, int (*event_cb)(unsigned short fcode, unsigned short len, unsigned short *payload));
int fci_query(FCI_CLIENT *this_client, unsigned short fcode, unsigned short length, unsigned short *pcmd, unsigned short *rsplen, unsigned short *rsp_data);
int fci_fd(FCI_CLIENT *this_client);
#endif
