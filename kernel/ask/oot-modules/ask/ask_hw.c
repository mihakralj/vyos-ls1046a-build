// SPDX-License-Identifier: GPL-2.0
/*
 * ask_hw.c — ASK2 hardware backend (board-substrate consumer).
 *
 * HISTORY.  Through v1.3 this file drove the FMan PCD directly via the
 * ASK-flavor <linux/fsl/fman_pcd.h> / <linux/fsl/dpaa_flow_offload.h>
 * pointer API: a per-port private CC tree + KG scheme grafted onto the
 * kernel scheme by a pre-netdev hook, plus per-flow MANIP chains
 * (RMV_ETHERNET + INSRT_GENERIC + IPV4_FORWARD) allocated one-per-flow.
 * That API died with the single-image collapse, and the per-flow MANIP
 * model was the source of the `fman_pcd_manip_chain_create() failed -12`
 * MURAM-exhaustion blocker.
 *
 * BOARD SUBSTRATE (2026-06-15, updated 2026-07-27).  ask.ko now consumes ONLY
 * the exported COMMON-board FMan capability API (see include/ask_fman_caps.h):
 *
 *   - FE-VM offload via kernel API (Fork-B path):
 *       fman_pcd_fe_engage / fman_pcd_fe_disengage / fman_pcd_fe_flow_add / _del
 *     The FE-VM pipeline (pool/singletons/ehash/hashfe/enq/enter/arm) is built
 *     via the kernel API. Flow insert uses ask_fe_flow_insert() in
 *     ask_flow_offload.c. This is the Fork-B FE-VM ehash path.
 *
 * Fix C1 (2026-07-14): Removed Fork-A path (CC static tree via fman_cc_tree_install).
 * CR-007 (2026-07-27): Removed Fork-A shadow/HM bookkeeping (shadow[], nkeys,
 * next_key_id, cc_installed, cc_handle, hm_handle). The FE-VM ehash path
 * manages its own flow keys; the cookie now snapshots only port identity
 * and sink metadata for teardown.
 *
 * This file (Stage B of ask2-cc-repoint) reshapes bring-up/teardown and
 * the per-flow cookie onto that substrate; the per-flow insert path uses
 * the Fork-B FE-VM ehash path (ask_debugfs_fe_flow_write in ask_flow_offload.c).
 * Since the 2026-06-14 oot-ungate, ask.ko builds, signs, and packages
 * (dormant) into every single-image ISO; it engages only at runtime.
 */
#include <linux/fs.h>
#include <linux/file.h>

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/atomic.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/err.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/in.h>
#include <linux/if_ether.h>
#include <linux/if_vlan.h>
#include <linux/fsl/fman_pcd.h>
#include <linux/fsl/dpaa_wgfp.h>      /* WireGuard fast path family policy */
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/rtnetlink.h>       /* rtnl_lock/rtnl_unlock, CR-012 */
#include <linux/xarray.h>
#include <net/net_namespace.h>
#include <linux/etherdevice.h>          /* is_zero_ether_addr */
#include <linux/if_ether.h>             /* ETH_P_IP, ETH_ALEN */
#include <linux/in.h>                   /* IPPROTO_TCP */
#include <linux/unaligned.h>            /* get_unaligned_be32 */
#include <net/addrconf.h>               /* F-219: ipv6_addr_type, in6_dev_get */
#include <net/if_inet6.h>               /* F-219: struct inet6_dev / inet6_ifaddr */
#include <soc/fsl/qman.h>

#include <uapi/linux/ask/ask.h>	/* ASK_FAM_V4 / ASK_FAM_V6 family-mask bits */
#include "include/ask_internal.h"
#include "include/ask_fman_caps.h"      /* fman_cc_*, fman_hm_*, struct fman */

/*
 * OOT re-declarations of the mainline / board-substrate EXPORT_SYMBOLs the
 * vendored caps header does not surface (a bindeb-pkg linux-headers package
 * carries no driver headers, so re-declaring locally is the only mechanism).
 * fman_bind() resolves the FMan platform device's struct fman *.  The per-flow
 * CC-target resolvers dpaa_get_rx_fman_port() + dpaa_get_tx_fqid() (board patch
 * 0121) and fman_port_get_id() (board patch 0104) map an ASK flow's ingress /
 * egress netdevs to the (port_id, tx_fqid) the rebuild-via-install fast path
 * needs.  Mirrors the identical shim already linking in ask_flow_offload.c.
 */
struct device;
struct fman_port;
struct fman *fman_bind(struct device *dev);
struct device *fman_get_dev(struct fman *fman);
struct fman_port *dpaa_get_rx_fman_port(struct net_device *dev);
u8 fman_port_get_id(struct fman_port *port);
int dpaa_get_tx_fqid(struct net_device *dev, u32 queue, u32 *fqid);
/* F-199 (T-M7-2 S4): allocate a dedicated no-confirm TX FQ for the FE
 * hardware offload terminal (context_a B0V=0). Board patch exports it. */
int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);

/*
 * PER-PORT OFFLOAD FAMILY MASK (2026-08-21). IPv4 and IPv6 are selected
 * INDEPENDENTLY PER INTERFACE via the CLI `offload ipv4` / `offload ipv6`
 * knobs (the old single `offload ask` is removed). Userspace sends the mask
 * with ASK_CMD_ENGAGE (ASK_ATTR_FAMILY_MASK); a successful engage records it
 * here keyed by hardware port id. Flow admission
 * (ask_hw_flow_family_ok) consults the TRUE INGRESS port's mask — never the
 * interface's addresses and never engage timing — so late DHCP/SLAAC works
 * per-flow with no re-engage, exactly like IPv4 always has.
 *
 * The unified 46-byte dual-lane scheme/table (F-224/F-225) is family-neutral
 * and always programmed at engage regardless of the mask; the mask only gates
 * which families ask.ko publishes flow records for. Index is hw_port_id
 * (sparse 0x08..0x27, < 64). Default 0 = no family (a port not engaged, or
 * engaged with an explicit family, is set before its first flow).
 */
bool fman_pcd_v6_enabled(void);	/* F-210 export; still consulted by kernel */
static u8 ask_hw_port_family[64];
/* h->lock protects the sequence and each port/family's last transition. */
static u64 ask_hw_family_seq = 1;
static u64 ask_hw_family_changed[ARRAY_SIZE(ask_hw_port_family)][2];
static bool ask_hw_family_seq_exhausted;
static bool ask_hw_ready;

/* Ports engaged with the L2_DA profile (T-M6-2 B3a). Lockless for the routed,
 * VLAN and PPPoE gates, which must refuse them (bridge plan D1); bridge
 * records re-check under h->lock. */
static DECLARE_BITMAP(ask_hw_port_l2, 256);

static u8 ask_hw_port_family_get(u8 hw_port_id)
{
	if (hw_port_id < ARRAY_SIZE(ask_hw_port_family))
		return READ_ONCE(ask_hw_port_family[hw_port_id]);
	return 0;
}

/*
 * OFFLOAD GRANULARITY. The only per-port state is (engaged, family mask):
 * `offload ipv4|ipv6` on an interface. Everything else -- VLAN pop/push, NAT,
 * PPPoE encap/decap, L2 bridge, IPv6 hardware fragmentation -- is an automatic
 * capability of an engaged port, switched only by the global kill-switch module
 * parameters below (vlan_offload, nat44_offload, nat66_offload, pppoe_offload,
 * and ask.ipv6_hw_frag in ask_flow_offload.c). "Engaged" == non-zero family
 * mask: a successful engage sets it, disengage clears it, and flow admission
 * already keys off it.
 */

/*
 * T-M6-7.1 NAT offload arming gate. Default OFF: NAT/PAT flows fail closed to
 * software (the shipping contract). When set (modprobe ask nat44_offload=1) the
 * preflight stops rejecting the NAT action classes and ask.ko populates the
 * F-230 FE-VM rewrite opcodes. This is a silicon-experiment switch for the
 * S0..S3 gates; the fused-opcode encoding is UNPROVEN on 210.10.1, so this MUST
 * stay 0 in any shipping image until S1..S3 pass. A per-interface CLI knob
 * (T-M6-7.7) replaces it once the datapath is proven.
 */
#define ASK_HW_PORT_ETH0_MGMT 0x0c
/*
 * T-M6-7.7 productization: IPv4 NAT/PAT passed S0-S3 on 210.10.1
 * (SNAT+DNAT+masquerade, TCP 7.3 Gbit/s zero retr, UDP 500 Mbit/s zero loss).
 * Default ON for IPv4; remains runtime-disableable for diagnosis. IPv6 NAT is
 * NOT silicon-validated and is rejected in preflight even while this is true.
 */
static bool ask_nat44_offload = true;
module_param_named(nat44_offload, ask_nat44_offload, bool, 0644);
MODULE_PARM_DESC(nat44_offload,
		 "IPv4-to-IPv4 NAT/PAT FMan hardware offload (default 1; nat66 separate, nat46/nat64 not offloadable)");

bool ask_hw_nat44_offload_armed(void)
{
	bool armed = READ_ONCE(ask_nat44_offload);

	if (armed)
		pr_info_once("ask: IPv4 NAT/PAT hardware offload enabled (silicon-validated); IPv6 NAT and eth0 remain excluded\n");
	return armed;
}
EXPORT_SYMBOL_GPL(ask_hw_nat44_offload_armed);

/*
 * T-M6-7.8 NAT66 (IPv6<->IPv6 NAT). The F-230 emitter produces the fused v6 L3
 * opcode (0x2f = HOPLIMIT|SIP_V6|DIP_V6) with 16-byte address params. Passed
 * S0-S3 on 210.10.1 2026-08-23 (SNAT66/DNAT66/masquerade66, TCP -P4 7.13 Gbit/s
 * 0-retr + UDP 500 Mbit/s 0-loss, fused opcode 0x2d/0x2f + 16-byte v6 param
 * readback-confirmed, no wedge). Default ON; runtime-disableable for diagnosis.
 * NAT46/NAT64 (cross-family) are NOT offloadable — no FE family-conversion
 * opcode — and always fall back to software.
 */
static bool ask_nat66_offload = true;
module_param_named(nat66_offload, ask_nat66_offload, bool, 0644);
MODULE_PARM_DESC(nat66_offload,
		 "IPv6-to-IPv6 NAT FMan hardware offload (default 1; nat44 separate, nat46/nat64 not offloadable)");

bool ask_hw_nat66_offload_armed(void)
{
	bool armed = READ_ONCE(ask_nat66_offload);

	if (armed)
		pr_info_once("ask: IPv6 NAT66 hardware offload enabled (silicon-validated); eth0 excluded\n");
	return armed;
}
EXPORT_SYMBOL_GPL(ask_hw_nat66_offload_armed);

/*
 * VLAN pop/push is an automatic capability of ASK-engaged ports. Routed and
 * bridge records carry inline FE actions; disabling this gate withdraws those
 * records without re-engaging ports or disturbing unrelated ehash owners.
 */
static bool ask_vlan_offload = true;
static bool ask_vlan_disable_incomplete;

static int ask_vlan_offload_set(const char *val, const struct kernel_param *kp)
{
	bool enabled, old, ready;
	int rc;

	if (!try_module_get(THIS_MODULE))
		return -ENODEV;
	rc = kstrtobool(val ? val : "1", &enabled);
	if (rc)
		goto out;
	if (enabled && ask_vlan_disable_incomplete) {
		rc = -EBUSY;
		goto out;
	}
	old = READ_ONCE(ask_vlan_offload);
	ready = READ_ONCE(ask_hw_ready);
	if (ready && !enabled)
		ask_bridge_vlan_pause();
	WRITE_ONCE(ask_vlan_offload, enabled);
	if (!ready)
		goto changed;
	if (!enabled) {
		rc = ask_flow_vlan_flush();
		if (!rc)
			rc = ask_bridge_vlan_flush();
		ask_vlan_disable_incomplete = rc != 0;
		if (rc)
			ask_pr_err("hw: VLAN offload disable incomplete (%d); hardware records may remain, resolve retained owners or cold boot before enabling\n",
				   rc);
		else
			ask_bridge_vlan_resume();
	}
changed:
	if (old != enabled && enabled)
		ask_bridge_kick();
out:
	module_put(THIS_MODULE);
	return rc;
}

static const struct kernel_param_ops ask_vlan_offload_ops = {
	.set = ask_vlan_offload_set,
	.get = param_get_bool,
};
module_param_cb(vlan_offload, &ask_vlan_offload_ops, &ask_vlan_offload, 0644);
MODULE_PARM_DESC(vlan_offload,
		 "Single-tag 802.1Q VLAN pop/push FMan hardware offload (default 1; 0 withdraws live routed and bridge VLAN records; eth0/802.1ad/QinQ excluded)");

/*
 * Authoritative routed VLAN admission gate: switch on and ingress engaged.
 */
bool ask_hw_vlan_offload_armed_port(u8 hw_port_id)
{
	bool armed = READ_ONCE(ask_vlan_offload) &&
		     ask_hw_port_family_get(hw_port_id) &&
		     !test_bit(hw_port_id, ask_hw_port_l2);

	if (armed)
		pr_info_once("ask: single-tag 802.1Q VLAN hardware offload enabled (inline FE); eth0/802.1ad/QinQ excluded\n");
	return armed;
}
EXPORT_SYMBOL_GPL(ask_hw_vlan_offload_armed_port);

/*
 * Bridge (L2_DA) counterpart of ask_hw_vlan_offload_armed_port(): a bridge
 * pop/push record is admitted when the same vlan_offload kill switch is on
 * AND its ingress port is an engaged L2_DA port. The routed gate above
 * deliberately refuses L2 ports (their CC+HMTD path is not used), so the
 * bridge path must not reuse it (B7b run 2026-10-19: zero pop/push records).
 */
bool ask_hw_bridge_vlan_armed_port(u8 hw_port_id)
{
	return READ_ONCE(ask_vlan_offload) &&
	       ask_hw_port_family_get(hw_port_id) &&
	       test_bit(hw_port_id, ask_hw_port_l2);
}
EXPORT_SYMBOL_GPL(ask_hw_bridge_vlan_armed_port);

/*
 * Port-agnostic gate for the capability-advertise (ask_genl.c) and the
 * ask_intent_lower() fail-closed pre-check, neither of which has an ingress
 * port in hand: just the kill switch. The authoritative per-port decision is
 * still made downstream by ask_hw_vlan_offload_armed_port() at preflight and
 * CC insert.
 */
bool ask_hw_vlan_offload_armed(void)
{
	return READ_ONCE(ask_vlan_offload);
}
EXPORT_SYMBOL_GPL(ask_hw_vlan_offload_armed);

/*
 * L2 bridge FDB offload (T-M6-2) is an automatic capability of an engaged
 * bridge-member port (bridge plan D1), behind this global kill switch.
 * Clearing it closes admission and withdraws tracked bridge records; a
 * failed withdrawal may leave hardware forwarding.
 */
static bool ask_bridge_offload = true;
static bool ask_bridge_disable_incomplete;

static int ask_bridge_offload_set(const char *val,
				  const struct kernel_param *kp)
{
	bool enabled;
	int rc;

	if (!try_module_get(THIS_MODULE))
		return -ENODEV;
	rc = kstrtobool(val ? val : "1", &enabled);
	if (rc)
		goto out;
	if (enabled && ask_bridge_disable_incomplete) {
		rc = -EBUSY;
		goto out;
	}
	WRITE_ONCE(ask_bridge_offload, enabled);
	if (READ_ONCE(ask_hw_ready) && !enabled) {
		rc = ask_bridge_flush_all();
		ask_bridge_disable_incomplete = rc != 0;
		if (rc)
			ask_pr_err("hw: bridge offload disable incomplete (%d); hardware records may remain, resolve retained owners or cold boot before enabling\n",
				   rc);
	}
	ask_bridge_kick();
out:
	module_put(THIS_MODULE);
	return rc;
}

static const struct kernel_param_ops ask_bridge_offload_ops = {
	.set = ask_bridge_offload_set,
	.get = param_get_bool,
};
module_param_cb(bridge_offload, &ask_bridge_offload_ops, &ask_bridge_offload, 0644);
MODULE_PARM_DESC(bridge_offload,
		 "L2 bridge FDB hardware offload on engaged bridge-member ports (default 1; 0 closes admission and withdraws tracked bridge records)");

bool ask_hw_bridge_offload_armed(void)
{
	return READ_ONCE(ask_bridge_offload);
}
EXPORT_SYMBOL_GPL(ask_hw_bridge_offload_armed);


/*
 * PPPoE session offload (decap PPPoE->LAN and encap LAN->PPPoE, T-M6-SP4) is
 * an automatic capability of an engaged port, default ON. The session's
 * source-interface physical port must be engaged (it carries the PPPoE
 * frames); `offload pppoe` no longer exists. Turning the global switch off
 * closes admission and withdraws identified PPPoE flows. A failed
 * withdrawal leaves admission closed and may leave hardware flows active.
 */
static bool ask_pppoe_offload = true;
static bool ask_pppoe_disable_incomplete;

static int ask_pppoe_offload_set(const char *val, const struct kernel_param *kp)
{
	bool enabled;
	int rc;

	if (!try_module_get(THIS_MODULE))
		return -ENODEV;
	rc = kstrtobool(val ? val : "1", &enabled);
	if (rc)
		goto out;
	if (enabled && ask_pppoe_disable_incomplete) {
		rc = -EBUSY;
		goto out;
	}
	WRITE_ONCE(ask_pppoe_offload, enabled);
	if (READ_ONCE(ask_hw_ready) && !enabled) {
		rc = ask_flow_pppoe_flush_sync();
		ask_pppoe_disable_incomplete = rc != 0;
		if (rc)
			ask_pr_err("hw: PPPoE offload disable incomplete (%d); hardware records may remain, resolve retained owners or cold boot before enabling\n",
				   rc);
	}
out:
	module_put(THIS_MODULE);
	return rc;
}

static const struct kernel_param_ops ask_pppoe_offload_ops = {
	.set = ask_pppoe_offload_set,
	.get = param_get_bool,
};
module_param_cb(pppoe_offload, &ask_pppoe_offload_ops, &ask_pppoe_offload, 0644);
MODULE_PARM_DESC(pppoe_offload,
		 "PPPoE session decap/encap FMan hardware offload on ASK-engaged ports (default 1; 0 closes admission and withdraws identified records)");

bool ask_hw_pppoe_offload_armed(void)
{
	return READ_ONCE(ask_pppoe_offload);
}

bool ask_hw_pppoe_offload_armed_port(u8 hw_port_id)
{
	return ask_hw_pppoe_offload_armed() &&
	       ask_hw_port_family_get(hw_port_id) &&
	       !test_bit(hw_port_id, ask_hw_port_l2);
}
EXPORT_SYMBOL_GPL(ask_hw_pppoe_offload_armed_port);

static void ask_hw_set_family_locked(u8 hw_port_id, u8 family_mask)
{
	u8 old, changed;

	if (hw_port_id >= ARRAY_SIZE(ask_hw_port_family))
		return;
	family_mask &= ASK_FAM_V4 | ASK_FAM_V6;
	old = READ_ONCE(ask_hw_port_family[hw_port_id]);
	changed = old ^ family_mask;
	if (changed && !ask_hw_family_seq_exhausted) {
		if (ask_hw_family_seq == U64_MAX) {
			ask_hw_family_seq_exhausted = true;
			ask_pr_err("hw: family policy sequence exhausted; routed admission disabled\n");
		} else {
			++ask_hw_family_seq;
			if (changed & ASK_FAM_V4)
				ask_hw_family_changed[hw_port_id][0] = ask_hw_family_seq;
			if (changed & ASK_FAM_V6)
				ask_hw_family_changed[hw_port_id][1] = ask_hw_family_seq;
		}
	}
	WRITE_ONCE(ask_hw_port_family[hw_port_id], family_mask);
	/* A session's physical port may differ from its flow's ingress port. */
	if (old & ~family_mask)
		ask_flow_pppoe_flush();
}

/* Flow admission for the preflight + insert gates, gated by the INGRESS
 * port's selected family mask. TCP/UDP only (both families). Returns 0 if the
 * flow may be hardware-offloaded on @hw_port_id, -EOPNOTSUPP otherwise (SW
 * fallback). */
int ask_hw_flow_family_ok(u8 hw_port_id, const struct ask_flow_key *key)
{
	u8 fmask = ask_hw_port_family_get(hw_port_id);

	if (test_bit(hw_port_id, ask_hw_port_l2))	/* bridge plan D1 */
		return -EOPNOTSUPP;
	if (key->l4_proto != IPPROTO_TCP && key->l4_proto != IPPROTO_UDP)
		return -EOPNOTSUPP;
	if (key->l3_proto == ASK_FLOW_L3_IPV4)
		return (fmask & ASK_FAM_V4) ? 0 : -EOPNOTSUPP;
	if (key->l3_proto == ASK_FLOW_L3_IPV6)
		return (fmask & ASK_FAM_V6) ? 0 : -EOPNOTSUPP;
	return -EOPNOTSUPP;
}

/*
 * WireGuard fast path family policy (dpaa_wgfp.h): WireGuard has no offload
 * setting; dpaa_wgfp admits a flow by the same per-port mask and L2-port
 * exclusion as ask_hw_flow_family_ok(), for the ingress port (inner family)
 * and the port under the tunnel (outer family). Runs under RCU, no sleeping.
 */
static u8 ask_hw_wgfp_family(struct net_device *dev)
{
	struct fman_port *port;
	u8 id, m, out = 0;

	if (is_vlan_dev(dev))
		dev = vlan_dev_real_dev(dev);
	port = dpaa_get_rx_fman_port(dev);
	if (!port)
		return 0;
	id = fman_port_get_id(port);
	if (test_bit(id, ask_hw_port_l2))
		return 0;
	m = ask_hw_port_family_get(id);
	if (m & ASK_FAM_V4)
		out |= DPAA_WGFP_FAM_V4;
	if (m & ASK_FAM_V6)
		out |= DPAA_WGFP_FAM_V6;
	return out;
}

/*
 * QEF blob structural constants (PR13). The microcode version
 * reported by ASK_CMD_GET_INFO is extracted from the FMan firmware
 * blob the bootloader publishes via the device tree at
 * /soc/fman@1a00000/fman-firmware/fsl,firmware. See PR13 commit
 * comment in include/ask_internal.h for rationale.
 */
#define ASK_QEF_MAGIC          0x51454601u   /* 'Q' 'E' 'F' 0x01 */
#define ASK_QEF_MAGIC_OFFSET   4
#define ASK_QEF_DESC_OFFSET    8
#define ASK_QEF_DESC_LEN       64
#define ASK_QEF_MIN_LEN        (ASK_QEF_DESC_OFFSET + ASK_QEF_DESC_LEN)

/* Cached ucode version, populated on first probe. */
static struct ask_hw_ucode_version ask_hw_cached;
static bool ask_hw_cached_valid;

/*
 * Standard FMan parse-result byte offsets (RM 8.7.3 Table 8-107).
 * Used by the KG scheme extract recipe so the silicon emits a
 * deterministic byte stream into the downstream CC tree key buffer.
 *
 * Fix M2: The recipe now uses EKFC=0x001C0006 (IPSRC1 | IPDST1 | PTYPE1 |
 * L4PSRC | L4PDST) instead of the kernel's default 0x00180206 which included
 * IPSEC_SPI. The SPI field is removed because non-IPSec frames have undefined
 * SPI bytes, and PTYPE1 is added to distinguish TCP/UDP flows with the same
 * IP:port 4-tuple. This matches the spec requirement in
 * specs/fman-keygen-flow-key-spec.md §3.4.
 *
 * F-163 (2026-08-05): recipe extended to EKFC=0x801C0006, adding
 * KG_SCH_KN_PORT_ID (bit 31) so the extracted key carries a leading
 * ingress-port byte matching the real vendor cdx.ko external-hash key
 * format (union dpa_key, nxp-sdk branch cdx_common.h). Being the highest
 * set bit, PORT_ID lands first under the silicon's confirmed MSB-first
 * descending assembly order (spec §3.4).
 *
 * Total emitted key width = 14 bytes:
 * [PORT_ID:1][SIP:4][DIP:4][PROTO:1][SP:2][DP:2]. This is the Fork-B
 * FE-VM ehash path key format used by ask_fe_build_key() in
 * ask_flow_offload.c (ASK_FE_KEY_SIZE).
 */
#define ASK_HW_PR_OFF_IPV4_SIP  12
#define ASK_HW_PR_OFF_IPV4_DIP  16
#define ASK_HW_PR_OFF_L4_SPORT  20
#define ASK_HW_PR_OFF_L4_DPORT  22

#define ASK_HW_V4_KEY_WIDTH     14      /* F-163: was 13, +1 for PORT_ID prefix */

#define ASK_HW_MAX_PORTS        8       /* LS1046A has 8 BMI RX ports total */

#define ASK_HW_NOCONF_SLOTS     16      /* T-M7-2 S4: no-confirm TX FQ cache */

/*
 * Per-offloaded-port record.  Under the board substrate ask.ko owns no
 * private CC tree / KG scheme / pre-netdev hook — the CC tree lives in
 * CR-007 (2026-07-27): Fork-A shadow/HM bookkeeping removed. The FE-VM
 * ehash path (Fork-B) manages its own flow keys via ask_fe_flow_insert/remove().
 */
struct ask_hw_port {
	bool            in_use;
	u8              port_id;        /* BMI hwport id (sparse 0x01..0x31) */
	bool            offload_engaged;/* M1 coarse S1 mode-switch active (0129) */
	enum fman_pcd_fe_profile engaged_profile; /* valid while offload_engaged */
	u32             punt_mask;      /* F-266 FMAN_PCD_PUNT_* armed */
	bool            fe_retained;    /* F-289 FE disengage failed; FMan kept state */
	bool            teardown_in_progress;
	bool            module_pinned;
};

struct ask_hw_pcd {
	struct mutex    lock;
	struct fman     *fman;          /* shared FMan handle (fman_bind) */
	struct ask_hw_port port[ASK_HW_MAX_PORTS];

	/*
	 * Per-flow cookie indirection table.  u32 cookie -> struct
	 * ask_hw_flow_cookie{fm, port_id, sink_ifindex, sink_fqid}.
	 * XA_FLAGS_ALLOC1 keeps cookie 0 as the "no HW backing" sentinel.
	 */
	struct xarray   flow_cookies;

	/*
	 * T-M7-2 S4 (2026-08-15): per-egress-interface no-confirm TX FQ cache.
	 * The FE hardware terminal (F-198) must enqueue to a TX FQ whose FQD
	 * has B0V=0 so FMan emits no per-frame TX-confirm FD (the ~2.2 Gbps
	 * ceiling on the shared queue-0 TX FQ). dpaa_alloc_offload_tx_fq()
	 * (board patch F-199) allocates one such FQ per netdev on that
	 * netdev's own QMan channel; the FQ lives in the netdev's
	 * dpaa_fq_list and is freed by fsl_dpa on teardown, so ask.ko only
	 * caches the resolved FQID (never destroys it). Direct-mapped by
	 * ifindex % ASK_HW_NOCONF_SLOTS; a collision falls back to a fresh
	 * alloc without caching (correctness over reuse; impossible with the
	 * board's <=5 dpaa netdevs).
	 */
	struct {
		u32     ifindex;
		u32     fqid;
	}               noconf_tx[ASK_HW_NOCONF_SLOTS];

};

static struct ask_hw_pcd *ask_hw_pcd_inst;

void ask_hw_offload_set_family(u8 hw_port_id, u8 family_mask)
{
	struct ask_hw_pcd *h = READ_ONCE(ask_hw_pcd_inst);
	struct ask_hw_port *p = NULL;
	unsigned int i;

	if (!h) {
#ifdef ASK_KUNIT_EXPORTS
		ask_hw_set_family_locked(hw_port_id, family_mask);
#else
		if (family_mask)
			ask_pr_warn("hw: cannot set family on port 0x%02x without PCD\n",
				    hw_port_id);
#endif
		return;
	}

	mutex_lock(&h->lock);
	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use && h->port[i].port_id == hw_port_id) {
			p = &h->port[i];
			break;
		}
	}
	if (family_mask &&
	    (!p || !p->offload_engaged || p->fe_retained ||
	     p->teardown_in_progress)) {
		ask_pr_warn("hw: cannot set family on inactive or retained port 0x%02x\n",
			    hw_port_id);
	} else {
		ask_hw_set_family_locked(hw_port_id, family_mask);
	}
	mutex_unlock(&h->lock);
}
EXPORT_SYMBOL_GPL(ask_hw_offload_set_family);

int ask_hw_flow_policy_snapshot(u64 *seq)
{
	struct ask_hw_pcd *h;
	int rc = 0;

	if (!seq)
		return -EINVAL;
	*seq = 0;
	if (!try_module_get(THIS_MODULE))
		return -ESHUTDOWN;
	h = READ_ONCE(ask_hw_pcd_inst);
	if (!h) {
		rc = -ENODEV;
		goto put_module;
	}
	mutex_lock(&h->lock);
	if (!READ_ONCE(ask_hw_ready) || !h->fman)
		rc = -ENODEV;
	else if (ask_hw_family_seq_exhausted)
		rc = -EOVERFLOW;
	else
		*seq = ask_hw_family_seq;
	mutex_unlock(&h->lock);
put_module:
	module_put(THIS_MODULE);
	return rc;
}

int ask_hw_flow_publish_begin(const struct ask_flow_key *key, unsigned long enq_off,
			      struct ask_hw_pcd **guard, struct fman **fm)
{
	struct ask_hw_pcd *h;
	struct ask_hw_port *p = NULL;
	unsigned int i;
	int rc;

	if (!try_module_get(THIS_MODULE))
		return -ESHUTDOWN;
	h = READ_ONCE(ask_hw_pcd_inst);
	if (!h) {
		rc = -ENODEV;
		goto put_module;
	}

	mutex_lock(&h->lock);
	if (!READ_ONCE(ask_hw_ready) || !h->fman) {
		rc = -ENODEV;
		goto unlock;
	}
	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use && h->port[i].port_id == key->port_id) {
			p = &h->port[i];
			break;
		}
	}
	if (!p || !p->offload_engaged || !p->module_pinned ||
	    p->fe_retained || p->teardown_in_progress ||
	    p->engaged_profile != FMAN_PCD_FE_PROFILE_ROUTED) {
		rc = -ENODEV;
		goto unlock;
	}
	rc = ask_hw_flow_family_ok(key->port_id, key);
	if (rc)
		goto unlock;
	if (ask_hw_family_seq_exhausted) {
		rc = -EOVERFLOW;
		goto unlock;
	}
	if (!key->family_policy_seq || key->family_policy_seq > ask_hw_family_seq ||
	    ask_hw_family_changed[key->port_id]
		[key->l3_proto == ASK_FLOW_L3_IPV6] > key->family_policy_seq) {
		rc = -ESTALE;
		goto unlock;
	}
	if (ask_hw_get_enq_fe_off() != enq_off) {
		rc = -ESTALE;
		goto unlock;
	}

	*guard = h;
	*fm = h->fman;
	return 0;

unlock:
	mutex_unlock(&h->lock);
put_module:
	module_put(THIS_MODULE);
	return rc;
}

void ask_hw_flow_publish_end(struct ask_hw_pcd *guard)
{
	mutex_unlock(&guard->lock);
	module_put(THIS_MODULE);
}

bool ask_hw_flow_policy_current(const struct ask_flow_key *key)
{
	struct ask_hw_pcd *guard;
	struct fman *fm;
	int rc;

	rc = ask_hw_flow_publish_begin(key, ask_hw_get_enq_fe_off(), &guard, &fm);
	if (!rc)
		ask_hw_flow_publish_end(guard);
	return !rc;
}

/*
 * Control punt (F-266). On every port engaged with the ROUTED profile a
 * soft-parser routine sends selected frames to the port's default RX FQ (the
 * kernel), skipping KeyGen and the FE, as the vendor's cdx_sp.xml does:
 *
 * tcp_ctl_punt  TCP SYN/FIN/RST. With offload_delay_ms=0 the FE forwards the
 *               FIN/RST of an offloaded flow, the kernel never sees the
 *               close and conntrack keeps the entry ESTABLISHED for ~7300 s.
 * ttl_punt      IPv4 TTL <= 1 and IPv6 hop limit <= 1 (A14). The FE drops
 *               TTL 1 with no ICMP and rewrites hop limit 1 -> 0 and
 *               0 -> 255; the kernel sends ICMP time exceeded instead.
 *
 * Both default on. Punted frames skip KeyGen RSS, so they land on one CPU.
 * Writing a parameter re-applies it to ports that are already engaged. If
 * arming fails, the port stays engaged without that punt and logs a warning.
 */
static bool ask_tcp_ctl_punt = true;
static bool ask_ttl_punt = true;

/* p->punt_mask after a failed change: matches no wanted mask */
#define ASK_HW_PUNT_UNKNOWN	U32_MAX

/* Caller holds h->lock, or has exclusive access to h (teardown). */
static int ask_hw_punt_apply(struct ask_hw_pcd *h, struct ask_hw_port *p,
			     u32 want)
{
	int rc;

	if (want == p->punt_mask)
		return 0;
	rc = fman_pcd_ctl_punt_set(h->fman, p->port_id, want);
	if (rc) {
		/*
		 * A failed call may still have changed some HXS (fman_pcd tracks
		 * each one), so the hardware state is now unknown: mark it so
		 * the next apply, including 0 from disengage/teardown, always
		 * calls fman_pcd_ctl_punt_set() again and converges.
		 */
		ask_pr_warn("hw: control punt 0x%x -> 0x%x on port 0x%02x failed: %d\n",
			    p->punt_mask, want, p->port_id, rc);
		p->punt_mask = ASK_HW_PUNT_UNKNOWN;
		return rc;
	}
	ask_pr_info("hw: control punt 0x%x -> 0x%x on port 0x%02x\n",
		    p->punt_mask, want, p->port_id);
	p->punt_mask = want;
	return 0;
}

static u32 ask_hw_punt_wanted(const struct ask_hw_port *p)
{
	u32 mask = 0;

	if (!p->in_use || !p->offload_engaged ||
	    p->engaged_profile != FMAN_PCD_FE_PROFILE_ROUTED)
		return 0;
	if (READ_ONCE(ask_tcp_ctl_punt))
		mask |= FMAN_PCD_PUNT_TCP_CTL;
	if (READ_ONCE(ask_ttl_punt))
		mask |= FMAN_PCD_PUNT_TTL;
	return mask;
}

/*
 * Runs under kernel_param_lock(THIS_MODULE); ask_hw_pcd_teardown() clears
 * ask_hw_pcd_inst under the same lock, so h cannot be freed here.
 */
static int ask_punt_param_set(const char *val, const struct kernel_param *kp)
{
	struct ask_hw_pcd *h;
	unsigned int i;
	int rc = param_set_bool(val, kp);

	h = ask_hw_pcd_inst;
	if (rc || !h)
		return rc;
	mutex_lock(&h->lock);
	for (i = 0; i < ASK_HW_MAX_PORTS; i++)
		if (h->port[i].in_use)
			ask_hw_punt_apply(h, &h->port[i],
					  ask_hw_punt_wanted(&h->port[i]));
	mutex_unlock(&h->lock);
	return 0;
}

static const struct kernel_param_ops ask_punt_param_ops = {
	.set = ask_punt_param_set,
	.get = param_get_bool,
};
module_param_cb(tcp_ctl_punt, &ask_punt_param_ops, &ask_tcp_ctl_punt, 0644);
MODULE_PARM_DESC(tcp_ctl_punt,
		 "Send TCP SYN/FIN/RST on ROUTED-engaged ports to the kernel so conntrack sees offloaded flows close (default 1; applies to engaged ports on write)");
module_param_cb(ttl_punt, &ask_punt_param_ops, &ask_ttl_punt, 0644);
MODULE_PARM_DESC(ttl_punt,
		 "Send IPv4 TTL <= 1 and IPv6 hop limit <= 1 on ROUTED-engaged ports to the kernel for ICMP time exceeded (default 1; applies to engaged ports on write)");

/* F-113: kmem_cache for struct ask_hw_flow_cookie — O(1) alloc/free
 * under heavy flow churn (50k+ active flows).  Replaces per-entry
 * kzalloc/kfree to reduce slab fragmentation and allocation pressure. */
static struct kmem_cache *ask_hw_cookie_cache;

/* ENQ FE MURAM offset, captured during engage for use in flow insert.
 * F-110: Write-once-read-many — WRITE_ONCE() on engage (under h->lock),
 * READ_ONCE() in flow_offload REPLACE path (lockless, hot path). */
static unsigned long ask_hw_enq_fe_off;

/* ------------------------------------------------------------------------- */
/* QEF blob parsing                                                           */
/* ------------------------------------------------------------------------- */

static int ask_hw_qef_get_description(const u8 *blob, size_t len, char *desc)
{
	u32 magic;

	if (!blob || len < ASK_QEF_MIN_LEN) {
		ask_pr_warn("hw: firmware blob too short (%zu < %d)\n",
			    len, ASK_QEF_MIN_LEN);
		return -EINVAL;
	}

	magic = get_unaligned_be32(&blob[ASK_QEF_MAGIC_OFFSET]);

	if (magic != ASK_QEF_MAGIC) {
		ask_pr_warn("hw: firmware magic mismatch (got 0x%08x, want 0x%08x)\n",
			    magic, ASK_QEF_MAGIC);
		return -EINVAL;
	}

	memcpy(desc, blob + ASK_QEF_DESC_OFFSET, ASK_QEF_DESC_LEN);
	desc[ASK_QEF_DESC_LEN - 1] = '\0';
	return 0;
}

static int ask_hw_parse_desc(const char *desc,
			     struct ask_hw_ucode_version *out)
{
	unsigned int family, major, minor;
	int matched;

	matched = sscanf(desc, "Microcode version %u.%u.%u",
			 &family, &major, &minor);
	if (matched != 3) {
		ask_pr_warn("hw: unrecognised QEF description '%s'\n", desc);
		return -EINVAL;
	}

	if (family > U16_MAX || major > U8_MAX || minor > U8_MAX) {
		ask_pr_warn("hw: QEF version fields out of range: %u.%u.%u\n",
			    family, major, minor);
		return -EINVAL;
	}

	out->family = (u16)family;
	out->major  = (u8)major;
	out->minor  = (u8)minor;
	out->patch  = 0;
	strscpy(out->description, desc, sizeof(out->description));
	return 0;
}

static int ask_hw_probe_ucode_locked(struct ask_hw_ucode_version *out)
{
	struct device_node *np;
	const u8 *blob;
	int blob_len;
	char desc[ASK_QEF_DESC_LEN];
	int rc;

	np = of_find_compatible_node(NULL, NULL, "fsl,fman-firmware");
	if (!np) {
		ask_pr_warn("hw: no fsl,fman-firmware node in device tree\n");
		return -ENODEV;
	}

	blob = of_get_property(np, "fsl,firmware", &blob_len);
	if (!blob || blob_len <= 0) {
		ask_pr_warn("hw: fsl,firmware property missing or empty\n");
		of_node_put(np);
		return -ENOENT;
	}

	rc = ask_hw_qef_get_description(blob, (size_t)blob_len, desc);
	if (rc) {
		of_node_put(np);
		return rc;
	}

	rc = ask_hw_parse_desc(desc, out);
	of_node_put(np);
	if (rc)
		return rc;

	ask_pr_info("hw: FMan microcode %u.%u.%u (\"%s\")\n",
		    out->family, out->major, out->minor, out->description);
	return 0;
}

int ask_hw_ucode_get_version(struct ask_hw_ucode_version *out)
{
	int rc;

	if (!out)
		return -EINVAL;

	/* Acquire pairs with the smp_store_release() below so a reader that
	 * observes the valid flag is guaranteed to see the fully-published
	 * ask_hw_cached struct (never a torn copy) on weakly-ordered arm64. */
	if (smp_load_acquire(&ask_hw_cached_valid)) {
		*out = ask_hw_cached;
		return 0;
	}

	rc = ask_hw_probe_ucode_locked(out);
	if (rc)
		return rc;

	ask_hw_cached = *out;
	/* Publish the struct before the valid flag (release barrier). */
	smp_store_release(&ask_hw_cached_valid, true);
	return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_ucode_get_version);

/* ------------------------------------------------------------------------- */
/* Cookie indirection table (Phase 4.10 will populate from flow_offload)      */
/* ------------------------------------------------------------------------- */

/*
 * T-M6-A4 fault injection: fail every Nth cookie allocation (0 = off, the
 * default) so the board can prove a failed insert leaves no partial record.
 */
static unsigned int ask_fault_cookie_nth;
module_param_named(fault_cookie_nth, ask_fault_cookie_nth, uint, 0644);
MODULE_PARM_DESC(fault_cookie_nth,
		 "Fault injection: fail every Nth flow cookie allocation (0=off)");
static atomic_t ask_fault_cookie_cnt = ATOMIC_INIT(0);

u32 ask_hw_cookie_alloc(struct ask_hw_pcd *h,
			const struct ask_hw_flow_cookie *src)
{
	struct ask_hw_flow_cookie *entry;
	u32 cookie = 0;
	int rc;

	if (!h || !src)
		return 0;

	if (ask_fault_cookie_nth &&
	    !(atomic_inc_return(&ask_fault_cookie_cnt) % ask_fault_cookie_nth))
		return 0;

	/* F-113: Use kmem_cache for O(1) alloc under flow churn.
	 * Falls back to kzalloc if cache creation failed at bringup. */
	if (ask_hw_cookie_cache)
		entry = kmem_cache_zalloc(ask_hw_cookie_cache, GFP_KERNEL);
	else
		entry = kzalloc(sizeof(*entry), GFP_KERNEL);
	if (!entry)
		return 0;
	*entry = *src;

	rc = xa_alloc(&h->flow_cookies, &cookie, entry,
		      XA_LIMIT(1, U32_MAX), GFP_KERNEL);
	if (rc) {
		if (ask_hw_cookie_cache)
			kmem_cache_free(ask_hw_cookie_cache, entry);
		else
			kfree(entry);
		return 0;
	}
	return cookie;
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_alloc);

struct ask_hw_flow_cookie *
ask_hw_cookie_lookup(struct ask_hw_pcd *h, u32 cookie)
{
	if (!h || cookie == 0)
		return NULL;
	return xa_load(&h->flow_cookies, cookie);
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_lookup);

void ask_hw_cookie_free(struct ask_hw_pcd *h, u32 cookie)
{
	struct ask_hw_flow_cookie *entry;

	if (!h || cookie == 0)
		return;
	entry = xa_erase(&h->flow_cookies, cookie);
	/* F-113: Use kmem_cache_free for O(1) dealloc. */
	if (ask_hw_cookie_cache)
		kmem_cache_free(ask_hw_cookie_cache, entry);
	else
		kfree(entry);
}
EXPORT_SYMBOL_GPL(ask_hw_cookie_free);

/* ------------------------------------------------------------------------- */
/* Board-substrate bring-up / teardown                                        */
/* ------------------------------------------------------------------------- */

int ask_hw_pcd_bringup(void)
{
	struct ask_hw_pcd *h;
	struct device_node *np;
	struct platform_device *pdev;
	struct fman *fman;

	if (ask_hw_pcd_inst) {
		ask_pr_dbg("hw: pcd bringup already done\n");
		return 0;
	}

	np = of_find_compatible_node(NULL, NULL, "fsl,fman");
	if (!np) {
		ask_pr_info("hw: no fsl,fman in DT — HW offload not available\n");
		return 0;
	}

	pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!pdev) {
		ask_pr_info("hw: no platform_device for fsl,fman — HW offload not available\n");
		return 0;
	}

	fman = fman_bind(&pdev->dev);
	if (!fman) {
		ask_pr_info("hw: fman_bind() failed — HW offload not available\n");
		put_device(&pdev->dev);
		return 0;
	}

	h = kzalloc(sizeof(*h), GFP_KERNEL);
	if (!h) {
		put_device(&pdev->dev);
		return -ENOMEM;
	}

	mutex_init(&h->lock);
	h->fman = fman;
	xa_init_flags(&h->flow_cookies, XA_FLAGS_ALLOC1);

	/* F-113: Create kmem_cache for flow cookie entries.
	 * Falls back to kzalloc/kfree if cache creation fails (non-fatal). */
	ask_hw_cookie_cache = kmem_cache_create("ask_hw_flow_cookie",
		sizeof(struct ask_hw_flow_cookie),
		__alignof__(struct ask_hw_flow_cookie),
		0, NULL);
	if (!ask_hw_cookie_cache)
		ask_pr_warn("hw: kmem_cache_create failed — falling back to kzalloc\n");

	ask_hw_pcd_inst = h;

	/*
	 * Balance the of_find_device_by_node() reference.  fman_bind()
	 * took its own get_device(), which we deliberately hold for the
	 * module lifetime (the FMan platform device is static on LS1046A)
	 * to keep the cached struct fman * valid.
	 */
	put_device(&pdev->dev);

	ask_pr_info("hw: board-substrate FMan handle bound; per-flow CC/HM offload available\n");
	return 0;
}

/* F-109: debugfs_fe_write forward declaration removed — all callers
 * now use kernel API (fman_pcd_fe_disengage, fman_pcd_fe_flow_add). */
void ask_hw_pcd_teardown(void)
{
	struct ask_hw_pcd *h = ask_hw_pcd_inst;
	struct ask_hw_flow_cookie *ck;
	unsigned long idx;
	unsigned int i;

	if (!h)
		return;

	mutex_lock(&h->lock);
	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		struct ask_hw_port *p = &h->port[i];

		if (p->in_use && (p->offload_engaged || p->fe_retained ||
				  p->teardown_in_progress || p->module_pinned)) {
			ask_pr_err("hw: teardown refused: port 0x%02x still owns FE state\n",
				   p->port_id);
			mutex_unlock(&h->lock);
			return;
		}
	}
	mutex_unlock(&h->lock);

	/* F-266: fence ask_punt_param_set(), which dereferences the instance. */
	kernel_param_lock(THIS_MODULE);
	ask_hw_pcd_inst = NULL;
	kernel_param_unlock(THIS_MODULE);

	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		struct ask_hw_port *p = &h->port[i];

		if (p->in_use)
			ask_hw_punt_apply(h, p, 0);	/* F-266 */
	}
	/*
	 * Drain any flow cookies that survived to teardown.
	 * CR-007: Fork-A HM refcount balancing removed — the FE-VM ehash
	 * path (Fork-B) manages its own teardown via fman_pcd_fe_flow_del().
	 */
	xa_for_each(&h->flow_cookies, idx, ck) {
		if (!ck)
			continue;
		xa_erase(&h->flow_cookies, idx);
		/* F-113: Use kmem_cache_free for O(1) dealloc. */
		if (ask_hw_cookie_cache)
			kmem_cache_free(ask_hw_cookie_cache, ck);
		else
			kfree(ck);
	}

	/* 2026-08-21 LEAK FIX: global TX-confirm bypass removed (see engage);
	 * no per-port TX register state to restore on teardown. */

	/* CR-007: Fork-A CC static tree teardown removed — cc_installed was
	 * never set after C1 removed the Fork-A path. */

	xa_destroy(&h->flow_cookies);
	mutex_destroy(&h->lock);
	if (h->fman)
		put_device(fman_get_dev(h->fman));
	/* F-113: Destroy the flow-cookie kmem_cache. */
	if (ask_hw_cookie_cache) {
		kmem_cache_destroy(ask_hw_cookie_cache);
		ask_hw_cookie_cache = NULL;
	}
	kfree(h);
	ask_pr_dbg("hw: pcd teardown complete\n");
}

struct ask_hw_pcd *ask_hw_pcd_get(void)
{
	return ask_hw_pcd_inst;
}
#ifdef ASK_KUNIT_EXPORTS
EXPORT_SYMBOL_GPL(ask_hw_pcd_get);
#endif

/*
 * Fix B: expose the cached FMan handle (resolved via fman_bind() at bringup)
 * so ask_flow_offload.c can drive fman_pcd_fe_flow_add/_del against the real
 * FMan instead of passing NULL. Returns NULL before bringup / after teardown.
 */
struct fman *ask_hw_get_fman(void)
{
	return ask_hw_pcd_inst ? ask_hw_pcd_inst->fman : NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_get_fman);

/* T-M7-2 S1: recover the per-egress-interface TX FQID saved in the
 * ask_hw_flow_insert() cookie. Cookie entries are freed immediately after
 * xa_erase(), so hold the xarray lock until the value has been copied. */
int ask_hw_flow_get_sink_fqid(u32 hw_flow_id, u32 *fqid)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_flow_cookie *ck;
	unsigned long flags;

	if (!h || !fqid || !hw_flow_id)
		return -EINVAL;

	*fqid = 0;
	xa_lock_irqsave(&h->flow_cookies, flags);
	ck = ask_hw_cookie_lookup(h, hw_flow_id);
	if (ck)
		*fqid = READ_ONCE(ck->sink_fqid);
	xa_unlock_irqrestore(&h->flow_cookies, flags);

	return *fqid ? 0 : -ENOENT;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_get_sink_fqid);

/*
 * T-M6-8 DIAGNOSTIC: no-confirm TX FQ backlog probe.
 *
 * ORIGIN (2026-08-25, HISTORICAL): the retired inline FE-VM VLAN path
 * (F-233/F-234) froze after ~20 packets with no ErrFD / no FMFP stall / no FE
 * workspace depletion, and F-234 (bpid + MURAM frag word2) did not help. The
 * FQ-probe verdict was frm_cnt=0 -- the VLAN-rebuilt (STRIP_ETH+INSERT_L2)
 * frames never reached the TX FQ; the FE-VM stopped enqueuing after ~20 (a
 * 5+tnums per-task management-index the strip/rebuild handlers consume and
 * never release). That freeze is RESOLVED: the 2026-08-26 R1-R5
 * re-architecture retired the inline FE-VM VLAN emitter and moved VLAN
 * pop/push to a CC-leaf AD -> combined VLAN HMTD (a separate HM engine, not
 * the FE-VM), so the freeze cannot recur (silicon-validated R4c-2/R4c-3; see
 * ask_vlan_cc.c and plans/ASK2-VLAN-REARCH.md). ask_fe_flow_insert() now
 * rejects any VLAN flow with -EOPNOTSUPP; the FE-VM never touches a VLAN frame.
 *
 * The probe itself is kept as a general-purpose read-only backlog inspector
 * for ANY per-port no-confirm TX FQ (routed/NAT/VLAN). qman_query_fq_np()
 * reads the FQD's non-programmable fields including frm_cnt (frames
 * enqueued-not-dequeued = backlog) and state. Writing 1 to
 * /sys/module/ask/parameters/fq_probe walks the cached per-port no-confirm FQs
 * and logs each FQ's live state + backlog.
 *
 * Interpretation while inspecting a suspect flow:
 *   frm_cnt stuck high / rising -> frames enqueued but NOT draining (QMan/TX/
 *       EBD buffer-recycle side): the FQ is backlogged.
 *   frm_cnt ~0 + byte_cnt ~0    -> frames never reach the FQ (producer side).
 *   state != ACTIVE/SCHED       -> the FQ retired/parked/held-active (OAC /
 *       congestion / order-restoration wedge).
 * Read-only: issues QM MC QUERYFQ_NP commands only; touches no datapath state.
 */
static int ask_fq_probe_set(const char *val, const struct kernel_param *kp)
{
	struct ask_hw_pcd *h = ask_hw_pcd_inst;
	unsigned int i, shown = 0;
	bool trig;
	int rc;

	rc = kstrtobool(val, &trig);
	if (rc)
		return rc;
	if (!trig)
		return 0;
	if (!h) {
		pr_info("ask: FQ-PROBE: no HW instance (offload not engaged)\n");
		return 0;
	}

	mutex_lock(&h->lock);

	for (i = 0; i < ASK_HW_NOCONF_SLOTS; i++) {
		struct qman_fq fq;
		struct qm_mcr_queryfq_np np;

		if (!h->noconf_tx[i].fqid)
			continue;
		memset(&fq, 0, sizeof(fq));
		fq.fqid = h->noconf_tx[i].fqid;
		memset(&np, 0, sizeof(np));
		rc = qman_query_fq_np(&fq, &np);
		if (rc) {
			pr_info("ask: FQ-PROBE slot=%u ifindex=%u fqid=0x%x query rc=%d\n",
				i, h->noconf_tx[i].ifindex, fq.fqid, rc);
		} else {
			pr_info("ask: FQ-PROBE slot=%u ifindex=%u fqid=0x%x state=0x%02x frm_cnt=%u byte_cnt=%u fqd_link=0x%x\n",
				i, h->noconf_tx[i].ifindex, fq.fqid,
				np.state & QM_MCR_NP_STATE_MASK,
				qm_mcr_np_get(&np, frm_cnt), np.byte_cnt,
				qm_mcr_np_get(&np, fqd_link));
		}
		shown++;
	}

	mutex_unlock(&h->lock);

	if (!shown)
		pr_info("ask: FQ-PROBE: no no-confirm TX FQs cached yet (no HW flow inserted)\n");
	return 0;
}

static const struct kernel_param_ops ask_fq_probe_ops = {
	.set = ask_fq_probe_set,
	/* write-only trigger; no .get */
};
module_param_cb(fq_probe, &ask_fq_probe_ops, NULL, 0200);
MODULE_PARM_DESC(fq_probe,
		 "T-M6-8 diagnostic: write 1 to log no-confirm TX FQ state + backlog (frm_cnt) via QMan QUERYFQ_NP");

/*
 * A6-LOSS step6 DIAGNOSTIC: live CGR congestion-state probe.
 *
 * dpaa_eth.c's dpaa_fq_init() already places every FQ_TYPE_TX_NO_CONFIRM FQ
 * (the no-confirm TX FQs this module caches in h->noconf_tx[]) into the
 * owning netdev's own egress CGR (QM_FQCTRL_CGE set, fqd.cgid =
 * priv->cgr_data.cgr.cgrid, CS tail-drop + CSCN enabled at CGR init time
 * with a 256 MiB threshold on 10G ports). That CGR's cgrid is private to
 * dpaa_eth and not exported, so this probe cannot name which cgrid belongs
 * to eth3/eth4 -- it instead calls the exported qman_query_cgr_congested()
 * across the full 0..255 cgrid space and logs only the IDs currently
 * reporting congested=1. QUERYCGR is a read-only QMan management command
 * (same class as QUERYFQ_NP); this touches no datapath state.
 *
 * Decisive question this answers: when h->noconf_tx[]'s frm_cnt/byte_cnt
 * (per fq_probe) show a deep, sustained backlog on a no-confirm TX FQ, does
 * ANY CGR ever actually report congested=1? If none do despite a backlog
 * far above the configured 256 MiB CS threshold, the CGR's congestion
 * integrator is not being updated by the FE hardware's direct ENQUEUE_PKT
 * dispatch into that FQ -- i.e. CGR/CSTD is cosmetically configured on the
 * FQD but functionally inert for this producer path, and a per-FQ FQTD
 * threshold (evaluated locally against the FQD's own counters, not routed
 * through a separate CG integrator) becomes the next candidate instead.
 */
static int ask_cgr_probe_set(const char *val, const struct kernel_param *kp)
{
	unsigned int i, hits = 0;
	bool trig;
	int rc;

	rc = kstrtobool(val, &trig);
	if (rc)
		return rc;
	if (!trig)
		return 0;

	for (i = 0; i < 256; i++) {
		struct qman_cgr cgr = { .cgrid = i };
		bool congested = false;

		rc = qman_query_cgr_congested(&cgr, &congested);
		if (rc)
			continue; /* unallocated/invalid cgrid: expected for most IDs */
		if (congested) {
			pr_info("ask: CGR-PROBE cgrid=%u congested=1\n", i);
			hits++;
		}
	}

	pr_info("ask: CGR-PROBE scan done: %u/%u cgrid(s) congested\n", hits, 256);
	return 0;
}

static const struct kernel_param_ops ask_cgr_probe_ops = {
	.set = ask_cgr_probe_set,
	/* write-only trigger; no .get */
};
module_param_cb(cgr_probe, &ask_cgr_probe_ops, NULL, 0200);
MODULE_PARM_DESC(cgr_probe,
		 "A6-LOSS diagnostic: write 1 to scan cgrid 0-255 via qman_query_cgr_congested() and log congested IDs");

/* ------------------------------------------------------------------------- */
/* M1 coarse dataplane mode-switch (control-plane plumbing; ships dormant)    */
/* ------------------------------------------------------------------------- */

/* F-109: debugfs_fe_write(), debugfs_fe_read(), and parse_enq_offset()
 * DELETED.  All callers now use kernel API:
 *   - fman_pcd_fe_disengage() replaces debugfs_fe_write("fe_arm", ...)
 *   - fman_pcd_fe_enq_get_offset() replaces debugfs_fe_read("fe_enq", ...)
 *   - fman_pcd_fe_flow_add() replaces ask_debugfs_fe_flow_write()
 * Debugfs is for diagnostics only (Decision 10, 2026-07-19).
 */

/* Accessor for ENQ FE offset (used by flow_offload REPLACE handler).
 * F-110: READ_ONCE() — this is called lockless from the flow offload
 * hot path.  The writer (engage) holds h->lock and uses WRITE_ONCE(). */
unsigned long ask_hw_get_enq_fe_off(void)
{
	return READ_ONCE(ask_hw_enq_fe_off);
}
EXPORT_SYMBOL_GPL(ask_hw_get_enq_fe_off);

/* Defined further down with the per-flow fast path; forward-declared here. */
static struct ask_hw_port *ask_hw_port_slot_get(struct ask_hw_pcd *h,
						u8 port_id);

/*
 * Engage/disengage the coarse S0<->S1 PCD mode-switch on one FMan RX port.
 * These forward to the board-exported fman_pcd_offload_engage()/_disengage()
 * (board patch 0129) - the EXACT reversible KGSE_CCBS graft sequence proven by
 * the cc_test harness + 100x soak.  A per-port "engaged" flag makes both
 * idempotent so a double-engage / stray-disengage is a safe no-op, and
 * teardown reverts any port left engaged.  M1 carries no classification
 * semantics and is NOT wired to a traffic path - this is control-plane
 * plumbing only (the traffic-carrying engage is blocked on M2 AC_CC
 * disposition).  Triggered manually via /sys/kernel/debug/ask/offload; M7
 * routes `set system offload ask` here.
 *
 * @from_flow: the caller is the lazy flowtable REPLACE path
 * (ask_hw_port_bind()). It may only (re)arm a port the operator has enabled,
 * i.e. one with a non-zero family mask, and never applies the both-families
 * compatibility default below. Checked under h->lock, which
 * ask_hw_offload_disengage() also holds while clearing the mask, so a
 * flowtable callback can never re-engage a port after `delete ... offload`.
 */
static int ask_hw_offload_engage_profile(u8 hw_port_id,
					 enum fman_pcd_fe_profile profile,
					 bool from_flow, u8 family_mask)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_port *p;
	u8 family;
	int rc;

	if (!h)
		return -ENODEV;
	if (!READ_ONCE(ask_hw_ready))
		return -EAGAIN;

	mutex_lock(&h->lock);

	if (from_flow && !ask_hw_port_family_get(hw_port_id)) {
		rc = -ENODEV;	/* administratively disabled: stay in software */
		goto out_unlock;
	}

	p = ask_hw_port_slot_get(h, hw_port_id);
	if (!p) {
		rc = -ENOSPC;
		goto out_unlock;
	}

	if (p->fe_retained) {
		rc = -ESHUTDOWN;
		goto out_unlock;
	}
	if (p->teardown_in_progress) {
		rc = -EAGAIN;
		goto out_unlock;
	}

	family = family_mask ? family_mask : ask_hw_port_family_get(hw_port_id);
	if (!family)
		family = ASK_FAM_V4 | ASK_FAM_V6;
	if (ask_hw_family_seq_exhausted ||
	    (ask_hw_family_seq == U64_MAX &&
	     family != ask_hw_port_family_get(hw_port_id))) {
		rc = -EOVERFLOW;
		goto out_unlock;
	}

	if (p->offload_engaged) {
		/* Same profile is idempotent; another one needs a disengage. */
		rc = p->engaged_profile == profile ? 0 : -EBUSY;
		if (rc)
			ask_pr_err("hw: port 0x%02x already engaged with profile %d\n",
				   hw_port_id, p->engaged_profile);
		else
			ask_hw_set_family_locked(hw_port_id, family);
		goto out_unlock;
	}

	/*
	 * The unified 46-byte scheme is family-neutral. Publish the selected
	 * mask only after a successful arm so failed or transitioning ports
	 * cannot admit flows.
	 */

	/* F-092: Build + arm FE-VM via kernel API (not debugfs).
	 * fman_pcd_fe_engage() now builds the full VM chain via
	 * __fman_pcd_fe_build_vm_chain(), creates the CONT_LOOKUP scaffold
	 * with numKeys=1 (F-091), and arms the port for FE-VM dispatch.
	 * This replaces the debugfs bridge — debugfs is for diagnostics only.
	 *
	 * F-157 (2026-08-01) historically passed a module-global QMan FQ
	 * pinned to channel 0x801 = eth4/MAC10 TX as
	 * the shared FE-VM ENQ-singleton target. That is WRONG for any ingress
	 * port other than eth4: the shared ENQ singleton feeds the record's
	 * next-FE for flows inserted WITHOUT a per-egress action.tx_fqid, so a
	 * non-eth4 ingress flow would enqueue onto eth4's channel and the dpaa
	 * driver drops the cross-port frame (E25/E26). Five-port readiness:
	 * pass 0 so the shared ENQ singleton falls back to the generic 0x200
	 * builder default (F-175), decoupling it from eth4. The production
	 * routed HIT terminal is per-egress via action.tx_fqid (F-198/F-199)
	 * and is unaffected; the CC MISS disposition is already per-ingress-port
	 * via fman_pcd_resolve_miss_fqid() inside fman_pcd_fe_engage().
	 */
	/* Keep ASK's callbacks and owners resident until the port is cleanly
	 * disengaged. An engage error may have left hardware state behind. */
	if (!try_module_get(THIS_MODULE)) {
		rc = -ENODEV;
		goto out_unlock;
	}
	p->module_pinned = true;
	rc = fman_pcd_fe_engage_profile(h->fman, hw_port_id, 0, profile);
	if (rc) {
		p->fe_retained = true;
		ask_hw_set_family_locked(hw_port_id, 0);
		ask_pr_err("hw: fman_pcd_fe_engage port 0x%02x failed: %d; FE ownership retained\n",
			   hw_port_id, rc);
		goto out_unlock;
	}

	/* F-109: Capture ENQ FE offset via kernel API instead of
	 * debugfs loopback (filp_open + kernel_read + sscanf).
	 * F-110: WRITE_ONCE() — paired with READ_ONCE() in the
	 * lockless flow_offload REPLACE hot path. */
	WRITE_ONCE(ask_hw_enq_fe_off, fman_pcd_fe_enq_get_offset(h->fman));
	ask_pr_info("hw: ENQ FE offset 0x%lx (kernel API)\n",
		    ask_hw_enq_fe_off);

	/*
	 * Enable silicon HIT-release on all FMan TX ports so that
	 * HIT frames bypass QMan and go directly to the wire.
	 * F-108: Refcount-guarded — only enable hardware bypass on the
	 * first engage (0→1 transition).  Subsequent engages on other
	 * ports increment the refcount without touching hardware.
	 * Reversed symmetrically in ask_hw_offload_disengage() so the
	 * S1→S0 cycle restores the TX-confirm bit to its S0 default and
	 * `pcd-snapshot diff` stays byte-clean (DUAL-DATAPLANE.md M1
	 * reversibility contract).  Also reversed in ask_hw_pcd_teardown()
	 * for module-unload cleanup as a belt-and-suspenders.
	 */
	/*
	 * 2026-08-21 LEAK FIX: the global TX-confirm bypass
	 * (fman_port_set_silicon_hit_release_all) is REMOVED. It set
	 * NIA_BMI_AC_TX_RELEASE on EVERY TX port, which suppressed the
	 * TX-confirm FD for the kernel's confirmed egress_fqs[] too, so any
	 * kernel-forwarded skb (software flowtable / plain forward) on those
	 * ports was never freed -> ~1.7 GB/s skbuff_head leak -> OOM. No-
	 * confirm behaviour is now provided PER-FRAME by the per-egress-port
	 * no-confirm FQ (F-198/F-199, B0V=0/EBD=1) that the HIT record's
	 * ENQUEUE_PKT targets via ask_hw_resolve_oif_fqid(); the resolver now
	 * fails closed (SW forward) rather than using a confirmed FQ, so a
	 * HIT frame never lands on a confirmed FQ. Nothing to arm here.
	 */

	p->offload_engaged = true;
	p->engaged_profile = profile;
	if (profile == FMAN_PCD_FE_PROFILE_ROUTED)
		clear_bit(hw_port_id, ask_hw_port_l2);
	else
		set_bit(hw_port_id, ask_hw_port_l2);
	ask_hw_set_family_locked(hw_port_id, family);
	/* F-266: optional; a failure leaves the port engaged without it. */
	ask_hw_punt_apply(h, p, ask_hw_punt_wanted(p));
	ask_pr_info("hw: offload ENGAGED on port 0x%02x (S0->S1)\n", hw_port_id);

out_unlock:
	mutex_unlock(&h->lock);
	return rc;
}

int ask_hw_offload_engage(u8 hw_port_id)
{
	return ask_hw_offload_engage_profile(hw_port_id,
					     FMAN_PCD_FE_PROFILE_ROUTED, false, 0);
}
EXPORT_SYMBOL_GPL(ask_hw_offload_engage);

static int ask_hw_resolve_iif_port(u32 ifindex, u8 *port_id);
static int ask_hw_resolve_oif_fqid(u32 ifindex, u32 *fqid, bool allow_alloc);

static int ask_hw_b2_port_of(const char *ifname, u8 *port_id)
{
	struct net_device *dev = dev_get_by_name(&init_net, ifname);
	int rc;

	if (!dev)
		return -ENODEV;
	rc = ask_hw_resolve_iif_port(dev->ifindex, port_id);
	dev_put(dev);
	return rc;
}

/*
 * Bridge plan D1 port role: engage @hw_port_id with @profile, disengaging it
 * first if it is armed with another one; the family mask survives. With
 * @if_engaged, a port that is not engaged is left alone (bridge join/leave).
 * Callers serialize on rtnl. Never call with ask_bridge_lock held: disengage
 * takes it.
 */
int ask_hw_offload_engage_role(u8 hw_port_id, enum fman_pcd_fe_profile profile,
			       bool if_engaged, u8 family_mask)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	u8 fam;
	bool engaged = false, other = false, retained = false, transitioning = false;
	unsigned int i;

	if (!h)
		return -ENODEV;
	mutex_lock(&h->lock);
	fam = family_mask ? family_mask : ask_hw_port_family_get(hw_port_id);
	for (i = 0; i < ASK_HW_MAX_PORTS; i++)
		if (h->port[i].in_use && h->port[i].port_id == hw_port_id) {
			retained = h->port[i].fe_retained;
			transitioning = h->port[i].teardown_in_progress;
			engaged = h->port[i].offload_engaged;
			other = engaged && h->port[i].engaged_profile != profile;
		}
	mutex_unlock(&h->lock);

	if (retained)
		return -ESHUTDOWN;
	if (transitioning)
		return -EAGAIN;
	if (if_engaged && (!engaged || !fam))
		return 0;
	if (other) {
		int rc;

		ask_pr_info("hw: port 0x%02x role change to profile %d\n",
			    hw_port_id, profile);
		rc = ask_hw_offload_disengage(hw_port_id);
		if (rc)
			return rc;	/* F-289: stays disengaged, family cleared */
	}
	return ask_hw_offload_engage_profile(hw_port_id, profile, false, fam);
}

/* T-M6-2 B2 (debugfs only): engage @ifname's port with the L2_DA profile. */
int ask_hw_b2_l2_engage(const char *ifname)
{
	u8 port;
	int rc = ask_hw_b2_port_of(ifname, &port);

	return rc ? rc : ask_hw_offload_engage_profile(port,
						       FMAN_PCD_FE_PROFILE_L2_DA,
						       false, 0);
}

/* Manual records bypass bridge ownership and synchronous withdrawal. */
int ask_hw_b2_l2_record(const char *iifname, const u8 *key,
			const char *oifname, bool add)
{
	pr_warn_ratelimited("ask: bridge: manual FE add/delete disabled; ownership is not tracked\n");
	return -EOPNOTSUPP;
}

struct ask_hw_fe_transition {
	struct ask_hw_pcd *hw;
	struct ask_hw_port *port;
	u8 port_id;
	bool remove_dependents;
	bool prepared;
};

static int ask_hw_fe_prepare_disengage(void *data)
{
	struct ask_hw_fe_transition *transition = data;
	int rc;

	transition->prepared = true;
	ask_hw_set_family_locked(transition->port_id, 0);
	if (transition->remove_dependents) {
		rc = ask_hw_punt_apply(transition->hw, transition->port, 0);
		if (rc)
			return rc;
		rc = ask_vlan_cc_teardown_port(transition->port_id);
		if (rc)
			return rc;
	}
	return 0;
}

int ask_hw_offload_disengage(u8 hw_port_id)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_fe_transition transition;
	struct ask_hw_port *p;
	bool drop_module_ref;
	unsigned int i;
	int rc;

	if (!h)
		return -ENODEV;

	mutex_lock(&h->lock);

	p = NULL;
	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use && h->port[i].port_id == hw_port_id) {
			p = &h->port[i];
			break;
		}
	}
	if (!p) {
		ask_hw_set_family_locked(hw_port_id, 0);
		mutex_unlock(&h->lock);
		return 0;               /* idempotent no-op */
	}
	if (p->fe_retained) {
		ask_hw_set_family_locked(hw_port_id, 0);
		mutex_unlock(&h->lock);
		return -ESHUTDOWN;
	}
	if (p->teardown_in_progress) {
		ask_hw_set_family_locked(hw_port_id, 0);
		mutex_unlock(&h->lock);
		return -EAGAIN;
	}
	if (!p->offload_engaged) {
		ask_hw_set_family_locked(hw_port_id, 0);
		mutex_unlock(&h->lock);
		return 0;
	}

	/*
	 * Hold FE ownership from the busy check through dependent teardown.
	 * The callback closes admission and removes punt/CC state before FE
	 * disarm; a preflight -EBUSY leaves the entire ASK state unchanged.
	 */
	transition = (struct ask_hw_fe_transition) {
		.hw = h, .port = p, .port_id = hw_port_id,
		.remove_dependents = true,
	};
	rc = fman_pcd_fe_disengage_prepared(h->fman, hw_port_id,
					   ask_hw_fe_prepare_disengage, &transition);
	if (rc == -EBUSY && !transition.prepared) {
		mutex_unlock(&h->lock);
		return rc;
	}
	if (rc && !transition.prepared)
		ask_hw_set_family_locked(hw_port_id, 0);
	p->fe_retained = rc != 0;

	/*
	 * Reverse the FMan-global TX-confirm bypass set at engage so the
	 * S1→S0 cycle restores the silicon HIT-release bit to its S0
	 * default.  Mirrors the engage-side call at line ~598; keeps
	 * `pcd-snapshot diff` byte-clean per the DUAL-DATAPLANE.md M1
	 * reversibility contract.
	 * 2026-08-21 LEAK FIX: global TX-confirm bypass removed (see engage);
	 * nothing to clear here.
	 */

	p->offload_engaged = false;
	p->teardown_in_progress = !rc;
	if (!rc)
		clear_bit(hw_port_id, ask_hw_port_l2);
	mutex_unlock(&h->lock);

	if (rc) {
		ask_pr_err("hw: offload disengage port 0x%02x incomplete: %d; FE owners retained\n",
			   hw_port_id, rc);
		return rc;
	}

	ask_bridge_forget_port(hw_port_id);

	/* Also retry withdrawal if this port's family was already cleared. */
	ask_flow_pppoe_flush();

	mutex_lock(&h->lock);
	p->teardown_in_progress = false;
	drop_module_ref = p->module_pinned;
	p->module_pinned = false;
	mutex_unlock(&h->lock);
	if (drop_module_ref)
		module_put(THIS_MODULE);
	ask_pr_info("hw: offload DISENGAGED on port 0x%02x (S1->S0)\n", hw_port_id);
	return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_offload_disengage);

/* ------------------------------------------------------------------------- */
/* Legacy ABI stubs (consumers/tests still name these; Stage C removes them)  */
/* ------------------------------------------------------------------------- */

/*
 * Under the board substrate ask.ko owns no fman_pcd_cc_node objects (the
 * CC tree lives inside fsl_dpa.ko, addressed by (struct fman *, port_id)),
 * so these per-port node accessors always return NULL.  Kept as ABI stubs
 * until the consumers/tests that still name them are cleaned up in Stage C.
 */
struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_tcp_for_port(u8 hwport_id)
{
	(void)hwport_id;
	return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_tcp_for_port);

struct fman_pcd_cc_node *
ask_hw_pcd_cc_v4_udp_for_port(u8 hwport_id)
{
	(void)hwport_id;
	return NULL;
}
EXPORT_SYMBOL_GPL(ask_hw_pcd_cc_v4_udp_for_port);

int ask_hw_port_bind(u8 port_id, enum ask_hw_dir dir,
		     struct net_device *ingress_dev)
{
	int rc;

	/* Bridge plan D1: an L2_DA port carries no routed flows. Quiet -ENODEV
	 * keeps the flow in software without a per-flow log line. */
	if (test_bit(port_id, ask_hw_port_l2))
		return -ENODEV;

	/*
	 * A successful FLOW_CLS_REPLACE must arm the ingress FE pipeline
	 * before it can add an ehash record.  fman_pcd_fe_engage() publishes
	 * the per-port FE workspace and the shared ENQ FE offset consumed by
	 * ask_fe_flow_insert().  The wrapper is idempotent for subsequent
	 * flows on this port.  It refuses (-ENODEV, quiet SW fallback) a port
	 * whose family mask is 0: only the operator's `offload ipv4|ipv6`
	 * (genl ASK_CMD_ENGAGE) may enable a port, never traffic.
	 */
	rc = ask_hw_offload_engage_profile(port_id,
					   FMAN_PCD_FE_PROFILE_ROUTED, true, 0);
	if (rc && rc != -ENODEV)
		ask_pr_warn("hw: port-bind engage port 0x%02x dir=%u dev=%s failed: %d\n",
			    port_id, dir,
			    ingress_dev ? netdev_name(ingress_dev) : "?", rc);
	return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_port_bind);

int ask_hw_port_unbind(u8 port_id)
{
	(void)port_id;
	return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_port_unbind);

/*
 * T-M6-8 R4c-3: re-assert the FE-VM ehash graft on an ASK-engaged port after
 * the VLAN CC tree that was fronting it is torn down.
 *
 * Historically, while VLAN CC flows existed, ask_vlan_cc grafted the port to a
 * CC tree (RCCB -> CC, CC miss -> FE_ENTER) so routed/NAT reached ehash via the
 * miss row. When the LAST VLAN flow was deleted, fman_cc_tree_destroy reverted the
 * port to bare RSS (RCCB=0) — which would silently drop routed/NAT off the
 * ehash HW path onto the software RSS path until the next port re-engage.
 * fman_pcd_fe_engage() alone will NOT fix this: the port's fe_port_armed bit is
 * still set, so it returns early without re-writing the RCCB. Do a full
 * disengage->engage cycle instead: disengage clears the armed bit and the FE
 * chain stays warm (F-136), so the re-engage cheaply re-writes RCCB -> FE_ENTER
 * and re-captures the ENQ FE offset. Only touches ports we actually engaged.
 * Legacy exported helper; no in-tree callers. Process context; takes h->lock.
 * Its retained-owner checks do not prove hardware quiescence.
 */
int ask_hw_fe_reengage(u8 hw_port_id)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_fe_transition transition;
	struct ask_hw_port *p;
	u8 family;
	unsigned int i;
	int rc = 0;

	if (!h)
		return -ENODEV;

	mutex_lock(&h->lock);
	p = NULL;
	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use && h->port[i].port_id == hw_port_id) {
			p = &h->port[i];
			break;
		}
	}
	/* Only re-assert on a port ASK actually has engaged. An L2_DA (bridge)
	 * port has no VLAN CC tree to restore from, and a routed re-engage
	 * would swap its L2 table for a routed one under live bridge records
	 * (B7b run 2026-10-19: every later 16-byte L2 add failed -EINVAL). */
	if (!p || !p->offload_engaged ||
	    p->engaged_profile != FMAN_PCD_FE_PROFILE_ROUTED) {
		mutex_unlock(&h->lock);
		return 0;
	}

	family = ask_hw_port_family_get(hw_port_id);
	if (ask_hw_family_seq_exhausted ||
	    (family && ask_hw_family_seq >= U64_MAX - 1)) {
		mutex_unlock(&h->lock);
		return -EOVERFLOW;
	}
	transition = (struct ask_hw_fe_transition) {
		.hw = h, .port = p, .port_id = hw_port_id,
	};
	rc = fman_pcd_fe_disengage_prepared(h->fman, hw_port_id,
					   ask_hw_fe_prepare_disengage, &transition);
	if (rc == -EBUSY && !transition.prepared) {
		mutex_unlock(&h->lock);
		return rc;
	}
	if (!rc)
		rc = fman_pcd_fe_engage(h->fman, hw_port_id, 0);
	if (rc) {
		p->offload_engaged = false;
		p->fe_retained = true;
		ask_hw_set_family_locked(hw_port_id, 0);
		ask_pr_warn("hw: FE re-engage port 0x%02x after VLAN CC teardown failed: %d\n",
			    hw_port_id, rc);
	} else {
		WRITE_ONCE(ask_hw_enq_fe_off,
			   fman_pcd_fe_enq_get_offset(h->fman));
		ask_hw_set_family_locked(hw_port_id, family);
		ask_pr_info("hw: FE re-engaged port 0x%02x after VLAN CC teardown (RCCB restored)\n",
			    hw_port_id);
	}
	mutex_unlock(&h->lock);
	return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_fe_reengage);

u32 ask_priv_pack_hw_flow_id(u16 node_token, u16 key_idx)
{
	/* Debug helper kept for ABI; xarray cookies are the live form. */
	return ((u32)node_token << 16) | (u32)key_idx;
}
EXPORT_SYMBOL_GPL(ask_priv_pack_hw_flow_id);

void ask_priv_unpack_hw_flow_id(u32 hw_flow_id,
				u16 *node_token, u16 *key_idx)
{
	if (node_token)
		*node_token = (u16)(hw_flow_id >> 16);
	if (key_idx)
		*key_idx    = (u16)(hw_flow_id & 0xffffu);
}
EXPORT_SYMBOL_GPL(ask_priv_unpack_hw_flow_id);

/* ------------------------------------------------------------------------- */
/* Per-flow fast path                                                         */
/* ------------------------------------------------------------------------- */

/*
 * Resolve (or claim) the per-ingress-port shadow record for @port_id.
 * Returns NULL only when all ASK_HW_MAX_PORTS slots are already owned by
 * other ports.  Caller holds h->lock.
 */
static struct ask_hw_port *ask_hw_port_slot_get(struct ask_hw_pcd *h, u8 port_id)
{
	unsigned int i, free_idx = ASK_HW_MAX_PORTS;

	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use) {
			if (h->port[i].port_id == port_id)
				return &h->port[i];
		} else if (free_idx == ASK_HW_MAX_PORTS) {
			free_idx = i;
		}
	}
	if (free_idx == ASK_HW_MAX_PORTS)
		return NULL;

	h->port[free_idx].in_use       = true;
	h->port[free_idx].port_id      = port_id;
	return &h->port[free_idx];
}

/*
 * T-M6-A4: non-allocating capacity probe. Returns true if @port_id already
 * has a slot OR a free slot exists, i.e. ask_hw_port_slot_get() would succeed.
 * Caller holds h->lock. Pure read — no side effects, so a preflight that
 * ultimately fails elsewhere does not consume a port slot.
 */
static bool ask_hw_port_slot_available(struct ask_hw_pcd *h, u8 port_id)
{
	unsigned int i;
	bool have_free = false;

	for (i = 0; i < ASK_HW_MAX_PORTS; i++) {
		if (h->port[i].in_use) {
			if (h->port[i].port_id == port_id)
				return true;
		} else {
			have_free = true;
		}
	}
	return have_free;
}

/*
 * Map an ASK flow netdev ifindex to its ingress BMI hwport id via the board
 * resolver chain.  Returns -ENODEV for a non-DPAA / unknown ifindex (the
 * graceful SW-fallback signal).  ASK offload flows on this single-image board
 * live in the init namespace.
 */
static int ask_hw_resolve_iif_port(u32 ifindex, u8 *port_id)
{
	struct net_device *dev;
	struct net_device *phys;
	struct fman_port *port;

	dev = dev_get_by_index(&init_net, ifindex);
	if (!dev)
		return -ENODEV;
	/*
	 * T-M6-8: if the flow's ingress/egress is an 802.1Q VLAN vif, the FMan
	 * BMI port lives on the physical lower device. Resolve through it. The
	 * VID + pop/push intent ride the flow VLAN match/actions, so this only
	 * corrects the port lookup. (Mirrors ask_dpaa_get_fman_port_id.)
	 */
	phys = is_vlan_dev(dev) ? vlan_dev_real_dev(dev) : dev;
	port = dpaa_get_rx_fman_port(phys);
	if (!port) {
		dev_put(dev);
		return -ENODEV;
	}
	*port_id = fman_port_get_id(port);
	dev_put(dev);
	return 0;
}

/*
 * Map an ASK flow egress netdev ifindex to its TX QMan FQID (queue 0).
 * Returns -ENODEV for a non-DPAA / unknown ifindex.
 *
 * CR-013 (2026-09-15): @allow_alloc gates the COLD-CACHE path
 * (dpaa_alloc_offload_tx_fq(), which takes rtnl_lock()) separately from
 * the cache-hit fast path (no locking beyond what the caller already
 * holds). CR-012 fixed the *prewarm* call site's own rtnl_lock() but left
 * this function's cache-miss fallback still reachable from the flow-insert
 * hot path (ask_hw_flow_preflight()/ask_hw_flow_insert(), both called
 * under &flowtable->flow_block_lock) for any egress interface the prewarm
 * pass could never have warmed -- a non-DPAA netdev (e.g. a veth or
 * tunnel) that dpaa_get_rx_fman_port() can't map to a hw_port_id, or any
 * DPAA port whose own engage-time prewarm hasn't run yet. Found live via
 * PROVE_LOCKING during T-M6-2 B2 testing: a synthetic flow's egress
 * resolved to a veth ifindex, taking the exact same rtnl_lock()-under-
 * flow_block_lock path CR-012 already closed for the prewarm helper.
 *
 * Callers from the hot path MUST pass allow_alloc=false: a cache miss
 * fails closed (-ENODEV, keeping the flow in software) instead of ever
 * calling dpaa_alloc_offload_tx_fq(). This is the same FAIL-CLOSED
 * philosophy already used a few lines below for allocation failure --
 * extended to cover "cannot safely attempt allocation from here" too.
 * Only ask_hw_prewarm_egress_fq() (genuinely safe process context, no
 * flow_block_lock held) passes allow_alloc=true.
 */
static int ask_hw_resolve_oif_fqid(u32 ifindex, u32 *fqid, bool allow_alloc)
{
	struct ask_hw_pcd *h = ask_hw_pcd_inst;
	struct net_device *dev;
	struct net_device *phys;
	u32 phys_ifindex;
	unsigned int slot;
	int rc;

	/* T-M7-2 S4 (2026-08-15): resolve a PER-EGRESS-INTERFACE NO-CONFIRM
	 * TX FQ for the FE hardware terminal. S1 used dpaa_get_tx_fqid(dev,0),
	 * the netdev's shared queue-0 TX FQ whose FQD has B0V=1 -> FMan emits
	 * a TX-confirm FD per frame -> per-CPU NAPI skb-free cost -> the
	 * ~2.2 Gbps TCP ceiling. dpaa_alloc_offload_tx_fq() (F-199) returns a
	 * dedicated FQ with B0V=0 (no confirmation) on the same channel; we
	 * cache it per ifindex so each egress port allocates exactly once.
	 * The FQ is owned by fsl_dpa (netdev dpaa_fq_list) — never destroyed
	 * here. Allocation failure keeps the flow in software. */
	dev = dev_get_by_index(&init_net, ifindex);
	if (!dev)
		return -ENODEV;

	/* T-M6-8: egress may be an 802.1Q VLAN vif; the DPAA TX FQ belongs to
	 * the physical lower device. Resolve + cache by the PHYSICAL ifindex so
	 * a vif and its parent share the one per-port no-confirm FQ. */
	phys = is_vlan_dev(dev) ? vlan_dev_real_dev(dev) : dev;
	phys_ifindex = phys->ifindex;

	slot = phys_ifindex % ASK_HW_NOCONF_SLOTS;
	if (h && h->noconf_tx[slot].fqid &&
	    h->noconf_tx[slot].ifindex == phys_ifindex) {
		*fqid = h->noconf_tx[slot].fqid;
		dev_put(dev);
		return 0;
	}

	/* CR-013: cache miss from an unsafe (hot-path) caller -- do not risk
	 * dpaa_alloc_offload_tx_fq()'s rtnl_lock() here. Fail closed; the flow
	 * stays in software. A later engage's prewarm (or a retry once this
	 * port's own prewarm has run) covers it going forward. */
	if (!allow_alloc) {
		dev_put(dev);
		return -ENODEV;
	}

	rc = dpaa_alloc_offload_tx_fq(phys, fqid);
	if (rc) {
		/*
		 * FAIL CLOSED (2026-08-21 leak fix): if we cannot get a
		 * NO-CONFIRM (B0V=0) TX FQ, we MUST NOT fall back to the
		 * confirmed queue-0 FQ. Since the global TX-confirm bypass
		 * (F-108) is removed, an FMan-HIT frame on a confirmed FQ
		 * would generate a TX-confirm FD that the kernel would
		 * confirm-process as a bogus skb. Returning an error here
		 * makes the caller keep the flow in SOFTWARE forwarding
		 * (whose skbs ARE freed by the normal TX confirm), instead
		 * of hardware-offloading onto a confirmed FQ. Correctness
		 * over speed; on this board (<=5 dpaa netdevs) the per-port
		 * no-confirm FQ alloc does not realistically fail.
		 */
		ask_pr_warn("hw: no-confirm TX FQ alloc failed on ifindex=%u (phys=%u, %d); NOT offloading (SW forward)\n",
			    ifindex, phys_ifindex, rc);
		dev_put(dev);
		return rc ? rc : -ENODEV;
	}

	if (h && h->noconf_tx[slot].fqid == 0) {
		h->noconf_tx[slot].ifindex = phys_ifindex;
		h->noconf_tx[slot].fqid = *fqid;
	} else if (h && h->noconf_tx[slot].ifindex != phys_ifindex) {
		ask_pr_warn("hw: no-confirm TX FQ cache collision slot=%u phys_ifindex=%u (cached %u); not caching\n",
			    slot, phys_ifindex, h->noconf_tx[slot].ifindex);
	}

	ask_pr_info("hw: no-confirm TX FQ 0x%x resolved for ifindex=%u (phys=%u)\n",
		    *fqid, ifindex, phys_ifindex);
	dev_put(dev);
	return 0;
}

/*
 * CR-012 (2026-09-15): dpaa_alloc_offload_tx_fq() takes rtnl_lock(), which
 * is unsafe to call from the flow-insert hot path (ask_hw_resolve_oif_fqid()
 * via ask_hw_flow_preflight()) because that path runs under
 * &flowtable->flow_block_lock (nf_flow_offload_tuple() called from
 * flow_offload_work_handler()), while stock nftables independently takes
 * rtnl_mutex -> commit_mutex -> flow_block_lock at module load (netdev
 * notifier registration under commit_mutex, then flow_block_lock via
 * nf_flow_table_offload_setup()). PROVE_LOCKING correctly flags the
 * resulting circular lock-order chain -- found 2026-09-14 on the very first
 * hardware flow install during normal board setup, not even under
 * deliberate stress (plans/ASK2-MASTER-PLAN.md CR-012).
 *
 * The noconf_tx[] cache already exists to allocate each egress port's FQ
 * only once (T-M7-2 S4); this warms that cache from a genuinely safe
 * calling context instead of leaving the first allocation to whichever
 * flow happens to be first through preflight.
 *
 * NOT safe to call from ask_hw_offload_engage() itself: that function is
 * also reached from ask_hw_port_bind(), called by ask_flow_offload_replace()
 * from INSIDE the flow-install callback while flow_block_lock is held (the
 * exact hazard above) -- confirmed by a 2026-09-15 board PROVE_LOCKING run
 * that fired the identical circular-dependency splat through THIS
 * function's own rtnl_lock() once it was wired in there. Callers MUST be
 * genuinely-safe process context: the genl ASK_CMD_ENGAGE handler and the
 * debugfs engage write, both called directly, never from inside
 * ask_hw_offload_engage()/ask_hw_port_bind(). Best-effort either way: if
 * the port's netdev can't be found or the resolve fails here, the port's
 * cache slot stays cold. Per CR-013, the hot path no longer has a lazy
 * fallback for that case -- ask_hw_flow_preflight()/ask_hw_flow_insert()
 * call ask_hw_resolve_oif_fqid() with allow_alloc=false and fail closed
 * (stay in software) on a cache miss, rather than risking
 * dpaa_alloc_offload_tx_fq()'s rtnl_lock() from under flow_block_lock.
 * A cold slot self-heals on the port's next successful engage/prewarm.
 *
 * Iterates under rtnl_lock() (matching how dpaa_get_rx_fman_port() is
 * called everywhere else in this file -- via dev_get_by_index(), never
 * under RCU) to find hw_port_id's netdev, then drops it before calling
 * ask_hw_resolve_oif_fqid(), which re-takes rtnl_lock() itself on a cache
 * miss -- rtnl_lock() is not recursive, so the two must not overlap.
 */
/* The physical DPAA netdev of @hw_port_id, or NULL. Caller holds rtnl; no
 * reference is taken. */
struct net_device *ask_hw_port_netdev(u8 hw_port_id)
{
	struct net_device *dev;

	ASSERT_RTNL();
	for_each_netdev(&init_net, dev) {
		struct fman_port *port = dpaa_get_rx_fman_port(dev);

		if (port && fman_port_get_id(port) == hw_port_id)
			return dev;
	}
	return NULL;
}

/* @dev's own RX port id; VLAN vifs and non-DPAA netdevs are -ENODEV (a
 * bridge record keyed on the physical port would ignore the VID). */
int ask_hw_dev_port(struct net_device *dev, u8 *hw_port_id)
{
	struct fman_port *port = dpaa_get_rx_fman_port(dev);

	if (!port)
		return -ENODEV;
	*hw_port_id = fman_port_get_id(port);
	return 0;
}

/* The port's `offload ipv4|ipv6` mask (ASK_FAM_V4/V6), 0 when disengaged. */
u8 ask_hw_port_family_mask(u8 hw_port_id)
{
	return ask_hw_port_family_get(hw_port_id);
}

bool ask_hw_port_is_l2(u8 hw_port_id)
{
	return test_bit(hw_port_id, ask_hw_port_l2);
}

/* Cache-only egress FQ lookup: safe under rtnl (never allocates). */
int ask_hw_oif_fqid_cached(u32 ifindex, u32 *fqid)
{
	return ask_hw_resolve_oif_fqid(ifindex, fqid, false);
}

/* @hw_port_id's slot if it is engaged as L2_DA. h->lock held. */
static struct ask_hw_port *ask_hw_l2_port(struct ask_hw_pcd *h, u8 hw_port_id)
{
	unsigned int i;

	for (i = 0; i < ASK_HW_MAX_PORTS; i++)
		if (h->port[i].in_use && h->port[i].port_id == hw_port_id &&
		    h->port[i].offload_engaged &&
		    h->port[i].engaged_profile == FMAN_PCD_FE_PROFILE_L2_DA)
			return &h->port[i];
	return NULL;
}

/*
 * Add or remove one bridge record (@key -> @fqid) in @hw_port_id's L2_DA
 * table. h->lock makes the profile check and the table
 * operation atomic against disengage: with no per-port table,
 * fman_pcd_fe_flow_add() would fall back to the global ROUTED table.
 * A missing context or L2 port does not prove that the old table was
 * safely removed; leave the caller's record retained.
 */
int ask_hw_l2_record(u8 hw_port_id, const u8 *key, u32 fqid, u8 act,
		     u16 vid, u64 fe_token, bool add)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct fman_pcd_fe_flow_action a;
	int rc;

	if (!key || !fe_token)
		return -EINVAL;
	if (!h)
		return -ENODEV;
	if (add) {
		rc = ask_bridge_fe_action(key, fqid, ask_hw_get_enq_fe_off(),
					  act, vid, &a);
		if (rc)
			return rc;
		a.owner_token = fe_token;
	} else {
		memcpy(a.key, key, FMAN_PCD_FE_L2_DA_KEY_SIZE);
	}

	mutex_lock(&h->lock);
	if (!ask_hw_l2_port(h, hw_port_id))
		rc = -ENODEV;
	else if (add)
		rc = fman_pcd_fe_flow_add(h->fman, hw_port_id, &a);
	else
		rc = fman_pcd_fe_flow_del_identified(h->fman, hw_port_id, a.key,
						    FMAN_PCD_FE_L2_DA_KEY_SIZE,
						    FMAN_PCD_FE_OWNER_ASK,
						    fe_token);
	mutex_unlock(&h->lock);
	return rc;
}

/* One record's hardware packet count (the FE writes it on every hit,
 * qdrant 2026-08-22). O(records in the port's table) under fe_lock: for the
 * bridge ageing pass only, never a per-flow stats path (2026-10-10 convoy). */
int ask_hw_l2_record_hits(u8 hw_port_id, const u8 *key, u64 fe_token,
			  u64 *pkts)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	u64 bytes;
	u32 ts;
	int rc;

	if (!key || !fe_token || !pkts)
		return -EINVAL;
	if (!h)
		return -ENODEV;
	mutex_lock(&h->lock);
	rc = ask_hw_l2_port(h, hw_port_id) ?
		fman_pcd_fe_flow_get_stats_identified(h->fman, hw_port_id, key,
						      FMAN_PCD_FE_L2_DA_KEY_SIZE,
						      FMAN_PCD_FE_OWNER_ASK,
						      fe_token, pkts, &bytes,
						      &ts) : -ENODEV;
	mutex_unlock(&h->lock);
	return rc;
}

void ask_hw_prewarm_egress_fq(u8 hw_port_id)
{
	struct net_device *dev;
	int ifindex = 0;
	u32 fqid;

	rtnl_lock();
	dev = ask_hw_port_netdev(hw_port_id);
	if (dev)
		ifindex = dev->ifindex;
	rtnl_unlock();

	if (ifindex)
		ask_hw_resolve_oif_fqid(ifindex, &fqid, true);
}

int ask_hw_flow_preflight(const struct ask_flow_key *key,
			  u32 oif, u32 action_flags,
			  enum ask_hw_dir dir)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	u32 tx_fqid = 0;
	u8  port_id = 0;
	int rc;

	(void)dir;

	if (!key)
		return -EINVAL;

	/* No HW backing -> caller keeps the flow in software. */
	if (!h)
		return -ENODEV;

	/*
	 * T-M6-A4: resource-class gate. The plain IPv4/IPv6 unicast HW path
	 * consumes exactly one DDR ehash record + one cookie + a pre-existing
	 * per-egress TX FQ; it needs NO per-flow MURAM, policer, or CAAM slot.
	 * Any action that WOULD require an unprovisioned resource class must
	 * fail to software here, before publication, rather than partially
	 * program silicon. CAAM/OP are still rejected unconditionally.
	 *
	 * T-M6-7.1: NAT/PAT (ASK_ACT_NAT_SRC/DST/PAT) is admitted to hardware
	 * ONLY when the ask_nat44_offload gate is enabled (default on) AND the flow
	 * is not on the eth0 management port. Disarmed (default) it fails
	 * closed to software exactly as before -- byte-identical shipping
	 * behaviour. The F-230 FE-VM NAT emitter is likewise dormant unless
	 * ask.ko populates action.nat_* (only done when armed).
	 *
	 * Routed VLAN edits use inline FE actions in ehash records. Admit them
	 * only when the VLAN gate is armed on the ingress port.
	 */
	if (action_flags & (ASK_ACT_TO_CAAM | ASK_ACT_TO_OP))
		return -EOPNOTSUPP;
	if (action_flags & (ASK_ACT_VLAN_PUSH | ASK_ACT_VLAN_POP)) {
		/* Per-port gate: VLAN is armed on the flow's INGRESS port
		 * (key->port_id, set by ask_flow_offload_replace before
		 * preflight). Fails closed to software on unarmed ports. */
		if (!ask_hw_vlan_offload_armed_port(key->port_id))
			return -EOPNOTSUPP;
	}
	if (action_flags & (ASK_ACT_NAT_SRC | ASK_ACT_NAT_DST | ASK_ACT_PAT)) {
		/* IPv4 NAT is silicon-validated (S0-S3), shipping default-on.
		 * IPv6 NAT66 uses the separate nat66_offload gate (default on; fused v6 opcode
		 * 0x2f unproven); default off -> software fallback. */
		if (key->l3_proto == ASK_FLOW_L3_IPV4) {
			if (!ask_hw_nat44_offload_armed())
				return -EOPNOTSUPP;
		} else if (key->l3_proto == ASK_FLOW_L3_IPV6) {
			if (!ask_hw_nat66_offload_armed())
				return -EOPNOTSUPP;
		} else {
			return -EOPNOTSUPP;
		}
	}

	/* Ingress hwport must resolve first (it owns the classifier AND its
	 * per-port family mask decides v4/v6 admission). Resolve BEFORE the
	 * family gate so admission is by TRUE INGRESS PORT, address-independent. */
	rc = ask_hw_resolve_iif_port(key->iif, &port_id);
	if (rc)
		return rc;

	/* Never NAT- or VLAN-offload the eth0 management lifeline, even when the
	 * corresponding global gate is enabled. It remains available for SSH/recovery. */
	if ((action_flags & (ASK_ACT_NAT_SRC | ASK_ACT_NAT_DST | ASK_ACT_PAT |
			     ASK_ACT_VLAN_PUSH | ASK_ACT_VLAN_POP)) &&
	    port_id == ASK_HW_PORT_ETH0_MGMT)
		return -EOPNOTSUPP;

	/* Per-port family/proto gate: TCP/UDP, and the ingress port must have
	 * this L3 family selected (CLI offload ipv4 / offload ipv6). Otherwise
	 * fall back to software. */
	rc = ask_hw_flow_family_ok(port_id, key);
	if (rc)
		return rc;

	/* Egress L2 header not yet resolved -> defer (SW carries it). */
	if (is_zero_ether_addr(key->next_hop_mac) ||
	    is_zero_ether_addr(key->egress_mac))
		return -EAGAIN;

	/* Egress forward FQ must exist before we publish. CR-013: this runs
	 * in the flow-insert hot path under flow_block_lock -- never allow
	 * the cold-cache allocation here. */
	rc = ask_hw_resolve_oif_fqid(oif, &tx_fqid, false);
	if (rc)
		return rc;

	/* Port slot must be available (the CC/FE port context). Non-allocating
	 * probe: this is the -ENOSPC condition the real insert would hit, but
	 * without consuming a slot if the flow later fails to publish. */
	mutex_lock(&h->lock);
	rc = ask_hw_port_slot_available(h, port_id) ? 0 : -ENOSPC;
	mutex_unlock(&h->lock);

	return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_preflight);

int ask_hw_flow_insert(const struct ask_flow_key *key,
		       u32 oif, u32 action_flags,
		       enum ask_hw_dir dir,
		       u32 *out_hw_id)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_flow_cookie ck;
	struct ask_hw_port *p;
	u32 tx_fqid = 0;
	u32 cookie;
	u8  port_id = 0;
	int rc;

	(void)action_flags; (void)dir;

	/* NULL-arg contract: fail without touching *out_hw_id. */
	if (!key || !out_hw_id)
		return -EINVAL;

	/* No HW backing -> SW-only fallback. */
	if (!h)
		return -ENODEV;

	/* Ingress hwport owns the CC tree AND its per-port family mask; resolve
	 * first, then apply the same per-port family/proto gate as preflight. */
	rc = ask_hw_resolve_iif_port(key->iif, &port_id);
	if (rc)
		return rc;

	rc = ask_hw_flow_family_ok(port_id, key);
	if (rc)
		return rc;

	/*
	 * Neighbour not yet resolved: keep the flow in SW until the egress L2
	 * header is known, then the upper layer re-inserts.
	 */
	if (is_zero_ether_addr(key->next_hop_mac) ||
	    is_zero_ether_addr(key->egress_mac))
		return -EAGAIN;

	/* CR-013: same hot-path constraint as ask_hw_flow_preflight() above --
	 * this runs under flow_block_lock, never allow the cold-cache alloc. */
	rc = ask_hw_resolve_oif_fqid(oif, &tx_fqid, false);
	if (rc)
		return rc;

	mutex_lock(&h->lock);

	p = ask_hw_port_slot_get(h, port_id);
	if (!p) {
		rc = -ENOSPC;
		goto out_unlock;
	}

	/* CR-007: Fork-A shadow/HM bookkeeping removed. The FE-VM ehash path
	 * (Fork-B) manages its own keys via ask_fe_flow_insert() in
	 * ask_flow_offload.c. This function now only snapshots the cookie
	 * metadata for teardown (port_id, sink_ifindex, sink_fqid). */

	ck.fm           = h->fman;
	ck.port_id      = port_id;
	ck.sink_ifindex = (int)oif;
	ck.sink_fqid    = tx_fqid;

	cookie = ask_hw_cookie_alloc(h, &ck);
	if (!cookie) {
		rc = -ENOMEM;
		goto out_unlock;
	}

	mutex_unlock(&h->lock);
	*out_hw_id = cookie;
	ask_pr_dbg("hw: flow_insert: port=0x%02x fqid=%u cookie=0x%x\n",
		   port_id, tx_fqid, cookie);
	return 0;

out_unlock:
	mutex_unlock(&h->lock);
	return rc;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_insert);

int ask_hw_flow_remove(u32 hw_flow_id)
{
	struct ask_hw_pcd *h = ask_hw_pcd_get();
	struct ask_hw_flow_cookie *ck;

	if (!h || hw_flow_id == 0)
		return 0;

	mutex_lock(&h->lock);

	ck = ask_hw_cookie_lookup(h, hw_flow_id);
	if (!ck) {
		/* Already torn down; treat as success (idempotent). */
		mutex_unlock(&h->lock);
		return 0;
	}

	/* CR-007: Fork-A shadow/HM teardown removed. The FE-VM ehash path
	 * (Fork-B) manages its own flow deletion via ask_fe_flow_remove()
	 * in ask_flow_offload.c. */

	ask_hw_cookie_free(h, hw_flow_id);

	mutex_unlock(&h->lock);
	ask_pr_dbg("hw: flow_remove: cookie=0x%x released\n", hw_flow_id);
	return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_remove);

int ask_hw_flow_query_stats(u32 hw_flow_id, u64 *packets, u64 *bytes)
{
	(void)hw_flow_id;
	(void)packets;
	(void)bytes;
	return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(ask_hw_flow_query_stats);

/* ------------------------------------------------------------------------- */
/* Module hooks                                                               */
/* ------------------------------------------------------------------------- */

int ask_hw_init(void)
{
	struct ask_hw_ucode_version v;
	int rc;

	rc = ask_hw_ucode_get_version(&v);
	if (rc)
		ask_pr_warn("hw: ucode version probe failed (%d); ASK_CMD_GET_INFO will report zeros\n",
			    rc);

	(void)ask_hw_pcd_bringup();
	return 0;
}

void ask_hw_set_ready(bool ready)
{
	if (!ready)	/* waits for running WGFP callers before teardown */
		dpaa_wgfp_set_family_policy(NULL);
	kernel_param_lock(THIS_MODULE);
	WRITE_ONCE(ask_hw_ready, ready);
	kernel_param_unlock(THIS_MODULE);
	if (ready)
		dpaa_wgfp_set_family_policy(ask_hw_wgfp_family);
}

void ask_hw_exit(void)
{
	ask_hw_pcd_teardown();
	WRITE_ONCE(ask_hw_cached_valid, false);
}
