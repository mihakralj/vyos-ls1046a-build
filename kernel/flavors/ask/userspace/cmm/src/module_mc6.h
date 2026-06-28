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

#ifndef __MODULE_MC6_H__
#define __MODULE_MC6_H__

	void cmmMc6ShowPrintHelp();
	int cmmMc6ShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	int cmmMc6QueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	void cmmMc6SetPrintHelp();
	int cmmMc6SetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	int cmmMc6ProcessClientCmd(FCI_CLIENT* fciMsgHandler, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
	int cmmMc6Show(struct cli_def * cli, const char *command, char *argv[], int argc);

	extern  int parse_macaddr(char *pstring, unsigned char *pmacaddr);


#endif
