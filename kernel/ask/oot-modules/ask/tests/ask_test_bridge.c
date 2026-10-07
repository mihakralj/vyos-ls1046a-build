// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge FE record action (T-M6-2 B1)
 *
 * Pins what ask_bridge_fe_action() hands fman_pcd_fe_flow_add() for one FDB
 * entry on an L2_DA-profile port (F-255): key = 6-byte DA, table 0 (the
 * port's own table), HIT = plain ENQUEUE_PKT to the egress TX FQ via
 * rx_fqid with tx_fqid 0 (no L2 rewrite), and nothing else armed (no NAT,
 * VLAN, or drop). Non-unicast DAs must be refused so BUM keeps missing to
 * the kernel bridge.
 */

#include <kunit/test.h>
#include <linux/etherdevice.h>
#include <linux/fsl/fman_pcd.h>

#include "../include/ask_internal.h"

#define ENQ_OFF		0x5a000UL
#define TX_FQID		0x2bb		/* eth3 no-confirm TX FQ (F-199) */

static const u8 da_uc[ETH_ALEN] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };

static void ask_bridge_test_action_unicast(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;
	u8 zero[FMAN_FE_FLOW_KEY_MAX - ETH_ALEN] = {};

	memset(&a, 0xa5, sizeof(a));
	KUNIT_ASSERT_EQ(test, ask_bridge_fe_action(da_uc, TX_FQID, ENQ_OFF, &a), 0);

	KUNIT_EXPECT_EQ(test, a.key_size, (u8)6);
	KUNIT_EXPECT_MEMEQ(test, a.key, da_uc, ETH_ALEN);
	KUNIT_EXPECT_MEMEQ(test, a.key + ETH_ALEN, zero, sizeof(zero));
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

static void ask_bridge_test_action_rejects_bum(struct kunit *test)
{
	static const u8 bcast[ETH_ALEN] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	static const u8 mcast[ETH_ALEN] = { 0x01, 0x80, 0xc2, 0x00, 0x00, 0x00 };
	static const u8 zero[ETH_ALEN] = {};
	struct fman_pcd_fe_flow_action a;

	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(bcast, TX_FQID, ENQ_OFF, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(mcast, TX_FQID, ENQ_OFF, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(zero, TX_FQID, ENQ_OFF, &a), -EINVAL);
}

static void ask_bridge_test_action_rejects_bad_target(struct kunit *test)
{
	struct fman_pcd_fe_flow_action a;

	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(da_uc, 0, ENQ_OFF, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(da_uc, 0x1000000, ENQ_OFF, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(da_uc, TX_FQID, 0, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(NULL, TX_FQID, ENQ_OFF, &a), -EINVAL);
	KUNIT_EXPECT_EQ(test, ask_bridge_fe_action(da_uc, TX_FQID, ENQ_OFF, NULL), -EINVAL);
}

static struct kunit_case ask_bridge_cases[] = {
	KUNIT_CASE(ask_bridge_test_action_unicast),
	KUNIT_CASE(ask_bridge_test_action_rejects_bum),
	KUNIT_CASE(ask_bridge_test_action_rejects_bad_target),
	{}
};

struct kunit_suite ask_bridge_suite = {
	.name = "ask_bridge",
	.test_cases = ask_bridge_cases,
};
