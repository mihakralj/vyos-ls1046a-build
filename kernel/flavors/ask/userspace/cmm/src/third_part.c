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
#include "cmm.h"

int * cmm_third_part_init(void)
{
#ifndef CMM_THIRD_PART
    return NULL;
#endif
  
    cmm_print(DEBUG_INFO, "%s\n", __func__);

    /* perform all required initialization */

    /* define and return private data */

    return NULL;
}

#ifdef CMM_THIRD_PART
static void cmm_third_part_cb (void *priv, unsigned short type, void *data, unsigned short *resp_length, unsigned short *resp_payload)
{
    struct cmm_ct_to_queue_t *msg;
    char saddr_buf[INET6_ADDRSTRLEN];
    char daddr_buf[INET6_ADDRSTRLEN];
    char ifname[IFNAMSIZ];

    cmm_print(DEBUG_INFO, "%s\n", __func__);
        
    /* For now the only supported event type has mandatory data. Should be moved to switch-case below if new event are added */
    if(data == NULL)
        return;
    
    /* Handling of the private data should be done here if needed */
   

    /* Event type handling */
    switch(type)
    {
	case CMM_CB_CT_TO_QUEUE:
            /* Retrieve connection information */
            msg = (struct cmm_ct_to_queue_t *)data;

            cmm_print(DEBUG_INFO, "CMM_CB_CT_TO_QUEUE\nstate %d ipfamily %d proto %d mark 0x%" PRIx64 "\n", msg->state, msg->ip_family, msg->proto, msg->qosmark);
                
            if(msg->state & ORIGINATOR) 
                cmm_print(DEBUG_INFO, "originate output_itf %s saddr %s daddr %s sport %d dport %d gw mac %02x:%02x:%02x:%02x:%02x:%02x\n", if_indextoname(msg->orig_output, ifname), inet_ntop(msg->ip_family, msg->orig_saddr, saddr_buf, INET6_ADDRSTRLEN), inet_ntop(msg->ip_family, msg->orig_daddr, daddr_buf, INET6_ADDRSTRLEN), ntohs(msg->orig_sport), ntohs(msg->orig_dport), msg->orig_gw_mac[0],msg->orig_gw_mac[1],msg->orig_gw_mac[2],msg->orig_gw_mac[3],msg->orig_gw_mac[4],msg->orig_gw_mac[5]);
                
            if(msg->state & REPLIER)
                cmm_print(DEBUG_INFO, "replier output_itf %s saddr %s daddr %s sport %d dport %d gw mac %02x:%02x:%02x:%02x:%02x:%02x\n", if_indextoname(msg->repl_output, ifname), inet_ntop(msg->ip_family, msg->repl_saddr, saddr_buf, INET6_ADDRSTRLEN), inet_ntop(msg->ip_family, msg->repl_daddr, daddr_buf, INET6_ADDRSTRLEN), ntohs(msg->repl_sport), ntohs(msg->repl_dport), msg->repl_gw_mac[0],msg->repl_gw_mac[1],msg->repl_gw_mac[2],msg->repl_gw_mac[3],msg->repl_gw_mac[4],msg->repl_gw_mac[5]);        
                
            /* Define new mark */
            
            /* Following lines are for test purpose only (mark change for a given port 1024) */
        #if 0
            if(ntohs(msg->orig_sport) == 1024)
                msg->qosmark = 0x1;
        #endif
            break;

	default:
            break;
    }
}
#endif /* CMM_THIRD_PART */

void cmm_third_part_exit(void *priv_data)
{
#ifndef CMM_THIRD_PART
    return;
#endif
  
    cmm_print(DEBUG_INFO, "%s\n", __func__);

    /* free allocated resources */
}

#ifdef CMM_THIRD_PART
static void cmm_third_part_dump_ct(struct cmm_ct_to_queue_t * msg)
{
    char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN];
    
    cmm_print(DEBUG_INFO, "orig_saddr %s orig_daddr %s orig_sport %d orig_dport %d\n", 
              inet_ntop(msg->ip_family, msg->orig_saddr, saddr_buf, INET6_ADDRSTRLEN),
              inet_ntop(msg->ip_family, msg->orig_daddr, daddr_buf, INET6_ADDRSTRLEN),
              ntohs(msg->orig_sport),
              ntohs(msg->orig_dport));                        
      
    cmm_print(DEBUG_INFO, "repl_saddr %s repl_daddr %s repl_sport %d repl_dport %d\n", 
              inet_ntop(msg->ip_family, msg->repl_saddr, saddr_buf, INET6_ADDRSTRLEN),
              inet_ntop(msg->ip_family, msg->repl_daddr, daddr_buf, INET6_ADDRSTRLEN),
              ntohs(msg->repl_sport),
              ntohs(msg->repl_dport));       

    cmm_print(DEBUG_INFO, "mark 0x%" PRIx64 "\n", msg->qosmark);
}
#endif /* CMM_THIRD_PART */


void cmm_third_part_update(struct ctTable *ctEntry, int dir)
{
#ifndef CMM_THIRD_PART
    return;
#else
  
    /* Fill a buffer with the information required for CMM_CB_CT_TO_QUEUE event */
    struct cmm_ct_to_queue_t msg;
    const unsigned int *dAddrOrig, *dAddrRepl, *sAddrOrig, *sAddrRepl;
    unsigned short dPortOrig, dPortRepl, sPortOrig, sPortRepl;
    unsigned char proto;
    struct nf_conntrack *ct = ctEntry->ct;

    proto = nfct_get_attr_u8(ct, ATTR_ORIG_L4PROTO);

    if (ctEntry->family == AF_INET)
    {
        sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV4_SRC);
        sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV4_SRC);
        dAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV4_DST);
        dAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV4_DST);
    }
    else
    {
        sAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
        sAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);
        dAddrOrig = nfct_get_attr(ct, ATTR_ORIG_IPV6_DST);
        dAddrRepl = nfct_get_attr(ct, ATTR_REPL_IPV6_DST);
    }

    sPortOrig = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_SRC);
    sPortRepl = nfct_get_attr_u16(ct, ATTR_REPL_PORT_SRC);
    dPortOrig = nfct_get_attr_u16(ct, ATTR_ORIG_PORT_DST);
    dPortRepl = nfct_get_attr_u16(ct, ATTR_REPL_PORT_DST);

    memset(&msg, 0, sizeof(struct cmm_ct_to_queue_t ));
    
    msg.ip_family = ctEntry->family;
    msg.proto = proto;
    msg.qosmark = cmmQosmarkGet(ct);
    
    if(dir & ORIGINATOR)
    {
        if (ctEntry->family == AF_INET)
        {
            msg.orig_saddr[0] = sAddrOrig[0]; 
            msg.orig_daddr[0] = dAddrOrig[0]; 
        } 
        else 
        {
            memcpy(msg.orig_saddr, sAddrOrig, 16); 
            memcpy(msg.orig_daddr, dAddrOrig, 16);
        }

        msg.orig_sport = sPortOrig; 
        msg.orig_dport = dPortOrig;
	 if(ctEntry->orig.route->neighEntry)
        	memcpy(msg.orig_gw_mac, ctEntry->orig.route->neighEntry->macAddr, 6); 
        msg.orig_output = ctEntry->orig.route->oifindex;
    }

    if(dir & REPLIER)
    {
        if (ctEntry->family == AF_INET) 
        {
            msg.repl_saddr[0] = sAddrRepl[0]; 
            msg.repl_daddr[0] = dAddrRepl[0]; 
        } 
        else 
        {
            memcpy(msg.repl_saddr, sAddrRepl, 16); 
            memcpy(msg.repl_daddr, dAddrRepl, 16);
        }

        msg.repl_sport = sPortRepl; 
        msg.repl_dport = dPortRepl;
	 if(ctEntry->rep.route->neighEntry)
        	memcpy(msg.repl_gw_mac, ctEntry->rep.route->neighEntry->macAddr, 6); 
        msg.repl_output = ctEntry->rep.route->oifindex;
    }

    /* Call 3rd Party callback */
    if((dir & (ORIGINATOR | REPLIER)) && (ctEntry->flags & FPP_NEEDS_UPDATE))
    {
        cmm_print(DEBUG_INFO, "%s: Old connection entry\n",  __func__);
        cmm_third_part_dump_ct(&msg);

        msg.state = dir;
        cmm_third_part_cb(globalConf.third_part_data, CMM_CB_CT_TO_QUEUE, (void*)&msg, NULL, NULL);

        cmm_print(DEBUG_INFO, "%s: New connection entry\n",  __func__);
        cmm_third_part_dump_ct(&msg);

        /* Retrieve modified parameter and apply to the connection */
        cmmQosmarkSet(ct, msg.qosmark);
    }
#endif
}


