 /*
  * ffcontrol.c: Fast Forward Control
  *
  *  Copyright (C) 2007 Mindspeed Technologies, Inc.
  *  Copyright 2014-2016 Freescale Semiconductor, Inc.
  *  Copyright 2017-2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
  *
  */

#include "cmm.h"

#include <sys/ioctl.h>
#include <sys/socket.h>
#include <string.h>
#include <signal.h>
#include <ctype.h>

/* bits/sockaddr.h is glibc internal, use sys/socket.h (already included) */
#include <asm/types.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>

#include "ffbridge.h"
#include "pppoe.h"
#include "cmmd.h"
#include "fpp.h"
#include "module_lro.h"
#include "module_tx.h"

static struct denyRuleList *denyRules = NULL;
static struct asymFFRuleList *asymFFRules = NULL;
static struct rule_section_data section_data;
#ifdef WIFI_ENABLE
struct wifi_ff_entry glbl_wifi_ff_ifs[MAX_WIFI_FF_IFS];
#endif

/*****************************************************************
* cmmFcIsLoopbak()
*
*
******************************************************************/
static int cmmFcIsLoopback(FCI_CLIENT *fci_handle, struct nf_conntrack *ct, struct flow *flow_orig, struct RtEntry **rtEntryOrig)
{
	char saddr_buf[INET6_ADDRSTRLEN], daddr_buf[INET6_ADDRSTRLEN];
	int iif;

	/* Allow local->outside connections, iif = 0 because packets doesn't go through pre-routing hook */
	iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IIF);

	/* Filter local->local connections */
	if (iif == LO_IFINDEX)
		goto reject;

	/* Find the output route in case of local connections */
	if (!iif)
		flow_orig->iifindex = 0;

	if (!*rtEntryOrig)
	{
		*rtEntryOrig = __cmmRouteGet(flow_orig);
		if (!*rtEntryOrig)
			goto reject;
	}

	if ((*rtEntryOrig)->type == RTN_UNICAST)
		goto accept;

	/* Allow local connections passing through pre-routing hook */
	if (iif && (*rtEntryOrig)->type == RTN_LOCAL)
		goto accept;
	else
		goto reject;
accept:
	return 0;

reject:
	if (*rtEntryOrig)
	{
		____cmmRouteDeregister(*rtEntryOrig, "originator");
		*rtEntryOrig = NULL;
	}

	cmm_print(DEBUG_WARNING, "%s: conntrack local dst:%s src:%s\n", __func__,
			inet_ntop(flow_orig->family, flow_orig->sAddr, saddr_buf, INET6_ADDRSTRLEN),
			inet_ntop(flow_orig->family, flow_orig->dAddr, daddr_buf, INET6_ADDRSTRLEN));

	return 1;
}

/*****************************************************************
* cmmIsConntrack4Allowed()
*
*
******************************************************************/
#define MULTICAST(x)    (((x) & htonl(0xf0000000)) == htonl(0xe0000000))
int cmmFcIsConntrack4Allowed(FCI_CLIENT *fci_handle, struct nf_conntrack *ct, struct RtEntry **rtEntryOrig)
{
	struct denyRuleList * temp;
	denyRule_t	tempRule;
	unsigned int sAddr, dAddr;
	struct flow flow_orig;
	char saddr_buf[INET_ADDRSTRLEN], raddr_buf[INET_ADDRSTRLEN];

	sAddr = nfct_get_attr_u32(ct, ATTR_ORIG_IPV4_SRC);
	dAddr = nfct_get_attr_u32(ct, ATTR_REPL_IPV4_SRC);

	 /* Multicast connections are not forwarded */
	 if (MULTICAST(dAddr)) {
		cmm_print(DEBUG_WARNING, "%s: conntrack multicast dst:%s:%x src:%s:%x\n", __func__,
		     inet_ntop(AF_INET, &sAddr, saddr_buf, sizeof(saddr_buf)),sAddr,
		     inet_ntop(AF_INET, &dAddr, raddr_buf, sizeof(raddr_buf)),dAddr);
		goto refused;
	}

	flow_orig.family = AF_INET;
	flow_orig.sAddr = &sAddr;
	flow_orig.dAddr = &dAddr;
	flow_orig.iifindex = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IFINDEX);
#ifdef VLAN_FILTER
	flow_orig.underlying_vlan_id = nfct_get_attr_u16(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_VID);
#endif
#ifdef LS1043
 	flow_orig.underlying_iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_IIF);
#else
	flow_orig.underlying_iif = 0;
#endif
	
	flow_orig.fwmark = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_MARK);
	flow_orig.flow_flags = 0;

	if (cmmFcIsLoopback(fci_handle, ct, &flow_orig, rtEntryOrig)) {
		cmm_print(DEBUG_INFO, "%s: loopback connection refused\n", __func__);
		goto refused;
	}
	
	/*Go through each rule to see if it is allowed*/
	for(temp = denyRules ; temp != NULL ; temp = temp->next)
	{	
		for(tempRule = temp->rule ; tempRule != NULL ; tempRule = tempRule->next)
		{		
			unsigned int temp = 0;
#if __BYTE_ORDER == __BIG_ENDIAN
			unsigned int temp_shift = tempRule->value;
#endif

			const void *ret = nfct_get_attr(ct, tempRule->type);
			if(ret == NULL) { //If ret==NULL it means we are not able to get the informations we need from the conntrack, check next rule (default is accept)
				cmm_print(DEBUG_ERROR, "%s: can't get infos from conntrack, connection refused\n", __func__); 	
				break;
			}
			
			memcpy(&temp, ret, tempRule->width);
			temp &= tempRule->mask;

#if __BYTE_ORDER == __BIG_ENDIAN
			// bytes shift only in case of short type and big endian
			if ( tempRule->width == 2) {
                                temp_shift = tempRule->value << 16;
                        }
#endif
			
			
#if __BYTE_ORDER == __BIG_ENDIAN
			//cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, temp_shift, tempRule->mask, tempRule->width);
			if (memcmp(&temp, &temp_shift, tempRule->width)) {
#else
			//cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, tempRule->value, tempRule->mask, tempRule->width);
			if (memcmp(&temp, &tempRule->value, tempRule->width)) {
#endif
				//cmm_print(DEBUG_INFO, "%s: rule does not match\n", __func__); 
				break;
			} else {
				//cmm_print(DEBUG_INFO, "%s: rule's attribute matched, check next one(s)\n", __func__);
			}
		}

		/*
		 * We reach the end of the list meaning all the values matched
		 * So the conntrack is not allowed to be FastForwarded
		 */
		if(tempRule == NULL)
		{
				cmm_print(DEBUG_INFO, "%s: conntrack refused by rules\n", __func__);
				goto refused;
		}
		
		cmm_print(DEBUG_INFO, "%s: check next deny rule\n", __func__);
	}

	cmm_print(DEBUG_INFO, "%s: conntrack accepted\n", __func__);

	return 1;

refused:
	return 0;
}

/*****************************************************************
* cmmIsConntrack6Allowed()
*
*
******************************************************************/
int cmmFcIsConntrack6Allowed(FCI_CLIENT *fci_handle, struct nf_conntrack * ct, struct RtEntry **rtEntryOrig)
{
	struct denyRuleList * temp;
	denyRule_t	tempRule;
	struct flow flow_orig;
	const unsigned int *Saddr, *SaddrReply;

	/*Local connections are not Fast Forwarded*/
	Saddr = nfct_get_attr(ct, ATTR_ORIG_IPV6_SRC);
	SaddrReply = nfct_get_attr(ct, ATTR_REPL_IPV6_SRC);

	if ((SaddrReply[0] & ntohl(0xff000000)) == ntohl(0xff000000))
	{
		goto refused;
	}

	flow_orig.family = AF_INET6;
	flow_orig.sAddr = Saddr;
	flow_orig.dAddr = SaddrReply;
	flow_orig.iifindex = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_IFINDEX);
#ifdef VLAN_FILTER
	flow_orig.underlying_vlan_id = nfct_get_attr_u16(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_VID);
#endif
#ifdef LS1043
	flow_orig.underlying_iif = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_UNDERLYING_IIF);
#else
	flow_orig.underlying_iif = 0;
#endif
	flow_orig.fwmark = nfct_get_attr_u32(ct, ATTR_ORIG_COMCERTO_FP_MARK);
	flow_orig.flow_flags = 0;

	if (cmmFcIsLoopback(fci_handle, ct, &flow_orig, rtEntryOrig)) {
		cmm_print(DEBUG_INFO, "%s: local connection refused\n", __func__);
		goto refused;
	}

	/*Go through each rule to see if it is allowed*/
	for(temp = denyRules ; temp != NULL ; temp = temp->next)
	{
		for(tempRule = temp->rule ; tempRule != NULL ; tempRule = tempRule->next)
		{
			unsigned int temp = 0;
#if __BYTE_ORDER == __BIG_ENDIAN
			unsigned int temp_shift = tempRule->value;
#endif
            struct in6_addr *valueIpV6Ret, valueIpV6Tmp ;
		
            
            if ((tempRule->type == ATTR_ORIG_IPV6_SRC )|| (tempRule->type == ATTR_ORIG_IPV6_DST )
                || (tempRule->type == ATTR_REPL_IPV6_SRC ) || (tempRule->type == ATTR_REPL_IPV6_DST )) {

			    valueIpV6Ret = (struct in6_addr *)nfct_get_attr(ct, tempRule->type);
			    if(valueIpV6Ret == NULL)	{//If ret==NULL it means we are not able to get the informations we need from the conntrack, check next rule (default is accept)
				    cmm_print(DEBUG_ERROR, "%s: can't get infos from conntrack, jumps to next rule\n", __func__); 	
				    break;
			    }

			    if(16 != tempRule->width)	{//width of IPv6 address should be of 16 bytes
				    cmm_print(DEBUG_ERROR, "%s: Incorrect width in the rule,  jumps to next rule\n", __func__); 	
				    break;
			    }

			    memcpy(&valueIpV6Tmp.s6_addr[0], &valueIpV6Ret->s6_addr[0], tempRule->width);
			    
			    if (memcmp(&valueIpV6Tmp.s6_addr[0], &tempRule->valueIpV6.s6_addr[0], tempRule->width)) {
				    cmm_print(DEBUG_INFO, "%s: rule does not match\n", __func__); 
				    break;
			    } else {
				    cmm_print(DEBUG_INFO, "%s: rule's attribute matched, check next one(s)\n", __func__);
			    }
            } 
            else {
			const void *ret = nfct_get_attr(ct, tempRule->type);
			if(ret == NULL)	{//If ret==NULL it means we are not able to get the informations we need from the conntrack, check next rule (default is accept)
				cmm_print(DEBUG_ERROR, "%s: can't get infos from conntrack, jumps to next rule\n", __func__); 	
				break;
			}

			memcpy(&temp, ret, tempRule->width);
			temp &= tempRule->mask; 
#if __BYTE_ORDER == __BIG_ENDIAN
			// bytes shift only in case of short type and big endian
			if ( tempRule->width == 2) {
                                temp_shift = tempRule->value << 16;
                        }
#endif
			
#if __BYTE_ORDER == __BIG_ENDIAN
			cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, temp_shift, tempRule->mask, tempRule->width);
			if (memcmp(&temp, &temp_shift, tempRule->width)) {
#else
			cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, tempRule->value, tempRule->mask, tempRule->width);
			if (memcmp(&temp, &tempRule->value, tempRule->width)) {
#endif
				cmm_print(DEBUG_INFO, "%s: rule does not match\n", __func__); 
				break;
			} else {
				cmm_print(DEBUG_INFO, "%s: rule's attribute matched, check next one(s)\n", __func__);
			}
		}
		}

		/*
		 * We reach the end of the list meaning all the values matched
		 * So the conntrack is not allowed to be FastForwarded
		 */
		if(tempRule == NULL)
		{
				cmm_print(DEBUG_INFO, "%s: conntrack refused by rules\n", __func__);
				goto refused;
		}
		
		cmm_print(DEBUG_INFO, "%s: check next deny rule\n", __func__);
	}

	cmm_print(DEBUG_INFO, "%s: conntrack accepted\n", __func__);

	return 1;

refused:
	return 0;
}

/*****************************************************************
* cmmFcStop
*
*
******************************************************************/
int cmmFcStop(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	cli_print(cli, "Killing cmm ...\n");

	kill(0, SIGTERM);

	return CLI_OK;
}

/*****************************************************************
* cmmFcActivate
*
*
******************************************************************/
int cmmFcActivate(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int val;
	int family;

	if (argc < 1)
		goto usage;

	//Check if it is a digit. atoi() returns 0 if not digit
	if(! isdigit(*argv[0]))
		goto usage;

	val = atoi(argv[0]);
	if (globalConf.enable != val)
	{
		if (val == 0)
		{
			/*Reset Forward Engine*/
			cmmFeReset(globalConf.cli.fci_handle);
			globalConf.enable = val;
		}
		else if (val == 1)
		{
			/*Reset Forward Engine*/
			cmmFeReset(globalConf.cli.fci_handle);
			globalConf.enable = val;
		
			/*Get already existing ipv4 conntrack*/
			family = AF_INET;
			if (nfct_query(globalConf.ct.catch_handle, NFCT_Q_DUMP, (void *) &family) < 0)
				cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_DUMP) %s\n", __func__, strerror(errno));

			/*Get already existing ipv6 conntrack*/
			family = AF_INET6;
			if (nfct_query(globalConf.ct.catch_handle, NFCT_Q_DUMP, (void *) &family) < 0)
				cmm_print(DEBUG_ERROR, "%s: nfct_query(NFCT_Q_DUMP) %s\n", __func__, strerror(errno));
		}
		else
			goto usage;
	}

	return CLI_OK;

usage:
	cli_print(cli, "Usage: activate <0 1>");
	return CLI_OK;
}

/*****************************************************************
* cmmFcDesactivate
*
*
******************************************************************/
int cmmFcActivateShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	cli_print(cli, "%d", globalConf.enable);

	return CLI_OK;
}

/*****************************************************************
* cmmFcDesactivate
*
*
******************************************************************/
int cmmFcDebug(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	int val;
	int flag = 0;

	if (argc < 2)
		goto usage;

	if (strncmp(argv[0], "command", strlen(argv[0])) == 0)
		flag = DEBUG_COMMAND;
	else if (strncmp(argv[0], "error", strlen(argv[0])) == 0)
		flag = DEBUG_ERROR;
	else if (strncmp(argv[0], "warning", strlen(argv[0])) == 0)
		flag = DEBUG_WARNING;
	else if (strncmp(argv[0], "info", strlen(argv[0])) == 0)
		flag = DEBUG_INFO;
	else
		goto usage;

	//Check if it is a digit. atoi() returns 0 if not digit
	if(! isdigit(*argv[1]))
		goto usage;

	val = atoi(argv[1]);
	if (val == 0)
		globalConf.debug_level &= ~flag;
	else if (val == 1)
		globalConf.debug_level |= flag;
	else
		goto usage;

	return CLI_OK;

usage:
	cli_print(cli, "Usage: set debug <command error warning info> <0 1>");
	return CLI_OK;
}

/*****************************************************************
* cmmFcDebugShow
*
*
******************************************************************/
int cmmFcDebugShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	cli_print(cli, "command:\t%s", (globalConf.debug_level & DEBUG_COMMAND) ? "printed": "not printed");
	cli_print(cli, "error:  \t%s", (globalConf.debug_level & DEBUG_ERROR) ? "printed": "not printed");
	cli_print(cli, "warning:\t%s", (globalConf.debug_level & DEBUG_WARNING) ? "printed": "not printed");
	cli_print(cli, "info:   \t%s", (globalConf.debug_level & DEBUG_INFO) ? "printed": "not printed");

	return CLI_OK;
}

/*****************************************************************
* cmmFcAsymFFRuleAddAtrribut()
*
*
******************************************************************/
asymFFRule_t cmmFcAsymFFRuleAddAttribut(asymFFRule_t rule, int attributType, int attributValue, char *attrStrValue, int attributWidth, int mask)
{
	asymFFRule_t temp;

	temp = (asymFFRule_t) malloc(sizeof(asymFFRule));
	if (temp ==NULL)
		return rule;

	temp->next = rule;
	temp->type = attributType;
	temp->value = attributValue;
	{
		size_t len = strlen(attrStrValue);
		if (len >= sizeof(temp->strValue))
			len = sizeof(temp->strValue) - 1;
		memcpy(temp->strValue, attrStrValue, len);
		temp->strValue[len] = '\0';
	}
	temp->width = attributWidth;
	temp->mask = mask;

	return temp;
}

/*****************************************************************
* cmmFcAsymFFListAddRule()
*
*
******************************************************************/
struct asymFFRuleList * cmmFcAsymFFListAddRule(struct asymFFRuleList *list, char * ruleName, asymFFRule_t rule)
{
	struct asymFFRuleList * temp;

	temp = (struct asymFFRuleList *) malloc(sizeof(struct asymFFRuleList));
	if (temp == NULL)
		return list;

	temp->next = list;
	temp->rule = rule;
	{
		size_t len = strlen(ruleName);
		if (len >= sizeof(temp->name))
			len = sizeof(temp->name) - 1;
		memcpy(temp->name, ruleName, len);
		temp->name[len] = '\0';
	}
	return temp;
}

/*****************************************************************
* cmmFcAsymFFRulesShow()
*
*	Print rules on CLI
******************************************************************/
int cmmFcAsymFFRulesShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct asymFFRuleList *rulesList;
	struct asymFFRule *rule;

	for(rulesList = asymFFRules ; rulesList != NULL ; rulesList = rulesList->next)
	{
		cli_print(cli, "\n%s:", rulesList->name);
		for(rule = rulesList->rule ; rule != NULL ; rule = rule->next)
		{
			switch(rule->type)
			{
				case ATTR_ORIG_PORT_SRC:
					cli_print(cli, "\t%s: %d mask %x", ATTR_ORIG_PORT_SRC_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_ORIG_PORT_DST:
					cli_print(cli, "\t%s: %d mask %x", ATTR_ORIG_PORT_DST_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_ORIG_L4PROTO:
					if (rule->value == IPPROTO_TCP)
						cli_print(cli, "\t%s: tcp (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == IPPROTO_UDP)
						cli_print(cli, "\t%s: udp (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == IPPROTO_IPIP)
						cli_print(cli, "\t%s: ipip (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else
						cli_print(cli, "\t%s: unknown (%d) mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					break;

				case ATTR_L3PROTO:
					if (rule->value == AF_INET)
						cli_print(cli, "\t%s: ipv4 (%d)  mask %x", ATTR_L3_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == AF_INET6)
						cli_print(cli, "\t%s: ipv6 (%d)  mask %x", ATTR_L3_PROTO_STR, rule->value, rule->mask);

					break;
				case ATTR_ORIG_COMCERTO_FP_IIF:
					 cli_print(cli, "\tinterface: %s", rule->strValue);
					break;
				default:
					cli_print(cli, "\tERROR");
			}
		}
	}
	return CLI_OK;
}

/*****************************************************************
* cmmFcIsConntrackAsymFastForwarded()
*
*
******************************************************************/
int cmmFcIsConntrackAsymFastForwarded(struct nf_conntrack *ct)
{
	struct asymFFRuleList * temp;
	asymFFRule_t	tempRule;
	int iif;
        struct interface *itf;

	/*Go through each rule to see if it is allowed*/
	for(temp = asymFFRules ; temp != NULL ; temp = temp->next)
	{
		for(tempRule = temp->rule ; tempRule != NULL ; tempRule = tempRule->next)
		{
			unsigned int temp = 0;
#if __BYTE_ORDER == __BIG_ENDIAN
			unsigned int temp_shift = tempRule->value;
#endif

			const void *ret = nfct_get_attr(ct, tempRule->type);
			if(ret == NULL) { //If ret==NULL it means we are not able to get the informations we need from the conntrack, check next rule (default is accept)
				cmm_print(DEBUG_ERROR, "%s: can't get infos from conntrack, connection refused\n", __func__);
				break;
			}

			if(tempRule->type == ATTR_ORIG_COMCERTO_FP_IIF)
			{
				memcpy(&iif, ret, tempRule->width);
				itf = __itf_find(iif);
				if (!itf) {
					cmm_print(DEBUG_ERROR, "%s: can't get inteface details from conntrack\n", __func__);
					goto out;
				}
				if(!strcmp(itf->ifname,tempRule->strValue)) {
					cmm_print(DEBUG_INFO, "%s: interface attribute matched, check next atribute\n", __func__);
				}
				else {
					cmm_print(DEBUG_INFO, "%s: rule does not match\n", __func__); 
					break;
				}
			}
			else {
				memcpy(&temp, ret, tempRule->width);
				temp &= tempRule->mask;

#if __BYTE_ORDER == __BIG_ENDIAN
				// bytes shift only in case of short type and big endian
				if ( tempRule->width == 2) {
                        	        temp_shift = tempRule->value << 16;
                        	}
#endif

#if __BYTE_ORDER == __BIG_ENDIAN
				cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, temp_shift, tempRule->mask, tempRule->width);
				if (memcmp(&temp, &temp_shift, tempRule->width)) {
#else
				cmm_print(DEBUG_INFO, "%s: ct attr %x - rule value %x rule mask %x rule width %x\n", __func__, temp, tempRule->value, tempRule->mask, tempRule->width);
				if (memcmp(&temp, &tempRule->value, tempRule->width)) {
#endif
					cmm_print(DEBUG_INFO, "%s: rule does not match\n", __func__); 
					break;
				} else {
					cmm_print(DEBUG_INFO, "%s: rule's attribute matched, check next one(s)\n", __func__);
				}
			}
		}

		/*
		 * We reach the end of the list meaning all the values matched
		 * So the conntrack should be asymmetrically FastForwarded
		 */
		if(tempRule == NULL)
		{
				cmm_print(DEBUG_INFO, "%s: conntrack should be asym forwarded as per rules\n", __func__);
				goto asym_forward;
		}

		cmm_print(DEBUG_INFO, "%s: check next Asym Fastpath rule\n", __func__);
	}

	//cmm_print(DEBUG_INFO, "%s: conntrack accepted\n", __func__);
out:
	return 0;

asym_forward:
	return 1;
}


/*****************************************************************
* cmmFcRuleAddAtrribut()
*
*
******************************************************************/
denyRule_t cmmFcRuleAddAttribut(denyRule_t rule, int attributType, int attributValue, int attributWidth, int mask, const u_int8_t *attrValIpV6)
{
	denyRule_t temp;

	if ( (attributWidth == 16) && (attrValIpV6 == NULL) )
		return rule;


	temp = (denyRule_t) malloc(sizeof(denyRule));
	if (temp ==NULL)
		return rule;

	temp->next = rule;
	temp->type = attributType;
	temp->value = attributValue;
	temp->width = attributWidth;
	temp->mask = mask;

	if (attributWidth == 16)
		memcpy(&temp->valueIpV6.s6_addr[0], attrValIpV6, attributWidth) ;

	return temp;
}

/*****************************************************************
* cmmFcListAddRule()
*
*
******************************************************************/
struct denyRuleList * cmmFcListAddRule(struct denyRuleList *list, char * ruleName, denyRule_t rule)
{
	struct denyRuleList * temp;

	temp = (struct denyRuleList *) malloc(sizeof(struct denyRuleList));
	if (temp == NULL)
		return list;

	temp->next = list;
	temp->rule = rule;
	{
		size_t len = strlen(ruleName);
		if (len >= sizeof(temp->name))
			len = sizeof(temp->name) - 1;
		memcpy(temp->name, ruleName, len);
		temp->name[len] = '\0';
	}
	return temp;
}

/*****************************************************************
* cmmFcRulesShow()
*
*		Print rules on CLI
******************************************************************/
int cmmFcRulesShow(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	struct denyRuleList *rulesList;
	struct denyRule *rule;
	char *ipv4Address;
        struct in_addr tmp;
	char ipv6Address[INET6_ADDRSTRLEN];
	

	for(rulesList = denyRules ; rulesList != NULL ; rulesList = rulesList->next)
	{
		cli_print(cli, "\n%s:", rulesList->name);
		for(rule = rulesList->rule ; rule != NULL ; rule = rule->next)
		{
			switch(rule->type)
			{
				case ATTR_ORIG_PORT_SRC:
					cli_print(cli, "\t%s: %d mask %x", ATTR_ORIG_PORT_SRC_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_ORIG_PORT_DST:
					cli_print(cli, "\t%s: %d mask %x", ATTR_ORIG_PORT_DST_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_REPL_PORT_SRC:
					cli_print(cli, "\t%s: %d mask %x", ATTR_REPL_PORT_SRC_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_REPL_PORT_DST:
					cli_print(cli, "\t%s: %d mask %x", ATTR_REPL_PORT_DST_STR, ntohs(rule->value), rule->mask);
					break;

				case ATTR_MARK:
					cli_print(cli, "\t%s: %d mask %x", ATTR_MARK_STR, rule->value, rule->mask);
					break;

				case ATTR_ORIG_IPV4_SRC:
                                        tmp.s_addr = (unsigned int)rule->value ;
					ipv4Address = inet_ntoa(tmp) ;
					if (ipv4Address == NULL){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_ORIG_IPV4_SRC_STR, ipv4Address, rule->mask);
					break;

				case ATTR_ORIG_IPV4_DST:
                                        tmp.s_addr = (unsigned int)rule->value ;
					ipv4Address = inet_ntoa(tmp) ;
					if (ipv4Address == NULL){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_ORIG_IPV4_DST_STR, ipv4Address, rule->mask);
					break;

				case ATTR_REPL_IPV4_SRC:
                                        tmp.s_addr = (unsigned int)rule->value ;
					ipv4Address = inet_ntoa(tmp) ;
					if (ipv4Address == NULL){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_REPL_IPV4_SRC_STR, ipv4Address, rule->mask);
					break;

				case ATTR_REPL_IPV4_DST:
                                        tmp.s_addr = (unsigned int)rule->value ;
					ipv4Address = inet_ntoa(tmp) ;
					if (ipv4Address == NULL){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_REPL_IPV4_DST_STR, ipv4Address, rule->mask);
					break;

				case ATTR_ORIG_IPV6_SRC:
					if (! inet_ntop(AF_INET6, ((struct in6_addr *)&(rule->valueIpV6)), ipv6Address, INET6_ADDRSTRLEN)){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_ORIG_IPV6_SRC_STR, ipv6Address, rule->mask);
					break;

				case ATTR_ORIG_IPV6_DST:
					if (! inet_ntop(AF_INET6, ((struct in6_addr *)&(rule->valueIpV6)), ipv6Address, INET6_ADDRSTRLEN)){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						} 
					cli_print(cli, "\t%s: %s mask %x", ATTR_ORIG_IPV6_DST_STR, ipv6Address, rule->mask);
					break;

				case ATTR_REPL_IPV6_SRC:
					if (! inet_ntop(AF_INET6, ((struct in6_addr *)&(rule->valueIpV6)), ipv6Address, INET6_ADDRSTRLEN)){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_REPL_IPV6_SRC_STR, ipv6Address, rule->mask);
					break;

				case ATTR_REPL_IPV6_DST:
					if (! inet_ntop(AF_INET6, ((struct in6_addr *)&(rule->valueIpV6)), ipv6Address, INET6_ADDRSTRLEN)){
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing rule IPv4 address");
							break;
						}
					cli_print(cli, "\t%s: %s mask %x", ATTR_REPL_IPV6_DST_STR, ipv6Address, rule->mask);
					break;

				case ATTR_ORIG_L4PROTO:
				case ATTR_REPL_L4PROTO: 	
					if (rule->value == IPPROTO_TCP)
						cli_print(cli, "\t%s: tcp (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == IPPROTO_UDP)
						cli_print(cli, "\t%s: udp (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == IPPROTO_IPIP)
						cli_print(cli, "\t%s: ipip (%d)  mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					else 
						cli_print(cli, "\t%s: unknown (%d) mask %x", ATTR_PROTO_STR, rule->value, rule->mask);
					break;

				case ATTR_L3PROTO:
					if (rule->value == AF_INET)
						cli_print(cli, "\t%s: ipv4 (%d)  mask %x", ATTR_L3_PROTO_STR, rule->value, rule->mask);
					else if (rule->value == AF_INET6)
						cli_print(cli, "\t%s: ipv6 (%d)  mask %x", ATTR_L3_PROTO_STR, rule->value, rule->mask);

					break;
				default:
					cli_print(cli, "\tERROR");
			}
		}
	}
	return CLI_OK;
}

static int section_logging_option_hdlr(void *data, int argc, char **argv)
{
	char *option = argv[0];
	char *value = argv[1];

	if (!strcasecmp(option, "file"))
	{
		globalConf.logFile = fopen(value, "a");
		if (!globalConf.logFile)
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Opening logfile %s returned error %s.\n", value, strerror(errno));
			goto err;
		}

		globalConf.log_level |= DEBUG_STDERR;

		pthread_mutex_init(&globalConf.logMutex, NULL);

		setlinebuf(globalConf.logFile);
	}
	else if (!strcasecmp(option, "command"))
	{
		if (atoi(value) == 1)
			globalConf.log_level |= DEBUG_COMMAND;
	}
	else if (!strcasecmp(option, "error"))
	{
		if (atoi(value) == 1)
			globalConf.log_level |= DEBUG_ERROR;
	}
	else if (!strcasecmp(option, "warning"))
	{
		if (atoi(value) == 1)
			globalConf.log_level |= DEBUG_WARNING;
	}
	else if (!strcasecmp(option, "info"))
	{
		if (atoi(value) == 1)
			globalConf.log_level |= DEBUG_INFO;
	}
	else
		goto err;

	return 0;

err:
	return -1;
}


static int section_vlan_option_hdlr(void *data, int argc, char **argv)
{
	char *option = argv[0];
	char *value = argv[1];

	if (!strcasecmp(option, "policy"))
	{
		if (!strcasecmp(value, "allow"))
		{
			globalConf.vlan_policy = ALLOW;
		}
		else if (!strcasecmp(value, "prohibit"))
		{
		      	globalConf.vlan_policy = PROHIBIT;
		}
		else if (strcasecmp(value, "manual"))
		{
			globalConf.vlan_policy = MANUAL;
		}
		else
			goto err;
	}
	else
		goto err;

	return 0;

err:
	return -1;
}

static int section_tun_option_hdlr(void *data, int argc, char **argv)
{
	char *option = argv[0];
	char *value = argv[1];

	if (!strcasecmp(option, "proto"))
	{
		if (!strcasecmp(value, "IPIP"))
		{
			globalConf.tun_proto = IPPROTO_IPIP;
			globalConf.tun_family = AF_INET6;
			globalConf.enable_sam_itfs = 1; /* enabling this 4rd support for SAM interfaces feature */
		}
		else
			goto err;
	}
	else
		goto err;

	return 0;

err:
	return -1;
}

#ifdef WIFI_ENABLE
static void *section_wifi_fastforward_start_hdlr(int argc, char **argv)
{
	int i;

	/* Get Free WiFi fastforward entry */
	for (i = 0; i < MAX_WIFI_FF_IFS; i++)
	{
		if (!glbl_wifi_ff_ifs[i].used)
		{
			glbl_wifi_ff_ifs[i].used = 1;
			glbl_wifi_ff_ifs[i].vapid = i;

			return &glbl_wifi_ff_ifs[i];
		}
	}

	return NULL;
}


static int section_wifi_fastforward_option_hdlr(void *data, int argc, char **argv)
{
	struct wifi_ff_entry *wifi_if = data;
	char *option = argv[0];
	char *value = argv[1];

	if (!wifi_if)
		goto err;

	if (!strcasecmp(option, "ifname"))
	{
		size_t len = strlen(value);
		if (len >= IFNAMSIZ)
			len = IFNAMSIZ - 1;
		memcpy(wifi_if->ifname, value, len);
		wifi_if->ifname[len] = '\0';

		cmm_print(DEBUG_ERROR, "cmmFcParser: WiFi name: %s\n", wifi_if->ifname);
	}
	else if (!strcasecmp(option, "direct_path_rx"))
	{
		wifi_if->direct_path_rx = atoi(value);
	}
	else if (strcasecmp(option, "wifi_guest") == 0)
	{
		wifi_if->wifi_guest = atoi(value);
	}
	else if (strcasecmp(option, "no_l2_interface") == 0)
	{
		wifi_if->no_l2_itf = atoi(value);
	}
	else
		goto err;

	return 0;

err:
	return -1;
}
#endif

static void *section_rule_start_hdlr(int argc, char **argv)
{
	struct rule_section_data *section = &section_data;
	size_t len = strlen(argv[0]);

	if (len >= sizeof(section->name))
		len = sizeof(section->name) - 1;
	memcpy(section->name, argv[0], len);
	section->name[len] = '\0';
	section->rule = NULL;
	section->last = 0;

	return section;
}

static void section_fastforward_end_hdlr(void *data)
{
	struct rule_section_data *section = data;

	if (!section)
		return;
	denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);
}

static int section_fastforward_option_hdlr(void *data, int argc, char **argv)
{
	struct rule_section_data *section = data;
	char *option = argv[0];
	char *value = argv[1];
	unsigned int attrType, attrValue;
	u_int8_t *attrValueV6 = NULL;
	int attrWidth;
	unsigned int mask;
	denyRule_t copy_rule;
	denyRule_t head_rule;

	if (!section)
		goto err;

	if (section->last)
		goto err;

	// Scan options
	if (!strcmp(option, "orig_src_port"))
	{
		attrType = ATTR_ORIG_PORT_SRC;
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);

		mask = 0x0000FFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "orig_dst_port"))
	{
		attrType = ATTR_ORIG_PORT_DST;
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		mask = 0x0000FFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "repl_src_port"))
	{
		attrType = ATTR_REPL_PORT_SRC;
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		mask = 0x0000FFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "repl_dst_port"))
	{
		attrType = ATTR_REPL_PORT_DST;
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		mask = 0x0000FFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "mark"))
	{
		if (argc < 3)
			goto err;

		attrType = ATTR_MARK;
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned int);

		if (!strcmp(argv[2], "mask"))
		{
			if (argc < 4)
				goto err;

			mask = atoi(argv[3]);
		}
		else
		{
			mask = 0xFFFFFFFF;
		}

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "proto"))
	{
		if (!strcmp(value, "tcp"))
		{
			attrValue = IPPROTO_TCP;

			attrType = ATTR_ORIG_L4PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else if (!strcmp(value, "udp"))
		{
			attrValue = IPPROTO_UDP;

			attrType = ATTR_ORIG_L4PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else if (!strcmp(value, "ipv4"))
		{
			attrValue = AF_INET /*ETH_P_IP*/;
			attrType = ATTR_L3PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else if (!strcmp(value, "ipv6"))
		{
			attrValue = AF_INET6 /*ETH_P_IPV6*/;

			attrType = ATTR_L3PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "port"))
	{
		attrValue = atoi(value);
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		head_rule = section->rule;

		// Port option. It is like 4 differents rules
		attrType = ATTR_ORIG_PORT_SRC;
		mask = 0x0000FFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_ORIG_PORT_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_PORT_SRC;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		// The last one will be added to the list later
		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_PORT_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);

		section->last = 1;
	}
	else if (!strcmp(option, "orig_src_ipv4"))
	{
		struct in_addr addr;

		if (!inet_aton(value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValue = addr.s_addr;

		attrWidth = sizeof(unsigned int);
		attrType = ATTR_ORIG_IPV4_SRC;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "orig_dst_ipv4"))
	{
		struct in_addr addr;

		if (!inet_aton(value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValue = addr.s_addr;

		attrWidth = sizeof(unsigned int);
		attrType = ATTR_ORIG_IPV4_DST;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "reply_src_ipv4"))
	{
		struct in_addr addr;

		if (!inet_aton(value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValue = addr.s_addr;

		attrWidth = sizeof(unsigned int);
		attrType = ATTR_REPL_IPV4_SRC;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "reply_dst_ipv4"))
	{
		struct in_addr addr;

		if (!inet_aton(value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValue = addr.s_addr;

		attrWidth = sizeof(unsigned int);
		attrType = ATTR_REPL_IPV4_DST;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, NULL);
	}
	else if (!strcmp(option, "ip_v4_addr"))
	{
		struct in_addr addr;

		if (!inet_aton(value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValue = addr.s_addr;
		attrWidth = sizeof(unsigned int);
		mask = 0xFFFFFFFF;

		// this option is like 4 differents rules
		head_rule = section->rule;

		attrType = ATTR_ORIG_IPV4_SRC;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_ORIG_IPV4_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_IPV4_SRC;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_IPV4_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);

		// The last one will be added to the list later
		section->last = 1;
	}
	else if (!strcmp(option, "orig_src_ipv6"))
	{
		struct in6_addr addr;

		if (!inet_pton(AF_INET6, value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValueV6 = addr.s6_addr;
		attrValue = 0;

		attrWidth = 16;
		attrType = ATTR_ORIG_IPV6_SRC;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
	}
	else if (!strcmp(option, "orig_dst_ipv6"))
	{
		struct in6_addr addr;

		if (!inet_pton(AF_INET6, value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValueV6 = addr.s6_addr;
		attrValue = 0;

		attrWidth = 16;
		attrType = ATTR_ORIG_IPV6_DST;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
	}
	else if (!strcmp(option, "reply_src_ipv6"))
	{
		struct in6_addr addr;

		if (!inet_pton(AF_INET6, value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValueV6 = addr.s6_addr;
		attrValue = 0;

		attrWidth = 16;
		attrType = ATTR_REPL_IPV6_SRC;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
	}
	else if (!strcmp(option, "reply_dst_ipv6"))
	{
		struct in6_addr addr;

		if (!inet_pton(AF_INET6, value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValueV6 = addr.s6_addr;
		attrValue = 0;

		attrWidth = 16;
		attrType = ATTR_REPL_IPV6_DST;
		mask = 0xFFFFFFFF;

		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
	}
	else if (!strcmp(option, "ip_v6_addr"))
	{
		struct in6_addr addr;

		if (!inet_pton(AF_INET6, value, &addr))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribut value IP Address: %s\n", value);
			goto err;
		}

		attrValueV6 = addr.s6_addr;
		attrValue = 0;
		attrWidth = 16;
		mask = 0xFFFFFFFF;

		// this option is like 4 differents rules
		head_rule = section->rule;

		attrType = ATTR_ORIG_IPV6_SRC;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_ORIG_IPV6_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_IPV6_SRC;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);
		denyRules = cmmFcListAddRule(denyRules, section->name, section->rule);

		copy_rule = head_rule;
		section->rule = NULL;
		while (copy_rule) {
			section->rule = cmmFcRuleAddAttribut(section->rule, copy_rule->type, copy_rule->value, copy_rule->width, copy_rule->mask, NULL);
			copy_rule = copy_rule->next;
		}

		attrType = ATTR_REPL_IPV6_DST;
		section->rule = cmmFcRuleAddAttribut(section->rule, attrType, attrValue, attrWidth, mask, attrValueV6);

		// The last one will be added to the list later   
		section->last = 1;
	}
	else
		goto err;

	return 0;

err:
	return -1;
}

static void section_asym_fastforward_end_hdlr(void *data)
{
	struct rule_section_data *section = data;

	if (!section)
		return;
	asymFFRules = cmmFcAsymFFListAddRule(asymFFRules, section->name, section->asym_rule);
}


static int section_asym_fastforward_option_hdlr(void *data, int argc, char **argv)
{
	struct rule_section_data *section = data;
	char *option = argv[0];
	char *value = argv[1];
	unsigned int attrType, attrValue;
	char attrSValue[IFNAMSIZ + 1] = {0};
	int attrWidth;
	unsigned int mask;

	if (!section)
		goto err;

	if (!strcmp(option, "orig_src_port"))
	{
		attrType = ATTR_ORIG_PORT_SRC;
		attrValue = atoi(value);
		attrSValue[0] = '\0';
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribute value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		mask = 0x0000FFFF;

		section->asym_rule = cmmFcAsymFFRuleAddAttribut(section->asym_rule, attrType, attrValue, attrSValue, attrWidth, mask);
	}
	else if (!strcmp(option, "orig_dst_port"))
	{
		attrType = ATTR_ORIG_PORT_DST;
		attrValue = atoi(value);
		attrSValue[0] = '\0';
		attrWidth = sizeof(unsigned short);

		if ((attrValue < 1) || (attrValue > 65535))
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribute value: %s\n", value);
			goto err;
		}

		attrValue = htons(attrValue);
		mask = 0x0000FFFF;

		section->asym_rule = cmmFcAsymFFRuleAddAttribut(section->asym_rule, attrType, attrValue, attrSValue, attrWidth, mask);
	}
	else if (!strcmp(option, "l3proto"))
	{
		if (!strcmp(value, "ipv4"))
		{
			attrValue = AF_INET;
			attrSValue[0] = '\0';
			attrType = ATTR_L3PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
	 	}
		else if (!strcmp(value, "ipv6")) {
			attrValue = AF_INET6;
			attrSValue[0] = '\0';
			attrType = ATTR_L3PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribute value: %s\n", value);
			goto err;
		}

		section->asym_rule = cmmFcAsymFFRuleAddAttribut(section->asym_rule, attrType, attrValue, attrSValue, attrWidth, mask);
	}
	else if (!strcmp(option, "l4proto"))
	{
		if (!strcmp(value, "tcp"))
		{
			attrValue = IPPROTO_TCP;
			attrSValue[0] = '\0';
			attrType = ATTR_ORIG_L4PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else if (!strcmp(value, "udp")) {
			attrValue = IPPROTO_UDP;
			attrSValue[0] = '\0';
			attrType = ATTR_ORIG_L4PROTO;
			attrWidth = sizeof(unsigned char);
			mask = 0x000000FF;
		}
		else
		{
			cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nBad attribute value: %s\n", value);
			goto err;
		}

		section->asym_rule = cmmFcAsymFFRuleAddAttribut(section->asym_rule, attrType, attrValue, attrSValue, attrWidth, mask);
	}
	else if (!strcmp(option, "interface"))
	{
		size_t len = strlen(value);
		attrValue = 0;
		if (len >= IFNAMSIZ)
			len = IFNAMSIZ - 1;
		memcpy(attrSValue, value, len);
		attrSValue[len] = '\0';
		attrType = ATTR_ORIG_COMCERTO_FP_IIF;
		attrWidth = sizeof(int);
		mask = 0x000000FF;

		section->asym_rule = cmmFcAsymFFRuleAddAttribut(section->asym_rule, attrType, attrValue, attrSValue, attrWidth, mask);
	}

	return 0;

err:
	return -1;
}

static int section_tcp_lro_option_hdlr(void *data, int argc, char **argv)
{
	char *option = argv[0];
	char *value = argv[1];

	if (!strcmp(option, "ifname"))
	{
		if (lro_interface_add(value) < 0)
			goto err;
	}
	else
		goto err;

	return 0;

err:
	return -1;
}


static int section_cli_listenaddr_option_hdlr(void *data,int argc, char **argv)
{
	if(strcmp(argv[0],"ip4addr"))
		goto err;
	if(inet_pton(AF_INET,argv[1],&globalConf.cli_listenaddr)==0)
		goto err;
	return 0;
err:
	return -1;
}

	
static struct section_hdlr section_handler[] = {
	{
		.name = "fastforward",
		.start = section_rule_start_hdlr,
		.option = section_fastforward_option_hdlr,
		.end = section_fastforward_end_hdlr,
	},
	{
		.name = "asym_fastforward",
		.start = section_rule_start_hdlr,
		.option = section_asym_fastforward_option_hdlr,
		.end = section_asym_fastforward_end_hdlr,
	},
	{
		.name = "logging",
		.option = section_logging_option_hdlr,
	},
	{
		.name = "vlan",
		.option = section_vlan_option_hdlr,
	},
	{
		.name = "tun",
		.option = section_tun_option_hdlr,
	},
#ifdef WIFI_ENABLE
	{
		.name = "wifi_fastforward",
		.start = section_wifi_fastforward_start_hdlr,
		.option = section_wifi_fastforward_option_hdlr,
	},
#endif
	{
		.name = "tcp_lro",
		.option = section_tcp_lro_option_hdlr,
	},
	{
		 .name = "cli_listenaddr",
		 .option = section_cli_listenaddr_option_hdlr,
	}
};

/*****************************************************************
* cmmFcParser()
*
*           Returns 0 if the parser succeed
*
******************************************************************/
int cmmFcParser(char *confFilePath)
{
	FILE *fp;
	char buf[150];
	int argc;
	char *argv[ARGC_MAX];
	void *hdlr_data = NULL;
	struct section_hdlr *hdlr = NULL;
	int i;
	int ret = 0;
	fp = fopen(confFilePath , "r");
	if (!fp)
	{
		cmm_print(DEBUG_CRIT, "cmmFcParser: Error opening %s\n", confFilePath);
		return -1;
	}

#ifdef WIFI_ENABLE
	memset( glbl_wifi_ff_ifs, 0, sizeof(struct wifi_ff_entry) * MAX_WIFI_FF_IFS );
#endif

	while (fgets(buf, sizeof(buf), fp))
	{
		argc = 0;
		argv[argc] = strtok(buf, " \t\r\n");

		while (argv[argc]) {
			argc++;
			if (argc == ARGC_MAX)
				break;

			argv[argc] = strtok(NULL, " \t\r\n");
		}

		if (argc < 1)
			continue;

		if (argv[0][0] == '#')
		{
			/* skip comments */
			continue;
		}
		else if (!strcasecmp(argv[0], "config"))
		{
			/* Section ending */
			if (hdlr && hdlr->end) {
				hdlr->end(hdlr_data);

				hdlr = NULL;
				hdlr_data = NULL;
			}

			/* New section starting */
			if (argc < 2)
				continue;

			for (i = 0; i < sizeof(section_handler) / sizeof(struct section_hdlr); i++) {
				if (!strcasecmp(argv[1], section_handler[i].name)) {

					hdlr = &section_handler[i];

					if (hdlr->start) {
						hdlr_data = hdlr->start(argc - 2, &argv[2]);

						if (!hdlr_data) {
							hdlr = NULL;
							cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nLine: \"%s\"\n", buf);
							ret = -1;
						}
					}

					break;
				}
			}
		}
		else if (!strcasecmp(argv[0], "option"))
		{
			if (argc < 3)
				continue;

			/* Continue parsing section */
			if (hdlr && hdlr->option)
				if (hdlr->option(hdlr_data, argc - 1, &argv[1]) < 0) {
					cmm_print(DEBUG_CRIT, "cmmFcParser: Error parsing configuration file.\nLine: \"%s\"\n", buf);
					ret = -1;
				}
		}
	}

	if (hdlr && hdlr->end) {
		hdlr->end(hdlr_data);

		hdlr = NULL;
		hdlr_data = NULL;
	}

	fclose(fp);

	return ret;
}

/*****************************************************************
* cmmRxCmd
*
*
******************************************************************/
static int cmmRxCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmRxSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

#ifdef LS1043
/*****************************************************************
* cmmTxCmd
*
*
******************************************************************/
static int cmmTxCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call TX process function*/
	cmmTxSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
#endif

/*****************************************************************
* cmmStatCmd
*
*
******************************************************************/
static int cmmStatCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmStatSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmm4rdIdConvCmd
*
*
******************************************************************/
static int cmm4rdIdConvCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call tunnel 4rd id conversion set process function*/
  cmm4rdIdConvSetProcess(argv, 0, argc, globalConf.cli.daemon_handle);

       return CLI_OK;
}


/*****************************************************************
* cmmDPDSaQueryCmd
*
*
******************************************************************/
static int cmmDPDSaQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmDPDSaQuerySetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

#ifdef C2000_DPI
/*****************************************************************
* cmmDPIEnableCmd
*
*
******************************************************************/
static int cmmDPIEnableCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmDPIFlagSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
#endif

/*****************************************************************
* cmmAsymFFEnableCmd
*
*
******************************************************************/
static int cmmAsymFFEnableCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmAsymFFSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmShowRxCmd
*
*
******************************************************************/
static int cmmShowRxCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmRxShowProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmShowStatCmd
*
*
******************************************************************/
static int cmmShowStatCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Stat process function*/
  cmmStatShowProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}


/*****************************************************************
* cmmQueryRxCmd
*
*
******************************************************************/
static int cmmQueryRxCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmRxQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}


/*****************************************************************
* cmmQueryRtCmd
*
*
******************************************************************/
static int cmmQueryRtCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmRtQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmQueryCtCmd
*
*
******************************************************************/
static int cmmQueryCtCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmCtQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmQueryMacVlanCmd
*
*
******************************************************************/
static int cmmQueryMacVlanCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call MacVlan process function*/
  cmmMacVlanQueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}

/*****************************************************************
 * * cmmQueryV6CtCmd
 * *
 * *
 * ******************************************************************/
static int cmmQueryV6CtCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmCt6QueryProcess(argv, 0, globalConf.cli.daemon_handle);

  return CLI_OK;
}



/*****************************************************************
* cmmQueryPPPoECmd
*
*
******************************************************************/
static int cmmQueryPPPoECmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call PPPoE process function*/
  cmmPPPoEQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}


/*****************************************************************
 * * cmmQueryVlanCmd
 * *
 * *
 * ******************************************************************/
static int cmmQueryVlanCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Vlan process function*/
  cmmVlanQuery(argv, 0, globalConf.cli.daemon_handle);

        return CLI_OK;
}

#if defined(LS1043)
/*****************************************************************
 * * cmmQueryipr4Cmd
 * *
 * *
 * ******************************************************************/
static int cmmQueryipr4statsCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  	/*Call ipr process function*/
	return (cmmIpr4StatsQuery(argv, 0, globalConf.cli.daemon_handle));
}
static int cmmQueryipr6statsCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  	/*Call ipr process function*/
	return (cmmIpr6StatsQuery(argv, 0, globalConf.cli.daemon_handle));
}
#endif

/*****************************************************************
 *  * * cmmQueryMc4Cmd
 *   * *
 *    * *
 *     * ******************************************************************/
static int cmmQueryMc4Cmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Multicast IPV4 process function*/
   cmmMc4QueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}

/*****************************************************************
 *  *  * * cmmQueryMc6Cmd
 *   *   * *
 *    *    * *
 *     *     * ******************************************************************/
static int cmmQueryMc6Cmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Multicast IPV4 process function*/
   cmmMc6QueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}


/*****************************************************************
 *  *  *  * * cmmQueryQmCmd
 *   *   *   * *
 *    *    *    * *
 *     *     *     * ******************************************************************/
static int cmmQueryQmCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
   cmmQmQueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}


/*****************************************************************
 *  *  *  *  * * cmmQmExptRateQueryCmd
 *   *   *   *   * *
 *    *    *    *    * *
 *     *     *     *     * ******************************************************************/
static int cmmQmExptRateQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
   cmmQmExptRateQueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}

#ifdef LS1043
/*****************************************************************
 *  *  *  *  * * cmmQmDSCPFqMapQueryCmd
 *   *   *   *   * * This function query the dscp fq map configuration on the interface.
 *    *    *    *    * *
 *     *     *     *     * ******************************************************************/
static int cmmQmDSCPFqMapQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
   cmmQmDSCPFqMapQueryProcess(argv, 0, globalConf.cli.daemon_handle);

   return CLI_OK;
}

/*****************************************************************
 *  *  *  *  * * cmmQmFFRateQueryProcess
 *   *   *   *   * *
 *    *    *    *    * *
 *     *     *     *     * ******************************************************************/
static int cmmQmFFRateQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call process function*/
   cmmQmFFRateQueryProcess(argv, 0, globalConf.cli.daemon_handle);
   return CLI_OK;
}

/*****************************************************************
 *  *  *  *  * * cmmDSCPVlanPcpMapQueryCmd
 *   *   *   *   * * This function query the dscp vlan pcp map configuration on the interface.
 *    *    *    *    * *
 *     *     *     *     * ******************************************************************/
static int cmmDSCPVlanPcpMapQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	cmmDSCPVlanPcpMapQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
#endif

/*****************************************************************
 *  * * cmmSaQueryCmd
 *   * *
 *    * *
 *     * ******************************************************************/
static int cmmSaQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Sa Query process function*/
  cmmSAQueryProcess(argv, 0, globalConf.cli.daemon_handle);

        return CLI_OK;
}

#if defined(LS1043)
/*****************************************************************
* cmmSECfailStatsQueryCmd
*
*
******************************************************************/
static int cmmSECfailStatsQueryCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call SEC failure stats Query process function*/
	cmmSECfailStatsQueryProcess(argv, 0, globalConf.cli.daemon_handle);
	
	return CLI_OK;
}
#endif /* LS1043 */

/*****************************************************************
* cmmQueryNatptCmd
*
*
******************************************************************/
static int cmmQueryNatptCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call NATPT process function*/
	cmmNATPTQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
#ifdef AUTO_BRIDGE
/*****************************************************************
* cmmQueryL2FlowCmd
*
*
******************************************************************/
static int cmmQueryL2FlowCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call L2Flow query function*/
	cmmL2FlowQueryProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
#endif
/*****************************************************************
* cmmQosCmds
*
*
******************************************************************/
static int cmmQmCmds(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call QM process function*/
	cmmQmSetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmMc4Cmd
*
*
******************************************************************/
static int cmmMc4Cmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmMc4SetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}
/*****************************************************************
* cmmMc6Cmd
*
*
******************************************************************/
static int cmmMc6Cmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call RX process function*/
  cmmMc6SetProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmMspMem
*
*
******************************************************************/
static int cmmMspMem(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Memory DIsplay function */
	prfMspMS( globalConf.cli.daemon_handle,argc, argv);

	return CLI_OK;
}
/*****************************************************************
* cmmMspMem
*
*
******************************************************************/
static int cmmMspMemW(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Memory DIsplay function */
	prfMspMSW( globalConf.cli.daemon_handle,argc, argv);

	return CLI_OK;
}
/*****************************************************************
* cmmMspMem
******************************************************************/
static int cmmMspCT(struct cli_def * cli, const char *command, char *argv[], int argc)
{
  /*Call Memory DIsplay function */
	prfMspCT(globalConf.cli.daemon_handle,argc, argv );
	return CLI_OK;
}

/*
** Performance mesaurement and tracing 
*/
/* Busy CPU */
static int cmmPTBusyCPU(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfPTBusyCPU( globalConf.cli.daemon_handle,argc, argv);
	return CLI_OK;
}
/* Tracing/profiling */
static int cmmPTsetmask(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfPTsetmask( globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}
static int cmmPTstart(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfPTstart(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}
static int cmmPTswitch(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfPTswitch(globalConf.cli.daemon_handle, argc,argv);
	return CLI_OK;
}

static int cmmPTshow(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfPTshow(globalConf.cli.daemon_handle, argc,argv);
	return CLI_OK;
}

static int cmmPTstatus(struct cli_def * cli, const char *command, char *argv[], int argc) {
	prfStatus(globalConf.cli.daemon_handle,argc, argv);
	return CLI_OK;
}

/*****************************************************************
* cmmVlan commands
*
*
******************************************************************/
static int cmmVlanCliAdd(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	/*Call vlan process function*/
  	vlanAddProcess(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}

static int cmmVlanCliDelete(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	/*Call vlan process function*/
  	vlanDeleteProcess(globalConf.cli.daemon_handle, argc, argv);

	return CLI_OK;
}


/*****************************************************************
* cmmPktCap commands
*
*
******************************************************************/
static int cmmPktCapSlice(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	
  	PktCapSliceProcess(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}

static int cmmPktCapStat(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	PktCapStatProcess(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmPktCapFilter(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	PktCapFilterProcess(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmPktCapQuery(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	PktCapQueryProcess(cli , globalConf.cli.daemon_handle);
	return CLI_OK;
}


/*****************************************************************
* cmmIcc commands
*
*
******************************************************************/
static int cmmIccReset(struct cli_def *cli, const char *command, char *argv[], int argc)
{
	
  	IccReset(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}

static int cmmIccThreshold(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	IccThreshold(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmIccAdd(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	IccAdd(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmIccDelete(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	IccDelete(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmIccQuery(struct cli_def *cli, const char *command, char *argv[], int argc)
{
  	IccQuery(globalConf.cli.daemon_handle, argc, argv);
	return CLI_OK;
}


static int cmmQueryTnlCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	cmmTnlQueryProcess(argv, 0, globalConf.cli.daemon_handle);

        return CLI_OK;
}



/*****************************************************************
* cmmSetTimeoutCLI
******************************************************************/
static int cmmSetTimeoutCLI(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	timeoutSet(globalConf.cli.daemon_handle, argv, argc);
	return CLI_OK; 
}

static int cmmSetRouteCLI(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	cmmRouteSetProcess(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK; 
}

static int cmmFFControlCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	cmmFFControlProcess(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

static int cmmIpv4Cmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	cmmCtChangeProcess4(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

static int cmmIpv6Cmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	cmmCtChangeProcess6(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

int cmmFFControlProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	cmmd_ff_ctrl_cmd_t cmd;
	union u_rxbuf rxbuf;
	char enable;
	int rc;

	if(!keywords[cpt])
		goto usage;

	if(strcasecmp(keywords[cpt], "enable") == 0)
		enable = 1;
	else if (strcasecmp(keywords[cpt], "disable") == 0)
		enable = 0;
	else
		goto usage;

	cmd.enable = enable;

	// Send message to forward engine
	cmm_print(DEBUG_COMMAND, "Send CMD_CMMTD_IPV4_FF_CONTROL cmd to daemon len=%zu\n",sizeof(cmmd_ff_ctrl_cmd_t));
	rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_IPV4_FF_CONTROL, (unsigned short *) &cmd, sizeof(cmmd_ff_ctrl_cmd_t), rxbuf.rcvBuffer);
	if (rc != 2) /* we expect 2 bytes in response */
	{
		cmm_print(DEBUG_STDERR, "CMD_CMMTD_IPV4_FF_CONTROL unexpected response length %d\n", rc);
		return -1;
	}
	else if (rxbuf.result != CMMD_ERR_OK)
	{
		showErrorMsg("CMD_CMMTD_IPV4_FF_CONTROL", ERRMSG_SOURCE_CMMD, rxbuf.rcvBuffer);
		return -1;
	}

	return 0;
usage:
	cmm_print(DEBUG_ERROR, "Usage: set ff <enable disable>\n");
	return -1;
}

int cmmIPsecSetProcess(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	int cpt = tabStart;
	fpp_ipsec_cmd_t cmd;
	union u_rxbuf rxbuf;
	char enable;

	if(!keywords[cpt])
		goto usage;

	else if(strcasecmp(keywords[cpt], "pre-frag") == 0)
	{
		if(!keywords[++cpt])
			goto usage;

		if(strcasecmp(keywords[cpt], "enable") == 0)
			enable = 1;
		else if (strcasecmp(keywords[cpt], "disable") == 0)
			enable = 0;
		else
			goto usage;

		cmd.pre_frag_en = enable;

		// Send message to forward engine
		cmm_print(DEBUG_COMMAND, "Send CMD_IPSEC_FRAG_CFG cmd to daemon len=%zu\n",sizeof(fpp_ipsec_cmd_t));
		if(cmmSendToDaemon(daemon_handle, FPP_CMD_IPSEC_FRAG_CFG, (unsigned short *) &cmd, sizeof(fpp_ipsec_cmd_t), rxbuf.rcvBuffer) == 4)
		{
			if (rxbuf.result != 0) {
				showErrorMsg("CMD_IPSEC_FRAG_CFG", ERRMSG_SOURCE_CMMD,rxbuf.rcvBuffer);
				return (rxbuf.result);
			}
		}
		return 0;
	}
usage:
	cmm_print(DEBUG_ERROR, "Usage: set ipsec pre-frag <enable disable>\n");
	return -1;
}

int cmmExptCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
  	/*Call exception path process function*/
 	cmmExptSetProcess(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

int cmmRtpCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
  	/*Call RTP process function*/
 	cmmRTPSetProcess(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

int cmmSocketCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
	/*Call Socket process function*/
	cmmSocketSetProcess(argv, 0, globalConf.cli.daemon_handle, AF_INET);
	return CLI_OK;
}

int cmmSocket6Cmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call Socket process function*/
	cmmSocketSetProcess(argv, 0, globalConf.cli.daemon_handle, AF_INET6);
	return CLI_OK;
}

int cmmNatptCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
  	/*Call NAT-PT process function*/
 	cmmNATPTSetProcess(argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

int cmmAltConfCmd(struct cli_def * cli, const char *command, char *argv[], int argc) 
{
  	/*Call AltConf process function*/
 	cmmAltConfClient(argc, argv, 0, globalConf.cli.daemon_handle);
	return CLI_OK;
}

static void cliCallback(struct cli_def *cliHandle, const char *format)
{
	if (format[0] && cliHandle->client)
		fprintf(cliHandle->client, "%s\r\n", format);
}

/*****************************************************************
* cmmBridgeControlCmd
*
*
******************************************************************/
static int cmmBridgeControlCmd(struct cli_def * cli, const char *command, char *argv[], int argc)
{
	/*Call L2Flow query function*/
	cmmBridgeControlProcess(argv, 0, globalConf.cli.daemon_handle);

	return CLI_OK;
}

/*****************************************************************
* cmmCliThread()
*
*
*
******************************************************************/
static void *cmmCliThread(void *data)
{
	struct cmm_cli *ctx = data;

	cmm_print(DEBUG_INFO, "%s: pid %d\n", __func__, getpid());

	while (1)
	{
		ctx->sock2 = accept(ctx->sock, NULL, 0);
		if (ctx->sock2 < 0)
		{
			cmm_print(DEBUG_ERROR, "%s: accept() %s\n", __func__, strerror(errno));
			break;
		}

		cli_loop(ctx->handle, ctx->sock2);
		cmm_print(DEBUG_INFO, "%s: cli_loop exiting\n", __func__);

		//ctx->sock2 is already closed in cli_loop and need not close it again here

	}

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	kill (0, SIGTERM);
	pthread_exit(NULL);

	return NULL;
}


/*****************************************************************
* cmmCliInit()
*
*
*
******************************************************************/
int cmmCliInit(struct cmm_cli *ctx)
{
	struct sockaddr_in serveraddr;
	struct cli_command *c;
	int on;

	cmm_print(DEBUG_INFO, "%s\n", __func__);

	ctx->fci_handle = fci_open(FCILIB_FF_TYPE, 0);
	if (!ctx->fci_handle)
	{
		cmm_print(DEBUG_ERROR, "%s: fci_open() failed, %s\n", __func__, strerror(errno));
		goto err0;
	}

#ifdef NEW_IPC
	ctx->daemon_handle = cmm_open();
	if (!ctx->daemon_handle)
	{
		cmm_print(DEBUG_ERROR, "%s: cmm_open() failed, %s\n", __func__, strerror(errno));
		goto err1;
	}
#else
	ctx->daemon_handle = globalConf.cmmPid;
#endif
	ctx->handle = cli_init();
	if (!ctx->handle)
	{
		cmm_print(DEBUG_ERROR, "%s: cli_init() failed\n", __func__);
		goto err2;
	}

	cli_set_hostname(ctx->handle, "cmm");
	cli_set_banner(ctx->handle, "Welcome to the CMM (Conntrack Monitor Module) CLI");
	cli_print_callback(ctx->handle, cliCallback);

	cli_allow_user(ctx->handle, "admin", "admin");

	c = cli_register_command(ctx->handle, NULL, "show", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "connections", cmmCtShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of fast forwarded connections");
		cli_register_command(ctx->handle, c, "fpp_route", cmmFPPRtShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the fpp route entries used by the fast forwarded connections");
		cli_register_command(ctx->handle, c, "route", cmmRtShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the route entries used by the fast forwarded connections");
		cli_register_command(ctx->handle, c, "neighbor", cmmNeighShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the neighbor entries used by the fast forwarded connections");
		cli_register_command(ctx->handle, c, "rules", cmmFcRulesShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of non fast forwardable connections");
		cli_register_command(ctx->handle, c, "debug_level", cmmFcDebugShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the debug level");
		cli_register_command(ctx->handle, c, "activate", cmmFcActivateShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show if cmm is activated or not");
		cli_register_command(ctx->handle, c, "pppoe", cmmPPPoELocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the pppoe entries used by the fast forwarded connections");
		cli_register_command(ctx->handle, c, "vlan", cmmVlanLocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the vlan entries programmmed");
		cli_register_command(ctx->handle, c, "macvlan", cmmMacVlanLocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the macvlan entries programmmed");
		cli_register_command(ctx->handle, c, "rx", cmmShowRxCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show ICC, Bridge status");
		cli_register_command(ctx->handle, c, "stat", cmmShowStatCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show Statistics");
		cli_register_command(ctx->handle, c, "sa_query_timer", cmmSaQueryTimerShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the DPD SA query timer configuration");
		cli_register_command(ctx->handle, c, "sa", cmmSAShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the SA stored by CMM");
#ifdef IPSEC_FLOW_CACHE
		cli_register_command(ctx->handle, c, "sec-connections", cmmFlowLocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the local table of secure connections");
#endif /* IPSEC_FLOW_CACHE */
		cli_register_command(ctx->handle, c, "relay", cmmRelayLocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show pppoe relay entries used by  the fast forwarded connections");
		cli_register_command(ctx->handle, c, "mc6", cmmMc6Show, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of V6 multicat listeners");
		cli_register_command(ctx->handle, c, "mc4", cmmMc4Show, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of V4 multicat listeners");
#ifdef C2000_DPI
		cli_register_command(ctx->handle, c, "dpi", cmmDPIEnableShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the DPI flag status");
#endif
		cli_register_command(ctx->handle, c, "asym_fastforward", cmmAsymFFEnableShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the Asymmetric fast forward status");
		cli_register_command(ctx->handle, c, "asym_ff_rules", cmmFcAsymFFRulesShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of asymmetric fast forwardable connections");
	}

        c = cli_register_command(ctx->handle, NULL, "query", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
	        cli_register_command(ctx->handle, c, "pppoe", cmmQueryPPPoECmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query PPPoE entries on FPP");
        	cli_register_command(ctx->handle, c, "rx", cmmQueryRxCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Bridge entries on FPP");
	        cli_register_command(ctx->handle, c, "route", cmmQueryRtCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Route entries on FPP");
        	cli_register_command(ctx->handle, c, "connections", cmmQueryCtCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Connection entries on FPP");
	        cli_register_command(ctx->handle, c, "macvlan", cmmQueryMacVlanCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Macvlan interfaces on FPP");
	        cli_register_command(ctx->handle, c, "v6connections", cmmQueryV6CtCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query IPV6Connection entries on FPP");
	        cli_register_command(ctx->handle, c, "vlan", cmmQueryVlanCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query VLAN entries on FPP");
#ifdef LS1043
	        cli_register_command(ctx->handle, c, "ipr4stats", cmmQueryipr4statsCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query ipv4 reassembly statistics");
	        cli_register_command(ctx->handle, c, "ipr6stats", cmmQueryipr6statsCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query ipv6 reassembly statistics");
#endif
	        cli_register_command(ctx->handle, c, "mc4", cmmQueryMc4Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Multicast IPV4 entries on FPP");
	        cli_register_command(ctx->handle, c, "mc6", cmmQueryMc6Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Multicast IPV6 entries on FPP");
        	cli_register_command(ctx->handle, c, "qm", cmmQueryQmCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query QOS configuration on FPP");
	        cli_register_command(ctx->handle, c, "qmexptrate", cmmQmExptRateQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query Exception rate configured on FPP");
        	cli_register_command(ctx->handle, c, "sa", cmmSaQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query for SA details");
#if defined(LS1043)
	        cli_register_command(ctx->handle, c, "qm-dscp-fqmap", cmmQmDSCPFqMapQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query DSCP Fq map configuration");
	        cli_register_command(ctx->handle, c, "tx-dscp-to-vlanpcp", cmmDSCPVlanPcpMapQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query DSCP VLAN PCP map configuration");
        	cli_register_command(ctx->handle, c, "secfailstats", cmmSECfailStatsQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query SEC engine failure statistics");
#endif
        	cli_register_command(ctx->handle, c, "natpt", cmmQueryNatptCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query NAT-PT connections");
		cli_register_command(ctx->handle, c, "pktcapture", cmmPktCapQuery, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query packet capture config parameters");
		cli_register_command(ctx->handle, c, "tunnels",cmmQueryTnlCmd , PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query tunnel parameters");
#ifdef AUTO_BRIDGE
		cli_register_command(ctx->handle, c, "l2flows", cmmQueryL2FlowCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query L2Flows entries on FPP");
#endif
#ifdef LS1043
                cli_register_command(ctx->handle, c, "qmffrate", cmmQmFFRateQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Query rate configured  for fast path");
#endif
	}

	//	cli_register_command(ctx->handle, pshow, "eth_icc", cmmEthIccShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the pppoe entries used by the fast forwarded connections");
	c = cli_register_command(ctx->handle, NULL, "set", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "activate", cmmFcActivate, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Activate or desactivate fast forwarding");
		cli_register_command(ctx->handle, c, "debug", cmmFcDebug, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Debug level");
		cli_register_command(ctx->handle, c, "rx", cmmRxCmd, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage RX module (ICC, Bridge ...)");
#ifdef LS1043
		cli_register_command(ctx->handle, c, "tx", cmmTxCmd, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage TX module (DSCP VLAN P bit map)");
#endif
		cli_register_command(ctx->handle, c, "qm", cmmQmCmds, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage QM module (QOS, Rate Limiting ...)");
		cli_register_command(ctx->handle, c, "mc6", cmmMc6Cmd, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage MC6 module (IpV6 multicast)");
		cli_register_command(ctx->handle, c, "mc4", cmmMc4Cmd, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage MC4 module (IPv4 multicast)");
		cli_register_command(ctx->handle, c, "timeout", cmmSetTimeoutCLI, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Set UDP/TCP/IPIP timeout value in FPP");
		cli_register_command(ctx->handle, c, "route", cmmSetRouteCLI, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Set Extended Route");
		cli_register_command(ctx->handle, c, "ff", cmmFFControlCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Enable or disable fast forward");
		cli_register_command(ctx->handle, c, "stat", cmmStatCmd, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Manage Statistics (PPPoE, Bridge ...)");
		cli_register_command(ctx->handle, c, "expt_queue", cmmExptCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage Exception Path Queuing");
		cli_register_command(ctx->handle, c, "socket", cmmSocketCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage Socket module");
		cli_register_command(ctx->handle, c, "socket6", cmmSocket6Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage Socket module");
		cli_register_command(ctx->handle, c, "rtp", cmmRtpCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage RTP-relay module");
		cli_register_command(ctx->handle, c, "natpt", cmmNatptCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage NAT-PT module");
		cli_register_command(ctx->handle, c, "sa_query_timer", cmmDPDSaQueryCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Enable or disable SA query timer");
		cli_register_command(ctx->handle, c, "config", cmmAltConfCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage Alternate Configuration module");
		cli_register_command(ctx->handle, c, "bridge", cmmBridgeControlCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Manage automatic bridging");
#ifdef C2000_DPI
		cli_register_command(ctx->handle, c, "dpi", cmmDPIEnableCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Enable or disable Global DPI flag");
#endif
		cli_register_command(ctx->handle, c, "asym_fastforward", cmmAsymFFEnableCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Enable or disable Asymmetric Fast forward");
		cli_register_command(ctx->handle, c, "4rd-id-conversion", cmm4rdIdConvCmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Enable or disable 4rd Ipv4  header ID conversion");
	}

	c = cli_register_command(ctx->handle, NULL, "ipv4", cmmIpv4Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
		cli_register_command(ctx->handle, c, "update", cmmIpv4Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Update IPv4 Connection");

	c = cli_register_command(ctx->handle, NULL, "ipv6", cmmIpv6Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
		cli_register_command(ctx->handle, c, "update", cmmIpv6Cmd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Update IPv6 Connection");

	cli_register_command(ctx->handle, NULL, "stop", cmmFcStop, PRIVILEGE_PRIVILEGED, MODE_EXEC, "Kill cmm");

	c = cli_register_command(ctx->handle, NULL, "prf",NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "busycpu",cmmPTBusyCPU, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Measure available CPU cycles");
		cli_register_command(ctx->handle, c, "status", cmmPTstatus, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show status of profiling/CPU measurement");
		c = cli_register_command(ctx->handle, c, "trace", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
		if (c)
		{
			cli_register_command(ctx->handle, c, "setmask", cmmPTsetmask, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Set module mask");
			cli_register_command(ctx->handle, c, "start", cmmPTstart, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Start trace");
			cli_register_command(ctx->handle, c, "switch", cmmPTswitch, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Stop or switch trace and show inactive trace");
			cli_register_command(ctx->handle, c, "showtrace", cmmPTshow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show inactive trace");
		}
	}

	c = cli_register_command(ctx->handle, NULL, "mspmem",NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "ct", cmmMspCT, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show the table of fast forwarded connections");
		cli_register_command(ctx->handle, c, "bytes", cmmMspMem, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show msp memory in host order");
		cli_register_command(ctx->handle, c, "words", cmmMspMemW, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Show msp memory in network order");
	}

	c = cli_register_command(ctx->handle, NULL, "vlan",NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "add", cmmVlanCliAdd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Register vlan entry with fpp");
		cli_register_command(ctx->handle, c, "delete", cmmVlanCliDelete, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Deregister vlan with fpp");
		cli_register_command(ctx->handle, c, "show", cmmVlanLocalShow, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "Deregister vlan with fpp");
	}

	c = cli_register_command(ctx->handle, NULL, "pktcapture", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "slice", cmmPktCapSlice, PRIVILEGE_UNPRIVILEGED, MODE_EXEC,
								"Register packet capture size with fpp");
		cli_register_command(ctx->handle, c, "status", cmmPktCapStat, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, 
								"Enable/disable packet capture on LAN/WAN with fpp");
		cli_register_command(ctx->handle, c, "filter", cmmPktCapFilter, PRIVILEGE_UNPRIVILEGED, MODE_EXEC,
								 "Register first-level-filter string for LAN/WAN with fpp");
	}

	c = cli_register_command(ctx->handle, NULL, "icc", NULL, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, "");
	if (c)
	{
		cli_register_command(ctx->handle, c, "reset", cmmIccReset, PRIVILEGE_UNPRIVILEGED, MODE_EXEC,
								"Reset ICC");
		cli_register_command(ctx->handle, c, "threshold", cmmIccThreshold, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, 
								"Set ICC threshold values");
		cli_register_command(ctx->handle, c, "add", cmmIccAdd, PRIVILEGE_UNPRIVILEGED, MODE_EXEC,
								 "Add ICC table entry");
		cli_register_command(ctx->handle, c, "delete", cmmIccDelete, PRIVILEGE_UNPRIVILEGED, MODE_EXEC,
								 "Delete ICC table entry");
		cli_register_command(ctx->handle, c, "query", cmmIccQuery, PRIVILEGE_UNPRIVILEGED, MODE_EXEC, 
								"Query ICC table values");
	}


	ctx->sock = socket(AF_INET, SOCK_STREAM, 0);
	if (ctx->sock < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: socket() %s\n", __func__, strerror(errno));
		goto err3;
	}

	on = 1;
	if (setsockopt(ctx->sock, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) < 0)
		goto err4;

	memset(&serveraddr, 0, sizeof(serveraddr));
	serveraddr.sin_family = AF_INET;
	serveraddr.sin_addr.s_addr = globalConf.cli_listenaddr;
	serveraddr.sin_port = htons(2103);
	if (bind(ctx->sock, (struct sockaddr *) &serveraddr, sizeof(serveraddr)) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: bind() %s\n", __func__, strerror(errno));
		goto err4;
	}

	if (listen(ctx->sock, 1) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: listen() %s\n", __func__, strerror(errno));
		goto err4;
	}

	if (pthread_create(&ctx->pthread, NULL, cmmCliThread, ctx) < 0)
	{
		cmm_print(DEBUG_CRIT, "%s: pthread_create() failed, %s\n", __func__, strerror(errno));
		goto err4;
	}

	return 0;

err4:
	close(ctx->sock);

err3:
	cli_done(ctx->handle);

err2:
#ifdef NEW_IPC
	cmm_close(ctx->daemon_handle);

err1:
#endif
	fci_close(ctx->fci_handle);

err0:
	return -1;
}


void cmmCliExit(struct cmm_cli *ctx)
{
	cmm_print(DEBUG_INFO, "%s\n", __func__);

#if defined(__UCLIBC__)
	/* workaround uclibc pthread_cancel() bug, force thread to exit */
	close(ctx->sock);
	close(ctx->sock2);
#endif
	pthread_cancel(ctx->pthread);

	pthread_join(ctx->pthread, NULL);

#if !defined(__UCLIBC__)
	close(ctx->sock);
	close(ctx->sock2);
#endif

	cli_done(ctx->handle);
	ctx->handle = 0;

#ifdef NEW_IPC
	cmm_close(ctx->daemon_handle);
#endif

	fci_close(ctx->fci_handle);

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);
}
