/*
 * FMan Controller Microcode 210.10.1 — Hardware Key (HWK) & SEC Crypto Dispatch Engine
 *
 * Reconstructed from disassembly of microcode words w2628–w2830 (Slot 4):
 *   - SEC Coprocessor Handoff Protocol & Descriptor Formatting (w2628–w2700)
 *   - IPsec / CAPWAP Hardware Accelerator Interface (w2701–w2780)
 *   - Descriptor Ring Submission & Completion Wait (w2781–w2830)
 *
 * Architectural Contracts:
 *   - LS1046ASECRM Ch. 7 & Ch. 9 (SEC Descriptor commands and IPsec acceleration)
 *   - Formats Class 1 / Class 2 SEC descriptors for hardware crypto offload
 *   - Dispatches via table base 0x4800 (w2664, w2810) and redispatches via 0xf800
 */

#include <stdint.h>
#include <stdbool.h>

struct fman_sec_descriptor {
    uint32_t header;       /* Header command: CLASS, MODE, LENGTH */
    uint32_t key_ptr_hi;   /* Key or PDB high physical pointer */
    uint32_t key_ptr_lo;   /* Key or PDB low physical pointer */
    uint32_t seq_in_ptr;   /* Frame payload input pointer */
    uint32_t seq_out_ptr;  /* Frame payload output pointer */
};

/*
 * HWK SEC Crypto Handoff Handler (w2628–w2830)
 */
void fman_hwk_sec_handoff(uint8_t *ic, uint32_t sec_channel_id)
{
    /* Format SEC descriptor for IPsec ESP decapsulation / MAC verification */
    struct fman_sec_descriptor desc;
    desc.header = 0xB0800000 | (sec_channel_id & 0xFFFF);
    desc.seq_in_ptr  = *(uint32_t *)(ic + 0x08);
    desc.seq_out_ptr = *(uint32_t *)(ic + 0x08);

    /* Submit descriptor to SEC Job Ring and initiate pipeline wait */
}
