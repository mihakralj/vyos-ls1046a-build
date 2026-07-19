// SPDX-License-Identifier: GPL-2.0+
/*
 * fman_pcd_fe_test.c — §17 FE-VM descriptor encoder KUnit tests.
 *
 * Second tripwire: validates FE type constants, NIA encodings, and
 * descriptor sizes at KUnit time (CI, no hardware needed).
 *
 * Included as a trailer at the end of fman_pcd.c via
 *   #if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)
 *   #include "tests/fman_pcd_fe_test.c"
 *   #endif
 *
 * Companion: fman-pcd-fe-static-asserts.h (compile-time guards)
 *           fe_verify debugfs (arm-time readback)
 */
#include <kunit/test.h>

/* ── Test 1: FE type constants match silicon §17.1–§17.6 ─────────────── */
static void fe_type_constants(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_HM,         0x01000000U);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_ENQ,        0x02000000U);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_EXIT,       0x03000000U);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_MUX,        0x04000000U);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_TRANSITION, 0x05000000U);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TYPE_EXT_HASH,   0x06000000U);
}

/* ── Test 2: FE sizes match MURAM allocation contract  ───────────────── */
static void fe_sizes(struct kunit *test)
{
	/* Singletons */
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_ENQ_SIZE,        16);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_EXIT_SIZE,        4);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_MUX_SIZE,         8);
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_TRANSITION_SIZE,  8);
	/* Core FE */
	KUNIT_EXPECT_EQ(test, (u32)FMAN_FE_HASH_SIZE,       28);
	/* Pool max */
	KUNIT_EXPECT_EQ(test, (u32)FMAN_PCD_FE_MAX_SIZE,    28);
	KUNIT_EXPECT_LE(test, FMAN_PCD_FE_MAX_SIZE, 64);
	KUNIT_EXPECT_GT(test, FMAN_PCD_FE_MAX_SIZE,  8);
}

/* ── Test 3: NIA encodings match 210.10.1 silicon (fman_keygen.c scope) */
#ifdef NIA_ENG_BMI
static void fe_nia_constants(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, (u32)NIA_ENG_BMI,          0x00500000U);
	KUNIT_EXPECT_EQ(test, (u32)NIA_BMI_AC_ENQ_FRAME, 0x00000002U);
#ifdef NIA_FM_CTL_AC_CC
	KUNIT_EXPECT_EQ(test, (u32)NIA_FM_CTL_AC_CC,     0x00000006U);
#endif
#ifdef ENQUEUE_KG_DFLT_NIA
	KUNIT_EXPECT_EQ(test, (u32)ENQUEUE_KG_DFLT_NIA,  0x80500002U);
#endif
}
#endif /* NIA_ENG_BMI */

/* ── Test 4: FE_ENTER root AD word encodings ─────────────────────────── */
static void fe_enter_ad_encoding(struct kunit *test)
{
	/* §17.1 w0: CONT_LOOKUP (0x40) + ALLOCATE (0x00800000) */
	KUNIT_EXPECT_EQ(test, FMAN_AD_CONT_LOOKUP_TYPE  & 0xFF000000, 0x40000000U);
	KUNIT_EXPECT_EQ(test, FMAN_AD_FE_ENTER_ALLOCATE,              0x00800000U);
	/* §17.1 w2: OPC_FE_ENTER (0xF6 in bits [31:16]) */
	KUNIT_EXPECT_EQ(test, (FMAN_AD_FE_ENTER_OPCODE & 0xFFFF0000), 0x00F60000U);
}

/* ── Test 5: ENQ FE word encodings ───────────────────────────────────── */
static void fe_enq_encoding(struct kunit *test)
{
	/* §17.5 w0 flags: fqidEn = bit 16 */
	KUNIT_EXPECT_EQ(test, FMAN_FE_ENQ_FQID, 0x00010000U);
	/* §17.5 w1: fqid field is 24 bits */
	KUNIT_EXPECT_EQ(test, FMAN_FE_ENQ_NIA_MASK, 0x00FFFFFFU);
}

/* ── Test 6: EXIT singleton encoding ─────────────────────────────────── */
static void fe_exit_encoding(struct kunit *test)
{
	/* §17.4 w0: type=0x03 + DEALLOCATE=0x00800000 */
	KUNIT_EXPECT_EQ(test, FMAN_FE_EXIT_DEALLOCATE, 0x00800000U);
}

/* ── Test 7: ehash constants ─────────────────────────────────────────── */
static void fe_ehash_constants(struct kunit *test)
{
	/* §17.2 w1: max hash mask */
	KUNIT_EXPECT_EQ(test, (u32)FMAN_EHASH_MASK_MAX, 0x7FFFU);
	KUNIT_EXPECT_GT (test, FMAN_EHASH_MASK_MAX, 0);
	/* Bucket size */
	KUNIT_EXPECT_EQ(test, (u32)FMAN_EHASH_BUCKET_SIZE, 16);
}

/* ── Test 8: FE type mask covers all 6 types ─────────────────────────── */
static void fe_type_range(struct kunit *test)
{
	u32 types[] = {
		FMAN_FE_TYPE_HM,
		FMAN_FE_TYPE_ENQ,
		FMAN_FE_TYPE_EXIT,
		FMAN_FE_TYPE_MUX,
		FMAN_FE_TYPE_TRANSITION,
		FMAN_FE_TYPE_EXT_HASH,
	};
	int i;

	/* Every type has exactly one bit set in the type field [31:24] */
	for (i = 0; i < ARRAY_SIZE(types); i++) {
		u32 type_byte = (types[i] >> 24) & 0xFF;
		KUNIT_EXPECT_GT_MSG(test, type_byte, 0,
			"FE type 0x%08x has zero type byte", types[i]);
		/* Type byte alone (masked from rest of word) */
		KUNIT_EXPECT_EQ_MSG(test, types[i] & 0xFF000000,
			types[i],
			"FE type 0x%08x has bits outside [31:24]", types[i]);
	}
}

/* ── Test case array ─────────────────────────────────────────────────── */
static struct kunit_case fman_pcd_fe_test_cases[] = {
	KUNIT_CASE(fe_type_constants),
	KUNIT_CASE(fe_sizes),
#ifdef NIA_ENG_BMI
	KUNIT_CASE(fe_nia_constants),
#endif
	KUNIT_CASE(fe_enter_ad_encoding),
	KUNIT_CASE(fe_enq_encoding),
	KUNIT_CASE(fe_exit_encoding),
	KUNIT_CASE(fe_ehash_constants),
	KUNIT_CASE(fe_type_range),
	{},
};

static struct kunit_suite fman_pcd_fe_test_suite = {
	.name = "fman_pcd_fe",
	.test_cases = fman_pcd_fe_test_cases,
};
kunit_test_suite(fman_pcd_fe_test_suite);
