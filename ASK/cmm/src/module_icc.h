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

#ifndef __MODULE_ICC_H__
#define __MODULE_ICC_H__
#include "fpp.h"

#define ICC_NUM_INTERFACES	3

#define ICC_ACTION_ADD		0
#define ICC_ACTION_DELETE	1

#define ICC_ACTION_QUERY	0
#define ICC_ACTION_QUERY_CONT	1

#define	ICC_TABLETYPE_ETHERTYPE	0
#define	ICC_TABLETYPE_PROTOCOL	1
#define	ICC_TABLETYPE_DSCP	2
#define	ICC_TABLETYPE_SADDR	3
#define	ICC_TABLETYPE_DADDR	4
#define	ICC_TABLETYPE_SADDR6	5
#define	ICC_TABLETYPE_DADDR6	6
#define	ICC_TABLETYPE_PORT	7
#define	ICC_TABLETYPE_VLAN	8

int IccReset(daemon_handle_t daemon_handle, int argc, char *argv[]);
int IccThreshold(daemon_handle_t daemon_handle, int argc, char *argv[]);
int IccAdd(daemon_handle_t daemon_handle, int argc, char *argv[]);
int IccDelete(daemon_handle_t daemon_handle, int argc, char *argv[]);
int IccQuery(daemon_handle_t daemon_handle, int argc, char *argv[]);

#endif
