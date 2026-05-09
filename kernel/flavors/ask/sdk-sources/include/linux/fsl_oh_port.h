// SPDX-License-Identifier: GPL-2.0+
/*
 * Copyright 2018 NXP
 */

/*
 * include/linux/fsl_oh_port.h
 *
 * Definitions for offline parsing port device related flags or structures i
 */

#ifndef _FSL_OH_PORT_H_
#define _FSL_OH_PORT_H_

#define MAX_FMANS               1
#define MAX_OFFLINE_PORTS       4

struct fman_offline_port_info {
	char port_name[32];
	uint32_t channel_id;
	uint32_t err_fqid;
	uint32_t default_fqid;
};
int oh_port_driver_get_port_info(struct fman_offline_port_info *info);

#endif
