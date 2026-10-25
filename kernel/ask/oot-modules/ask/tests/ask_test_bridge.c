// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge FE record action (T-M6-2 B1)
 *
 * Pins what ask_bridge_fe_action() hands fman_pcd_fe_flow_add() for one
 * bridge flow on an L2_DA-profile port (F-255): key = 6-byte DA + 6-byte SA
 * + 2-byte TCI + 2-byte EtherType (plan §13.32), table 0 (the port's own
 * table), HIT = plain
 * ENQUEUE_PKT to the egress TX FQ via rx_fqid with tx_fqid 0 (no L2
 * rewrite), and nothing else armed (no NAT, VLAN, or drop). Non-unicast
 * DAs and SAs must be refused so BUM keeps missing to the kernel bridge. Folding
 * EtherType into the key means a record installed for one EtherType cannot
 * be hit by a frame of a different EtherType sharing the same DA: non-IP
 * frames (ARP, 0x88b5, ...) miss the table and fall to the kernel FQ by
 * construction, instead of wedging the FE-VM hit path (plan §13.16).
 *
 * B7b (F-268): an access <-> trunk pair pops or pushes the tag. The record
 * then takes the L2/TX terminal (tx_fqid, rx_fqid 0) with the frame's own
 * DA/SA and FMAN_PCD_VLANF_BRIDGE (no TTL change), so the key must match
 * the action: a pop needs a key tagged with the bridge VID, a push an
 * untagged key; IPv4/IPv6 only.
 */

#include <kunit/test.h>
#include <linux/etherdevice.h>
#include <linux/fsl/fman_pcd.h>

#include "../include/ask_internal.h"

#define ENQ_OFF		0x5a000UL
#define TX_FQID		0x2bb		/* eth3 no-confirm TX FQ (F-199) */
#define ETH_P_IPV4_BE	cpu_to_be16(0x0800)
#define TCI		0xa00a		/* PCP 5, VID 10 */

static const u8 da_uc[ETH_ALEN] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };
static const u8 sa_uc[ETH_ALEN] = { 0x02, 0x66, 0x77, 0x88, 0x99, 0xaa };

static void ask_bridge_test_action_unicast(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;
	static const u8 tci_be[2] = { 0xa0, 0x0a };
	u8 zero[FMAN_FE_FLOW_KEY_MAX - FMAN_PCD_FE_L2_DA_KEY_SIZE] = {};
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];
	__be16 et = ETH_P_IPV4_BE;

	memset(&a, 0xa5, sizeof(a));
	ask_bridge_key(key, da_uc, sa_uc, TCI, et);
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(key, TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), 0);

	KUNIT_EXPECT_EQ(test, a.key_size, (u8)16);
	KUNIT_EXPECT_MEMEQ(test, a.key, da_uc, ETH_ALEN);
	KUNIT_EXPECT_MEMEQ(test, a.key + ETH_ALEN, sa_uc, ETH_ALEN);
	KUNIT_EXPECT_MEMEQ(test, a.key + 2 * ETH_ALEN, tci_be, 2);
	KUNIT_EXPECT_MEMEQ(test, a.key + 2 * ETH_ALEN + 2, &et, sizeof(et));
	KUNIT_EXPECT_MEMEQ(test, a.key + FMAN_PCD_FE_L2_DA_KEY_SIZE, zero, sizeof(zero));
	KUNIT_EXPECT_EQ(test, a.table_idx, (u8)0);
	KUNIT_EXPECT_EQ(test, a.enq_off, ENQ_OFF);
	KUNIT_EXPECT_EQ(test, a.rx_fqid, (u32)TX_FQID);
	/* tx_fqid != 0 would make the record emit INSERT_L2_HDR (F-198). */
	KUNIT_EXPECT_EQ(test, a.tx_fqid, (u32)0);
	KUNIT_EXPECT_EQ(test, a.drop_action, (u8)0);
	KUNIT_EXPECT_EQ(test, a.nat_flags, (u8)0);
	KUNIT_EXPECT_EQ(test, a.vlan_flags, (u8)0);
	KUNIT_EXPECT_EQ(test, a.flags, (u32)0);
}

/* A key for (@da, @sa), untagged IPv4; NULL @da gives a NULL key. */
static const u8 *k(const u8 *da, const u8 *sa)
{
	static u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	if (!da)
		return NULL;
	ask_bridge_key(key, da, sa, 0, ETH_P_IPV4_BE);
	return key;
}

static void ask_bridge_test_action_rejects_bum(struct kunit *test)
{
	static const u8 bcast[ETH_ALEN] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	static const u8 mcast[ETH_ALEN] = { 0x01, 0x80, 0xc2, 0x00, 0x00, 0x00 };
	static const u8 zero[ETH_ALEN] = {};
	struct fman_pcd_fe_flow_action a;

	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(bcast, sa_uc), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(mcast, sa_uc), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(zero, sa_uc), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, mcast), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, zero), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
}

static void ask_bridge_test_action_rejects_bad_target(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;

	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, sa_uc), 0, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, sa_uc), 0x1000000, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, sa_uc), TX_FQID, 0, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(NULL, sa_uc), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(k(da_uc, sa_uc), TX_FQID, ENQ_OFF, ASK_BRIDGE_ACT_SAME, 0, NULL), -EINVAL);
}

#define VID		10

static void ask_bridge_test_action_pop(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	memset(&a, 0xa5, sizeof(a));
	ask_bridge_key(key, da_uc, sa_uc, VID, ETH_P_IPV4_BE);
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(key, TX_FQID, ENQ_OFF,
						   ASK_BRIDGE_ACT_POP, VID, &a), 0);
	KUNIT_EXPECT_MEMEQ(test, a.key, key, sizeof(key));
	KUNIT_EXPECT_EQ(test, a.key_size, (u8)16);
	KUNIT_EXPECT_EQ(test, a.table_idx, (u8)0);
	KUNIT_EXPECT_EQ(test, a.enq_off, ENQ_OFF);
	KUNIT_EXPECT_EQ(test, a.tx_fqid, (u32)TX_FQID);
	KUNIT_EXPECT_EQ(test, a.rx_fqid, (u32)0);
	KUNIT_EXPECT_MEMEQ(test, a.next_hop_mac, da_uc, ETH_ALEN);
	KUNIT_EXPECT_MEMEQ(test, a.egress_mac, sa_uc, ETH_ALEN);
	KUNIT_EXPECT_EQ(test, a.eth_type, (u16)ETH_P_IP);
	KUNIT_EXPECT_EQ(test, a.vlan_flags,
			(u8)(FMAN_PCD_VLANF_BRIDGE | FMAN_PCD_VLANF_POP));
	KUNIT_EXPECT_EQ(test, a.vlan_ingress_vid, (u16)VID);
	KUNIT_EXPECT_EQ(test, a.vlan_push_tci, (u16)0);
	KUNIT_EXPECT_EQ(test, a.egress_mtu, (u16)0);
	KUNIT_EXPECT_EQ(test, a.nat_flags, (u8)0);
	KUNIT_EXPECT_EQ(test, a.drop_action, (u8)0);
	KUNIT_EXPECT_EQ(test, a.flags, (u32)0);
}

static void ask_bridge_test_action_push(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	memset(&a, 0xa5, sizeof(a));
	ask_bridge_key(key, da_uc, sa_uc, 0, cpu_to_be16(ETH_P_IPV6));
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(key, TX_FQID, ENQ_OFF,
						   ASK_BRIDGE_ACT_PUSH, VID, &a), 0);
	KUNIT_EXPECT_EQ(test, a.tx_fqid, (u32)TX_FQID);
	KUNIT_EXPECT_EQ(test, a.rx_fqid, (u32)0);
	KUNIT_EXPECT_MEMEQ(test, a.next_hop_mac, da_uc, ETH_ALEN);
	KUNIT_EXPECT_MEMEQ(test, a.egress_mac, sa_uc, ETH_ALEN);
	KUNIT_EXPECT_EQ(test, a.eth_type, (u16)ETH_P_IPV6);
	KUNIT_EXPECT_EQ(test, a.vlan_flags,
			(u8)(FMAN_PCD_VLANF_BRIDGE | FMAN_PCD_VLANF_PUSH));
	/* PCP 0, as br_allowed_ingress() tags an untagged frame */
	KUNIT_EXPECT_EQ(test, a.vlan_push_tci, (u16)VID);
	KUNIT_EXPECT_EQ(test, a.vlan_push_tpid, (u16)ETH_P_8021Q);
	KUNIT_EXPECT_EQ(test, a.vlan_ingress_vid, (u16)0);
	KUNIT_EXPECT_EQ(test, a.egress_mtu, (u16)0);
}

static int act(u16 tci, __be16 et, u8 action, u16 vid)
{
	struct fman_pcd_fe_flow_action a;
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	ask_bridge_key(key, da_uc, sa_uc, tci, et);
	return ask_bridge_fe_action(key, TX_FQID, ENQ_OFF, action, vid, &a);
}

static void ask_bridge_test_action_rejects_bad_edit(struct kunit *test)
{
	/* pop: untagged key, other VID */
	KUNIT_EXPECT_EQ(test, act(0, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_POP, VID), -EINVAL);
	KUNIT_EXPECT_EQ(test, act(VID + 1, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_POP, VID), -EINVAL);
	/* pop matches the VID only: PCP may be set */
	KUNIT_EXPECT_EQ(test, act(0xa000 | VID, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_POP, VID), 0);
	/* push: tagged key */
	KUNIT_EXPECT_EQ(test, act(VID, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_PUSH, VID), -EINVAL);
	/* VID 0 / 4095 */
	KUNIT_EXPECT_EQ(test, act(0, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_PUSH, 0), -EINVAL);
	KUNIT_EXPECT_EQ(test, act(0, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_PUSH, 4095), -EINVAL);
	KUNIT_EXPECT_EQ(test, act(0xfff, ETH_P_IPV4_BE, ASK_BRIDGE_ACT_POP, 4095), -EINVAL);
	/* non-IP EtherType */
	KUNIT_EXPECT_EQ(test, act(0, cpu_to_be16(ETH_P_ARP), ASK_BRIDGE_ACT_PUSH, VID), -EINVAL);
	KUNIT_EXPECT_EQ(test, act(VID, cpu_to_be16(ETH_P_ARP), ASK_BRIDGE_ACT_POP, VID), -EINVAL);
	/* unknown action */
	KUNIT_EXPECT_EQ(test, act(0, ETH_P_IPV4_BE, 3, VID), -EINVAL);
}

/* B7a records stay byte-identical whatever @vid the caller passes. */
static void ask_bridge_test_action_same_ignores_vid(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a, b;
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	ask_bridge_key(key, da_uc, sa_uc, TCI, ETH_P_IPV4_BE);
	memset(&a, 0x5a, sizeof(a));
	memset(&b, 0xa5, sizeof(b));
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(key, TX_FQID, ENQ_OFF,
						   ASK_BRIDGE_ACT_SAME, 0, &a), 0);
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(key, TX_FQID, ENQ_OFF,
						   ASK_BRIDGE_ACT_SAME, VID, &b), 0);
	KUNIT_EXPECT_MEMEQ(test, &a, &b, sizeof(a));
}

static struct kunit_case ask_bridge_cases[] = {
	KUNIT_CASE(ask_bridge_test_action_unicast),
	KUNIT_CASE(ask_bridge_test_action_rejects_bum),
	KUNIT_CASE(ask_bridge_test_action_rejects_bad_target),
	KUNIT_CASE(ask_bridge_test_action_pop),
	KUNIT_CASE(ask_bridge_test_action_push),
	KUNIT_CASE(ask_bridge_test_action_rejects_bad_edit),
	KUNIT_CASE(ask_bridge_test_action_same_ignores_vid),
	{}
};

struct kunit_suite ask_bridge_suite = {
	.name = "ask_bridge",
	.test_cases = ask_bridge_cases,
};
