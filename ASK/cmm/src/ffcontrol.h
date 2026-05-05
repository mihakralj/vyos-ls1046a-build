/*
 * ffcontrol.c: Fast Forward Control
 *
 *	Copyright (C) 2007 Mindspeed Technologies, Inc.
 *	Copyright 2014-2016 Freescale Semiconductor, Inc.
 *	Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */
#ifndef __FFCONTROL_H__
#define __FFCONTROL_H__

	#define CONF_FILE_PATH	"/etc/config/fastforward"


	#define ATTR_ORIG_PORT_SRC_STR "orig_src_port"
	#define ATTR_ORIG_PORT_DST_STR "orig_dst_port"
	#define ATTR_REPL_PORT_SRC_STR "repl_src_port"
	#define ATTR_REPL_PORT_DST_STR "repl_dst_port"
	#define ATTR_ORIG_IPV4_SRC_STR "orig_src_ipv4"
	#define ATTR_ORIG_IPV4_DST_STR "orig_dst_ipv4"
	#define ATTR_REPL_IPV4_SRC_STR "repl_src_ipv4"
	#define ATTR_REPL_IPV4_DST_STR "repl_dst_ipv4"
	#define ATTR_ORIG_IPV6_SRC_STR "orig_src_ipv6"
	#define ATTR_ORIG_IPV6_DST_STR "orig_dst_ipv6"
	#define ATTR_REPL_IPV6_SRC_STR "repl_src_ipv6"
	#define ATTR_REPL_IPV6_DST_STR "repl_dst_ipv6"
	#define ATTR_MARK_STR "mark"
	#define ATTR_PROTO_STR "proto"
	#define ATTR_L3_PROTO_STR "l3 proto"


	typedef struct denyRule {
		struct denyRule * next;

		int type;					/*Netfilter attribut*/
		int value;					/*Value of the attribut*/
		int width;			/* 8, 16, 32 bits */
		int mask;
		struct in6_addr valueIpV6 ;
	} *denyRule_t, denyRule;

 	struct denyRuleList {
		struct denyRuleList * next;
		char name[20];
		denyRule_t rule;
	};

	struct cmm_cli;

	typedef struct asymFFRule {
		struct asymFFRule * next;

		int type;			/*Netfilter attribut*/
		int value;			/*Value of the attribut*/
		char strValue[16];		/*Value of the non-numerical atribute*/
		int width;			/* 8, 16, 32 bits */
		int mask;
	} *asymFFRule_t, asymFFRule;

	struct asymFFRuleList {
		struct asymFFRuleList * next;
		char name[20];
		asymFFRule_t rule;
	};

	int cmmFcIsConntrackAsymFastForwarded(struct nf_conntrack *ct);
	int cmmFcIsConntrack4Allowed(FCI_CLIENT *fci_handle, struct nf_conntrack *ct, struct RtEntry **rtEntryOrig);
	int cmmFcIsConntrack6Allowed(FCI_CLIENT *fci_handle, struct nf_conntrack * ct, struct RtEntry **rtEntryOrig);

	int cmmFcParser(char *confFilePath);
	int cmmCliInit(struct cmm_cli *ctx);
	void cmmCliExit(struct cmm_cli *ctx);

	int cmmFFControlProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);
	int cmmIPsecSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle);

static inline int cmmFcIsConntrackAllowed(FCI_CLIENT *fci_handle, struct nf_conntrack *ct, struct RtEntry **rtEntryOrig)
{
	if (nfct_get_attr_u8(ct, ATTR_ORIG_L3PROTO) == AF_INET)
		return cmmFcIsConntrack4Allowed(fci_handle, ct, rtEntryOrig);
	else
		return cmmFcIsConntrack6Allowed(fci_handle, ct, rtEntryOrig);
}

struct section_hdlr {
	char name[32];
	void * (*start)(int argc, char *argv[]);
	int (*option)(void *data, int argc, char *argv[]);
	void (*end)(void *data);
};

#define ARGC_MAX	6

struct rule_section_data
{
	union {
		denyRule_t rule;
		asymFFRule_t asym_rule;
	};

	char name[32];
	int last;
};

#ifdef WIFI_ENABLE
#define MAX_WIFI_FF_IFS 32	

struct wifi_ff_entry
{
	char   ifname[IFNAMSIZ];
	unsigned char   macaddr[6];
	unsigned short  used;
	unsigned int    vapid;
	unsigned short  direct_path_rx;
	unsigned short  wifi_guest;
	unsigned short  no_l2_itf;
	struct interface *wifi_ff_if;
};
#endif

#endif
