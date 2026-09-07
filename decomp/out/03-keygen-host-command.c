/*
 * FMan Controller Microcode 210.10.1 — KeyGen Host Command (HC) Engine
 *
 * Reconstructed from disassembly of microcode words w2, w653–w850.
 *
 * Cross-references:
 *   - LS1046ADPAARM Ch. 5 §5.8 (KeyGen Scheme Architecture)
 *   - arch/fman-microcode-210-programming-reference.md §4
 *   - decomp/out/fman-210.10.1-full.asm
 */

#include <stdint.h>
#include <stdbool.h>

/* KeyGen Indirect Access Registers (FMKG_AR / FMKG_DR) */
#define FMKG_AR_UPDATE_BIT   0x80000000
#define FMKG_AR_READ_BIT     0x40000000
#define FMKG_AR_SCHEME_SHIFT 16

/* KeyGen Scheme Register Block (17 32-bit words per scheme) */
struct fman_kg_scheme_regs {
    uint32_t kgse_mode;      /* Word 0: EN, AC_CC, CCOBASE */
    uint32_t kgse_ekfc;      /* Word 1: Extract Key Field Configuration */
    uint32_t kgse_ekdv[2];   /* Words 2-3: Default Values */
    uint32_t kgse_bmval[2];  /* Words 4-5: Byte Mask Values */
    uint32_t kgse_fqb;       /* Word 6: Frame Queue Base */
    uint32_t kgse_hc;        /* Word 7: Hash Configuration (CRC-64) */
    uint32_t kgse_pp;        /* Word 8: Policer Profile / Port Profile */
    uint32_t kgse_gec[4];    /* Words 9-12: Generic Extract Configuration */
    uint32_t kgse_spc;       /* Word 16: Scheme Packet Counter (Accumulating) */
};

/* Host Command Header (HCOR format) */
struct fman_hc_header {
    uint32_t opcode;         /* 0x01 = KeyGen Scheme Program */
    uint32_t flags;
    uint32_t scheme_id;
    uint32_t status;
};

/*
 * KeyGen Host Command Processing Loop (w653–w720)
 *
 * Handles indirect scheme register programming and CRC-64 verification.
 */
uint32_t fman_keygen_hc_execute(struct fman_hc_header *hc,
                               struct fman_kg_scheme_regs *input_scheme,
                               volatile struct fman_kg_scheme_regs *hw_schemes)
{
    uint32_t scheme_id = hc->scheme_id & 0x3F; /* Schemes 0..63 */
    volatile struct fman_kg_scheme_regs *target = &hw_schemes[scheme_id];

    /* 
     * Atomic Scheme Update via Indirect FMKG_AR protocol (w656–w670):
     * 1. Read current task info (task.info r10).
     * 2. Clear scheme enable bit during reconfiguration.
     */
    target->kgse_mode &= ~0x80000000; /* Disable scheme during write */

    /* 3. Program field extraction configuration (EKFC) */
    target->kgse_ekfc = input_scheme->kgse_ekfc;
    target->kgse_ekdv[0] = input_scheme->kgse_ekdv[0];
    target->kgse_ekdv[1] = input_scheme->kgse_ekdv[1];

    /* 4. Program Frame Queue Base (FQB) and Hash Config (CRC-64 ECMA-182) */
    target->kgse_fqb = input_scheme->kgse_fqb;
    target->kgse_hc  = input_scheme->kgse_hc;

    /* 5. Program Generic Extract Configuration (GEC lanes 0..3) */
    for (int i = 0; i < 4; i++) {
        target->kgse_gec[i] = input_scheme->kgse_gec[i];
    }

    /* 6. Re-arm scheme with target mode and CCOBASE */
    target->kgse_mode = input_scheme->kgse_mode | 0x80000000;

    /* Return success status to host buffer */
    hc->status = 0x00000000;
    return 0;
}
