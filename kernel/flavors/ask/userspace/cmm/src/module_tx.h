/*
 *  Copyright 2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */
#ifdef LS1043
#define SUCCESS               0
#define ERROR                -1
#define INVALID_KEYWORD      -2

int cmmTxSetProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmDSCPVlanPcpMapQueryProcess(char ** keywords, int cpt, daemon_handle_t daemon_handle);
#endif /* endif for LS1043 */
