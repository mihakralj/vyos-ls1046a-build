// SPDX-License-Identifier: GPL-2.0
/*
 * fman_pcd_cc_bridge_shadow_test.c — T-M6-2 B1 bridge FDB shadow builder
 * KUnit tests.
 *
 * Pins fman_pcd_cc_bridge_key_add()/fman_pcd_cc_bridge_key_remove()'s
 * host-side struct fman_pcd_cc_hw_spec manipulation: add appends a
 * DA-only FMAN_PCD_CC_HW_F_MAC_DST leaf and sets miss_fe_off; a second
 * add for the same MAC updates target_fqid in place instead of
 * duplicating (FDB entry moved to a different egress port); remove
 * deletes the matching leaf and compacts the array so keys[0..num_keys)
 * stays contiguous. No MURAM/hardware I/O is exercised here — B1 is
 * dormant, host-memory-only (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md B1 gate).
 *
 * Included as a trailer at the end of fman_pcd_cc.c via
 *   #if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)
 *   #include "tests/fman_pcd_cc_bridge_shadow_test.c"
 *   #endif
 */
#include <kunit/test.h>

static const u8 bridge_test_mac1[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x01 };
static const u8 bridge_test_mac2[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x02 };
static const u8 bridge_test_mac3[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x03 };

static void cc_bridge_shadow_add_one(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;
	int rc;

	memset(&spec, 0, sizeof(spec));

	rc = fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x641,
					0xdeadbeef);
	KUNIT_EXPECT_EQ(test, rc, 0);
	KUNIT_EXPECT_EQ(test, spec.num_keys, (u16)1);
	KUNIT_EXPECT_EQ(test, spec.miss_fe_off, 0xdeadbeefu);
	KUNIT_EXPECT_EQ(test, spec.keys[0].present,
			(u32)FMAN_PCD_CC_HW_F_MAC_DST);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[0].dst_mac, bridge_test_mac1, 6);
	KUNIT_EXPECT_EQ(test, spec.keys[0].target_fqid, 0x641u);
}

static void cc_bridge_shadow_add_idempotent_update(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;
	int rc;

	memset(&spec, 0, sizeof(spec));

	rc = fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x641,
					0x1000);
	KUNIT_EXPECT_EQ(test, rc, 0);

	/* Same MAC, moved to a different egress FQ: must update in place,
	 * not append a second key. */
	rc = fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x642,
					0x2000);
	KUNIT_EXPECT_EQ(test, rc, 0);
	KUNIT_EXPECT_EQ(test, spec.num_keys, (u16)1);
	KUNIT_EXPECT_EQ(test, spec.keys[0].target_fqid, 0x642u);
	KUNIT_EXPECT_EQ(test, spec.miss_fe_off, 0x2000u);
}

static void cc_bridge_shadow_add_multiple(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;

	memset(&spec, 0, sizeof(spec));

	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x641, 0x1000), 0);
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac2, 0x642, 0x1000), 0);
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac3, 0x643, 0x1000), 0);
	KUNIT_EXPECT_EQ(test, spec.num_keys, (u16)3);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[0].dst_mac, bridge_test_mac1, 6);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[1].dst_mac, bridge_test_mac2, 6);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[2].dst_mac, bridge_test_mac3, 6);
}

static void cc_bridge_shadow_remove_compacts(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;
	int rc;

	memset(&spec, 0, sizeof(spec));
	fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x641, 0x1000);
	fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac2, 0x642, 0x1000);
	fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac3, 0x643, 0x1000);

	/* Remove the middle entry; the tail must shift down, not leave a
	 * hole -- every other consumer of this spec walks [0..num_keys). */
	rc = fman_pcd_cc_bridge_key_remove(&spec, bridge_test_mac2);
	KUNIT_EXPECT_EQ(test, rc, 0);
	KUNIT_EXPECT_EQ(test, spec.num_keys, (u16)2);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[0].dst_mac, bridge_test_mac1, 6);
	KUNIT_EXPECT_MEMEQ(test, spec.keys[1].dst_mac, bridge_test_mac3, 6);
	KUNIT_EXPECT_EQ(test, spec.keys[1].target_fqid, 0x643u);
}

static void cc_bridge_shadow_remove_not_found(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;
	int rc;

	memset(&spec, 0, sizeof(spec));
	fman_pcd_cc_bridge_key_add(&spec, bridge_test_mac1, 0x641, 0x1000);

	rc = fman_pcd_cc_bridge_key_remove(&spec, bridge_test_mac2);
	KUNIT_EXPECT_EQ(test, rc, -ENOENT);
	KUNIT_EXPECT_EQ(test, spec.num_keys, (u16)1);
}

static void cc_bridge_shadow_bad_args(struct kunit *test)
{
	struct fman_pcd_cc_hw_spec spec;

	memset(&spec, 0, sizeof(spec));
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_add(NULL, bridge_test_mac1, 1, 0), -EINVAL);
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_add(&spec, NULL, 1, 0), -EINVAL);
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_remove(NULL, bridge_test_mac1), -EINVAL);
	KUNIT_EXPECT_EQ(test, fman_pcd_cc_bridge_key_remove(&spec, NULL), -EINVAL);
}

static struct kunit_case fman_pcd_cc_bridge_shadow_cases[] = {
	KUNIT_CASE(cc_bridge_shadow_add_one),
	KUNIT_CASE(cc_bridge_shadow_add_idempotent_update),
	KUNIT_CASE(cc_bridge_shadow_add_multiple),
	KUNIT_CASE(cc_bridge_shadow_remove_compacts),
	KUNIT_CASE(cc_bridge_shadow_remove_not_found),
	KUNIT_CASE(cc_bridge_shadow_bad_args),
	{}
};

static struct kunit_suite fman_pcd_cc_bridge_shadow_suite = {
	.name = "fman_pcd_cc_bridge_shadow",
	.test_cases = fman_pcd_cc_bridge_shadow_cases,
};
kunit_test_suite(fman_pcd_cc_bridge_shadow_suite);
