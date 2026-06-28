/*
 *
 *  Copyright (C) 2010 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017-2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#ifndef __CMMD__
#define __CMMD__

#include <fpp.h>

/*--------------------------------- Generic errors ---------------------------*/
#define CMMD_ERR_OK					FPP_ERR_OK
#define CMMD_ERR_UNKNOWN_COMMAND			FPP_ERR_UNKNOWN_COMMAND
#define CMMD_ERR_WRONG_COMMAND_SIZE			FPP_ERR_WRONG_COMMAND_SIZE
#define CMMD_ERR_WRONG_COMMAND_PARAM			FPP_ERR_WRONG_COMMAND_PARAM
#define CMMD_ERR_UNKNOWN_ACTION				FPP_ERR_UNKNOWN_ACTION
#define CMMD_ERR_UNKNOWN				32000
#define CMMD_ERR_NOT_FOUND				32001
#define CMMD_ERR_NOT_CONFIGURED				32002
#define CMMD_ERR_DUPLICATE				32003
#define CMMD_ERR_MEMORY					32004

/*Actions used in severals commands*/
#define CMMD_ACTION_REGISTER     FPP_ACTION_REGISTER
#define CMMD_ACTION_DEREGISTER   FPP_ACTION_DEREGISTER
#define CMMD_ACTION_KEEP_ALIVE   FPP_ACTION_KEEP_ALIVE
#define CMMD_ACTION_REMOVED      FPP_ACTION_REMOVED
#define CMMD_ACTION_UPDATE       FPP_ACTION_UPDATE
#define CMMD_ACTION_QUERY        FPP_ACTION_QUERY
#define CMMD_ACTION_QUERY_CONT   FPP_ACTION_QUERY_CONT
#define CMMD_ACTION_QUERY_LOCAL  FPP_ACTION_QUERY_LOCAL

/*------------------------------------- Sockets ------------------------------*/
#define CMMD_CMD_SOCKET_OPEN				0x1301
#define CMMD_CMD_SOCKET_CLOSE				0x1302
#define CMMD_CMD_SOCKET_SHOW				0x1303
#define CMMD_CMD_SOCKET_UPDATE				0x1305

#define CMMD_SOCKET_TYPE_LANWAN				0
#define CMMD_SOCKET_TYPE_ACP				1
#define CMMD_SOCKET_TYPE_MSP				2
#define CMMD_SOCKET_TYPE_L2TP				3
#define CMMD_SOCKET_TYPE_LRO				4

#define CMMD_ERR_SOCK_ALREADY_OPEN			FPP_ERR_SOCK_ALREADY_OPEN	
#define CMMD_ERR_SOCKID_ALREADY_USED			FPP_ERR_SOCKID_ALREADY_USED
#define CMMD_ERR_SOCK_ALREADY_OPENED_WITH_OTHER_ID	FPP_ERR_SOCK_ALREADY_OPENED_WITH_OTHER_ID
#define CMMD_ERR_WRONG_SOCKID				FPP_ERR_WRONG_SOCKID

typedef struct cmmd_socket_open_cmd {
	u_int16_t	id;
	u_int8_t type;
	u_int8_t mode;
	u_int32_t	family;
	u_int32_t	saddr[4];
	u_int32_t	daddr[4];
	u_int16_t	sport;
	u_int16_t	dport;
	u_int8_t	proto;
        u_int8_t	queue;
	u_int16_t	dscp;
	u_int32_t	fwmark;
	u_int16_t	secure;
#if defined(LS1043)
	u_int16_t       expt_flag; /* flag use to 1)send first packet to exception path or/and 2)duplicate rtp packets*/
#else
	u_int16_t	pad2;
#endif //LS1043
} __attribute__((__packed__)) cmmd_socket_open_cmd_t;

typedef struct cmmd_socket_update_cmd {
	u_int16_t	id;
	u_int16_t	rsvd1;
	u_int32_t	family;
	u_int32_t	saddr[4];
	u_int16_t	sport;
	u_int8_t	rsvd2;
	u_int8_t	queue;
	u_int16_t	dscp;
	u_int16_t	pad;
	u_int32_t	fwmark;
	u_int16_t	secure;
#if defined(LS1043)
	u_int16_t       expt_flag; /* flag use to 1)send first packet to exception path or/and 2)duplicate rtp packets*/
#else
	u_int16_t	pad2;
#endif //LS1043
} __attribute__((__packed__)) cmmd_socket_update_cmd_t;

typedef struct cmmd_socket_close_cmd {
	u_int16_t	id;
	u_int16_t	pad1;
} __attribute__((__packed__)) cmmd_socket_close_cmd_t;

typedef struct cmmd_socket_show_cmd {
	u_int16_t id;
	u_int16_t dump_all;
	u_int16_t nb_socket;
	u_int16_t pad1;
} __attribute__((__packed__)) cmmd_socket_show_cmd_t;

typedef struct cmmd_socket_show_res {
	u_int16_t	rc;
	u_int16_t	eof;
	u_int16_t	nb_socket;
	u_int16_t	pad1;
	struct socket_info {
		u_int16_t	sock_id;
		u_int32_t	flags;
	} sockets[0];
} __attribute__((__packed__)) cmmd_socket_show_res_t;

/*---------------------------------- Routing ---------------------------------*/
#define CMMD_CMD_EXTROUTE		0x0D01

#define CMMD_EXTROUTE_ACTION_ADD	0
#define CMMD_EXTROUTE_ACTION_REMOVE	1
#define CMMD_EXTROUTE_ACTION_QUERY	2
#define CMMD_EXTROUTE_ACTION_RESET	3
                        
typedef struct cmmd_route_entry {
	int32_t		action;
	u_int32_t	prio;
	u_int32_t	mtu;
	char		output_device_str[IFNAMSIZ];
	char		input_device_str[IFNAMSIZ];
	u_int32_t	dst_addr[2];
	u_int32_t	src_addr[2];
	u_int16_t	proto;
	u_int16_t	dst_port[2];
	u_int16_t	src_port[2];
} cmmd_route_entry_t;              

/*-------------------------------------- Conntrack ----------------------------*/
#define CMMD_CMD_IPV4_CONNTRACK		FPP_CMD_IPV4_CONNTRACK
#define CMMD_CMD_IPV6_CONNTRACK		FPP_CMD_IPV6_CONNTRACK

#define CMMD_ERR_CT_ENTRY_ALREADY_REGISTERED	FPP_ERR_CT_ENTRY_ALREADY_REGISTERED
#define CMMD_ERR_CT_ENTRY_NOT_FOUND		FPP_ERR_CT_ENTRY_NOT_FOUND

typedef fpp_ct_cmd_t 		cmmd_ct_cmd_t;
typedef fpp_ct_ex_cmd_t		cmmd_ct_ex_cmd_t;
typedef fpp_ct6_cmd_t		cmmd_ct6_cmd_t;
typedef fpp_ct6_ex_cmd_t	cmmd_ct6_ex_cmd_t;

/*---------------------------------- Fast Forwarding -------------------------*/
#define CMMD_CMD_IPV4_FF_CONTROL		FPP_CMD_IPV4_FF_CONTROL  

/* Structure representing the command sent to enable/disable fast-forward */
typedef fpp_ff_ctrl_cmd_t 	cmmd_ff_ctrl_cmd_t;
 
/*---------------------------------- Multicast -------------------------------*/
// 0x0700 -> 0x07FF : Multicast modules 
#define CMMD_ERR_MC_ENTRY_NOT_FOUND		FPP_ERR_MC_ENTRY_NOT_FOUND
#define CMMD_ERR_MC_MAX_LISTENERS                    701
#define CMMD_ERR_MC_DUP_LISTENER                     702
#define CMMD_ERR_MC_ENTRY_OVERLAP  		     703 
#define CMMD_ERR_MC_INVALID_MAC                      704
#define CMMD_ERR_MC_INTERFACE_NOT_ALLOWED            705

#define CMMD_CMD_MC6_MULTICAST			0x0703
#define CMMD_CMD_MC6_RESET			0x0704
#define CMMD_CMD_MC6_MODE               	0x0705


#define CMMD_MC6_MAX_LISTENERS_PER_GROUP     	4

#define CMMD_MC6_MODE_BRIDGED			0x0001
#define CMMD_MC6_MODE_ROUTED         		0x0000
#define CMMD_MC6_MODE_MASK           		0x0001

typedef struct mc_listener cmmd_mc6_listener_t;

typedef struct cmmd_mc6_entry {
	u_int16_t	action;
	u_int8_t	mode : 1, 
			queue : 5,
			rsvd : 2 ;
	u_int8_t	src_mask_len;
	u_int32_t	src_addr[4];
	u_int32_t	dst_addr[4];
	u_int32_t	num_output;
#if defined(LS1043)
	char	        input_device_str[IFNAMSIZ];
#endif
} __attribute__((__packed__)) cmmd_mc6_entry_t;

// 0x0700 -> 0x07FF : Multicast modules
#define CMMD_CMD_MC4_MULTICAST			0x0701
#define CMMD_CMD_MC4_RESET			0x0702

#define CMMD_MC_ACTION_ADD			0
#define CMMD_MC_ACTION_REMOVE			1
#define CMMD_MC_ACTION_UPDATE       		2
#define CMMD_MC_ACTION_REMOVE_LOCAL       	11

#define CMMD_MC4_MODE_BRIDGED     		0x0001
#define CMMD_MC4_MODE_ROUTED			0x0000
#define CMMD_MC4_MODE_MASK			0x0001

#define CMMD_MC4_MAX_LISTENERS_PER_GROUP	4

typedef struct mc_listener  cmmd_mc4_listener_t;      

typedef struct cmmd_mc4_entry {
	u_int16_t	action;
	u_int8_t	src_mask_len;
	u_int8_t	mode : 1, 
			queue : 5,
			rsvd : 2 ;
	u_int32_t	src_addr;
	u_int32_t	dst_addr;
	u_int32_t	num_output;
#if defined(LS1043)
	char	        input_device_str[IFNAMSIZ];
#endif
} __attribute__((__packed__)) cmmd_mc4_entry_t;


typedef struct mc_listener  cmmd_mc_listener_t;      
/*----------------------------------- Voice File ---------------------------------*/
#define CMMD_CMD_VOICE_FILE_LOAD	0x0820
#define CMMD_CMD_VOICE_FILE_UNLOAD	0x0821

#define CMMD_VOICE_FILE_MAX_NAMESIZE	240

typedef struct cmmd_voice_file_load_cmd {
	u_int16_t file_id;
	u_int16_t payload_type;
	u_int16_t frame_size;
	char filename[CMMD_VOICE_FILE_MAX_NAMESIZE];
} __attribute__((__packed__)) cmmd_voice_file_load_cmd_t;

typedef struct cmmd_voice_file_unload_cmd {
	u_int16_t file_id;
} __attribute__((__packed__)) cmmd_voice_file_unload_cmd_t;

/*----------------------------------- Tunnel ---------------------------------*/
#define CMMD_ERR_TNL_ENTRY_NOT_FOUND	FPP_ERR_TNL_ENTRY_NOT_FOUND

/* CMM to FPP API Commands */
#define CMMD_CMD_TUNNEL_ADD		FPP_CMD_TUNNEL_ADD
#define CMMD_CMD_TUNNEL_DEL		FPP_CMD_TUNNEL_DEL
#define CMMD_CMD_TUNNEL_UPDATE		FPP_CMD_TUNNEL_UPDATE
#define CMMD_CMD_TUNNEL_SEC		FPP_CMD_TUNNEL_SEC
#define CMMD_CMD_TUNNEL_QUERY		FPP_CMD_TUNNEL_QUERY	
#define CMMD_CMD_TUNNEL_QUERY_CONT	FPP_CMD_TUNNEL_QUERY_CONT	
#define CMMD_CMD_TUNNEL_SHOW		0x0B09
#define CMMD_CMD_TUNNEL_SAMREADY	0x0B0a

/* CMM to Deamon message structure */
typedef struct cmmd_tunnel {
	char		name[IFNAMSIZ];    /* Tunnel name */
        int8_t		ipsec;             /* IPSec ? */
        int8_t		tunnel_type;       /* Type */
} __attribute__((__packed__)) cmmd_tunnel_t;

/* CMM / FPP API Command */
typedef fpp_tunnel_create_cmd_t cmmd_tunnel_create_cmd_t; 
typedef fpp_tunnel_query_cmd_t cmmd_tunnel_query_cmd_t; 
typedef fpp_tunnel_del_cmd_t	cmmd_tunnel_del_cmd_t;
typedef fpp_tunnel_sec_cmd_t	cmmd_tunnel_sec_cmd_t; 

/*--------------------------------------- PPoE --------------------------------*/
#define CMMD_CMD_PPPOE_RELAY_ENTRY			FPP_CMD_PPPOE_RELAY_ENTRY
#define CMMD_CMD_PPPOE_RELAY_ADD			FPP_CMD_PPPOE_RELAY_ADD
#define CMMD_CMD_PPPOE_RELAY_REMOVE			FPP_CMD_PPPOE_RELAY_REMOVE

#define CMMD_ERR_PPPOE_ENTRY_ALREADY_REGISTERED		FPP_ERR_PPPOE_ENTRY_ALREADY_REGISTERED	
#define CMMD_ERR_PPPOE_ENTRY_NOT_FOUND			FPP_ERR_PPPOE_ENTRY_NOT_FOUND

typedef fpp_relay_info_t cmmd_relay_info_t;

/* Structure representing the command sent to add or remove a pppoe session */
typedef fpp_pppoe_relay_cmd_t cmmd_pppoe_relay_cmd_t;

/*---------------------------------------- VLAN --------------------------------*/
#define CMMD_CMD_VLAN_ENTRY				FPP_CMD_VLAN_ENTRY	
#define CMMD_CMD_VLAN_RESET				FPP_CMD_VLAN_RESET	

#define CMMD_ERR_VLAN_ENTRY_ALREADY_REGISTERED		FPP_ERR_VLAN_ENTRY_ALREADY_REGISTERED	
#define CMMD_ERR_VLAN_ENTRY_NOT_FOUND			FPP_ERR_VLAN_ENTRY_NOT_FOUND

/* Structure describing vlan in query response between client and Daemon */
typedef struct cmmd_vlan_response {
	u_int16_t	vlan_id;
	char 		vlan_ifname[IFNAMSIZ];
	char		vlan_phy_if_name[IFNAMSIZ];
} __attribute__((__packed__)) cmmd_vlan_response_t;

typedef fpp_vlan_cmd_t cmmd_vlan_cmd_t; 
/*---------------------------------------- IPR STATS ------------------------------*/
#define CMMD_CMD_IPR_V4_STATS				FPP_CMD_IPR_V4_STATS
#define CMMD_CMD_IPR_V6_STATS				FPP_CMD_IPR_V6_STATS

struct ip_reassembly_info {
	u_int64_t num_frag_pkts;
        u_int64_t num_reassemblies;
        u_int64_t num_completed_reassly;
        u_int64_t num_sess_matches;
        u_int64_t num_frags_too_small;
        u_int64_t num_reassm_timeouts;
        u_int64_t num_overlapping_frags;
        u_int64_t num_too_many_frags;
        u_int64_t num_failed_bufallocs;
        u_int64_t num_failed_ctxallocs;
        u_int64_t num_fatal_errors;
        u_int64_t num_failed_ctxdeallocs;
        u_int32_t table_mask;            //hash mask
        u_int32_t ipr_timer;             //ipr timer location
        u_int32_t timeout_val;           //reassembly timeout
        u_int32_t timeout_fqid;          //fqid for timeout fragments
        u_int32_t max_frags;             //max allowed sessions per session
        u_int32_t min_frag_size;         //min frag size other than last
        u_int32_t max_con_reassm;        //max concurrent reassemblies
        u_int32_t reassem_bpid;          //buffer pool for re-assembly context
        u_int32_t reassem_bsize;         //size of buffers in reassem_context
        u_int32_t frag_bpid;             //buffer pool for re-assembly fragments
        u_int32_t frag_bsize;            //size of buffers in reassem_bpid
        u_int32_t timer_tnum;            //timer task number
        u_int32_t reassly_dbg;           //debug area
	u_int32_t bucket_base;           //start of hash buckets
        u_int32_t curr_sessions;         //curr reassembly sessions
        u_int32_t txc_fqid;              //fqid for handling SG buffers
};


/*---------------------------------------- MACVLAN --------------------------------*/
#define CMMD_CMD_MACVLAN_ENTRY				FPP_CMD_MACVLAN_ENTRY
#define CMMD_CMD_MACVLAN_RESET				FPP_CMD_MACVLAN_RESET

#define CMMD_ERR_MACVLAN_ENTRY_ALREADY_REGISTERED	FPP_ERR_MACVLAN_ENTRY_ALREADY_REGISTERED
#define CMMD_ERR_MACVLAN_ENTRY_NOT_FOUND		FPP_ERR_MACVLAN_ENTRY_NOT_FOUND

typedef fpp_macvlan_cmd_t cmmd_macvlan_cmd_t; 

/*----------------DPD---------------------------------------*/
#define CMMD_CMD_IPSEC_DPDSAQUERYTIMER		0x0a0d

/*-Structure representing the command sent to configure IPsec SA query Timer for DPD */
typedef struct cmmd_saquery_timer {
	int32_t		action;
	u_int32_t	SaQueryTimerVal;
} cmmd_saquery_timer_t;

#define CMMD_DPDSAQUERY_ACTION_ENABLE		0
#define CMMD_DPDSAQUERY_ACTION_DISABLE		1
#define CMMD_DPDSAQUERY_ACTION_SETTIMER		2

#define MAX_QUERY_TIMER_VAL 900
/*-------------------------------------------*/

/*----------------DPI---------------------------------*/
#ifdef COMCERTO_2000 

#define CMMD_CMD_DPIENABLE	0x1601

/*-Structure representing the command sent to configure DPI enable/disable Flag */
typedef struct cmmd_dpi_enable {
	int32_t		action;
}cmmd_dpi_enable_t;

#define CMMD_DPIFLAG_ACTION_ENABLE		0
#define CMMD_DPIFLAG_ACTION_DISABLE		1

#endif /*C2000_DPI*/
/*----------------DPI---------------------------*/

/*-------------Asymmetric fast forward------------------*/
#define CMMD_ASYM_FF_ENABLE	0x1701

/*-Structure representing the command sent to configure DPI enable/disable Flag */
typedef struct cmmd_asymff_enable {
	int32_t		action;
}cmmd_asymff_enable_t;

#define CMMD_ASYM_FF_ACTION_ENABLE		0
#define CMMD_ASYM_FF_ACTION_DISABLE		1

/*-------------Asymmetric fast forward-------------------*/

/*----------------------------------- L2TP ---------------------------------*/
#define CMMD_CMD_L2TP_SESSION		0x1801		
#define CMMD_CMD_L2TP_SESSION_CREATE		CMMD_CMD_L2TP_SESSION
#define CMMD_CMD_L2TP_SESSION_DESTROY		0x1802

#define CMMD_L2TP_OPT_LENGTH			0x0001
#define CMMD_L2TP_OPT_SEQ			0x0002

/* CMM Lib message structure */
typedef struct cmmd_l2tp_session {
	char		itf_name[IFNAMSIZ];		/* L2TP/PPP interface name */
	u_int32_t	family;
	u_int32_t	local_addr[4];
	u_int32_t	peer_addr[4];
	u_int16_t	local_port;
	u_int16_t	peer_port;
	u_int16_t	local_tun_id;
	u_int16_t	peer_tun_id;
	u_int16_t	local_ses_id;
	u_int16_t	peer_ses_id;
	u_int16_t	options;		
	u_int16_t	dscp;
	u_int32_t	fwmark;
        u_int8_t	queue;
	u_int8_t	rsvd;
	u_int16_t	rsvd2;
} __attribute__((__packed__)) cmmd_l2tp_session_t;

#endif

