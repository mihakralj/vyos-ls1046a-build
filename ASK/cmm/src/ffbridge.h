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

#ifndef __FFBRIDGE_H__
#define __FFBRIDGE_H__

	#include "cmm.h"
	#include <linux/if_bridge.h>

	/* Macros */
	#define CHUNK	256

	/* Structures */
	struct fdb_entry
	{
		u_int8_t mac_addr[ETH_ALEN];
		u_int16_t port_no;
		unsigned char is_local;
	};

	/* Functions */
	void __cmmGetBridges(int fd);
	int cmmBrToFF(struct RtEntry *route);
	int cmmBrGetPhysItf(int br_ifindex, unsigned char* fdb_mac);

#endif
