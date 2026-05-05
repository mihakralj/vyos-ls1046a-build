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

#ifndef __MODULE_RTP_H__
#define __MODULE_RTP_H__

int cmmRTPSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmRTPQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmRTCPQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);

/******************************** RTP Stats QoS Measurement **********************/

int cmmRTPStatsSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmRTPStatsQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);

#endif
