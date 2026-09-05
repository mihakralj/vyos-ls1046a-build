/*
 * FMan Controller Microcode 210.10.1 — Custom Classifier (CC) & ehash Match Walker
 *
 * Reconstructed from disassembly and live Ghidra decompilation of Island 1:
 *   - Entry & Setup: w1576–w1620 (Task info, staging buffer init, root fetch)
 *   - DMA Fetch & Dispatch: w1621–w1637 (dma.read256, AD word0 bit 31/30/29 decode)
 *   - Hardware Keycmp: w1638–w1650, w1720–w1735 (unit 0x10 func 0x20, r0 bit 4 mismatch)
 *   - Table Indexing & Chaining: w1651–w1719 (AD + AD[3] + idx*8, ehash +8 record chain)
 *   - Task Redispatch: w1766–w1860 (NIA and FQID update, task boundary/redispatch)
 *
 * Architectural Contracts:
 *   - Internal Context (IC) anchored at r26 = 0xd000 (RM §5.4.3 Table 5-19).
 *   - ctx[0x1c] (IC_KS) is POPULATED BY KEYGEN HARDWARE SILICON (valid key length 1..56).
 *     It is NEVER written by microcode; microcode reads it at w1639/w1721 to configure keycmp.
 *   - ctx[0x90] is the current MURAM table base address (microcode-managed).
 *   - ctx[0x98] is the 256-byte workspace staging buffer (0x0300 + tnum * 0x800 + offset).
 *   - One shared walker serves both CC tree match tables and the external-hash engine.
 */

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

/* Internal Context (IC) offsets (r26 = 0xd000) */
#define IC_FD_STATUS         0x00
#define IC_FD_LENGTH         0x04
#define IC_AD_BASE           0x08
#define IC_FLOW_HASH         0x0C
#define IC_ICAD_OP_MODE      0x10
#define IC_CCBASE            0x18 /* CC Root Action Descriptor / Table Base */
#define IC_KS_HPNIA          0x1C /* [7:0] = KS (Key Size from KeyGen), [31:8] = HPNIA */
#define IC_PR_L2R            0x22
#define IC_PR_L3R            0x24
#define IC_PR_L4R            0x26
#define IC_PR_L4_OFF         0x3E
#define IC_TIMESTAMP         0x40
#define IC_KG_HASH           0x48 /* Raw 64-bit CRC hash from KeyGen */
#define IC_KG_KEY            0x50 /* Extracted key composite from KeyGen (up to 56 bytes) */
#define IC_WALKER_TABLE_BASE 0x90 /* Current table address in MURAM (ctx[0x90]) */
#define IC_DMA_STAGING_BUF   0x98 /* Pointer to 256-byte staging buffer in workspace */
#define IC_STAGING_STATUS    0x9C /* Staging/DMA status flag */
#define IC_MGMT_INDEX        0xB8
#define IC_TASK_FLAGS        0xC0
#define IC_CURRENT_NIA       0xC4

/* Action Descriptor (AD) Word 0 Bitfield Flags */
#define AD_W0_TERM_BIT       0x80000000 /* Bit 31: Terminal / direct result handling */
#define AD_W0_TYPE_CONT      0x40000000 /* Bit 30: 0 = CONT_LOOKUP (continue table walk) */
#define AD_W0_MISS_PTR_BIT   0x20000000 /* Bit 29: Miss-pointer presence flag */

/* Hardware Unit 0x10 keycmp.run Status Bits (Returned in r0) */
#define KEYCMP_R0_MISMATCH   0x10       /* Bit 4: 1 = mismatch, 0 = MATCH */

/* External hardware/coprocessor unit mock declarations */
extern uint8_t  fman_hw_get_tnum(void);
extern void     fman_hw_dma_read8(uint32_t src_muram_addr, uint8_t *dst);
extern void     fman_hw_dma_read256(uint32_t src_muram_addr, uint8_t *dst_staging);
extern uint32_t fman_hw_keycmp_run(const uint8_t *key_a, const uint8_t *key_b, uint8_t len);
extern void     fman_hw_task_boundary(void);
extern void     fman_hw_task_redispatch(uint32_t nia);

/*
 * Reconstructed C model of the FMan Island 1 CC & ehash DMA Fetch Engine.
 */
void fman_cc_ehash_walker(uint8_t *ic, uint8_t *muram_base)
{
    uint32_t cc_base;
    uint32_t flow_hash;
    uint8_t  tnum;
    uint32_t staging_addr;
    uint8_t *staging_buf;
    uint32_t current_table_addr;
    uint8_t  key_size;

    /* Step 1: Initial context setup & workspace derivation (w1584–w1615) */
    cc_base   = *(uint32_t *)(ic + IC_CCBASE);
    flow_hash = *(uint32_t *)(ic + IC_FLOW_HASH);

    /* Derive per-task workspace staging buffer address (w1609–w1615) */
    tnum = fman_hw_get_tnum();
    staging_addr = 0x0300 + ((uint32_t)tnum << 8) + 0x50; /* Task workspace slot */
    *(uint32_t *)(ic + IC_DMA_STAGING_BUF) = staging_addr;
    staging_buf = muram_base + staging_addr;

    /* Initial 8-byte table descriptor fetch into ctx[0x90] (w1617–w1618) */
    current_table_addr = cc_base;
    fman_hw_dma_read8(current_table_addr, ic + IC_WALKER_TABLE_BASE);
    *(uint64_t *)(ic + IC_WALKER_TABLE_BASE) = current_table_addr;

    /* Step 2: Main table traversal loop (w1621–w1765) */
    while (true) {
        current_table_addr = *(uint32_t *)(ic + IC_WALKER_TABLE_BASE);
        if (current_table_addr == 0) {
            break; /* No further table to walk */
        }

        /* DMA fetch 256-byte table page into workspace staging buffer (w1626) */
        fman_hw_dma_read256(current_table_addr, staging_buf);
        *(uint8_t *)(ic + IC_STAGING_STATUS) = 0;

        uint32_t ad_word0 = *(uint32_t *)(staging_buf + 0);

        /* Bit 31 test: Terminal result or direct dispatch (w1635) */
        if ((ad_word0 & AD_W0_TERM_BIT) != 0) {
            break;
        }

        /* Bit 30 test: Action Descriptor Type (w1637) */
        if ((ad_word0 & AD_W0_TYPE_CONT) == 0) {
            /*
             * CONT_LOOKUP branch: evaluate match key using hardware keycmp unit.
             * Key size is read from IC_KS (ctx[0x1c]), deposited by KeyGen silicon!
             */
            key_size = *(uint8_t *)(ic + IC_KS_HPNIA); /* w1639: memb.read r17, [r26 + 0x1c] */
            uint8_t compare_len = key_size - 1;       /* w1640: subi16 r17, 1 */

            const uint8_t *extracted_composite = ic + IC_KG_KEY; /* w1638: r16 = IC + 0x50 */
            const uint8_t *row_key             = staging_buf + 8;/* w1642: r18 = staging + 8 */

            /* Hardware unit 0x10 function 0x20 execution (w1646) */
            uint32_t cmp_result = fman_hw_keycmp_run(extracted_composite, row_key, compare_len);

            /* Bit 4 mismatch test: andi16z 0x10 (w1649) */
            if ((cmp_result & KEYCMP_R0_MISMATCH) == 0) {
                /*
                 * KEY MATCH:
                 * Check for chained external-hash record link (+8 chain walk)
                 * or next-hop table selection.
                 */
                uint32_t next_ptr = *(uint32_t *)(staging_buf + (uint16_t)staging_buf[3] + 8);
                if ((next_ptr & 0xC0000000) != 0) {
                    /* Chained record link: entry+0x10 = *(entry+0x18) */
                    uint32_t *entry = (uint32_t *)(muram_base + (next_ptr & 0x3FFFFFFF));
                    entry[4] = *(uint32_t *)(muram_base + entry[6]);
                }
                
                /* Advance to next table or target Action Descriptor */
                *(uint32_t *)(ic + IC_WALKER_TABLE_BASE) = next_ptr;
                continue;
            }
        }

        /* Bit 29 test: Miss pointer evaluation (w1665) */
        if ((ad_word0 & AD_W0_MISS_PTR_BIT) == 0) {
            /* Miss pointer absent: terminate walk to default miss exit */
            break;
        }

        /* Miss pointer present: advance to next table via *(AD + 4) */
        *(uint32_t *)(ic + IC_WALKER_TABLE_BASE) = *(uint32_t *)(staging_buf + 4);
    }

    /* Step 3: Exit & Handoff / Redispatch (w1766–w1860) */
    uint32_t end_nia = *(uint32_t *)(staging_buf + 12);
    if (end_nia == 0) {
        end_nia = 0x1a; /* Default fallback NIA */
    }

    *(uint32_t *)(ic + IC_CURRENT_NIA) = end_nia;
    fman_hw_task_boundary();
    fman_hw_task_redispatch(end_nia);
}
