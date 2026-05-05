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

#ifndef __ALT_CONF_H__
#define __ALT_CONF_H__

int cmmAltConfClient(int argc, char **argv, int firstarg, daemon_handle_t daemon_handle);
int altconfResetProcess(daemon_handle_t daemon_handle);
int altconfSetProcess(daemon_handle_t daemon_handle, unsigned int option_id, unsigned int num_params, unsigned int *params);

#endif

