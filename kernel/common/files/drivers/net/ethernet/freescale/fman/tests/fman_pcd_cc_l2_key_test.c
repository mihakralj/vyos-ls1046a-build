// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_l2_key_test.c — T-M6-2 B1 bridge FDB CC key KUnit tests.
 *
 * Pins cc_pack_key_l2()'s exact byte layout: PORT_ID(1, always 0x00 on
 * this ucode) + DST_MAC(6) + SRC_MAC(6) + ETHERTYPE(2) = 15 bytes,
 * board-confirmed against the vendor's own L2/bridge KeyGen scheme
 * (scheme 11 on `.106`, EKFC 0xe4000000 = PORT_ID|MACDST|MACSRC|ETYPE,
 * by far the highest live-traffic scheme observed -- 1,225,734 packets):
 * live scheme 11 group-table row (CCOBASE=0) keysize field = 15,
 * matching this layout exactly. Field order (DA before SA before
 * ETYPE) follows this project's consistent "PORT_ID|DA|SA|ETYPE"
 * documentation convention; per B1's own scope this is dormant
 * (readback-only, no live install), so a wrong intra-field order here
 * costs nothing beyond re-deriving before B2's silicon arm.
 *
 * Per plans/ASK2-BRIDGE-OFFLOAD-PLAN.md B1's gate: "KUnit vectors for
 * DA-only and PORT_ID|DA|SA|ETYPE keys; spec byte-exact vs a golden
 * vector."
 *
 * Included as a trailer at the end of fman_pcd_cc.c via
 *   #if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)
 *   #include "tests/fman_pcd_cc_l2_key_test.c"
 *   #endif
 */
#include <kunit/test.h>

static void cc_l2_key_size(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_SIZE_L2, 15U);
}

static void cc_l2_key_da_only(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 dst_mac[6] = { 0x00, 0x11, 0x22, 0x33, 0x44, 0x55 };
	static const u8 expect_key[CC_KEY_SIZE_L2] = {
		0x00,				  /* [0]     PORT_ID */
		0x00, 0x11, 0x22, 0x33, 0x44, 0x55, /* [1..6]  DA */
		/* [7..12] SA: absent, zero */
		/* [13..14] ETYPE: absent, zero */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST;
	memcpy(k.dst_mac, dst_mac, 6);

	cc_pack_key_l2((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_L2;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_L2);
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);	/* PORT_ID always compared */
	KUNIT_EXPECT_EQ(test, msk[1], 0xff);
	KUNIT_EXPECT_EQ(test, msk[6], 0xff);
	KUNIT_EXPECT_EQ(test, msk[7], 0x00);	/* SA absent: wildcard */
	KUNIT_EXPECT_EQ(test, msk[13], 0x00);	/* ETYPE absent: wildcard */
}

static void cc_l2_key_full(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 dst_mac[6] = { 0x00, 0x11, 0x22, 0x33, 0x44, 0x55 };
	static const u8 src_mac[6] = { 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff };
	static const u8 expect_key[CC_KEY_SIZE_L2] = {
		0x00,				  /* [0]      PORT_ID */
		0x00, 0x11, 0x22, 0x33, 0x44, 0x55, /* [1..6]   DA */
		0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, /* [7..12]  SA */
		0x08, 0x00,			  /* [13..14] ETYPE 0x0800 (IPv4) */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST | FMAN_PCD_CC_HW_F_MAC_SRC |
		    FMAN_PCD_CC_HW_F_ETHERTYPE;
	memcpy(k.dst_mac, dst_mac, 6);
	memcpy(k.src_mac, src_mac, 6);
	k.ethertype_be = cpu_to_be16(0x0800);

	cc_pack_key_l2((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_L2;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_L2);
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	KUNIT_EXPECT_EQ(test, msk[6], 0xff);
	KUNIT_EXPECT_EQ(test, msk[12], 0xff);
	KUNIT_EXPECT_EQ(test, msk[14], 0xff);
}

static void cc_l2_key_row_stride(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;

	buf = kunit_kzalloc(test, 3 * 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST;
	k.dst_mac[5] = 0xab;

	cc_pack_key_l2((void __iomem *)buf, 1, &k);

	KUNIT_EXPECT_EQ(test, buf[0], 0);			/* row 0 untouched */
	KUNIT_EXPECT_EQ(test, buf[2 * CC_KEY_SIZE_L2 + 6], 0xab);
}

static struct kunit_case fman_pcd_cc_l2_key_cases[] = {
	KUNIT_CASE(cc_l2_key_size),
	KUNIT_CASE(cc_l2_key_da_only),
	KUNIT_CASE(cc_l2_key_full),
	KUNIT_CASE(cc_l2_key_row_stride),
	{}
};

static struct kunit_suite fman_pcd_cc_l2_key_suite = {
	.name = "fman_pcd_cc_l2_key",
	.test_cases = fman_pcd_cc_l2_key_cases,
};
kunit_test_suite(fman_pcd_cc_l2_key_suite);
