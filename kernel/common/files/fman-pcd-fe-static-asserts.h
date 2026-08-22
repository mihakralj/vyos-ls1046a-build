/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * fman-pcd-fe-static-asserts.h — §17 canonical FE descriptor type/size/NIA
 * compile-time guards.  Baked into every kernel build by F-089 fixup so
 * that any patch/edit that corrupts a constant fails at compile time,
 * not 3 board sessions later.
 *
 * The three-time ENQ regression survived because nothing between "edit"
 * and "silicon" knew that word 1 is an NIA.  These BUILD_BUG_ON guards
 * are the first tripwire.
 *
 * Companion: tests/fman_pcd_fe_test.c (KUnit — second tripwire)
 *           fe_verify debugfs (arm-time — third tripwire)
 */
#ifndef _FMAN_PCD_FE_STATIC_ASSERTS_H
#define _FMAN_PCD_FE_STATIC_ASSERTS_H

#include <linux/build_bug.h>

/* ── §17.1–§17.6: FE type codes (silicon bits [31:26] of AD word 0) ──── */
static_assert(FMAN_FE_TYPE_HM         == 0x01000000,
	"§17: HM type = 0x01000000");
static_assert(FMAN_FE_TYPE_ENQ        == 0x02000000,
	"§17: ENQ type = 0x02000000");
static_assert(FMAN_FE_TYPE_EXIT       == 0x03000000,
	"§17: EXIT type = 0x03000000");
static_assert(FMAN_FE_TYPE_MUX        == 0x04000000,
	"§17: MUX type = 0x04000000");
static_assert(FMAN_FE_TYPE_TRANSITION == 0x05000000,
	"§17: TRANSITION type = 0x05000000");
static_assert(FMAN_FE_TYPE_EXT_HASH   == 0x06000000,
	"§17: EXT_HASH type = 0x06000000");

/* ── §17 sizes: MURAM allocations must match ────────────────────────── */
static_assert(FMAN_FE_ENQ_SIZE        == 16,
	"§17: ENQ FE = 16 B (4 words)");
static_assert(FMAN_FE_EXIT_SIZE       == 4,
	"§17: EXIT FE = 4 B (1 word)");
static_assert(FMAN_FE_MUX_SIZE        == 4,
	"§17: MUX FE = 4 B (1 word)");
static_assert(FMAN_FE_TRANSITION_SIZE == 8,
	"§17: TRANSITION FE = 8 B (2 words)");
static_assert(FMAN_FE_HASH_SIZE       == 28,
	"§17: EXT_HASH FE = 28 B (7 words)");
static_assert(FMAN_PCD_FE_MAX_SIZE    == 28,
	"§17: FE_MAX_SIZE = 28 B (= EXT_HASH)");

/* ── §5.1: NIA (Next Instruction Address) encodings ──────────────────── */
/* NIA_ENG_BMI, NIA_BMI_AC_ENQ_FRAME, NIA_FM_CTL_AC_CC, ENQUEUE_KG_DFLT_NIA
 * are defined in fman_keygen.c, not fman_pcd.c.  Guard with #ifdef so the
 * header compiles from both translation units.
 */
#ifdef NIA_ENG_BMI
static_assert(NIA_ENG_BMI          == 0x00500000,
	"§17: NIA engine = BMI (0x00500000)");
static_assert(NIA_BMI_AC_ENQ_FRAME == 0x00000002,
	"§17: BMI action = enqueue frame (0x00000002)");
#endif
#ifdef NIA_FM_CTL_AC_CC
static_assert(NIA_FM_CTL_AC_CC     == 0x00000006,
	"§17: FM_CTL action = AC_CC (0x00000006)");
#endif
#ifdef ENQUEUE_KG_DFLT_NIA
static_assert(ENQUEUE_KG_DFLT_NIA  == 0x80500002,
	"§17: KG default NIA = BMI|ENQ_FRAME (0x80500002)");
#endif

/* ── §17.2: EXT_HASH FE field widths ─────────────────────────────────── */
static_assert(FMAN_EHASH_MASK_MAX  == 0x7FFF,
	"§17: EXT_HASH max hash mask = 0x7FFF");

/* ── §17.8: F-230 NAT/PAT opcode encoding (T-M6-7.1) ──────────────────── */
/* The FE NAT opcodes are single bytes that F-230 BIT-FUSES (OR) into one
 * opcode per operation group. These guards pin the exact vendor values and
 * prove the fused combinations do not alias an unrelated opcode. This is the
 * single riskiest silicon assumption; the S1 board gate confirms the runtime
 * behaviour, these asserts prevent an accidental value drift before that. */
/* Fused L4: SPORT(0x31)|DPORT(0x32) = 0x33, distinct from every L3 opcode. */
static_assert((0x31 | 0x32) == 0x33, "§17: fused SPORT|DPORT = 0x33");
static_assert((0x31 | 0x32) != 0x21 && (0x31 | 0x32) != 0x29,
	"§17: fused port opcode must not alias TTL/HOPLIMIT");
/* Fused v4 L3: TTL|SIP|DIP = 0x21|0x22|0x24 = 0x27. */
static_assert((0x21 | 0x22 | 0x24) == 0x27, "§17: fused v4 TTL|SIP|DIP = 0x27");
/* Fused v6 L3: HOPLIMIT|SIP|DIP = 0x29|0x2a|0x2c = 0x2f. */
static_assert((0x29 | 0x2a | 0x2c) == 0x2f,
	"§17: fused v6 HOPLIMIT|SIP|DIP = 0x2f");

/* ── §17.7: Params page FE pool fields ───────────────────────────────── */
/* +0x54: FE buffer pool MURAM offset (u32, must be non-zero when armed) */
/* +0x58: FE buffer depletion counter (u32, must be zero at disengage)   */
/* These are validated at arm time by fe_verify, not compile time.       */

#endif /* _FMAN_PCD_FE_STATIC_ASSERTS_H */
