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

struct list_head mc_table[MC_NUM_HASH_ENTRIES];
pthread_mutex_t mc_lock = PTHREAD_MUTEX_INITIALIZER;

/************************************************************
 *
 *
 ************************************************************/
struct mcast_entry *mc_find( void  *data,  unsigned char family )
{
	struct mcast_entry *mc = NULL;
	struct list_head *entry;
	int hash, addr_len;
	unsigned int src_addr[4], dst_addr[4];

	
	if ( family == AF_INET )
	{
		memcpy(src_addr, &((cmmd_mc4_entry_t *)data)->src_addr, 4 );
		memcpy(dst_addr, &((cmmd_mc4_entry_t *)data)->dst_addr, 4 );
		addr_len = 4;
	}
	else
	{
		memcpy(src_addr, &((cmmd_mc6_entry_t *)data)->src_addr, 16 );
		memcpy(dst_addr, &((cmmd_mc6_entry_t *)data)->dst_addr, 16 );
		addr_len = 16;
	}
	
	hash = MC_HASH(dst_addr, family); 
		
	for (entry = list_first(&mc_table[hash]); entry != &mc_table[hash]; entry = list_next(entry))
	{
		mc = container_of(entry, struct mcast_entry, list);
		if(!(memcmp(mc->src_addr, src_addr, addr_len)) && 
			!(memcmp(mc->dst_addr, dst_addr, addr_len)) && 
			(mc->family == family) )
		{
			cmm_print(DEBUG_INFO, "%s: (%d) mc found\n", __func__, __LINE__);
			goto found;	
		}	
		
	}
	
	mc = NULL;
	

found:
	return mc;	
} 

/************************************************************
 *
 *
 ************************************************************/
int mc6_send_command( FCI_CLIENT *fci, unsigned short action, struct mcast_entry *mc , char * Ifname )
{
	int ret = 0, i,j;
	char sndBuffer[256], program = 0;
	cmmd_mc6_entry_t * entryCmd = (cmmd_mc6_entry_t *) sndBuffer;
	cmmd_mc_listener_t *listener = (cmmd_mc_listener_t *) (sndBuffer + sizeof(cmmd_mc6_entry_t));

    cmm_print(DEBUG_INFO, "%s: output: %s action : %d \n", __func__, Ifname, action);
	program = (action ==  CMMD_MC_ACTION_ADD) ? 1 : 0;
	entryCmd->action = action;	
	entryCmd->mode = mc->mode;	
	entryCmd->queue = mc->queue;	
	entryCmd->rsvd = mc->rsvd;	
	entryCmd->src_mask_len = mc->src_mask_len;	
	memcpy(entryCmd->src_addr, mc->src_addr, 16);
	memcpy(entryCmd->dst_addr, mc->dst_addr, 16);
#if defined(LS1043)
	memcpy(entryCmd->input_device_str, mc->input_device_str, IFNAMSIZ);
#endif //LS1043
	for(i = 0 , j = 0 ; i < mc->num_output; i++)
	{
		if((program ^ mc->l_program[i]) && ((Ifname) && (!strncmp(mc->listener[i].output_device_str,Ifname,
						sizeof(mc->listener[i].output_device_str)))))
		{
			memcpy(listener + j++, &mc->listener[i], sizeof(cmmd_mc_listener_t));	
			mc->l_program[i] = program;
		}
	}
	entryCmd->num_output = j;
	
        cmm_print(DEBUG_INFO, "%s: j is  %d \n", __func__, j);
	ret = fci_write(fci, CMMD_CMD_MC6_MULTICAST, 
		sizeof(cmmd_mc6_entry_t) + ( j * sizeof(cmmd_mc_listener_t)), (u_int16_t *) sndBuffer);
        
	if( ( ret == FPP_ERR_MC_ENTRY_NOT_FOUND ) && (action == CMMD_MC_ACTION_REMOVE) )
	{
        	cmm_print(DEBUG_INFO, "%s: Entry not found, might be deleted by fpp : %d\n", __func__, ret);
		return FPP_ERR_OK;	
	}

	if (ret != FPP_ERR_OK)
        {
        	cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_MC6_MULTICAST\n", __func__, ret);
        }
	
	return ret;
}

/************************************************************
 *
 *
 ***********************************************************/
int mc4_send_command( FCI_CLIENT *fci, unsigned short action, struct mcast_entry *mc, char * Ifname)
{
	int ret = 0, i, j;
	char sndBuffer[256], program = 0;
	cmmd_mc4_entry_t * entryCmd = (cmmd_mc4_entry_t *) sndBuffer;
	cmmd_mc_listener_t * listener = (cmmd_mc_listener_t *) (sndBuffer + sizeof(cmmd_mc4_entry_t));
	
        cmm_print(DEBUG_INFO, "%s: (%d) interface : %s command : %d\n", __func__, __LINE__, Ifname , action);

	program = (action ==  CMMD_MC_ACTION_ADD) ? 1 : 0;
	entryCmd->action = action;	
	entryCmd->mode = mc->mode;	
	entryCmd->queue = mc->queue;	
	entryCmd->rsvd = mc->rsvd;	
	entryCmd->src_mask_len = mc->src_mask_len;	
	entryCmd->src_addr = mc->src_addr[0];
	entryCmd->dst_addr = mc->dst_addr[0];
#if defined(LS1043)
	memcpy(entryCmd->input_device_str, mc->input_device_str, IFNAMSIZ);
#endif //LS1043
	// copy each listener for the given interface.
	for(i = 0 , j = 0 ; i< mc->num_output; i++)
	{
		if( (program ^ mc->l_program[i]) && ((Ifname) && (!strncmp(mc->listener[i].output_device_str,Ifname, 
						sizeof(mc->listener[i].output_device_str)))))
		{
			memcpy(listener + j++, &mc->listener[i], sizeof(cmmd_mc_listener_t));	
			mc->l_program[i] = program;
		}
	}
	entryCmd->num_output = j;
	ret = fci_write(fci, CMMD_CMD_MC4_MULTICAST, 
		sizeof(cmmd_mc4_entry_t) + (j * sizeof(cmmd_mc_listener_t) ), (u_int16_t *) sndBuffer);
       
	if( ( ret == FPP_ERR_MC_ENTRY_NOT_FOUND ) && (action == CMMD_MC_ACTION_REMOVE) )
	{
        	cmm_print(DEBUG_INFO, "%s: Entry not found, might be deleted by fpp : %d\n", __func__, ret);
		return FPP_ERR_OK;	
	}

	if (ret != FPP_ERR_OK)
        {
        	cmm_print(DEBUG_ERROR, "%s: Error %d while sending CMD_MC4_MULTICAST\n", __func__, ret);
        }
	
	return ret;
}
    
/***********************************************************
 * Name : mc_reset
 *
 ***********************************************************/ 
int mc_reset( unsigned char family )
{
	struct list_head *entry;
	struct mcast_entry *mc;
	int ii;

        cmm_print(DEBUG_INFO, "%s: %d\n", __func__, __LINE__);

	for( ii = 0; ii < MC_NUM_HASH_ENTRIES; ii++)
	{
		for (entry = list_first(&mc_table[ii]); entry != &mc_table[ii];)
		{
			mc = container_of(entry, struct mcast_entry, list);

			/* Move to next list entry before list_del*/
			entry = list_next(entry);
			if ( family == mc->family )
			{
				list_del(&mc->list);
				free(mc);
			}

		}

	}

	return 0;
}

int mc_update( struct mcast_entry *mc, cmmd_mc_listener_t *listener, int  num_output)
{
	int i = 0, j =0,program = 1;
	cmm_print(DEBUG_INFO, "%s: \n", __func__);

	for( j = 0 ; j < num_output; j++)
	{
		for(i=0 ;i < mc->num_output; i++)
		{
			if(!memcmp((listener + j)->uc_mac, mc->listener[i].uc_mac,6) && 
				(!strcasecmp((listener + j)->output_device_str,mc->listener[i].output_device_str))) 
			{
				memcpy(&mc->listener[i], (listener + j), sizeof(cmmd_mc_listener_t));
				program = mc->l_program[i];
			}
		}
	}
	return program;
}

/************************************************************
 * Name : mc6_add_entry
 * Parameters : 
 *
 ***********************************************************/
struct mcast_entry *mc_add(struct mcast_entry *mc, void *entry, cmmd_mc_listener_t *listener, unsigned char program, unsigned char family )
{
	int hash, add_group = 1 , i = 0, j =0, k = 0, dup = 0;
	cmm_print(DEBUG_INFO, "%s: \n", __func__);

	if( !((family == AF_INET) || (family == AF_INET6)) )
		return NULL;  
	
	if(mc)
		add_group = 0;	
	else 
	{
		if((mc = (struct mcast_entry *)malloc(sizeof(struct mcast_entry))) == NULL)
			return NULL;
		memset(mc, 0, sizeof(struct mcast_entry));
	}

	if ( family == AF_INET )
	{
		cmmd_mc4_entry_t *mc4 = (cmmd_mc4_entry_t *)entry;
		if( add_group)
		{
			mc->mode = mc4->mode;
			mc->queue = mc4->queue;
			mc->rsvd = mc4->rsvd;
			mc->src_addr[0] = mc4->src_addr;
			mc->dst_addr[0] = mc4->dst_addr;
			mc->src_mask_len = mc4->src_mask_len;
			mc->family = AF_INET;
#if defined(LS1043)
			memcpy(mc->input_device_str, mc4->input_device_str, IFNAMSIZ);
#endif //LS1043
		}
		for( i = mc->num_output , j = 0 ;( ( i < MC_MAX_LISTENERS_PER_GROUP)  && (j < mc4->num_output)); i++, j++)
		{
			for(k=0 ;k < i; k++)
			{
				if(!memcmp((listener + j)->uc_mac, mc->listener[k].uc_mac,6) && // Duplicate entry
					(!strcasecmp((listener + j)->output_device_str,mc->listener[k].output_device_str))) 
				{
					dup++;
					break;	
				}
			}
			if(k == i)	
			{
				memcpy(&mc->listener[i], (listener + j), sizeof(cmmd_mc_listener_t));
				mc->l_program[i] = program;
			}
		}
		mc->num_output =  i - dup;
	}
	else
	{	
		cmmd_mc6_entry_t *mc6 = (cmmd_mc6_entry_t *)entry;
		if(add_group)
		{	
			mc->mode = mc6->mode;
			mc->queue = mc6->queue;
			mc->rsvd = mc6->rsvd;
			memcpy(mc->src_addr, mc6->src_addr, 16);
			memcpy(mc->dst_addr, mc6->dst_addr, 16);
			mc->src_mask_len = mc6->src_mask_len;
			mc->family = AF_INET6;
#if defined(LS1043)
			memcpy(mc->input_device_str, mc6->input_device_str, IFNAMSIZ);
#endif //LS1043
		}

		for( i = mc->num_output, j = 0 ;(( i < MC_MAX_LISTENERS_PER_GROUP) && ( j< mc6->num_output)); i++, j++ )
		{
			for(k=0 ;k < i; k++)
			{
				if(!memcmp((listener + j)->uc_mac, mc->listener[k].uc_mac,6) && // Duplicate entry
					(!strcasecmp((listener + j)->output_device_str,mc->listener[k].output_device_str))) 
				{
					dup++;
					break;	
				}
			}
			if(k == i)	
			{
				memcpy(&mc->listener[i], (listener + j), sizeof(cmmd_mc_listener_t));
				mc->l_program[i] = program;
			}
		}
		mc->num_output =  i - dup;
	}
	// If new mc entry add group and listeners
	if(add_group)
	{
		hash = MC_HASH(mc->dst_addr, family);
		list_add(&mc_table[hash], &mc->list);
	}
	return mc;
}

/************************************************************
 * Name : mc_remove_group
 * Parameters : 
 *
 ***********************************************************/
void mc_remove_group(void * entry,unsigned char family)
{

	struct mcast_entry *mc =  NULL;
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&mc_lock);

	mc = mc_find( entry, family);
	if(mc)
	{
		list_del(&mc->list);
		free(mc);
	}
	__pthread_mutex_unlock(&mc_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);
}

/************************************************************
 * Name : mc_remove
 * Parameters : 
 *
 ***********************************************************/
int  mc_remove( struct mcast_entry *mc, cmmd_mc_listener_t * listener, int num_output )
{
	int i = 0, j = 0;
	int ret = CMMD_ERR_OK;
	for(i = 0;  i <  num_output; i++ )
	{
		for(j = 0 ; j < mc->num_output; j++ )
		{
			if((listener + i)->uc_bit)
			{
				if(!(mc->listener[j].uc_bit))
					continue;

				if(!memcmp((listener + i)->uc_mac, mc->listener[j].uc_mac,6))
				{
					cmm_print(DEBUG_STDOUT,"UC MAC is %02x:%02x:%02x:%02x:%02x:%02x  ",mc->listener[j].uc_mac[0],mc->listener[j].uc_mac[1],mc->listener[j].uc_mac[2],mc->listener[j].uc_mac[3],mc->listener[j].uc_mac[4],mc->listener[j].uc_mac[5]);
					if(!mc->l_program[j])
						ret = CMMD_ERR_NOT_CONFIGURED;
					// Found a match remove it and fill the hole
					memmove(&mc->listener[j],&mc->listener[j+1], (mc->num_output - (j+1)) * sizeof(cmmd_mc_listener_t));
					for (;j < mc->num_output - 1; j++)
						mc->l_program[j] = mc->l_program[j+1];
					mc->num_output -=1;
					break;
				}
			}
			else
			{
				if(mc->listener[j].uc_bit)
					continue;

				if(!strncmp((listener + i)->output_device_str, mc->listener[j].output_device_str,sizeof((listener + i)->output_device_str)))
				{
					if(!mc->l_program[j])
						ret = CMMD_ERR_NOT_CONFIGURED;
					// Found a match remove it and fill the hole
					memcpy(&mc->listener[j],&mc->listener[j+1], (mc->num_output - (j+1)) * sizeof(cmmd_mc_listener_t));
					for (;j < mc->num_output - 1; j++)
						mc->l_program[j] = mc->l_program[j+1];
					mc->num_output -=1;
					break;
				}

			}
		}
	}	
	if(! mc-> num_output)
	{
		list_del(&mc->list);
		free(mc);
	}
	return ret;
}

/************************************************************
 *
 *
 ************************************************************/ 
void mc_update_table( FCI_CLIENT *fci, struct rtattr *tb[], struct ifinfomsg *ifi )
{
	struct mcast_entry *mc = NULL;
	struct list_head *entry;
	char ifname[IFNAMSIZ] = {} ;
	unsigned int ii, len, jj;
	unsigned short action = CMMD_MC_ACTION_ADD;
	
	if(!ifi->ifi_change)
		return;

	len = RTA_PAYLOAD(tb[IFLA_IFNAME]);
	
	memcpy( ifname, RTA_DATA(tb[IFLA_IFNAME]), len );
	ifname[len] = '\0';
	
        cmm_print(DEBUG_INFO, "%s: (%d) interface name : %s index:%d length is %d  change is %d \n", __func__, __LINE__, 
								ifname, ifi->ifi_index, len, ifi->ifi_change ) ;

	if( __itf_is_programmed(ifi->ifi_index) <= 0 )
		action = CMMD_MC_ACTION_REMOVE; 

	__pthread_mutex_lock(&mc_lock);
	
	for( ii = 0; ii < MC_NUM_HASH_ENTRIES; ii++ )
	{
		for (entry = list_first(&mc_table[ii]); entry != &mc_table[ii]; entry = list_next(entry))
		{
			mc = container_of(entry, struct mcast_entry, list);
			for(jj =0 ; jj < mc->num_output; jj++)
			{
				 cmm_print(DEBUG_INFO, "%s: %s", mc->listener[jj].output_device_str, ifname);		
				if(! strncmp(mc->listener[jj].output_device_str, ifname, sizeof(mc->listener[jj].output_device_str) ) )
				{
					if( mc->family == AF_INET )
						mc4_send_command( fci, action, mc, ifname);
					else
						mc6_send_command( fci, action, mc, ifname);
				}
			}
			/* In case of VLAN, MC entries in FPP are purged, when vlan is down */ 
		}
	}  
	__pthread_mutex_unlock(&mc_lock);

	return;
}


/**************************************************************************
 *
 *
 *************************************************************************/ 
int mc6_update_entry( cmmd_mc6_entry_t *entry, 
			cmmd_mc_listener_t *listener, unsigned short action )
{
	struct mcast_entry *mc = NULL;
	int program = 1, ret = CMMD_ERR_OK;	
				

	if (!strcasecmp( listener->output_device_str, "acp"))
		return CMMD_ERR_OK;
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&mc_lock);
		

	cmm_print(DEBUG_INFO, "%s, Check for interface %s index : %d \n", 
				__func__, listener->output_device_str, 
				if_nametoindex(listener->output_device_str));

	if( __itf_is_programmed(if_nametoindex(listener->output_device_str)) <= 0 )
		program = 0;
	mc = mc_find( entry, AF_INET6);

	switch( action )
	{
		case CMMD_MC_ACTION_ADD:
			if( mc && mc->num_output == MC_MAX_LISTENERS_PER_GROUP)
			{
				ret = CMMD_ERR_MC_MAX_LISTENERS; 
				goto done;
			}
			mc = mc_add(mc,entry, listener, program, AF_INET6 );

			if (!mc)
				ret = CMMD_ERR_MEMORY;

			if(!program)
				ret = CMMD_ERR_NOT_CONFIGURED;
			break;
						
						
		case CMMD_MC_ACTION_REMOVE:
			if(mc)
				ret = mc_remove(mc,listener, entry->num_output);
			else
			{
				ret = CMMD_ERR_NOT_FOUND;		
				goto done;
			}

			break;
		case CMMD_MC_ACTION_UPDATE:
			if(mc)
			{
				mc->mode = entry->mode;
				mc->queue = entry->queue;
				if(! mc_update(mc,listener, entry->num_output))
					ret = CMMD_ERR_NOT_CONFIGURED;
			}
			else
			{
				ret = CMMD_ERR_NOT_FOUND;		
				goto done;
			}
		break;	
	}					
done:
	__pthread_mutex_unlock(&mc_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return ret;	
}

/**************************************************************************
 *
 *
 *************************************************************************/ 
int mc4_update_entry( cmmd_mc4_entry_t *entry, 
			cmmd_mc_listener_t *listener, unsigned short action )
{
	struct mcast_entry *mc = NULL;
	int program = 1, ret = CMMD_ERR_OK;	
				

	if (!strcasecmp( listener->output_device_str, "acp"))
		return CMMD_ERR_OK;
	__pthread_mutex_lock(&itf_table.lock);
	__pthread_mutex_lock(&ctMutex);
	__pthread_mutex_lock(&rtMutex);
	__pthread_mutex_lock(&neighMutex);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_lock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_lock(&mc_lock);
		
	cmm_print(DEBUG_INFO, "%s, Check for interface %s index : %d \n", 
				__func__, listener->output_device_str, 
				if_nametoindex(listener->output_device_str));
	if( __itf_is_programmed(if_nametoindex(listener->output_device_str)) <= 0 )
			program = 0;


	mc = mc_find( entry, AF_INET);
	switch( action )
	{
		case CMMD_MC_ACTION_ADD:
			if(mc && (mc->num_output == MC_MAX_LISTENERS_PER_GROUP))
			{
				ret = CMMD_ERR_MC_MAX_LISTENERS; 
				goto done;
			}
			mc = mc_add(mc, entry, listener, program, AF_INET );
			if (!mc)
				ret = CMMD_ERR_MEMORY;

			if(!program)
				ret = CMMD_ERR_NOT_CONFIGURED;
			break;
						
						
		case CMMD_MC_ACTION_REMOVE:
			if ( mc )
				ret = mc_remove(mc,listener, entry->num_output);
			else
			{
				ret = CMMD_ERR_NOT_FOUND;		
				goto done;
			}
			break;
			
		case CMMD_MC_ACTION_UPDATE:
			if(mc)
			{
				mc->mode = entry->mode;
				mc->queue = entry->queue;
				if(! mc_update(mc,listener, entry->num_output))
					ret = CMMD_ERR_NOT_CONFIGURED;

			}
			else
			{
				ret = CMMD_ERR_NOT_FOUND;		
				goto done;
			}
		break;	
	}					
				
done:
	__pthread_mutex_unlock(&mc_lock);
#ifdef IPSEC_FLOW_CACHE
	__pthread_mutex_unlock(&flowMutex);
#endif /* IPSEC_FLOW_CACHE */
	__pthread_mutex_unlock(&neighMutex);
	__pthread_mutex_unlock(&rtMutex);
	__pthread_mutex_unlock(&ctMutex);
	__pthread_mutex_unlock(&itf_table.lock);

	return ret;	
}
