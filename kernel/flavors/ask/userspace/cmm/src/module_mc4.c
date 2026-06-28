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
#include "cmmd.h"

#define MC4_MAX_LISTENERS_IN_QUERY    5
extern int cmmMcParseListener(char ** keywords, int *cpt, cmmd_mc6_listener_t * listener, u_int8_t * mode, u_int16_t action );
extern pthread_mutex_t mc_lock;

int cmmMc4Show(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int count = 0, ii, jj;
	struct list_head *entry;
	struct mcast_entry *mc;
        char buf1[INET_ADDRSTRLEN];
        char buf2[INET_ADDRSTRLEN];

	__pthread_mutex_lock(&mc_lock);
	
	for( ii = 0; ii < MC_NUM_HASH_ENTRIES; ii++ )
	{
		for (entry = list_first(&mc_table[ii]); entry != &mc_table[ii]; entry = list_next(entry))
		{
			mc = container_of(entry, struct mcast_entry, list);
			
			if( mc->family != AF_INET )
				continue;
			
                	cmm_print(DEBUG_STDOUT, "%04d: Src addr: %s  src_mask_len: %x   Dst addr: %s  Queue : %d\n",
			  count,
			  inet_ntop(AF_INET, &mc->src_addr, buf1, INET_ADDRSTRLEN),
			  mc->src_mask_len,
			  inet_ntop(AF_INET, &mc->dst_addr, buf2, INET_ADDRSTRLEN),
                          mc->queue); 
			for( jj = 0; jj < mc->num_output; jj++) 	
			{
				cmm_print(DEBUG_STDOUT," PROGRAMMED : %02d TIMER : 0x%x   SHAPER : 0x%x  Interface %s\n",mc->l_program[jj],
					mc->listener[jj].timer,mc->listener[jj].shaper_mask , mc->listener[jj].output_device_str);
				if(mc->listener[jj].q_bit)
					cmm_print(DEBUG_STDOUT," QUEUE : 0x%x  \n",mc->listener[jj].queue );
				if(mc->listener[jj].uc_bit)
					cmm_print(DEBUG_STDOUT,"UC MAC is %02x:%02x:%02x:%02x:%02x:%02x  ",mc->listener[jj].uc_mac[0],mc->listener[jj].uc_mac[1],mc->listener[jj].uc_mac[2],mc->listener[jj].uc_mac[3],mc->listener[jj].uc_mac[4],mc->listener[jj].uc_mac[5]);
			}	

			
			count++;	
				
		}
	}  
        cmm_print(DEBUG_STDOUT, "Total Multicast v4 Entries: %d\n", count);
	__pthread_mutex_unlock(&mc_lock);
	
	return 0;
}

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmMc4ShowPrintHelp()
{
	cmm_print(DEBUG_STDOUT, "show mc4 not yet supported\n");
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmMc4ShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	
//help:
	cmmMc4ShowPrintHelp();
	return -1;
}

/************************************************************
 *
 *
 *
 *************************************************************/
int cmmMc4QueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        char buf1[INET_ADDRSTRLEN];
        char buf2[INET_ADDRSTRLEN];
	char output_buf[256];
        short rc;
        int count = 0,len=0;

	memset(rxbuf.rcvBuffer, 0, sizeof(rxbuf.rcvBuffer));
        cmmd_mc4_entry_t *mc4_entry = (cmmd_mc4_entry_t *) rxbuf.rcvBuffer;
	cmmd_mc4_listener_t* mc4_listener = (cmmd_mc4_listener_t *)(rxbuf.rcvBuffer + sizeof (cmmd_mc4_entry_t)); 

        mc4_entry->action = CMMD_ACTION_QUERY;
        rcvBytes = cmmSendToDaemon(daemon_handle,CMMD_CMD_MC4_MULTICAST , 
                  mc4_entry, sizeof(cmmd_mc4_entry_t)+ MC4_MAX_LISTENERS_IN_QUERY * sizeof(cmmd_mc4_listener_t) , rxbuf.rcvBuffer);

        if (rcvBytes < sizeof(cmmd_mc4_entry_t) + sizeof (cmmd_mc4_listener_t) ) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == CMMD_ERR_UNKNOWN_ACTION) {
                   cmm_print(DEBUG_STDERR, "ERROR: FPP CMD_MC4_MULTICAST does not support ACTION_QUERY\n");
                } else if (rc == CMMD_ERR_MC_ENTRY_NOT_FOUND) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP Multicast IPV4 table empty\n");
                } else {
                    cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }
            cmm_print(DEBUG_STDOUT, "IPv4 Multicast Entries: \n");
            do {
        	int i, listener_count = 0;
		if((ntohl(mc4_entry->dst_addr) & 0xE0000000) == 0xE0000000)
		{
#if defined(LS1043)
                	cmm_print(DEBUG_STDOUT, "%04d: Ingress Interface: %s Src addr: %s src_mask_len: %x   Dst addr: %s	Queue: 0x%x  \n\n", count, 
                          mc4_entry->input_device_str,
			  inet_ntop(AF_INET, &mc4_entry->src_addr, buf1, INET_ADDRSTRLEN),
			  mc4_entry->src_mask_len,
			  inet_ntop(AF_INET, &mc4_entry->dst_addr, buf2, INET_ADDRSTRLEN),
			  mc4_entry->queue);
			count++;
#else
                	cmm_print(DEBUG_STDOUT, "%04d: Src addr: %s  src_mask_len: %x   Dst addr: %s	Queue: 0x%x  \n\n",
			  count,
			  inet_ntop(AF_INET, &mc4_entry->src_addr, buf1, INET_ADDRSTRLEN),
			  mc4_entry->src_mask_len,
			  inet_ntop(AF_INET, &mc4_entry->dst_addr, buf2, INET_ADDRSTRLEN),
			  mc4_entry->queue);
			count++;
#endif
		}
		listener_count = mc4_entry->num_output;
		if(listener_count > MC4_MAX_LISTENERS_IN_QUERY)
                {
                        cmm_print(DEBUG_STDOUT,"output interfaces are more than %d",MC4_MAX_LISTENERS_IN_QUERY);
                        listener_count = MC4_MAX_LISTENERS_IN_QUERY;
                }

                for ( i = 0; i < listener_count; i++)
		{
	            len += sprintf(output_buf + len, "output interface:  ");
                    len += sprintf(output_buf + len, "%s  ", mc4_listener[i].output_device_str);
		    cmm_print (DEBUG_STDOUT,output_buf);
                    if(mc4_listener[i].uc_bit)
                           cmm_print(DEBUG_STDOUT,"UC MAC is %02x:%02x:%02x:%02x:%02x:%02x  ",mc4_listener[i].uc_mac[0],mc4_listener[i].uc_mac[1],mc4_listener[i].uc_mac[2],mc4_listener[i].uc_mac[3],mc4_listener[i].uc_mac[4],mc4_listener[i].uc_mac[5]);
                   cmm_print(DEBUG_STDOUT," TIMER : 0x%x   SHAPER : 0x%x   QUEUE : 0x%x\n",mc4_listener[i].timer,mc4_listener[i].shaper_mask, mc4_listener[i].queue);
                   len = 0;
		}
                mc4_entry->action = CMMD_ACTION_QUERY_CONT;
                rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_MULTICAST, mc4_entry, 
                                          sizeof(cmmd_mc4_entry_t)+ 4* sizeof(cmmd_mc4_listener_t), rxbuf.rcvBuffer);
            } while (rcvBytes >= sizeof(cmmd_mc4_entry_t)+ sizeof(cmmd_mc4_listener_t) + sizeof(unsigned short));
            cmm_print(DEBUG_STDOUT, "Total Multicast Entries: %d\n", count);
        return CLI_OK;

}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmMc4SetPrintHelp()
{
#if defined(COMCERTO_2000) || defined(LS1043)
      #if defined(LS1043)
	cmm_print(DEBUG_STDOUT, "Usage: set mc4 ingress_interface {if_name} interface {if_name} {add | del | update}\n"
      #else
	cmm_print(DEBUG_STDOUT, "Usage: set mc4 interface {if_name} {add | del | update}\n"
      #endif
									"                 group {bit_mask} {ipv4_src_addr} {ipv4_multicast_dst_addr}\n"
									"                 [timer {timer_value}]\n"
									"                 [mode {bridged | routed}]\n"
									"                 [queue {0..19}]\n"
									"                 [listener [timer {timer_value1}] [shapers {0..0xFF}][mc | uc {Mac addr1} ] [queue {0..19}] [if {if_name}]] \n"
									"                 [listener [timer {timer_value2}] [shapers {0..0xFF}][mc | uc {Mac addr2} ] [queue {0..19}] [if {if_name}]] ...\n"
									"                 [listener [timer {timer_valuen}] [shapers {0..0xFF}][mc | uc {Mac addrn} ] [queue {0..19}] [if {if_name}]] \n");
#else
	cmm_print(DEBUG_STDOUT, "Usage: set mc4 interface {if_name} {add | del | update}\n"
									"                 group {bit_mask} {ipv4_src_addr} {ipv4_multicast_dst_addr}\n"
									"                 [timer {timer_value}]\n"
									"                 [mode {bridged | routed}]\n"
									"                 [queue {0..31}]\n"
									"                 [listener [timer {timer_value1}] [shapers {0..0xFF}][mc | uc {Mac addr1} ] [queue {0..31}] [if {if_name}]] \n"
									"                 [listener [timer {timer_value2}] [shapers {0..0xFF}][mc | uc {Mac addr2} ] [queue {0..31}] [if {if_name}]] ...\n"
									"                 [listener [timer {timer_valuen}] [shapers {0..0xFF}][mc | uc {Mac addrn} ] [queue {0..31}] [if {if_name}]] \n");

#endif									
}

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmMc4SetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	
	char sndBuffer[CMM_BUF_SIZE];
	union u_rxbuf rxbuf;
	cmmd_mc4_entry_t * entryCmd = (cmmd_mc4_entry_t *) sndBuffer;
	cmmd_mc4_listener_t * listener = (cmmd_mc4_listener_t *) (sndBuffer + sizeof(cmmd_mc4_entry_t));
	char interfaceName[IFNAMSIZ] = {};
	int listenerCnt = 0;
	u_int8_t mode = 0;
	int rc = 0;

	memset(sndBuffer, 0, sizeof(sndBuffer));
	listener->timer = 0xFFFFFFFF;
	listener->q_bit = 0;
	listener->uc_bit = 0; // Multicast by default when a new entry is added
	listener->Ifbit = 0;


	if(!keywords[cpt])
		goto help;
#if defined(LS1043)
	if(strcasecmp(keywords[cpt], "ingress_interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if (strlen(keywords[cpt]) > (IFNAMSIZ-1)) {
			cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters\n", keywords[cpt], (IFNAMSIZ - 1));
			goto help;
		}
		strcpy(interfaceName, keywords[cpt]);
		strcpy(entryCmd->input_device_str, interfaceName);
	   
                if(!keywords[++cpt])
		  goto help;
	}
        else
        {
           goto keyword_error;
        }
#endif
	if(strcasecmp(keywords[cpt], "interface") == 0)
	{
		if(!keywords[++cpt])
			goto help;

		if (strlen(keywords[cpt]) > (IFNAMSIZ - 1)) {
			cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters\n", keywords[cpt], (IFNAMSIZ - 1));
			goto help;
		}
		strcpy(interfaceName, keywords[cpt]);
		strcpy(listener->output_device_str, interfaceName);
	}
	else if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		/*Reset, special case*/
		if(cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_RESET, NULL, 0, rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_MC4_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
		return (rxbuf.result);
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;
	
	if(strcasecmp(keywords[cpt], "add") == 0)
	{		
		entryCmd->action = CMMD_MC_ACTION_ADD;
	}
	else if(strcasecmp(keywords[cpt], "del") == 0)
	{
		entryCmd->action = CMMD_MC_ACTION_REMOVE;
	}
	else if(strcasecmp(keywords[cpt], "update") == 0)
	{
		entryCmd->action = CMMD_MC_ACTION_UPDATE;
	}
	else
		goto keyword_error;

	if(!keywords[++cpt])
		goto help;

	if (strcasecmp(keywords[cpt], "group") == 0)
	{
		if(!keywords[++cpt])
			goto help;
		
		/*Get an integer from the string for the MASK*/
		endptr = NULL;
		tmp = strtoul(keywords[cpt], &endptr, 0);
		
		if (tmp > 32)
			goto help;

		entryCmd->src_mask_len = tmp;

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string for the IPsrc*/
		if(inet_pton(AF_INET, keywords[cpt], &entryCmd->src_addr) != 1)
			goto help;

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string for the IPdst*/
		if(inet_pton(AF_INET, keywords[cpt], &entryCmd->dst_addr) != 1)
			goto help;
	}
	++cpt;

	while(keywords[cpt])
	{
		strcpy(((cmmd_mc4_listener_t *)listener + listenerCnt)->output_device_str,interfaceName);
		if (strncasecmp(keywords[cpt], "listener",strlen(keywords[cpt])) == 0) //May be a case of multiple listeners, parse differently
		{
			++cpt;
			if(!cmmMcParseListener(keywords, &cpt, listener + listenerCnt, &mode, entryCmd->action))
			{
				if(++listenerCnt ==  MC4_MAX_LISTENERS_IN_QUERY)
					//Send first batch to FPP and reset listener count
				{

					entryCmd->num_output = listenerCnt;
					if(cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_MULTICAST, sndBuffer, sizeof(cmmd_mc4_entry_t) + (listenerCnt * sizeof(cmmd_mc4_listener_t)), rxbuf.rcvBuffer) == 2)
					{
						if ((rc = rxbuf.result != 0))
						{
							if(rc == CMMD_ERR_NOT_CONFIGURED)
								cmm_print(DEBUG_STDERR, "%d Interface not yet up in FPP, will be configured when interface comes up\n", rc);
							else if(rc == CMMD_ERR_MC_MAX_LISTENERS)
								cmm_print(DEBUG_STDERR, "Error %d MAX listeners for the group exhausted\n", rc);
							else if(rc == CMMD_ERR_MC_INTERFACE_NOT_ALLOWED)
								cmm_print(DEBUG_STDERR, "Error %d Interface configured cannot support Unicast MAC\n", rc);
							else if(rc == CMMD_ERR_MC_ENTRY_OVERLAP)
							{
								cmm_print(DEBUG_STDERR, "Error %d Overlapping entry configured\n", rc);
								entryCmd->action = CMMD_MC_ACTION_REMOVE_LOCAL;
								cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_MULTICAST, sndBuffer, 
								sizeof(cmmd_mc4_entry_t) +  (listenerCnt * sizeof(cmmd_mc4_listener_t)), rxbuf.rcvBuffer);	
							}
							else
								showErrorMsg("CMD_MC6_MULTICAST", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
						}

					}
					listenerCnt = 0;
					memset(sndBuffer + sizeof(cmmd_mc4_entry_t), 0 , sizeof(sndBuffer) - sizeof(cmmd_mc4_entry_t));
				}
			}
			else
				goto help;
		}
		else
		{
			if(!cmmMcParseListener(keywords, &cpt, listener, &mode, entryCmd->action))
			{
				listener->q_bit = 0; // Default config listener q_bit is always zero.
				entryCmd->queue = listener->queue;
				entryCmd->mode = mode;
			}
			else
				goto help;
		}
	}
	if(!listenerCnt & !entryCmd->num_output)
		listenerCnt = 1; //fall back to legacy design, single listener is configured.

	entryCmd->num_output = listenerCnt;

	if(listenerCnt && (cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_MULTICAST, sndBuffer, sizeof(cmmd_mc4_entry_t) + (listenerCnt * sizeof(cmmd_mc4_listener_t)), rxbuf.rcvBuffer) == 2))
	{
		if ( (rc = rxbuf.result) != 0)
		{
			if(rc == CMMD_ERR_NOT_CONFIGURED)
				cmm_print(DEBUG_STDERR, "%d Interface not yet up in FPP, will be configured when interface comes up\n", rc);
			else if(rc == CMMD_ERR_MC_MAX_LISTENERS)
				cmm_print(DEBUG_STDERR, "Error %d MAX listeners for the group exhausted\n", rc);
			else if(rc == CMMD_ERR_MC_INTERFACE_NOT_ALLOWED)
				cmm_print(DEBUG_STDERR, "Error %d Interface configured cannot support Unicast MAC\n", rc);
			else if(rc == CMMD_ERR_MC_ENTRY_OVERLAP)
			{
				cmm_print(DEBUG_STDERR, "Error %d Overlapping entry configured\n", rc);
				entryCmd->action = CMMD_MC_ACTION_REMOVE_LOCAL;
				cmmSendToDaemon(daemon_handle, CMMD_CMD_MC4_MULTICAST, sndBuffer, 
				sizeof(cmmd_mc4_entry_t) +  (listenerCnt * sizeof(cmmd_mc4_listener_t)), rxbuf.rcvBuffer);	
			}
			else
				showErrorMsg("CMD_MC6_MULTICAST", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}


#if 1
		cmm_print(DEBUG_INFO, "action:%x\n",entryCmd->action);
		cmm_print(DEBUG_INFO, "src addr:%x\n",entryCmd->src_addr);
		cmm_print(DEBUG_INFO, "src addr:%x\n",entryCmd->dst_addr);
		cmm_print(DEBUG_INFO, "src masklen:%x\n",entryCmd->src_mask_len);
		cmm_print(DEBUG_INFO, "num output:%x\n",entryCmd->num_output);
		cmm_print(DEBUG_INFO, "timer:%x\n",listener->timer);
		cmm_print(DEBUG_INFO, "outputstring:%s\n",listener->output_device_str);
		cmm_print(DEBUG_INFO, "queue:%d\n",entryCmd->queue);
#if defined(LS1043)
		cmm_print(DEBUG_INFO, "ingress interface:%s\n",entryCmd->input_device_str);
#endif
#endif

	return rc;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmMc4SetPrintHelp();
	return -1;
}


/************************************************************
 * cmmMc4ProcessClientCmd
 *    This function do needed local process before sending
 *    the command to FPP
 *     
 ************************************************************/

int cmmMc4ProcessClientCmd(FCI_CLIENT* fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ret = 0;
	cmmd_mc4_entry_t * entry = (cmmd_mc4_entry_t *) cmd_buf;
	cmmd_mc4_listener_t * listener;

	switch(function_code)
	{
		case CMMD_CMD_MC4_MULTICAST:
			// We need to check if we don't have to do something with the interface names

			//Sanity checks
			if (cmd_len < sizeof(cmmd_mc4_entry_t))
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;
				break;
			}

                        if ((entry->action == CMMD_ACTION_QUERY) ||
                            (entry->action == CMMD_ACTION_QUERY_CONT) ||
			    (entry->action == CMMD_ACTION_KEEP_ALIVE))
				goto FCI_CMD;

			if (entry->num_output > MC4_MAX_LISTENERS_IN_QUERY)
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
				break;
			}

			if (cmd_len < (sizeof(cmmd_mc4_entry_t) + (entry->num_output * sizeof(cmmd_mc4_listener_t))))
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;
				break;
			}


			// Check if the listeners are on virtual interface
			listener = (cmmd_mc4_listener_t *)(cmd_buf + sizeof(cmmd_mc4_entry_t) );
			switch( entry->action )
			{
				
				case CMMD_MC_ACTION_ADD:
				case CMMD_MC_ACTION_REMOVE:
				case CMMD_MC_ACTION_UPDATE:
					res_buf[0] = CMMD_ERR_OK;
					ret = mc4_update_entry( entry, listener, entry->action );
					if( ret != CMMD_ERR_OK )
					{
						res_buf[0] = ret;
						goto out;
					}
					goto FCI_CMD;
					break;
				case CMMD_MC_ACTION_REMOVE_LOCAL:
					mc_remove_group(entry,AF_INET);
					break;
				
			}
out:
			break;

		case CMMD_CMD_MC4_RESET:
			__pthread_mutex_lock(&itf_table.lock);
			__pthread_mutex_lock(&ctMutex);
			__pthread_mutex_lock(&rtMutex);
			__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
			__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
			__pthread_mutex_lock(&mc_lock);
			
			res_buf[0] = fci_write(fci_handle, CMMD_CMD_MC4_RESET, 0, NULL);
			if ( ret == CMMD_ERR_OK )
				mc_reset(AF_INET);		

			__pthread_mutex_unlock(&mc_lock);
#ifdef IPSEC_FLOW_CACHE
			__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
			__pthread_mutex_unlock(&neighMutex);
			__pthread_mutex_unlock(&rtMutex);
			__pthread_mutex_unlock(&ctMutex);
			__pthread_mutex_unlock(&itf_table.lock);
			break;

		default:
			res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
			break;
	}

	*res_len = 2;
	return 0;

FCI_CMD:
	return fci_cmd(fci_handle, function_code, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
}

