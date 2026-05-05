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
#include "cmm.h"
#include <ctype.h>

/*Function codes*/
/* 0x0fxx : trace/profiling */
#define FPP_CMD_TRC_ON                              0x0f01
#define FPP_CMD_TRC_OFF                             0x0f02
#define FPP_CMD_TRC_SWITCH                          0x0f03
#define FPP_CMD_TRC_DMEM                            0x0f04
#define FPP_CMD_TRC_SETMASK                         0x0f05
#define FPP_CMD_TRC_SHOW                            0x0f06
#define FPP_CMD_TRC_BSYCPU                          0x0f07
#define FPP_CMD_TRC_STATUS                          0x0f08
/* Trace/profiling return codes */
#define FPP_ERR_TRC_SOME_OK                         0xf00
#define FPP_ERR_TRC_UNIMPLEMENTED                   0xf7f

/*
** Command/response layouts
*/
/* Display memory command */
typedef struct fpp_dm_cmd {
        u_int16_t       pad_in_rc_out; /* Padding - retcode */
        u_int16_t       msp_len;      /* Lenght of memory to display < 224 bytes
                               ** returns length being displayed in response */
        u_int32_t       msp_addr;       /* msp address of memory to display
                               ** returns address being displayed in response */
        u_int8_t        mspmem[224];
} __attribute__((__packed__)) fpp_dm_cmd_t;

/* Trace On command */
typedef struct fpp_trc_on_cmd {
        u_int16_t       pad_in_rc_out; /* Padding - retcode */
        u_int16_t       pad;
        u_int16_t       pmn0_id;       /* counter code for PMN0 counter to use - default 0*/
        u_int16_t       pmn1_id;       /* counter code for PMN1 counter to use - default 2*/
} __attribute__((__packed__)) fpp_trc_on_cmd_t;

/* Trace switch/show/stop */
typedef struct fpp_trc_off_cmd {
        u_int16_t       pad_in_rc_out;
        u_int16_t       pad_in_ec_out;
        u_int16_t       pmn0_id;
        u_int16_t       pmn1_id;
        u_int16_t       trc_module_mask;
        u_int16_t       trc_ctr_length;
        u_int16_t       trc_mask_length;
        u_int16_t       trc_length;
        u_int32_t       trc_address;
} __attribute__((__packed__)) fpp_trc_off_cmd_t;

/* trace status */
typedef struct fpp_trc_stat_cmd {
        u_int16_t       pad_in_rc_out;  /* Padding - retcode */
        u_int16_t       state;          /* state 0:off
                                         * 1:tracing on
                                         * 2:available cpu measurement on */
        u_int16_t       pmn0;           /* counter code for PMN0 counter in use*/
        u_int16_t       pmn1;           /* counter code for PMN1 counter in use*/
        u_int32_t       trc_mask;       /* bitmask of module probes would be in effect */
        u_int32_t       bsycpu_weight;  /* weight factor (would be) in effect */
} __attribute__((__packed__)) fpp_trc_stat_cmd_t;

/* trace setmask */
typedef struct fpp_trc_sm_cmd {
        u_int16_t       mask_in_rc_out; /* Input mask - retcode */
} __attribute__((__packed__)) fpp_trc_sm_cmd_t;

/* busycpy start/stop */
typedef struct fpp_trc_cpu_cmd {
        u_int16_t       pad_in_rc_out;          /* Padding - retcode */
        u_int16_t       on_off;                 /* 0:stop, 1: start or change weight */
        u_int32_t       on_weight_off_pad;      /* start only: weight factor value to use */
        u_int64_t       off_rsp_busy_count; /* stop only: used cpu cycles */
        u_int64_t       off_rsp_idle_count; /* stop only: available cpu cycles */
} __attribute__((__packed__)) fpp_trc_cpu_cmd_t;

/*****************************************************************
 * cmmMspMemShow
 *
 *
******************************************************************/

static void msp_dm(daemon_handle_t daemon_handle, unsigned int mm_address, unsigned int mm_length, int fmt) {
  /*
  ** fmt - format - bit mask
  ** 	0x1 - human readable words 
  **	0x2 - prefix each line with hex addr
  */
  fpp_dm_cmd_t cmd, *prsp;

  unsigned int tmp,i,j,k;
  unsigned char rspbuf[512];
  char output_line[128] ; //cli(cmm) inserts newlines - need to buffer 
  unsigned short rsplen;
  prsp = (void *)rspbuf;

  for(tmp = 0;tmp < mm_length;) {
    cmd.msp_addr = mm_address + tmp;
    if ( (mm_length-tmp) > sizeof(cmd.mspmem))
      cmd.msp_len = sizeof(cmd.mspmem);
    else
      cmd.msp_len = mm_length -tmp;
      
    if (cmd.msp_len > 16) { 
      // For long queries we want to go in multiples of 16
      cmd.msp_len = ((cmd.msp_len >> 4) << 4);
    }
//    cmm_print(DEBUG_COMMAND, "Send CMD_TRC_DMEM l:%d %04x ql:%04x qa:%08x\n",
//	      (sizeof(cmd)- sizeof(cmd.mspmem)),
//	      cmd.pad_in_rc_out,
//	      cmd.msp_len,
//	      cmd.msp_addr );
    if ( 
	( (rsplen = cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_DMEM,&cmd,(sizeof(cmd)- sizeof(cmd.mspmem)),rspbuf)) < sizeof(unsigned short) ) ||
	cmmDaemonCmdRC(rspbuf)
	)
      {
      cmm_print(DEBUG_ERROR, "Error sending CMD_TRC_DMEM\n");
      /*  break; */ return;
    }
//    cmm_print(DEBUG_COMMAND, "Response to CMD_TRC_DMEM rsplen rc:%04x rl:%04x ra:%08x\n",
//	      prsp->pad_in_rc_out,
//	      prsp->msp_len,
//	      prsp->msp_addr );
    tmp += prsp->msp_len;
#define MIN_ACK_LEN 8

    if ((rsplen < MIN_ACK_LEN ) || (prsp->pad_in_rc_out)) {
       cmm_print(DEBUG_ERROR, "Bad response to CMD_TRC_DMEM, rsplen %d , rc %d\n", rsplen, prsp->pad_in_rc_out);
      /*  break; */ return;
    }
#undef  MIN_ACK_LEN 
    for(i=prsp->msp_len;i > 0;) {
      k = (i > 16) ? 16 : i;
      if (fmt & 2) 
	j = sprintf(output_line,"0x%08x:", cmd.msp_addr + cmd.msp_len - i);
      else
	j = 0;
      do {
	switch (i) {
	case 1:
	  j += sprintf(output_line+j," %02x",
		       prsp->mspmem[prsp->msp_len-1]);
	  k -= 1;
	  i -= 1;
	  break;
	case 2:
	  j += sprintf(output_line+j," %02x%02x",
		       prsp->mspmem[prsp->msp_len-2],
		       prsp->mspmem[prsp->msp_len-1]);
	  k -= 2;
	  i -= 2;
	  break;
	case 3:
	  j += sprintf(output_line+j," %02x%02x%02x",
		       prsp->mspmem[prsp->msp_len-3],
		       prsp->mspmem[prsp->msp_len-2],
		       prsp->mspmem[prsp->msp_len-1]);
	  k -= 3;
	  i -= 3;
	  break;
	default:
	  if (fmt & 1) {
	    j += sprintf(output_line+j," %08x",
			 *((unsigned int*)(prsp->mspmem+prsp->msp_len-i) )
			 );
	  } else {
	  j += sprintf(output_line+j," %02x%02x%02x%02x",
		       prsp->mspmem[prsp->msp_len-i],
		       prsp->mspmem[prsp->msp_len-i+1],
		       prsp->mspmem[prsp->msp_len-i+2],
		       prsp->mspmem[prsp->msp_len-i+3]);
	  }
	  k -= 4;
	  i -= 4;
	  break;
	}
      } while(k);
      cmm_print(DEBUG_STDOUT,"%s\n",output_line);
    } // for

  }
  return;
}

int prfMspMS(daemon_handle_t daemon_handle, int argc, char *argv[])
{
  unsigned int startaddr;
  unsigned int len,tmp;

  //  startaddr = simple_strtoul(argv[0],NULL,0);
  if ((argc < 1) || (1 != sscanf(argv[0],"%i",&startaddr)))
    goto usage;

  len = 16;
  if (argc > 1) {
    //    len =  simple_strtoul(argv[1],NULL,0);
    if (1 == sscanf(argv[1],"%i",&tmp))
      len = tmp;
  }
  // display - arbitrary length,wire format 
  msp_dm(daemon_handle, startaddr, len, 0);
  return 0;
 usage:
  cmm_print(DEBUG_ERROR, "Usage: shmspmem bytes addr [len|16]\n");
  return 0;

}

int prfMspMSW(daemon_handle_t daemon_handle, int argc, char *argv[])
{
  unsigned int startaddr;
  unsigned int len,tmp;

  //  startaddr = simple_strtoul(argv[0],NULL,0);
  if ((argc < 1) || (1 != sscanf(argv[0],"%i",&startaddr)))
    goto usage;

  len = 16;
  if (argc > 1) {
    //    len =  simple_strtoul(argv[1],NULL,0);
    if (1 == sscanf(argv[1],"%i",&tmp))
      len = tmp;
  }
  // display - arbitrary length,wire format 
  msp_dm(daemon_handle, startaddr, len, 0x3);
  return 0;
 usage:
  cmm_print(DEBUG_ERROR, "Usage: mspmem words addr [len|16], Address and length have to be multiple of 4\n");
  return 0;

}

int prfMspCT(daemon_handle_t daemon_handle, int argc, char *argv[])
{
  /*  typedef */ struct {
    void *next;
    void *twin;
    void *actNext;
    void *actPrevious;
    unsigned int Saddr;
    unsigned int Daddr;
    unsigned short Sport;
    unsigned short Dport;
    unsigned int     fwmark;
    unsigned int keepAlive;  //keep alive timer
    unsigned int timer;
    void *pRtEntry;
    void *pARPEntry;
    //unsigned int fw_packets;
    unsigned short ip_chksm_corr;
    unsigned short tcp_udp_chksm_corr;
    unsigned char      status;
    unsigned char      proto;
    unsigned char	pad1;
    unsigned char	pad2;
  } MCtEntry;

  fpp_dm_cmd_t cmd, *prsp;
    //Conntrack entry

    unsigned int startaddr;
    unsigned int tmp,count,j, k;
    unsigned char rspbuf[512];
    char output_line[128] ; //cli inserts newlines - need to buffer 

  //  startaddr = simple_strtoul(argv[0],NULL,0);
  if ((argc < 1) || (1 != sscanf(argv[0],"%i",&startaddr)))
    goto usage;
  
  count = 16;
  if (argc > 1) {
    //    len =  simple_strtoul(argv[1],NULL,0);
    if (1 == sscanf(argv[1],"%i",&tmp))
      count = tmp;
    if (count > 9999)
      count = 9999;
  }
 
  prsp = (void*)rspbuf;
  cmd.msp_addr = startaddr;
  cmd.msp_len = sizeof(MCtEntry);
  k = 0;
  while (k < count) {
    if (
	(cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_DMEM,&cmd,(sizeof(cmd)- sizeof(cmd.mspmem)),rspbuf) < sizeof(unsigned short) ) ||
	cmmDaemonCmdRC(rspbuf)
	)
      {
      cmm_print(DEBUG_ERROR, "Error_sending CMD_TRC_DMEM l:%zu %04x ql:%04x qa:%08x\n",
		(sizeof(cmd)- sizeof(cmd.mspmem)),
		cmd.pad_in_rc_out,
		cmd.msp_len,
		cmd.msp_addr );
      break;
    }
    memcpy(&MCtEntry,prsp->mspmem, sizeof(MCtEntry));
    j = sprintf(output_line,"%04d @%08x", k,   prsp->msp_addr);
    j += sprintf(output_line+j," n:%p t:%p an:%p ap:%p sa:%08x da:%08x S:%04x D:%04x",
		 MCtEntry.next,
		 MCtEntry.twin,
		 MCtEntry.actNext,
		 MCtEntry.actPrevious,
		 MCtEntry.Saddr,
		 MCtEntry.Daddr,
		 MCtEntry.Sport,
		 MCtEntry.Dport 
		 );
    cmm_print(DEBUG_STDOUT, "%s\n",output_line);
    if ( MCtEntry.actNext == NULL)
      break;
    else
      cmd.msp_addr = (unsigned long)MCtEntry.actNext;
    cmd.msp_len = sizeof(MCtEntry);
    k++;
  }
  
  return 0;
 usage:
  cmm_print(DEBUG_STDOUT, "Usage: mspmem ct addr [count|9999], Address and length have to be multiple of 4\n");
  return 0;

}

/* 
   status
*/  
int prfStatus(daemon_handle_t daemon_handle, int argc, char *argv[]) {
  fpp_trc_stat_cmd_t *res;
  unsigned char rspbuf[CMM_BUF_SIZE];
  unsigned short rc;

  if (
      (cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_STATUS,NULL,0,rspbuf) < sizeof(unsigned short) ) ||
      (rc = cmmDaemonCmdRC(rspbuf))
      )
    {
      cmm_print(DEBUG_ERROR, "Error_sending CMD_TRC_SHOW command to fpp\n");
      return 0;
    } 

  res = (fpp_trc_stat_cmd_t *)rspbuf;
/*   cmm_print(DEBUG_STDOUT, */
  cmm_print(DEBUG_COMMAND,
	    " CMD_TRC_SHOW response:rc(%d) %04X %04X %04X %04X %08X %08X\n",
	    rc,
	    res->pad_in_rc_out, res->state,
	    res->pmn0, res->pmn1,
	    res->trc_mask, res->bsycpu_weight);
  switch(res->state) {
  case 0:
    cmm_print(DEBUG_STDOUT, "Tracing is OFF\n");
    break;
  case 1:
    cmm_print(DEBUG_STDOUT, "Tracing is ON\n");
    break;
  case 2:
    cmm_print(DEBUG_STDOUT, "CPU measurement is ON\n");
    break;
  }
  cmm_print(DEBUG_STDOUT,"pmn0:0x%02x pmn1:0x%02x t_mask:0x%04x b_weight 0x%x\n",
	    res->pmn0, res->pmn1, res->trc_mask, res->bsycpu_weight);
  return 0;
}

/* Busy CPU */
int prfPTBusyCPU(daemon_handle_t daemon_handle, int argc, char **argv) {
  fpp_trc_cpu_cmd_t cmd, *prsp;
  unsigned char rspbuf[512];
  unsigned short len;
  int cmdrc;
  unsigned int tmp;
  if (argc < 1) 
    goto usage;
  if (strncmp(argv[0],"start",3) == 0) cmd.on_off = 1;
  else if (strncmp(argv[0],"stop",3) == 0) cmd.on_off = 0;
  else goto usage;

  len = 2* sizeof(unsigned short);
  if (cmd.on_off) {
    if ((argc <2) || (1 !=  sscanf(argv[1],"%i",&tmp)))
      tmp = 0;
    cmd.on_weight_off_pad = tmp;
    len += 2 * sizeof(unsigned short);
  }

  prsp = (void*) rspbuf;
  cmm_print(DEBUG_COMMAND, "Send CMD_TRC_BSYCPU OnOff:%d\n",
	    cmd.on_off);
  if ( 
      ( (cmdrc = cmmSendToDaemon(daemon_handle,  FPP_CMD_TRC_BSYCPU, &cmd, len, rspbuf)) < sizeof(unsigned short)) ||
      (cmdrc = cmmDaemonCmdRC(rspbuf))
      ) 
    {
      if ( (cmd.on_off) && (cmdrc == FPP_ERR_TRC_SOME_OK) ) {
	cmm_print(DEBUG_STDOUT,"Only weight_factor value was changed\n");
      }  else {
	cmm_print(DEBUG_ERROR, "Error 0x%x sending TRC_BSYCPU OnOff:%d\n", cmdrc, cmd.on_off);
      }
    } else {
      if (cmd.on_off) {
	cmm_print(DEBUG_STDOUT,"Busy CPU measurement started\n");
      } else if ( (prsp->off_rsp_busy_count > 0x100) || (prsp->off_rsp_idle_count >0x100 )) {
	cmm_print(DEBUG_STDOUT,"Busy:0x%llX Idle:0x%llx BusyPart:%5.2f\n",
		  prsp->off_rsp_busy_count ,
		  prsp->off_rsp_idle_count,
		  100.0 * (prsp->off_rsp_busy_count >> 8) / ((prsp->off_rsp_busy_count >> 8) +  (prsp->off_rsp_idle_count >> 8)));
      }
      else
	cmm_print(DEBUG_STDOUT,"System is idle\n");
    }

  return 0;
 usage:
  cmm_print(DEBUG_STDOUT,"Usage: prf busycpu {start|stop} [weight_factor]\n");
  return 0;
}

/* Tracing and profiling */
int prfPTsetmask(daemon_handle_t daemon_handle, int argc, char **argv){
  unsigned short cmd[2];
  unsigned int tmp;
  unsigned char rspbuf[512];

  if ((argc < 1) || (1 !=  sscanf(argv[0],"%i",&tmp)))
    goto usage;
  cmd[0] = tmp;
  cmm_print(DEBUG_COMMAND, "Sending CMD_TRC_SETMASK\n");
  if ( (cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_SETMASK, cmd, 2, rspbuf) < sizeof(unsigned short)) ||
      cmmDaemonCmdRC(rspbuf))
    {
      cmm_print(DEBUG_ERROR, "Error sending CMD_TRC_SETMASK %s\n",argv[0]);
    }
  return 0;
 usage:
  cmm_print(DEBUG_STDOUT,"Usage: prf trace setmask MASK_VALUE\n");
  return 0;
}
int prfPTstart(daemon_handle_t daemon_handle, int argc, char **argv){
  fpp_trc_on_cmd_t cmd ;
  unsigned int tmp, len;
  len = 0;
  if (argc > 0) {
    if (1 !=  sscanf(argv[0],"%i",&tmp))
      goto usage;
    cmd.pmn0_id = (unsigned short) (tmp & 0xff);
    len = 6 /* (offsetof(cmd.pmn1_id) + sizeof(cmd.pmn1_id)) */;
    if (argc > 1) {
      if (1 !=  sscanf(argv[1],"%i",&tmp))
	goto usage;
      len += sizeof(cmd.pmn1_id);
      cmd.pmn1_id = (unsigned short) (tmp & 0xff);      
    }
  }
  cmm_print(DEBUG_COMMAND, "Sending CMD_TRC_ON\n");
  if (
      (cmmSendToDaemon(daemon_handle, FPP_CMD_TRC_ON, &cmd, len, &cmd) < sizeof(unsigned short))||
      cmmDaemonCmdRC(&cmd))
    {
      cmm_print(DEBUG_ERROR, "Error sending CMD_TRC_ON to MSP\n");
    }
  return 0;
 usage:
  cmm_print(DEBUG_STDOUT,"Usage: prf trace start [ctr_id0 [ctrid1]]\n");
  return 0;
}

static int cmm_trace_display(daemon_handle_t daemon_handle, fpp_trc_off_cmd_t *pcmd, unsigned int startaddr, unsigned int length, unsigned int offset) {

    /* display offset to the end of trace */
    msp_dm(daemon_handle, startaddr + offset , length - offset, 0x1);
    if (offset)
      msp_dm(daemon_handle, startaddr, offset, 0x1);
 
    return 0;
}

int prfPTswitch(daemon_handle_t daemon_handle, int argc, char **argv){
  fpp_trc_off_cmd_t *res; 
  unsigned int startaddr, offset, length;
  unsigned char rspbuf[CMM_BUF_SIZE];
  if (
      (argc == 0) && 
      ( 
       (cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_SWITCH,NULL,0,rspbuf) < sizeof(unsigned short) ) ||
       cmmDaemonCmdRC(rspbuf)
       )
      )
    {
      cmm_print(DEBUG_ERROR, "Error_sending CMD_TRC_SWITCH command to fpp\n");
      return 0;
    } else if ((argc > 0) && 
	       (
		(cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_OFF,NULL,0,&rspbuf)< sizeof(unsigned short) ) || 
		cmmDaemonCmdRC(rspbuf)
		)
	       ) {
    cmm_print(DEBUG_ERROR, "Error_sending CMD_TRC_OFF command to fpp\n");
    return 0;
  }
  
  res = (fpp_trc_off_cmd_t *)rspbuf;
  cmm_print(DEBUG_COMMAND," CMD_TRC_SWITCH/OFF response: %04X %04X %04X %04X  %04X %04X %04X %04X %08X\n",
	    res->pad_in_rc_out, res->pad_in_ec_out,
	    res->pmn0_id, res->pmn1_id,
	    res->trc_module_mask, res->trc_ctr_length,
	    res->trc_mask_length, res->trc_length,
	    res->trc_address);
  startaddr = res->trc_address + res->trc_ctr_length + res->trc_mask_length + 4;
  length = res->trc_length - res->trc_ctr_length - res->trc_mask_length - 4;
  
  offset = res->pad_in_ec_out << 4; /* oldest entry in the trace */
  cmm_print(DEBUG_STDOUT,"Trace at 0x%08x for 0x%x bytes offset 0x%0x\n",startaddr , length, offset); 
  return cmm_trace_display(daemon_handle, res, startaddr, length, offset);

  cmm_print(DEBUG_STDOUT,"Usage: prf trace switch [stop]\n");
  return 0;
}
int prfPTshow(daemon_handle_t daemon_handle, int argc, char **argv) {
  fpp_trc_off_cmd_t *res; 
  unsigned char rspbuf[CMM_BUF_SIZE];
  unsigned int startaddr, offset, length;
  if (
      (cmmSendToDaemon(daemon_handle,FPP_CMD_TRC_SHOW,NULL,0,rspbuf) < sizeof(unsigned short)) ||
      cmmDaemonCmdRC(rspbuf)
      )
    {
      cmm_print(DEBUG_ERROR, "Error_sending CMD_TRC_SHOW command to fpp\n");
      return 0;
    } 
  
  res = (fpp_trc_off_cmd_t *)rspbuf;
  cmm_print(DEBUG_COMMAND," CMD_TRC_SHOW response: %04X %04X %04X %04X  %04X %04X %04X %04X %08X\n",
	    res->pad_in_rc_out, res->pad_in_ec_out,
	    res->pmn0_id, res->pmn1_id,
	    res->trc_module_mask, res->trc_ctr_length,
	    res->trc_mask_length, res->trc_length,
	    res->trc_address);

  startaddr = res->trc_address + res->trc_ctr_length + res->trc_mask_length + 4;
  length = res->trc_length - res->trc_ctr_length - res->trc_mask_length - 4;
  offset = res->pad_in_ec_out << 4; /* oldest entry in the trace */

  cmm_print(DEBUG_STDOUT,"Trace at 0x%08x for 0x%x bytes offset 0x%0x\n",startaddr , length, offset);
  return cmm_trace_display(daemon_handle, res, startaddr, length, offset);

  cmm_print(DEBUG_STDOUT,"Usage: prf trace showtrace\n");  return 0;
  return 0;
}
int cmmPrfMem(int argc,char **argv,int firstarg ,daemon_handle_t daemon_handle)
{
  if (argc > firstarg) {
    if (strncasecmp(argv[firstarg],"bytes",1) == 0)
      return prfMspMS(daemon_handle, argc - firstarg - 1, &argv[firstarg+1]);
    else if (strncasecmp(argv[firstarg],"words",1) == 0)
      return prfMspMSW(daemon_handle, argc - firstarg - 1, &argv[firstarg+1]);
    else  if (strncasecmp(argv[firstarg],"ct",1) == 0)
      return prfMspMSW(daemon_handle, argc - firstarg - 1, &argv[firstarg+1]);
  }

  prfMspMS(daemon_handle, 0, NULL);
  prfMspMSW(daemon_handle, 0, NULL);
  prfMspCT(daemon_handle, 0, NULL);
  return 0;
}

int cmmPrfNM(int argc,char **argv,int firstarg ,daemon_handle_t daemon_handle)
{
  if (argc > firstarg) {
    if (strncasecmp(argv[firstarg],"status",1) == 0) {
      return prfStatus(daemon_handle, 0,NULL);
    }
    else if (strncasecmp(argv[firstarg],"busycpu",1) == 0) {
      /* Available CPU measurement */
      return prfPTBusyCPU(daemon_handle,argc - firstarg - 1, &argv[firstarg+1]); 
    } else if  (strncasecmp(argv[firstarg],"trace",1) == 0) {
      /* Tracing command */
      if (argc > firstarg +1 ) {
	if (strncasecmp(argv[firstarg+1],"setmask",2) == 0) {
	  return prfPTsetmask(daemon_handle,argc-firstarg-2,&argv[firstarg+2]);
	} else if (strncasecmp(argv[firstarg+1],"start",2) == 0) {
	  return prfPTstart(daemon_handle,argc-firstarg-2,&argv[firstarg+2]);
	} else if (strncasecmp(argv[firstarg+1],"switch",2) == 0) {
	  return prfPTswitch(daemon_handle,argc-firstarg-2,&argv[firstarg+2]);
	} else if (strncasecmp(argv[firstarg+1],"showtrace",2) == 0) {
	  return prfPTshow(daemon_handle,argc-firstarg-2,&argv[firstarg+2]);
	}
      }
    cmm_print(DEBUG_ERROR,"Usage prf trace {setmask|start|switch|showtrace}\n");
    }
  } else
    cmm_print(DEBUG_ERROR,"Usage: prf {status|trace|busycpu}\n");
    
  return 0;
}
