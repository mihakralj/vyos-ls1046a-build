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

#ifndef __MODULE_RELAY_H__
#define __MODULE_RELAY_H__

#include "cmm.h"

void cmmRelayShowPrintHelp();

int cmmRelayProcessClientCmd(FCI_CLIENT * fciMsgHandler, int function_code,
                             u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);

int cmmRelayLocalShow(struct cli_def *cli, const char *command, char *argv[],
                      int argc);
int cmmRelayParseCmd(int argc, char ** keywords, int tabStart, daemon_handle_t daemon_handle);
#endif
