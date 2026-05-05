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

#ifndef __MODULE_ROUTE_H__
#define __MODULE_ROUTE_H__

	void cmmRouteShowPrintHelp();
	int cmmRouteShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	void cmmRouteSetPrintHelp();
	int cmmRouteSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	int cmmRouteProcessClientCmd(FCI_CLIENT* fciMsgHandler, int function_code, u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len);
	struct RtEntry *cmmPolicyRouting(unsigned int srcip, unsigned int dstip, unsigned short proto, unsigned short sport, unsigned short dport);

#endif

