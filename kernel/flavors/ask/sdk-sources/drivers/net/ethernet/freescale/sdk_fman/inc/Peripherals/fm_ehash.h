/*
 *  Copyright (c) 2011, 2014 Freescale Semiconductor, Inc.
 *
 *  This program is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU General Public License
 *  as published by the Free Software Foundation; either version 2
 *  of the License, or (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
 *
 */
        
/**     
 * @file                fm_ehash.c     
 * @description         DPAA enhanced external hash functions
 */             

#ifndef FM_EHASH_H
#define FM_EHASH_H 1

#define MAX_KEY_LEN			56
#define MAX_EN_EHASH_EXT_ENTRY_SIZE	320 /* The extended entry also includes room for stats, Stats begins at the 256th byte address aligned again on 64 bytes */
#define MAX_EN_EHASH_ENTRY_SIZE		256
#define EN_EHASH_ENTRY_ALIGN		256	
#define TBLENTRY_OPC_ALIGN      	sizeof(uint32_t)
#define MAX_OPCODES			16

//enhanced external hash structure
//flags field in struct en_ehash_entry

#define SET_INVALID_ENTRY_64BIT(entry)  (entry |= (((uint64_t)1) << 63))
#define SET_INVALID_ENTRY(flags)	(flags |= (1 << 15))
#define SET_TIMESTAMP_ENABLE(flags) 	(flags |= (1 << 13))
#define SET_STATS_ENABLE(flags)		(flags |= (1 << 12))
#define SET_OPC_OFFSET(flags, offset)	(flags |= ((offset >> 2) << 6))
#define SET_PARAM_OFFSET(flags, offset)	(flags |= (offset >> 2))

#define GET_INVALID_ENTRY_64BIT(entry)  (entry &  (((uint64_t)1) << 63))
#define GET_INVALID_ENTRY(flags)	(flags & (1 << 15))
#define GET_TIMESTAMP_ENABLE(flags) 	((flags >> 13) & 1)  // only one bit is used to indicate TS
#define GET_STATS_ENABLE(flags)		(flags & (1 << 12))
#define GET_OPC_OFFSET(x)		(((x >> 6) & 0x1f) << 2)
#define GET_PARAM_OFFSET(x)		((x & 0x3f) << 2)

struct en_ehash_entry {
	union {
		struct {
			union {
				struct {
					uint16_t flags;
					uint16_t next_entry_hi;		//link to next entry (upper)
					uint32_t next_entry_lo;		//link to next entry (lower)
				};
				uint64_t next_entry;
			};
			uint8_t key[0];			//variable size key
		}__attribute__ ((packed));
		struct {
			uint8_t	hash_entry[MAX_EN_EHASH_ENTRY_SIZE];
			uint64_t packet_count;	//number of packet handled by flow
			uint64_t packet_bytes;	//number of bytes handled by flow
			uint32_t timestamp;		//flow timestamp
			uint32_t reserved;		//padding for 24 bytes dma
			uint32_t timestamp_counter;	//address of timestamp counter in muram
		}__attribute__((packed));
		uint8_t	hash_ext_entry[MAX_EN_EHASH_EXT_ENTRY_SIZE];
	}__attribute__ ((packed));
}__attribute__ ((packed));

/*** cumulative entry is to add multiple table entries of same hash bucket into one entry
   This is reduce the overhead of going through collisions.
   This structure can have the following fields:
     --flags: to specify info about the node,  some info can be:
        bit 1 (1<<0) : may indicate that this is invalid node
        bit 2 (1<<1) : may indicate that this cumulative entry contains the next entry
        bit 3 (1<<2) : indicate that this cumulative entry contains only one table entry and
                that table entry data is added inside this cumulative entry.
     --key size
     --number of key entries
     -- next cumulative entry address
     -- cumulative key
     -- array of table entries
**/

#define EN_CUMULATIVE_NODE_MAX_SIZE 256

struct en_cumulative_entry {
	union {
		struct {
			uint8_t flags;
			uint8_t num_key_entries;
			uint8_t key_size;
			uint8_t	tbl_entry_index;
			uint64_t next_entry_addr;
			uint8_t data[244];
		}__attribute__ ((packed));
		uint8_t node_data[EN_CUMULATIVE_NODE_MAX_SIZE];
	}__attribute__ ((packed));
}__attribute__ ((packed));

#define EN_CUMULATIVE_NODE  0x80
#define EN_INVALID_CUMULATIVE_NODE 0x40
#define EN_NEXT_CUMULATIVE_NODE  0x20
//#define EN_CRC_BASED_NODE 	0x10
#define EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE 8
#define EN_CU_FIXED_ELEMENTS_SIZE 12

struct en_cumulative_tbl_entry {
	struct en_cumulative_entry cumulative_entry;
	struct en_cumulative_tbl_entry *next_entry;
	struct en_cumulative_tbl_entry *prev_entry;
};

/* DPA Classifier table entry statistics */
#define STATS_VALID		(1 << 0)
#define TIMESTAMP_VALID		(1 << 1)
struct en_tbl_entry_stats {
	/* The total number of packets that have hit the entry */
	uint64_t	pkts;

	/* The total number of bytes that have hit the entry */
	uint64_t	bytes;
	
	/* timestamp */
	uint32_t 	timestamp;

	/* flags to indicate field validity */
	uint32_t flags;
};

//opcodes
#define	ENQUEUE_PKT		 0x01
#define	REPLICATE_PKT		 0x02
#define ENQUEUE_ONLY		 0x03
#define	UPDATE_ETH_RX_STATS 	 0x04
#define PREEMPTIVE_CHECKS_ON_PKT 0x05
#define PREEMPTIVE_CHECKS_ON_IPSEC_PKT 0x06
#define	STRIP_ETH_HDR		 0x11
#define	STRIP_ALL_VLAN_HDRS	 0x12
#define	STRIP_PPPoE_HDR		 0x14
#define	STRIP_L2_HDR		 0x17
#define STRIP_FIRST_VLAN_HDR	 0x18
#define	REMOVE_FIRST_IP_HDR	 0x19
#define	VALIDATE_IPSEC_ID	 0x1a
#define	UPDATE_TTL		 0x21
#define	UPDATE_SIP_V4		 0x22
#define	UPDATE_DIP_V4		 0x24
#define	UPDATE_HOPLIMIT		 0x29
#define	UPDATE_SIP_V6		 0x2A
#define	UPDATE_DIP_V6		 0x2C
#define	UPDATE_SPORT		 0x31
#define	UPDATE_DPORT		 0x32
#define	INSERT_L2_HDR		 0x41
#define	INSERT_VLAN_HDR		 0x42
#define	INSERT_PPPoE_HDR	 0x43
#define	INSERT_L3_HDR		 0x44
#define	REPLACE_PPPOE_HDR	 0x45
#define	NATPT_4to6		 0x51
#define	NATPT_6to4		 0x52
#define PROCESS_RTP_PAYLOAD	 0x61
#define PROCESS_RTCP_PAYLOAD	 0x62
#define	UPDATE_GLOBAL_STATS	 0x80

/* Parameters for opcode PREEMPTIVE_CHECKS_ON_PKT */
#define	PREEMPT_TX_VALIDATE    (1 << 0)
#define PREEMPT_DFBIT_HONOR    (1 << 1)
#define	PREEMPT_POLICE_PKT     (1 << 2)
#define PREEMPT_MATCH_DSCP     (1 << 3)
#define PREEMPT_REPLACE_DSCP   (1 << 4)

struct en_ehash_preempt_op {
	uint8_t mtu_offset;        /* Offset to Mtu in the param array */
	uint8_t OpMask;            /* Mask of operations to be performed PREEMPT_OP_IFSTATUS 0x01, PREEMPT_OP_FRAG 0x02 */
	uint8_t dscp_match_value;  /* valid if PREEMPT_MATCH_DSCP is set */
	uint8_t pp_no;             /* valid if PREEMPT_POLICE_PKT is set */
	uint8_t new_dscp_val;	   /* valid if PREEMPT_REPLACE_DSCP is set */
	uint8_t pad;
	uint16_t pad1;
}__attribute__ ((packed));

#define MAX_VLAN_PER_FLOW      2       /* Maximum VLANs to be verified for a flow */
#define MAX_SPI_PER_FLOW       16      /* Maximum SPIs to be verified for a flow */
#define VALIDATE_SPI 		( 0x1 << 0);
struct spi_info{
	uint32_t spi ;
	uint32_t fqid ;
}__attribute__ ((packed));

struct en_ehash_ipsec_preempt_op {
	uint8_t         op_flags;       /* To check SPI, VLAN, PPPoE session id */
	uint8_t         unused;        /* Number of spis configured to match */
	uint16_t	natt_arr_mask;  /* spi's enabled in each array index */
	uint16_t        pppoe_session_id; /* PPPoE Session to be verified (Future) */
	uint16_t	pad;
	struct spi_info  spi_param[MAX_SPI_PER_FLOW];
}__attribute__ ((packed));


//parameters for opcode	ENQUEUE_PKT 
#define EN_EHASH_DISABLE_FRAG	0xffff	/* set in mtu to disable fragmentation */
struct en_ehash_enqueue_param {
	uint16_t mtu;		/* mtu size in bytes for fragmentation */
	uint8_t hdr_xpnd_sz;	/* The headroom expansion size in bytes */
	uint8_t bpid;		/* buffer pool id for frag buffers */
	uint32_t fqid;		/* fqid to which pkt needs to be enqueued */
	union{
		struct{
			uint32_t rspid:8;       /* The relative storage profile ID when set
						   triggers usage of VSP in the Ucode during enque */
			uint32_t stats_ptr:24;	/* rspid 8 bits, interface egress stats 24 bits */
		};
		uint32_t word;
	};
	union{
		struct{
			uint32_t dscp_fq_enable:8; /* It gets enabled only when the connection does not 
							have qos connmark and dscp fq mapping enabled on 
							this connection tx interface. */
			uint32_t muram_frag_param_addr:24;
		};
		uint32_t word2;
	};
}__attribute__ ((packed));


#define EEH_RTP_SEND_FIRST_PACKET_TO_CP  0x0001
#define EEH_RTP_DUPLICATE_PKT_SEND_TO_CP  0x0002
#define EEH_RTP_ENABLE_VLAN_P_BIT_LEARN	  0x0004


struct en_ehash_rtprelay_param {
	uint32_t	rtpinfo_ptr;
	uint32_t	in_sock_stats_ptr;
	uint32_t	out_sock_stats_ptr;
	union
	{
		uint32_t src_ipv4_val;			//ipv4 address 
		uint32_t src_ipv6_val[4];		//ipv6 address
	}__attribute__ ((packed));
//	// 2 types of timestamp takeover: a) Fixed TS increment value, b) real timing using sampling frequency
	// in case (a) TimeStampIncr is configured
	uint32_t	   	TimeStampIncr; // fixed TS increment value
	uint32_t		SSRC_1; // configured SSRC_1 value;
	uint16_t		seq_base; // configured sequence base value
	uint16_t	   	egress_socketID; // used for generation of SSRC value
	uint8_t 		DTMF_PT[2];
	uint16_t		rtp_flags;
	// temp variables for functionality in ucode 
	uint16_t	seq_incr;
	uint32_t	chksum_ptr;
	uint32_t	rtp_hdr;
	uint32_t	ts_incr;
	uint32_t	cur_ts_msec;
	int32_t  	rtp_check;
}__attribute__ ((packed));

//update ether receive stats
//parameters for opcode REPLICATE_PKT
struct en_ehash_replicate_param {
	union {
		struct {
			uint16_t	rsvd; // num_mcast_members; // number of multicast members
			uint16_t	first_member_flow_addr_hi;
			uint32_t	first_member_flow_addr_lo; // first multicast member flow entry address
			};
		uint64_t first_member_flow_addr;
	};
	void	*first_listener_entry;
}__attribute__ ((packed));

//update ether receive stats
struct en_ehash_update_ether_rx_stats {
	uint32_t stats_ptr;
}__attribute__ ((packed));

//parameters for opcode	STRIP_FIRST_VLAN_HDR
struct en_ehash_strip_first_vlan_hdr {
	uint32_t stats_ptr;	//muram location address for interface statistics
	uint16_t        vlan_id; /* outer Vlan id to be verified for a flow*/
	uint16_t        pad;
}__attribute__ ((packed));

#define OP_SKIP_VLAN_VALIDATE	(1 << 0) /* This flag will be set in case of bridging with member as physical(non-vlan)
					interface. Bridge can accept the vlan packets on non-vlan interface, hence it should 
					be validated against vlan id */
#define OP_VLAN_FILTER_EN	(1 << 1) /* Vlan filtering is enabled in the routing path(ingress) */
#define OP_VLAN_FILTER_PVID_SET	(1 << 2) /* PVID is set on ingress interface. */

//parameters for opcode	STRIP_ALL_VLAN_HDRS
struct en_ehash_strip_all_vlan_hdrs {
	uint16_t        vlan_id[MAX_VLAN_PER_FLOW]; /* Vlan id s to be verified for a flow*/
	union {
		struct {
			uint32_t padding:2;	//padding to align structure to next 4 bytes boundary	
			uint32_t num_entries:6;	//number of stats entries
			uint32_t stats_ptr:24;	//pointer to stats table
		};
		uint32_t word;
	};
	uint8_t         op_flags;       /* To check vlan validation should be enabled or disabled*/
	uint8_t         pad;
	uint16_t         pad1;
	uint8_t stats_offsets[0];		//array of offsets into stats base
}__attribute__ ((packed));

//parameters for STRIP_PPPoE_HDR
struct en_ehash_strip_pppoe_hdr {
	uint32_t stats_ptr;	//muram location address for interface statistics
}__attribute__ ((packed));

//strip all l2 headers
struct en_ehash_strip_l2_hdrs {
	uint16_t        vlan_id[MAX_VLAN_PER_FLOW]; /* Vlan id s to be verified for a flow*/
	union {
		struct {
			uint32_t padding:2;	//padding to align structure to next 4 bytes boundary	
			uint32_t num_entries:6;	//number of stats entries
			uint32_t stats_ptr:24;	//pointer to stats table
		};
		uint32_t word;
	};
	uint8_t stats_offsets[0];		//array of offsets into stats base
}__attribute__ ((packed));

//parameters for opcode VALIDATE_IPSEC_ID
struct en_ehash_validate_ipsec {
	uint32_t reserved:16;
	uint32_t identifier:16;		//tunnel identifier opaque value
}__attribute__ ((packed));

//parameters for opcode UPDATE_SIP,DIP
struct en_ehash_update_ipv4_ip {
	uint32_t ip_v4;			//ipv4 address 
}__attribute__ ((packed));

struct en_ehash_update_ipv6_ip {
	uint8_t ip_v6[16];		//ipv6 address
}__attribute__ ((packed));

struct en_ehash_update_dscp {
	union {
		struct {
			uint32_t rsvd:24;
			uint32_t dscp_mark_value:6;  //dscp value
			uint32_t dscp_mark_flag:1;
			uint32_t padding:1;
		};
		uint32_t dscp;
	};
}__attribute__ ((packed));

//parameters for opcode UPDATE_SPORT, UPDATE_DPORT
struct en_ehash_update_port{
	uint16_t dport;			//dest port info
	uint16_t sport;			//source port info
}__attribute__ ((packed));

//parameters for opcode INSERT_L2_HDR
struct en_ehash_insert_l2_hdr {
	union {
		struct {
        		uint8_t replace:1;		//replace header, do not insert
			uint8_t header_padding:2;	//padding for L2 header field adjust
			uint8_t reserved:5;
			uint8_t stats_count;		//number of statistics offsets
			uint8_t reserved_1;
			uint8_t hdr_len;		//length of L2 header
		};
		uint32_t word;
	};
        uint8_t l2hdr[0];               //l2 hdr padded to next 4 bytes boundary
}__attribute__ ((packed));

struct en_ehash_insert_l2_hdr_stats {
	union {
        	struct {
                	uint32_t padding:2;     //padding to align structure to next 4 bytes boundary
                        uint32_t reserved:6; 
                        uint32_t stats_ptr:24;  //pointer to stats table
                };
                uint32_t word;
        };
        uint8_t stats_offsets[0];               //array of offsets into stats base
}__attribute__ ((packed));


/* parameters for opcode INSERT_VLAN_HDR */
struct en_ehash_insert_vlan_hdr {
	union {
		struct {
			uint32_t reserved:1;			/* unused bit */
			uint32_t dscp_vlanpcp_map_enable:1;	/* dscp vlanpcp map status bit */
			uint32_t num_hdrs:6;			/* number of headers to insert, If need more bits, look at this one */
			uint32_t statptr:24;			/* base of stats area or stats pointer, null no stats */
		}__attribute__ ((packed));
		uint32_t word;
	}__attribute__ ((packed));
	uint32_t vlanhdr[0];				/* array of vlan header includes TPID */
}__attribute__ ((packed));

struct en_ehash_insert_vlan_hdr_stats {
        uint8_t stats_offsets[1];               //array of offsets into stats base
}__attribute__ ((packed));

//default version, code and type fields 
#define PPPoE_VERSION   1
#define PPPoE_TYPE      1
#define PPPoE_CODE      0
//parameters for opcode INSERT_PPPoE_HDR - session offload
struct en_ehash_insert_pppoe_hdr {
	uint32_t stats_ptr;		//interface stats pointer
	union {
		struct {
			uint32_t version:4;
			uint32_t type:4;
			uint32_t code:8;
			uint32_t session_id:16;
		};
		uint32_t word;
	};
}__attribute__ ((packed));

//parameters for opcode REPLACE_PPPOE_HDR - session offload
struct en_ehash_replace_pppoe_hdr_params {
	uint8_t   destination_mac[6];
	uint8_t   source_mac[6];
	uint16_t  session_id;
	uint16_t  pad;
	uint32_t  fqid;
	uint32_t  stats_ptr;
}__attribute__ ((packed));

//parameters for opcode INSERT_L3 HDR 
#define TYPE_CUSTOM	0
#define TYPE_4o6	1
#define TYPE_6o4	2

#define IPID_STARTVAL	1
struct en_ehash_insert_l3_hdr {
	union {
		struct {
			uint8_t reserved:3;
			uint8_t calc_cksum:1;
			uint8_t qos:1;
			uint8_t df:1;
			uint8_t type:2;
			uint8_t hdr_len;
			uint16_t ipident;
		};
		uint32_t word;
	};
	union {
		struct {
			uint32_t route_dest_offset:8;
			uint32_t stats_ptr:24;
		};
		uint32_t word_1;
	};
        uint8_t l3hdr[0];               //l3 hdr padded to next 4 bytes boundary
}__attribute__ ((packed));

#define COPY_DSCP_OUTER_INNER (1<<24)

//remove tunnel header
struct en_ehash_remove_first_ip_hdr {
	uint32_t flags:8; /* currently only one bit is used for dscp copy */
	uint32_t stats_ptr:24;
}__attribute__ ((packed));


struct en_ehash_portinfo
{
	uint32_t reserved[3]; /* This overlaps with statistics info en_ehash_ifstats, it is not to be used.  */
	uint32_t port_info; /* LSbit is port status 0 => port down 1 => port up */
}__attribute__ ((packed));


struct en_ehash_ifportinfo
{
	struct en_ehash_portinfo rxpinfo;
	struct en_ehash_portinfo txpinfo;
}__attribute__ ((packed));

//basic statistics structure
struct en_ehash_stats
{
	uint64_t bytes;
	uint32_t pkts;
	uint32_t reserved; /* This 32 bit field overlaps struct en_ehash_portinfo, so it is not to be used */
}__attribute__ ((packed));

struct en_ehash_stats_with_ts {
	uint64_t bytes;
	uint32_t pkts;
	uint32_t pad;
	uint64_t timestamp;
}__attribute__ ((packed));

//statistics structure
struct en_ehash_ifstats {
	struct en_ehash_stats rxstats;
	struct en_ehash_stats txstats;
}__attribute__ ((packed));

#define STATS_WITH_TS	(1 << 7)
//statistics structure with timestamp
struct en_ehash_ifstats_with_ts {
	struct en_ehash_stats_with_ts rxstats;
	struct en_ehash_stats_with_ts txstats;
}__attribute__ ((packed));

//statistics table structure
struct en_ehash_stats_tbl {
	uint32_t table_indicator:1;	//set for table indication, valid in the first entry only
	uint32_t num_entries:7;		//number of entries in table, valid in the first entry only
	uint32_t stats_ptr:24;		//muRam address of en_ehash_stats member
}__attribute__ ((packed));

//NATPT structures
struct en_ehash_natpt_hdr {
	union {
		struct {
			uint32_t reserved:6;
			uint32_t tcu:1; //traffic class update
			uint32_t hlu:1; //hop limit update
			uint32_t hdrlen:8; //length of header
			uint32_t reserved1:16;
		}ip4to6;
		struct {
			uint16_t reserved:5;
			uint16_t ipu:1; //ipidentifier update
			uint16_t tou:1; //tos update
			uint16_t tlu:1; //ttl update
			uint16_t hdrlen:8; //length of header
			uint16_t ipident; //flow based ipidentifier
		}ip6to4;
		uint32_t word;
	};
        uint8_t l3hdr[0];               //l3 hdr padded to next 4 bytes boundary
}__attribute__ ((packed));

//4 to 6 
#define NATPT_TCU	(1 << 25)
#define NATPT_HLU	(1 << 24)
//6 to 4 
#define NATPT_IPU	(1 << 26)
#define NATPT_TOU	(1 << 25)
#define NATPT_TLU	(1 << 24)


//types for miss_action_type
#define EN_EHASH_MISS_ACTION_DROP       3
#define EN_EHASH_MISS_ACTION_NIA        1
#define EN_EHASH_MISS_ACTION_ENQUE      2
#define EN_EHASH_MISS_ACTION_DONE       0
//AD for this table, defines assuming a LE GPP core

/* Following structure is used to update SEC failure stats in ucode */
typedef struct en_SEC_failure_stats_s
{
	uint32_t icv_failures; /* incremented when a ucode receives ICV failure*/
	uint32_t hw_errs; /* when SEC returns hw errors */
	uint32_t CCM_AAD_size_errs;  /* When SEC recturns CCM AAD size error, increment it */
	uint32_t anti_replay_late_errs; /* when SEC returns ANTI REPLAY LATE errors */
	uint32_t anti_replay_replay_errs; /* when SEC returns ANTI REPLAY REPLAY errors */
	uint32_t seq_num_overflows; /* when SEC returns SEQ NUM OVERFLOW errors */
	uint32_t DMA_errs; /* when SEC returns DMA errors */
	uint32_t DECO_watchdog_timer_timedout_errs; /* when DECO watchdog timer timedout */
	uint32_t input_frame_read_errs; /*when SEC returns input frame read errors */
	uint32_t protocol_format_errs; /* when SEC returns protocol format errors */
	uint32_t ipsec_ttl_zero_errs; /* when SEC returns IPSEC TTL or hop limit either came in as 0 or decremented to 0 */
	uint32_t ipsec_pad_chk_failures; /* when SEC returns IPSEC padding check error found */
	uint32_t output_frame_length_rollover_errs; /* when SEC returns output frame length rollover */
	uint32_t tbl_buff_too_small_errs;/* when SEC returns table buffer too small */
	uint32_t tbl_buff_pool_depletion_errs; /* when SEC returns table buffer pool depletions */
	uint32_t output_frame_too_large_errs; /* when SEC returns output frame too large */
	uint32_t cmpnd_frame_write_errs; /* when SEC returns compound frame write errors */
	uint32_t buff_too_small_errs; /* when SEC returns buffer too small errors */
	uint32_t buff_pool_depletion_errs; /* when SEC returns buffer pool depeletion errors */
	uint32_t output_frame_write_errs; /* when SEC returns output frame write errors */
	uint32_t cmpnd_frame_read_errs; /* when SEC returns compound frame read errors */
	uint32_t prehdr_read_errs; /* when SEC returns when preheader read errors */
	uint32_t other_errs; /* when SEC returns errors other than above */
} __attribute__ ((packed)) en_SEC_failure_stats;

#define	MAX_VLAN_PCP	7
/*
* DSCP to VLAN PCP mapping structure. Each array element value is pcp
* mapped to the DSCP. DSCP 64 values divided into 8 groups.  0-7, 8-15, ..
* Each DSCP group mapped to one vlan pcp value.
*/
typedef struct en_dscp_vlanpcp_map_cfg_s
{
	uint8_t		dscp_vlanpcp[MAX_VLAN_PCP + 1];
} __attribute__ ((packed)) en_dscp_vlanpcp_map_cfg;
/*this structure is used to maintain a kind of global stats or 
  global info maintained between ucode and CP */
/* currently using for SEC failure statistics and dscp vlanpcp mapping , can be extended further based
  on requirement */
typedef struct en_exthash_global_mem_s
{
	en_dscp_vlanpcp_map_cfg	dscp_vlanpcp_map;
	en_SEC_failure_stats	SEC_failure_stats;
} __attribute__ ((packed)) en_exthash_global_mem;

int32_t ExternalHashGetSECfailureStats(en_SEC_failure_stats *stats);
int32_t ExternalHashResetSECfailureStats(void);
int32_t ExternalHashSetDscpVlanpcpMapCfg(en_dscp_vlanpcp_map_cfg *map);
int32_t ExternalHashGetDscpVlanpcpMapCfg(en_dscp_vlanpcp_map_cfg *map);

#ifdef EXCLUDE_FMAN_IPR_OFFLOAD 
struct en_exthash_node {
	union {
		struct {
			uint32_t table_base_hi:16;	//upper addr of 40 bit table phys addr
			uint32_t hash_bytes_offset:2;	//bytes of FMAN hash result to use
			uint32_t reserved_1:6;
			uint32_t key_size:6;		//size in bytes of key
			uint32_t miss_action_type:2;	//miss action
		}__attribute__ ((packed));
		uint32_t word_0;
	}__attribute__ ((packed));
	uint32_t table_base_lo;		//lower addr of 40 bit table phys addr
	union {
		struct {
			uint32_t global_mem_offset:12; /* Global memory space of structure en_exthash_global_mem
				is located in MURAM  at int_buf_pool_addr + (global_mem_offset *256)*/
			uint32_t hash_mask_bits:4; /* hash mask bits will be saved to table descriptor*/
					/* to get the hash mask value , ucode should do (1 << hash_mask_bits) -1 ,
						assumption is table is always power of 2 and 
						hash mask bits value will not exceed 15 (0xf)*/
			uint32_t int_buf_pool_addr:16; /* actual address is * 256 , value compressed from 3 bytes to 2 bytes*/
		}__attribute__ ((packed));;
		uint32_t word_1;
	}__attribute__ ((packed));
	union {
		union {
			uint32_t nia;		//nia  to use if type is EN_EHASH_MISS_ACTION_NIA
			uint32_t fqid;		//fqid to use if type is EN_EHASH_MISS_ACTION_ENQUE
		}__attribute__ ((packed));
		uint32_t word_2;
	}__attribute__ ((packed));
}__attribute__ ((packed));
#else
//table type
#define REASSEMBLY_TABLE                (1 << 3)
#define L4_TABLE                        (1 << 2)
#define L3_TABLE                        (1 << 1)
#define L2_TABLE                        (1 << 0)
struct en_exthash_node {
	union {
		struct {
			uint32_t table_base_hi:8;       //upper addr of 40 bit table phys addr
			uint32_t ipv4_ad_offset:8;      //ipv4 ad offset in this tree
			uint32_t hash_bytes_offset:3;   //offset in hash value
			uint32_t reserved:1;
			uint32_t table_type:4;
			uint32_t key_size:6;            //size in bytes of key
			uint32_t miss_action_type:2;    //miss action
		}__attribute__ ((packed));
		uint32_t word_0;
	}__attribute__ ((packed));
	uint32_t table_base_lo;         //lower addr of 40 bit table phys addr
	union {
		struct {
			uint32_t hash_mask_bits:4; /* hash mask bits will be saved to table descriptor*/
			/* to get the hash mask value , ucode should do (1 << hash_mask_bits) -1 ,
			   assumption is table is always power of 2 and 
			   hash mask bits value will not exceed 15 (0xf)*/
			uint32_t global_mem_offset:12; /* Global memory space of structure en_exthash_global_mem
											  is located in MURAM  at int_buf_pool_addr + global_mem_offset */
			uint32_t int_buf_pool_addr:16;
		}__attribute__ ((packed));;
		uint32_t word_1;
	}__attribute__ ((packed));
	union {
		uint32_t nia;   //nia if type is EN_EHASH_MISS_ACTION_NIA
		uint32_t fqid;  //fqid if type  EN_EHASH_MISS_ACTION_ENQUE
		uint32_t reassm_param;//pointer to reassembly parameters in muram, for ipv4/v6 reassembly tables
		uint32_t word_2;
	}__attribute__ ((packed));
}__attribute__ ((packed));

//ipv4/6 reassembly statistics
struct ip_reassembly_stats {
	uint64_t num_frag_pkts;
	uint64_t num_reassemblies;
	uint64_t num_completed_reassly;
	uint64_t num_sess_matches;
	uint64_t num_frags_too_small;
	uint64_t num_reassm_timeouts;
	uint64_t num_overlapping_frags;
	uint64_t num_too_many_frags;
	uint64_t num_failed_bufallocs;
	uint64_t num_failed_ctxallocs;
	uint64_t num_fatal_errors;
	uint64_t num_failed_ctxdeallocs;
	uint32_t reassm_count;
	uint32_t pad;
}__attribute__ ((packed));


#define IPR_MAX_SESSIONS        256
#define IPR_MAX_SESSSIZE        256
#define IPR_CTX_ALIGN           256
#define UCODE_MAX_TASKS         128
#define TASK_PRIV_MEMSIZE       32
#define IPR_CONTEXT_EOL         0xffffffff
struct ipr_context_info {
	uint8_t context_data[IPR_MAX_SESSIONS][IPR_MAX_SESSSIZE];
	uint8_t task_priv_data[UCODE_MAX_TASKS][TASK_PRIV_MEMSIZE];
	uint32_t next_free_ctx;
}__attribute__ ((packed));

#define MAX_REASSM_BUCKETS              (1 << 4)
//ipv4/6 reassembly info
struct ip_reassembly_params {
	uint32_t table_base_hi;         //reassembly table base
	uint32_t table_base_lo;
	struct ip_reassembly_stats stats; //stats
	uint32_t table_mask;            //hash mask
	uint32_t type;                  //type of table
	uint32_t ipr_timer;             //ipr timer location
	uint32_t timeout_val;           //reassembly timeout
	uint32_t timeout_fqid;          //fqid for timeout fragments
	uint32_t min_frag_size;         //min frag size other than last
	uint32_t reassem_bpid;          //buffer pool for re-assembly context
	uint32_t reassem_bsize;         //size of buffers in reassem_context
	uint32_t frag_bpid;             //buffer pool for re-assembly fragments
	uint32_t frag_bsize;            //size of buffers in reassem_bpid
	uint32_t reassly_dbg;           //debug area
	uint32_t context_info;          //context info structute ptr in muram
	uint32_t curr_sessions;         //curr reassembly sessions
	uint32_t txc_fqid;              //fqid for handling SG buffers
	uint32_t timer_tnum;            //timer task number
	uint32_t max_frags;             //max allowed frags per session
	uint32_t max_con_reassm;        //max concurrent reassemblies
	uint32_t bucket_base;           //base of bucket
	uint32_t bucket_lock[MAX_REASSM_BUCKETS];//locks for hash buckets, should be the last member
	uint32_t bucket_head[MAX_REASSM_BUCKETS];//head pointers of collision list
}__attribute__ ((packed));

//ipv4/v6 config and stats info required by cmm
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
	uint32_t table_mask;            //hash mask
	uint32_t ipr_timer;             //ipr timer location
	uint32_t timeout_val;           //reassembly timeout
	uint32_t timeout_fqid;          //fqid for timeout fragments
	uint32_t max_frags;             //max allowed sessions per session
	uint32_t min_frag_size;         //min frag size other than last
	uint32_t max_con_reassm;        //max concurrent reassemblies
	uint32_t reassem_bpid;          //buffer pool for re-assembly context
	uint32_t reassem_bsize;         //size of buffers in reassem_context
	uint32_t frag_bpid;             //buffer pool for re-assembly fragments
	uint32_t frag_bsize;            //size of buffers in reassem_bpid
	uint32_t timer_tnum;            //timer task number
	uint32_t reassly_dbg;           //debug area
	uint32_t curr_sessions;         //curr reassembly sessions
	uint32_t txc_fqid;              //fqid for handling SG buffers
};
#endif

#define EN_INTERNAL_BUFF_POOL_SIZE (256*128)
#define EN_EXTHASH_TBL_ALIGNMENT	256
#define TIMESTAMP_EN	(1 << 0)
#define STATS_EN	(1 << 1)
struct en_exthash_info {
	uint32_t flags;
	void *table_base;	//base of table in DDR
	void **pSpinlock;	//array of spin locks for each bucket
	void *h_Ad;		//handle to muRam holding AD
	struct en_exthash_node node; //ccnode
	uint32_t tablesize;	//number of bytes allocated for hash table
	uint32_t hashmask;	//mask to be used on hash value to get to bucket
	uint32_t keysize;	//size of key in bytes
	uint32_t hashshift;	//number of bytes to shift the fman hash result
	uint32_t dataMemId;	//memory partition id
	uint32_t dataLiodnOffset; //LIODN offsdet for access to ext hash table
	uint32_t num_keys; 	// number of keys in the table currently
	uint32_t max_collisions;//max collisions in the table that occured anytime upto now.
	void *pcd;		//pcd handle
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	uint32_t type;		//table type
	struct ip_reassembly_params *ip_reassem_info; //muram area,used in case of reassembly tables only 
#endif
};

struct en_exthash_tbl_entry {
	struct en_ehash_entry hashentry;
	struct en_exthash_tbl_entry *prev;
	struct en_exthash_tbl_entry *next;
	uint8_t	*replicate_params;
	union{
		uint8_t *enqueue_params;
		uint8_t *ipsec_preempt_params;
	};
};

struct en_exthash_bucket{
	uint64_t h;
	uint64_t pad;
};

static inline void display_mcast_member_tbl_entry(struct en_ehash_entry *entry);
static inline void display_tbl_ad(void *h_Ad)
{
	uint8_t *ptr;
	uint32_t ii;

	ptr = (uint8_t *)h_Ad;
	for (ii = 0; ii < sizeof(struct en_exthash_node); ii++) 
		printk("%02x ", *(ptr + ii));
	printk("\n");
}

static inline void display_ehashtbl_info(void *handle, char *func)
{
	struct en_exthash_info *info;
	
	info = (struct en_exthash_info *)handle;
	printk("%s::en_ehash info %p\n", func, info);
	printk("flags\t%08x\n", info->flags);
	printk("table_base\t%p\n", info->table_base);
	printk("hash mask\t%x\n", info->hashmask);
	printk("key size\t%d\n", info->keysize);
	printk("hash shift\t%d\n", info->hashshift);
	printk("table size\t%d\n", info->tablesize);
	printk("datamemId\t%d\n", info->dataMemId);
	printk("dataLiodnOffset\t%d\n", info->dataLiodnOffset);
	printk("num keys\t%d\n", info->num_keys);
	printk("max collisions \t%d\n", info->max_collisions);
	printk("Ad handle\t%p\n", info->h_Ad);
	display_tbl_ad(info->h_Ad);
}

static inline void disp_buf(void *buf, uint32_t size)
{
        uint8_t *ptr;
	uint32_t ii,jj=0;
	uint8_t buff[200];

        ptr = buf;
        for (ii = 0; ii < size; ii++) {
                
		if (ii && (ii % 16 == 0))
		{
			buff[jj] = 0;
			printk("%s\n",buff);
			jj = 0;
		}
		jj += sprintf(buff+jj, "%02x ", *ptr);
                ptr++;
        }
	buff[jj] = 0;
	printk("%s\n", buff);
}


static inline void *display_l2hdr_insert_opc(void *ptr)
{
	struct en_ehash_insert_l2_hdr *l2hdrins;
	struct en_ehash_insert_l2_hdr_stats *stats;
	uint32_t size;
	uint32_t word;
	uint32_t len;
	uint32_t padding;

	printk("opcode : INSERT_L2_HDR\n");
	l2hdrins = (struct en_ehash_insert_l2_hdr *)ptr;
	word = cpu_to_be32(l2hdrins->word);
	len = (word & 0xff);
	padding = ((word >> 29) & 3);
	if (word & (1 << 31))
		printk("Replace hdr, size %d\n", len);
	else
		printk("insert hdr, size %d\n", len);
        disp_buf(&l2hdrins->l2hdr[0], len);
	printk("stats count %d\n", l2hdrins->stats_count);
	size = (sizeof(struct en_ehash_insert_l2_hdr) + len + padding);
	//stats count
	len = ((word >> 16) & 0xff);
	if (len) {
		size += sizeof(struct en_ehash_insert_l2_hdr_stats);
		stats = (struct en_ehash_insert_l2_hdr_stats *)((uint8_t *)ptr + size);
		word = cpu_to_be32(stats->word);
		padding = (word >> 30);
		//get stats pointer
		word &= 0xffffffff;
		if (len == 1) {
			printk("stats pointer %x\n", word);
		} else {
			uint32_t ii;
			for (ii = 0; ii < len; ii++) {
				printk("offset %d stats ptr %lx\n", 
					stats->stats_offsets[ii], 
				  	(word + stats->stats_offsets[ii] * 
						sizeof(struct en_ehash_stats)));	
			}
		}
		size += padding;
	}
	return ((uint8_t *)ptr + size);
}

static inline void *display_enqparams_opc(void *ptr)
{
	struct en_ehash_enqueue_param *param;

	printk("opcode : ENQUEUE_PKT\n");
	param = (struct en_ehash_enqueue_param *)ptr;
	printk("mtu\t%d\n", cpu_to_be16(param->mtu));
	//printk("bpid\t%d\n", cpu_to_be16(param->bpid));
	printk("bpid\t%d\n", param->bpid);
	printk("fqid\t%d(0x%x)\n", 
		cpu_to_be32(param->fqid),
		cpu_to_be32(param->fqid));
	printk("stats_ptr\t%x\n", cpu_to_be32(param->stats_ptr));
	return ((uint8_t *)ptr + sizeof(struct en_ehash_enqueue_param));
}

static inline void *display_preemptchkipsecparams_opc(void *ptr)
{
	struct en_ehash_ipsec_preempt_op *param;
	int i;

	printk("opcode : PREEMPTIVE_CHECKS_ON_IPSEC_PKT\n");
	param = (struct en_ehash_ipsec_preempt_op *)ptr;
	printk(" Opcode Flags \t%x\n", param->op_flags);
	printk("NATT Mask\t%x\n", param->natt_arr_mask);
	printk("PPPoE session\t%d\n", param->pppoe_session_id);
	for (i = 0; i < MAX_SPI_PER_FLOW; i++)
	{
		printk("%d:SPI :\t%x - FQID :\t%x\n", i, param->spi_param[i].spi,  param->spi_param[i].fqid);
		
	}
		
	return ((uint8_t *)ptr + sizeof(struct en_ehash_ipsec_preempt_op));
}

static inline void *display_preemptchkparams_opc(void *ptr)
{
        struct en_ehash_preempt_op *param;

        printk("opcode : PREEMPTIVE_CHECKS_ON_PKT\n");
        param = (struct en_ehash_preempt_op *)ptr;
        printk("mtu offset\t%d\n", param->mtu_offset);
        printk("OP Mask\t%d\n", param->OpMask);
        printk("DSCP Match value\t%d\n", param->dscp_match_value);
        printk("Policer profile number \t%d(0x%x)\n",
                param->pp_no,param->pp_no);
        return ((uint8_t *)ptr + sizeof(struct en_ehash_preempt_op));
}


static inline void *display_pppoe_relay_opc(void *ptr)
{
  struct en_ehash_replace_pppoe_hdr_params *param;

  printk("opcode : REPLACE_PPPOE_HDR\n");
  param = (struct en_ehash_replace_pppoe_hdr_params *)ptr;
  printk("\nDestination Mac:");
  disp_buf(ptr, 6);
  printk("\nSource Mac:");
  disp_buf(ptr+6,6);
  printk("\nsession id:%d",cpu_to_be16(param->session_id));
  printk("\r\n");
  //printk("stats_ptr\t%x\n", cpu_to_be32(param->stats_ptr));
  return ((uint8_t *)ptr + sizeof(struct en_ehash_replace_pppoe_hdr_params));
}

static inline void *display_dscp(void *ptr)
{
	struct en_ehash_update_dscp *param = (struct en_ehash_update_dscp *)ptr;
	uint32_t word;

	word = cpu_to_be32(param->dscp);
	if(word & 2) {
		printk("dscp_mark flag\n");
		printk("dscp_mark %x size %lu\n", (word >> 2),sizeof(struct en_ehash_update_dscp));
	}
	else
		printk("NO DSCP marking for this FLOW\n");

	return (param + 1);
}

static inline void *display_ttlupdate_opc(void *ptr)
{
	printk("opcode : UPDATE_TTL\n");
	return display_dscp(ptr);
}

static inline void *display_hoplupdate_opc(void *ptr)
{
	printk("opcode : UPDATE_HOPLIMIT\n");
	return display_dscp(ptr);
}

static inline void *display_replicate_opc(void *ptr)
{
	struct en_ehash_replicate_param *param = (struct en_ehash_replicate_param *)ptr;
	struct en_exthash_tbl_entry *tbl_entry;
	int ii = 0;
	uint64_t phyaddr;

	printk("opcode : REPLICATE_PKT\n");
	printk("Group table entry addr HI : 0x%04x, LO : 0x%08x\n", 
		param->first_member_flow_addr_hi, param->first_member_flow_addr_lo);
	phyaddr = be16_to_cpu(param->first_member_flow_addr_hi);
	phyaddr = (phyaddr << 32);
	phyaddr |= be32_to_cpu(param->first_member_flow_addr_lo);
	while(1)//	for (ii=0; ii< be16_to_cpu(param->num_mcast_members); ii++)
	{
		printk("mcast member(%d) info:\n",ii);
		//tbl_entry = (struct en_exthash_tbl_entry *)XX_PhysToVirt(phyaddr);
		tbl_entry = (struct en_exthash_tbl_entry *)phys_to_virt(phyaddr);
		display_mcast_member_tbl_entry(&tbl_entry->hashentry);
		phyaddr =  be16_to_cpu(tbl_entry->hashentry.next_entry_hi);
		phyaddr = phyaddr << 32;
		phyaddr |=  be32_to_cpu(tbl_entry->hashentry.next_entry_lo);
		ii++;
		if (!phyaddr)	
			break;
	}
	printk("no. of mcast members : %d\n", ii);
	return (param + 1);
}

static inline void *display_strip_eth_hdr_opc(void *ptr)
{
	printk("opcode : STRIP_ETH_HDR\n");
	return ptr;
}

static inline void *display_update_eth_rx_stats_opc(void *ptr)
{
	struct en_ehash_update_ether_rx_stats *param;

	param = (struct en_ehash_update_ether_rx_stats *)ptr;
	printk("opcode : UPDATE_ETH_RX_STATS\n");
	printk("interface stats pointer %08x\n",
			cpu_to_be32(param->stats_ptr));
	return (param + 1);
}

static inline void *display_strip_allvlan_hdr_opc(void *ptr)
{
	struct en_ehash_strip_all_vlan_hdrs *param;
	uint32_t size;
	uint32_t word;
	uint32_t padding;
	uint32_t num_entries;
	uint32_t stats_ptr;

	param = (struct en_ehash_strip_all_vlan_hdrs *)ptr;
	printk("opcode : STRIP_ALL_VLAN_HDRS\n");
	printk(" Opcode Flags \t%x\n", param->op_flags);
	printk("VLAN Match 1\t%d\n", param->vlan_id[0]);
	printk("VLAN Match 2\t%d\n", param->vlan_id[1]);
	size = sizeof(struct en_ehash_strip_all_vlan_hdrs);
	word = cpu_to_be32(param->word);
	padding = (word >> 30);
	stats_ptr = (word & 0xffffff);
	num_entries = ((word >> 24) & 0x3f);
	printk("num entries %d, padding %d\n", num_entries, padding);
	if (stats_ptr) {
		if (num_entries > 1) {
			uint32_t ii;
			size += (num_entries + padding);
			for (ii = 0; ii < num_entries; ii++) {
				printk("offset %d, statsptr %lx\n",
					param->stats_offsets[ii],	
					(stats_ptr + 
					(param->stats_offsets[ii] * sizeof(struct en_ehash_stats))));
			} 
		} else {
			printk("stats ptr %x\n", stats_ptr);
		}
	}
	return ((uint8_t *)ptr + size);
}

static inline void *display_strip_pppoe_hdr_opc(void *ptr)
{
	struct en_ehash_strip_pppoe_hdr *param;

	param = (struct en_ehash_strip_pppoe_hdr *)ptr;
	printk("opcode : STRIP_PPPoE_HDR\n");
	printk("stats ptr %08x\n", cpu_to_be32(param->stats_ptr));
	return (param + 1);
}

static inline void *display_strip_l2_hdr_opc(void *ptr)
{
	struct en_ehash_strip_l2_hdrs *param;
	uint32_t size;
	uint32_t word;
	uint32_t padding;
	uint32_t num_entries;
	uint32_t stats_ptr;

	param = (struct en_ehash_strip_l2_hdrs *)ptr;
	printk("opcode : STRIP_L2_HDR\n");
	printk("VLAN Match 1\t%d\n", param->vlan_id[0]);
	printk("VLAN Match 2\t%d\n", param->vlan_id[1]);
	size = sizeof(struct en_ehash_strip_l2_hdrs);
	word = cpu_to_be32(param->word);
	padding = (word >> 30);
	stats_ptr = (word & 0xffffff);
	num_entries = ((word >> 24) & 0x3f);
	printk("num entries %d, padding %d\n", num_entries, padding);
	if (stats_ptr) {
		if (num_entries >= 1) {
			uint32_t ii;
			size += (num_entries + padding);
			for (ii = 0; ii < num_entries; ii++) {
				printk("offset %d, statsptr %lx\n",
						param->stats_offsets[ii],
						(stats_ptr +
						 (param->stats_offsets[ii] * sizeof(struct en_ehash_stats))));
			}
		} else {
			printk("stats ptr %x\n", stats_ptr);
		}
	}
	return ((uint8_t *)ptr + size);
}


static inline void *display_strip_firstvlan_opc(void *ptr)
{
	struct en_ehash_strip_first_vlan_hdr *param;

	param = (struct en_ehash_strip_first_vlan_hdr *)ptr;
	printk("opcode : STRIP_FIRST_VLAN_HDR\n");
	printk("interface stats pointer %08x\n",
			param->stats_ptr);
	printk("VLAN Match \t%d\n", param->vlan_id);
	return (param + 1);
}

static inline void *display_strip_first_iphdr(void *ptr)
{
	
	struct en_ehash_remove_first_ip_hdr *param;

	param = (struct en_ehash_remove_first_ip_hdr *)ptr;
	printk("opcode : REMOVE_FIRST_IP_HDR\n");
	printk("stats ptr %x\n", cpu_to_be32(param->stats_ptr));
	printk("dscp prpagation from outer to inner%d\n",cpu_to_be32((param->flags >> 24) & 1));
	return (param + 1);
}

static inline void *display_validate_ipsecid_opc(void *ptr)
{
	printk("opcode : VALIDATE_IPSEC_ID\n");
	return ptr;
}

static inline void *display_update_nat_ipv4_opc(void *ptr, uint32_t opcode)
{
	struct en_ehash_update_ipv4_ip *param;

	printk("opcode : %02x\n", opcode);
	param = (struct en_ehash_update_ipv4_ip *)ptr;
	if ((opcode & UPDATE_TTL) == UPDATE_TTL)
		printk("update TTL\n");
	if ((opcode & UPDATE_SIP_V4) == UPDATE_SIP_V4) {
		printk("update SIP V4 - SIP::%08x\n", htonl(param->ip_v4));
		param++;
	}
	if ((opcode & UPDATE_DIP_V4) == UPDATE_DIP_V4) {
		printk("update DIP V4 - DIP::%08x\n", htonl(param->ip_v4));
		param++;
	}
	return param;
}

static inline void *display_update_nat_ipv6_opc(void *ptr, uint32_t opcode)
{
	struct en_ehash_update_ipv6_ip *param;

	printk("opcode : %02x\n", opcode);
	param = (struct en_ehash_update_ipv6_ip *)ptr;
	if ((opcode & UPDATE_HOPLIMIT) == UPDATE_HOPLIMIT)
		printk("update UPDATE_HOPLIMIT\n");
	if ((opcode & UPDATE_SIP_V6) == UPDATE_SIP_V6) {
		printk("update SIP V6 - SIP::%pI6c", param->ip_v6);
		param++;
	}
	if ((opcode & UPDATE_DIP_V6) == UPDATE_DIP_V6) {
		printk("update DIP V6 - DIP::%pI6c", param->ip_v6);
		param++;
	}
	return param;
}

static inline void *display_update_nat_port_opc(void *ptr, uint32_t opcode)
{
	struct en_ehash_update_port *param;
	
	printk("opcode : %02x\n", opcode);
	param = (struct en_ehash_update_port *)ptr;
	if ((opcode & UPDATE_SPORT) == UPDATE_SPORT) {
		printk("update SPORT - SPORT::%d\n", htons(param->sport));
	}
	if ((opcode & UPDATE_DPORT) == UPDATE_DPORT) {
		printk("update DPORT - DPORT::%d\n", htons(param->dport));
	}
	param++;
	return param;
}

static inline void *display_vlanhdr_insert_opc(void *ptr)
{
	uint32_t ii;
	uint32_t stats_addr;
	struct en_ehash_insert_vlan_hdr *param;
	struct en_ehash_insert_vlan_hdr_stats *vlan_stats;
	uint32_t *vlanhdr;
	uint32_t num_hdrs;
	uint32_t padding;
	uint32_t size;

	printk("opcode : INSERT_VLAN_HDR\n");
	param = (struct en_ehash_insert_vlan_hdr *)ptr;
	ii = cpu_to_be32(param->word);
	num_hdrs = ((ii >> 24) & 0x3f);
	stats_addr = (ii & 0xffffff);
	printk("num headers %d stats ptr %x\n", 
		num_hdrs, stats_addr);
	padding = (ii >> 30);
	vlanhdr = &param->vlanhdr[0];
	for (ii = 0; ii < num_hdrs; ii++) {
		disp_buf(vlanhdr, sizeof(uint32_t));	
		printk("vlan hdr %08x\n", htonl(*vlanhdr));
		vlanhdr++;
	}
	size = (sizeof(struct en_ehash_insert_vlan_hdr) + 
		(num_hdrs * sizeof(uint32_t))); 
	if (stats_addr) {
		vlan_stats = (struct en_ehash_insert_vlan_hdr_stats *)vlanhdr;
		if (num_hdrs == 1)
			printk("stats ptr %x\n", stats_addr);
		else {
			for (ii = 0; ii < num_hdrs; ii++) 
				printk ("stats offset %d:: %lx\n", 
					vlan_stats->stats_offsets[ii],
				  	(stats_addr + (vlan_stats->stats_offsets[ii] * 
						sizeof(struct en_ehash_stats))));
			size += (padding + num_hdrs);	
		}
	}
	return(ptr + size);
}

static inline void *display_pppoehdr_insert_opc(void *ptr)
{
	uint32_t size;
	struct en_ehash_insert_pppoe_hdr *param;

	printk("opcode : INSERT_PPPoE_HDR\n");
	param = (struct en_ehash_insert_pppoe_hdr *)ptr;
	printk("version %d, type %d, code %d\n\n",
			param->version,
			param->type,
			param->code);
	printk("session id %d\n", param->session_id);
	printk("stats ptr %x\n", param->stats_ptr);
	size = sizeof(struct en_ehash_insert_pppoe_hdr);
	return ((uint8_t *)ptr + ALIGN(size, sizeof(uint32_t)));
}

static inline void *display_l3hdr_insert_opc(void *ptr)
{
	struct en_ehash_insert_l3_hdr *param;
	uint32_t word;

	printk("opcode : INSERT_L3_HDR - ");
	param = (struct en_ehash_insert_l3_hdr *)ptr;
	word = cpu_to_be32(param->word);
	switch ((word >> 24) & 3) {

		case TYPE_4o6:
			printk("TYPE_4o6\n");
			break;
		case TYPE_6o4:
			printk("TYPE_6o4\n");
			break;
		default:
			printk("TYPE_CUSTOM\n");
			break;
	}
	printk("hdr len %d\n", param->hdr_len);
	printk("df %d, qos %d, cs %d\n", 
		((word >> 28) & 1),
		((word >> 27) & 1),
		((word >> 29) & 1));
	printk("stats ptr %x\n", param->stats_ptr);
	disp_buf(&param->l3hdr[0], param->hdr_len);
	return ((uint8_t *)ptr + 
		ALIGN((sizeof(struct en_ehash_insert_l3_hdr) + 
					param->hdr_len), sizeof(uint32_t)));
}

static inline void *display_upd_glbl_stats_opc(void *ptr)
{
	printk("opcode : UPDATE_GLOBAL_STATS\n");
	return ptr;
}

static inline void *display_rtp_opc(void *ptr)
{
        struct en_ehash_rtprelay_param   *param;
        uint32_t  word;

        printk("opcode : PROCESS_RTP_PAYLOAD\n");
        param = (struct en_ehash_rtprelay_param *)ptr;

        word = be32_to_cpu(param->in_sock_stats_ptr);
        printk ("In Socket stats ptr: %x \n", word);
        word = be32_to_cpu(param->out_sock_stats_ptr);
        printk ("Out Socket stats ptr: %x \n", word);
        word = be32_to_cpu(param->rtpinfo_ptr);
        printk("RTP info ptr: %x\n", word);
        word = be32_to_cpu(param->src_ipv4_val);
        printk("src ipv4: %x\n", word);
        word = be32_to_cpu(param->TimeStampIncr);
        printk("TimestampiIncr :0x%x(%d) \n", word, word);
        word = be16_to_cpu(param->seq_base);
        printk("seq_base : %x(%d)\n", word, word);
        word = be16_to_cpu(param->egress_socketID);
        printk("egress_socketID : %x(%d)\n", word, word);
        printk("DTMF_PT[0] %d DTMF_PT[1] %d\n",param->DTMF_PT[0], param->DTMF_PT[1]);
		word = be16_to_cpu(param->rtp_flags);
	printk("Send first packet to CP : %s \n", 
			(word & EEH_RTP_SEND_FIRST_PACKET_TO_CP) ? "TRUE" : "FALSE");
	printk("Duplicate packet and send to CP: %s \n",
			(word & EEH_RTP_DUPLICATE_PKT_SEND_TO_CP) ? "TRUE" : "FALSE");

	printk("VLAN P bit learning feature : %s \n",
		(word & EEH_RTP_ENABLE_VLAN_P_BIT_LEARN) ? "Enabled" : "Disabled");
        return ((uint8_t*) ptr +(sizeof (struct en_ehash_rtprelay_param)));
}

static inline void *display_rtcp_opc(void *ptr)
{
        struct en_ehash_rtprelay_param   *param;
        uint32_t  word;

        printk("opcode : PROCESS_RTCP_PAYLOAD\n");
        param = (struct en_ehash_rtprelay_param *)ptr;

        word = be32_to_cpu(param->in_sock_stats_ptr);
        printk ("In Socket stats ptr: %x \n", word);
        word = be32_to_cpu(param->out_sock_stats_ptr);
        printk ("Out Socket stats ptr: %x \n", word);
        word = be32_to_cpu(param->rtpinfo_ptr);
        printk("RTP info ptr: %x\n", word);
        word = be32_to_cpu(param->src_ipv4_val);
        printk("src ipv4: %x\n", word);
		word = be32_to_cpu(param->SSRC_1);
		printk("SSRC_1	: %x (%d)\n", word, word);
		word = be16_to_cpu(param->rtp_flags);
		printk("Send first packet to CP : %s \n", 
			(word & EEH_RTP_SEND_FIRST_PACKET_TO_CP) ? "TRUE" : "FALSE");
		printk("Duplicate packet and send to CP: %s \n",
			(word & EEH_RTP_DUPLICATE_PKT_SEND_TO_CP) ? "TRUE" : "FALSE");
        return ((uint8_t*) ptr +(sizeof (struct en_ehash_rtprelay_param)));
}


static inline void display_mcast_member_tbl_entry(struct en_ehash_entry *entry)
{
	uint8_t *opc_ptr;
	uint8_t *param_ptr;
	uint64_t addr;
	uint32_t ii;
	uint16_t flags;
	flags = cpu_to_be16(entry->flags);
	opc_ptr = ((uint8_t *)entry + GET_OPC_OFFSET(flags));
	param_ptr = ((uint8_t *)entry + GET_PARAM_OFFSET(flags));
	addr = ((uint64_t)entry->next_entry_hi << 32);
	addr |= entry->next_entry_lo;
	printk("next_entry\t%p\n", (void *)addr);
	for (ii = 0; ii < MAX_OPCODES; ii++) {
		printk("opc ptr\t%p\n", opc_ptr);
		printk("param ptr\t%p\n", param_ptr);
		switch (*opc_ptr) {
			case 0:
				printk("end of opcodelist\n");
				return;
			case ENQUEUE_PKT:
				param_ptr = display_enqparams_opc(param_ptr);	
				return;
			case PREEMPTIVE_CHECKS_ON_PKT:
				param_ptr = display_preemptchkparams_opc(param_ptr);	
				break;
			case UPDATE_ETH_RX_STATS:	
				param_ptr = display_update_eth_rx_stats_opc(param_ptr);	
				break;
			case STRIP_ETH_HDR:          
				param_ptr = display_strip_eth_hdr_opc(param_ptr);
				break;
			case STRIP_ALL_VLAN_HDRS:
				param_ptr = display_strip_allvlan_hdr_opc(param_ptr);
				break;
			case STRIP_PPPoE_HDR:
				param_ptr = display_strip_pppoe_hdr_opc(param_ptr);
				break;
			case STRIP_FIRST_VLAN_HDR: 
				param_ptr = display_strip_firstvlan_opc(param_ptr);
				break;
			case STRIP_L2_HDR:
				param_ptr = display_strip_l2_hdr_opc(param_ptr);
				break;
			case REMOVE_FIRST_IP_HDR:
				param_ptr = display_strip_first_iphdr(param_ptr);
				break;
			case VALIDATE_IPSEC_ID:
				param_ptr = display_validate_ipsecid_opc(param_ptr);
				break;
			case UPDATE_TTL:
				param_ptr = display_ttlupdate_opc(param_ptr);
				break;
			case UPDATE_HOPLIMIT:
				param_ptr = display_hoplupdate_opc(param_ptr);
				break;
			case UPDATE_SIP_V4:
			case (UPDATE_SIP_V4 | UPDATE_TTL):
			case UPDATE_DIP_V4:
			case (UPDATE_DIP_V4 | UPDATE_TTL):
			case (UPDATE_SIP_V4 | UPDATE_DIP_V4):
			case (UPDATE_SIP_V4 | UPDATE_DIP_V4 | UPDATE_TTL):
				param_ptr = display_update_nat_ipv4_opc(param_ptr, *opc_ptr);
				break;
			case UPDATE_SIP_V6:
			case (UPDATE_SIP_V6 | UPDATE_HOPLIMIT): 
			case UPDATE_DIP_V6:
			case (UPDATE_DIP_V6 | UPDATE_HOPLIMIT):
			case (UPDATE_SIP_V6 | UPDATE_DIP_V6):
			case (UPDATE_SIP_V6 | UPDATE_DIP_V6 | UPDATE_HOPLIMIT):
				param_ptr = display_update_nat_ipv6_opc(param_ptr, *opc_ptr);
				break;
			case UPDATE_SPORT:
			case UPDATE_DPORT:
			case (UPDATE_SPORT | UPDATE_DPORT):
				param_ptr = display_update_nat_port_opc(param_ptr, *opc_ptr);
				break;
			case INSERT_L2_HDR:
				param_ptr = display_l2hdr_insert_opc(param_ptr);
				break;
			case INSERT_VLAN_HDR:
				param_ptr = display_vlanhdr_insert_opc(param_ptr);
				break;
			case INSERT_PPPoE_HDR:        
				param_ptr = display_pppoehdr_insert_opc(param_ptr);
				break;
			case INSERT_L3_HDR:
				param_ptr = display_l3hdr_insert_opc(param_ptr);
				break;
			case UPDATE_GLOBAL_STATS:
				param_ptr = display_upd_glbl_stats_opc(param_ptr);
				break;
			default:
				printk("unknown opcode %d\n", *opc_ptr);
			break;

		}
		opc_ptr++;
	}
}

static inline void *display_natpt_4to6_opc(void *ptr)
{
	struct en_ehash_natpt_hdr *param;
	uint32_t word;
	uint32_t hdrlen;

	printk("opcode : NATPT_4to6 - ");
	param = (struct en_ehash_natpt_hdr *)ptr;
	word = cpu_to_be32(param->word);
	hdrlen = ((word >> 16) & 0xff);
	printk("header length %d\n", hdrlen);
	if (word & NATPT_TCU)
		printk("update traffic class from ipv4 header\n");
	else
		printk("use traffic class from template\n");
	if (word & NATPT_HLU)
		printk("update HOP length from ipv4 header TTL\n");
	else
		printk("use HOP length from template\n");
	disp_buf(&param->l3hdr, hdrlen);	
	return ((uint8_t *)ptr + sizeof(struct en_ehash_natpt_hdr) + 
			((word >> 16) & 0xff));
}

static inline void *display_natpt_6to4_opc(void *ptr)
{
	uint32_t word;
	struct en_ehash_natpt_hdr *param;
	uint32_t hdrlen;

	printk("opcode : NATPT_6to4 - ");
	param = (struct en_ehash_natpt_hdr *)ptr;
	word = cpu_to_be32(param->word);
	printk("ipident %d\n", (word & 0xffff));
	hdrlen = ((word >> 16) & 0xff);
	printk("header length %d\n", hdrlen);
	if (word & NATPT_IPU)
		printk("use ipident from template\n");
	else
		printk("update ipident from flow\n");
	
	if (word & NATPT_TOU)
		printk("update tos from ipv6 header\n");
	else
		printk("use tos from template\n");

	if (word & NATPT_TLU)
		printk("update ttl value from ipv6 header\n");
	else
		printk("use ttl value from template\n");
	disp_buf(&param->l3hdr, hdrlen);	
	return ((uint8_t *)ptr + sizeof(struct en_ehash_natpt_hdr) + 
			((word >> 16) & 0xff));
}

static inline void display_ehash_tbl_entry(struct en_ehash_entry *entry, uint32_t keysize)
{
	uint8_t *opc_ptr;
	uint8_t *param_ptr;
	uint64_t addr;
	uint32_t ii;
	uint16_t flags;

	printk("entry %p\n", entry);
	disp_buf(entry, MAX_EN_EHASH_EXT_ENTRY_SIZE);
	flags = cpu_to_be16(entry->flags);
	opc_ptr = ((uint8_t *)entry + GET_OPC_OFFSET(flags));
	param_ptr = ((uint8_t *)entry + GET_PARAM_OFFSET(flags));
	if (GET_TIMESTAMP_ENABLE(flags)) {	
		printk("external timestamp addr %08x\n",
			cpu_to_be32(entry->timestamp_counter));
	} 
	if (GET_STATS_ENABLE(flags))
		printk("statistics\tenabled\n");
	printk("opc offset\t%d\n", GET_OPC_OFFSET(flags));
	printk("param offset\t%d\n", GET_PARAM_OFFSET(flags));
	addr = ((uint64_t)entry->next_entry_hi << 32);
	addr |= entry->next_entry_lo;
	printk("next_entry\t%p\n", (void *)addr);
	printk("key :: size %d\n", keysize);
	disp_buf(&entry->key[0], keysize);
	for (ii = 0; ii < MAX_OPCODES; ii++) {
		printk("opc ptr\t%p\n", opc_ptr);
		printk("param ptr\t%p\n", param_ptr);
		switch (*opc_ptr) {
			case 0:
				printk("end of opcodelist\n");
				return;
			case ENQUEUE_ONLY:
				printk("opcode:ENQUEUE ONLY\n");
				return;
			case ENQUEUE_PKT:
				param_ptr = display_enqparams_opc(param_ptr);	
				return;
			case PREEMPTIVE_CHECKS_ON_PKT:
				param_ptr = display_preemptchkparams_opc(param_ptr);	
				break;
			case PREEMPTIVE_CHECKS_ON_IPSEC_PKT:
				param_ptr = display_preemptchkipsecparams_opc(param_ptr);	
				break;
			case REPLICATE_PKT:
				param_ptr = display_replicate_opc(param_ptr);
				break;
			case UPDATE_ETH_RX_STATS:	
				param_ptr = display_update_eth_rx_stats_opc(param_ptr);	
				break;
			case STRIP_ETH_HDR:          
				param_ptr = display_strip_eth_hdr_opc(param_ptr);
				break;
			case STRIP_ALL_VLAN_HDRS:
				param_ptr = display_strip_allvlan_hdr_opc(param_ptr);
				break;
			case STRIP_PPPoE_HDR:
				param_ptr = display_strip_pppoe_hdr_opc(param_ptr);
				break;
			case STRIP_L2_HDR:
				param_ptr = display_strip_l2_hdr_opc(param_ptr);
				break;
			case STRIP_FIRST_VLAN_HDR: 
				param_ptr = display_strip_firstvlan_opc(param_ptr);
				break;
			case REMOVE_FIRST_IP_HDR:
				param_ptr = display_strip_first_iphdr(param_ptr);
				break;
			case VALIDATE_IPSEC_ID:
				param_ptr = display_validate_ipsecid_opc(param_ptr);
				break;
			case UPDATE_TTL:
				param_ptr = display_ttlupdate_opc(param_ptr);
				break;
			case UPDATE_HOPLIMIT:
				param_ptr = display_hoplupdate_opc(param_ptr);
				break;
			case UPDATE_SIP_V4:
			case (UPDATE_SIP_V4 | UPDATE_TTL):
			case UPDATE_DIP_V4:
			case (UPDATE_DIP_V4 | UPDATE_TTL):
			case (UPDATE_SIP_V4 | UPDATE_DIP_V4):
			case (UPDATE_SIP_V4 | UPDATE_DIP_V4 | UPDATE_TTL):
				param_ptr = display_update_nat_ipv4_opc(param_ptr, *opc_ptr);
				break;
			case UPDATE_SIP_V6:
			case (UPDATE_SIP_V6 | UPDATE_HOPLIMIT): 
			case UPDATE_DIP_V6:
			case (UPDATE_DIP_V6 | UPDATE_HOPLIMIT):
			case (UPDATE_SIP_V6 | UPDATE_DIP_V6):
			case (UPDATE_SIP_V6 | UPDATE_DIP_V6 | UPDATE_HOPLIMIT):
				param_ptr = display_update_nat_ipv6_opc(param_ptr, *opc_ptr);
				break;
			case UPDATE_SPORT:
			case UPDATE_DPORT:
			case (UPDATE_SPORT | UPDATE_DPORT):
				param_ptr = display_update_nat_port_opc(param_ptr, *opc_ptr);
				break;
			case INSERT_L2_HDR:
				param_ptr = display_l2hdr_insert_opc(param_ptr);
				break;
			case INSERT_VLAN_HDR:
				param_ptr = display_vlanhdr_insert_opc(param_ptr);
				break;
			case INSERT_PPPoE_HDR:        
				param_ptr = display_pppoehdr_insert_opc(param_ptr);
				break;
			case INSERT_L3_HDR:
				param_ptr = display_l3hdr_insert_opc(param_ptr);
				break;
			case UPDATE_GLOBAL_STATS:
				param_ptr = display_upd_glbl_stats_opc(param_ptr);
				break;
			case NATPT_4to6:
				param_ptr = display_natpt_4to6_opc(param_ptr);
				break;
			case NATPT_6to4:
				param_ptr = display_natpt_6to4_opc(param_ptr);
				break;
			case REPLACE_PPPOE_HDR:
				param_ptr = display_pppoe_relay_opc(param_ptr);
				break;
			case PROCESS_RTP_PAYLOAD:
				param_ptr = display_rtp_opc(param_ptr);
				break;
			case PROCESS_RTCP_PAYLOAD:
				param_ptr = display_rtcp_opc(param_ptr);
				break;
			default:
				printk("unknown opcode %d\n", *opc_ptr);
			break;

		}
		opc_ptr++;
	}
}

extern void *ExternalHashTableAllocEntry(void *h_HashTbl);
extern void ExternalHashTableEntryFree(void *entry);
extern int ExternalHashTableFmPcdHcSync(void *h_HashTbl);

#endif
