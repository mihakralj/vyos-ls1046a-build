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

#ifndef __MODULE_PKTCAP_H__
#define __MODULE_PKTCAP_H__
#include "fpp.h"

#define CMD_PKTCAP_IFSTATUS	FPP_CMD_PKTCAP_IFSTATUS	
#define CMD_PKTCAP_SLICE	FPP_CMD_PKTCAP_SLICE	
#define CMD_PKTCAP_FLF          FPP_CMD_PKTCAP_FLF

#define PKTCAP_IFSTATUS_ENABLE  0x1
#define PKTCAP_IFSTATUS_DISABLE 0x0



int PktCapSliceProcess(daemon_handle_t daemon_handle, int argc, char *argv[]);

int PktCapStatProcess(daemon_handle_t daemon_handle, int argc, char *argv[]);

int PktCapFilterProcess(daemon_handle_t daemon_handle, int argc, char *argv[]);

int PktCapQueryProcess(struct cli_def *cli, daemon_handle_t daemon_handle);

int Check_BPFfilter(struct bpf_insn *filter, int flen);

#endif
