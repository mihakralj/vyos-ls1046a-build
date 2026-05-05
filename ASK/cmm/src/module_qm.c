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
#include "fpp.h"
#include <ctype.h>
#include <limits.h>

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmQmShowPrintHelp()
{
	cmm_print(DEBUG_STDOUT, "show qm not yet supported\n");
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmQmShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	
//help:
	cmmQmShowPrintHelp();
	return -1;
}

int cmmQmExptRateQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
#if defined(COMCERTO_2000) || defined(LS1043)
	int cpt = tabStart;
#endif
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
	short rc;
	fpp_qm_expt_rate_cmd_t *pExptRateCmd = ( fpp_qm_expt_rate_cmd_t *)&rxbuf.rcvBuffer;

#if defined(COMCERTO_2000) || defined(LS1043)
	if(!keywords[cpt])
		goto help;
	memset(pExptRateCmd, 0, sizeof(fpp_qm_expt_rate_cmd_t));
	if (strcasecmp(keywords[cpt], "eth") == 0)
		pExptRateCmd->if_type = FPP_EXPT_TYPE_ETH;
#ifdef COMCERTO_2000
	else if (strcasecmp(keywords[cpt], "wifi") == 0)
		pExptRateCmd->if_type = FPP_EXPT_TYPE_WIFI;
	else if (strcasecmp(keywords[cpt], "arp_ndp") == 0)
		pExptRateCmd->if_type = FPP_EXPT_TYPE_ARP;
	else if (strcasecmp(keywords[cpt], "pcap") == 0)
		pExptRateCmd->if_type = FPP_EXPT_TYPE_PCAP;
#endif
	else
		goto help;
#endif
#ifdef LS1043
	if (keywords[++cpt]) {
		if (strcmp(keywords[cpt], "reset") == 0)
			pExptRateCmd->clear = 1;		
	} 
#endif

   	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY_EXPT_RATE , 
                            pExptRateCmd, sizeof(fpp_qm_expt_rate_cmd_t), rxbuf.rcvBuffer);
	
   	if (rcvBytes < sizeof( fpp_qm_expt_rate_cmd_t)  ) {
   		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == FPP_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR, "ERROR: does not support ACTION_QUERY\n");
                } else {
                    cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP FPP_CMD_QM_QUERY_EXPT_RATE cmd, rc:%d\n", rc);
                }
                return CLI_OK;
	}
   	cmm_print(DEBUG_STDOUT, "QM Exception RATE (packets/sec): %d \nBurst size(packets/usec):\t%d \n\n", 
					pExptRateCmd->pkts_per_sec,  pExptRateCmd->burst_size);
#ifdef LS1043
	cmm_print(DEBUG_STDOUT, "Red (dropped) packets   \t%u\n", pExptRateCmd->counterval[RED_TOTAL]);
	cmm_print(DEBUG_STDOUT, "Yellow packets          \t%u\n", pExptRateCmd->counterval[YELLOW_TOTAL]);
	cmm_print(DEBUG_STDOUT, "Green packets           \t%u\n", pExptRateCmd->counterval[GREEN_TOTAL]);
	cmm_print(DEBUG_STDOUT, "packets recolored red   \t%u\n", pExptRateCmd->counterval[RED_RECOLORED]);
	cmm_print(DEBUG_STDOUT, "packets recolored yellow\t%u\n", pExptRateCmd->counterval[YELLOW_RECOLORED]);
#endif
   	return CLI_OK;
#if defined(COMCERTO_2000) || defined(LS1043)
help:
#ifdef COMCERTO_2000
	cmm_print(DEBUG_STDOUT, "Usage: query qmexptrate {eth | wifi | arp_ndp | pcap}\n");
#else
	cmm_print(DEBUG_STDOUT, "Usage: query qmexptrate {eth}\n");
#endif
	return CLI_OK;
#endif
}

#ifdef LS1043
/*
 * This function query DSCP FQ mapping. It gets mapping status on interface, if it is enable
 * it also gets each DSCP mapped FQID value.
*/
int cmmQmDSCPFqMapQueryProcess(char ** keywords, int cpt, daemon_handle_t daemon_handle)
{
	int rcvBytes = 0;
	union u_rxbuf rxbuf;
	short rc;
	short index;
	fpp_qm_iface_dscp_fqid_map_cmd_t *pDscpFqMapCmd = (fpp_qm_iface_dscp_fqid_map_cmd_t *)&rxbuf.rcvBuffer;

	if(!keywords[cpt])
		goto help;
	memset(pDscpFqMapCmd, 0, sizeof(fpp_qm_iface_dscp_fqid_map_cmd_t));
	if (get_port_id(keywords[cpt]) >= 0)
	{
		STR_TRUNC_COPY(pDscpFqMapCmd->interface, keywords[cpt], sizeof(pDscpFqMapCmd->interface));
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid interface name(%s)\n", keywords[cpt]);
		goto help;
	}

	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY_IFACE_DSCP_FQID_MAP,
			pDscpFqMapCmd, sizeof(fpp_qm_iface_dscp_fqid_map_cmd_t), rxbuf.rcvBuffer);

	if (rcvBytes < sizeof(fpp_qm_iface_dscp_fqid_map_cmd_t)  ) {
		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
		cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP_CMD_QM_QUERY_IFACE_DSCP_FQ_MAP cmd, rc:%d rcvBytes %d\n", rc, rcvBytes);
		return CLI_OK;
	}

	cmm_print(DEBUG_STDOUT, "QM DSCP FQ Map :\n");
	cmm_print(DEBUG_STDOUT, "Status : %s\n", pDscpFqMapCmd->enable ? "Enable" : "Disable");
	if (pDscpFqMapCmd->enable)
	{
		cmm_print(DEBUG_STDOUT, "Below information is fqid configured for each dscp value(0 means fqid not configured)\n");
		for (index = 0; index < FPP_NUM_DSCP; index++)
			cmm_print(DEBUG_STDOUT, "dscp[%d] : 0x%x(policer profile: 0x%x(%d) fqid: 0x%x(%d))\n", 
				index, pDscpFqMapCmd->fqid[index], 
				pDscpFqMapCmd->fqid[index] >> 24, pDscpFqMapCmd->fqid[index] >> 24,
				(pDscpFqMapCmd->fqid[index] << 8) >>8, (pDscpFqMapCmd->fqid[index] << 8) >>8);
	}
	return CLI_OK;
help:
	cmm_print(DEBUG_STDOUT, "Usage: query qm-dscp-fqmap {physical interface name}\n");
	return CLI_OK;
}

int cmmQmFFRateQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int cpt = tabStart;
        int rcvBytes = 0;
        union u_rxbuf rxbuf;
        short rc;
        fpp_qm_ff_rate_cmd_t *pFFRateCmd = ( fpp_qm_ff_rate_cmd_t *)&rxbuf;

        if(!keywords[cpt])
                goto help;

	memset(pFFRateCmd, 0, sizeof(fpp_qm_ff_rate_cmd_t));
	strncpy((char *)(&pFFRateCmd->interface[0]),(keywords[cpt]), IFNAMSIZ);
	if (keywords[++cpt]) {
		if (strcmp(keywords[cpt], "reset") == 0) {
			pFFRateCmd->clear = 1;		
		} else 	
			goto help;
	}
        rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY_FF_RATE,
                            pFFRateCmd, sizeof(fpp_qm_ff_rate_cmd_t) , &rxbuf);

        if (rcvBytes < sizeof( fpp_qm_ff_rate_cmd_t)  ) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == FPP_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR, "ERROR: does not support ACTION_QUERY\n");
                } else {
                    cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d, rcvbytes %d \n", rc, rcvBytes);
                }
                return CLI_OK;
        }
        cmm_print(DEBUG_STDOUT, "QM FF RATE (packets/sec) port %s, cir: %u, pir %u\n",
                                        pFFRateCmd->interface, pFFRateCmd->cir, pFFRateCmd->pir);
	cmm_print(DEBUG_STDOUT, "Red (dropped) packets   \t%u\n", pFFRateCmd->counterval[RED_TOTAL]);
	cmm_print(DEBUG_STDOUT, "Yellow packets          \t%u\n", pFFRateCmd->counterval[YELLOW_TOTAL]);
	cmm_print(DEBUG_STDOUT, "Green packets           \t%u\n", pFFRateCmd->counterval[GREEN_TOTAL]);
	cmm_print(DEBUG_STDOUT, "packets recolored red   \t%u\n", pFFRateCmd->counterval[RED_RECOLORED]);
	cmm_print(DEBUG_STDOUT, "packets recolored yellow\t%u\n", pFFRateCmd->counterval[YELLOW_RECOLORED]);
        return CLI_OK;
help:
	cmm_print(DEBUG_STDOUT, "Usage: query qmffrate portname\n");
        return CLI_OK;
}
#endif



#define NUM_INTERFACES GEM_PORTS


#if !defined(COMCERTO_2000) && !defined(LS1043)
int cmmQmQueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0,i,j,k,len=0;
	union u_rxbuf rxbuf;
        short rc;
        fpp_qm_query_cmd_t *pQmQuery = ( fpp_qm_query_cmd_t *)rxbuf.rcvBuffer;
	char output_buf[256];


        cmm_print(DEBUG_STDOUT, "QM details:\n");
        cmm_print(DEBUG_STDOUT, "---------- \n");
        for (i = 0 ; i < NUM_INTERFACES; i++)
   	{
	    char ifname[IFNAMSIZ];

	    memset(rxbuf.rcvBuffer,0,256);
            pQmQuery->port = i;
            rcvBytes = cmmSendToDaemon(daemon_handle,FPP_CMD_QM_QUERY ,
                  pQmQuery, sizeof(fpp_qm_query_cmd_t) , rxbuf.rcvBuffer);

            if (rcvBytes != sizeof(fpp_qm_query_cmd_t) ) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == FPP_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR, "ERROR: doess not support ACTION_QUERY\n");
                } else {
                    cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }

	    cmm_print(DEBUG_STDOUT, "Interface : %s\n", get_port_name(pQmQuery->port, ifname, IFNAMSIZ));

	    if (pQmQuery->queue_qosenable_mask != 0) {
            	cmm_print(DEBUG_STDOUT, "QOS: Enabled on queue(s): \n");
		for (j=0; j < FPP_NUM_QUEUES; j++)
		{
			if(pQmQuery->queue_qosenable_mask & (1 << j))
				len += sprintf(output_buf+len, "%d  ", j);
		}
		cmm_print(DEBUG_STDOUT, "%s \n",output_buf);
	    }
	    else
            	cmm_print(DEBUG_STDOUT, "QOS: Disabled \n");


		cmm_print(DEBUG_STDOUT, "Maximum Tx Depth = %d \n", pQmQuery->max_txdepth);

		cmm_print(DEBUG_STDOUT, "Shaper details:\n");
        	cmm_print(DEBUG_STDOUT, "---------- \n");
		for (j =0; j < FPP_NUM_SHAPERS; j++)
		{
			len=0;
			cmm_print(DEBUG_STDOUT, "Shaper %d:\n", j);

			if(pQmQuery->shaper_qmask[j] == 0)
				cmm_print(DEBUG_STDOUT, "No Queues attached\n");
			else 
			{
				cmm_print(DEBUG_STDOUT, "The following queue(s) are attached: \n");
				for (k=0; k < FPP_NUM_QUEUES; k++)
				{
					if(pQmQuery->shaper_qmask[j] & (1 << k))
						len += sprintf(output_buf+len, "%d  ", k);  
				}
				cmm_print(DEBUG_STDOUT, "%s \n",output_buf);
			}

			cmm_print(DEBUG_STDOUT, "Tokens Per Clock Period %d \n", pQmQuery->tokens_per_clock_period[j]);
			cmm_print(DEBUG_STDOUT, "Bucket Size %d \n", pQmQuery->bucket_size[j]);

		}
		cmm_print(DEBUG_STDOUT, "---------- \n");

		cmm_print(DEBUG_STDOUT, "Scheduler details:\n");
        	cmm_print(DEBUG_STDOUT, "---------- \n");
		for (j =0; j < FPP_NUM_SCHEDULERS; j++)
		{
			len=0;
			cmm_print(DEBUG_STDOUT, "Scheduler %d:\n", j);

			if(pQmQuery->sched_qmask[j] == 0)
				cmm_print(DEBUG_STDOUT, "No Queues attached\n");
			else 
			{
				cmm_print(DEBUG_STDOUT, "The following queue(s) are attached: \n");
				for (k=0; k < FPP_NUM_QUEUES; k++)
				{
					if(pQmQuery->sched_qmask[j] & (1 << k))
						len += sprintf(output_buf+len, "%d  ", k);  						
				}
				cmm_print(DEBUG_STDOUT, "%s \n",output_buf);
			}

			switch (pQmQuery->sched_alg[j])
             		{
	       		case 0:
				cmm_print(DEBUG_STDOUT, "ALG : PQ \n");
				break;
	       		case 1:
				cmm_print(DEBUG_STDOUT, "ALG :CBWFQ \n");
				break;
	       		case 2:
				cmm_print(DEBUG_STDOUT, "ALG :DWRR \n");
				break;
			case 3:
				cmm_print(DEBUG_STDOUT, "ALG :RR \n");
				break;
			default:
				cmm_print(DEBUG_STDOUT, "ALG :NONE \n");
				break;
	    		}

		}
		cmm_print(DEBUG_STDOUT, "---------- \n");

		cmm_print(DEBUG_STDOUT, "Queue details:\n");
        	cmm_print(DEBUG_STDOUT, "---------- \n");
		for (j =0; j < FPP_NUM_QUEUES; j++)
		{
			len=0;
			len += sprintf(output_buf+len, "Queue %d: ", j);
			len += sprintf(output_buf+len, "Max Queue Depth %d  ", pQmQuery->max_qdepth[j]);
			cmm_print(DEBUG_STDOUT, "%s \n",output_buf);
		}
		cmm_print(DEBUG_STDOUT, "\n---------- \n");

   
	    
	    cmm_print(DEBUG_STDOUT,"--------------------------------------------------\n");
       	}
	return CLI_OK;
}
#else 
int cmmQmQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle) 
{
#ifdef ENABLE_EGRESS_QOS 
	int rcvBytes;
	fpp_qm_query_cmd_t *query;
	int cpt = tabStart;
	char *ifname;
	union u_rxbuf rxbuf;
	uint32_t ii;
        short rc;
	uint32_t clear_stats;
	uint64_t val;
	uint32_t chnl_map;
       
	if(!keywords[cpt])
		goto help;
	if(strcasecmp(keywords[cpt], "interface") != 0)
		goto help;
	if(!keywords[++cpt])
		goto help;
	/* get port parameters */
	query = (fpp_qm_query_cmd_t *)rxbuf.rcvBuffer;
	memset(query, 0, sizeof(fpp_qm_query_cmd_t));
	ifname = keywords[cpt];
	STR_TRUNC_COPY(query->interface, ifname, sizeof(query->interface));
	cpt++;
	clear_stats = 0;
	if (keywords[cpt]) {
		/* look for stats clear command */
		if(strcasecmp(keywords[cpt], "clearstats") == 0)
			clear_stats = 1;
	}
	rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY,
	    				query, sizeof(fpp_qm_query_cmd_t), rxbuf.rcvBuffer);
	if (rcvBytes != sizeof(fpp_qm_query_cmd_t))
	{
		rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
		cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d bytes %d\n", rc,
			rcvBytes);
		return CLI_OK;
	}
	if (!query->if_qos_enabled) {
		cmm_print(DEBUG_STDOUT, "Interface %s qos disabled\n", ifname);
		return CLI_OK;
	}
	cmm_print(DEBUG_STDOUT, "Egress QOS info %s::\n", ifname);
	if (!query->shaper_enabled)  {
		cmm_print(DEBUG_STDOUT, "port shaper:: disabled\n");
	} else {
		cmm_print(DEBUG_STDOUT, "port shaper:: rate in kbps %d, bucketsize %d\n", 
				query->rate, query->bsize);
	}
	chnl_map = 0;
	for (ii = 0; ii < MAX_CHANNELS; ii++)  {
		if (query->chnl_shaper_info[ii].valid) {
			chnl_map |= (1 << ii);
			if (query->chnl_shaper_info[ii].shaper_enabled) {
				cmm_print(DEBUG_STDOUT, "channel %d, shaper enabled - rate in kbps %d bucketsize %d\n",
					(ii + 1), query->chnl_shaper_info[ii].rate,
					query->chnl_shaper_info[ii].bsize);
			} else {
				cmm_print(DEBUG_STDOUT, "channel %d, shaper disabled\n", (ii + 1));
			}
		}
	}
	if (!chnl_map) {
		cmm_print(DEBUG_STDOUT, "channels not assigned to interface\n");
		return CLI_OK;
	}
	for (ii = 0; ii < MAX_CHANNELS; ii++)  {
		uint32_t jj;

		if (!(chnl_map & (1 << ii)))
			continue;

		for (jj = 0; jj < MAX_QUEUES; jj++) {
			fpp_qm_cq_query_cmd_t *cq_query;

			cq_query = (fpp_qm_cq_query_cmd_t *)rxbuf.rcvBuffer;
			/* query channel */
			memset(cq_query, 0, sizeof(fpp_qm_cq_query_cmd_t));
			/* upper nibble is channel number */
			cq_query->channel_num = ii;
			cq_query->clear_stats = clear_stats;
			cq_query->queuenum = jj;
			rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY_QUEUE,
				cq_query, sizeof(fpp_qm_cq_query_cmd_t), rxbuf.rcvBuffer);
			if (rcvBytes != sizeof(fpp_qm_cq_query_cmd_t)) {
				rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
				cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
				return CLI_OK;
			}
			cmm_print(DEBUG_STDOUT, "-------------------------------------\n"); 
			/* cmm channel numbers are 1 + ceetm channel numbers */
			if (jj < NUM_PQS)
				cmm_print(DEBUG_STDOUT, "priority que: channel %d classque %d::\n", (ii + 1), jj);
			else
				cmm_print(DEBUG_STDOUT, "wbfq: channel %d classque %d::\n", (ii + 1), jj);
			cmm_print(DEBUG_STDOUT, "fqid %d(%x), frm count %d qdepth %d\n", 
				cq_query->fqid, cq_query->fqid, cq_query->frm_count, cq_query->qdepth);
			if (jj < NUM_PQS) {
				if (cq_query->cq_ch_shaper)
					cmm_print(DEBUG_STDOUT, "channel queue shaper enabled\n");
				else
					cmm_print(DEBUG_STDOUT, "channel queue shaper disabled\n");
			}
			if (jj >= NUM_PQS) {
				cmm_print(DEBUG_STDOUT, "wbfq priority %d, weight %d\n", 
					cq_query->wbfq_priority, cq_query->weight);
				if (cq_query->wbfq_chshaper)
					cmm_print(DEBUG_STDOUT, "channel queue shaper enabled\n");
				else
					cmm_print(DEBUG_STDOUT, "channel queue shaper disabled\n");
			}	
			/* display cq stats */
			cmm_print(DEBUG_STDOUT, "\nclassque %d statistics::\n", jj);
			val = (((uint64_t)cq_query->deque_pkts_high << 32) |
				(uint64_t)cq_query->deque_pkts_lo);
			cmm_print(DEBUG_STDOUT, "deque packets\t %lld\n", val);
			val = (((uint64_t)cq_query->deque_bytes_high << 32) |
				(uint64_t)cq_query->deque_bytes_lo);
			cmm_print(DEBUG_STDOUT, "deque bytes\t %lld\n", val);
			val = (((uint64_t)cq_query->reject_pkts_high << 32) |
				(uint64_t)cq_query->reject_pkts_lo);
			cmm_print(DEBUG_STDOUT, "reject packets\t %lld\n", val);
			val = (((uint64_t)cq_query->reject_bytes_high << 32) |
				(uint64_t)cq_query->reject_bytes_lo);
			cmm_print(DEBUG_STDOUT, "reject bytes\t %lld\n", val);

			/* display cq shaper stats */
			if (cq_query->cq_shaper_on) {
				cmm_print(DEBUG_STDOUT, "\n\nclass queue %d, shaper enabled - rate in kbps %u\n",
					jj, cq_query->cir);
				cmm_print(DEBUG_STDOUT, "Total green pkts : %u\n", cq_query->counterval[GREEN_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total yellow pkts: %u\n", cq_query->counterval[YELLOW_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total red pkts   : %u\n", cq_query->counterval[RED_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total recoloured yellow pkts : %u\n", cq_query->counterval[YELLOW_RECOLORED]);
				cmm_print(DEBUG_STDOUT, "Total recoloured red pkts    :  %u\n", cq_query->counterval[RED_RECOLORED]);
			} else {
				cmm_print(DEBUG_STDOUT, "\nclass queue  %d, shaper disabled\n", jj);
			}
		}
	}
        return CLI_OK;
help:
	{
		char buf[128];

		print_all_gemac_ports(buf, 128);
       		cmm_print(DEBUG_STDOUT, "Usage: query qm interface %s\n", buf);
	}
#else
	cmm_print(DEBUG_STDOUT, "Egress Qos support disabled\n");
	
#endif
        return CLI_OK;
}

int cmmQmIngressQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle)
{
#ifdef ENABLE_INGRESS_QOS
	int rcvBytes;
	int cpt = tabStart;
	union u_rxbuf rxbuf;
	uint32_t ii;
	short rc;

	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "stats") == 0) {
		fpp_qm_ingress_plcr_query_stats_cmd_t *query;
		struct fpp_qm_ingress_policer_info *pstats;

		query = (fpp_qm_ingress_plcr_query_stats_cmd_t *)rxbuf.rcvBuffer;
		memset(query, 0, sizeof(fpp_qm_ingress_plcr_query_stats_cmd_t));

		cpt++;
		if((keywords[cpt])) {
			if(strcasecmp(keywords[cpt], "clear") == 0)
				query->clear = 1;
		}
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_INGRESS_POLICER_QUERY_STATS,
						query, sizeof(fpp_qm_ingress_plcr_query_stats_cmd_t), rxbuf.rcvBuffer);

		if (rcvBytes != (sizeof(struct fpp_qm_ingress_policer_info) * FPP_NUM_INGRESS_POLICER_QUEUES)) {
			rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d bytes %d\n", rc,
				rcvBytes);
			return CLI_OK;
		}
		for(ii = 0; ii< FPP_NUM_INGRESS_POLICER_QUEUES; ii++) {
			pstats = &query->policer_stats[ii];
			if(pstats->policer_on) {
				cmm_print(DEBUG_STDOUT, "Ingress QOS info Queue No:%d::\n", ii);
				cmm_print(DEBUG_STDOUT,"\n");
				cmm_print(DEBUG_STDOUT, "Policer Enabled\n");

				/* display policer stats */
				cmm_print(DEBUG_STDOUT, "cir              : %u\n", pstats->cir);
				cmm_print(DEBUG_STDOUT, "pir              : %u\n", pstats->pir);
				cmm_print(DEBUG_STDOUT, "Total green pkts : %u\n", pstats->counterval[GREEN_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total yellow pkts: %u\n", pstats->counterval[YELLOW_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total red pkts   : %u\n", pstats->counterval[RED_TOTAL]);
				cmm_print(DEBUG_STDOUT, "Total recoloured yellow pkts : %u\n", pstats->counterval[YELLOW_RECOLORED]);
				cmm_print(DEBUG_STDOUT, "Total recoloured red pkts    :  %u\n", pstats->counterval[RED_RECOLORED]);
				cmm_print(DEBUG_STDOUT,"\n\n");
			}
			else {
				cmm_print(DEBUG_STDOUT, "Policer is disabled on Queue No  : %d\n",ii);
				cmm_print(DEBUG_STDOUT, "cir              : %u\n", pstats->cir);
				cmm_print(DEBUG_STDOUT, "pir              : %u\n", pstats->pir);
				cmm_print(DEBUG_STDOUT,"\n\n");
				continue;
			}
		}
	}
	else
		goto help;

	return CLI_OK;
help:
	{
		cmm_print(DEBUG_STDOUT, "Usage: query qmingress stats {clear}\n");
	}
#else
		cmm_print(DEBUG_STDOUT, "Ingress Qos support disabled\n");
#endif
	return CLI_OK;
}

#ifdef SEC_PROFILE_SUPPORT
int cmmQmSecQueryProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle)
{
#ifdef ENABLE_INGRESS_QOS
	int rcvBytes;
	int cpt = tabStart;
	union u_rxbuf rxbuf;
	short rc;

	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "stats") == 0) {
		fpp_qm_sec_plcr_query_stats_cmd_t *query;
		struct fpp_qm_ingress_policer_info *pstats;

		query = (fpp_qm_sec_plcr_query_stats_cmd_t *)rxbuf.rcvBuffer;
		memset(query, 0, sizeof(fpp_qm_ingress_plcr_query_stats_cmd_t));

		cpt++;
		if((keywords[cpt])) {
			if(strcasecmp(keywords[cpt], "clear") == 0)
				query->clear = 1;
		}
		rcvBytes = cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUERY_SEC_POLICERRATE,
						query, sizeof(fpp_qm_sec_plcr_query_stats_cmd_t), rxbuf.rcvBuffer);

		if (rcvBytes != (sizeof(struct fpp_qm_ingress_policer_info))) {
			rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
			cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d bytes %d \n", rc,
				rcvBytes);
			return CLI_OK;
		}
		pstats = &query->policer_stats;
		cmm_print(DEBUG_STDOUT, "Sec policer QOS info::\n");
		cmm_print(DEBUG_STDOUT,"\n");

		/* display policer stats */
		cmm_print(DEBUG_STDOUT, "cir              : %u\n", pstats->cir);
		cmm_print(DEBUG_STDOUT, "pir              : %u\n", pstats->pir);
		cmm_print(DEBUG_STDOUT, "cbs              : %u\n", pstats->cbs);
		cmm_print(DEBUG_STDOUT, "pbs              : %u\n", pstats->pbs);
		cmm_print(DEBUG_STDOUT, "Total green pkts : %u\n", pstats->counterval[GREEN_TOTAL]);
		cmm_print(DEBUG_STDOUT, "Total yellow pkts: %u\n", pstats->counterval[YELLOW_TOTAL]);
		cmm_print(DEBUG_STDOUT, "Total red pkts   : %u\n", pstats->counterval[RED_TOTAL]);
		cmm_print(DEBUG_STDOUT, "Total recoloured yellow pkts : %u\n", pstats->counterval[YELLOW_RECOLORED]);
		cmm_print(DEBUG_STDOUT, "Total recoloured red pkts    :  %u\n", pstats->counterval[RED_RECOLORED]);
		cmm_print(DEBUG_STDOUT,"\n\n");
	}
	else
		goto help;

	return CLI_OK;
help:
	{
		cmm_print(DEBUG_STDOUT, "Usage: query qmsecrate stats {clear}\n");
	}
#else
	cmm_print(DEBUG_STDOUT, "Sec profile Qos support disabled\n");
#endif
	return CLI_OK;
}
#endif /* endif for SEC_PROFILE_SUPPORT */
#endif


/************************************************************
 *
 *
 *
 ************************************************************/

#ifdef COMCERTO_2000
#define QRANGE "{0-15}"
#else
#define QRANGE "{0-31}"
#endif

#ifdef LS1043
#define PQ_RANGE 	"{0 - 7}"
#define WBFQ_RANGE 	"{8 - 15}"
#define MAX_PQS		8
void cmmQmSetPrintHelp(void)
{
	char buf[128];

	print_all_gemac_ports(buf, 128);
	cmm_print(DEBUG_STDOUT, 
#ifdef ENABLE_EGRESS_QOS
		"Usage:\n"
		"iface name {%s}\n"
		"\n"
		"	set qm interface [iface name] reset\n"
		"	set qm interface [iface name] qos {on | off}\n"
		"	set qm interface [iface name] shaper\n"
                "                                       [on | off]\n"
                "                                       [rate {Kbps}]\n"
                "                                       [bucketsize]\n"
		"	set qm channel <1-8> shaper\n"
                "                                       [on | off]\n"
                "                                       [rate {Kbps}]\n"
                "                                       [bucketsize]\n"
		"	set qm channel <1-8> assign interface [iface name]>\n"
		"	set qm channel <1-8> wbfq chshaper [on | off]\n"
		"					[priority {0 - 6}]\n"
		"	set qm channel <1-8> classque" PQ_RANGE "\n"
                "                                       [qdepth {depth}]\n"
                "                                       [chshaper {on | off}]\n"
		"	set qm channel <1-8> classque" WBFQ_RANGE "\n"
                "                                       [qdepth {depth}]\n"
                "                                       [weight {1 - 255}]\n"
		"	set qm channel <1-8> classque [0-15] cqshaper\n"
                "                                       [on | off]\n"
                "                                       [rate {Kbps}]\n"
		"	set qm dscp-to-fqmap [iface name] enable|disable\n"
		"	set qm dscp-to-fqmap [iface name] dscp [0-63] channel-id [0-7] classqueue [0-15]\n"
		"	set qm dscp-to-fqmap [iface name] dscp [0-63] reset\n"
#endif
		"	set qm expt_rate {eth} {%d - %d or 0} {%d - %d}\n"
                "\n"
                "	set qm ff_rate portname [cir {%d - %d}] [pir {%d - %d}]\n"
                "\n"
#ifdef SEC_PROFILE_SUPPORT
                "	set qm sec_rate [cir {%d - %d}] [pir {%d - %d}] [cbs {%d - %d}] [pbs {%d - %d}]\n"
                "	set qm sec_rate reset\n"
#endif /* endif for SEC_PROFILE_SUPPORT */
                "\n"
                "	set qm ingress queue <0-7> policer [on | off]\n"
                "	set qm ingress queue <1-7> [cir {1 - 20971250}] [pir {1 - 20971250}]\n"
                "	set qm ingress queue default [cir {1 - 20971250}] [pir {1 - 20971250}]\n"
                "	set qm ingress reset \n"
                "\n"
		,
#ifdef ENABLE_EGRESS_QOS
	        buf,
#endif
		QM_EXPTRATE_MINVAL, QM_EXPTRATE_MAXVAL, QM_EXPTRATE_MIN_BS, QM_EXPTRATE_MAX_BS, 
		QM_FFRATE_MIN_CIR, QM_FFRATE_MAX_CIR,
		QM_FFRATE_MIN_PIR, QM_FFRATE_MAX_PIR
#ifdef SEC_PROFILE_SUPPORT
		, QM_SECRATE_MIN_CIR, QM_SECRATE_MAX_CIR,
		QM_SECRATE_MIN_PIR, QM_SECRATE_MAX_PIR,
		QM_SECRATE_MIN_CBS, QM_SECRATE_MAX_CBS,
		QM_SECRATE_MIN_PBS, QM_SECRATE_MAX_PBS
#endif /* endif for SEC_PROFILE_SUPPORT */
		);
}
#else
void cmmQmSetPrintHelp()
{
	char buf[128];

	print_all_gemac_ports(buf, 128);

	cmm_print(DEBUG_STDOUT, 
		  "Usage: set qm interface {%s}\n"
		  "                                  reset\n"
                  "\n"
#ifdef COMCERTO_2000
		  "                                  qos {on | off}\n"
#else
		  "                                  qos\n"
                  "                                       [on | off]\n"
                  "                                       [max_txdepth {bytes}]\n"
                  "                                       [scheduler {pq|cbwfq|dwrr}] **\n"
                  "                                       [nhigh_queue {number of queues}] **\n"
                  "                                       [qweight {queue number} {weight}] **\n"
                  "                                       [qdepth {queue number} {depth}] **\n"
#endif
                  "\n"
#ifdef COMCERTO_2000
                  "                                  shaper {0-7 | port}\n"
#else
                  "                                  shaper {0-7}\n"
#endif
                  "                                       [on | off]\n"
                  "                                       [rate {Kbps}]\n"
                  "                                       [ifg {bytes}]\n"
                  "                                       [bucket_size {bits}]\n"
                  "                                       [queue " QRANGE "] [queue " QRANGE "] ...\n"                  
                  "\n"
                  "                                  scheduler {0-7}\n"
                  "                                       [algorithm {pq | cbwfq | dwrr | rr}]\n"
                  "                                       [queue " QRANGE "] [queue " QRANGE "] ...\n"                  
                  "\n"
                  "                                  queue " QRANGE "\n"                  
                  "                                       [qos {on | off}] \n"
                  "                                       [shaper {0-7}]\n"
                  "                                       [scheduler {0-7}]\n"
                  "                                       [qweight {weight}]\n"
                  "                                       [qdepth {depth}]\n"
                  "\n"
#ifndef COMCERTO_2000
                  "                                  rate_limiting {on|off} **\n"
                  "                                       [rate {Kbps}]\n"
                  "                                       [bucket_size {bits}]\n"
                  "                                       [queue " QRANGE "] [queue " QRANGE "] ...\n"                  
                  "\n"
		  "                                  ** Deprecated\n"
#endif

                  "\n"
#ifdef COMCERTO_2000
		    "       set qm expt_rate {eth|wifi|arp_ndp|pcap} {1000-5000000 or 0}\n"
#endif
                  "\n"
		    "       set qm dscp_queue\n"
		    "						[queue {0-31}] \n"
                  "						[dscp {0-63}-{0-63}]  \n"
		,
	          buf);
}
#endif

/************************************************************
 *
 *
 *
 ************************************************************/
#ifdef LS1043
int qm_get_num(char **keywords, int *pcpt, uint32_t max_val, uint32_t *val, char *errmsg)
{
	char *endptr;
	unsigned int tmp;
	int cpt;

	cpt = *pcpt;
	if(!keywords[++cpt])
		return QM_ERROR;
	/* Get number from the string */
	endptr = NULL;
	tmp = strtoul(keywords[cpt], &endptr, 0);
	if (keywords[cpt] == endptr)
		return QM_ERROR; 
	if (tmp > max_val) {
		cmm_print(DEBUG_CRIT, "%s", errmsg);
		return QM_ERROR;
	}
	*pcpt = (cpt + 1);
	*val = tmp;
	return QM_SUCCESS;
}

#ifdef ENABLE_EGRESS_QOS
static int qm_shaper_cfg(char **keywords, int *pcpt, fpp_qm_shaper_cfg_cmd_t *shaperCmd, daemon_handle_t daemon_handle)
{
	union u_rxbuf rxbuf;
	int cpt;

	cpt = *pcpt;
	/* use interface name is present treat it as port shaper configuration */
	/* check for other arguments */
	cpt++;
	if(!keywords[cpt])
		return QM_ERROR;
	while (1) {
		if (keywords[cpt] == NULL) 
			break;
		if(strcasecmp(keywords[cpt], "on") == 0) {
			if (shaperCmd->enable)
				return QM_ERROR;
			shaperCmd->enable = SHAPER_ON;
			cpt++;
			continue;		
		} 
		if(strcasecmp(keywords[cpt], "off") == 0) {
			if (shaperCmd->enable)
				return QM_ERROR;
			shaperCmd->enable = SHAPER_OFF;
			cpt++;
			continue;		
		}
		if(strcasecmp(keywords[cpt], "rate") == 0) {
			/* Get an integer from the string */
			if (qm_get_num(keywords, &cpt, UINT_MAX, &shaperCmd->rate,
				"invalid value for shaper rate\n"))
				return QM_ERROR;
			shaperCmd->cfg_flags |= (RATE_VALID | SHAPER_CFG_VALID);
			continue;
		}	
		if(strcasecmp(keywords[cpt], "bucketsize") == 0) {
			/* Get an integer from the string*/
			if (qm_get_num(keywords, &cpt, UINT_MAX, &shaperCmd->bsize,
				"invalid value for bucket size\n"))
				return QM_ERROR;
			shaperCmd->cfg_flags |= (BSIZE_VALID | SHAPER_CFG_VALID);
			continue;
		}
		*pcpt = cpt;
		return QM_INVALID_KEYWORD;
	}
	/* check if all parameters have been provided for shaping if enabled */
	if (shaperCmd->cfg_flags & SHAPER_CFG_VALID) {
		if((shaperCmd->cfg_flags & 
			(RATE_VALID | BSIZE_VALID)) !=
			(RATE_VALID | BSIZE_VALID)) {
			cmm_print(DEBUG_CRIT, "shaper ERROR: missing parameters for shaper\n");
			return QM_ERROR;
		}
	}
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_SHAPER_CFG, shaperCmd, sizeof(fpp_qm_shaper_cfg_cmd_t), 
		&rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("CMD_QM_SHAPER_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	*pcpt = cpt;
	return QM_SUCCESS;
}

static int qm_port_shaper_cfg(char **keywords, int *cpt, char *ifname, daemon_handle_t daemon_handle)
{
	fpp_qm_shaper_cfg_cmd_t shaperCmd;

	memset(&shaperCmd, 0, sizeof(shaperCmd));
	STR_TRUNC_COPY(shaperCmd.interface, ifname, sizeof(shaperCmd.interface));
	shaperCmd.cfg_flags = PORT_SHAPER_CFG;
	return(qm_shaper_cfg(keywords, cpt, &shaperCmd, daemon_handle));
	
}

static int qm_channel_shaper_cfg(char **keywords, int *cpt, uint32_t channel_num, daemon_handle_t daemon_handle) 
{
	fpp_qm_shaper_cfg_cmd_t shaperCmd;

	memset(&shaperCmd, 0, sizeof(shaperCmd));
	shaperCmd.channel_num = channel_num;
	shaperCmd.cfg_flags = CHANNEL_SHAPER_CFG;
	return(qm_shaper_cfg(keywords, cpt, &shaperCmd, daemon_handle));
}


static int qm_wbfq_cfg(char **keywords, int *pcpt, uint32_t channel, daemon_handle_t daemon_handle)
{
	fpp_qm_wbfq_cfg_cmd_t wbfqCmd;
	union u_rxbuf rxbuf;
	char *kw;

	memset(&wbfqCmd, 0, sizeof(fpp_qm_wbfq_cfg_cmd_t));

	*pcpt += 1;
	if (!keywords[*pcpt])
		return QM_ERROR; 

	if(strcasecmp(keywords[*pcpt], "chshaper") != 0) {
		return QM_INVALID_KEYWORD;
	}
	*pcpt += 1;
	kw = keywords[*pcpt];
	if(!kw)
		return QM_ERROR;
	if(strcasecmp(kw, "on") == 0) {
		wbfqCmd.wbfq_chshaper = 1;
	} else {
		if(strcasecmp(kw, "off") == 0)
			wbfqCmd.wbfq_chshaper = 0;
		else
			return QM_INVALID_KEYWORD;
	}
	 wbfqCmd.cfg_flags |= WBFQ_SHAPER_VALID;
	*pcpt += 1;

	kw = keywords[*pcpt];

	if(kw && strcasecmp(kw, "priority") == 0) {
		/* Get an integer from the string*/
		if (qm_get_num(keywords, pcpt, (MAX_PQS - 2), &wbfqCmd.priority,
			"invalid value for wbfq priority\n"))
			return QM_ERROR;
		wbfqCmd.cfg_flags |= WBFQ_PRIORITY_VALID;
	}
	wbfqCmd.channel_num = channel;
	/* Send the command to CDX */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_WBFQ_CFG, &wbfqCmd, sizeof(wbfqCmd), 
		&rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0) {
			showErrorMsg("FPP_CMD_QM_WBFQ_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}		
	return QM_SUCCESS;
}

static int qm_classque_cfg(char **keywords, int *pcpt, uint32_t channel, daemon_handle_t daemon_handle)
{
	fpp_qm_cq_cfg_cmd_t CqCmd;
	union u_rxbuf rxbuf;	
	char *kw;

	memset(&CqCmd, 0, sizeof(fpp_qm_cq_cfg_cmd_t));
	CqCmd.channel_num = channel;
	/* Get que number from the string */
	if (qm_get_num(keywords, pcpt, 15, &CqCmd.quenum,
		"invalid value for que number\n"))
		return QM_ERROR;
	while (keywords[*pcpt] != NULL) {
		kw = keywords[*pcpt];

		if(strcasecmp(kw, "cqshaper") == 0) {
			*pcpt += 1;
			kw = keywords[*pcpt];
			if(!kw)
				return QM_ERROR;
			if(strcasecmp(kw, "on") == 0) {
				CqCmd.cq_shaper_on = 1;
			} else {
				if(strcasecmp(kw, "off") == 0)
					CqCmd.cq_shaper_on = 0;
				else
					return QM_INVALID_KEYWORD;
			}
			CqCmd.cfg_flags |= (CQ_SHAPER_CFG_VALID);
			*pcpt += 1;
			kw = keywords[*pcpt];
			if(!kw)
				return QM_ERROR;

			if(strcasecmp(kw, "rate") == 0) {
				/* Get an integer from the string */
				if (qm_get_num(keywords, pcpt, UINT_MAX, &CqCmd.shaper_rate,
					"invalid value for shaper rate\n"))
					return QM_ERROR;
				CqCmd.cfg_flags |= (CQ_RATE_VALID | CQ_SHAPER_CFG_VALID);
			}
			/* if no parameters are set abort */
			if (!(CqCmd.cfg_flags & (CQ_SHAPER_CFG_VALID | CQ_RATE_VALID |
				CQ_CMINFO_VALID)))
				return QM_ERROR;

		}
		else {
			if (CqCmd.quenum >= NUM_PRIO_QUEUES) {
				if(strcasecmp(kw, "weight") == 0) {
					/* Get weight from the string */
					if (qm_get_num(keywords, pcpt, UINT_MAX, &CqCmd.weight,
						"invalid value for que weight\n"))
						return QM_ERROR;
					CqCmd.cfg_flags |= (CQ_WEIGHT_VALID);
					continue;
				}
			}
			if(strcasecmp(kw, "chshaper") == 0) {
				*pcpt += 1;
				kw = keywords[*pcpt];
				if(!kw)
					return QM_ERROR;
				if(strcasecmp(kw, "on") == 0) {
					CqCmd.ch_shaper_en = 1;
				} else {
					if(strcasecmp(kw, "off") == 0)
						CqCmd.ch_shaper_en = 0;
					else
						return QM_INVALID_KEYWORD;
				}
				CqCmd.cfg_flags |= (CQ_SHAPER_CFG_VALID);
				*pcpt += 1;
				continue;
			}
			if(strcasecmp(kw, "qdepth") == 0) {
				/* Get td threshold from the string */
				if (qm_get_num(keywords, pcpt, UINT_MAX, &CqCmd.tdthresh,
					"invalid value for que depth\n"))
					return QM_ERROR;
				CqCmd.cfg_flags |= (CQ_TDINFO_VALID);
				continue;
			}
			return QM_INVALID_KEYWORD;
		}
	}

	/* if no parameters are set abort */
	if (!(CqCmd.cfg_flags & (CQ_WEIGHT_VALID | CQ_SHAPER_CFG_VALID | CQ_TDINFO_VALID |
					CQ_CMINFO_VALID)))
		return QM_ERROR;

	/* Send the command to CDX */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_CQ_CFG, &CqCmd, sizeof(CqCmd),
		&rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("FPP_CMD_QM_CQ_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	return QM_SUCCESS;
}

static int qm_channel_assign(char **keywords, int cpt, uint32_t channel, daemon_handle_t daemon_handle)
{
	int port_id;
	fpp_qm_chnl_assign_cmd_t assignCmd;
	union u_rxbuf rxbuf;
	char *ifname;

	if(!keywords[++cpt])
		return QM_ERROR;
	/* get interface name */
	if(strcasecmp(keywords[cpt], "interface") != 0)
		return QM_INVALID_KEYWORD;
	cpt++;
	if ((port_id = get_port_id(keywords[cpt])) >= 0)
		ifname = keywords[cpt];
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid port name %s\n", keywords[cpt]);
		return QM_ERROR;
	}
	memset(&assignCmd, 0, sizeof(fpp_qm_chnl_assign_cmd_t));
	STR_TRUNC_COPY(assignCmd.interface, ifname, sizeof(assignCmd.interface));
	assignCmd.channel_num = channel;
	/* Send CMD_QM_EXPT_RATE command */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_CHNL_ASSIGN, &assignCmd, 
		sizeof(fpp_qm_chnl_assign_cmd_t), &rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("FPP_CMD_QM_CHNL_ASSIGN", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	return QM_SUCCESS;
}

static int qm_channel_cfg(char **keywords, int *pcpt, daemon_handle_t daemon_handle)
{
	uint32_t chnl_num;
	char *kw;

	/* get channel number */
	if (qm_get_num(keywords, pcpt, FPP_NUM_SHAPERS, &chnl_num, 
		"invalid value for channel number\n"))
		return QM_ERROR;
	if (!chnl_num) {
		cmm_print(DEBUG_CRIT, "invalid value for channel number\n");
		return QM_ERROR;
	}
		
	kw = keywords[*pcpt];
	if (!kw)
		return QM_ERROR;
	/* channel number internally is from 0 thru 7 */
	chnl_num--;
	
	/* handle channel shaper configuration */
	if(strcasecmp(kw, "shaper") == 0) {
		return(qm_channel_shaper_cfg(keywords, pcpt, chnl_num, daemon_handle));
	}
	/* handle wbfq configuration within channel */
	if(strcasecmp(kw, "wbfq") == 0) {
		return(qm_wbfq_cfg(keywords, pcpt, chnl_num, daemon_handle));
	}
	/* handle classque configuration within channel */
	if(strcasecmp(kw, "classque") == 0) {
		return(qm_classque_cfg(keywords, pcpt, chnl_num, daemon_handle));
	}
	/* handle channel to port assignment */
	if(strcasecmp(kw, "assign") == 0) {
		return(qm_channel_assign(keywords, *pcpt, chnl_num, daemon_handle));
	}
	return QM_INVALID_KEYWORD;
}

/*
 * This function does the following actions.
 *  1. Enable/Disable DSCP to FQ map on an interface.
 *  2. Maps specific DSCP value with channel and classqueue.
 *  3. Reset the specific DSCP value mapping.
 * It returns QM_SUCCESS after successful configuration,
 * otherwise returns QM_ERROR.
*/
static int qm_dscp_fqmap_cfg(char **keywords, int *pcpt, daemon_handle_t daemon_handle)
{
	fpp_qm_dscp_chnl_clsq_map_t dscp_fq_map_cmd;
	union u_rxbuf rxbuf;
	int cpt;
	int cmd = 0;
	uint32_t val;
	//char *ifname;

	cpt = *pcpt;
	if(!keywords[++cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: interface name(%s) is invalid\n", keywords[cpt]);
		return QM_ERROR;
	}

	memset(&dscp_fq_map_cmd, 0, sizeof(dscp_fq_map_cmd));
	if (get_port_id(keywords[cpt]) >= 0)
	{
		STR_TRUNC_COPY(dscp_fq_map_cmd.interface, keywords[cpt], sizeof(dscp_fq_map_cmd.interface));
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid interface name(%s)\n", keywords[cpt]);
		return QM_ERROR;
	}

	/* handle dscp or enable/disable */
	if(!keywords[++cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting dscp, channel id and class queue configuration or enable/disable\n");
		return QM_ERROR;
	}
	if(strcasecmp(keywords[cpt], "enable") == 0) {
		dscp_fq_map_cmd.status = 1;
		cmd = FPP_CMD_QM_DSCP_FQ_MAP_STATUS;
		goto send_cmd;
	}
	else if(strcasecmp(keywords[cpt], "disable") == 0) {
		dscp_fq_map_cmd.status = 0;
		cmd = FPP_CMD_QM_DSCP_FQ_MAP_STATUS;
		goto send_cmd;
	}
	else if(strcasecmp(keywords[cpt], "dscp") == 0) {
		/* Get dscp number from the string */
		if (qm_get_num(keywords, &cpt, FPP_NUM_DSCP-1, &val,
					"invalid dscp value\n"))
			return QM_ERROR;
		dscp_fq_map_cmd.dscp = (uint8_t)val;
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting dscp configuration\n");
		return QM_ERROR;
	}

	/* handle channel id */
	if(!keywords[cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting channel id and class queue configuration\n");
		return QM_ERROR;
	}
	if(strcasecmp(keywords[cpt], "reset") == 0) {
		cmd = FPP_CMD_QM_DSCP_FQ_MAP_RESET;
		goto send_cmd;
	}
	else if(strcasecmp(keywords[cpt], "channel-id") == 0) {
		/* Get channel id number from the string */
		if (qm_get_num(keywords, &cpt, MAX_CHANNELS-1, &val,
					"invalid channel id value\n"))
			return QM_ERROR;
		dscp_fq_map_cmd.channel_num = (uint8_t)val;
		cmd = FPP_CMD_QM_DSCP_FQ_MAP_CFG;
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting channel id configuration or dscp reset\n");
		return QM_ERROR;
	}

	/* handle class queue */
	if(!keywords[cpt]) {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting class queue configuration\n");
		return QM_ERROR;
	}
	if(strcasecmp(keywords[cpt], "classqueue") == 0) {
		/* Get class queue number from the string */
		if (qm_get_num(keywords, &cpt, MAX_QUEUES-1, &val,
					"invalid class queue value\n"))
			return QM_ERROR;
		dscp_fq_map_cmd.queue_num = (uint8_t)val;
	}
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid command, expecting class queue configuration\n");
		return QM_ERROR;
	}

send_cmd:
	/* Send the command to CDX */
	if(cmmSendToDaemon(daemon_handle, cmd, &dscp_fq_map_cmd, sizeof(dscp_fq_map_cmd),
		&rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
		{
			if (cmd == FPP_CMD_QM_DSCP_FQ_MAP_STATUS)
				showErrorMsg("FPP_CMD_QM_DSCP_FQ_MAP_STATUS", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			else if (cmd == FPP_CMD_QM_DSCP_FQ_MAP_CFG)
				showErrorMsg("FPP_CMD_QM_DSCP_FQ_MAP_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			else if (cmd == FPP_CMD_QM_DSCP_FQ_MAP_RESET)
				showErrorMsg("FPP_CMD_QM_DSCP_FQ_MAP_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			else
				showErrorMsg("Invalid cmd", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	return QM_SUCCESS;
}

static int qm_interface_cfg(char **keywords, int *pcpt, daemon_handle_t daemon_handle)
{
	int port_id;
	char *ifname;
	union u_rxbuf rxbuf;
	int cpt;

	cpt = *pcpt;
	if(!keywords[++cpt])
		return QM_ERROR;

	if ((port_id = get_port_id(keywords[cpt])) >= 0)
		ifname = keywords[cpt];
	else {
		cmm_print(DEBUG_CRIT, "ERROR: invalid port name %s\n", keywords[cpt]);
		return QM_ERROR;
	}

	if(!keywords[++cpt])
		return QM_ERROR;
	if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		fpp_qm_reset_cmd_t resetCmd;

		/* handle qos configuration reset */
		memset(&resetCmd, 0, sizeof(fpp_qm_reset_cmd_t));
		STR_TRUNC_COPY(resetCmd.interface, ifname, sizeof(resetCmd.interface));
		/* Send CMD_QM_RESET command */
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_RESET, &resetCmd, sizeof(fpp_qm_reset_cmd_t), 
			&rxbuf.rcvBuffer) == 2) {
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}	
		return QM_SUCCESS;
	}
	if(strcasecmp(keywords[cpt], "qos") == 0)
	{		
		fpp_qm_qos_enable_cmd_t enableCmd;
		/* handle Qos enable or disable on port */	
		if(!keywords[++cpt])
			return QM_ERROR;
		memset(&enableCmd, 0, sizeof(enableCmd));
		STR_TRUNC_COPY(enableCmd.interface, ifname, sizeof(enableCmd.interface));
		if(strcasecmp(keywords[cpt], "on") == 0) {
			enableCmd.enable = 1;
		} else {
			if(strcasecmp(keywords[cpt], "off") == 0)  {
				cmm_print(DEBUG_CRIT, "qos off feature not supported in this version\n");
				return QM_ERROR;
			}
			else 
				return QM_INVALID_KEYWORD;
		}
		/* Send CMD_QM_QOSENABLE command */
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QOSENABLE, &enableCmd, 
			sizeof(fpp_qm_qos_enable_cmd_t), 
			&rxbuf.rcvBuffer) == 2) {
			switch (rxbuf.result) {
				case QOS_ENERR_NOT_CONFIGURED:
					cmm_print(DEBUG_STDOUT, "no channels assigned\n");
					break;
				case QOS_ENERR_IO:
					cmm_print(DEBUG_STDOUT, "IO error\n");
					break;
				case QOS_ENERR_INVAL_PARAM:
					cmm_print(DEBUG_STDOUT, "Invalid parameters\n");
					break;
				default:
					 break;
			}
		}	
		return QM_SUCCESS;
	}
	if(strcasecmp(keywords[cpt], "shaper") == 0)
	{
		/* handle port shaper configuration */
		return(qm_port_shaper_cfg(keywords, &cpt, ifname, daemon_handle));
	}
	*pcpt = cpt;
	return QM_INVALID_KEYWORD;
}
#endif /* ENABLE_EGRESS_QOS */

static int qm_exptrate_cfg(char **keywords, int cpt, daemon_handle_t daemon_handle)
{
	/* Exception packet rate limit */
	fpp_qm_expt_rate_cmd_t exptRateCmd;
	union u_rxbuf rxbuf;
	/* Use aligned local variable for qm_get_num() to avoid taking
	 * address of packed struct members (causes alignment issues on arm64) */
	uint32_t tmp_val;

	if(!keywords[++cpt])
		return QM_ERROR;

	memset(&exptRateCmd, 0, sizeof(exptRateCmd));
	if(strcasecmp(keywords[cpt], "eth") != 0 )
		return QM_ERROR;
	exptRateCmd.if_type = FPP_EXPT_TYPE_ETH;
	/* Get an integer from the string */
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val,
		"invalid value for expt rate\n"))
		return QM_ERROR;
	exptRateCmd.pkts_per_sec = tmp_val;
	if ((exptRateCmd.pkts_per_sec != 0 &&
		(exptRateCmd.pkts_per_sec < QM_EXPTRATE_MINVAL || exptRateCmd.pkts_per_sec > QM_EXPTRATE_MAXVAL))) {
		cmm_print(DEBUG_CRIT, "CMD_QM_EXPT_RATE ERROR: rate must be zero (to disable) or a number between %d and %d\n",
			QM_EXPTRATE_MINVAL, QM_EXPTRATE_MAXVAL);
		return QM_ERROR;
	}
	cpt--;
	/* Get an integer from the string*/
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val, "invalid value for burst_size value\n"))
		return QM_ERROR;
	exptRateCmd.burst_size = tmp_val;
	/* pps values for 64 bytes frames 10 Gbps max */
	if ((exptRateCmd.burst_size < QM_EXPTRATE_MIN_BS) || (exptRateCmd.burst_size > QM_EXPTRATE_MAX_BS))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_EXPT_RATE ERROR: invalid burst size\n");
		return QM_ERROR;
	}
	/* Send CMD_QM_EXPT_RATE command */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_EXPT_RATE, &exptRateCmd, 
		sizeof(exptRateCmd), &rxbuf.rcvBuffer) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("CMD_QM_EXPT_RATE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	return QM_SUCCESS;
}

static int qm_ffrate_cfg(char **keywords, int cpt, daemon_handle_t daemon_handle)
{
	union u_rxbuf rxbuf;
	/* Use aligned local variable for qm_get_num() to avoid taking
	 * address of packed struct members (causes alignment issues on arm64) */
	uint32_t tmp_val;

	/* fast forward rate limit */
	fpp_qm_ff_rate_cmd_t ffRateCmd;

	memset(&ffRateCmd, 0, sizeof(fpp_qm_ff_rate_cmd_t));

	if(!keywords[cpt + 1])
	{
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects portname and cir/pir configuration.\n");
		return QM_ERROR;
	}
	if (strlen(keywords[++cpt]) > (IFNAMSIZ - 1)) {
		cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters\n", keywords[cpt], (IFNAMSIZ - 1));
		return QM_ERROR;
	}
	if (get_port_id(keywords[cpt]) < 0) {
		cmm_print(DEBUG_STDERR, "Error : invalid interface name %s \n", keywords[cpt]);
		return QM_ERROR;
	}
	strncpy((char *)&ffRateCmd.interface[0], keywords[cpt], IFNAMSIZ);

	if((!keywords[cpt + 1]) || (strcasecmp(keywords[++cpt], "cir") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects cir parameter and its value.\n");
		return QM_ERROR;
	}
	/* Get an integer from the string */
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val,
		"invalid value for port cir rate\n"))
		return QM_ERROR;
	ffRateCmd.cir = tmp_val;
	if ((ffRateCmd.cir < QM_FFRATE_MIN_CIR) || (ffRateCmd.cir > QM_FFRATE_MAX_CIR))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_FF_RATE ERROR: invalid cir rate\n");
		return QM_ERROR;
	}

	if((!keywords[cpt]) || (strcasecmp(keywords[cpt], "pir") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects pir parameter and its value.\n");
		return QM_ERROR;
	}
	/* Get an integer from the string*/
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val,
		"invalid value for port pir rate\n"))
		return QM_ERROR;
	ffRateCmd.pir = tmp_val;
	/* pps values for 64 bytes frames 10 Gbps max */
	if ((ffRateCmd.pir < QM_FFRATE_MIN_PIR) || (ffRateCmd.pir > QM_FFRATE_MAX_PIR))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_FF_RATE ERROR: invalid pir rate\n");
		return QM_ERROR;
	}
	if (ffRateCmd.pir < ffRateCmd.cir) {
		cmm_print(DEBUG_CRIT, "CMD_QM_FF_RATE ERROR: pir < cir\n");
		return QM_ERROR;
	}
	/* Send CMD_QM_FF_RATE command */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_FF_RATE, &ffRateCmd, sizeof(ffRateCmd), &rxbuf) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("CMD_QM_FF_RATE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	return QM_SUCCESS;
}

#ifdef ENABLE_INGRESS_QOS
static int qm_ingress_policer_cfg(char **keywords, int *pcpt, daemon_handle_t daemon_handle)
{
	int queue_no;
	int cpt;
	unsigned int tmp;
	char * endptr;
	union u_rxbuf rxbuf;
	fpp_qm_ingress_policer_cfg_cmd_t policerCfgcmd;

	cpt = *pcpt;

	if(!keywords[++cpt])
                goto help;

	if(strcasecmp(keywords[cpt], "queue") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "default") == 0)
			queue_no = 0;
		else {
			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) || ( tmp > FPP_NUM_INGRESS_POLICER_QUEUES -1)) {
				cmm_print(DEBUG_STDERR, "queue ERROR: selected queue must be a number between 0 and %d\n", (FPP_NUM_INGRESS_POLICER_QUEUES-1));
				goto help;
			}
			queue_no = tmp;
		}

		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "policer") == 0)
		{
			fpp_qm_ingress_policer_enable_cmd_t enableCmd;
			memset(&enableCmd, 0, sizeof(enableCmd));
			/* handle Ingress Qos enable or disable on queue */
			if(!keywords[++cpt])
				goto help;

			if(strcasecmp(keywords[cpt], "on") == 0)
				enableCmd.enable_flag = 1;
			 else {
				if(strcasecmp(keywords[cpt], "off") == 0)  {
					enableCmd.enable_flag = 0;
				}
				else
					goto help;
			}
			enableCmd.queue_no = queue_no;

			/* Send CMD_QM_QOSENABLE command */
			if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_INGRESS_POLICER_ENABLE, &enableCmd,
						sizeof(fpp_qm_ingress_policer_enable_cmd_t),
						&rxbuf.rcvBuffer) == 2) {

				if (rxbuf.result != 0) {
					showErrorMsg("CMD_QM_INGRESS_POLICER_ENABLE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
					cmm_print(DEBUG_ERROR, "Enable/Disable operation unsuccessful\n");
				}
				else
					cmm_print(DEBUG_STDOUT, "policer enable/disable operation successful on queue_no %d\n",enableCmd.queue_no);
			}
			return QM_SUCCESS;
		}
		else {
			memset(&policerCfgcmd, 0, sizeof(policerCfgcmd));

			if(strcasecmp(keywords[cpt], "cir") == 0) {

				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || ((tmp < QM_INGRESS_MIN_CIR) || (tmp > QM_INGRESS_MAX_CIR)))
				{
					cmm_print(DEBUG_CRIT, "CMD_QM_INGRESS_POLICER_CFG ERROR: invalid cir rate\n");
					goto help;
				}
				policerCfgcmd.cir = tmp;
			}
			if(!keywords[++cpt])
				goto help;

			if(strcasecmp(keywords[cpt], "pir") == 0) {

				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || ((tmp < QM_INGRESS_MIN_PIR) || (tmp > QM_INGRESS_MAX_PIR))) {
					cmm_print(DEBUG_CRIT, "CMD_QM_INGRESS_POLICER_CFG ERROR: invalid pir rate pir %d\n",tmp);
					goto help;
				}
				if ( tmp < policerCfgcmd.cir) {
					cmm_print(DEBUG_CRIT, "CMD_QM_INGRESS_POLICER_CFG  ERROR: pir < cir\n");
					goto help;
				}
				policerCfgcmd.pir = tmp;
			}
			policerCfgcmd.queue_no = queue_no;

			/* Send CMD_QM_QOSENABLE command */
			if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_INGRESS_POLICER_CONFIG, &policerCfgcmd,
						sizeof(fpp_qm_ingress_policer_cfg_cmd_t),
						rxbuf.rcvBuffer) == 2) {
				if (rxbuf.result != 0) {
					showErrorMsg("CMD_QM_INGRESS_POLICER_CONFIG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
					cmm_print(DEBUG_ERROR, "Policer configuration operation unsuccessful\n");
				}
				else
					cmm_print(DEBUG_STDOUT, "Policer configuration operation successful\n");
			}
			return QM_SUCCESS;
		}
	} else {

		if(strcasecmp(keywords[cpt], "reset") == 0)
		{
			fpp_qm_ingress_policer_reset_cmd_t resetCmd;

			/* Send CMD_QM_INGRESS_POLICER_RESET command */
			if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_INGRESS_POLICER_RESET, &resetCmd, sizeof(fpp_qm_ingress_policer_reset_cmd_t),
						&rxbuf.rcvBuffer) == 2) {

				if (rxbuf.result != 0) {
					cmm_print(DEBUG_ERROR, "Policer reset configuration operation unsuccessful\n");
					showErrorMsg("CMD_QM_INGRESS_POLICER_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
				}
				else
					cmm_print(DEBUG_ERROR, "Policer reset operation successful\n");
			}
			return QM_SUCCESS;
		}
	}
help:
	cmm_print(DEBUG_STDOUT, "Usage: set qm ingress queue <0-7> policer [on | off]\n");
	cmm_print(DEBUG_STDOUT, "Usage: set qm ingress queue <1-7> [cir {1 - 20971250}] [pir {1 - 20971250}]\n");
	cmm_print(DEBUG_STDOUT, "Usage: set qm ingress queue default [cir {1 - 20971250] [pir {1 - 20971250}]\n");
	cmm_print(DEBUG_STDOUT, "Usage: set qm ingress reset \n");
	return QM_ERROR;
}

#ifdef SEC_PROFILE_SUPPORT
static int qm_sec_policer_cfg(char **keywords, int cpt, daemon_handle_t daemon_handle)
{
	union u_rxbuf rxbuf;
	/* Use aligned local variable for qm_get_num() to avoid taking
	 * address of packed struct members (causes alignment issues on arm64) */
	uint32_t tmp_val;

	/* fast forward rate limit */
	fpp_qm_sec_rate_cmd_t secRateCmd;

	memset(&secRateCmd, 0, sizeof(fpp_qm_sec_rate_cmd_t));

	if(!keywords[cpt + 1])
	{
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects either cir/pir configuration or reset.\n");
		goto help;
	}
	if((strcasecmp(keywords[cpt + 1], "reset") != 0) &&
	   (strcasecmp(keywords[cpt + 1], "cir") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects either cir/pir configuration or reset.\n");
		goto help;
	}

	if(strcasecmp(keywords[cpt + 1], "reset") == 0)
	{
		fpp_qm_ingress_policer_reset_cmd_t resetCmd;

		/* Send FPP_CMD_QM_SEC_POLICER_RESET command */
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_SEC_POLICER_RESET,
				&resetCmd, sizeof(fpp_qm_ingress_policer_reset_cmd_t), &rxbuf.rcvBuffer) == 2) {

			if (rxbuf.result != 0) {
				cmm_print(DEBUG_ERROR, "Sec Policer reset configuration operation unsuccessful\n");
				showErrorMsg("FPP_CMD_QM_SEC_POLICER_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
			}
			else
				cmm_print(DEBUG_ERROR, "Sec Policer reset operation successful\n");
		}
		return QM_SUCCESS;
	}

	if(strcasecmp(keywords[++cpt], "cir") != 0)  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects cir parameter and its value.\n");
		goto help;
	}
	/* Get an integer from the string */
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val,
		"invalid value for port cir rate\n"))
		goto help;
	secRateCmd.cir = tmp_val;

	if ((secRateCmd.cir < QM_SECRATE_MIN_CIR) || (secRateCmd.cir > QM_SECRATE_MAX_CIR))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_SEC_RATE ERROR: invalid cir rate\n");
		goto help;
	}

	if((!keywords[cpt]) || (strcasecmp(keywords[cpt], "pir") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects pir parameter and its value.\n");
		goto help;
	}
	/* Get an integer from the string*/
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val,
		"invalid value for port pir rate\n"))
		goto help;
	secRateCmd.pir = tmp_val;
	/* pps values for 64 bytes frames 10 Gbps max */
	if ((secRateCmd.pir < QM_SECRATE_MIN_PIR) || (secRateCmd.pir > QM_SECRATE_MAX_PIR))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_SEC_POLICER_RATE ERROR: invalid pir rate\n");
		goto help;
	}
	if (secRateCmd.pir < secRateCmd.cir) {
		cmm_print(DEBUG_CRIT, "CMD_QM_SEC_POLICER_RATE ERROR: pir < cir\n");
		goto help;
	}
	if((!keywords[cpt]) || (strcasecmp(keywords[cpt], "cbs") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects cbs parameter and its value.\n");
		goto help;
	}
	/* Get an integer from the string*/
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val, "invalid value for port cbs value\n"))
		goto help;
	secRateCmd.cbs = tmp_val;
	/* pps values for 64 bytes frames 10 Gbps max */
	if ((secRateCmd.cbs < QM_SECRATE_MIN_CBS) || (secRateCmd.cbs > QM_SECRATE_MAX_CBS))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_SEC_POLICER_CBS ERROR: invalid cbs\n");
		goto help;
	}
	if((!keywords[cpt]) || (strcasecmp(keywords[cpt], "pbs") != 0))  {
		cmm_print(DEBUG_STDERR, "Error : invalid keyword. It expects pbs parameter and its value.\n");
		goto help;
	}
	/* Get an integer from the string*/
	if (qm_get_num(keywords, &cpt, UINT_MAX, &tmp_val, "invalid value for port pbs value\n"))
		goto help;
	secRateCmd.pbs = tmp_val;
	/* pps values for 64 bytes frames 10 Gbps max */
	if ((secRateCmd.pbs < QM_SECRATE_MIN_PBS) || (secRateCmd.pbs > QM_SECRATE_MAX_PBS))
	{
		cmm_print(DEBUG_CRIT, "CMD_QM_SEC_POLICER_PBS ERROR: invalid pbs\n");
		goto help;
	}


	/* Send CMD_QM_SEC_RATE command */
	if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_SEC_POLICER_RATE, &secRateCmd, sizeof(secRateCmd), &rxbuf) == 2)
	{
		if (rxbuf.result != 0)
			showErrorMsg("FPP_CMD_QM_SEC_POLICER_RATE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
	}
	return QM_SUCCESS;


help:
	cmm_print(DEBUG_STDOUT, "Usage: set qm sec_rate cir {%u - %u} pir {%u - %u} cbs {%u - %u} pbs {%u - %u}\n",
			QM_SECRATE_MIN_CIR, QM_SECRATE_MAX_CIR, QM_SECRATE_MIN_PIR, QM_SECRATE_MAX_PIR,
			QM_SECRATE_MIN_CBS, QM_SECRATE_MAX_CBS, QM_SECRATE_MAX_PBS, QM_SECRATE_MIN_PBS);
	cmm_print(DEBUG_STDOUT, "Usage: set qm sec_rate reset \n");
	return QM_ERROR;
}
#endif /* endif for SEC_PROFILE_SUPPORT */
#endif

int cmmQmSetProcess(char **keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt;
	int retval;

	cpt = tabStart;
	if (!keywords[cpt])  {
		retval = QM_ERROR;
		goto err_ret;
	} else
		retval = QM_INVALID_KEYWORD;
	while(1)
	{
		if(strcasecmp(keywords[cpt], "expt_rate") == 0) {
			retval = qm_exptrate_cfg(keywords, cpt, daemon_handle);
			break;
		}

		if(strcasecmp(keywords[cpt], "ff_rate") == 0)  {
			retval = qm_ffrate_cfg(keywords, cpt, daemon_handle);
			break;
		}

#ifdef ENABLE_INGRESS_QOS
		if(strcasecmp(keywords[cpt], "ingress") == 0)  {
			retval = qm_ingress_policer_cfg(keywords, &cpt, daemon_handle);
			break;
                }
#ifdef SEC_PROFILE_SUPPORT
		if(strcasecmp(keywords[cpt], "sec_rate") == 0)  {
			retval = qm_sec_policer_cfg(keywords, cpt, daemon_handle);
			break;
		}
#endif /* endif for SEC_PROFILE_SUPPORT */
#endif

#ifdef ENABLE_EGRESS_QOS
		/* handle interface configuration */
		if(strcasecmp(keywords[cpt], "interface") == 0)
		{
			retval = qm_interface_cfg(keywords, &cpt, daemon_handle);
			break;
		}
		/* handle channel configuration */
		if(strcasecmp(keywords[cpt], "channel") == 0)
		{
			retval = qm_channel_cfg(keywords, &cpt, daemon_handle);
			break;
		}
		/* handle DSCP to Q mapping configuration */
		if(strcasecmp(keywords[cpt], "dscp-to-fqmap") == 0)
		{
			retval = qm_dscp_fqmap_cfg(keywords, &cpt, daemon_handle);
			break;
		}
#endif
		break;
	} 
err_ret:
	switch(retval) {
		case QM_INVALID_KEYWORD:
			cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);
		case QM_ERROR:
			cmmQmSetPrintHelp();
			break;
		default:
			return 0;
	}
	return -1;
}
#else
int cmmQmSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp, tmp1;
	unsigned int cmdToSend = 0; /* bits field*/
	char * endptr;
	unsigned char first_dscp = 0, last_dscp = 0, dscp_range = 0;
	int num_dscp = 0;
	int i;
	unsigned char dscp_value[FPP_NUM_DSCP] = {0};

	
	fpp_qm_qos_enable_cmd_t enableCmd;
	fpp_qm_qos_alg_cmd_t algCmd;
	fpp_qm_nhigh_cmd_t nHighCmd;
	fpp_qm_max_qdepth_cmd_t maxQdepthCmd;
	fpp_qm_max_txdepth_cmd_t maxTxDepthCmd;
	fpp_qm_max_weight_cmd_t maxWeightCmd;
	fpp_qm_rate_limit_cmd_t rateLimitCmd;
	fpp_qm_expt_rate_cmd_t exptRateCmd;
	fpp_qm_scheduler_cfg_t schedulerCmd;
	fpp_qm_shaper_cfg_t shaperCmd;
	fpp_qm_reset_cmd_t resetCmd;
	fpp_qm_dscp_queue_mod_t dscpCmd;
	fpp_qm_queue_qos_enable_cmd_t queueenableCmd;
    
	union u_rxbuf rxbuf;

	memset(&enableCmd, 0, sizeof(enableCmd));
	memset(&algCmd, 0, sizeof(algCmd));
	memset(&nHighCmd, 0, sizeof(nHighCmd));
	memset(&maxQdepthCmd, 0, sizeof(maxQdepthCmd));
	memset(&maxTxDepthCmd, 0, sizeof(maxTxDepthCmd));
	memset(&maxWeightCmd, 0, sizeof(maxWeightCmd));
	memset(&rateLimitCmd, 0, sizeof(rateLimitCmd));
	memset(&exptRateCmd, 0, sizeof(exptRateCmd));
	memset(&schedulerCmd, 0, sizeof(schedulerCmd));
	memset(&shaperCmd, 0, sizeof(shaperCmd));
	memset(&resetCmd, 0, sizeof(resetCmd));
	memset(&dscpCmd, 0, sizeof(dscpCmd));
	memset(&queueenableCmd, 0, sizeof(queueenableCmd));


	if(!keywords[cpt])
		goto help;

	if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		int port_id;

		if(!keywords[++cpt])
			goto help;

		if ((port_id = get_port_id(keywords[cpt])) >= 0)
		{
			enableCmd.interface = port_id;
			algCmd.interface = port_id;
			nHighCmd.interface = port_id;
			maxQdepthCmd.interface = port_id;
			maxTxDepthCmd.interface = port_id;
			maxWeightCmd.interface = port_id;
			rateLimitCmd.interface = port_id;
			shaperCmd.interface = port_id;
			schedulerCmd.interface = port_id;
			resetCmd.interface = port_id;
			queueenableCmd.interface = port_id;
		}
		else
			goto keyword_error;
	}
	else if(strcasecmp(keywords[cpt], "expt_rate") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		memset(&exptRateCmd, 0, sizeof(exptRateCmd));

		if(strcasecmp(keywords[cpt], "eth") == 0 )
			exptRateCmd.if_type = FPP_EXPT_TYPE_ETH;
#ifdef COMCERTO_2000
		else if (strcasecmp(keywords[cpt], "wifi") == 0 )
			exptRateCmd.if_type = FPP_EXPT_TYPE_WIFI;
		else if (strcasecmp(keywords[cpt], "arp_ndp") == 0 )
			exptRateCmd.if_type = FPP_EXPT_TYPE_ARP;
		else if (strcasecmp(keywords[cpt], "pcap") == 0 )
			exptRateCmd.if_type = FPP_EXPT_TYPE_PCAP;
#endif
		else
			goto help;

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp != 0 && (tmp < 1000 || tmp > 5000000)))
		{
			cmm_print(DEBUG_CRIT, "CMD_QM_EXPT_RATE ERROR: rate must be zero (to disable) or a number between 1000 and 5000000\n");
			goto help;
		}
		if(keywords[++cpt])
			goto help;
		//exptRateCmd.pkts_per_msec = tmp / 1000;
		exptRateCmd.pkts_per_sec = tmp;
		// Send CMD_QM_EXPT_RATE command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_EXPT_RATE, &exptRateCmd, sizeof(exptRateCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_EXPT_RATE", ERRMSG_SOURCE_FPP,rxbuf.rcvBuffer);
		}
		return 0;
	}
	else if(strcasecmp(keywords[cpt], "dscp_queue") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if(strcasecmp(keywords[cpt], "queue") == 0)
		{
			if(!keywords[++cpt])
				goto help;

			 /*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
			{
				cmm_print(DEBUG_STDERR, "dscp_queue ERROR: selected queue must be a number between 0 and %d\n", (FPP_NUM_QUEUES-1));
				goto help;
			}
			dscpCmd.queue = tmp;
			cmm_print(DEBUG_INFO, "dscp_queue - queue %d selected\n", dscpCmd.queue);

			if(!keywords[++cpt])
				goto help;
		}
		else
		   goto keyword_error;

		if(strcasecmp(keywords[cpt], "dscp") == 0)
		{
			/* get list of dscp values assigned to the selected queue */
			if(!keywords[++cpt])
				goto help;
			num_dscp = 0;
			first_dscp = 0;
			cmm_print(DEBUG_INFO, "dscp_queue - parsing dscp list for queue %d\n", dscpCmd.queue);
			while(keywords[cpt] && (num_dscp < FPP_NUM_DSCP))
			{
				cmm_print(DEBUG_INFO, "dscp_queue - processing arg '%s' \n", keywords[cpt]);
				if(strcasecmp(keywords[cpt], "-") == 0)
				{
					dscp_range = 1;
					cmm_print(DEBUG_INFO, "dscp_queue - dscp range detected\n");
				}
				else
				{
					endptr = NULL;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp > FPP_MAX_DSCP))
					{
						cmm_print(DEBUG_STDERR, "dscp_queue ERROR: DSCP value out of range\n");
						goto help;
					}
					else
					{
						cmm_print(DEBUG_INFO, "dscp_queue - one more dscp added\n");
						/* save low-end dscp value i.e. the first value specified*/
						if(num_dscp == 0)
							first_dscp = tmp;
						last_dscp = tmp; /* save high end dscp i.e. the last one specified*/
						dscp_value[num_dscp++] = tmp;
					}
				}
				cpt++;
			}

			/* no dscp specified means all dscp */
			if(num_dscp == 0) 
			{
				for(i = 0; i < FPP_NUM_DSCP; i++)
					dscpCmd.dscp[i] = i;
				dscpCmd.num_dscp = FPP_NUM_DSCP;
				cmm_print(DEBUG_INFO, "dscp_queue - all dscp assigned\n");
			}
			else if (dscp_range)
			{
				if(last_dscp <= first_dscp)
				{
					cmm_print(DEBUG_STDERR, "dscp_queue: wrong DSCP range\n");
					goto help;
				}
				for(i = first_dscp; i <= last_dscp; i++)
					dscpCmd.dscp[i - first_dscp] = i;
				dscpCmd.num_dscp = (last_dscp - first_dscp) + 1; 
				cmm_print(DEBUG_INFO, "dscp_queue - dscp range %d to %d\n", first_dscp, last_dscp);
			}
			else
			{
				cmm_print(DEBUG_INFO, "dscp_queue - dscp non-ordered list\n");
				dscpCmd.num_dscp = num_dscp;
				for(i = 0; i < dscpCmd.num_dscp; i++)
					dscpCmd.dscp[i] = dscp_value[i];
			}
			cmm_print(DEBUG_INFO, "dscp_queue - %d dscp assigned ->\n", dscpCmd.num_dscp);
			for(i = 0; i < dscpCmd.num_dscp; i++)
				cmm_print(DEBUG_INFO, "%d ", dscpCmd.dscp[i]);
			cmm_print(DEBUG_INFO, "\n");

			if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_DSCP_MAP, &dscpCmd, sizeof(fpp_qm_dscp_queue_mod_t), rxbuf.rcvBuffer) == 2)
			{
				if (rxbuf.result != 0)
					showErrorMsg("CMD_QM_DSCP_MAP", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
				return (rxbuf.result);
			}
		}
		else {
			cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);
			goto help;
		}

		return 0;
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;
	
	if(strcasecmp(keywords[cpt], "qos") == 0)
	{		
		if(!keywords[++cpt])
			goto help;
		
		while (keywords[cpt] != NULL)
		{
			if(strcasecmp(keywords[cpt], "on") == 0)
			{
				cmdToSend |= CMD_BIT(FPP_CMD_QM_QOSENABLE);
				enableCmd.enable = 1;
			}
			else if(strcasecmp(keywords[cpt], "off") == 0)
			{
				cmdToSend |= CMD_BIT(FPP_CMD_QM_QOSENABLE);
				enableCmd.enable = 0;
			}
			else if(strcasecmp(keywords[cpt], "scheduler") == 0)
			{
				if(!keywords[++cpt])
					goto help;


				cmdToSend |= CMD_BIT(FPP_CMD_QM_QOSALG);

				if(strcasecmp(keywords[cpt], "pq") == 0)
				{
					algCmd.scheduler = 0;
				}
				else if (strcasecmp(keywords[cpt], "cbwfq") == 0)
				{
					algCmd.scheduler = 1;
				}
				else if (strcasecmp(keywords[cpt], "dwrr") == 0)
				{
					algCmd.scheduler = 2;
				}
				else
					goto keyword_error;
			}
			else if(strcasecmp(keywords[cpt], "nhigh_queue") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_CRIT, "qos ERROR: nhigh_queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
					goto help;
				}

				nHighCmd.number_high_queues = tmp;
				
				cmdToSend |= CMD_BIT(FPP_CMD_QM_NHIGH);
			}
			else if(strcasecmp(keywords[cpt], "max_txdepth") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || tmp < 1 || (tmp > USHRT_MAX))
				{
					cmm_print(DEBUG_CRIT, "qos ERROR: max_txdepth must be a number between 1 and %d\n", USHRT_MAX);
					goto help;
				}
		
				maxTxDepthCmd.max_bytes = tmp;

				cmdToSend |= CMD_BIT(FPP_CMD_QM_MAX_TXDEPTH);
			}
			else if(strcasecmp(keywords[cpt], "qweight") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_STDERR, "qos ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1) );
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;
				
				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || tmp1 < 1 || (tmp1 > USHRT_MAX))
				{
					cmm_print(DEBUG_STDERR, "qos ERROR: weight must be a number between 1 and %d\n", USHRT_MAX);
					goto help;
				}
				
				maxWeightCmd.qxweight[tmp] = tmp1;
				cmdToSend |= CMD_BIT(FPP_CMD_QM_MAX_WEIGHT);
			}

			else if(strcasecmp(keywords[cpt], "qdepth") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_STDERR, "qos ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;
				
				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || tmp1 < 1 || (tmp1 > USHRT_MAX))
				{
					cmm_print(DEBUG_STDERR, "qos ERROR: depth must be a number between 1 and %d\n", USHRT_MAX);
					goto help;
				}
				
				maxQdepthCmd.qtxdepth[tmp] = tmp1;
				cmdToSend |= CMD_BIT(FPP_CMD_QM_MAX_QDEPTH);
			}
			else
				goto keyword_error;

			cpt++;
		}
	}
	else if(strcasecmp(keywords[cpt], "rate_limiting") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		
		if(strcasecmp(keywords[cpt], "on") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_QM_RATE_LIMIT);
			rateLimitCmd.enable = 1;
	
			cpt++;
			while (keywords[cpt] != NULL)
			{
				if(strcasecmp(keywords[cpt], "queue") == 0)
				{
					if(!keywords[++cpt])
						goto help;

					/*Get an integer from the string*/
					endptr = NULL;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
					{
						cmm_print(DEBUG_CRIT, "rate_limiting ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
						goto help;
					}

					rateLimitCmd.queues |= (1 << tmp);
				}
				else if(strcasecmp(keywords[cpt], "rate") == 0)
				{
					if(!keywords[++cpt])
						goto help;

					/*Get an integer from the string*/
					endptr = NULL;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
					{
						cmm_print(DEBUG_CRIT, "rate_limiting ERROR: rate must be a number between 8 and %d (Kbps)\n", (unsigned int)UINT_MAX);
						goto help;
					}

					rateLimitCmd.rate = tmp;
				}
				else if(strcasecmp(keywords[cpt], "bucket_size") == 0)
				{
					if(!keywords[++cpt])
						goto help;

					/*Get an integer from the string*/
					endptr = NULL;
					tmp = strtoul(keywords[cpt], &endptr, 0);
					if ((keywords[cpt] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
					{
						cmm_print(DEBUG_CRIT, "rate_limiting ERROR: bucket_size must be a number between 8 and %d\n", (unsigned int)UINT_MAX);
						goto help;
					}

					rateLimitCmd.bucket_size = tmp;
				}
				else
					goto keyword_error;
			
				cpt++;
			}

			/*Dependencies check*/
			if (rateLimitCmd.queues == 0)
			{
				cmm_print(DEBUG_CRIT, "Rate Limiting ERROR: At least one queue must be specified\n");
				goto help;
			}
			
			if(rateLimitCmd.rate == 0)
			{
				cmm_print(DEBUG_CRIT, "Rate Limiting ERROR: The bandwidth have to be specified\n");
				goto help;
			}
		}
		else if(strcasecmp(keywords[cpt], "off") == 0)
		{
			cmdToSend |= CMD_BIT(FPP_CMD_QM_RATE_LIMIT);
			rateLimitCmd.enable = 0;
		}
		else
			goto keyword_error;
		
	}
	else if(strcasecmp(keywords[cpt], "shaper") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
#ifdef COMCERTO_2000
		if (strcasecmp(keywords[cpt], "port") == 0)
		{
			tmp = FPP_PORT_SHAPER_NUM;
		}
		else
#endif
		{
			endptr = NULL;
			tmp = strtoul(keywords[cpt], &endptr, 0);
			if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_SHAPERS))
			{
				cmm_print(DEBUG_CRIT, "shaper ERROR: shaper number must be between 0 and %d\n", FPP_NUM_SHAPERS);
				goto help;
			}
		}

		shaperCmd.shaper = tmp;
		
		if(!keywords[++cpt])
			goto help;

		cmdToSend |= CMD_BIT(FPP_CMD_QM_SHAPER_CFG);

		while (keywords[cpt] != NULL)
		{
			if(strcasecmp(keywords[cpt], "on") == 0)
			{
				shaperCmd.enable = 1;
			}
			else if(strcasecmp(keywords[cpt], "off") == 0)
			{
				shaperCmd.enable = 2;
			}
			else if(strcasecmp(keywords[cpt], "queue") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_CRIT, "shaper ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
					goto help;
				}

				shaperCmd.queues |= (1 << tmp);
			}
			else if(strcasecmp(keywords[cpt], "ifg") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp > 255))
				{
					cmm_print(DEBUG_CRIT, "shaper ERROR: ifg must be a number between 0 and 255\n");
					goto help;
				}

				shaperCmd.ifg = tmp;
				shaperCmd.ifg_change_flag = 1;
			}
			else if(strcasecmp(keywords[cpt], "rate") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/* Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
                                if ((keywords[cpt] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
                                {
                                        cmm_print(DEBUG_CRIT, "shaper ERROR: rate must be a number between 8 and %d (Kbps)\n", (unsigned int)UINT_MAX);
                                        goto help;
                                }


				shaperCmd.rate = tmp;
			}
			else if(strcasecmp(keywords[cpt], "bucket_size") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp < 8) || (tmp > UINT_MAX))
				{
					cmm_print(DEBUG_CRIT, "shaper ERROR: bucket_size must be a number between 8 and %d\n", (unsigned int)UINT_MAX);
					goto help;
				}

				shaperCmd.bucket_size = tmp;
			}
			else
				goto keyword_error;
		
			cpt++;
		}
	}
	else if(strcasecmp(keywords[cpt], "scheduler") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_SCHEDULERS))
		{
			cmm_print(DEBUG_CRIT, "scheduler ERROR: scheduler number must be between 0 and 3\n");
			goto help;
		}

		schedulerCmd.scheduler = tmp;
		
		if(!keywords[++cpt])
			goto help;

		cmdToSend |= CMD_BIT(FPP_CMD_QM_SCHED_CFG);
	
		while (keywords[cpt] != NULL)
		{
			if(strcasecmp(keywords[cpt], "queue") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_CRIT, "scheduler ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
					goto help;
				}

				schedulerCmd.queues |= (1 << tmp);
			}
			else if(strcasecmp(keywords[cpt], "algorithm") == 0)
			{
				if(!keywords[++cpt])
					goto help;

				if(strcasecmp(keywords[cpt], "pq") == 0)
				{
					schedulerCmd.algo = 0;
					schedulerCmd.algo_change_flag = 1;
				}
				else if (strcasecmp(keywords[cpt], "cbwfq") == 0)
				{
					schedulerCmd.algo = 1;
					schedulerCmd.algo_change_flag = 1;
				}
				else if (strcasecmp(keywords[cpt], "dwrr") == 0)
				{
					schedulerCmd.algo = 2;
					schedulerCmd.algo_change_flag = 1;
				}
				else if (strcasecmp(keywords[cpt], "rr") == 0)
				{
					schedulerCmd.algo = 3;
					schedulerCmd.algo_change_flag = 1;
				}
				else
					goto keyword_error;
			}			
			else
				goto keyword_error;
		
			cpt++;
		}

	}

	else if(strcasecmp(keywords[cpt], "queue") == 0)
	{
		unsigned int qmask=0; /* Bit mask of single or set of queues that are programmed */

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
		{
			cmm_print(DEBUG_CRIT, "queue ERROR: queue must be a number between 0 and %d\n", (FPP_NUM_QUEUES-1));
			goto help;
		}
		qmask |= (1<<tmp);

		if(!keywords[++cpt])
			goto help;

		while (keywords[cpt] != NULL)
		{
			if(strcasecmp(keywords[cpt], "queue") == 0)
			{
			       if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_QUEUES))
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: queue must be a number between 0 and %d \n", (FPP_NUM_QUEUES -1));
					goto help;
				}

				qmask |= (1<<tmp);
			}
			else if(strcasecmp(keywords[cpt], "qos") == 0)
			{
				if (qmask ==0)
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: One or more queues need to be specified \n");
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;
		
				
				if(strcasecmp(keywords[cpt], "on") == 0)
					queueenableCmd.enable_flag = 1;
				else if(strcasecmp(keywords[cpt], "off") == 0)
					queueenableCmd.enable_flag = 0;
				
				queueenableCmd.queue_qosenable_mask = qmask;
				cmdToSend |= CMD_BIT(FPP_CMD_QM_QUEUE_QOSENABLE);
				
			}
			else if(strcasecmp(keywords[cpt], "shaper") == 0)
			{
				if (qmask ==0)
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: One or more queues need to be specified \n");
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_SHAPERS))
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: shaper number must be between 0 and 4\n");
					goto help;
				}

				shaperCmd.shaper = tmp;
				shaperCmd.queues = qmask;
				cmdToSend |= CMD_BIT(FPP_CMD_QM_SHAPER_CFG);
			}
			else if(strcasecmp(keywords[cpt], "scheduler") == 0)
			{
				if (qmask ==0)
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: One or more queues need to be specified \n");
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || (tmp >= FPP_NUM_SCHEDULERS))
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: scheduler number must be between 0 and 3\n");
					goto help;
				}

				schedulerCmd.scheduler = tmp;
				schedulerCmd.queues = qmask;
				cmdToSend |= CMD_BIT(FPP_CMD_QM_SCHED_CFG);
			}
			else if(strcasecmp(keywords[cpt], "qweight") == 0)
			{
				if (qmask ==0)
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: One or more queues need to be specified \n");
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || tmp1 < 1 || (tmp1 > USHRT_MAX))
				{
					cmm_print(DEBUG_STDERR, "queue ERROR: weight must be a number between 1 and %d\n", USHRT_MAX);
					goto help;
				}

				for(i=0; i < FPP_NUM_QUEUES; i++) {
					if(qmask & (1 << i))
						maxWeightCmd.qxweight[i] = tmp1;
				}
				cmdToSend |= CMD_BIT(FPP_CMD_QM_MAX_WEIGHT);
			}

			else if(strcasecmp(keywords[cpt], "qdepth") == 0)
			{
				if (qmask ==0)
				{
					cmm_print(DEBUG_CRIT, "queue ERROR: One or more queues need to be specified \n");
					goto help;
				}
				
				if(!keywords[++cpt])
					goto help;

				/*Get an integer from the string*/
				endptr = NULL;
				tmp1 = strtoul(keywords[cpt], &endptr, 0);
				if ((keywords[cpt] == endptr) || tmp1 < 1 || (tmp1 > USHRT_MAX))
				{
					cmm_print(DEBUG_STDERR, "queue ERROR: depth must be a number between 1 and %d\n", USHRT_MAX);
					goto help;
				}

				for(i=0; i < FPP_NUM_QUEUES; i++) {
					if(qmask & (1 << i))
						maxQdepthCmd.qtxdepth[i] = tmp1;
				}
				cmdToSend |= CMD_BIT(FPP_CMD_QM_MAX_QDEPTH);
			}
			else
				goto keyword_error;
		
			cpt++;
		}

	}

	else if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		if(keywords[++cpt])
			goto help;

		cmdToSend |= CMD_BIT(FPP_CMD_QM_RESET);	
	}
	else
		goto keyword_error;

	/*
	 * Parsing have been performed
	 * Now send the right commands
	 */

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_RESET))
	{
		// Send CMD_QM_RATE_LIMIT command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_RESET, &resetCmd, sizeof(resetCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_QOSENABLE))
	{
		// Send CMD_QM_QOSENABLE command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QOSENABLE, & enableCmd, sizeof(enableCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_QOSENABLE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_QUEUE_QOSENABLE))
	{
		// Send FPP_CMD_QM_QUEUE_QOSENABLE command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QUEUE_QOSENABLE, &queueenableCmd, sizeof(queueenableCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_QUEUE_QOSENABLE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	
	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_QOSALG))
	{
		// Send CMD_QM_QOSALG command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_QOSALG, & algCmd, sizeof(algCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_QOSALG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_NHIGH))
	{
		// Send CMD_QM_NHIGH command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_NHIGH, & nHighCmd, sizeof(nHighCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_NHIGH", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_MAX_TXDEPTH))
	{
		// Send CMD_QM_MAX_TXDEPTH command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_MAX_TXDEPTH, &maxTxDepthCmd, sizeof(maxTxDepthCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_MAX_TXDEPTH", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_MAX_QDEPTH))
	{
		// Send CMD_QM_MAX_QDEPTH command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_MAX_QDEPTH, & maxQdepthCmd , sizeof(maxQdepthCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_MAX_QDEPTH", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_MAX_WEIGHT))
	{
		// Send CMD_QM_MAX_WEIGHT command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_MAX_WEIGHT, &maxWeightCmd , sizeof(maxWeightCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_MAX_WEIGHT", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_RATE_LIMIT))
	{
		// Send CMD_QM_RATE_LIMIT command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_RATE_LIMIT, &rateLimitCmd, sizeof(rateLimitCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_RATE_LIMIT", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_SHAPER_CFG))
	{
		// Send CMD_QM_RATE_LIMIT command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_SHAPER_CFG, &shaperCmd, sizeof(shaperCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_SHAPER_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	if(TEST_CMD_BIT(cmdToSend, FPP_CMD_QM_SCHED_CFG))
	{
		// Send CMD_QM_RATE_LIMIT command
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_QM_SCHED_CFG, &schedulerCmd, sizeof(schedulerCmd), &rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_QM_SCHED_CFG", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}

	return 0;

keyword_error:
	cmm_print(DEBUG_CRIT, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmQmSetPrintHelp();
	return -1;
}
#endif


void cmmQmResetQ2Prio(fpp_qm_reset_cmd_t *cmdp, int cmdlen)
{
	char fname[128];
	FILE *fp;

	if (cmdlen != sizeof(fpp_qm_reset_cmd_t))
	{
		cmm_print(DEBUG_ERROR, "%s: Wrong length for cmd, expected %zu, got %d\n", __func__,
						sizeof(fpp_qm_scheduler_cfg_t), cmdlen);
		return;
	}

	/* cmdp->interface already contains the interface name as a string */
	snprintf(fname, sizeof(fname), "/sys/class/net/%s/q2prio", (const char *)cmdp->interface);
	fp = fopen(fname, "w");
	if (!fp)
	{
		cmm_print(DEBUG_WARNING, "%s: Cannot open %s\n", __func__, fname);
		return;
	}
	fprintf(fp, "reset\n");
	fclose(fp);
}


void cmmQmUpdateQ2Prio(fpp_qm_scheduler_cfg_t *cmdp, int cmdlen)
{
	u_int16_t interface;
        u_int16_t scheduler;
        u_int32_t queues;
	char fname[128], ifname[IFNAMSIZ];
	FILE *fp;

	if (cmdlen != sizeof(fpp_qm_scheduler_cfg_t))
	{
		cmm_print(DEBUG_ERROR, "%s: Wrong length for cmd, expected %zu, got %d\n", __func__,
						sizeof(fpp_qm_scheduler_cfg_t), cmdlen);
		return;
	}

	interface = cmdp->interface;
	scheduler = cmdp->scheduler;
	queues = cmdp->queues;

	snprintf(fname, 128, "/sys/class/net/%s/q2prio", get_port_name(interface, ifname, IFNAMSIZ));
	fp = fopen(fname, "w");
	if (!fp)
	{
		cmm_print(DEBUG_WARNING, "%s: Cannot open %s\n", __func__, fname);
		return;
	}
	fprintf(fp, "%d 0x%x\n", scheduler, queues);
	fclose(fp);
}

