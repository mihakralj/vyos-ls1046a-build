/*
 *
 *  Copyright 2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */

#ifndef __MODULE_IPR_H__
#define __MODULE_IPR_H__
#if defined(LS1043)
int cmmIpr4StatsQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmIpr6StatsQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmIprStatsProcessClientCmd(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf,
        u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);
#endif
#endif

