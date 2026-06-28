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

#ifndef __PPPOE_H__
#define __PPPOE_H__

#include "itf.h"

#define PPPOE_PATH "/proc/net/pppoe"

	int __cmmGetPPPoESession(FILE *fp, struct interface* ifp);

	int cmmFePPPoEUpdate(FCI_CLIENT *fci_handler, int action, struct interface *itf);
	int cmmPPPoELocalShow(struct cli_def * cli, const char *command, char *argv[], int argc);
	int cmmPPPoEQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);

#endif

