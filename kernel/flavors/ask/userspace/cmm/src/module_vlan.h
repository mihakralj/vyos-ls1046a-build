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

#ifndef __MODULE_VLAN_H__
#define __MODULE_VLAN_H__

	#include "itf.h"

	void __cmmGetVlan(int fd, struct interface *itf);
	int cmmFeVLANUpdate(FCI_CLIENT *fci_handle, int request, struct interface *itf);
	void cmmVlanReset(FCI_CLIENT *fci_handle);
	int cmmVlanLocalShow(struct cli_def *cli, const char *command, char *argv[], int argc);
	int cmmVlanCheckPolicy(struct interface *itf);

/* remote command processing */
	int vlanAddProcess(daemon_handle_t daemon_handle, int argc, char *argv[]);
	int vlanDeleteProcess(daemon_handle_t daemon_handle, int argc, char *argv[]);
	int cmmVlanClient(int argc, char **argv, int firstarg, daemon_handle_t daemon_handle);
	int cmmVlanProcessClientCmd(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
	int cmmVlanShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	int cmmVlanShowClientCmd(u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
	int cmmVlanQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
#endif
