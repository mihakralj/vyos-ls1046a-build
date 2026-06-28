/*
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017-2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */
#include "cdx.h"
#include "control_bridge.h"
#include "control_pppoe.h"
#include "control_stat.h"
#include "control_tunnel.h"
#include "control_tx.h"
#ifdef DPA_IPSEC_OFFLOAD
#include "control_ipsec.h"
#include "cdx_dpa_ipsec.h"
#endif
#include "control_vlan.h"
#include "dpa_control_mc.h"
#include "fm_ehash.h"
#include <net/pkt_sched.h>
#include <linux/fsl_qman.h>
#include "module_qm.h"
#ifdef CFG_WIFI_OFFLOAD
#include "control_wifi.h"
#endif

CmdProc gCmdProcTable[EVENT_MAX];

#define CDX_CMD_MAX_REPLY_LENGTH	512

enum cdx_cmd_len_status {
	CDX_CMD_LEN_OK,
	CDX_CMD_LEN_BAD,
	CDX_CMD_LEN_UNKNOWN,
};

enum cdx_cmd_len_type {
	CDX_CMD_LEN_EXACT,
	CDX_CMD_LEN_ALT_EXACT,
	CDX_CMD_LEN_RANGE,
};

struct cdx_cmd_len_spec {
	U16 fcode;
	U16 len;
	U16 len2;
	enum cdx_cmd_len_type type;
};

struct cdx_tx_enable_mac_cmd {
	U16 portid;
	U8 reserved[12];
	U8 mac_addr[6];
} __attribute__((__packed__));

struct cdx_l2_bridge_add_entry_cmd {
	U16 input_interface;
	U16 input_svlan;
	U16 input_cvlan;
	U8 destaddr[6];
	U8 srcaddr[6];
	U16 ethertype;
	U16 output_interface;
	U16 output_svlan;
	U16 output_cvlan;
	U16 pkt_priority;
	U16 svlan_priority;
	U16 cvlan_priority;
	U8 input_name[IF_NAME_SIZE];
	U8 output_name[IF_NAME_SIZE];
	U16 queue_modifier;
	U16 session_id;
} __attribute__((__packed__));

struct cdx_l2_bridge_remove_entry_cmd {
	U16 input_interface;
	U16 input_svlan;
	U16 input_cvlan;
	U8 destaddr[6];
	U8 srcaddr[6];
	U16 ethertype;
	U16 session_id;
	U16 reserved;
	U8 input_name[IF_NAME_SIZE];
} __attribute__((__packed__));

struct cdx_stat_action_pad_cmd {
	U16 action;
	U16 pad;
} __attribute__((__packed__));

struct cdx_ingress_policer_reset_cmd {
	U16 reserved1;
	U16 reserved2;
} __attribute__((__packed__));

struct cdx_ipr_statistics_cmd {
	U16 ackstats;
	struct ip_reassembly_info info;
};

#define CDX_RTP_SPECIAL_PAYLOAD_LEN	160

struct cdx_rtp_open_cmd {
	U16 call_id;
	U16 socket_a;
	U16 socket_b;
	U16 reserved;
};

struct cdx_rtp_close_cmd {
	U16 call_id;
	U16 reserved;
};

struct cdx_rtp_takeover_cmd {
	U16 call_id;
	U16 socket;
	U16 mode;
	U16 seq_number_base;
	U32 ssrc;
	U32 timestamp_base;
	U32 timestamp_incr;
	U32 ssrc_1;
	U8 param_flags;
	U8 marker_bit_conf_mode;
	U16 reserved;
};

struct cdx_rtp_control_cmd {
	U16 call_id;
	U16 control_dir;
	U16 vlan_pbit_conf;
	U16 reserved;
};

struct cdx_rtp_spec_tx_ctrl_cmd {
	U16 call_id;
	U16 type;
};

struct cdx_rtp_spec_tx_payload_cmd {
	U16 call_id;
	U16 payload_id;
	U16 payload_length;
	U16 payload[CDX_RTP_SPECIAL_PAYLOAD_LEN / 2];
};

struct cdx_rtcp_query_cmd {
	U16 socket_id;
	U16 flags;
};

struct cdx_rtp_dtmf_pt_cmd {
	U16 pt;
};

struct cdx_natpt_open_cmd {
	U16 socket_a;
	U16 socket_b;
	U16 control;
	U16 reserved;
};

struct cdx_natpt_close_cmd {
	U16 socket_a;
	U16 socket_b;
};

struct cdx_natpt_query_cmd {
	U16 reserved1;
	U16 socket_a;
	U16 socket_b;
	U16 reserved2;
};

#define CDX_CMD_LEN(_fcode, _type) \
	{ .fcode = (_fcode), .len = sizeof(_type), .type = CDX_CMD_LEN_EXACT }
#define CDX_CMD_LEN_BYTES(_fcode, _len) \
	{ .fcode = (_fcode), .len = (_len), .type = CDX_CMD_LEN_EXACT }
#define CDX_CMD_LEN_ALT(_fcode, _type1, _type2) \
	{ .fcode = (_fcode), .len = sizeof(_type1), .len2 = sizeof(_type2), .type = CDX_CMD_LEN_ALT_EXACT }
#define CDX_CMD_LEN_RANGE(_fcode, _min, _max) \
	{ .fcode = (_fcode), .len = (_min), .len2 = (_max), .type = CDX_CMD_LEN_RANGE }

static const struct cdx_cmd_len_spec cdx_cmd_len_specs[] = {
	CDX_CMD_LEN(CMD_RX_ENABLE, U16),
	CDX_CMD_LEN(CMD_RX_DISABLE, U16),
	CDX_CMD_LEN(CMD_RX_LRO, U16),

	CDX_CMD_LEN(CMD_RX_L2BRIDGE_ENABLE, L2BridgeEnableCommand),
	CDX_CMD_LEN(CMD_RX_L2BRIDGE_ADD, struct cdx_l2_bridge_add_entry_cmd),
	CDX_CMD_LEN(CMD_RX_L2BRIDGE_REMOVE, struct cdx_l2_bridge_remove_entry_cmd),
	CDX_CMD_LEN_BYTES(CMD_RX_L2BRIDGE_QUERY_STATUS, 0),
	CDX_CMD_LEN_BYTES(CMD_RX_L2BRIDGE_QUERY_ENTRY, 0),
	CDX_CMD_LEN(CMD_RX_L2BRIDGE_FLOW_ENTRY, L2BridgeL2FlowEntryCommand),
	CDX_CMD_LEN(CMD_RX_L2BRIDGE_MODE, L2BridgeControlCommand),
	CDX_CMD_LEN(CMD_RX_L2BRIDGE_FLOW_TIMEOUT, L2BridgeControlCommand),
	CDX_CMD_LEN_BYTES(CMD_RX_L2BRIDGE_FLOW_RESET, 0),
	CDX_CMD_LEN(CMD_BRIDGED_ITF_UPDATE, BridgedItfCommand),

	CDX_CMD_LEN(CMD_QM_RESET, QosResetCommand),
	CDX_CMD_LEN(CMD_QM_QOSENABLE, QosEnableCommand),
	CDX_CMD_LEN(CMD_QM_SHAPER_CONFIG, QosShaperConfigCommand),
	CDX_CMD_LEN(CMD_QM_WBFQ_CONFIG, QosWbfqConfigCommand),
	CDX_CMD_LEN(CMD_QM_CQ_CONFIG, QosCqConfigCommand),
	CDX_CMD_LEN(CMD_QM_CHNL_ASSIGN, QosChnlAssignCommand),
	CDX_CMD_LEN(CMD_QM_DSCP_Q_MAP_STATUS, QosDscpChnlClsq_mapCmd),
	CDX_CMD_LEN(CMD_QM_DSCP_Q_MAP_CFG, QosDscpChnlClsq_mapCmd),
	CDX_CMD_LEN(CMD_QM_DSCP_Q_MAP_RESET, QosDscpChnlClsq_mapCmd),
	CDX_CMD_LEN(CMD_QM_EXPT_RATE, QosExptRateCommand),
	CDX_CMD_LEN(CMD_QM_FF_RATE, QosFFRateCommand),
	CDX_CMD_LEN(CMD_QM_QUERY, QosQueryCmd),
	CDX_CMD_LEN(CMD_QM_QUERY_QUEUE, QosCqQueryCmd),
	CDX_CMD_LEN(CMD_QM_QUERY_FF_RATE, QosFFRateCommand),
	CDX_CMD_LEN(CMD_QM_QUERY_EXPT_RATE, QosExptRateCommand),
	CDX_CMD_LEN(CMD_QM_QUERY_IFACE_DSCP_FQID_MAP, QosIfaceDscpFqidMapCommand),
	CDX_CMD_LEN(CMD_QM_INGRESS_POLICER_ENABLE, IngressQosEnableCommand),
	CDX_CMD_LEN(CMD_QM_INGRESS_POLICER_CONFIG, IngressQosCfgCommand),
	CDX_CMD_LEN(CMD_QM_INGRESS_POLICER_RESET, struct cdx_ingress_policer_reset_cmd),
	CDX_CMD_LEN(CMD_QM_INGRESS_POLICER_QUERY_STATS, IngressQosStatCmd),
#ifdef SEC_PROFILE_SUPPORT
	CDX_CMD_LEN(CMD_QM_SEC_POLICER_CONFIG, QosSecRateCommand),
	CDX_CMD_LEN(CMD_QM_SEC_POLICER_QUERY_STATS, SecQosStatCmd),
	CDX_CMD_LEN(CMD_QM_SEC_POLICER_RESET, struct cdx_ingress_policer_reset_cmd),
#endif

	CDX_CMD_LEN_ALT(CMD_IPV4_CONNTRACK, CtCommand, CtExCommand),
	CDX_CMD_LEN(CMD_IP_ROUTE, RtCommand),
	CDX_CMD_LEN_BYTES(CMD_IPV4_RESET, 0),
	CDX_CMD_LEN(CMD_IPV4_SET_TIMEOUT, TimeoutCommand),
	CDX_CMD_LEN(CMD_IPV4_GET_TIMEOUT, CtCommand),
	CDX_CMD_LEN(CMD_IPV4_FF_CONTROL, FFControlCommand),
	CDX_CMD_LEN(CMD_IPV4_FRAGTIMEOUT, FragTimeoutCommand),
	CDX_CMD_LEN(CMD_IPV4_SAM_FRAGTIMEOUT, FragTimeoutCommand),
	CDX_CMD_LEN(CMD_IPV4_SOCK_OPEN, SockOpenCommand),
	CDX_CMD_LEN(CMD_IPV4_SOCK_CLOSE, SockCloseCommand),
	CDX_CMD_LEN(CMD_IPV4_SOCK_UPDATE, SockUpdateCommand),

	CDX_CMD_LEN_ALT(CMD_IPV6_CONNTRACK, CtCommandIPv6, CtExCommandIPv6),
	CDX_CMD_LEN_BYTES(CMD_IPV6_RESET, 0),
	CDX_CMD_LEN(CMD_IPV6_GET_TIMEOUT, CtCommandIPv6),
	CDX_CMD_LEN(CMD_IPV6_FRAGTIMEOUT, FragTimeoutCommand),
	CDX_CMD_LEN(CMD_IPV6_SOCK_OPEN, Sock6OpenCommand),
	CDX_CMD_LEN(CMD_IPV6_SOCK_CLOSE, Sock6CloseCommand),
	CDX_CMD_LEN(CMD_IPV6_SOCK_UPDATE, Sock6UpdateCommand),

	CDX_CMD_LEN_ALT(CMD_TX_ENABLE, U16, struct cdx_tx_enable_mac_cmd),
	CDX_CMD_LEN(CMD_TX_DISABLE, U16),
	CDX_CMD_LEN(CMD_PORT_UPDATE, PortUpdateCommand),
	CDX_CMD_LEN(CMD_TX_DSCP_VLANPCP_MAP_STATUS, DSCPVlanPCPMapCmd),
	CDX_CMD_LEN(CMD_TX_DSCP_VLANPCP_MAP_CFG, DSCPVlanPCPMapCmd),
	CDX_CMD_LEN(CMD_TX_QUERY_IFACE_DSCP_VLANPCP_MAP, QueryDSCPVlanPCPMapCmd),

	CDX_CMD_LEN(CMD_PPPOE_ENTRY, PPPoECommand),
	CDX_CMD_LEN(CMD_PPPOE_RELAY_ENTRY, PPPoERelayCommand),
	CDX_CMD_LEN(CMD_PPPOE_GET_IDLE, PPPoEIdleTimeCmd),

	CDX_CMD_LEN_RANGE(CMD_MC4_MULTICAST, MC4_MIN_COMMAND_SIZE, sizeof(MC4Command)),
	CDX_CMD_LEN_RANGE(CMD_MC6_MULTICAST, MC6_MIN_COMMAND_SIZE, sizeof(MC6Command)),

	CDX_CMD_LEN(CMD_RTP_OPEN, struct cdx_rtp_open_cmd),
	CDX_CMD_LEN(CMD_RTP_UPDATE, struct cdx_rtp_open_cmd),
	CDX_CMD_LEN(CMD_RTP_TAKEOVER, struct cdx_rtp_takeover_cmd),
	CDX_CMD_LEN(CMD_RTP_CONTROL, struct cdx_rtp_control_cmd),
	CDX_CMD_LEN(CMD_RTP_SPECTX_PLD, struct cdx_rtp_spec_tx_payload_cmd),
	CDX_CMD_LEN(CMD_RTP_SPECTX_CTRL, struct cdx_rtp_spec_tx_ctrl_cmd),
	CDX_CMD_LEN(CMD_RTCP_QUERY, struct cdx_rtcp_query_cmd),
	CDX_CMD_LEN(CMD_RTP_CLOSE, struct cdx_rtp_close_cmd),
	CDX_CMD_LEN(CMD_RTP_STATS_DTMF_PT, struct cdx_rtp_dtmf_pt_cmd),
	CDX_CMD_LEN_BYTES(CMD_VOICE_BUFFER_RESET, 0),

	CDX_CMD_LEN(CMD_VLAN_ENTRY, VlanCommand),
	CDX_CMD_LEN_BYTES(CMD_VLAN_ENTRY_RESET, 0),

	CDX_CMD_LEN(CMD_TNL_CREATE, TNLCommand_create),
	CDX_CMD_LEN(CMD_TNL_DELETE, TNLCommand_delete),
	CDX_CMD_LEN(CMD_TNL_UPDATE, TNLCommand_create),
#ifdef CDX_TODO_IPSEC
	CDX_CMD_LEN(CMD_TNL_IPSEC, TNLCommand_ipsec),
#endif
#ifdef CDX_TODO_TUNNEL
	CDX_CMD_LEN(CMD_TNL_4o6_ID_CONVERSION_dupsport, TNLCommand_IdConvDP),
	CDX_CMD_LEN(CMD_TNL_4o6_ID_CONVERSION_psid, TNLCommand_IdConvPsid),
#endif
	CDX_CMD_LEN(CMD_TNL_QUERY, TNLCommand_query),
	CDX_CMD_LEN(CMD_TNL_QUERY_CONT, TNLCommand_query),

#ifdef DPA_IPSEC_OFFLOAD
	CDX_CMD_LEN(CMD_IPSEC_SA_CREATE, CommandIPSecCreateSA),
	CDX_CMD_LEN(CMD_IPSEC_SA_DELETE, CommandIPSecDeleteSA),
	CDX_CMD_LEN_BYTES(CMD_IPSEC_SA_FLUSH, 0),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_KEYS, CommandIPSecSetKey),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_TUNNEL, CommandIPSecSetTunnel),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_NATT, CommandIPSecSetNatt),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_STATE, CommandIPSecSetState),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_LIFETIME, CommandIPSecSetLifetime),
	CDX_CMD_LEN(CMD_IPSEC_SA_ACTION_QUERY, SAQueryCommand),
	CDX_CMD_LEN(CMD_IPSEC_SA_ACTION_QUERY_CONT, SAQueryCommand),
	CDX_CMD_LEN(CMD_IPSEC_FRAG_CFG, CommandIPSecSetPreFrag),
	CDX_CMD_LEN(CMD_IPSEC_SA_SET_TNL_ROUTE, CommandIPSecSetTunnelRoute),
	CDX_CMD_LEN(CMD_IPSEC_SEC_FAILURE_STATS, fpp_sec_failure_stats_query_cmd_t),
	CDX_CMD_LEN(CMD_IPSEC_RESET_SEC_FAILURE_STATS, fpp_sec_failure_stats_query_cmd_t),
#endif

	CDX_CMD_LEN(CMD_STAT_ENABLE, StatEnableCmd),
	CDX_CMD_LEN(CMD_STAT_INTERFACE_PKT, StatInterfaceCmd),
	CDX_CMD_LEN(CMD_STAT_CONN, struct cdx_stat_action_pad_cmd),
	CDX_CMD_LEN(CMD_STAT_PPPOE_STATUS, struct cdx_stat_action_pad_cmd),
	CDX_CMD_LEN_BYTES(CMD_STAT_PPPOE_ENTRY, 0),
	CDX_CMD_LEN(CMD_STAT_BRIDGE_STATUS, struct cdx_stat_action_pad_cmd),
	CDX_CMD_LEN_BYTES(CMD_STAT_BRIDGE_ENTRY, 0),
	CDX_CMD_LEN(CMD_STAT_IPSEC_STATUS, StatIpsecStatusCmd),
	CDX_CMD_LEN_BYTES(CMD_STAT_IPSEC_ENTRY, 0),
	CDX_CMD_LEN(CMD_STAT_VLAN_STATUS, struct cdx_stat_action_pad_cmd),
	CDX_CMD_LEN_BYTES(CMD_STAT_VLAN_ENTRY, 0),
	CDX_CMD_LEN(CMD_STAT_TUNNEL_STATUS, StatTunnelStatusCmd),
	CDX_CMD_LEN_BYTES(CMD_STAT_TUNNEL_ENTRY, 0),
	CDX_CMD_LEN(CMD_STAT_FLOW, StatFlowStatusCmd),
	CDX_CMD_LEN(FPP_CMD_IPR_V4_STATS, struct cdx_ipr_statistics_cmd),
	CDX_CMD_LEN(FPP_CMD_IPR_V6_STATS, struct cdx_ipr_statistics_cmd),

	CDX_CMD_LEN(CMD_NATPT_OPEN, struct cdx_natpt_open_cmd),
	CDX_CMD_LEN(CMD_NATPT_CLOSE, struct cdx_natpt_close_cmd),
	CDX_CMD_LEN(CMD_NATPT_QUERY, struct cdx_natpt_query_cmd),

#ifdef CFG_WIFI_OFFLOAD
	CDX_CMD_LEN(CMD_WIFI_VAP_ENTRY, struct wifiCmd),
	CDX_CMD_LEN_BYTES(CMD_WIFI_VAP_QUERY, 0),
	CDX_CMD_LEN_BYTES(CMD_WIFI_VAP_RESET, 0),
#endif
};

static enum cdx_cmd_len_status cdx_validate_cmd_length(U16 fcode, U16 length)
{
	size_t ii;

	for (ii = 0; ii < ARRAY_SIZE(cdx_cmd_len_specs); ii++) {
		const struct cdx_cmd_len_spec *spec = &cdx_cmd_len_specs[ii];

		if (spec->fcode != fcode)
			continue;

		switch (spec->type) {
		case CDX_CMD_LEN_EXACT:
			return (length == spec->len) ? CDX_CMD_LEN_OK : CDX_CMD_LEN_BAD;
		case CDX_CMD_LEN_ALT_EXACT:
			return (length == spec->len || length == spec->len2) ?
				CDX_CMD_LEN_OK : CDX_CMD_LEN_BAD;
		case CDX_CMD_LEN_RANGE:
			return (length >= spec->len && length <= spec->len2) ?
				CDX_CMD_LEN_OK : CDX_CMD_LEN_BAD;
		}
	}

	return CDX_CMD_LEN_UNKNOWN;
}

int FCODE_TO_EVENT(U32 fcode)
{
	int eventid;
	switch((fcode & 0xFF00) >> 8)
	{
		case FC_RX:
			if (fcode >= L2BRIDGE_FIRST_COMMAND && fcode <= L2BRIDGE_LAST_COMMAND)
				eventid = EVENT_BRIDGE;
			else
				eventid = EVENT_PKT_RX;
			break;

		case FC_IPV4: eventid = EVENT_IPV4; break;

		case FC_IPV6: eventid = EVENT_IPV6; break;

		case FC_QM: eventid = EVENT_QM; break;

		case FC_TX: eventid = EVENT_PKT_TX; break;

		case FC_PPPOE: eventid = EVENT_PPPOE; break;

		case FC_MC: if(fcode <= CMD_MC4_RESET)
									eventid = EVENT_MC4;
								else 
									eventid = EVENT_MC6;             
								break;

		case FC_RTP: eventid = EVENT_RTP_RELAY; break;

		case FC_VLAN: eventid = EVENT_VLAN; break;

		case FC_IPSEC: eventid = EVENT_IPS_IN; break;

		case FC_TRC: eventid = EVENT_IPS_OUT; break;

		case FC_TNL:eventid = EVENT_TNL_IN; break;

		case FC_MACVLAN: eventid = EVENT_MACVLAN; break;

		case FC_STAT: eventid = EVENT_STAT; break;

		case FC_ALTCONF: eventid = EVENT_IPV4; break;

		case FC_WIFI_RX: eventid = EVENT_PKT_WIFIRX; break;

		case FC_NATPT: eventid = EVENT_NATPT; break;

		case FC_PKTCAP: eventid = EVENT_PKTCAP; break;

		case FC_FPPDIAG: eventid = EVENT_IPV4; break;

		case FC_ICC: eventid = EVENT_ICC; break;

		case FC_L2TP: eventid = EVENT_L2TP; break;

		default: eventid = -1; break;
	}

	return eventid;
}

int cdx_cmd_handler(U16 fcode, U16 length, U16 *payload, U16 *rlen, U16 *rbuf, U16 rbuf_len)
{
	CmdProc cmdproc;
	int eventid;
	enum cdx_cmd_len_status len_status;

	if (!rlen || !rbuf || (length && !payload))
		return -EINVAL;

	*rlen = 0;
	if (rbuf_len < sizeof(U16))
		return -EMSGSIZE;

	memset(rbuf, 0, rbuf_len);

	if (length > rbuf_len)
		return -EMSGSIZE;

	eventid = FCODE_TO_EVENT(fcode);
#ifdef CDX_DEBUG_ENABLE
	DPRINT("fcode=0x%04x, length=%d\n", fcode, length);
	print_hex_dump(KERN_DEBUG, "cmd: ", DUMP_PREFIX_NONE, 16, 1, payload, length, 1);
#endif
	len_status = cdx_validate_cmd_length(fcode, length);
	if (len_status == CDX_CMD_LEN_BAD) {
		rbuf[0] = ERR_WRONG_COMMAND_SIZE;
		*rlen = 2;
		goto out;
	}
/////////////////////////////////////////////////////////////////////////////
	// TEMP code to satisfy CMM
	if (fcode == CMD_VOICE_BUFFER_RESET)
	{
		rbuf[0] = NO_ERR;
		*rlen = 2;
	}
	else
/////////////////////////////////////////////////////////////////////////////
	if (eventid >= 0 && (cmdproc = gCmdProcTable[eventid]) != NULL)
	{
		if (len_status == CDX_CMD_LEN_UNKNOWN) {
			rbuf[0] = ERR_UNKNOWN_COMMAND;
			*rlen = 2;
			goto out;
		}
		if (length)
			memcpy(rbuf, payload, length);
		*rlen = (*cmdproc)(fcode, length, rbuf);
		if (*rlen == 0)
		{
			rbuf[0] = NO_ERR;
			*rlen = 2;
		}
	}
	else
	{
		rbuf[0] = ERR_UNKNOWN_COMMAND;
		*rlen = 2;
	}
	if (rbuf[0] != NO_ERR)
		DPRINT("rbuf[0]=0x%04x, *rlen=%d\n", rbuf[0], *rlen);

out:
	if (*rlen > rbuf_len)
		return -EMSGSIZE;

	return 0;
}

#define CMD_DECLARE(xx)		\
static BOOL xx##_init_flag = 0;	\
int xx##_init(void);		\
void xx##_exit(void);		\

#define CMD_INIT(xx) do {	\
	rc = xx##_init();	\
	if (rc < 0)		\
		goto exit;	\
	xx##_init_flag = 1;	\
	} while (0)

#define CMD_EXIT(xx) do {	\
	if (xx##_init_flag)	\
		xx##_exit();	\
	xx##_init_flag = 0;	\
	} while (0)

CMD_DECLARE(tx)
CMD_DECLARE(rx)
CMD_DECLARE(pppoe)
CMD_DECLARE(vlan)
CMD_DECLARE(ipv4)
CMD_DECLARE(ipv6)
CMD_DECLARE(socket)
CMD_DECLARE(tunnel)
CMD_DECLARE(natpt)
CMD_DECLARE(bridge)
CMD_DECLARE(qm)
CMD_DECLARE(statistics)
#ifdef DPA_IPSEC_OFFLOAD 
static BOOL ipsec_init_flag = 0;
#endif
#ifdef WIFI_ENABLE
CMD_DECLARE(wifi)
#endif
CMD_DECLARE(mc4)
CMD_DECLARE(mc6)
CMD_DECLARE(rtp_relay)
#ifdef CDX_TODO
CMD_DECLARE(pktcap)
CMD_DECLARE(l2tp)
#endif

int __init cdx_cmdhandler_init(void)
{
	int rc = 0;

	CMD_INIT(tx);
	CMD_INIT(rx);
	CMD_INIT(pppoe);
	CMD_INIT(vlan);
	CMD_INIT(ipv4);
	CMD_INIT(ipv6);
	CMD_INIT(socket);
	CMD_INIT(tunnel);
	CMD_INIT(natpt);
	CMD_INIT(bridge);
	CMD_INIT(qm);
	CMD_INIT(statistics);
#ifdef DPA_IPSEC_OFFLOAD 
	CMD_INIT(ipsec);
#endif
#ifdef WIFI_ENABLE
	CMD_INIT(wifi);
#endif
	CMD_INIT(mc4);
	CMD_INIT(mc6);
	CMD_INIT(rtp_relay);
#ifdef CDX_TODO
#ifdef WIFI_ENABLE
	CMD_INIT(wifi);
#endif
	CMD_INIT(pktcap);
	CMD_INIT(l2tp);
#endif

exit:
	return rc;
}

void __exit cdx_cmdhandler_exit(void)
{
	DPRINT("\n");

	// EXIT routines must be in reverse order from the INIT routines

#ifdef CDX_TODO
	CMD_EXIT(l2tp);
	CMD_EXIT(pktcap);
	CMD_EXIT(rtp_relay);
#endif
	CMD_EXIT(mc6);
	CMD_EXIT(mc4);
#ifdef WIFI_ENABLE
	CMD_EXIT(wifi);
#endif
#ifdef DPA_IPSEC_OFFLOAD 
	CMD_EXIT(ipsec);
#endif
	CMD_EXIT(statistics);
	CMD_EXIT(qm);
	CMD_EXIT(bridge);
	CMD_EXIT(natpt);
	CMD_EXIT(tunnel);
	CMD_EXIT(socket);
	CMD_EXIT(ipv6);
	CMD_EXIT(ipv4);
	CMD_EXIT(vlan);
	CMD_EXIT(pppoe);
	CMD_EXIT(rx);
	CMD_EXIT(tx);
}

int comcerto_fpp_send_command(u16 fcode, u16 length, u16 *payload, u16 *rlen, u16 *rbuf, u16 rbuf_len)
{
	struct _cdx_ctrl *ctrl = &cdx_info->ctrl;
	u16 tmp_rbuf[CDX_CMD_MAX_REPLY_LENGTH / sizeof(u16)];
	u16 tmp_rlen = 0;
	int rc;

	if (!rlen || !rbuf || (length && !payload))
		return -EINVAL;

	if (rbuf_len < sizeof(u16))
		return -EMSGSIZE;

	mutex_lock(&ctrl->mutex);

	rc = cdx_cmd_handler(fcode, length, payload, &tmp_rlen, tmp_rbuf, sizeof(tmp_rbuf));

	if (!rc) {
		if (tmp_rlen > rbuf_len) {
			rc = -EMSGSIZE;
			tmp_rlen = 0;
		} else if (tmp_rlen) {
			memcpy(rbuf, tmp_rbuf, tmp_rlen);
		}
	}
	*rlen = tmp_rlen;

	mutex_unlock(&ctrl->mutex);

	return rc;
}
EXPORT_SYMBOL(comcerto_fpp_send_command);

/**
 * comcerto_fpp_send_command_simple - 
 *
 *	This function is used to send command to FPP in a synchronous way. Calls to the function blocks until a response
 *	from FPP is received. This API can not be used to query data from FPP
 *	
 * Parameters
 *	fcode:		Function code. FPP function code associated to the specified command payload
 *	length:		Command length. Length in bytes of the command payload
 *	payload:	Command payload. Payload of the command sent to the FPP. 16bits buffer allocated by the client's code and sized up to 256 bytes
 *
 * Return values
 *	0:	Success
 *	<0:	Linux system failure (check errno for detailed error condition)
 *	>0:	FPP returned code
 */
int comcerto_fpp_send_command_simple(u16 fcode, u16 length, u16 *payload)
{
	u16 rbuf[128];
	u16 rlen;
	int rc;

	rc = comcerto_fpp_send_command(fcode, length, payload, &rlen, rbuf, sizeof(rbuf));

	/* if a command delivery error is detected, do not check command returned code */
	if (rc < 0)
		return rc;

	/* retrieve FPP command returned code. Could be error or acknowledgment */
	rc = rbuf[0];

	return rc;
}
EXPORT_SYMBOL(comcerto_fpp_send_command_simple);


void comcerto_fpp_workqueue(struct work_struct *work)
{
	struct _cdx_ctrl *ctrl = container_of(work, struct _cdx_ctrl, work);
	struct fpp_msg *msg;
	unsigned long flags;
	u16 rbuf[128];
	u16 rlen;
	int rc;

	spin_lock_irqsave(&ctrl->lock, flags);

	while (!list_empty(&ctrl->msg_list)) {

		msg = list_entry(ctrl->msg_list.next, struct fpp_msg, list);

		list_del(&msg->list);

		spin_unlock_irqrestore(&ctrl->lock, flags);

		rc = comcerto_fpp_send_command(msg->fcode, msg->length, msg->payload, &rlen, rbuf, sizeof(rbuf));

		/* send command response to caller's callback */
		if (msg->callback != NULL)
			msg->callback(msg->data, rc, rlen, rbuf);

		kfree(msg);

		spin_lock_irqsave(&ctrl->lock, flags);
	}

	spin_unlock_irqrestore(&ctrl->lock, flags);
}

/**
 * comcerto_fpp_send_command_atomic -
 *
 *	This function is used to send command to FPP in an asynchronous way. The Caller specifies a function pointer
 *	that is called by the FPP Comcerto driver when command reponse from FPP engine is received. This API can be also
 *	used to query data from FPP. Queried data are returned through the specified client's callback function
 *
 * Parameters
 *	fcode:		Function code. FPP function code associated to the specified command payload
 *	length:		Command length. Length in bytes of the command payload
 *	payload:	Command payload. Payload of the command sent to the FPP. 16bits buffer allocated by the client's code and sized up to 256 bytes
 *	callback:	Client's callback handler for FPP response processing
 *	data:		Client's private data. Not interpreted by the FPP driver and sent back to the Client as a reference (client's code own usage)
 *
 * Return values
 *	0:	Success
 *	<0:	Linux system failure (check errno for detailed error condition)
 **/
int comcerto_fpp_send_command_atomic(u16 fcode, u16 length, u16 *payload, void (*callback)(unsigned long, int, u16, u16 *), unsigned long data)
{
	struct _cdx_ctrl *ctrl = &cdx_info->ctrl;
	struct fpp_msg *msg;
	unsigned long flags;
	int rc;

	if (length > FPP_MAX_MSG_LENGTH) {
		rc = -EINVAL;
		goto err0;
	}

	msg = kmalloc(sizeof(struct fpp_msg) + length, GFP_ATOMIC);
	if (!msg) {
		rc = -ENOMEM;
		goto err0;
	}

	/* set caller's callback function */
	msg->callback = callback;
	msg->data = data;

	msg->payload = (u16 *)(msg + 1);

	msg->fcode = fcode;
	msg->length = length;
	memcpy(msg->payload, payload, length);

	spin_lock_irqsave(&ctrl->lock, flags);

	list_add(&msg->list, &ctrl->msg_list);

	spin_unlock_irqrestore(&ctrl->lock, flags);

	schedule_work(&ctrl->work);

	return 0;

err0:
	return rc;
}

EXPORT_SYMBOL(comcerto_fpp_send_command_atomic);


int cdx_ctrl_send_command_simple(u16 fcode, u16 length, u16 *payload)
{
	u16 rbuf[128];
	u16 rlen;
	int rc;

	/* send command to FE */
	rc = comcerto_fpp_send_command(fcode, length, payload, &rlen, rbuf, sizeof(rbuf));
	if (rc < 0)
		return rc;

	/* retrieve FE command returned code. Could be error or acknowledgment */
	rc = rbuf[0];

	return rc;
}


/**
 * comcerto_fpp_register_event_cb -
 *
 */
int comcerto_fpp_register_event_cb(int (*event_cb)(u16, u16, u16*))
{
	struct _cdx_ctrl *ctrl = &cdx_info->ctrl;

	/* register FCI callback used for asynchrounous event */
	ctrl->event_cb = event_cb;

	return 0;
}
EXPORT_SYMBOL(comcerto_fpp_register_event_cb);
