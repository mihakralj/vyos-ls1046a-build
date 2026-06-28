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
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/ioctl.h>

#include "cmm.h"
#include "cmmd.h"
#include "module_mc6.h"

#define MC6_MAX_LISTENERS_IN_QUERY     5

unsigned char Null_Mac[6] = {0,0,0,0,0,0};

extern pthread_mutex_t mc_lock;

int cmmMc6Show(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int count = 0, ii, jj;
	struct list_head *entry;
	struct mcast_entry *mc;
        char buf1[INET6_ADDRSTRLEN];
        char buf2[INET6_ADDRSTRLEN];

	__pthread_mutex_lock(&mc_lock);
	
	for( ii = 0; ii < MC_NUM_HASH_ENTRIES; ii++ )
	{
		for (entry = list_first(&mc_table[ii]); entry != &mc_table[ii]; entry = list_next(entry))
		{
			mc = container_of(entry, struct mcast_entry, list);
			
			if( mc->family != AF_INET6 )
				continue;
			
                	 cmm_print(DEBUG_STDOUT, "%04d: Src addr: %s  src_mask_len: %x   Dst addr: %s Queue %d  \n",
			  count,
			  inet_ntop(AF_INET6, &mc->src_addr, buf1, INET6_ADDRSTRLEN),
			  mc->src_mask_len,
			  inet_ntop(AF_INET6, &mc->dst_addr, buf2, INET6_ADDRSTRLEN),
                          mc->queue);
		  	for( jj = 0; jj < mc->num_output; jj++) 	
			{
				cmm_print(DEBUG_STDOUT," PROGRAMMED : %02d TIMER : 0x%x   SHAPER : 0x%x  Interface %s\n",mc->l_program[jj],
					mc->listener[jj].timer,mc->listener[jj].shaper_mask, mc->listener[jj].output_device_str);
				if(mc->listener[jj].q_bit)
					cmm_print(DEBUG_STDOUT," QUEUE : 0x%x  \n",mc->listener[jj].queue );
				if(mc->listener[jj].uc_bit)
					cmm_print(DEBUG_STDOUT,"UC MAC is %02x:%02x:%02x:%02x:%02x:%02x  ",mc->listener[jj].uc_mac[0],mc->listener[jj].uc_mac[1],mc->listener[jj].uc_mac[2],mc->listener[jj].uc_mac[3],mc->listener[jj].uc_mac[4],mc->listener[jj].uc_mac[5]);
			}	
			count++;	
				
		}
	}  
        cmm_print(DEBUG_STDOUT, "Total Multicast v6 Entries: %d\n", count);
	__pthread_mutex_unlock(&mc_lock);
	
	return 0;
}

/************************************************************
 *
 *
 *
 ************************************************************/
void cmmMc6ShowPrintHelp()
{
	cmm_print(DEBUG_STDOUT, "show mc6 not yet supported\n");
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmMc6ShowProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	
//help:
	cmmMc6ShowPrintHelp();
	return -1;
}

/************************************************************
 *
 *
 *
 ************************************************************/
int cmmMc6QueryProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	
        int rcvBytes = 0;
	union u_rxbuf rxbuf;
        char buf1[INET6_ADDRSTRLEN];
        char buf2[INET6_ADDRSTRLEN];
	char output_buf[256];
        short rc;
        int count = 0,len =0;

	memset(rxbuf.rcvBuffer, 0, sizeof(rxbuf.rcvBuffer));
        cmmd_mc6_entry_t *mc6_entry = (cmmd_mc6_entry_t *) rxbuf.rcvBuffer;
        cmmd_mc6_listener_t* mc6_listener = (cmmd_mc6_listener_t *)(rxbuf.rcvBuffer + sizeof (cmmd_mc6_entry_t));

        mc6_entry->action = CMMD_ACTION_QUERY;
        rcvBytes = cmmSendToDaemon(daemon_handle,CMMD_CMD_MC6_MULTICAST ,
                  mc6_entry, sizeof(cmmd_mc6_entry_t)+ MC6_MAX_LISTENERS_IN_QUERY * sizeof(cmmd_mc6_listener_t) , rxbuf.rcvBuffer);

        if (rcvBytes < sizeof(cmmd_mc6_entry_t) + sizeof (cmmd_mc6_listener_t)) {
                rc = (rcvBytes < sizeof(unsigned short) ) ? 0 : rxbuf.result;
                if (rc == CMMD_ERR_UNKNOWN_ACTION) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP CMD_MC6_MULTICAST does not support ACTION_QUERY\n");
                } else if (rc == CMMD_ERR_MC_ENTRY_NOT_FOUND) {
                    cmm_print(DEBUG_STDERR, "ERROR: FPP Multicast V6 table empty\n");
                } else {
                    cmm_print(DEBUG_STDERR, "ERROR: Unexpected result returned from FPP rc:%d\n", rc);
                }
                return CLI_OK;
            }
            cmm_print(DEBUG_STDOUT, "IPv6 Multicast Entries:\n");
            do {
                int i, listner_count;
		if(mc6_entry->dst_addr[0] & 0xFF)
		{
#if defined(LS1043)
	                cmm_print(DEBUG_STDOUT, "\n%04d: Ingress Interface: %s Src addr: %s  src_mask_len: %x   Dst addr: %s   Queue: 0x%x \n\n", count,
                                  mc6_entry->input_device_str,
                	          inet_ntop(AF_INET6, &mc6_entry->src_addr, buf1, INET6_ADDRSTRLEN),
                        	  mc6_entry->src_mask_len,
	                          inet_ntop(AF_INET6, &mc6_entry->dst_addr, buf2, INET6_ADDRSTRLEN),
				  mc6_entry->queue );
#else
	                cmm_print(DEBUG_STDOUT, "\n%04d: Src addr: %s  src_mask_len: %x   Dst addr: %s   Queue: 0x%x \n\n",
        	                  count,
                	          inet_ntop(AF_INET6, &mc6_entry->src_addr, buf1, INET6_ADDRSTRLEN),
                        	  mc6_entry->src_mask_len,
	                          inet_ntop(AF_INET6, &mc6_entry->dst_addr, buf2, INET6_ADDRSTRLEN),
				  mc6_entry->queue );
#endif
			count++;
		}
                listner_count = mc6_entry->num_output;
                if (listner_count > MC6_MAX_LISTENERS_IN_QUERY)
                {
                        cmm_print(DEBUG_STDOUT,"output interfaces are more than %d",MC6_MAX_LISTENERS_IN_QUERY);
                        listner_count = MC6_MAX_LISTENERS_IN_QUERY;
                }

                for ( i = 0; i < listner_count; i++)
		{
	            len += sprintf(output_buf + len, "output interface:  ");
                    len += sprintf(output_buf + len, "%s  ", mc6_listener[i].output_device_str);
		    cmm_print (DEBUG_STDOUT,output_buf);
                    if(mc6_listener[i].uc_bit)
                           cmm_print(DEBUG_STDOUT,"UC MAC is %02x:%02x:%02x:%02x:%02x:%02x  ",mc6_listener[i].uc_mac[0],mc6_listener[i].uc_mac[1],mc6_listener[i].uc_mac[2],mc6_listener[i].uc_mac[3],mc6_listener[i].uc_mac[4],mc6_listener[i].uc_mac[5]);
                   cmm_print(DEBUG_STDOUT," TIMER : 0x%x   SHAPER : 0x%x   QUEUE : 0x%x\n",mc6_listener[i].timer,mc6_listener[i].shaper_mask, mc6_listener[i].queue);
                   len = 0;
		}

                mc6_entry->action = CMMD_ACTION_QUERY_CONT;
                rcvBytes = cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MULTICAST, mc6_entry,
                                          sizeof(cmmd_mc6_entry_t)+ MC6_MAX_LISTENERS_IN_QUERY * sizeof(cmmd_mc6_listener_t), rxbuf.rcvBuffer);
            } while (rcvBytes >= sizeof(cmmd_mc6_entry_t)+ sizeof(cmmd_mc6_listener_t) + sizeof(unsigned short));
            cmm_print(DEBUG_STDOUT, "Total Multicast v6 Entries: %d\n", count);

        return CLI_OK;

}


/************************************************************
 *
 *
 *
 ************************************************************/
void cmmMc6SetPrintHelp()
{
#ifdef COMCERTO_2000	
	cmm_print(DEBUG_STDOUT, "Usage: set mc6 interface {if_name} {add | del | update } \n\tgroup {bit_mask} {IPv6 Src Address} {IPv6 Multicast Dst Address} [mode {bridged | routed}] [queue {0..19}] \n\t[listener [timer {timer_value1}] [shapers {0..0xFF}][mc | uc {Mac addr1} ] [queue {0..19}]] [if {if_name}] \n\t [ listener [timer {timer_value2}]  [shapers {0..0xFF}][mc | uc {Mac addr2} ] [queue {0..19}]] [if {if_name}]...  \n\t[ listener [timer {timer_valueN}]  [shapers {0..0xFF}][mc | uc {Mac addrN} ] [queue {0..19}]] [if {if_name}]");
#elif defined(LS1043)
	cmm_print(DEBUG_STDOUT, "Usage: set mc6 ingress_interface {if_name} interface {if_name} {add | del | update } \n\tgroup {bit_mask} {IPv6 Src Address} {IPv6 Multicast Dst Address} [mode {bridged | routed}] [queue {0..31}]\n\t[listener [timer {timer_value1}] [shapers {0..0xFF}][mc | uc {Mac addr1} ] [queue {0..31}]] [if {if_name}] \n\t [ listener [timer {timer_value2}]  [shapers {0..0xFF}][mc | uc {Mac addr2} ] [queue {0..31}]] [if {if_name}]...  \n\t[ listener [timer {timer_valueN}]  [shapers {0..0xFF}][mc | uc {Mac addrN} ] [queue {0..31}]] [if {if_name}] ");
#else
	cmm_print(DEBUG_STDOUT, "Usage: set mc6 interface {if_name} {add | del | update } \n\tgroup {bit_mask} {IPv6 Src Address} {IPv6 Multicast Dst Address} [mode {bridged | routed}] [queue {0..31}]\n\t[listener [timer {timer_value1}] [shapers {0..0xFF}][mc | uc {Mac addr1} ] [queue {0..31}]] [if {if_name}] \n\t [ listener [timer {timer_value2}]  [shapers {0..0xFF}][mc | uc {Mac addr2} ] [queue {0..31}]] [if {if_name}]...  \n\t[ listener [timer {timer_valueN}]  [shapers {0..0xFF}][mc | uc {Mac addrN} ] [queue {0..31}]] [if {if_name}] ");

#endif
	cmm_print(DEBUG_STDOUT, "\n\nUsage:set mc6 mode route|bridge\n");
}

/************************************************************
 * cmmMc6ParseListener
 * This function parses the mc6 command with multiple listeners
 * and a single listener alike.
 *
 ************************************************************/
int cmmMcParseListener(char ** keywords, int *cpt, cmmd_mc6_listener_t * listener, u_int8_t * mode, u_int16_t action)
{

	unsigned int tmp;
	char * endptr;
	
	listener->timer = 0xFFFFFFFF;
	listener->q_bit = 0;
	listener->uc_bit = 0; // Multicast by default when a new entry is added
	listener->Ifbit = 0;
	
	while(keywords[*cpt] && (strncasecmp(keywords[*cpt],"listener",strlen(keywords[*cpt])) != 0))
	{
		if(strcasecmp(keywords[*cpt],"mc") == 0)
		{
			listener->uc_bit = 0;
		}
		else if(strcasecmp(keywords[*cpt],"uc") == 0)
		{
			if(!keywords[++*cpt])
				return 1;
			listener->uc_bit = 1;
			if (!parse_macaddr(keywords[*cpt], listener->uc_mac))
			{
				cmm_print(DEBUG_STDERR, "ERROR %d Invalid Mac Address \n",CMMD_ERR_MC_INVALID_MAC);
				return 1;
			}
			if((listener->uc_mac[0] & 0x01) || !memcmp(listener->uc_mac,Null_Mac,6))
			{
				cmm_print(DEBUG_STDERR, "ERROR %d Invalid Mac Address \n",CMMD_ERR_MC_INVALID_MAC);
				return 1;
			}
		}

		else if (strcasecmp(keywords[*cpt], "timer") == 0)
		{
			if(!keywords[++*cpt])
				return 1;

			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[*cpt], &endptr, 0);
			listener->timer = tmp;
		}
		else if (strcasecmp(keywords[*cpt], "mode") == 0)
		{
			if(!keywords[++*cpt])
				return 1;

			if (strcasecmp(keywords[*cpt], "bridged") == 0)
				*mode = CMMD_MC6_MODE_BRIDGED;
			else if (strcasecmp(keywords[*cpt], "routed") == 0)
				*mode = CMMD_MC6_MODE_ROUTED;
			else 
				return 1;

		}
		else if (strcasecmp(keywords[*cpt], "queue") == 0)
		{

			if(!keywords[++*cpt])
				return 1;

			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[*cpt], &endptr, 0);
			listener->q_bit = 1;

			if (tmp < FPP_NUM_QUEUES)
				listener->queue = tmp;
			else 
				return 1;
		}
		else if (strcasecmp(keywords[*cpt], "shapers") == 0)
		{
			if(!keywords[++*cpt])
				return 1;

			/*Get an integer from the string*/
			endptr = NULL;
			tmp = strtoul(keywords[*cpt], &endptr, 0);

			if (tmp <= 0xFF)
				listener->shaper_mask = tmp;
			else 
				return 1;
		}
		else if(strcasecmp(keywords[*cpt],"if") == 0 )
		{
			if(action != CMMD_MC_ACTION_UPDATE)
				return 1;

			if(!keywords[++*cpt])
				return 1;

			if (strlen(keywords[*cpt]) > (IFNAMSIZ - 1)) {
				cmm_print(DEBUG_STDERR, "Error : interface name %s limited to %d characters\n", keywords[*cpt], (IFNAMSIZ - 1));
				return 1;
			}
			strcpy(listener->new_output_device_str, keywords[*cpt]);
			listener->Ifbit = 1;	
		}
		else
			return 1;
		++*cpt;

	}
	return 0;
}


/************************************************************
 *
 *
 *
 ************************************************************/
int cmmMc6SetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	unsigned int tmp;
	char * endptr;
	int rc = 0;
	char sndBuffer[CMM_BUF_SIZE];
	union u_rxbuf rxbuf;
	char interfaceName[IFNAMSIZ] = {};
	cmmd_mc6_entry_t * entryCmd = (cmmd_mc6_entry_t *) sndBuffer;
	cmmd_mc6_listener_t * listener = (cmmd_mc6_listener_t *) (sndBuffer + sizeof(cmmd_mc6_entry_t));
	int listenerCnt = 0;
	u_int8_t mode = 0;

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

		if (strlen(keywords[cpt]) > (IFNAMSIZ -1)) {
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

		if (strlen(keywords[cpt]) > (IFNAMSIZ -1)) {
			cmm_print(DEBUG_STDERR, "Error : interface name %s limited to 10 characters\n", keywords[cpt]);
			goto help;
		}
		strcpy(interfaceName, keywords[cpt]);
		strcpy(listener->output_device_str,interfaceName);
	}
	else if(strcasecmp(keywords[cpt], "reset") == 0)
	{
		/*Reset, special case*/
		if(cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_RESET, NULL, 0, rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_MC6_RESET", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
		return (rxbuf.result);
	}
	else if (strcasecmp(keywords[cpt], "mode") == 0)
	{
		unsigned short mc6_mode;

		if (!keywords[++cpt])
			goto help;

		if (strcasecmp(keywords[cpt], "bridge") == 0)
			mc6_mode = CMMD_MC6_MODE_BRIDGED;
		else if (strcasecmp(keywords[cpt], "route") == 0)
			mc6_mode = CMMD_MC6_MODE_ROUTED;
		else
			goto help;

		if(cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MODE, &mc6_mode, sizeof(mc6_mode), rxbuf.rcvBuffer) == 2)
		{
			if (rxbuf.result != 0)
				showErrorMsg("CMD_MC6_MODE", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
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
		if (tmp > 128)
			goto help;
		entryCmd->src_mask_len = tmp;

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string for the IPsrc*/
		if(inet_pton(AF_INET6, keywords[cpt], &entryCmd->src_addr) != 1)
			goto help;

		if(!keywords[++cpt])
			goto help;

		/*Get an integer from the string for the IPdst*/
		if(inet_pton(AF_INET6, keywords[cpt], &entryCmd->dst_addr) != 1)
			goto help;

		if (! (entryCmd->dst_addr[0] & 0xFF))
			goto help;
	}

	++cpt;

	while(keywords[cpt])
	{
		strcpy(((cmmd_mc6_listener_t *)listener + listenerCnt)->output_device_str,interfaceName);
		if (strncasecmp(keywords[cpt], "listener",strlen(keywords[cpt])) == 0) //May be a case of multiple listeners, parse differently
		{
			++cpt;
			if(!cmmMcParseListener(keywords, &cpt, listener + listenerCnt, &mode, entryCmd->action))
			{
				if(++listenerCnt ==  MC6_MAX_LISTENERS_IN_QUERY)
					//Send first batch to FPP and reset listener count
				{

					entryCmd->num_output = listenerCnt;
					if(cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MULTICAST, sndBuffer, sizeof(cmmd_mc6_entry_t) + (listenerCnt * sizeof(cmmd_mc6_listener_t)), rxbuf.rcvBuffer) == 2)
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
								cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MULTICAST, sndBuffer, 
								sizeof(cmmd_mc6_entry_t) + (listenerCnt * sizeof(cmmd_mc6_listener_t)), rxbuf.rcvBuffer);	
							}
							else
								showErrorMsg("CMD_MC6_MULTICAST", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
						}
					}
					listenerCnt = 0;
					memset(sndBuffer + sizeof(cmmd_mc6_entry_t), 0 , sizeof(sndBuffer) - sizeof(cmmd_mc6_entry_t));
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
	if(!listenerCnt && !entryCmd->num_output)
		listenerCnt = 1; //fall back to legacy design, single listener is configured.

	entryCmd->num_output = listenerCnt;

	if(listenerCnt && (cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MULTICAST, sndBuffer, sizeof(cmmd_mc6_entry_t) + (listenerCnt * sizeof(cmmd_mc6_listener_t)), rxbuf.rcvBuffer) == 2))
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
				cmmSendToDaemon(daemon_handle, CMMD_CMD_MC6_MULTICAST, sndBuffer, 
				sizeof(cmmd_mc6_entry_t) + (listenerCnt * sizeof(cmmd_mc6_listener_t)), rxbuf.rcvBuffer);	
			}
			else
				showErrorMsg("CMD_MC6_MULTICAST", ERRMSG_SOURCE_FPP, rxbuf.rcvBuffer);
		}
	}
	return rc;

keyword_error:
	cmm_print(DEBUG_STDERR, "ERROR: Unknown keyword %s\n", keywords[cpt]);

help:
	cmmMc6SetPrintHelp();
	return -1;
}


/************************************************************
 * cmmMc6ProcessClientCmd
 *    This function do needed local process before sending
 *    the command to FPP
 *     
 ************************************************************/

int cmmMc6ProcessClientCmd(FCI_CLIENT* fci_handle, int function_code, u_int8_t *cmd_buf, u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	int ret = 0;
	cmmd_mc6_entry_t * entry = (cmmd_mc6_entry_t *) cmd_buf;
	cmmd_mc6_listener_t * listener;

	switch(function_code)
	{
		case CMMD_CMD_MC6_MULTICAST:
			// We need to check if we don't have to do something with the interface names

			//Sanity checks
			if (cmd_len < sizeof(cmmd_mc6_entry_t))
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;
				break;
			}

			if ((entry->action == CMMD_ACTION_QUERY) ||
                            (entry->action == CMMD_ACTION_QUERY_CONT))
                             goto FCI_CMD;

			if (entry->num_output > MC6_MAX_LISTENERS_IN_QUERY)
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM; 
				break;
			}

			if (cmd_len < (sizeof(cmmd_mc6_entry_t) + (entry->num_output * sizeof(cmmd_mc6_listener_t))))
			{
				res_buf[0] = CMMD_ERR_WRONG_COMMAND_SIZE;
				break;
			}


			// Check if the listeners are on virtual interface
			listener = (cmmd_mc6_listener_t *)(cmd_buf + sizeof(cmmd_mc6_entry_t) );
			switch( entry->action )
			{
				case CMMD_MC_ACTION_ADD:
				case CMMD_MC_ACTION_REMOVE:
				case CMMD_MC_ACTION_UPDATE:
					res_buf[0] = CMMD_ERR_OK;
					ret = mc6_update_entry(entry, listener, entry->action );
					if( ret != CMMD_ERR_OK )
					{
						res_buf[0] = ret;
						goto out;
					}
					goto FCI_CMD;		
					break;
				case CMMD_MC_ACTION_REMOVE_LOCAL:
					mc_remove_group(entry,AF_INET6);
					break;
					
			}
out:
			
			break;

		case CMMD_CMD_MC6_RESET:
			__pthread_mutex_lock(&itf_table.lock);
			__pthread_mutex_lock(&ctMutex);
			__pthread_mutex_lock(&rtMutex);
			__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
			__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
			__pthread_mutex_lock(&mc_lock);
			
			res_buf[0] = fci_write(fci_handle, CMMD_CMD_MC6_RESET, 0, NULL);
			if ( ret == CMMD_ERR_OK )
				mc_reset(AF_INET6);

			__pthread_mutex_unlock(&mc_lock);
#ifdef IPSEC_FLOW_CACHE
			__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
			__pthread_mutex_unlock(&neighMutex);
			__pthread_mutex_unlock(&rtMutex);
			__pthread_mutex_unlock(&ctMutex);
			__pthread_mutex_unlock(&itf_table.lock);
			break;

		case CMMD_CMD_MC6_MODE:
			goto FCI_CMD;

		default:
			res_buf[0] = CMMD_ERR_UNKNOWN_COMMAND;
			break;
	}

	*res_len = 2;
	return 0;

FCI_CMD:
	return fci_cmd(fci_handle, function_code, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
}

