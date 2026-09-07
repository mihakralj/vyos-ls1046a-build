/*
 * FMan Controller Microcode 210.10.1 — Policer & Rate Limiting Engine
 *
 * Reconstructed from disassembly of microcode words w0, w633–w650, w1450–w1520.
 *
 * Cross-references:
 *   - LS1046ADPAARM Ch. 5 §5.9 (Policer Engine)
 *   - RFC 2697 (srTCM) & RFC 2698 (trTCM)
 *   - decomp/out/fman-210.10.1-full.asm
 */

#include <stdint.h>
#include <stdbool.h>

/* Policer Color Definitions */
#define POLICER_COLOR_GREEN   0x00
#define POLICER_COLOR_YELLOW  0x01
#define POLICER_COLOR_RED     0x02

/* Policer Modes */
#define POLICER_MODE_PASS     0x00
#define POLICER_MODE_SRTCM    0x01 /* Single Rate Three Color Marker */
#define POLICER_MODE_TRTCM    0x02 /* Two Rate Three Color Marker */

/* Policer Profile Entry in MURAM */
struct fman_policer_profile {
    uint32_t mode_flags;      /* Mode, Color-aware/blind, packet/byte mode */
    uint32_t cir;             /* Committed Information Rate */
    uint32_t cbs;             /* Committed Burst Size */
    uint32_t eir_pir;         /* Excess / Peak Information Rate */
    uint32_t ebs_pbs;         /* Excess / Peak Burst Size */
    uint32_t current_c_tokens;/* Token bucket C level */
    uint32_t current_e_tokens;/* Token bucket E/P level */
    uint32_t last_timestamp;  /* Timestamp of last bucket refresh */
    uint32_t action_yellow;   /* Action descriptor on yellow */
    uint32_t action_red;      /* Action descriptor on red (discard / remark) */
};

/*
 * Policer Evaluation Algorithm (w633–w650, w1450–w1520)
 *
 * Runs per-frame token bucket replenishment and conformance testing.
 */
uint8_t fman_policer_evaluate(struct fman_policer_profile *prof,
                             uint32_t frame_len,
                             uint32_t current_time,
                             uint8_t input_color)
{
    /* Step 1: Token Replenishment based on elapsed time */
    uint32_t elapsed = current_time - prof->last_timestamp;
    prof->last_timestamp = current_time;

    /* Add tokens: C_tokens += elapsed * CIR, bounded by CBS */
    uint64_t new_c = (uint64_t)prof->current_c_tokens + ((uint64_t)elapsed * prof->cir);
    prof->current_c_tokens = (new_c > prof->cbs) ? prof->cbs : (uint32_t)new_c;

    /* Add tokens: E_tokens += elapsed * PIR, bounded by PBS */
    uint64_t new_e = (uint64_t)prof->current_e_tokens + ((uint64_t)elapsed * prof->eir_pir);
    prof->current_e_tokens = (new_e > prof->ebs_pbs) ? prof->ebs_pbs : (uint32_t)new_e;

    /* Step 2: Rate Evaluation (trTCM color-blind mode) */
    uint8_t output_color;

    if (prof->current_e_tokens < frame_len) {
        /* Packet exceeds peak/excess bucket -> RED */
        output_color = POLICER_COLOR_RED;
    } else if (prof->current_c_tokens < frame_len) {
        /* Packet exceeds committed bucket but fits peak -> YELLOW */
        prof->current_e_tokens -= frame_len;
        output_color = POLICER_COLOR_YELLOW;
    } else {
        /* Packet conforms to both buckets -> GREEN */
        prof->current_c_tokens -= frame_len;
        prof->current_e_tokens -= frame_len;
        output_color = POLICER_COLOR_GREEN;
    }

    return output_color;
}
