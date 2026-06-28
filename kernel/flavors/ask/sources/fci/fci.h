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

#ifndef _FCI_H
#define _FCI_H

/*
* Prototypes
*/

/* FPP Forward Engine API*/
extern int comcerto_fpp_send_command(unsigned short fcode, unsigned short length, unsigned short *payload, unsigned short *, unsigned short *);
extern int comcerto_fpp_register_event_cb(void *cb);

/*
* Debug macros
*/

#define FCI_PRINT	0
#define FCI_INIT	0
#define FCI_STAT	0
#define FCI_OUTBOUND	0
#define FCI_INBOUND	0
#define FCI_DUMP	0
#define FCI_NL		0
#define FCI_ACK		0

#ifdef FCI_PRINT
#define FCI_PRINTK(type, info, args...) do {if(type) printk(KERN_DEBUG info, ## args);} while(0);
#else
#define FCI_PRINTK(type, info, args...) do {} while(0);
#endif

/* Supported netlink protocol type NETLINK_FF */
#define FCI_NL_FF		0
#define FCI_MAX_PROTO		1

/* Netlink multicast groups supported by FCI */
#define NL_FF_GROUP	1

/* FCI message definitions*/
#define FCI_MSG_MAX_PAYLOAD	512
#define FCI_MSG_HDR_SIZE 	4 /* fcode + length */
#define FCI_MSG_SIZE		(FCI_MSG_MAX_PAYLOAD + FCI_MSG_HDR_SIZE)

/*
* Structures
*
*/
typedef struct t_FCI_MSG
{
	/* message data */
	u16 fcode;
	u16 length;
	u16 payload[(FCI_MSG_MAX_PAYLOAD / sizeof(u16))];
} FCI_MSG;


typedef struct t_FCI_SOCK_STATS
{
	unsigned long tx_msg;
	unsigned long rx_msg;
	unsigned long tx_msg_err;
	unsigned long rx_msg_err;
} FCI_SOCK_STATS;


typedef struct t_FCI_STATS
{
	/* Globlas Statistics*/
	unsigned long tx_msg;
	unsigned long rx_msg;
	unsigned long tx_msg_err;
	unsigned long rx_msg_err;
	unsigned long mem_alloc_err;
	unsigned long kernel_create_err;
	unsigned long unknown_sock_type;
	/* Per socket type statistics*/
	FCI_SOCK_STATS sock_stats[FCI_MAX_PROTO];
} FCI_STATS;


typedef struct t_FCI
{
	struct sock *fci_nl_sock[FCI_MAX_PROTO];
	FCI_STATS stats;
} FCI;



#endif /* _FCI_H */
