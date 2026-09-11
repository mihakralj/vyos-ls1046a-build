// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_dual_key_test.c — T-M6-8 VLAN-v6 dual-lane CC key KUnit tests.
 *
 * Pins cc_pack_key_dual()'s exact byte layout against the same values
 * ask_fe_build_key_dual() (ask.ko, ask_flow_offload.c) produces for
 * equivalent input, so the two independently-compiled layers cannot
 * silently drift apart. Also pins that the existing v4-only cc_pack_key()/
 * CC_KEY_SIZE=16 path is untouched (V6-1 is purely additive).
 *
 * Included as a trailer at the end of fman_pcd_cc.c via
 *   #if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)
 *   #include "tests/fman_pcd_cc_dual_key_test.c"
 *   #endif
 */
#include <kunit/test.h>

static void cc_dual_key_size_unchanged(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_SIZE, 16U);
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_SIZE_DUAL, 48U);
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_DUAL_FAMILY_V4, 0x40U);
	KUNIT_EXPECT_EQ(test, (u32)CC_KEY_DUAL_FAMILY_V6, 0x60U);
}

static void cc_dual_key_v4(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 expect_key[CC_KEY_SIZE_DUAL] = {
		0x00,				  /* [0]  family V4 */
		0x0a, 0x00, 0x00, 0x01,		  /* [1..4]   src 10.0.0.1 */
		0x0a, 0x00, 0x00, 0x02,		  /* [5..8]   dst 10.0.0.2 */
		/* [9..40] v6 lanes: zero */
		[41] = 6,			  /* proto TCP */
		[42] = 0x04, [43] = 0xd2,	  /* sport 1234 */
		[44] = 0x00, [45] = 0x50,	  /* dport 80 */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_DUAL, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_SRC_IP | FMAN_PCD_CC_HW_F_DST_IP |
		    FMAN_PCD_CC_HW_F_PROTO | FMAN_PCD_CC_HW_F_SRC_PORT |
		    FMAN_PCD_CC_HW_F_DST_PORT;
	k.src_ip_be = cpu_to_be32(0x0a000001);
	k.dst_ip_be = cpu_to_be32(0x0a000002);
	k.proto = 6;
	k.src_port_be = cpu_to_be16(1234);
	k.dst_port_be = cpu_to_be16(80);

	cc_pack_key_dual((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_DUAL;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_DUAL);
	/* Mask: 0xff at family + every present field + absent lane zero-assert. */
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	KUNIT_EXPECT_EQ(test, msk[4], 0xff);
	KUNIT_EXPECT_EQ(test, msk[8], 0xff);
	KUNIT_EXPECT_EQ(test, msk[9], 0xff);   /* v6 lane zero-assert */
	KUNIT_EXPECT_EQ(test, msk[40], 0xff);
	KUNIT_EXPECT_EQ(test, msk[41], 0xff);
	KUNIT_EXPECT_EQ(test, msk[45], 0xff);
}

static void cc_dual_key_v6(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;
	u8 *key, *msk;
	static const u8 src6[16] = {
		0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x01,
	};
	static const u8 dst6[16] = {
		0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x02,
	};
	static const u8 expect_key[CC_KEY_SIZE_DUAL] = {
		0x00,				 /* [0] family V6 */
		/* [1..8] v4 lane: zero */
		[9]  = 0x20, [10] = 0x01, [11] = 0x0d, [12] = 0xb8,
		[24] = 0x01,			 /* src6 tail */
		[25] = 0x20, [26] = 0x01, [27] = 0x0d, [28] = 0xb8,
		[40] = 0x02,			 /* dst6 tail */
		[41] = 17,			 /* proto UDP */
		[42] = 0x00, [43] = 0x35,	 /* sport 53 */
		[44] = 0x14, [45] = 0xe9,	 /* dport 5353 */
	};

	buf = kunit_kzalloc(test, 2 * CC_KEY_SIZE_DUAL, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_IPV6 | FMAN_PCD_CC_HW_F_SRC_IP |
		    FMAN_PCD_CC_HW_F_DST_IP | FMAN_PCD_CC_HW_F_PROTO |
		    FMAN_PCD_CC_HW_F_SRC_PORT | FMAN_PCD_CC_HW_F_DST_PORT;
	memcpy(k.src_ip6, src6, 16);
	memcpy(k.dst_ip6, dst6, 16);
	k.proto = 17;
	k.src_port_be = cpu_to_be16(53);
	k.dst_port_be = cpu_to_be16(5353);

	cc_pack_key_dual((void __iomem *)buf, 0, &k);

	key = buf;
	msk = buf + CC_KEY_SIZE_DUAL;
	KUNIT_EXPECT_MEMEQ(test, key, expect_key, CC_KEY_SIZE_DUAL);
	KUNIT_EXPECT_EQ(test, msk[0], 0xff);
	KUNIT_EXPECT_EQ(test, msk[1], 0xff);   /* v4 lane zero-assert */
	KUNIT_EXPECT_EQ(test, msk[8], 0xff);
	KUNIT_EXPECT_EQ(test, msk[9], 0xff);
	KUNIT_EXPECT_EQ(test, msk[40], 0xff);
	KUNIT_EXPECT_EQ(test, msk[41], 0xff);
	KUNIT_EXPECT_EQ(test, msk[45], 0xff);
}

static void cc_dual_key_row_stride(struct kunit *test)
{
	struct fman_pcd_cc_hw_key k;
	u8 *buf;

	/* idx=1 must land at byte offset 2*CC_KEY_SIZE_DUAL, not overlap row 0. */
	buf = kunit_kzalloc(test, 2 * 2 * CC_KEY_SIZE_DUAL, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, buf);

	memset(&k, 0, sizeof(k));
	k.present = FMAN_PCD_CC_HW_F_PROTO;
	k.proto = 0xab;

	cc_pack_key_dual((void __iomem *)buf, 1, &k);

	KUNIT_EXPECT_EQ(test, buf[0], 0);				/* row 0 untouched */
	KUNIT_EXPECT_EQ(test, buf[2 * CC_KEY_SIZE_DUAL + 0], CC_KEY_DUAL_FAMILY_V4);
	KUNIT_EXPECT_EQ(test, buf[2 * CC_KEY_SIZE_DUAL + 41], 0xab);
}

static struct kunit_case fman_pcd_cc_dual_key_cases[] = {
	KUNIT_CASE(cc_dual_key_size_unchanged),
	KUNIT_CASE(cc_dual_key_v4),
	KUNIT_CASE(cc_dual_key_v6),
	KUNIT_CASE(cc_dual_key_row_stride),
	{}
};

static struct kunit_suite fman_pcd_cc_dual_key_suite = {
	.name = "fman_pcd_cc_dual_key",
	.test_cases = fman_pcd_cc_dual_key_cases,
};
kunit_test_suite(fman_pcd_cc_dual_key_suite);
