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
#ifndef __KEYTRACK_H__
#define __KEYTRACK_H__

#include <linux/version.h>
#include "list.h"
#include "conntrack.h"
#include "module_ipsec.h"


#define MAX_SA_BUNDLE 4
#ifdef IPSEC_FLOW_CACHE
#define FLOW_HASH_TABLE_SIZE	CONNTRACK_HASH_TABLE_SIZE	// uses HASH_CT macros for keys

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
struct flowi_tunnel {
        __u64                  tun_id;
};
#endif

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,14,0)
typedef struct {
	uid_t val;
} kuid_t;
#endif

struct flowi_common {
	int	flowic_oif;
	int	flowic_iif;
	__u32	flowic_mark;
	__u8	flowic_tos;
	__u8	flowic_scope;
	__u8	flowic_proto;
	__u8	flowic_flags;
	#define FLOWI_FLAG_ANYSRC		0x01
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3,14,0)
	#define FLOWI_FLAG_KNOWN_NH		0x02
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
	#define FLOWI_FLAG_SKIP_NH_OIF          0x04
#endif
#else
	#define FLOWI_FLAG_PRECOW_METRICS	0x02
	#define FLOWI_FLAG_CAN_SLEEP		0x04
#endif
	__u32	flowic_secid;
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
	struct flowi_tunnel flowic_tun_key;
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,14,0)
	kuid_t  flowic_uid;
#endif
};

union flowi_uli {
	struct {
		__be16	dport;
		__be16	sport;
	} ports;
	
	struct {
		__u8	type;
		__u8	code;
	} icmpt;
	
	struct {
		__le16	dport;
		__le16	sport;
	} dnports;
	
	__be32		spi;
	__be32		gre_key;
	
	struct {
		__u8	type;
	} mht;
};

struct flowi4 {
	struct flowi_common	__fl_common;
	#define flowi4_oif		__fl_common.flowic_oif
	#define flowi4_iif		__fl_common.flowic_iif
	#define flowi4_mark		__fl_common.flowic_mark
	#define flowi4_tos		__fl_common.flowic_tos
	#define flowi4_scope		__fl_common.flowic_scope
	#define flowi4_proto		__fl_common.flowic_proto
	#define flowi4_flags		__fl_common.flowic_flags
	#define flowi4_secid		__fl_common.flowic_secid
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
	#define flowi4_tun_key          __fl_common.flowic_tun_key
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(3,6,0)
	/* (saddr,daddr) must be grouped, same order as in IP header */
	__be32			saddr;
	__be32			daddr;
#else
	__be32			daddr;
	__be32			saddr;
#endif
	union flowi_uli		uli;
	#define fl4_sport		uli.ports.sport
	#define fl4_dport		uli.ports.dport
	#define fl4_icmp_type		uli.icmpt.type
	#define fl4_icmp_code		uli.icmpt.code
	#define fl4_ipsec_spi		uli.spi
	#define fl4_mh_type		uli.mht.type
	#define fl4_gre_key		uli.gre_key
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
}__attribute__((__aligned__(8)));
#else
};
#endif

struct flowi6 {
	struct flowi_common	__fl_common;
	#define flowi6_oif		__fl_common.flowic_oif
	#define flowi6_iif		__fl_common.flowic_iif
	#define flowi6_mark		__fl_common.flowic_mark
#if LINUX_VERSION_CODE <= KERNEL_VERSION(4,2,0)
	#define flowi6_tos		__fl_common.flowic_tos
#endif
	#define flowi6_scope		__fl_common.flowic_scope
	#define flowi6_proto		__fl_common.flowic_proto
	#define flowi6_flags		__fl_common.flowic_flags
	#define flowi6_secid		__fl_common.flowic_secid
	struct in6_addr		daddr;
	struct in6_addr		saddr;
	__be32			flowlabel;
	union flowi_uli		uli;
	#define fl6_sport		uli.ports.sport
	#define fl6_dport		uli.ports.dport
	#define fl6_icmp_type		uli.icmpt.type
	#define fl6_icmp_code		uli.icmpt.code
	#define fl6_ipsec_spi		uli.spi
	#define fl6_mh_type		uli.mht.type
	#define fl6_gre_key		uli.gre_key
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,14,0)
	__u32                   mp_hash;
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,9,0)
}__attribute__((__aligned__(8)));
#else
};
#endif

struct flowidn {
	struct flowi_common	__fl_common;
	#define flowidn_oif		__fl_common.flowic_oif
	#define flowidn_iif		__fl_common.flowic_iif
	#define flowidn_mark		__fl_common.flowic_mark
	#define flowidn_scope		__fl_common.flowic_scope
	#define flowidn_proto		__fl_common.flowic_proto
	#define flowidn_flags		__fl_common.flowic_flags
	__le16			daddr;
	__le16			saddr;
	union flowi_uli		uli;
	#define fld_sport		uli.ports.sport
	#define fld_dport		uli.ports.dport
};

struct flowi {
	union {
		struct flowi_common	__fl_common;
		struct flowi4		ip4;
		struct flowi6		ip6;
		struct flowidn		dn;
	} u;
#if LINUX_VERSION_CODE <= KERNEL_VERSION(4,2,0)
#ifndef ARCH_ARM32
	/*In kernel this structure has attribute
	 * __attribute__((__aligned__(BITS_PER_LONG/8)))
	 * This is making 64bit aligned for 64Bit arch
	 * as WA following reserved feild added.
	 */
	unsigned int res;
#endif
#endif
	#define flowi_oif	u.__fl_common.flowic_oif
	#define flowi_iif	u.__fl_common.flowic_iif
	#define flowi_mark	u.__fl_common.flowic_mark
	#define flowi_tos	u.__fl_common.flowic_tos
	#define flowi_scope	u.__fl_common.flowic_scope
	#define flowi_proto	u.__fl_common.flowic_proto
	#define flowi_flags	u.__fl_common.flowic_flags
	#define flowi_secid	u.__fl_common.flowic_secid
};


struct FlowEntry
{
	struct list_head list;
	struct list_head list_by_sa[MAX_SA_PER_FLOW];
	struct flowi  fl;
	unsigned char sa_nr;
	unsigned short family;
	unsigned short dir;
	unsigned short ignore_neigh;
	int flags;
	unsigned short sa_handle[MAX_SA_BUNDLE];
	unsigned int ref_count;
};


#endif /* IPSEC_FLOW_CACHE */
typedef struct netkey_sa_update_cmd{
	unsigned short sagd;
	unsigned short rsvd;
	unsigned long long bytes;
	unsigned long long packets;
}netkey_sa_update_cmd_t;

#define FLOW_DIR_IN     0  //  Input flow  for all local traffic
#define FLOW_DIR_OUT    1  // Output flow to be sent out with ipsec policy applied 
#define FLOW_DIR_FWD    2  // Forwarded flow for all traffic getting forwarded

#define FLOW_DIR_IN_BITVAL (1 << FLOW_DIR_IN)
#define FLOW_DIR_OUT_BITVAL (1 << FLOW_DIR_OUT)
#define FLOW_DIR_FWD_BITVAL (1 << FLOW_DIR_FWD)

#define SAQUERY_UNKNOWN_CMD	0
#define SAQUERY_ENABLE_CMD	1
#define SAQUERY_TIMER_CMD	2

#define NETKEY_CMD_SA_INFO_UPDATE	0x0a0c

/* Authentication algorithms */
#define SADB_AALG_NONE                  0
#define SADB_AALG_MD5HMAC               2
#define SADB_AALG_SHA1HMAC              3
#define SADB_X_AALG_SHA2_256HMAC        5
#define SADB_X_AALG_SHA2_384HMAC        6
#define SADB_X_AALG_SHA2_512HMAC        7
#define SADB_X_AALG_RIPEMD160HMAC       8
#define SADB_X_AALG_AES_XCBC_MAC        9
#define SADB_X_AALG_NULL                251     /* kame */
#define SADB_AALG_MAX                   251


/* Encryption algorithms */
#define SADB_EALG_NONE                  0
#define SADB_EALG_DESCBC                2
#define SADB_EALG_3DESCBC               3
#define SADB_X_EALG_CASTCBC             6
#define SADB_X_EALG_BLOWFISHCBC         7
#define SADB_EALG_NULL                  11
#define SADB_X_EALG_AESCBC              12
#define SADB_X_EALG_AESCTR              13
#define SADB_X_EALG_AES_CCM_ICV8        14
#define SADB_X_EALG_AES_CCM_ICV12       15
#define SADB_X_EALG_AES_CCM_ICV16       16
#define SADB_X_EALG_AES_GCM_ICV8        18
#define SADB_X_EALG_AES_GCM_ICV12       19
#define SADB_X_EALG_AES_GCM_ICV16       20
#define SADB_X_EALG_NULL_AES_GMAC	23


/* AESGCM - 18/19/20 */
#define SADB_X_EALG_CAMELLIACBC         22
/* private allocations should use 249-255 (RFC2407) */
#define SADB_X_EALG_SERPENTCBC  252     /* draft-ietf-ipsec-ciph-aes-cbc-00 */
#define SADB_X_EALG_TWOFISHCBC  253     /* draft-ietf-ipsec-ciph-aes-cbc-00 */

#define SADB_EALG_MAX                   253 /* last EALG */


extern pthread_mutex_t flowMutex;
#ifdef IPSEC_FLOW_CACHE
extern struct  list_head flow_table[FLOW_HASH_TABLE_SIZE];
#endif

int cmmKeyCatch(unsigned short fcode, unsigned short len, unsigned short *payload);
int cmmKeyEnginetoIPSec(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload);
int cmmIPSectoKeyEngine(FCI_CLIENT *fci_handle, unsigned short fcode, unsigned short len, unsigned short *payload);
#ifdef IPSEC_FLOW_CACHE
int cmmFlowKeyEngineRemove(FCI_CLIENT *fci_handle, struct FlowEntry *fentry);

struct FlowEntry *__cmmFlowFind(int family, const unsigned int *Saddr, const unsigned int *Daddr, unsigned short Sport, unsigned short Dport, unsigned char proto, unsigned short dir);
void __cmmFlowRemove(struct FlowEntry *flow);
struct FlowEntry *__cmmFlowAdd(int family, struct flowi *fl, unsigned char sa_nr, unsigned short *sa_handle, unsigned short dir);
struct FlowEntry *__cmmFlowGet(int family, const unsigned int *Saddr, const unsigned int *Daddr, unsigned short Sport, unsigned short Dport, unsigned char proto, unsigned short dir);
void __cmmFlowPut(struct FlowEntry *flow);
#endif
int cmmUpdateFlows(struct SATable *pSAEntry);
#ifndef IPSEC_FLOW_CACHE
int cmmUpdateFlowsWithNewSAInfo(struct SATable *pNewSAEntry,unsigned short old_xfrm_handle);
#endif /* IPSEC_FLOW_CACHE */

int cmmDPDSaQuerySetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
int cmmSaQueryTimerShow(struct cli_def * cli, const char *command, char *argv[], int argc);
int cmmDPDSAQUERYProcessClientCmd(u_int8_t *cmd_buf, u_int16_t *res_buf, u_int16_t *res_len);
#endif
