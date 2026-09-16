// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_3tuple_key_test.c — T-M6-T3 3-tuple coarse-flow CC key
 * KUnit tests.
 *
 * Pins cc_pack_key_3tuple()'s exact byte layout: PORT_ID(1, always 0x00
 * on this ucode) + SIP(4) + DIP(4) + PROTO(1) = 10 bytes, board-confirmed
 * against the vendor's own tuple3 KeyGen scheme on `.106` (see the patch
 * changelog for the register readback). Also pins that a field left
 * absent (not in @present) leaves its key/mask bytes zero, so a caller
 * that only wants SIP+DIP (no protocol) gets a real wildcard on that
 * byte, not a garbage compare.
 *
 * Included as a trailer at the end of fman_pcd_cc.c via
 *   #if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)
 *   #include "tests/fman_pcd_cc_3tuple_key_test.c"
 *   #endif
 */
#include <kunit/test.h>

static void cc_3tuple_key_size(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_SIZE_3TUPLE, 10U);
}

static void cc_3tuple_key_full(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 expect_key[CC_KEY_SIZE_3TUPLE] = {
		0x00,			  /* [0] PORT_ID, always 0x00 */
		0x0a, 0x00, 0x00, 0x01,	  /* [1..4] src 10.0.0.1 */
		0x0a, 0x00, 0x00, 0x02,	  /* [5..8] dst 10.0.0.2 */
		6,			  /* [9]    proto TCP */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_3TUPLE, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_SRC_IP | FMAN_PCD_CC_HW_F_DST_IP |
		    FMAN_PCD_CC_HW_F_PROTO;
	k.src_ip_be = cpu_to_be32(0x0a000001);
	k.dst_ip_be = cpu_to_be32(0x0a000002);
	k.proto = 6;

	cc_pack_key_3tuple((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_3TUPLE;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_3TUPLE);
	/* Mask: 0xff at PORT_ID + every present field, 0x00 elsewhere
	 * (there is no "elsewhere" once SRC_IP/DST_IP/PROTO are all
	 * present -- every byte of a full 3-tuple key is compared).
	 */
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	KUNIT_EXPECT_EQ(test, msk[4], 0xff);
	KUNIT_EXPECT_EQ(test, msk[8], 0xff);
	KUNIT_EXPECT_EQ(test, msk[9], 0xff);
}

static void cc_3tuple_key_no_proto(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 expect_key[CC_KEY_SIZE_3TUPLE] = {
		0x00,
		0x0a, 0x00, 0x00, 0x01,
		0x0a, 0x00, 0x00, 0x02,
		0x00,			  /* [9] absent: zero, not compared */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_3TUPLE, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_SRC_IP | FMAN_PCD_CC_HW_F_DST_IP;
	k.src_ip_be = cpu_to_be32(0x0a000001);
	k.dst_ip_be = cpu_to_be32(0x0a000002);

	cc_pack_key_3tuple((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_3TUPLE;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_3TUPLE);
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	KUNIT_EXPECT_EQ(test, msk[8], 0xff);
	KUNIT_EXPECT_EQ(test, msk[9], 0x00);	/* proto absent: wildcard */
}

static void cc_3tuple_key_row_stride(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;

	buf = kunit_kzalloc(test, 3 * 2 * CC_KEY_SIZE_3TUPLE, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_PROTO;
	k.proto = 17;

	cc_pack_key_3tuple((void __iomem *)buf, 2, &k);

	/* Row 2 starts at idx * 2 * CC_KEY_SIZE_3TUPLE; rows 0/1 untouched. */
	KUNIT_EXPECT_EQ(test, buf[2 * 2 * CC_KEY_SIZE_3TUPLE + 9], 17);
	KUNIT_EXPECT_EQ(test, buf[0], 0);
	KUNIT_EXPECT_EQ(test, buf[2 * CC_KEY_SIZE_3TUPLE], 0);
}

static struct kunit_case fman_pcd_cc_3tuple_key_cases[] = {
	KUNIT_CASE(cc_3tuple_key_size),
	KUNIT_CASE(cc_3tuple_key_full),
	KUNIT_CASE(cc_3tuple_key_no_proto),
	KUNIT_CASE(cc_3tuple_key_row_stride),
	{}
};

static struct kunit_suite fman_pcd_cc_3tuple_key_suite = {
	.name = "fman_pcd_cc_3tuple_key",
	.test_cases = fman_pcd_cc_3tuple_key_cases,
};
kunit_test_suite(fman_pcd_cc_3tuple_key_suite);
