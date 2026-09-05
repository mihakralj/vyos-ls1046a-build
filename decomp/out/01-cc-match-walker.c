/*
 * FMan Controller Microcode 210.10.1 — Custom Classifier (CC) Match Tree Walker
 *
 * Reconstructed from disassembly of microcode words w24, w75–w125, w648–w715,
 * w1850–w1900, and w2837–w2960.
 *
 * Cross-references:
 *   - LS1046ADPAARM Ch. 5 §5.12 (Custom Classification Engine)
 *   - arch/fman-microcode-210-programming-reference.md §1.2, §5
 *   - decomp/naming-map.md §1, §7
 *   - decomp/out/fman-210.10.1-full.asm
 */

#include <stdint.h>
#include <stdbool.h>

/* Internal Context (IC) layout offsets (base r26 = 0xd000) */
#define IC_FD_STATUS         0x00
#define IC_FD_LENGTH         0x04
#define IC_AD_BASE           0x08
#define IC_FLOW_HASH         0x0C
#define IC_ICAD_OP_MODE      0x10
#define IC_CCBASE            0x18
#define IC_KS_HPNIA          0x1C
#define IC_PR_L2R            0x22
#define IC_PR_L3R            0x24
#define IC_PR_L4R            0x26
#define IC_PR_L4_OFF         0x3E
#define IC_TIMESTAMP_HI      0x40
#define IC_TIMESTAMP_LO      0x44
#define IC_KG_HASH_HI        0x48
#define IC_KG_KEY_START      0x50
#define IC_MGMT_INDEX        0xB8
#define IC_TASK_FLAGS        0xC0
#define IC_CURRENT_NIA       0xC4

/* Action Descriptor Types (bits 31:30 of AD Word 0) */
#define CC_AD_TYPE_MASK      0xC0000000
#define CC_AD_TYPE_CONT_LOOKUP 0x40000000 /* 0b01: Continue lookup */
#define CC_AD_TYPE_RESULT    0x80000000   /* 0b10: Terminal result */
#define CC_AD_TYPE_BYPASS    0xC0000000   /* 0b11: Bypass / Miss */

/* CC Table Entry Flags */
#define CC_ENTRY_VALID       0x80000000
#define CC_ENTRY_STATS_EN    0x40000000

/* Standard 16-byte CC Action Descriptor */
struct fman_cc_ad {
    uint32_t word0; /* [31:30]=type, [29:0]=descriptor control / flags */
    uint32_t word1; /* target address (MURAM or external table pointer) */
    uint32_t word2; /* action flags / stats pointer */
    uint32_t word3; /* target FQID or next-engine NIA */
};

/* CC Key Comparison Row */
struct fman_cc_key_row {
    uint8_t  key[16];   /* Match key (aligned up to 16, 24, 32, 40, 48, 56 bytes) */
    uint8_t  mask[16];  /* Key mask */
    struct fman_cc_ad ad; /* Associated action descriptor */
};

/*
 * High-level C representation of the Custom Classifier dispatch and match walk.
 */
void fman_cc_walk_and_dispatch(uint8_t *ic, uint8_t *frame, uint8_t *muram)
{
    /* Step 1: Initial CC Dispatch Entry (w75–w104) */
    uint32_t action_code = *(uint32_t *)(ic + IC_CURRENT_NIA);
    
    /* 
     * The e9c9 cascade: map action code into current-NIA slot.
     * Actions 0x01..0x3e are normalized and recorded into ctx[IC_CURRENT_NIA].
     */
    *(uint32_t *)(ic + IC_CURRENT_NIA) = action_code;

    /* Step 2: Retrieve CC Root Action Descriptor (w648, w672) */
    uint32_t cc_base_offset = *(uint32_t *)(ic + IC_CCBASE);
    struct fman_cc_ad *ad = (struct fman_cc_ad *)(muram + cc_base_offset);

    /* Enforce maximum lookup depth (<= 3 chained lookups per RM §5.12) */
    uint32_t lookup_depth = 0;

    for (;;) {
        uint32_t ad_type = ad->word0 & CC_AD_TYPE_MASK;

        if (ad_type == CC_AD_TYPE_RESULT) {
            /* Terminal Result: dispatch frame to target queue / next engine */
            uint32_t target_fqid = ad->word3;
            *(uint32_t *)(ic + IC_CURRENT_NIA) = ad->word2;
            
            /* Enqueue to QMI or handoff to BMI */
            return;
        }

        if (ad_type == CC_AD_TYPE_BYPASS) {
            /* Bypass / Miss: route to default miss queue */
            uint32_t miss_fqid = ad->word3;
            return;
        }

        if (ad_type == CC_AD_TYPE_CONT_LOOKUP) {
            lookup_depth++;
            if (lookup_depth > 3) {
                /* Exceeded hardware nesting limit: fault to error queue */
                return;
            }

            /* 
             * Multi-key match table evaluation (w2837 subroutine):
             * Acquire MURAM table semaphore (ld.sm w2850).
             */
            uint32_t table_offset = ad->word1;
            uint16_t num_entries  = (ad->word0 >> 16) & 0x3F;
            uint16_t key_size     = (ad->word0 >> 8) & 0x3F;

            /* Extract key from frame using key extraction parameters */
            uint8_t extracted_key[56];
            uint16_t key_offset = (uint16_t)(ad->word2 & 0xFFFF);
            for (int i = 0; i < key_size; i++) {
                extracted_key[i] = frame[key_offset + i];
            }

            /* Scan table entries */
            bool matched = false;
            struct fman_cc_key_row *table = (struct fman_cc_key_row *)(muram + table_offset);
            
            for (int e = 0; e < num_entries; e++) {
                struct fman_cc_key_row *entry = &table[e];
                
                /* Test masked match */
                bool entry_match = true;
                for (int b = 0; b < key_size; b++) {
                    if ((extracted_key[b] & entry->mask[b]) != (entry->key[b] & entry->mask[b])) {
                        entry_match = false;
                        break;
                    }
                }

                if (entry_match) {
                    /* Match HIT: update entry statistics if enabled */
                    if (entry->ad.word0 & CC_ENTRY_STATS_EN) {
                        /* Atomic counter increment in workspace */
                    }

                    /* Advance to next Action Descriptor */
                    ad = &entry->ad;
                    matched = true;
                    break;
                }
            }

            if (!matched) {
                /* Table MISS: follow Miss Action Descriptor at end of group */
                struct fman_cc_ad *miss_ad = (struct fman_cc_ad *)(table + num_entries);
                ad = miss_ad;
            }
        }
    }
}
