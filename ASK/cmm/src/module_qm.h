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

#ifndef __MODULE_QM_H__
#define __MODULE_QM_H__

/* min,max values */
#define QM_EXPTRATE_MINVAL      1000
#define QM_EXPTRATE_MAXVAL      5000000
#define QM_EXPTRATE_MIN_BS      1
#define QM_EXPTRATE_MAX_BS      2048
#define QM_FFRATE_MIN_CIR       1
#define QM_FFRATE_MAX_CIR       20971250
#define QM_FFRATE_MIN_PIR       1
#define QM_FFRATE_MAX_PIR       20971250

#ifdef SEC_PROFILE_SUPPORT
#define QM_SECRATE_MIN_CIR      1
#define QM_SECRATE_MAX_CIR      14880952
#define QM_SECRATE_MIN_PIR      1
#define QM_SECRATE_MAX_PIR      14880952 /* 64 byte size packet max frames per second for 10G. */
#define QM_SECRATE_MAX_CBS      2048
#define QM_SECRATE_MIN_CBS      1
#define QM_SECRATE_MAX_PBS      2048
#define QM_SECRATE_MIN_PBS      1
#endif /* endif for SEC_PROFILE_SUPPORT */

#define QM_SUCCESS              0
#define QM_ERROR                -1
#define QM_INVALID_KEYWORD      -2

#define QM_INGRESS_MIN_CIR       1
#define QM_INGRESS_MAX_CIR       20971250
#define QM_INGRESS_MIN_PIR       1
#define QM_INGRESS_MAX_PIR       20971250

int cmmQmShowProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmQmQueryProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmQmExptRateQueryProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmQmSetProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
void cmmQmResetQ2Prio(fpp_qm_reset_cmd_t *cmdp, int cmdlen);
void cmmQmUpdateQ2Prio(fpp_qm_scheduler_cfg_t *cmdp, int cmdlen);
#ifdef LS1043
int cmmQmFFRateQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmQmDSCPFqMapQueryProcess(char ** keywords, int tabSize, daemon_handle_t daemon_handle);
int cmmQmIngressQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle);
#ifdef SEC_PROFILE_SUPPORT
int cmmQmSecQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle);
#endif /* endif for SEC_PROFILE_SUPPORT */
int qm_get_num(char **keywords, int *pcpt, uint32_t max_val, uint32_t *val, char *errmsg);
#endif

#endif


