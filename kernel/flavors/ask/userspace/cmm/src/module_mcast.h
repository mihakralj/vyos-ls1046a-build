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

#ifndef __MODULE_MCAST_H__
#define __MODULE_MCAST_H__
#include "cmmd.h"

#define MC_NUM_HASH_ENTRIES 32
#define MC_HASH(_addr, _family) ((_family == AF_INET) ? (ntohl(_addr[0])&(MC_NUM_HASH_ENTRIES - 1)):(ntohl(_addr[3])&(MC_NUM_HASH_ENTRIES - 1)))	

#define MC_MAX_LISTENERS_PER_GROUP 10
typedef struct mc_listener {
	u_int32_t timer;
	char	  output_device_str[IFNAMSIZ];
	unsigned char shaper_mask ;
	u_int8_t	uc_bit:1,
			q_bit:1,
			rsvd:6;
	u_int8_t        uc_mac[6];
	u_int8_t	queue;
	char		new_output_device_str[IFNAMSIZ];
	u_int8_t	Ifbit:1,
			rsvd1:7;
        u_int8_t        padding[2];
}__attribute__((__packed__)) mc_listener_t;

/* Note: __packed__ removed - this struct is only used internally in CMM
 * for linked list management and is not sent to kernel or used for IPC.
 * Packed attribute caused arm64 alignment issues with list_head operations. */
typedef struct mcast_entry {
	struct list_head list;
	u_int8_t  family;
//	u_int8_t  programmed;
	u_int8_t  mode : 1,
		  queue : 5,
		  rsvd : 2;
	u_int8_t  src_mask_len;
	u_int32_t src_addr[4];
	u_int32_t dst_addr[4];
	u_int8_t num_output;
	u_int8_t l_program[MC_MAX_LISTENERS_PER_GROUP];
#if defined(LS1043)
	char	        input_device_str[IFNAMSIZ];
#endif
	struct mc_listener listener[MC_MAX_LISTENERS_PER_GROUP];
} mcast_entry_t;

extern struct list_head mc_table[MC_NUM_HASH_ENTRIES];
struct mcast_entry *mc_find ( void  *data, unsigned char family );
struct mcast_entry *mc_add(struct mcast_entry *mc, void *entry, cmmd_mc_listener_t *listener, unsigned char program, unsigned char family );
int  mc_remove( struct mcast_entry *mc, cmmd_mc_listener_t * listener, int num_entries );
void mc_remove_group(void * entryCmd,unsigned char family);
int mc_reset(  unsigned char family );
int mc_update( struct mcast_entry *mc, cmmd_mc_listener_t *listener, int  num_output);
void mc_update_table( FCI_CLIENT *fci, struct rtattr *tb[], struct ifinfomsg *ifi );
int mc6_send_command( FCI_CLIENT *fci, unsigned short action, struct mcast_entry *mc , char * Ifname);
int mc4_send_command( FCI_CLIENT *fci, unsigned short action, struct mcast_entry *mc , char * Ifname);

int mc4_update_entry( cmmd_mc4_entry_t *entry,cmmd_mc_listener_t *listener, unsigned short action );
int mc6_update_entry( cmmd_mc6_entry_t *entry,cmmd_mc_listener_t *listener, unsigned short action );
#endif
