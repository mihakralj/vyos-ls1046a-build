// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_l2_key_test.c — T-M6-2 B1 bridge FDB L2 CC key KUnit tests.
 *
 * Pins cc_pack_key_l2()'s exact byte layout: PORT_ID(1, always 0x00 on
 * this ucode) + DST_MAC(6) + SRC_MAC(6) + ETHERTYPE(2) = 15 bytes,
 * board-confirmed against the vendor's own L2/bridge KeyGen scheme on
 * `.106` (see the patch changelog for the register readback). Also pins
 * that a field left absent (not in @present) leaves its key/mask bytes
 * zero — a DA-only key (this project's expected common case for bridge
 * FDB matching) gets a real wildcard on SA/ETYPE, not a garbage compare.
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
	static const u8 expect_key[CC_KEY_SIZE_L2] = {
		0x00,				  /* [0]     PORT_ID */
		0x02, 0x00, 0x00, 0x00, 0x00, 0x01, /* [1..6]  DST_MAC */
		0x00, 0x00, 0x00, 0x00, 0x00, 0x00, /* [7..12] SRC_MAC absent */
		0x00, 0x00,			  /* [13..14] ETYPE absent */
	};
	int i;

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST;
	k.dst_mac[0] = 0x02; k.dst_mac[1] = 0x00; k.dst_mac[2] = 0x00;
	k.dst_mac[3] = 0x00; k.dst_mac[4] = 0x00; k.dst_mac[5] = 0x01;

	cc_pack_key_l2((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_L2;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_L2);
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	for (i = 1; i <= 6; i++)
		KUNIT_EXPECT_EQ(test, msk[i], 0xff);
	for (i = 7; i <= 14; i++)
		KUNIT_EXPECT_EQ(test, msk[i], 0x00);
}

static void cc_l2_key_full(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 expect_key[CC_KEY_SIZE_L2] = {
		0x00,
		0x02, 0x00, 0x00, 0x00, 0x00, 0x01, /* DST_MAC */
		0x02, 0x00, 0x00, 0x00, 0x00, 0x02, /* SRC_MAC */
		0x08, 0x00,			  /* ETYPE 0x0800 (IPv4), wire order */
	};
	int i;

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST | FMAN_PCD_CC_HW_F_MAC_SRC |
		    FMAN_PCD_CC_HW_F_ETHERTYPE;
	k.dst_mac[0] = 0x02; k.dst_mac[1] = 0x00; k.dst_mac[2] = 0x00;
	k.dst_mac[3] = 0x00; k.dst_mac[4] = 0x00; k.dst_mac[5] = 0x01;
	k.src_mac[0] = 0x02; k.src_mac[1] = 0x00; k.src_mac[2] = 0x00;
	k.src_mac[3] = 0x00; k.src_mac[4] = 0x00; k.src_mac[5] = 0x02;
	k.ethertype_be = cpu_to_be16(0x0800);

	cc_pack_key_l2((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_L2;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_L2);
	for (i = 0; i < CC_KEY_SIZE_L2; i++)
		KUNIT_EXPECT_EQ(test, msk[i], 0xff);
}

static void cc_l2_key_row_stride(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;

	buf = kunit_kzalloc(test, 3 * 2 * CC_KEY_SIZE_L2, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_MAC_DST;
	k.dst_mac[5] = 0x2a;

	cc_pack_key_l2((void __iomem *)buf, 2, &k);

	/* Row 2 starts at idx * 2 * CC_KEY_SIZE_L2; rows 0/1 untouched. */
	KUNIT_EXPECT_EQ(test, buf[2 * 2 * CC_KEY_SIZE_L2 + 6], 0x2a);
	KUNIT_EXPECT_EQ(test, buf[0], 0);
	KUNIT_EXPECT_EQ(test, buf[2 * CC_KEY_SIZE_L2], 0);
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
