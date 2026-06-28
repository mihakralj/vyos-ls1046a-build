/*
 *
 *  Copyright (C) 2014 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#ifndef __MODULE_LRO_H__
#define __MODULE_LRO_H__

int lro_interface_add(char *ifname);
void lro_interface_update(struct interface *itf);
void lro_socket_open(FCI_CLIENT *fci_handle, struct ctTable *ctEntry);
void lro_socket_close(FCI_CLIENT *fci_handle, FCI_CLIENT *fci_key_handle, struct ctTable *ctEntry);

#endif /* __MODULE_LRO_H__ */
