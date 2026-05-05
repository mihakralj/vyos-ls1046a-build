/*
 *
 *  Copyright (C) 2010 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */
#ifndef __VOICEBUF_H__
#define __VOICEBUF_H__

#include <sys/ioctl.h>

#define MEMBUF_CHAR_DEVNAME "/dev/membuf"

#define VOICE_FILE_MAX		8

/* These must match the kernel definitions */
#define MEMBUF_GET_SCATTER _IOR('m', 1, struct usr_scatter_list)

#define MAX_BUFFERS	48

struct usr_scatter_list
{
	u_int8_t entries;
	u_int8_t pg_order[MAX_BUFFERS];
	u_int32_t addr[MAX_BUFFERS];
};

int voice_file_load(FCI_CLIENT *fci_handle, cmmd_voice_file_load_cmd_t *cmd, u_int16_t *res_buf, u_int16_t *res_len);
int voice_file_unload(FCI_CLIENT *fci_handle, cmmd_voice_file_unload_cmd_t *cmd, u_int16_t *res_buf, u_int16_t *res_len);
int voice_buffer_reset(FCI_CLIENT *fci_handle);
int cmmVoiceBufSetProcess(int argc, char *argv[], daemon_handle_t daemon_handle);

#endif
