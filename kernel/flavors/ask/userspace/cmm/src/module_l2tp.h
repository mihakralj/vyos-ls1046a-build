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

#ifndef __MODULE_L2TP_H__
#define __MODULE_L2TP_H__


int l2tp_itf_add(FCI_CLIENT *fci_handle, int request, struct interface *itf);
int __l2tp_itf_del(FCI_CLIENT *fci_handle, struct interface *itf);
int l2tp_itf_del(FCI_CLIENT *fci_handle, struct interface *itf);
int l2tp_daemon(FCI_CLIENT *fci_handle,int command, cmmd_l2tp_session_t *cmd,  u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len);

#endif
