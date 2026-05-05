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

/* Function prototypes */ 
int timeoutSet(daemon_handle_t daemon_handle, char *argv[], int argc);
int cmmtimeoutSet(daemon_handle_t daemon_handle, char *argv[], int argc, int tab);
int cmmFeGetTimeout(FCI_CLIENT *fci_handle, struct ctTable *ctEntry, unsigned int *timeout);
int cmmFragTimeoutSet(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
unsigned long long cmm_convert_to_numeric(char *str );

#define MAX_TIMEOUT_STR_LEN 10
