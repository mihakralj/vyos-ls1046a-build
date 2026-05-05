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


#include <net/if.h>
#include <linux/if_vlan.h>
#include <linux/sockios.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <ctype.h>

#include "libcmm.h"
#include "cmm.h"
#include "fpp.h"
#include "itf.h"
#include "module_pktcap.h"

#define MIN_SLICE_VALUE		40
#define MAX_SLICE_VALUE		1518
#define SNAP_LENGTH          	96



int PktCapSliceProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{

        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_pktcap_slice_cmd_t 	*pktcap_cmd;
	unsigned char		port_id;
	unsigned short		slice;
	char buf[128];

	
	if (argc != 2)
		goto usage;
		
	if ((char)(port_id = get_port_id(argv[0])) < 0)
		goto usage;


	if (! isdigit(*argv[1]))
		goto usage;
	
	slice = atoi(argv[1]);

	if ( (slice < MIN_SLICE_VALUE )||(slice > MAX_SLICE_VALUE))
	{
		cmm_print(DEBUG_ERROR,"slice value should be between(%d-%d)", MIN_SLICE_VALUE, MAX_SLICE_VALUE);
		return -1;
	}

	memset(&cmd, 0 , sizeof(cmd));
        memset(&res, 0 , sizeof(res));
	
	
	cmd.func 	= FPP_CMD_PKTCAP_SLICE;
	cmd.length 	= sizeof(fpp_pktcap_slice_cmd_t);
	pktcap_cmd	= (fpp_pktcap_slice_cmd_t*)&cmd.buf;
	pktcap_cmd->action	= FPP_PKTCAP_SLICE;
	pktcap_cmd->ifindex	= port_id;
	pktcap_cmd->slice	= slice;
	if (cmm_send(daemon_handle, &cmd, 0) != 0) {
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (cmm_recv(daemon_handle, &res, 0) < 0) {
                cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (res.rc != FPP_ERR_OK) {
                cmm_print(DEBUG_ERROR,"Error from CMM, error = `%d'\n", res.rc);
                return -1;
        }
	
        return CLI_OK;
usage:
	print_all_gemac_ports(buf, 128);
	cmm_print(DEBUG_ERROR, "Usage: pktcapture  slice <%s> <value>\n", buf);
        return -1;
}

int PktCapStatProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{
        cmm_command_t           cmd;
        cmm_response_t          res;
        fpp_pktcap_status_cmd_t        *pktcap_cmd;
        unsigned char           port_id;
        unsigned char		status;
	char 			buf[128];


	if (argc != 2)
		goto usage;

	if ((char)(port_id = get_port_id(argv[0])) < 0)
		goto usage;


        if (strcmp(argv[1] , "enable") == 0)
                status = PKTCAP_IFSTATUS_ENABLE;
        else if (strcmp(argv[1], "disable") == 0)
                status = PKTCAP_IFSTATUS_DISABLE;
        else
                goto usage;
	

        memset(&cmd, 0 , sizeof(cmd));
        memset(&res, 0 , sizeof(res));

        
        cmd.func        = FPP_CMD_PKTCAP_IFSTATUS;
        cmd.length      = sizeof(fpp_pktcap_status_cmd_t);
        pktcap_cmd      = (fpp_pktcap_status_cmd_t*)&cmd.buf;
        pktcap_cmd->action      = FPP_PKTCAP_STATUS;
        pktcap_cmd->ifindex     = port_id;
        pktcap_cmd->status      = status;

        if (cmm_send(daemon_handle, &cmd, 0) != 0) {
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (cmm_recv(daemon_handle, &res, 0) < 0) {
                cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

        if (res.rc != FPP_ERR_OK) {
                cmm_print(DEBUG_ERROR,"Error from CMM, error = `%d'\n", res.rc);
                return -1;
        }

        return CLI_OK;
usage:
	print_all_gemac_ports(buf, 128);
	cmm_print(DEBUG_ERROR, "Usage: pktcapture status <%s> <enable|disable>\n", buf);
        return CLI_OK;

}


int PktCapFilterProcess(daemon_handle_t daemon_handle, int argc, char *argv[])
{
	cmm_command_t           cmd;
        cmm_response_t          res;
        struct bpf_program fd = {0,NULL};
        fpp_pktcap_flf_cmd_t *pFlfCmd = NULL;
        int port_id = 0, fgmts = 0;
	int length = 0, seqno = 0;
	char buf[128];

        if (argc < 2)
                goto usage;

	if ((char)(port_id = get_port_id(argv[0])) < 0)
		goto usage;

	

	cmd.func        = FPP_CMD_PKTCAP_FLF;
	cmd.length      = sizeof(fpp_pktcap_flf_cmd_t);
	pFlfCmd =	(fpp_pktcap_flf_cmd_t* )&cmd.buf; 

	if( strlen(argv[1]) >= 1024 )
	{
		cmm_print(DEBUG_ERROR,"Error Filter too long \n");
		goto usage;
	}
	

        pcap_t *pd = pcap_open_dead(DLT_EN10MB, SNAP_LENGTH);
        if(pd)
	{
		if(pcap_compile(pd, &fd, argv[1], 1, 0)<0)
		{
			cmm_print(DEBUG_ERROR,"Error Invalid filter string \n");
			goto done;
		}

		if(( fd.bf_len == 1)&& ( fd.bf_insns[0].code == BPF_RET ))// Filter reset !!	
			goto reset_flf;

		pFlfCmd->ifindex = port_id;
		pFlfCmd->flen = length = fd.bf_len & 0xFFFF;

		if(Check_BPFfilter(fd.bf_insns, fd.bf_len))
		{
			cmm_print(DEBUG_ERROR,"Error This filter combination is not supported by FLF\n");
			goto done;
		}
			
		if(fd.bf_len > MAX_FLF_INSTRUCTIONS) 
		{
			cmm_print(DEBUG_ERROR,"Warning: Filter could be too expensive");
			length = MAX_FLF_INSTRUCTIONS;	
			fgmts = (fd.bf_len / MAX_FLF_INSTRUCTIONS);
			if( fd.bf_len % MAX_FLF_INSTRUCTIONS )
				++fgmts; // Account for one more fragment;
		}
		do
		{
			
			memcpy(pFlfCmd->filter, &fd.bf_insns[seqno * MAX_FLF_INSTRUCTIONS], length * sizeof(struct bpf_insn));

			pFlfCmd->mfg     = ((( fgmts - (seqno + 1)) > 0) << 3 ) | (seqno & 0x7);
					

			/* Push to FPP */
			if (cmm_send(daemon_handle, &cmd, 0) != 0) {
				cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
				goto reset_flf;
			}

			if (cmm_recv(daemon_handle, &res, 0) < 0) {
				cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
				goto reset_flf;
			}

			if (res.rc != FPP_ERR_OK) {
				cmm_print(DEBUG_ERROR,"Error from CMM, error = `%d'\n", res.rc);
				goto reset_flf;
			}
			pFlfCmd->flen = (fd.bf_len - (++seqno * MAX_FLF_INSTRUCTIONS));
			if( pFlfCmd->flen / MAX_FLF_INSTRUCTIONS)
			{
				length = pFlfCmd->flen = MAX_FLF_INSTRUCTIONS;
				continue;
			}
			else
				length = pFlfCmd->flen;
			

		}while(seqno < fgmts);
	}

done:
        if(fd.bf_insns) free(fd.bf_insns);
	if(pd) pcap_close(pd);
        return CLI_OK;

usage:
	print_all_gemac_ports(buf, 128);
	cmm_print(DEBUG_ERROR, "Usage: pktcapture filter <%s> <string>\n", buf);
	return CLI_OK;

reset_flf:
	cmm_print(DEBUG_ERROR, "Resetting filter");
	/* cleanup structures got from library */
        if(fd.bf_insns) free(fd.bf_insns);
	if(pd) pcap_close(pd);

	/* reset length */
	pFlfCmd->ifindex = port_id;
	pFlfCmd->flen    = 0;

	if (cmm_send(daemon_handle, &cmd, 0) != 0) {
		cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
		return -1;
	}
	if (cmm_recv(daemon_handle, &res, 0) < 0) {
		cmm_print(DEBUG_ERROR,"Error receiving message from CMM, error = `%s'\n", strerror(errno));
		return -1;
	}
	if (res.rc != FPP_ERR_OK) {
		cmm_print(DEBUG_ERROR,"Error from CMM, error = `%d'\n", res.rc);
		return -1;
	}
        return CLI_OK;
}

/* 
 * The purpose of this function is to ensure the filter is compatible with our 
 * version of BPF interpretor in FPP.
 * As libpcap version changes further, with changes in the filter corresponding changes 
 * will also have to be made in fpp. 
 */

int Check_BPFfilter(struct bpf_insn *filter, int flen)
{
        struct bpf_insn *ftest;
        int pc;

        if (flen == 0 || flen > (3*MAX_FLF_INSTRUCTIONS))
                return -EINVAL;

        /* check the filter code now */
        for (pc = 0; pc < flen; pc++) {
                ftest = &filter[pc];

                /* Only allow valid instructions */
                switch (ftest->code) {
                case BPF_ALU|BPF_ADD|BPF_K:
                case BPF_ALU|BPF_ADD|BPF_X:
                case BPF_ALU|BPF_SUB|BPF_K:
                case BPF_ALU|BPF_SUB|BPF_X:
                case BPF_ALU|BPF_MUL|BPF_K:
                case BPF_ALU|BPF_MUL|BPF_X:
                case BPF_ALU|BPF_DIV|BPF_X:
                case BPF_ALU|BPF_AND|BPF_K:
                case BPF_ALU|BPF_AND|BPF_X:
                case BPF_ALU|BPF_OR|BPF_K:
                case BPF_ALU|BPF_OR|BPF_X:
                case BPF_ALU|BPF_LSH|BPF_K:
                case BPF_ALU|BPF_LSH|BPF_X:
                case BPF_ALU|BPF_RSH|BPF_K:
                case BPF_ALU|BPF_RSH|BPF_X:
                case BPF_ALU|BPF_NEG:
                case BPF_LD|BPF_W|BPF_ABS:
                case BPF_LD|BPF_H|BPF_ABS:
                case BPF_LD|BPF_B|BPF_ABS:
                case BPF_LD|BPF_W|BPF_LEN:
                case BPF_LD|BPF_W|BPF_IND:
                case BPF_LD|BPF_H|BPF_IND:
                case BPF_LD|BPF_B|BPF_IND:
                case BPF_LD|BPF_IMM:
                case BPF_LDX|BPF_W|BPF_LEN:
                case BPF_LDX|BPF_B|BPF_MSH:
                case BPF_LDX|BPF_IMM:
                case BPF_MISC|BPF_TAX:
                case BPF_MISC|BPF_TXA:
                case BPF_RET|BPF_K:
                case BPF_RET|BPF_A:
                        break;

                /* Some instructions need special checks */

                case BPF_ALU|BPF_DIV|BPF_K:
                        /* check for division by zero */
                        if (ftest->k == 0)
                                return -EINVAL;
                        break;

                case BPF_LD|BPF_MEM:
                case BPF_LDX|BPF_MEM:
                case BPF_ST:
                case BPF_STX:
                        /* check for invalid memory addresses */
                        if (ftest->k >= BPF_MEMWORDS)
                                return -EINVAL;
                        break;

                case BPF_JMP|BPF_JA:
	                /*
 	                 * Note, the large ftest->k might cause loops.
	                 * Compare this with conditional jumps below,
                         * where offsets are limited. --ANK (981016)
                         */
                        if (ftest->k >= (unsigned)(flen-pc-1))
                                return -EINVAL;
                        break;

                case BPF_JMP|BPF_JEQ|BPF_K:
                case BPF_JMP|BPF_JEQ|BPF_X:
                case BPF_JMP|BPF_JGE|BPF_K:
                case BPF_JMP|BPF_JGE|BPF_X:
                case BPF_JMP|BPF_JGT|BPF_K:
                case BPF_JMP|BPF_JGT|BPF_X:
                case BPF_JMP|BPF_JSET|BPF_K:
                case BPF_JMP|BPF_JSET|BPF_X:
                        /* for conditionals both must be safe */
                        if (pc + ftest->jt + 1 >= flen ||
                            pc + ftest->jf + 1 >= flen)
                                return -EINVAL;
                        break;

                default:
                        return -EINVAL;
                }
        }

        return (BPF_CLASS(filter[flen - 1].code) == BPF_RET) ? 0 : -EINVAL;
}



int PktCapQueryProcess(struct cli_def *cli, daemon_handle_t daemon_handle) 
{
	/* Query fpp for the details */
       	char rcvBuffer[256];
	fpp_pktcap_query_cmd_t *pktcap_cmd; 
	int ii;

        pktcap_cmd      = (fpp_pktcap_query_cmd_t*)&rcvBuffer;
	

	if ((cmmSendToDaemon(daemon_handle, FPP_CMD_PKTCAP_QUERY, pktcap_cmd, 
							0, pktcap_cmd)) < sizeof(fpp_pktcap_query_cmd_t))
	{
                cmm_print(DEBUG_ERROR,"Error sending message to CMM, error = `%s'\n", strerror(errno));
                return -1;
        }

	for (ii = 0; ii < GEM_PORTS; ii++)
	{
		cli_print(cli, "slice  <%s> %d \n", port_table[ii].logical_name, pktcap_cmd[port_table[ii].port_id].slice);
	}

	for (ii = 0; ii < GEM_PORTS; ii++)
	{
		cli_print(cli, "status <%s> %d \n", port_table[ii].logical_name, pktcap_cmd[port_table[ii].port_id].status);
	}

        return CLI_OK;
}

