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

#ifndef __MODULE_EXPT_H__
#define __MODULE_EXPT_H__

	void cmmExptShowPrintHelp();
	int cmmExptShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	void cmmExptSetPrintHelp();
	int cmmExptSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);

#endif
