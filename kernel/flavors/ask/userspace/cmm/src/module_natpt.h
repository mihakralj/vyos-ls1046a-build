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

#ifndef __MODULE_NATPT_H__
#define __MODULE_NATPT_H__

int cmmNATPTSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmNATPTQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmNATPTQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmNATPTOpenProcessClientCmd(FCI_CLIENT* fci_handle, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
#endif
